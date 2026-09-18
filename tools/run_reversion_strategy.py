# -*- coding: utf-8 -*-
"""
回归策略（v1.1）CLI

示例：
  python tools/run_reversion_strategy.py --start 2023-01-01 --end 2026-09-01 --hold-weeks 1 --out history_data/reversion_strategy/hold1
  python tools/run_reversion_strategy.py --start 2023-01-01 --end 2026-09-01 --hold-weeks 2 --out history_data/reversion_strategy/hold2
  python tools/run_reversion_strategy.py --start 2023-01-01 --end 2026-09-01 --hold-weeks 3 --out history_data/reversion_strategy/hold3
  python tools/run_reversion_strategy.py --max-stocks 80 --start 2024-01-01 --hold-weeks 1  # 烟雾
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reversion_strategy.config import ReversionStrategyConfig
from reversion_strategy.pipeline import run_pipeline


def _parse_date(s: str | None):
    if not s:
        return None
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Reversion FM strategy (spec v1.1)")
    p.add_argument("--start", type=str, default=None, help="YYYY-MM-DD")
    p.add_argument("--end", type=str, default=None, help="YYYY-MM-DD")
    p.add_argument("--max-stocks", type=int, default=None)
    p.add_argument("--top-n", type=int, default=20)
    p.add_argument("--window", type=int, default=52, help="FM estimate window (decision periods)")
    p.add_argument(
        "--hold-weeks",
        type=int,
        default=1,
        choices=[1, 2, 3],
        help="holding / rebalance period in calendar weeks (FM left-hand matches)",
    )
    p.add_argument("--min-amount", type=float, default=1e8, help="20d avg amount (yuan)")
    p.add_argument("--stop-loss", type=float, default=-0.08)
    p.add_argument("--no-stop", action="store_true", help="disable stop-loss")
    p.add_argument(
        "--score-mode",
        type=str,
        default="fm",
        choices=["fm", "theory_sign", "icir_w"],
        help="portfolio score: fm premiums / theory equal-weight / |ICIR| weighted",
    )
    p.add_argument(
        "--skip-top-k",
        type=int,
        default=0,
        help="skip most extreme K names before taking top-n",
    )
    p.add_argument("--out", type=str, default=None, help="output dir")
    p.add_argument("--codes", type=str, default=None, help="comma-separated 6-digit codes")
    args = p.parse_args(argv)

    cfg = ReversionStrategyConfig(
        start_date=_parse_date(args.start),
        end_date=_parse_date(args.end),
        top_n=args.top_n,
        estimate_window=args.window,
        hold_weeks=args.hold_weeks,
        min_avg_amount=args.min_amount,
        stop_loss=args.stop_loss,
        enable_stop_loss=not args.no_stop,
        score_mode=args.score_mode,
        skip_top_k=args.skip_top_k,
    )
    out = (
        Path(args.out)
        if args.out
        else (
            ROOT
            / "history_data"
            / "reversion_strategy"
            / f"hold{args.hold_weeks}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
    )
    codes = [c.strip() for c in args.codes.split(",")] if args.codes else None

    result = run_pipeline(cfg, max_stocks=args.max_stocks, out_dir=out, codes=codes)
    print(result["ic"].to_string(index=False))
    if result.get("gates") is not None and not result["gates"].empty:
        print(result["gates"].to_string(index=False))
    if result["nav"] is not None and not result["nav"].empty:
        print(result["nav"].tail(3).to_string(index=False))
        tu = result["nav"]["turnover_used"]
        print(
            f"turnover_used mean={tu.mean():.3f} median={tu.median():.3f} "
            f"frac>0.3={(tu > 0.3).mean():.1%} hold_weeks={args.hold_weeks}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
