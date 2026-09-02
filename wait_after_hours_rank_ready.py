# -*- coding: utf-8 -*-
"""轮询 data/after_hours_rank/{YYYYMMDD}/top10.csv，直到今日盘后量能表就绪。

就绪条件：
  - top10.csv 存在且至少 1 行数据（不含表头）

非交易日直接跳过（退出码 0）。超时未就绪则退出码 1。
供 run_all_if_trading_day.py 在导出 after_hours → COS 前调用。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

from utils.trading_day import is_tradeday

ROOT = Path(__file__).resolve().parent
RANK_DIR = ROOT / "data" / "after_hours_rank"


def _top10_path(day: date) -> Path:
    return RANK_DIR / day.strftime("%Y%m%d") / "top10.csv"


def _top10_ready(day: date) -> tuple[bool, str]:
    path = _top10_path(day)
    if not path.is_file():
        return False, f"top10 不存在: {path}"
    try:
        n = 0
        with path.open("r", encoding="utf-8-sig", errors="ignore") as f:
            for i, line in enumerate(f):
                if i == 0:
                    continue
                if line.strip():
                    n += 1
                    if n >= 1:
                        break
        mt = datetime.fromtimestamp(path.stat().st_mtime).strftime("%H:%M:%S")
        if n >= 1:
            return True, f"top10 就绪 rows>={n} mtime={mt} path={path}"
        return False, f"top10 无数据行 mtime={mt} path={path}"
    except Exception as e:
        return False, f"读取 top10 失败: {e}"


def main() -> int:
    parser = argparse.ArgumentParser(description="等待今日盘后量能 top10 就绪")
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
        print(f"[跳过] {today} 不是交易日，无需等待 after_hours_rank。")
        return 0

    path = _top10_path(today)
    print(
        f"[等待] after_hours_rank 就绪：存在 {path.name} 且有数据 "
        f"（间隔 {interval}s，超时 {timeout}s）"
    )
    print(f"[等待] 期望路径: {path}")

    t0 = time.time()
    attempt = 0
    while True:
        attempt += 1
        ok, detail = _top10_ready(today)
        elapsed = int(time.time() - t0)
        now = datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] #{attempt} 已等 {elapsed}s | {detail}")
        if ok:
            print("[就绪] 今日盘后量能 top10 已生成，可以导出 COS。")
            return 0
        if elapsed >= timeout:
            print(
                f"[超时] 等待超过 {timeout}s，今日 after_hours_rank 仍未就绪。"
                f" 请检查大 QMT 盘后量能是否在跑、tick 落盘是否完成。"
            )
            return 1
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
