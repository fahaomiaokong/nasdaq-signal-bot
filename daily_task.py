#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股信号每日任务 - 合并脚本
============================
依次执行：
  1. generate_dashboard.py → 更新仪表盘数据 + HTML
  2. signal_bot.py → 判断信号 → 推送企业微信
  3. 输出运行摘要

适用于 GitHub Actions 和本地定时调用。
"""

import json
import os
import sys
import subprocess
from datetime import datetime, timezone, timedelta

BJT = timezone(timedelta(hours=8))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def is_us_trading_day() -> bool:
    """北京时间周六(5)/周日(6)为非交易日。"""
    now_bjt = datetime.now(BJT)
    return now_bjt.weekday() not in (5, 6)


def run_generate_dashboard() -> dict:
    """运行仪表盘数据生成脚本。"""
    script = os.path.join(SCRIPT_DIR, "generate_dashboard.py")
    print("\n" + "=" * 60)
    print("[1/2] 更新仪表盘数据...")
    print("=" * 60)

    result = subprocess.run(
        [sys.executable, script],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=SCRIPT_DIR,
    )

    print(result.stdout)
    if result.stderr:
        print("[stderr]", result.stderr)

    if result.returncode != 0:
        print(f"[ERROR] generate_dashboard.py 失败 (rc={result.returncode})")
        return {"success": False, "error": result.stderr[:500]}
    return {"success": True}


def run_signal_bot(mode: str = "daily") -> dict:
    """运行信号推送脚本。"""
    script = os.path.join(SCRIPT_DIR, "signal_bot.py")
    print("\n" + "=" * 60)
    print(f"[2/2] 信号推送 (mode={mode})...")
    print("=" * 60)

    args = [sys.executable, script]
    if mode == "no_trade":
        args.append("--no-trade")
    # daily 模式不需要额外参数，signal_bot.py 默认就是 daily

    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=SCRIPT_DIR,
    )

    print(result.stdout)
    if result.stderr:
        print("[stderr]", result.stderr)

    if result.returncode != 0:
        print(f"[ERROR] signal_bot.py 失败 (rc={result.returncode})")
        return {"success": False, "error": result.stderr[:500]}
    return {"success": True}


def main():
    """主入口：依次执行仪表盘更新 + 信号推送。"""
    print("=" * 60)
    print(f"美股信号每日任务 | {datetime.now(BJT).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    trading_day = is_us_trading_day()
    print(f"交易日检测: {'交易日' if trading_day else '非交易日(周末)'}")

    # Step 1: 更新仪表盘（无论是否交易日都运行，确保数据最新）
    dashboard_result = run_generate_dashboard()

    # Step 2: 信号推送
    if trading_day:
        bot_result = run_signal_bot(mode="daily")
    else:
        bot_result = run_signal_bot(mode="no_trade")

    # 嵌入数据到 HTML
    print("\n" + "=" * 60)
    print("[后处理] 嵌入数据到 dashboard.html...")
    print("=" * 60)

    try:
        with open(os.path.join(SCRIPT_DIR, "dashboard_template.html"), "r", encoding="utf-8") as f:
            template = f.read()

        with open(os.path.join(SCRIPT_DIR, "dashboard_data.json"), "r", encoding="utf-8") as f:
            data = f.read()

        html = template.replace("%EMBED_DATA%", data)

        with open(os.path.join(SCRIPT_DIR, "dashboard.html"), "w", encoding="utf-8") as f:
            f.write(html)

        # 同时复制为 index.html，用于 GitHub Pages 默认入口
        import shutil
        shutil.copy2(
            os.path.join(SCRIPT_DIR, "dashboard.html"),
            os.path.join(SCRIPT_DIR, "index.html"),
        )

        print("[OK] dashboard.html + index.html 已更新")
    except Exception as e:
        print(f"[ERROR] HTML 嵌入失败: {e}")

    # 运行摘要
    print("\n" + "=" * 60)
    print("运行摘要")
    print("=" * 60)
    print(f"  交易日: {'是' if trading_day else '否(周末)'}")
    print(f"  仪表盘更新: {'成功' if dashboard_result['success'] else '失败'}")
    print(f"  信号推送: {'成功' if bot_result['success'] else '失败'}")

    overall_success = dashboard_result["success"] and bot_result["success"]
    print(f"\n  总体: {'成功 ✅' if overall_success else '失败 ❌'}")

    return {"success": overall_success, "dashboard": dashboard_result, "bot": bot_result}


if __name__ == "__main__":
    result = main()
    sys.exit(0 if result["success"] else 1)
