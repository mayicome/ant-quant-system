# -*- coding: utf-8 -*-
"""
趋势策略 CLI（v1.2 周频 / 半周压缩）

示例：
  python tools/run_trend_strategy.py --mode week --start 2023-01-01 --end 2026-09-01
  python tools/run_trend_strategy.py --mode half --start 2023-01-01 --end 2026-09-01 --out history_data/trend_strategy/half_run
  python tools/run_trend_strategy.py --mode half --max-stocks 80 --start 2024-01-01  # 烟雾
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
    p = argparse.ArgumentParser(description="Trend FM strategy (week | half)")
    p.add_argument("--mode", type=str, default="week", choices=["week", "half"],
                   help="week=v1.2 ISO week; half=every 3 trade days + compressed factors")
    p.add_argument("--start", type=str, default=None, help="YYYY-MM-DD")
    p.add_argument("--end", type=str, default=None, help="YYYY-MM-DD")
    p.add_argument("--max-stocks", type=int, default=None)
    p.add_argument("--top-n", type=int, default=20)
    p.add_argument("--window", type=int, default=None,
                   help="FM estimate window in decision periods (default: 52 week / 40 half)")
    p.add_argument("--r2-threshold", type=float, default=None,
                   help="fixed R2 threshold; week default=median; half default=0.30")
    p.add_argument("--turnover", type=float, default=None, help="max turnover (default 0.30/0.45)")
    p.add_argument("--cost", type=float, default=None, help="round-trip cost (default 0.0015/0.002)")
    p.add_argument("--min-amount", type=float, default=1e8, help="20d avg amount (yuan)")
    p.add_argument("--out", type=str, default=None, help="output dir")
    p.add_argument("--codes", type=str, default=None, help="comma-separated 6-digit codes")
    args = p.parse_args(argv)

    if args.mode == "half":
        cfg = TrendStrategyConfig.half_week(
            start_date=_parse_date(args.start),
            end_date=_parse_date(args.end),
            top_n=args.top_n,
            min_avg_amount=args.min_amount,
        )
        if args.window is not None:
            cfg.estimate_window = args.window
        if args.r2_threshold is not None:
            cfg.r2_threshold = args.r2_threshold
        if args.turnover is not None:
            cfg.max_turnover = args.turnover
        if args.cost is not None:
            cfg.cost_roundtrip = args.cost
    else:
        cfg = TrendStrategyConfig(
            start_date=_parse_date(args.start),
            end_date=_parse_date(args.end),
            top_n=args.top_n,
            estimate_window=args.window if args.window is not None else 52,
            r2_threshold=args.r2_threshold,
            min_avg_amount=args.min_amount,
            max_turnover=args.turnover if args.turnover is not None else 0.30,
            cost_roundtrip=args.cost if args.cost is not None else 0.0015,
        )

    out = (
        Path(args.out)
        if args.out
        else (
            ROOT
            / "history_data"
            / "trend_strategy"
            / f"{args.mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
    )
    codes = [c.strip() for c in args.codes.split(",")] if args.codes else None

    result = run_pipeline(cfg, max_stocks=args.max_stocks, out_dir=out, codes=codes)
    print(result["ic"].to_string(index=False))
    if result.get("premium_summary") is not None and not result["premium_summary"].empty:
        print(result["premium_summary"].to_string(index=False))
    if result.get("nav_stats"):
        print("nav_stats", result["nav_stats"])
    if result["nav"] is not None and not result["nav"].empty:
        print(result["nav"].tail(3).to_string(index=False))
        if "turnover_used" in result["nav"].columns:
            tu = result["nav"]["turnover_used"]
            print(
                f"turnover_used mean={tu.mean():.3f} median={tu.median():.3f} "
                f"frac>cap={(tu > cfg.max_turnover + 1e-9).mean():.1%}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
