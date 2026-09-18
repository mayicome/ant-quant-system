# -*- coding: utf-8 -*-
"""
技术反转 FM v1.0 CLI

  python tools/run_tech_rev_fm.py --exp A --r2 0.30 --n 20 --start 2023-01-01 --end 2026-09-01
  python tools/run_tech_rev_fm.py --exp B --r2-median --max-stocks 100  # 烟雾
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_rev_strategy.config import TechRevConfig
from tech_rev_strategy.pipeline import run_pipeline


def _parse_date(s: str | None):
    if not s:
        return None
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Tech reversal FM strategy v1.0")
    p.add_argument("--exp", type=str, default="A", choices=["A", "B", "a", "b"])
    p.add_argument("--r2", type=float, default=0.30, help="fixed R2 threshold")
    p.add_argument("--r2-median", action="store_true", help="use cross-section median R2")
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--start", type=str, default="2023-01-01")
    p.add_argument("--end", type=str, default="2026-09-01")
    p.add_argument("--max-stocks", type=int, default=None)
    p.add_argument("--out", type=str, default=None)
    p.add_argument("--codes", type=str, default=None)
    args = p.parse_args(argv)

    exp = args.exp.upper()
    cfg = TechRevConfig.for_group(
        exp,
        start_date=_parse_date(args.start),
        end_date=_parse_date(args.end),
        top_n=args.n,
        r2_threshold=None if args.r2_median else args.r2,
    )
    out = (
        Path(args.out)
        if args.out
        else ROOT
        / "history_data"
        / "reversion_strategy"
        / "tech_rev_fm"
        / f"{exp}_r2{args.r2}_n{args.n}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    codes = [c.strip() for c in args.codes.split(",")] if args.codes else None
    result = run_pipeline(cfg, max_stocks=args.max_stocks, out_dir=out, codes=codes)
    print(result["ic"].to_string(index=False))
    if result.get("premium_summary") is not None and not result["premium_summary"].empty:
        print(result["premium_summary"].to_string(index=False))
    print("nav_stats", result.get("nav_stats"))
    print(
        f"fm_r2_mean={result.get('fm_r2_mean')} d10_d1={result.get('d10_d1')} "
        f"to_med={result.get('to_med')} sell_fail={result.get('sell_fail_raw')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
