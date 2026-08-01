# -*- coding: utf-8 -*-
"""轮询 data/daily_cache/manifest.json，直到今日日线同步完成。

就绪条件：
  - status == "completed"
  - sync_trade_date == 今天（YYYY-MM-DD）

非交易日直接跳过（退出码 0）。超时未就绪则退出码 1。
供 run_all_if_trading_day.py / limit_up.bat 在选股与分析前调用。
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime

from utils.daily_cache_reader import MANIFEST_PATH, load_manifest
from utils.trading_day import is_tradeday


def _parse_sync_trade_date(raw: object) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    # 兼容 2026-07-15 / 20260715
    digits = s.replace("-", "")[:8]
    if len(digits) == 8 and digits.isdigit():
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return s[:10]


def _manifest_ready(today: date) -> tuple[bool, str]:
    m = load_manifest()
    if not m:
        return False, f"manifest 不存在或无法读取: {MANIFEST_PATH}"
    status = str(m.get("status") or "").strip().lower()
    sync_day = _parse_sync_trade_date(m.get("sync_trade_date"))
    today_s = today.strftime("%Y-%m-%d")
    finished = str(m.get("finished_at") or "").strip()
    ok = status == "completed" and sync_day == today_s
    detail = (
        f"status={status or '-'} sync_trade_date={sync_day or '-'} "
        f"期望={today_s} finished_at={finished or '-'}"
    )
    return ok, detail


def main() -> int:
    parser = argparse.ArgumentParser(description="等待 daily_cache 今日同步完成")
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="轮询间隔秒数（默认 30）",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=7200,
        help="最长等待秒数（默认 7200=2小时）",
    )
    args = parser.parse_args()
    interval = max(5, int(args.interval))
    timeout = max(interval, int(args.timeout))

    today = date.today()
    if not is_tradeday(today):
        print(f"[跳过] {today} 不是交易日，无需等待 daily_cache。")
        return 0

    print(
        f"[等待] daily_cache 就绪：status=completed 且 sync_trade_date={today} "
        f"（间隔 {interval}s，超时 {timeout}s）"
    )
    print(f"[等待] manifest: {MANIFEST_PATH}")

    t0 = time.time()
    attempt = 0
    while True:
        attempt += 1
        ok, detail = _manifest_ready(today)
        elapsed = int(time.time() - t0)
        now = datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] #{attempt} 已等 {elapsed}s | {detail}")
        if ok:
            print("[就绪] daily_cache 今日同步已完成，可以开始选股。")
            return 0
        if elapsed >= timeout:
            print(
                f"[超时] 等待超过 {timeout}s，daily_cache 仍未就绪。"
                f" 请检查大 QMT「蚂蚁量化规则」是否在跑、15:35 同步是否失败。"
            )
            return 1
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
