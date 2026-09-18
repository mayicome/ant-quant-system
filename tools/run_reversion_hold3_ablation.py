# -*- coding: utf-8 -*-
"""
hold=3 消融：共享面板，扫因子集 × 止损开关。

示例：
  python tools/run_reversion_hold3_ablation.py
  python tools/run_reversion_hold3_ablation.py --max-stocks 300  # 烟雾
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reversion_strategy.config import FACTOR_NAMES, ReversionStrategyConfig
from reversion_strategy.data import (
    align_calendar_from_panels,
    build_panels,
    list_available_codes,
    load_universe_meta,
)
from reversion_strategy.pipeline import run_pipeline


VARIANTS = [
    ("h3_base", list(FACTOR_NAMES), True),
    ("h3_drop_rsi14", ["REV5", "BIAS20", "VSHRK", "VDRAIN"], True),
    ("h3_drop_pos_prem", ["REV5", "BIAS20", "VSHRK"], True),  # 去掉溢价为正的 RSI14/VDRAIN
    ("h3_rev5_vshrk", ["REV5", "VSHRK"], True),
    ("h3_nostop", list(FACTOR_NAMES), False),
    ("h3_drop_rsi14_nostop", ["REV5", "BIAS20", "VSHRK", "VDRAIN"], False),
    ("h3_rev5_vshrk_nostop", ["REV5", "VSHRK"], False),
]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Reversion hold=3 factor/stop ablation")
    p.add_argument("--start", default="2023-01-01")
    p.add_argument("--end", default="2026-09-01")
    p.add_argument("--max-stocks", type=int, default=None)
    p.add_argument("--out-root", default="history_data/reversion_strategy/hold3_ablation")
    args = p.parse_args(argv)

    start = datetime.strptime(args.start[:10], "%Y-%m-%d").date()
    end = datetime.strptime(args.end[:10], "%Y-%m-%d").date()
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    meta = load_universe_meta(exclude_bj=True)
    name_map = dict(zip(meta["code"].astype(str), meta["name"].astype(str)))
    avail = set(list_available_codes("hfq")) & set(list_available_codes("none"))
    uni = [c for c in meta["code"].astype(str).tolist() if c in avail]
    if args.max_stocks and len(uni) > args.max_stocks:
        uni = uni[: args.max_stocks]

    # 面板按最大 hold=3 预热一次
    base_cfg = ReversionStrategyConfig(start_date=start, end_date=end, hold_weeks=3)
    from datetime import timedelta

    load_start = start - timedelta(days=400 + 7 * base_cfg.estimate_window * 3)
    print(f"[ablation] loading panels uni={len(uni)} ...", flush=True)
    panels = build_panels(uni, start=load_start, end=end)
    all_days = align_calendar_from_panels(panels)
    print(f"[ablation] trade_days={len(all_days)}", flush=True)

    summary_rows = []
    for tag, factors, use_stop in VARIANTS:
        cfg = ReversionStrategyConfig(
            start_date=start,
            end_date=end,
            hold_weeks=3,
            factor_names=tuple(factors),
            enable_stop_loss=use_stop,
            stop_loss=-0.08,
        )
        out = out_root / tag
        print(f"\n===== {tag} factors={factors} stop={use_stop} =====", flush=True)
        result = run_pipeline(
            cfg,
            out_dir=out,
            panels=panels,
            meta=meta,
            uni=uni,
            all_days=all_days,
            name_map=name_map,
        )
        st = result.get("nav_stats") or {}
        nav = result["nav"]
        to_med = float(nav["turnover_used"].median()) if nav is not None and not nav.empty else float("nan")
        summary_rows.append(
            {
                "tag": tag,
                "factors": ",".join(factors),
                "stop": use_stop,
                "ann": st.get("ann_return"),
                "total": st.get("total_return"),
                "sharpe": st.get("sharpe"),
                "max_dd": st.get("max_dd"),
                "to_med": to_med,
                "n_periods": st.get("n_periods"),
            }
        )
        prem = result.get("premium_summary")
        if prem is not None and not prem.empty:
            print(prem.to_string(index=False), flush=True)
        print(f"NAV stats: {st}", flush=True)

    import pandas as pd

    sdf = pd.DataFrame(summary_rows)
    sdf.to_csv(out_root / "ablation_summary.csv", index=False, encoding="utf-8-sig")
    print("\n======== ABLATION SUMMARY ========", flush=True)
    print(sdf.to_string(index=False), flush=True)
    print(f"wrote {out_root / 'ablation_summary.csv'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
