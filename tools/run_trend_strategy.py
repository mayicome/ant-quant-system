# -*- coding: utf-8 -*-
"""
趋势策略（v1.2）CLI

示例：
  python tools/run_trend_strategy.py --max-stocks 300 --start 2023-01-01 --end 2026-09-01
  python tools/run_trend_strategy.py --max-stocks 80 --start 2024-01-01  # 烟雾测试
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trend_strategy.config import TrendStrategyConfig
from trend_strategy.pipeline import run_pipeline


def _parse_date(s: str | None):
    if not s:
        return None
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Trend FM weekly strategy (spec v1.2)")
    p.add_argument("--start", type=str, default=None, help="YYYY-MM-DD")
    p.add_argument("--end", type=str, default=None, help="YYYY-MM-DD")
    p.add_argument("--max-stocks", type=int, default=None)
    p.add_argument("--top-n", type=int, default=20)
    p.add_argument("--window", type=int, default=52, help="FM estimate window (weeks)")
    p.add_argument("--r2-threshold", type=float, default=None, help="fixed R2 threshold; default=median")
    p.add_argument("--min-amount", type=float, default=1e8, help="20d avg amount (yuan)")
    p.add_argument("--out", type=str, default=None, help="output dir")
    p.add_argument("--codes", type=str, default=None, help="comma-separated 6-digit codes")
    args = p.parse_args(argv)

    cfg = TrendStrategyConfig(
        start_date=_parse_date(args.start),
        end_date=_parse_date(args.end),
        top_n=args.top_n,
        estimate_window=args.window,
        r2_threshold=args.r2_threshold,
        min_avg_amount=args.min_amount,
    )
    out = Path(args.out) if args.out else (ROOT / "history_data" / "trend_strategy" / datetime.now().strftime("%Y%m%d_%H%M%S"))
    codes = [c.strip() for c in args.codes.split(",")] if args.codes else None

    result = run_pipeline(cfg, max_stocks=args.max_stocks, out_dir=out, codes=codes)
    print(result["ic"].to_string(index=False))
    if result["nav"] is not None and not result["nav"].empty:
        print(result["nav"].tail(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
