#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股三信号仪表盘 - 数据生成脚本（日度版 + 日度CAPE/PE）
======================================================
优先从 yfinance 获取日度历史数据（SPY/QQQ/VIX）。
CAPE/QQQ_PE 从 Shiller 数据计算日度值：
  daily CAPE = Shiller月度CAPE × (SPY日度价 / SPY月均价)
  daily PE   = 月度PE × (QQQ日度价 / QQQ月均价)
分母（10年平均真实EPS / QQQ每股盈利）每月仅更新一次，
分子（价格）每天变动 → CAPE/PE 每天都波动。
yfinance/Shiller 下载失败时回退到内置月度数据插值。
"""

import json
import math
import os
import re
import time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import requests

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "dashboard_data.json")
BJT = timezone(timedelta(hours=8))


# ---------------------------------------------------------------------------
# 内置月度历史数据 (YYYY-MM: value) — 作为下载失败时的回退
# ---------------------------------------------------------------------------

SPY_MONTHLY = {
    "1990-01": 32.5, "1990-06": 35.0, "1991-01": 32.0, "1991-06": 37.5,
    "1992-01": 40.0, "1992-06": 39.0, "1993-01": 43.5, "1993-06": 44.0,
    "1994-01": 43.0, "1994-06": 44.5, "1995-01": 45.0, "1995-06": 52.0,
    "1996-01": 58.0, "1996-06": 65.0, "1997-01": 72.0, "1997-06": 85.0,
    "1998-01": 90.0, "1998-06": 110.0, "1999-01": 120.0, "1999-06": 130.0,
    "2000-01": 145.0, "2000-03": 136.0, "2000-06": 140.0, "2000-09": 110.0,
    "2001-01": 130.0, "2001-06": 120.0, "2001-09": 100.0, "2002-01": 110.0,
    "2002-06": 100.0, "2002-09": 80.0, "2003-01": 88.0, "2003-06": 98.0,
    "2004-01": 110.0, "2004-06": 112.0, "2005-01": 115.0, "2005-06": 120.0,
    "2006-01": 125.0, "2006-06": 127.0, "2007-01": 140.0, "2007-06": 150.0,
    "2007-10": 155.0, "2008-01": 130.0, "2008-06": 125.0, "2008-09": 110.0,
    "2008-10": 85.0, "2008-11": 85.0, "2009-01": 82.0, "2009-03": 75.0,
    "2009-06": 95.0, "2009-09": 105.0, "2010-01": 110.0, "2010-06": 105.0,
    "2011-01": 125.0, "2011-06": 130.0, "2011-09": 110.0, "2012-01": 125.0,
    "2012-06": 135.0, "2013-01": 145.0, "2013-06": 160.0, "2014-01": 180.0,
    "2014-06": 195.0, "2015-01": 200.0, "2015-06": 205.0, "2015-08": 195.0,
    "2016-01": 190.0, "2016-06": 210.0, "2017-01": 225.0, "2017-06": 240.0,
    "2018-01": 270.0, "2018-02": 255.0, "2018-06": 270.0, "2018-10": 260.0,
    "2018-12": 245.0, "2019-01": 260.0, "2019-06": 290.0, "2020-01": 320.0,
    "2020-03": 270.0, "2020-06": 310.0, "2020-09": 335.0, "2021-01": 370.0,
    "2021-06": 420.0, "2021-09": 430.0, "2021-12": 450.0, "2022-01": 430.0,
    "2022-06": 380.0, "2022-09": 360.0, "2022-12": 380.0, "2023-01": 395.0,
    "2023-06": 430.0, "2023-09": 430.0, "2023-12": 470.0, "2024-01": 480.0,
    "2024-06": 530.0, "2024-09": 560.0, "2024-12": 590.0, "2025-01": 600.0,
    "2025-03": 560.0, "2025-06": 620.0,
}

QQQ_MONTHLY = {
    "1990-01": 1.0, "1990-06": 1.2, "1991-01": 1.3, "1991-06": 1.5,
    "1992-01": 1.6, "1992-06": 1.5, "1993-01": 1.8, "1993-06": 2.0,
    "1994-01": 2.0, "1994-06": 2.2, "1995-01": 2.3, "1995-06": 3.0,
    "1996-01": 3.5, "1996-06": 4.5, "1997-01": 5.5, "1997-06": 8.0,
    "1998-01": 9.0, "1998-06": 12.0, "1999-01": 15.0, "1999-06": 22.0,
    "2000-01": 42.0, "2000-03": 35.0, "2000-06": 38.0, "2000-09": 25.0,
    "2001-01": 35.0, "2001-06": 28.0, "2001-09": 22.0, "2002-01": 27.0,
    "2002-06": 24.0, "2002-09": 18.0, "2003-01": 22.0, "2003-06": 30.0,
    "2004-01": 35.0, "2004-06": 36.0, "2005-01": 38.0, "2005-06": 40.0,
    "2006-01": 42.0, "2006-06": 43.0, "2007-01": 45.0, "2007-06": 50.0,
    "2007-10": 52.0, "2008-01": 46.0, "2008-06": 44.0, "2008-09": 38.0,
    "2008-10": 28.0, "2008-11": 27.0, "2009-01": 26.0, "2009-03": 24.0,
    "2009-06": 35.0, "2009-09": 40.0, "2010-01": 42.0, "2010-06": 40.0,
    "2011-01": 55.0, "2011-06": 57.0, "2011-09": 48.0, "2012-01": 55.0,
    "2012-06": 60.0, "2013-01": 65.0, "2013-06": 72.0, "2014-01": 85.0,
    "2014-06": 90.0, "2015-01": 105.0, "2015-06": 110.0, "2015-08": 100.0,
    "2016-01": 100.0, "2016-06": 115.0, "2017-01": 125.0, "2017-06": 140.0,
    "2018-01": 165.0, "2018-02": 155.0, "2018-06": 170.0, "2018-10": 160.0,
    "2018-12": 150.0, "2019-01": 160.0, "2019-06": 190.0, "2020-01": 210.0,
    "2020-03": 170.0, "2020-06": 230.0, "2020-09": 280.0, "2021-01": 310.0,
    "2021-06": 350.0, "2021-09": 370.0, "2021-12": 400.0, "2022-01": 380.0,
    "2022-06": 290.0, "2022-09": 280.0, "2022-12": 270.0, "2023-01": 280.0,
    "2023-06": 360.0, "2023-09": 370.0, "2023-12": 420.0, "2024-01": 430.0,
    "2024-06": 480.0, "2024-09": 490.0, "2024-12": 520.0, "2025-01": 530.0,
    "2025-03": 470.0, "2025-06": 530.0,
}

VIX_MONTHLY = {
    "1990-01": 25.0, "1990-06": 20.0, "1991-01": 20.0, "1991-06": 16.0,
    "1992-01": 17.0, "1992-06": 16.5, "1993-01": 14.0, "1993-06": 13.0,
    "1994-01": 13.5, "1994-06": 15.0, "1995-01": 14.0, "1995-06": 12.0,
    "1996-01": 14.0, "1996-06": 13.5, "1997-01": 16.0, "1997-06": 18.0,
    "1998-01": 16.0, "1998-06": 22.0, "1999-01": 25.0, "1999-06": 20.0,
    "2000-01": 23.0, "2000-03": 30.0, "2000-06": 25.0, "2000-09": 28.0,
    "2001-01": 24.0, "2001-06": 22.0, "2001-09": 35.0, "2002-01": 22.0,
    "2002-06": 28.0, "2002-09": 40.0, "2003-01": 28.0, "2003-06": 20.0,
    "2004-01": 16.0, "2004-06": 15.0, "2005-01": 14.0, "2005-06": 13.0,
    "2006-01": 12.5, "2006-06": 13.0, "2007-01": 12.0, "2007-06": 15.0,
    "2007-10": 18.0, "2008-01": 22.0, "2008-06": 25.0, "2008-09": 45.0,
    "2008-10": 60.0, "2008-11": 55.0, "2009-01": 42.0, "2009-03": 40.0,
    "2009-06": 28.0, "2009-09": 25.0, "2010-01": 20.0, "2010-06": 25.0,
    "2011-01": 16.0, "2011-06": 18.0, "2011-09": 35.0, "2012-01": 18.0,
    "2012-06": 16.0, "2013-01": 14.0, "2013-06": 15.0, "2014-01": 13.5,
    "2014-06": 12.0, "2015-01": 15.0, "2015-06": 14.0, "2015-08": 22.0,
    "2016-01": 20.0, "2016-06": 15.0, "2017-01": 12.0, "2017-06": 11.0,
    "2018-01": 12.0, "2018-02": 28.0, "2018-06": 14.0, "2018-10": 22.0,
    "2018-12": 25.0, "2019-01": 16.0, "2019-06": 15.0, "2020-01": 14.0,
    "2020-03": 60.0, "2020-06": 28.0, "2020-09": 28.0, "2021-01": 22.0,
    "2021-06": 16.0, "2021-09": 20.0, "2021-12": 17.0, "2022-01": 22.0,
    "2022-06": 28.0, "2022-09": 25.0, "2022-12": 22.0, "2023-01": 18.0,
    "2023-06": 14.0, "2023-09": 16.0, "2023-12": 13.0, "2024-01": 14.0,
    "2024-06": 13.0, "2024-09": 16.0, "2024-12": 15.0, "2025-01": 16.0,
    "2025-03": 22.0, "2025-06": 16.0,
}

CAPE_MONTHLY = {
    "1990-01": 15.0, "1990-06": 16.5, "1991-01": 17.5, "1991-06": 19.0,
    "1992-01": 19.0, "1992-06": 19.5, "1993-01": 20.0, "1993-06": 20.5,
    "1994-01": 21.0, "1994-06": 20.5, "1995-01": 18.5, "1995-06": 19.0,
    "1996-01": 20.5, "1996-06": 21.5, "1997-01": 22.0, "1997-06": 24.5,
    "1998-01": 25.0, "1998-06": 27.0, "1999-01": 30.0, "1999-06": 34.0,
    "2000-01": 44.2, "2000-03": 40.0, "2000-06": 38.0, "2000-09": 33.0,
    "2001-01": 30.5, "2001-06": 28.0, "2001-09": 26.0, "2002-01": 22.0,
    "2002-06": 24.0, "2002-09": 20.0, "2003-01": 18.0, "2003-06": 21.0,
    "2004-01": 21.5, "2004-06": 22.0, "2005-01": 23.0, "2005-06": 23.5,
    "2006-01": 20.5, "2006-06": 22.0, "2007-01": 24.0, "2007-06": 25.0,
    "2007-10": 26.0, "2008-01": 21.5, "2008-06": 20.0, "2008-09": 18.0,
    "2008-10": 15.0, "2008-11": 14.0, "2009-01": 13.3, "2009-03": 12.0,
    "2009-06": 16.0, "2009-09": 20.0, "2010-01": 21.5, "2010-06": 20.0,
    "2011-01": 22.5, "2011-06": 22.0, "2011-09": 18.5, "2012-01": 21.5,
    "2012-06": 21.0, "2013-01": 23.0, "2013-06": 24.0, "2014-01": 25.5,
    "2014-06": 26.0, "2015-01": 27.0, "2015-06": 27.5, "2015-08": 24.0,
    "2016-01": 24.0, "2016-06": 25.0, "2017-01": 28.5, "2017-06": 30.0,
    "2018-01": 33.0, "2018-02": 31.0, "2018-06": 30.0, "2018-10": 25.0,
    "2018-12": 24.0, "2019-01": 29.5, "2019-06": 30.0, "2020-01": 31.5,
    "2020-03": 24.5, "2020-06": 30.0, "2020-09": 33.0, "2021-01": 37.0,
    "2021-06": 38.0, "2021-09": 37.5, "2021-12": 38.5, "2022-01": 38.5,
    "2022-06": 28.0, "2022-09": 26.0, "2022-12": 28.0, "2023-01": 28.5,
    "2023-06": 31.0, "2023-09": 30.5, "2023-12": 32.0, "2024-01": 34.5,
    "2024-06": 35.0, "2024-09": 36.0, "2024-12": 37.0, "2025-01": 37.0,
    "2025-03": 35.0, "2025-06": 39.0,
}

QQQ_PE_MONTHLY = {
    "1990-01": 22.0, "1991-01": 24.0, "1992-01": 25.0, "1993-01": 26.0,
    "1994-01": 27.0, "1995-01": 24.0, "1996-01": 26.0, "1997-01": 28.0,
    "1998-01": 30.0, "1999-01": 50.0, "2000-01": 80.0, "2000-06": 60.0,
    "2001-01": 45.0, "2002-01": 30.0, "2003-01": 22.0, "2004-01": 28.0,
    "2005-01": 25.0, "2006-01": 24.0, "2007-01": 26.0, "2008-01": 22.0,
    "2009-01": 18.0, "2010-01": 25.0, "2011-01": 24.0, "2012-01": 22.0,
    "2013-01": 24.0, "2014-01": 27.0, "2015-01": 30.0, "2016-01": 28.0,
    "2017-01": 32.0, "2018-01": 38.0, "2019-01": 30.0, "2020-01": 34.0,
    "2020-03": 25.0, "2020-06": 35.0, "2021-01": 38.0, "2022-01": 40.0,
    "2022-06": 28.0, "2023-01": 30.0, "2024-01": 35.0, "2025-01": 36.0,
    "2025-06": 35.0,
}


# ---------------------------------------------------------------------------
# Shiller 数据下载与解析
# ---------------------------------------------------------------------------

SHILLER_URL = (
    "https://img1.wsimg.com/blobby/go/e5e77e0b-59d1-44d9-ab25-4763ac982e53"
    "/downloads/dd48d685-0157-4aa8-9ad3-375fd4eef22b/ie_data.xls"
    "?ver=1783022873468"
)
SHILLER_YALE_URL = "http://www.econ.yale.edu/~shiller/data/ie_data.xls"


def fetch_shiller_data():
    """下载 Shiller ie_data.xls，解析月度 CAPE 数据。
    返回 {YYYY-MM: CAPE_value} 或 None（失败时）。"""
    import tempfile
    try:
        import xlrd
    except ImportError:
        print("  xlrd 未安装，跳过 Shiller 数据下载")
        return None

    urls = [SHILLER_URL, SHILLER_YALE_URL]
    tmp_path = os.path.join(tempfile.gettempdir(), "ie_data.xls")

    for url in urls:
        try:
            print(f"  尝试下载 Shiller 数据: {url[:60]}...")
            headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code != 200 or len(resp.content) < 1000:
                continue

            with open(tmp_path, "wb") as f:
                f.write(resp.content)

            wb = xlrd.open_workbook(tmp_path)
            sheet = wb.sheet_by_name("Data")

            cape_map = {}
            for i in range(8, sheet.nrows):
                date_val = sheet.cell_value(i, 0)
                if date_val == "" or not isinstance(date_val, (int, float)):
                    continue
                year = int(date_val)
                month_frac = date_val - year
                month = int(round(month_frac * 100))
                if month < 1 or month > 12:
                    continue
                ym = f"{year}-{month:02d}"

                cape_val = sheet.cell_value(i, 12)
                if isinstance(cape_val, (int, float)) and cape_val > 0:
                    cape_map[ym] = round(float(cape_val), 4)

            if cape_map:
                latest_ym = max(cape_map.keys())
                print(f"  ✓ Shiller 数据: {len(cape_map)} 个月, 最早 {min(cape_map.keys())}, 最新 {latest_ym}")
                return cape_map

        except Exception as e:
            print(f"  下载/解析失败: {e}")
            continue

    print("  ✗ Shiller 数据下载失败，将使用内置 CAPE 数据")
    return None


# ---------------------------------------------------------------------------
# 日度数据获取
# ---------------------------------------------------------------------------

def fetch_daily_data():
    """从 yfinance 批量下载日度数据。返回 dict 或 None。"""
    import yfinance as yf

    print("  尝试 yfinance 批量下载日度数据...")
    try:
        data = yf.download(
            ["SPY", "QQQ", "^VIX"],
            start="1993-02-01",
            end=datetime.now(BJT).strftime("%Y-%m-%d"),
            group_by="ticker",
            auto_adjust=True,
            threads=True,
        )
        if data.empty:
            print("  yfinance 返回空数据，将回退到内置月度数据")
            return None

        results = {}
        for ticker, col_name in [("SPY", "SPY"), ("QQQ", "QQQ"), ("VIX", "^VIX")]:
            try:
                sub = data[col_name] if col_name in data.columns.get_level_values(0) else None
                if sub is None or sub.empty:
                    if ticker == "VIX":
                        sub = data["^VIX"] if "^VIX" in data.columns.get_level_values(0) else None
                    continue
                closes = sub["Close"].dropna()
                if len(closes) == 0:
                    continue
                results[ticker] = {
                    "dates": [d.strftime("%Y-%m-%d") for d in closes.index],
                    "values": [round(float(v), 2) for v in closes.values],
                }
                print(f"  {ticker}: {len(closes)} 个交易日, {closes.index[0].strftime('%Y-%m-%d')} ~ {closes.index[-1].strftime('%Y-%m-%d')}")
            except Exception as e:
                print(f"  {ticker} 提取失败: {e}")

        if len(results) < 2:
            print("  获取数据不足，将回退到内置月度数据")
            return None

        return results

    except Exception as e:
        print(f"  yfinance 下载失败: {e}")
        return None


def try_fetch_cape_live():
    """尝试从 multpl.com 获取最新 CAPE 值。"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }
        resp = requests.get("https://www.multpl.com/shiller-pe/table", headers=headers, timeout=10)
        if resp.status_code == 200:
            pattern = r'class="v"[^>]*>\s*([\d.]+)\s*</td>'
            matches = re.findall(pattern, resp.text)
            if matches:
                val = round(float(matches[0]), 2)
                print(f"  最新 CAPE: {val}")
                return val
    except Exception:
        pass
    return None


def try_fetch_qqq_pe_live():
    """尝试获取最新 QQQ PE 值。"""
    import yfinance as yf
    try:
        qqq = yf.Ticker("QQQ")
        info = qqq.info
        pe = info.get("trailingPE") or info.get("regularMarketPE")
        if pe is not None:
            val = round(float(pe), 2)
            print(f"  最新 QQQ PE: {val}")
            return val
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def month_to_ordinal(date_str):
    """将 YYYY-MM 转为序号。"""
    y, m = date_str.split("-")
    return int(y) * 12 + int(m)


def day_to_ordinal(date_str):
    """将 YYYY-MM-DD 转为天数序号。"""
    y, m, d = date_str.split("-")
    from datetime import date
    dt = date(int(y), int(m), int(d))
    base = date(1990, 1, 1)
    return (dt - base).days


def interpolate_monthly_to_daily(monthly_dict, daily_dates):
    """将月度数据插值到日度时间轴。"""
    sorted_keys = sorted(monthly_dict.keys())
    result = []

    for dd in daily_dates:
        ym = dd[:7]
        if ym in monthly_dict:
            result.append(monthly_dict[ym])
        else:
            d_ord = day_to_ordinal(dd)
            before = None
            after = None
            for k in sorted_keys:
                y_k, m_k = k.split("-")
                from datetime import date as dt_cls
                k_date = dt_cls(int(y_k), int(m_k), 15)
                k_ord = (k_date - dt_cls(1990, 1, 1)).days
                k_val = monthly_dict[k]
                if k_ord < d_ord:
                    before = (k_ord, k_val)
                elif k_ord >= d_ord and after is None:
                    after = (k_ord, k_val)
                    break
            if before and after:
                ratio = (d_ord - before[0]) / max(1, after[0] - before[0])
                val = before[1] + (after[1] - before[1]) * ratio
                result.append(round(val, 2))
            elif before:
                result.append(before[1])
            elif after:
                result.append(after[1])
            else:
                result.append(0)

    return result


def compute_monthly_avg_from_daily(daily_dates, daily_values):
    """从日度数据计算每月平均价格。返回 {YYYY-MM: avg_price}。"""
    month_data = defaultdict(list)
    for d, v in zip(daily_dates, daily_values):
        ym = d[:7]
        month_data[ym].append(v)

    result = {}
    for ym, vals in month_data.items():
        result[ym] = round(sum(vals) / len(vals), 4)
    return result


def compute_drawdown(closes: list) -> list:
    """计算每个价格相对于历史峰值的回撤百分比。"""
    peak = closes[0]
    dd_list = []
    for price in closes:
        if price > peak:
            peak = price
        dd = round((price - peak) / peak * 100, 2)
        dd_list.append(dd)
    return dd_list


def normalize(values: list) -> list:
    """归一化到 [0, 1]。"""
    min_v = min(values)
    max_v = max(values)
    range_v = max_v - min_v
    if range_v == 0:
        return [0.5] * len(values)
    return [(v - min_v) / range_v for v in values]


def normalize_log(values: list) -> list:
    """对数归一化到 [0, 1] — 适合跨度大的价格数据。"""
    min_v = min(values)
    offset = max(0, 1 - min_v)
    adjusted = [v + offset for v in values]
    log_vals = [math.log(v) for v in adjusted]
    min_log = min(log_vals)
    max_log = max(log_vals)
    range_log = max_log - min_log
    if range_log == 0:
        return [0.5] * len(values)
    return [(lv - min_log) / range_log for lv in log_vals]


def normalize_inverted(values: list) -> list:
    """反向归一化到 [0, 1] — 值越小归一化越高。用于 DD。"""
    norm = normalize(values)
    return [1.0 - n for n in norm]


# ---------------------------------------------------------------------------
# 日度 CAPE / QQQ_PE 计算
# ---------------------------------------------------------------------------

def compute_daily_cape(daily_dates, daily_spy_prices, shiller_cape_map, spy_monthly_avg_map):
    """计算日度 CAPE = Shiller月度CAPE × (SPY日度价 / SPY月均价)

    核心: 分母（10年平均真实EPS）每月仅更新，分子（价格）每天变动。
    同月内: daily_cape = monthly_cape × (spy_daily / spy_month_avg)
    Shiller 未覆盖月份: 用最近已知 CAPE + 最近月均价计算分母，再用日度价格除分母。
    回退路径: 用内置 CAPE_MONTHLY 数据。
    """
    cape_values = []

    # 用 Shiller 或内置数据作为月度 CAPE
    if shiller_cape_map:
        cape_monthly = shiller_cape_map
        print("  使用 Shiller 实际月度 CAPE 计算日度值")
    else:
        cape_monthly = CAPE_MONTHLY
        print("  使用内置月度 CAPE 计算日度值")

    # 最新月份之后: 计算分母并延续
    latest_cape_ym = max(cape_monthly.keys())
    latest_cape_val = cape_monthly[latest_cape_ym]
    latest_avg_ym = max(spy_monthly_avg_map.keys()) if spy_monthly_avg_map else latest_cape_ym
    latest_avg_val = spy_monthly_avg_map.get(latest_avg_ym, 0)

    # 最后已知月份的分母（SPY单位）
    # denominator_spy = spy_month_avg / cape_monthly
    # daily_cape = spy_daily / denominator_spy = spy_daily * cape_monthly / spy_month_avg
    if latest_avg_val > 0 and latest_cape_val > 0:
        last_denom = latest_avg_val / latest_cape_val
    else:
        last_denom = None

    for i, d in enumerate(daily_dates):
        ym = d[:7]
        spy_daily = daily_spy_prices[i]

        if ym in cape_monthly and ym in spy_monthly_avg_map:
            # 正常月份: 有 Shiller CAPE 和 SPY 月均价
            cape_m = cape_monthly[ym]
            avg_spy = spy_monthly_avg_map[ym]
            if avg_spy > 0 and cape_m > 0:
                daily_cape = cape_m * (spy_daily / avg_spy)
            else:
                daily_cape = cape_m
        elif last_denom and last_denom > 0:
            # Shiller 未覆盖的月份（通常是当前月）: 用最后已知分母
            daily_cape = spy_daily / last_denom
        elif ym in cape_monthly:
            # 有 CAPE 但没月均价（极罕见）: 直接用月度值
            daily_cape = cape_monthly[ym]
        else:
            # 插值回退
            cape_m = interpolate_monthly_to_daily(cape_monthly, [d])[0]
            daily_cape = cape_m

        cape_values.append(round(daily_cape, 4))

    return cape_values


def compute_daily_qqq_pe(daily_dates, daily_qqq_prices, qqq_pe_monthly_map, qqq_monthly_avg_map):
    """计算日度 QQQ PE = 月度PE × (QQQ日度价 / QQQ月均价)

    核心: 分母（QQQ每股盈利）每月仅更新，分子（价格）每天变动。
    QQQ_PE_MONTHLY 是稀疏的，需先插值到完整月份再计算日度值。
    """
    pe_values = []

    # 合并内置数据和可能的 live 数据
    pe_monthly = dict(QQQ_PE_MONTHLY)
    qqq_pe_live = try_fetch_qqq_pe_live()
    if qqq_pe_live:
        now_ym = datetime.now(BJT).strftime("%Y-%m")
        pe_monthly[now_ym] = qqq_pe_live
        print(f"  更新 QQQ PE: {now_ym} = {qqq_pe_live}")

    # 先把稀疏的 QQQ_PE_MONTHLY 插值到所有涉及的月份
    all_months_needed = sorted(set(d[:7] for d in daily_dates))
    pe_monthly_full = {}
    for ym in all_months_needed:
        if ym in pe_monthly:
            pe_monthly_full[ym] = pe_monthly[ym]
        else:
            # 用 interpolate_monthly_to_daily 插值（传入单个月份）
            val = interpolate_monthly_to_daily(pe_monthly, [ym + "-15"])[0]
            pe_monthly_full[ym] = val

    print(f"  QQQ PE 插值完成: {len(pe_monthly_full)} 个月 (原始 {len(pe_monthly)} 个月)")

    # 计算最后已知月份的分母（用于 Shiller 未覆盖的未来月份）
    latest_pe_ym = max(pe_monthly_full.keys())
    latest_pe_val = pe_monthly_full[latest_pe_ym]
    latest_avg_ym = max(qqq_monthly_avg_map.keys()) if qqq_monthly_avg_map else latest_pe_ym
    latest_avg_val = qqq_monthly_avg_map.get(latest_avg_ym, 0)

    if latest_avg_val > 0 and latest_pe_val > 0:
        last_denom = latest_avg_val / latest_pe_val
    else:
        last_denom = None

    for i, d in enumerate(daily_dates):
        ym = d[:7]
        qqq_daily = daily_qqq_prices[i]

        if ym in pe_monthly_full and ym in qqq_monthly_avg_map:
            pe_m = pe_monthly_full[ym]
            avg_qqq = qqq_monthly_avg_map[ym]
            if avg_qqq > 0 and pe_m > 0:
                daily_pe = pe_m * (qqq_daily / avg_qqq)
            else:
                daily_pe = pe_m
        elif last_denom and last_denom > 0:
            # 月均价缺失的罕见情况
            daily_pe = qqq_daily / last_denom
        elif ym in pe_monthly_full:
            daily_pe = pe_monthly_full[ym]
        else:
            daily_pe = pe_monthly_full.get(list(pe_monthly_full.keys())[-1], 30)

        pe_values.append(round(daily_pe, 4))

    return pe_values


# ---------------------------------------------------------------------------
# 信号判断
# ---------------------------------------------------------------------------

def assess_signals(spy_price, spy_dd, vix, cape, qqq_price, qqq_dd, qqq_pe):
    """根据当前指标值给出仓位建议。"""
    cfg = {
        "sp500_thresholds": {"drawdown": -10, "vix": 30, "cape": 30},
        "nasdaq100_thresholds": {"drawdown": -10, "vix": 30, "qqq_pe": 35},
    }

    sp_signals = [
        {"name": "SPY回撤", "triggered": spy_dd <= cfg["sp500_thresholds"]["drawdown"],
         "value": spy_dd, "threshold": cfg["sp500_thresholds"]["drawdown"]},
        {"name": "VIX恐慌", "triggered": vix >= cfg["sp500_thresholds"]["vix"],
         "value": vix, "threshold": cfg["sp500_thresholds"]["vix"]},
        {"name": "Shiller CAPE", "triggered": cape >= cfg["sp500_thresholds"]["cape"] if cape > 0 else False,
         "value": cape, "threshold": cfg["sp500_thresholds"]["cape"]},
    ]
    sp_triggered = sum(1 for s in sp_signals if s["triggered"])

    nd_signals = [
        {"name": "QQQ回撤", "triggered": qqq_dd <= cfg["nasdaq100_thresholds"]["drawdown"],
         "value": qqq_dd, "threshold": cfg["nasdaq100_thresholds"]["drawdown"]},
        {"name": "VIX恐慌(VXN代理)", "triggered": vix >= cfg["nasdaq100_thresholds"]["vix"],
         "value": vix, "threshold": cfg["nasdaq100_thresholds"]["vix"]},
        {"name": "QQQ PE估值", "triggered": qqq_pe >= cfg["nasdaq100_thresholds"]["qqq_pe"] if qqq_pe > 0 else False,
         "value": qqq_pe, "threshold": cfg["nasdaq100_thresholds"]["qqq_pe"]},
    ]
    nd_triggered = sum(1 for s in nd_signals if s["triggered"])

    def get_position(count):
        levels = {
            0: ("L1-满仓进攻", "所有信号正常，市场处于乐观状态"),
            1: ("L2-75%仓位", "一个信号触发，开始警惕"),
            2: ("L3-50%仓位", "两个信号触发，明显风险"),
            3: ("L4-25%仓位", "三个信号全部触发，极度危险"),
        }
        return levels.get(count, ("L5-100%现金", "超过3个信号触发，全部撤退"))

    return {
        "sp500": {"signals": sp_signals, "triggered_count": sp_triggered,
                  "position": get_position(sp_triggered)},
        "nasdaq100": {"signals": nd_signals, "triggered_count": nd_triggered,
                      "position": get_position(nd_triggered)},
    }


# ---------------------------------------------------------------------------
# 月度回退路径
# ---------------------------------------------------------------------------

def generate_daily_from_monthly():
    """从内置月度数据生成日度近似数据（回退路径）。"""
    all_months = sorted(set(
        list(SPY_MONTHLY.keys()) + list(QQQ_MONTHLY.keys()) +
        list(VIX_MONTHLY.keys()) + list(CAPE_MONTHLY.keys()) +
        list(QQQ_PE_MONTHLY.keys())
    ))

    daily_dates = [ym + "-15" for ym in all_months]

    spy_prices = interpolate_monthly_to_daily(SPY_MONTHLY, daily_dates)
    qqq_prices = interpolate_monthly_to_daily(QQQ_MONTHLY, daily_dates)
    vix_values = interpolate_monthly_to_daily(VIX_MONTHLY, daily_dates)
    cape_values = interpolate_monthly_to_daily(CAPE_MONTHLY, daily_dates)
    qqq_pe_values = interpolate_monthly_to_daily(QQQ_PE_MONTHLY, daily_dates)

    cape_live = try_fetch_cape_live()
    qqq_pe_live = try_fetch_qqq_pe_live()
    now_date = datetime.now(BJT).strftime("%Y-%m-15")
    if now_date not in daily_dates:
        daily_dates.append(now_date)
        spy_prices.append(SPY_MONTHLY.get(datetime.now(BJT).strftime("%Y-%m"), spy_prices[-1]))
        qqq_prices.append(QQQ_MONTHLY.get(datetime.now(BJT).strftime("%Y-%m"), qqq_prices[-1]))
        vix_values.append(VIX_MONTHLY.get(datetime.now(BJT).strftime("%Y-%m"), vix_values[-1]))
        if cape_live:
            cape_values.append(cape_live)
        else:
            cape_values.append(CAPE_MONTHLY.get(datetime.now(BJT).strftime("%Y-%m"), cape_values[-1]))
        if qqq_pe_live:
            qqq_pe_values.append(qqq_pe_live)
        else:
            qqq_pe_values.append(QQQ_PE_MONTHLY.get(datetime.now(BJT).strftime("%Y-%m"), qqq_pe_values[-1]))

    return daily_dates, spy_prices, qqq_prices, vix_values, cape_values, qqq_pe_values


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print(f"[{datetime.now(BJT).strftime('%Y-%m-%d %H:%M:%S')}] 开始生成日度仪表盘数据...")
    print("=" * 60)

    # 1. 尝试获取日度数据
    print("\n[1/5] 尝试获取日度数据...")
    daily_data = fetch_daily_data()

    use_yfinance = False
    if daily_data and "SPY" in daily_data and "QQQ" in daily_data:
        use_yfinance = True
        print("  ✓ 使用 yfinance 日度数据")
    else:
        print("  ✗ yfinance 不可用，使用内置月度数据插值")

    # 2. 下载 Shiller CAPE 数据
    print("\n[2/5] 下载 Shiller CAPE 数据...")
    shiller_cape_map = fetch_shiller_data()

    # 3. 构建统一日度时间轴 + 数据对齐
    print("\n[3/5] 构建日度时间轴 + 数据对齐...")

    if use_yfinance:
        spy_info = daily_data["SPY"]
        qqq_info = daily_data["QQQ"]
        vix_info = daily_data.get("VIX")

        daily_dates = spy_info["dates"]
        spy_prices = spy_info["values"]

        # QQQ: 对齐到 SPY 时间轴（交集）
        qqq_date_map = dict(zip(qqq_info["dates"], qqq_info["values"]))
        qqq_prices = []
        aligned_dates = []

        for i, d in enumerate(daily_dates):
            if d in qqq_date_map:
                aligned_dates.append(d)
                qqq_prices.append(qqq_date_map[d])

        spy_date_map = dict(zip(spy_info["dates"], spy_info["values"]))
        spy_prices = [spy_date_map[d] for d in aligned_dates]

        # VIX: 对齐
        if vix_info:
            vix_date_map = dict(zip(vix_info["dates"], vix_info["values"]))
            vix_values = []
            for d in aligned_dates:
                if d in vix_date_map:
                    vix_values.append(vix_date_map[d])
                else:
                    vix_values.append(vix_values[-1] if vix_values else 15.0)
        else:
            vix_values = interpolate_monthly_to_daily(VIX_MONTHLY, aligned_dates)

        # 计算月均价（用于日度 CAPE/PE 计算）
        spy_monthly_avg = compute_monthly_avg_from_daily(aligned_dates, spy_prices)
        qqq_monthly_avg = compute_monthly_avg_from_daily(aligned_dates, qqq_prices)
        print(f"  SPY 月均价: {len(spy_monthly_avg)} 个月, QQQ 月均价: {len(qqq_monthly_avg)} 个月")

        # 日度 CAPE 计算
        cape_values = compute_daily_cape(
            aligned_dates, spy_prices,
            shiller_cape_map, spy_monthly_avg,
        )

        # 日度 QQQ PE 计算
        qqq_pe_values = compute_daily_qqq_pe(
            aligned_dates, qqq_prices,
            None,  # 使用内置 QQQ_PE_MONTHLY
            qqq_monthly_avg,
        )

        daily_dates = aligned_dates
    else:
        # 回退路径
        daily_dates, spy_prices, qqq_prices, vix_values, cape_values, qqq_pe_values = \
            generate_daily_from_monthly()

    print(f"  时间轴: {daily_dates[0]} ~ {daily_dates[-1]}, {len(daily_dates)} 个数据点")

    # 验证 CAPE 值合理性
    cape_sample_indices = [0, len(cape_values)//4, len(cape_values)//2, len(cape_values)-1]
    print("  CAPE 日度值样本:")
    for idx in cape_sample_indices:
        print(f"    {daily_dates[idx]}: CAPE={cape_values[idx]}, SPY={spy_prices[idx]}")

    # 4. 计算 DD + 归一化
    print("\n[4/5] 计算回撤 + 归一化...")

    spy_dd = compute_drawdown(spy_prices)
    qqq_dd = compute_drawdown(qqq_prices)

    spy_price_norm = normalize_log(spy_prices)
    qqq_price_norm = normalize_log(qqq_prices)
    spy_dd_norm = normalize_inverted(spy_dd)
    qqq_dd_norm = normalize_inverted(qqq_dd)
    vix_norm = normalize(vix_values)
    cape_norm = normalize(cape_values)
    qqq_pe_norm = normalize(qqq_pe_values)

    # 当前值
    current_spy_price = spy_prices[-1]
    current_spy_dd = spy_dd[-1]
    current_qqq_price = qqq_prices[-1]
    current_qqq_dd = qqq_dd[-1]
    current_vix = vix_values[-1]
    current_cape = cape_values[-1]
    current_qqq_pe = qqq_pe_values[-1]

    # 5. 信号判断 + 组装输出
    print("\n[5/5] 信号判断 + 组装输出...")

    signals = assess_signals(
        current_spy_price, current_spy_dd, current_vix, current_cape,
        current_qqq_price, current_qqq_dd, current_qqq_pe,
    )

    # 6. 计算历史极值 + 关键时刻里程碑
    def find_extremes(values, dates_list):
        max_v = max(values)
        min_v = min(values)
        max_idx = values.index(max_v)
        min_idx = values.index(min_v)
        return {
            "max": round(max_v, 2),
            "max_date": dates_list[max_idx],
            "min": round(min_v, 2),
            "min_date": dates_list[min_idx],
        }

    def find_nearest_idx(dates_list, target):
        best_idx = 0
        best_diff = abs((datetime.strptime(dates_list[0], "%Y-%m-%d") -
                         datetime.strptime(target, "%Y-%m-%d")).days)
        for i, d in enumerate(dates_list):
            diff = abs((datetime.strptime(d, "%Y-%m-%d") -
                        datetime.strptime(target, "%Y-%m-%d")).days)
            if diff < best_diff:
                best_diff = diff
                best_idx = i
        return best_idx

    milestones = {
        "spy_dd": find_extremes(spy_dd, daily_dates),
        "qqq_dd": find_extremes(qqq_dd, daily_dates),
        "vix": find_extremes(vix_values, daily_dates),
        "cape": find_extremes(cape_values, daily_dates),
        "qqq_pe": find_extremes(qqq_pe_values, daily_dates),
    }

    key_events = [
        {"label": "互联网泡沫巅峰", "date": "2000-03-10", "desc": "2000年3月纳斯达克见顶，科技股泡沫破裂"},
        {"label": "互联网泡沫底部", "date": "2002-10-09", "desc": "纳指从巅峰暴跌82%，标普回撤45%"},
        {"label": "金融海啸前高", "date": "2007-10-09", "desc": "次贷危机前标普历史最高点"},
        {"label": "金融海啸深渊", "date": "2009-03-09", "desc": "标普从高点暴跌55%，VIX飙至49"},
        {"label": "COVID暴跌底", "date": "2020-03-23", "desc": "疫情冲击全球股市，VIX飙至61"},
        {"label": "2022熊市低点", "date": "2022-10-12", "desc": "加息周期中科技股大幅回调"},
        {"label": "2008危机高潮", "date": "2008-11-20", "desc": "VIX达80+，市场极度恐慌"},
        {"label": "2018年末恐慌", "date": "2018-12-24", "desc": "美联储加息引发短期恐慌"},
    ]

    for ev in key_events:
        idx = find_nearest_idx(daily_dates, ev["date"])
        ev["actual_date"] = daily_dates[idx]
        ev["spy_dd"] = round(spy_dd[idx], 2)
        ev["qqq_dd"] = round(qqq_dd[idx], 2)
        ev["vix"] = round(vix_values[idx], 2)
        ev["cape"] = round(cape_values[idx], 2)
        ev["qqq_pe"] = round(qqq_pe_values[idx], 2)
        ev["spy_price"] = round(spy_prices[idx], 2)
        ev["qqq_price"] = round(qqq_prices[idx], 2)

    milestones["key_events"] = key_events

    output = {
        "generated_at": datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S"),
        "granularity": "daily" if use_yfinance else "monthly_interpolated",
        "dates": daily_dates,
        "norm_info": {
            "price": "log",
            "dd": "inverted",
            "vix": "standard",
            "cape": "standard",
            "qqq_pe": "standard",
        },
        "cape_method": "shiller_daily" if shiller_cape_map else "monthly_interpolated",
        "spy": {
            "price_raw": spy_prices,
            "price_norm": spy_price_norm,
            "dd_raw": spy_dd,
            "dd_norm": spy_dd_norm,
            "price_min": round(min(spy_prices), 2),
            "price_max": round(max(spy_prices), 2),
            "dd_min": round(min(spy_dd), 2),
            "dd_max": round(max(spy_dd), 2),
            "current_price": current_spy_price,
            "current_dd": current_spy_dd,
        },
        "qqq": {
            "price_raw": qqq_prices,
            "price_norm": qqq_price_norm,
            "dd_raw": qqq_dd,
            "dd_norm": qqq_dd_norm,
            "price_min": round(min(qqq_prices), 2),
            "price_max": round(max(qqq_prices), 2),
            "dd_min": round(min(qqq_dd), 2),
            "dd_max": round(max(qqq_dd), 2),
            "current_price": current_qqq_price,
            "current_dd": current_qqq_dd,
        },
        "vix": {
            "raw": vix_values,
            "norm": vix_norm,
            "min": round(min(vix_values), 2),
            "max": round(max(vix_values), 2),
            "current": current_vix,
        },
        "cape": {
            "raw": cape_values,
            "norm": cape_norm,
            "min": round(min(cape_values), 2),
            "max": round(max(cape_values), 2),
            "current": current_cape,
        },
        "qqq_pe": {
            "raw": qqq_pe_values,
            "norm": qqq_pe_norm,
            "min": round(min(qqq_pe_values), 2),
            "max": round(max(qqq_pe_values), 2),
            "current": current_qqq_pe,
        },
        "milestones": milestones,
        "today": {
            "spy_price": current_spy_price,
            "spy_dd": current_spy_dd,
            "vix": current_vix,
            "cape": current_cape,
            "qqq_price": current_qqq_price,
            "qqq_dd": current_qqq_dd,
            "qqq_pe": current_qqq_pe,
            "sp500_signals": signals["sp500"],
            "nasdaq100_signals": signals["nasdaq100"],
        },
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False)

    print(f"\n数据已写入: {OUTPUT_FILE}")
    print(f"文件大小: {os.path.getsize(OUTPUT_FILE) / 1024:.1f} KB")
    print(f"数据粒度: {output['granularity']}")
    print(f"CAPE方法: {output['cape_method']}")
    print(f"时间跨度: {daily_dates[0]} ~ {daily_dates[-1]} ({len(daily_dates)} 个数据点)")
    print(f"当前值: SPY ${current_spy_price}, DD {current_spy_dd}%, VIX {current_vix}, CAPE {current_cape}")
    print(f"当前值: QQQ ${current_qqq_price}, DD {current_qqq_dd}%, PE {current_qqq_pe}")
    print("\n" + "=" * 60)
    print("数据生成完成!")
    print("=" * 60)

    return output


if __name__ == "__main__":
    main()
