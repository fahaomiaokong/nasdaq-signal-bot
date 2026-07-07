#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
US Market Dual-Signal Bot
=========================
同时监控标普500和纳斯达克100的三大信号，
通过企业微信 Webhook 推送精简每日报告和告警通知。

标普500三信号：SPY回撤 + VIX恐慌指数 + Shiller CAPE
纳斯达克100三信号：QQQ回撤 + VIX恐慌(VXN代理) + QQQ PE估值

适用于本地运行、GitHub Actions 和腾讯云函数（SCF）部署。
"""

import json
import math
import os
import re
import sys
import traceback
from datetime import datetime, timezone, timedelta

import requests
import yaml

# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
BJT = timezone(timedelta(hours=8))


def load_config(config_path: str = CONFIG_PATH) -> dict:
    """
    加载配置。优先级：环境变量 > config.yaml 文件。

    云函数/GitHub Actions 可通过环境变量覆盖：
      - WEBHOOK_URL
      - DASHBOARD_URL
      - SPY_DD_THRESHOLD, VIX_THRESHOLD, CAPE_THRESHOLD
      - QQQ_DD_THRESHOLD, VXN_THRESHOLD, QQQ_PE_THRESHOLD
      - LOOKBACK_DAYS
    """
    cfg = {}

    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    # 环境变量覆盖
    if os.getenv("WEBHOOK_URL"):
        cfg["webhook_url"] = os.getenv("WEBHOOK_URL")
    if os.getenv("DASHBOARD_URL"):
        cfg["dashboard_url"] = os.getenv("DASHBOARD_URL")

    sp5 = cfg.setdefault("sp500_thresholds", {})
    if os.getenv("SPY_DD_THRESHOLD"):
        sp5["drawdown"] = float(os.getenv("SPY_DD_THRESHOLD"))
    if os.getenv("VIX_THRESHOLD"):
        sp5["vix"] = float(os.getenv("VIX_THRESHOLD"))
    if os.getenv("CAPE_THRESHOLD"):
        sp5["cape"] = float(os.getenv("CAPE_THRESHOLD"))

    nd100 = cfg.setdefault("nasdaq100_thresholds", {})
    if os.getenv("QQQ_DD_THRESHOLD"):
        nd100["drawdown"] = float(os.getenv("QQQ_DD_THRESHOLD"))
    if os.getenv("VXN_THRESHOLD"):
        nd100["vxn"] = float(os.getenv("VXN_THRESHOLD"))
    if os.getenv("QQQ_PE_THRESHOLD"):
        nd100["qqq_pe"] = float(os.getenv("QQQ_PE_THRESHOLD"))

    if os.getenv("LOOKBACK_DAYS"):
        cfg["lookback_days"] = int(os.getenv("LOOKBACK_DAYS"))

    return cfg


# ---------------------------------------------------------------------------
# 仓位等级映射
# ---------------------------------------------------------------------------

POSITION_LEVELS = {
    0: ("L1-满仓进攻", "所有信号正常，市场处于乐观状态"),
    1: ("L2-偏进攻", "一个信号触发，减杠杆但不减仓"),
    2: ("L3-去杠杆", "两个信号触发，明显风险，去杠杆防守"),
    3: ("L4-避险", "三个信号全部触发，极度危险"),
}

# 调仓配比（基于纳斯达克100信号级别）
POSITION_ALLOCATION = {
    0: {"tqqq": 0.75, "qqq": 0.25, "cash": 0.00, "hint": "满仓进攻"},
    1: {"tqqq": 0.50, "qqq": 0.35, "cash": 0.10, "hint": "减杠杆偏进攻"},
    2: {"tqqq": 0.00, "qqq": 0.50, "cash": 0.30, "hint": "去杠杆防守"},
    3: {"tqqq": 0.00, "qqq": 0.25, "cash": 0.75, "hint": "大幅减仓避险"},
}


def get_position(triggered_count: int) -> tuple:
    """根据触发信号数量返回仓位建议。"""
    if triggered_count > 3:
        return ("L5-全现金", "超过3个信号触发，全部撤退")
    return POSITION_LEVELS.get(triggered_count, ("L1-满仓进攻", "所有信号正常"))


def get_allocation(triggered_count: int, cfg: dict = None) -> dict:
    """根据触发信号数量返回调仓配比。优先从config读取，否则用默认值。"""
    if cfg and "position_allocation" in cfg:
        alloc_cfg = cfg["position_allocation"]
        # 从config中查找对应级别
        level_key = f"L{min(triggered_count + 1, 5)}"
        if level_key in alloc_cfg:
            return alloc_cfg[level_key]
    
    if triggered_count > 3:
        return {"tqqq": 0.00, "qqq": 0.00, "cash": 1.00, "hint": "全部撤退"}
    return POSITION_ALLOCATION.get(triggered_count, POSITION_ALLOCATION[0])


# ---------------------------------------------------------------------------
# 趋势评分（25级连续风险评分）
# ---------------------------------------------------------------------------

def compute_trend_score(dd: float, vix: float, pe: float, cfg: dict) -> dict:
    """
    计算25级连续风险评分，用于趋势预警。
    
    评分公式：score = s_dd + s_vix + s_pe
    - s_dd = max(0, (-dd - |dd_baseline|) / 10) × scale  （DD越低风险越高）
    - s_vix = max(0, (vix - vix_baseline) / 7) × scale    （VIX越高风险越高）
    - s_pe = max(0, (pe - pe_baseline) / 5) × scale       （PE越高风险越高）
    
    评分→Step: step = ceil(score / 0.33) + 1, 最大25
    
    Returns: {"score": float, "step": int, "s_dd": float, "s_vix": float, "s_pe": float,
              "trend_direction": str, "trend_hint": str}
    """
    ts_cfg = cfg.get("trend_scoring", {})
    
    dd_baseline = ts_cfg.get("dd_baseline", -3.0)
    vix_baseline = ts_cfg.get("vix_baseline", 16.0)
    pe_baseline = ts_cfg.get("pe_baseline", 32.0)
    scale = ts_cfg.get("scale", 1.0)
    
    # 各指标评分
    s_dd = max(0, (-dd - abs(dd_baseline)) / 10) * scale if dd < dd_baseline else 0
    s_vix = max(0, (vix - vix_baseline) / 7) * scale if vix > vix_baseline else 0
    s_pe = max(0, (pe - pe_baseline) / 5) * scale if pe > pe_baseline else 0
    
    score = s_dd + s_vix + s_pe
    # score → step: S1=score 0~0.33, S2=0.34~0.66, ..., S25=score≥8
    step = min(25, max(1, int(score / 0.33) + 1))
    
    # 趋势方向判断
    if score < 0.5:
        trend_dir = "→"
        trend_hint = "风险极低，远离切换阈值"
    elif score < 1.0:
        trend_dir = "→"
        trend_hint = "风险偏低，安全区间"
    elif score < 2.0:
        trend_dir = "↑"
        trend_hint = "风险上升，注意可能升级"
    elif score < 4.0:
        trend_dir = "↑↑"
        trend_hint = "风险偏高，近期可能切换级别"
    elif score < 6.0:
        trend_dir = "⚠️"
        trend_hint = "风险高，应已触发信号"
    else:
        trend_dir = "🔴"
        trend_hint = "风险极高，严重危机"
    
    return {
        "score": round(score, 2),
        "step": step,
        "s_dd": round(s_dd, 2),
        "s_vix": round(s_vix, 2),
        "s_pe": round(s_pe, 2),
        "trend_dir": trend_dir,
        "trend_hint": trend_hint,
    }


# ---------------------------------------------------------------------------
# 信号状态符号
# ---------------------------------------------------------------------------

def signal_icon(triggered: bool) -> str:
    """返回信号状态图标。"""
    return "🔴" if triggered else "✅"


def signal_icon_warn(triggered: bool, value: float, threshold: float, is_upper: bool = True) -> str:
    """返回带警告级别的图标。接近阈值时用⚠️，触发用🔴，正常用✅。"""
    if triggered:
        return "🔴"
    if is_upper:
        if value >= threshold * 0.8:
            return "⚠️"
    else:
        if value <= threshold * 0.8:
            return "⚠️"
    return "✅"


# ---------------------------------------------------------------------------
# 通用数据获取
# ---------------------------------------------------------------------------

def get_etf_data(ticker: str, name: str, lookback_days: int = 252) -> dict:
    """获取 ETF 历史数据，计算当前价格和回撤。"""
    import yfinance as yf

    etf = yf.Ticker(ticker)
    hist = etf.history(period=f"{lookback_days}d")

    if hist.empty:
        raise ValueError(f"无法获取 {name} 历史数据")

    current_price = round(float(hist["Close"].iloc[-1]), 2)
    peak_price = round(float(hist["Close"].max()), 2)
    drawdown = round((current_price - peak_price) / peak_price * 100, 2)

    return {
        "ticker": ticker,
        "name": name,
        "price": current_price,
        "drawdown": drawdown,
        "peak": peak_price,
    }


def get_volatility_index(ticker: str, name: str) -> float:
    """获取波动率指数当前值。"""
    import yfinance as yf

    idx = yf.Ticker(ticker)
    hist = idx.history(period="5d")

    if hist.empty:
        raise ValueError(f"无法获取 {name} 数据")

    return round(float(hist["Close"].iloc[-1]), 2)


def get_cape() -> float:
    """获取 Shiller CAPE。
    
    优先从 dashboard_data.json 读取（generate_dashboard.py 已从 Shiller xls 获取），
    如果不可用则尝试 multpl.com，失败返回 -1。
    """
    # 优先读取本地 dashboard_data.json（数据更准确且无需网络请求）
    try:
        dashboard_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "dashboard_data.json"
        )
        if os.path.exists(dashboard_path):
            with open(dashboard_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cape_val = data.get("today", {}).get("cape", None)
            if cape_val and isinstance(cape_val, (int, float)) and cape_val > 0:
                print(f"[CAPE] 从 dashboard_data.json 获取: {cape_val}")
                return round(float(cape_val), 2)
    except Exception as e:
        print(f"[CAPE] 读取 dashboard_data.json 失败: {e}")

    # 备用：从 multpl.com 抓取
    try:
        url = "https://www.multpl.com/shiller-pe/table"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        html = resp.text

        pattern = r'class="v"[^>]*>\s*([\d.]+)\s*</td>'
        matches = re.findall(pattern, html)
        if matches:
            return round(float(matches[0]), 2)

        pattern2 = r'>\s*(\d{1,2}\.\d{1,2})\s*<'
        matches2 = re.findall(pattern2, html)
        if matches2:
            return round(float(matches2[0]), 2)

    except Exception as e:
        print(f"[CAPE] multpl.com 获取失败: {e}")

    return -1.0


def get_qqq_pe() -> float:
    """获取 QQQ PE 比率。
    
    优先从 dashboard_data.json 读取，如果不可用则尝试 yfinance，失败返回 -1。
    """
    # 优先读取本地 dashboard_data.json
    try:
        dashboard_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "dashboard_data.json"
        )
        if os.path.exists(dashboard_path):
            with open(dashboard_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            pe_val = data.get("today", {}).get("qqq_pe", None)
            if pe_val and isinstance(pe_val, (int, float)) and pe_val > 0:
                print(f"[QQQ PE] 从 dashboard_data.json 获取: {pe_val}")
                return round(float(pe_val), 2)
    except Exception as e:
        print(f"[QQQ PE] 读取 dashboard_data.json 失败: {e}")

    # 备用：从 yfinance 获取
    import yfinance as yf

    try:
        qqq = yf.Ticker("QQQ")
        info = qqq.info
        pe = info.get("trailingPE") or info.get("regularMarketPE")
        if pe is not None:
            return round(float(pe), 2)
    except Exception as e:
        print(f"[QQQ PE] yfinance 获取失败: {e}")

    return -1.0


# ---------------------------------------------------------------------------
# 信号判断
# ---------------------------------------------------------------------------

def check_sp500_signals(cfg: dict) -> dict:
    """检查标普500三大信号。"""
    thresholds = cfg.get("sp500_thresholds", {})
    lookback = cfg.get("lookback_days", 252)

    dd_threshold = thresholds.get("drawdown", -10.0)
    vix_threshold = thresholds.get("vix", 22.0)
    cape_threshold = thresholds.get("cape", 35.0)

    spy_data = get_etf_data("SPY", "SPY", lookback)
    vix_value = get_volatility_index("^VIX", "VIX")
    cape_value = get_cape()

    signals = []

    # 信号1: SPY 回撤
    dd = spy_data["drawdown"]
    dd_triggered = dd <= dd_threshold
    signals.append({
        "name": "SPY回撤",
        "triggered": dd_triggered,
        "value": dd,
        "threshold": dd_threshold,
        "icon": signal_icon_warn(dd_triggered, dd, dd_threshold, is_upper=False),
    })

    # 信号2: VIX
    vix_triggered = vix_value >= vix_threshold
    signals.append({
        "name": "VIX恐慌",
        "triggered": vix_triggered,
        "value": vix_value,
        "threshold": vix_threshold,
        "icon": signal_icon_warn(vix_triggered, vix_value, vix_threshold),
    })

    # 信号3: CAPE
    if cape_value > 0:
        cape_triggered = cape_value >= cape_threshold
        signals.append({
            "name": "CAPE估值",
            "triggered": cape_triggered,
            "value": cape_value,
            "threshold": cape_threshold,
            "icon": signal_icon_warn(cape_triggered, cape_value, cape_threshold),
        })
    else:
        signals.append({
            "name": "CAPE估值",
            "triggered": False,
            "value": cape_value,
            "threshold": cape_threshold,
            "icon": "❓",
        })

    triggered_count = sum(1 for s in signals if s["triggered"])

    return {
        "group_name": "标普500",
        "etf": spy_data,
        "vix": vix_value,
        "cape": cape_value,
        "signals": signals,
        "triggered_count": triggered_count,
        "position": get_position(triggered_count),
        "any_triggered": triggered_count > 0,
    }


def check_nasdaq100_signals(cfg: dict) -> dict:
    """检查纳斯达克100三大信号。"""
    thresholds = cfg.get("nasdaq100_thresholds", {})
    lookback = cfg.get("lookback_days", 252)

    dd_threshold = thresholds.get("drawdown", -10.0)
    vxn_threshold = thresholds.get("vxn", 22.0)
    qqq_pe_threshold = thresholds.get("qqq_pe", 35.0)

    qqq_data = get_etf_data("QQQ", "QQQ", lookback)
    vxn_value = get_volatility_index("^VIX", "VIX")  # VXN数据源不稳定，用VIX代理
    qqq_pe_value = get_qqq_pe()

    signals = []

    # 信号1: QQQ 回撤
    dd = qqq_data["drawdown"]
    dd_triggered = dd <= dd_threshold
    signals.append({
        "name": "QQQ回撤",
        "triggered": dd_triggered,
        "value": dd,
        "threshold": dd_threshold,
        "icon": signal_icon_warn(dd_triggered, dd, dd_threshold, is_upper=False),
    })

    # 信号2: VIX（VXN代理）
    vxn_triggered = vxn_value >= vxn_threshold
    signals.append({
        "name": "VIX恐慌",
        "triggered": vxn_triggered,
        "value": vxn_value,
        "threshold": vxn_threshold,
        "icon": signal_icon_warn(vxn_triggered, vxn_value, vxn_threshold),
    })

    # 信号3: QQQ PE
    if qqq_pe_value > 0:
        pe_triggered = qqq_pe_value >= qqq_pe_threshold
        signals.append({
            "name": "QQQ PE估值",
            "triggered": pe_triggered,
            "value": qqq_pe_value,
            "threshold": qqq_pe_threshold,
            "icon": signal_icon_warn(pe_triggered, qqq_pe_value, qqq_pe_threshold),
        })
    else:
        signals.append({
            "name": "QQQ PE估值",
            "triggered": False,
            "value": qqq_pe_value,
            "threshold": qqq_pe_threshold,
            "icon": "❓",
        })

    triggered_count = sum(1 for s in signals if s["triggered"])

    return {
        "group_name": "纳斯达克100",
        "etf": qqq_data,
        "vix": vxn_value,
        "qqq_pe": qqq_pe_value,
        "signals": signals,
        "triggered_count": triggered_count,
        "position": get_position(triggered_count),
        "any_triggered": triggered_count > 0,
    }


def check_all_signals(cfg: dict) -> dict:
    """同时检查标普500和纳斯达克100的所有信号。"""
    sp500 = check_sp500_signals(cfg)
    nasdaq100 = check_nasdaq100_signals(cfg)

    return {
        "sp500": sp500,
        "nasdaq100": nasdaq100,
        "any_triggered": sp500["any_triggered"] or nasdaq100["any_triggered"],
        "total_triggered": sp500["triggered_count"] + nasdaq100["triggered_count"],
    }


# ---------------------------------------------------------------------------
# 报告构建（新精简格式）
# ---------------------------------------------------------------------------

def format_dd(dd_value: float) -> str:
    """格式化回撤值。"""
    if dd_value == 0:
        return "0%(新高)"
    return f"{dd_value}%"


def build_daily_report(result: dict, cfg: dict) -> str:
    """
    构建精简版每日报告。
    SPY和QQQ各一行指标，加上调仓建议和趋势评分。
    """
    now_bjt = datetime.now(BJT).strftime("%Y-%m-%d")
    dashboard_url = cfg.get("dashboard_url", "")

    sp = result["sp500"]
    nd = result["nasdaq100"]
    spy = sp["etf"]
    qqq = nd["etf"]

    sp_pos = sp["position"]
    nd_pos = nd["position"]
    nd_triggered = nd["triggered_count"]

    # 获取调仓配比
    alloc = get_allocation(nd_triggered, cfg)
    
    # 计算趋势评分（基于QQQ的DD/VIX/PE）
    dd_val = qqq["drawdown"]
    vix_val = nd["vix"]
    pe_val = nd["qqq_pe"]
    trend = compute_trend_score(dd_val, vix_val, pe_val, cfg)

    lines = [
        f"**📊 美股信号日报 | {now_bjt}**",
        "",
        f"> SPY ${spy['price']} | QQQ ${qqq['price']}",
        "",
        f"**标普500 (SPY)**",
    ]

    # SPY 指标行
    spy_sigs = sp["signals"]
    sig_strs = []
    for s in spy_sigs:
        val_str = format_dd(s["value"]) if s["name"] == "SPY回撤" else str(s["value"])
        threshold_hint = f"(≥{s['threshold']})" if s["triggered"] else ""
        sig_strs.append(f"{s['name']} {val_str} {s['icon']}{threshold_hint}")

    lines.append(f"> {' | '.join(sig_strs)}")
    lines.append(f"> {sp['triggered_count']}/3 触发 → {sp_pos[0]}，{sp_pos[1]}")
    lines.append("")

    # QQQ 指标行
    lines.append("**纳斯达克100 (QQQ)**")
    nd_sigs = nd["signals"]
    nd_strs = []
    for s in nd_sigs:
        val_str = format_dd(s["value"]) if s["name"] == "QQQ回撤" else str(s["value"])
        threshold_hint = f"(≥{s['threshold']})" if s["triggered"] else ""
        nd_strs.append(f"{s['name']} {val_str} {s['icon']}{threshold_hint}")

    lines.append(f"> {' | '.join(nd_strs)}")
    lines.append(f"> {nd_triggered}/3 触发 → {nd_pos[0]}")

    # 调仓建议行
    tqqq_pct = int(alloc["tqqq"] * 100)
    qqq_pct = int(alloc["qqq"] * 100)
    cash_pct = int(alloc["cash"] * 100)
    
    alloc_str = f"TQQQ {tqqq_pct}%"
    if tqqq_pct == 0:
        alloc_str = f"QQQ {qqq_pct}%"
    elif qqq_pct > 0:
        alloc_str = f"TQQQ {tqqq_pct}% | QQQ {qqq_pct}%"
    
    if cash_pct > 0:
        alloc_str += f" | 现金 {cash_pct}%"
    
    lines.append(f"> 💰 调仓建议：**{alloc_str}** ← {alloc['hint']}")

    # 趋势评分行
    trend_score = trend["score"]
    trend_step = trend["step"]
    trend_dir = trend["trend_dir"]
    trend_hint = trend["trend_hint"]
    
    lines.append(f"> 📊 趋势评分：{trend_score} (S{trend_step}) {trend_dir} {trend_hint}")

    if dashboard_url:
        lines.append("")
        lines.append(f"[🔗 在线图表]({dashboard_url})")

    return "\n".join(lines)


def build_alert_report(result: dict, cfg: dict) -> str:
    """
    构建告警版推送。信号触发时使用，更醒目，含调仓建议和趋势评分。
    """
    now_bjt = datetime.now(BJT).strftime("%Y-%m-%d")
    dashboard_url = cfg.get("dashboard_url", "")

    sp = result["sp500"]
    nd = result["nasdaq100"]
    spy = sp["etf"]
    qqq = nd["etf"]

    sp_pos = sp["position"]
    nd_pos = nd["position"]
    nd_triggered = nd["triggered_count"]

    # 获取调仓配比
    alloc = get_allocation(nd_triggered, cfg)
    
    # 计算趋势评分
    dd_val = qqq["drawdown"]
    vix_val = nd["vix"]
    pe_val = nd["qqq_pe"]
    trend = compute_trend_score(dd_val, vix_val, pe_val, cfg)

    # 根据严重程度选择标题
    total = result["total_triggered"]
    if total >= 4:
        title_prefix = "🔴🔴"
    elif total >= 2:
        title_prefix = "⚠️"
    else:
        title_prefix = "⚠️"

    lines = [
        f"**{title_prefix} 美股信号告警 | {now_bjt}**",
        "",
        f"> SPY ${spy['price']} | QQQ ${qqq['price']}",
        "",
    ]

    # SPY 部分
    sp_color = "🔴" if sp["triggered_count"] >= 2 else "🟡" if sp["triggered_count"] == 1 else "✅"
    lines.append(f"**标普500 (SPY) — {sp['triggered_count']}/3 {sp_color}**")

    spy_sigs = sp["signals"]
    sig_strs = []
    for s in spy_sigs:
        val_str = format_dd(s["value"]) if s["name"] == "SPY回撤" else str(s["value"])
        threshold_hint = f"(≥{s['threshold']})" if s["triggered"] else ""
        sig_strs.append(f"{s['name']} {val_str} {s['icon']}{threshold_hint}")

    lines.append(f"> {' | '.join(sig_strs)}")
    lines.append(f"> {sp_pos[0]}：{sp_pos[1]}")
    lines.append("")

    # QQQ 部分
    nd_color = "🔴" if nd["triggered_count"] >= 2 else "🟡" if nd["triggered_count"] == 1 else "✅"
    lines.append(f"**纳斯达克100 (QQQ) — {nd['triggered_count']}/3 {nd_color}**")

    nd_sigs = nd["signals"]
    nd_strs = []
    for s in nd_sigs:
        val_str = format_dd(s["value"]) if s["name"] == "QQQ回撤" else str(s["value"])
        threshold_hint = f"(≥{s['threshold']})" if s["triggered"] else ""
        nd_strs.append(f"{s['name']} {val_str} {s['icon']}{threshold_hint}")

    lines.append(f"> {' | '.join(nd_strs)}")
    lines.append(f"> {nd_pos[0]}：{nd_pos[1]}")

    # 调仓建议行
    tqqq_pct = int(alloc["tqqq"] * 100)
    qqq_pct = int(alloc["qqq"] * 100)
    cash_pct = int(alloc["cash"] * 100)
    
    alloc_str = f"TQQQ {tqqq_pct}%"
    if tqqq_pct == 0:
        alloc_str = f"QQQ {qqq_pct}%"
    elif qqq_pct > 0:
        alloc_str = f"TQQQ {tqqq_pct}% | QQQ {qqq_pct}%"
    
    if cash_pct > 0:
        alloc_str += f" | 现金 {cash_pct}%"
    
    lines.append(f"> 💰 调仓建议：**{alloc_str}** ← {alloc['hint']}")

    # 趋势评分行
    lines.append(f"> 📊 趋势评分：{trend['score']} (S{trend['step']}) {trend['trend_dir']} {trend['trend_hint']}")

    if dashboard_url:
        lines.append("")
        lines.append(f"[🔗 在线图表]({dashboard_url})")

    return "\n".join(lines)


def build_no_trade_report(cfg: dict) -> str:
    """构建非交易日简短通知。"""
    now_bjt = datetime.now(BJT).strftime("%Y-%m-%d")
    dashboard_url = cfg.get("dashboard_url", "")

    # 尝试获取最新价格（即使非交易日，yfinance 仍有上一个交易日数据）
    try:
        import yfinance as yf
        spy_price = round(float(yf.Ticker("SPY").history(period="5d")["Close"].iloc[-1]), 2)
        qqq_price = round(float(yf.Ticker("QQQ").history(period="5d")["Close"].iloc[-1]), 2)
    except Exception:
        spy_price = "—"
        qqq_price = "—"

    lines = [
        f"**📊 美股信号日报 | {now_bjt}（非交易日）**",
        "",
        f"> 非交易日，数据无变化",
        f"> SPY ${spy_price} | QQQ ${qqq_price}",
        f"> 信号维持昨日判断",
    ]

    if dashboard_url:
        lines.append("")
        lines.append(f"[🔗 在线图表]({dashboard_url})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 企业微信推送
# ---------------------------------------------------------------------------

def send_wechat_webhook(webhook_url: str, markdown_content: str) -> dict:
    """发送 Markdown 消息到企业微信群机器人。"""
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "content": markdown_content,
        },
    }

    resp = requests.post(
        webhook_url,
        json=payload,
        timeout=15,
        headers={"Content-Type": "application/json"},
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# 非交易日检测
# ---------------------------------------------------------------------------

def is_us_trading_day() -> bool:
    """
    检测当前北京时间是否为美股交易日。
    北京时间周六(6)/周日(0)为非交易日。
    美股假日未在此检测（简单版），但周六周日一定不交易。
    """
    now_bjt = datetime.now(BJT)
    weekday = now_bjt.weekday()
    # 周六=5, 周日=6 在北京时间
    return weekday not in (5, 6)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(mode: str = "daily") -> dict:
    """
    执行完整的信号检查 + 推送流程。

    Args:
        mode: "daily"=每日报告(始终推送); "alert"=仅告警推送;
              "no_trade"=非交易日简报
    """
    cfg = load_config()

    if not cfg.get("webhook_url"):
        print("[WARN] 未配置 webhook_url，跳过企业微信推送")
        webhook_url = None
    else:
        webhook_url = cfg["webhook_url"]

    print("=" * 60)
    print(f"[{datetime.now(BJT).strftime('%Y-%m-%d %H:%M:%S')}] 开始检测美股双指数信号...")
    print(f"模式: {mode}")
    print("=" * 60)

    # 非交易日模式
    if mode == "no_trade":
        md_content = build_no_trade_report(cfg)
        print("\n[非交易日] 生成简报")
        print(md_content)

        if webhook_url:
            print("\n[推送] 发送非交易日简报...")
            push_result = send_wechat_webhook(webhook_url, md_content)
            print(f"[推送] 结果: {push_result}")
        else:
            push_result = None
            print("[推送] 无 webhook_url，跳过")

        return {"mode": "no_trade", "pushed": webhook_url is not None, "push_result": push_result}

    # 正常交易日
    result = check_all_signals(cfg)

    # 打印结果
    spy = result["sp500"]["etf"]
    qqq = result["nasdaq100"]["etf"]
    print(f"\n[标普500] SPY ${spy['price']}  回撤 {spy['drawdown']}%  峰值 ${spy['peak']}")
    print(f"[标普500] VIX {result['sp500']['vix']}  CAPE {result['sp500']['cape']}")
    print(f"[标普500] {result['sp500']['triggered_count']}/3 → {result['sp500']['position'][0]}")

    print(f"\n[纳斯达克100] QQQ ${qqq['price']}  回撤 {qqq['drawdown']}%  峰值 ${qqq['peak']}")
    print(f"[纳斯达克100] VIX {result['nasdaq100']['vix']}  PE {result['nasdaq100']['qqq_pe']}")
    print(f"[纳斯达克100] {result['nasdaq100']['triggered_count']}/3 → {result['nasdaq100']['position'][0]}")

    for sig in result["sp500"]["signals"]:
        status = "触发" if sig["triggered"] else "正常"
        print(f"  [{status}] {sig['name']}: {sig['value']}")

    for sig in result["nasdaq100"]["signals"]:
        status = "触发" if sig["triggered"] else "正常"
        print(f"  [{status}] {sig['name']}: {sig['value']}")

    # 选择报告格式
    if mode == "alert":
        # 仅告警模式：只在信号触发时推送
        if not result["any_triggered"]:
            print("\n[推送] 无信号触发，跳过推送")
            return {
                "sp500": {"triggered_count": result["sp500"]["triggered_count"]},
                "nasdaq100": {"triggered_count": result["nasdaq100"]["triggered_count"]},
                "any_triggered": False,
                "pushed": False,
            }
        md_content = build_alert_report(result, cfg)
    else:
        # 每日报告模式：始终推送
        md_content = build_daily_report(result, cfg)

    print(f"\n--- 推送内容 ---\n{md_content}\n--- END ---")

    # 推送
    push_result = None
    if webhook_url:
        print("[推送] 发送企业微信通知...")
        push_result = send_wechat_webhook(webhook_url, md_content)
        print(f"[推送] 结果: {push_result}")
    else:
        print("[推送] 无 webhook_url，跳过")

    print("\n" + "=" * 60)
    print("检测完成。")
    print("=" * 60)

    return {
        "sp500": {
            "spy_price": spy["price"],
            "spy_drawdown": spy["drawdown"],
            "vix": result["sp500"]["vix"],
            "cape": result["sp500"]["cape"],
            "triggered_count": result["sp500"]["triggered_count"],
            "position": result["sp500"]["position"][0],
        },
        "nasdaq100": {
            "qqq_price": qqq["price"],
            "qqq_drawdown": qqq["drawdown"],
            "vix": result["nasdaq100"]["vix"],
            "qqq_pe": result["nasdaq100"]["qqq_pe"],
            "triggered_count": result["nasdaq100"]["triggered_count"],
            "position": result["nasdaq100"]["position"][0],
        },
        "any_triggered": result["any_triggered"],
        "pushed": webhook_url is not None,
        "push_result": push_result,
    }


# ---------------------------------------------------------------------------
# 腾讯云函数入口
# ---------------------------------------------------------------------------

def main_handler(event: dict, context: dict) -> dict:
    """
    腾讯云函数 SCF 入口函数。

    支持 event 参数：
      - event.get("mode", "daily"): "daily"/"alert"/"no_trade"
      - event.get("always_push", True): True=始终推送, False=仅告警
    """
    try:
        mode = "daily"
        if event and isinstance(event, dict):
            if "mode" in event:
                mode = event["mode"]
            elif event.get("always_push") is False:
                mode = "alert"

        output = run(mode=mode)

        return {
            "statusCode": 200,
            "msg": "success",
            "data": output,
        }

    except Exception as e:
        error_msg = f"执行失败: {e}\n{traceback.format_exc()}"
        print(error_msg)
        return {
            "statusCode": 500,
            "msg": "error",
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# 本地运行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # python signal_bot.py           # 每日报告（始终推送）
    # python signal_bot.py --alert   # 仅告警推送
    # python signal_bot.py --no-trade # 非交易日简报
    mode = "daily"
    if "--alert" in sys.argv:
        mode = "alert"
    elif "--no-trade" in sys.argv:
        mode = "no_trade"

    result = main_handler({"mode": mode}, None)
    print(f"\n返回值:\n{json.dumps(result, indent=2, ensure_ascii=False)}")
