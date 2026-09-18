# -*- coding: utf-8 -*-
"""
hold=3 二轮研究（共享面板/因子，按 score_mode 缓存得分，只重跑回测）。

底座：五因子 + 关止损 + hold=3。
变体：theory_sign / icir_w / skip 极端 / N / 成本。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from reversion_strategy.config import FACTOR_NAMES, ReversionStrategyConfig
from reversion_strategy.data import (
    align_calendar_from_panels,
    build_panels,
    list_available_codes,
    load_universe_meta,
)
from reversion_strategy.engine import run_backtest
from reversion_strategy.factors import compute_factor_bundle
from reversion_strategy.filters import build_tradable_mask
from reversion_strategy.fm import open_to_open_week_returns, premium_summary, run_fama_macbeth
from reversion_strategy.validate import (
    evaluate_gates,
    factor_ic_table,
    nav_stats,
    prediction_deciles,
)
from reversion_strategy.weeks import week_end_dates

# tag, score_mode, skip_top_k, top_n, cost_roundtrip
VARIANTS = [
    ("r2_fm_n20", "fm", 0, 20, 0.0015),
    ("r2_theory_n20", "theory_sign", 0, 20, 0.0015),
    ("r2_icirw_n20", "icir_w", 0, 20, 0.0015),
    ("r2_fm_skip5", "fm", 5, 20, 0.0015),
    ("r2_fm_skip10", "fm", 10, 20, 0.0015),
    ("r2_theory_skip5", "theory_sign", 5, 20, 0.0015),
    ("r2_fm_n30", "fm", 0, 30, 0.0015),
    ("r2_fm_n15", "fm", 0, 15, 0.0015),
    ("r2_fm_cost10", "fm", 0, 20, 0.0010),
    ("r2_theory_n30", "theory_sign", 0, 30, 0.0015),
]


def _bt_weeks(score: pd.DataFrame, decision_weeks, hist_dates, cfg: ReversionStrategyConfig):
    bt = [
        d
        for d in decision_weeks
        if d in score.index and score.loc[d].notna().sum() >= cfg.top_n + int(cfg.skip_top_k or 0)
    ]
    if cfg.start_date:
        bt = [d for d in bt if d >= cfg.start_date]
    min_hist = max(cfg.premium_lag + 4, min(12, cfg.estimate_window // 4))
    if len(hist_dates) >= min_hist:
        first_ok = hist_dates[min(len(hist_dates) - 1, min_hist - 1)]
        if len(hist_dates) >= cfg.estimate_window + cfg.premium_lag:
            first_ok = hist_dates[cfg.estimate_window + cfg.premium_lag - 1]
        bt = [d for d in bt if d > first_ok]
    return bt


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2023-01-01")
    p.add_argument("--end", default="2026-09-01")
    p.add_argument("--max-stocks", type=int, default=None)
    p.add_argument("--out-root", default="history_data/reversion_strategy/hold3_round2")
    args = p.parse_args(argv)

    start = datetime.strptime(args.start[:10], "%Y-%m-%d").date()
    end = datetime.strptime(args.end[:10], "%Y-%m-%d").date()
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    meta = load_universe_meta(exclude_bj=True)
    name_map = dict(zip(meta["code"].astype(str), meta["name"].astype(str)))
    avail = set(list_available_codes("hfq")) & set(list_available_codes("none"))
    uni = [c for c in meta["code"].astype(str).tolist() if c in avail]
    if args.max_stocks:
        uni = uni[: args.max_stocks]

    base = ReversionStrategyConfig(
        start_date=start,
        end_date=end,
        hold_weeks=3,
        factor_names=tuple(FACTOR_NAMES),
        enable_stop_loss=False,
    )
    load_start = start - timedelta(days=400 + 7 * base.estimate_window * 3)
    print(f"[round2] loading panels uni={len(uni)} ...", flush=True)
    panels = build_panels(uni, start=load_start, end=end)
    all_days = align_calendar_from_panels(panels)
    print(f"[round2] trade_days={len(all_days)}", flush=True)

    close_hfq = panels["close_hfq"]
    high_hfq = panels["high_hfq"] if not panels["high_hfq"].empty else close_hfq
    low_hfq = panels["low_hfq"] if not panels["low_hfq"].empty else close_hfq

    print("[round2] tradable mask ...", flush=True)
    mask = build_tradable_mask(
        close_hfq,
        panels["amount"],
        panels["close_raw"],
        panels["volume_raw"],
        meta,
        trade_days=all_days,
        cfg=base,
        name_map=name_map,
    )

    week_ends = week_end_dates(all_days)
    warm = start - timedelta(days=400 + 7 * base.estimate_window * 3)
    week_ends = [d for d in week_ends if warm <= d <= end]
    decision_weeks = week_ends[::3]
    print(f"[round2] decision_weeks={len(decision_weeks)}", flush=True)

    print("[round2] factors once ...", flush=True)
    bundle = compute_factor_bundle(
        close_hfq, high_hfq, low_hfq, panels["amount"], mask, base, week_ends=decision_weeks
    )
    z, pass_mask = bundle["z"], bundle["pass_mask"]
    week_ret = open_to_open_week_returns(
        panels["open_hfq"], decision_weeks, all_days, hold_weeks=1
    )
    ic_tbl = factor_ic_table(z, week_ret, pass_mask)

    score_cache: dict = {}
    prem_cache: dict = {}
    hist_cache: dict = {}

    rows = []
    for tag, mode, skip_k, top_n, cost in VARIANTS:
        cfg = ReversionStrategyConfig(
            start_date=start,
            end_date=end,
            hold_weeks=3,
            factor_names=tuple(FACTOR_NAMES),
            enable_stop_loss=False,
            score_mode=mode,
            skip_top_k=skip_k,
            top_n=top_n,
            cost_roundtrip=cost,
        )
        print(
            f"\n===== {tag} mode={mode} skip={skip_k} N={top_n} cost={cost} =====",
            flush=True,
        )
        if mode not in score_cache:
            print(f"[round2] FM score_mode={mode} ...", flush=True)
            cfg_fm = ReversionStrategyConfig(
                start_date=start,
                end_date=end,
                hold_weeks=3,
                factor_names=tuple(FACTOR_NAMES),
                enable_stop_loss=False,
                score_mode=mode,
            )
            fm = run_fama_macbeth(z, pass_mask, week_ret, decision_weeks, cfg_fm)
            score_cache[mode] = fm["score"].dropna(how="all")
            prem_cache[mode] = fm["premium"]
            hist_cache[mode] = fm["hist_dates"]

        score = score_cache[mode]
        premium = prem_cache[mode]
        hist_dates = hist_cache[mode]
        prem_sum = premium_summary(premium) if not premium.empty else pd.DataFrame()
        deciles = prediction_deciles(score, week_ret, pass_mask)
        bt_weeks = _bt_weeks(score, decision_weeks, hist_dates, cfg)
        print(f"[round2] backtest periods={len(bt_weeks)}", flush=True)
        bt = run_backtest(
            score,
            bt_weeks,
            all_days,
            panels["open_raw"],
            panels["close_raw"],
            panels["open_hfq"],
            close_hfq,
            name_map,
            cfg,
        )
        gates = evaluate_gates(
            ic_tbl, prem_sum, deciles, bt["nav"], bt["trades"], hold_weeks=3
        )
        st = nav_stats(bt["nav"], hold_weeks=3)
        to_med = (
            float(bt["nav"]["turnover_used"].median())
            if bt["nav"] is not None and not bt["nav"].empty
            else float("nan")
        )

        out = out_root / tag
        out.mkdir(parents=True, exist_ok=True)
        bt["nav"].to_csv(out / "nav.csv", index=False, encoding="utf-8-sig")
        bt["trades"].to_csv(out / "trades.csv", index=False, encoding="utf-8-sig")
        gates.to_csv(out / "gates.csv", index=False, encoding="utf-8-sig")
        if not prem_sum.empty:
            prem_sum.to_csv(out / "premium_summary.csv", index=False, encoding="utf-8-sig")
        deciles.to_csv(out / "deciles.csv", index=False, encoding="utf-8-sig")
        ic_tbl.to_csv(out / "ic_summary.csv", index=False, encoding="utf-8-sig")
        with open(out / "config.json", "w", encoding="utf-8") as f:
            json.dump(
                {k: (str(v) if isinstance(v, date) else v) for k, v in asdict(cfg).items()},
                f,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        (out / "summary.txt").write_text(
            f"{tag}\n{st}\ngates:\n{gates.to_string(index=False)}\n", encoding="utf-8"
        )

        rows.append(
            {
                "tag": tag,
                "mode": mode,
                "skip": skip_k,
                "top_n": top_n,
                "cost": cost,
                "ann": st.get("ann_return"),
                "total": st.get("total_return"),
                "sharpe": st.get("sharpe"),
                "max_dd": st.get("max_dd"),
                "to_med": to_med,
                "n_periods": st.get("n_periods"),
            }
        )
        print(f"stats {st}", flush=True)

    sdf = pd.DataFrame(rows).sort_values("ann", ascending=False)
    sdf.to_csv(out_root / "round2_summary.csv", index=False, encoding="utf-8-sig")
    print("\n======== ROUND2 SUMMARY ========", flush=True)
    print(sdf.to_string(index=False), flush=True)
    print(f"wrote {out_root / 'round2_summary.csv'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
