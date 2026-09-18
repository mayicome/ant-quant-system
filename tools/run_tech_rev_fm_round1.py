# -*- coding: utf-8 -*-
"""
技术反转 FM r1 跑批：实验组 A/B × R²{0.25,0.30,0.35,0.40} × N{18,20,22} = 24 组。
共享面板/原始因子，按 (r2, negate) 缓存标准化与 FM。

  python tools/run_tech_rev_fm_round1.py
  python tools/run_tech_rev_fm_round1.py --max-stocks 200  # 烟雾
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from tech_rev_strategy.config import FACTOR_NAMES, NEGATE_ALL, NEGATE_DEFAULT, TechRevConfig
from tech_rev_strategy.factors import compute_raw_factors, standardize_on_dates
from tech_rev_strategy.fm import fm_r2_mean, premium_summary, run_fama_macbeth, week_returns_hfq
from tech_rev_strategy.pipeline import _trade_fail_rates
from trend_strategy.data import (
    align_calendar_from_panels,
    build_panels,
    list_available_codes,
    load_universe_meta,
)
from trend_strategy.engine import run_backtest
from trend_strategy.filters import build_tradable_mask
from trend_strategy.validate import factor_corr_and_vif, factor_ic_table, nav_stats, prediction_deciles
from trend_strategy.weeks import week_end_dates

R2_GRID = [0.25, 0.30, 0.35, 0.40]
N_GRID = [18, 20, 22]
EXPS = [("A", NEGATE_DEFAULT), ("B", NEGATE_ALL)]


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2023-01-01")
    p.add_argument("--end", default="2026-09-01")
    p.add_argument("--max-stocks", type=int, default=None)
    p.add_argument("--out-root", default="history_data/reversion_strategy/tech_rev_fm")
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

    base = TechRevConfig.for_group("A", start_date=start, end_date=end)
    load_start = start - timedelta(days=400 + 7 * 52)
    print(f"[r1] loading panels uni={len(uni)} ...", flush=True)
    panels = build_panels(uni, start=load_start, end=end)
    all_days = align_calendar_from_panels(panels)
    close_hfq = panels["close_hfq"]
    print(f"[r1] trade_days={len(all_days)}", flush=True)

    mask = build_tradable_mask(
        close_hfq,
        panels["amount"],
        panels["close_raw"],
        panels["volume_raw"],
        meta,
        trade_days=all_days,
        cfg=base,  # type: ignore[arg-type]
    )
    warm = start - timedelta(days=400 + 7 * 52)
    week_ends = [d for d in week_end_dates(all_days) if warm <= d <= end]
    print(f"[r1] week_ends={len(week_ends)}", flush=True)

    print("[r1] raw factors once ...", flush=True)
    raw = compute_raw_factors(close_hfq, panels["amount"], slope_window=30)
    week_ret = week_returns_hfq(close_hfq, week_ends)

    # cache key: (r2, negate_tuple) -> z, pass, fm pack
    z_cache: Dict[tuple, tuple] = {}
    fm_cache: Dict[tuple, dict] = {}

    def get_z(r2_thr: float, negate: Tuple[str, ...], r2_min_pool: int):
        key = (r2_thr, negate, r2_min_pool)
        if key in z_cache:
            return z_cache[key]
        z, pm, thr = standardize_on_dates(
            raw,
            mask,
            week_ends,
            r2_threshold=r2_thr,
            r2_min_pool=r2_min_pool,
            winsor_q=(0.01, 0.99),
            factor_names=FACTOR_NAMES,
            negate_factors=negate,
        )
        z_cache[key] = (z, pm, thr)
        return z_cache[key]

    def get_fm(r2_thr: float, negate: Tuple[str, ...], cfg: TechRevConfig):
        key = (r2_thr, negate, cfg.r2_min_pool, cfg.estimate_window)
        if key in fm_cache:
            return fm_cache[key]
        z, pm, _ = get_z(r2_thr, negate, cfg.r2_min_pool)
        print(f"[r1] FM r2={r2_thr} neg={negate} ...", flush=True)
        fm = run_fama_macbeth(z, pm, week_ret, week_ends, cfg)
        pack = {
            "fm": fm,
            "z": z,
            "pass_mask": pm,
            "ic": factor_ic_table(z, week_ret, pm),
        }
        fm_cache[key] = pack
        return pack

    rows: List[dict] = []
    for exp, negate in EXPS:
        for r2 in R2_GRID:
            for n in N_GRID:
                tag = f"r1_{exp}_r2{r2:.2f}_n{n}"
                cfg = TechRevConfig.for_group(
                    exp,
                    start_date=start,
                    end_date=end,
                    top_n=n,
                    r2_threshold=r2,
                    r2_min_pool=800 if not args.max_stocks else max(20, len(uni) // 3),
                )
                print(f"\n===== {tag} =====", flush=True)
                pack = get_fm(r2, tuple(negate), cfg)
                fm = pack["fm"]
                score = fm["score"].dropna(how="all")
                prem_sum = premium_summary(fm["premium"]) if not fm["premium"].empty else pd.DataFrame()
                deciles = prediction_deciles(score, week_ret, pack["pass_mask"])
                avg_r2 = fm_r2_mean(fm["fm_r2"])

                bt_weeks = [
                    d for d in week_ends if d in score.index and score.loc[d].notna().sum() >= n
                ]
                bt_weeks = [d for d in bt_weeks if d >= start]
                if len(fm["hist_dates"]) >= cfg.estimate_window:
                    first_ok = fm["hist_dates"][cfg.estimate_window - 1]
                    bt_weeks = [d for d in bt_weeks if d > first_ok]

                bt = run_backtest(
                    score,
                    bt_weeks,
                    all_days,
                    panels["open_raw"],
                    panels["close_raw"],
                    panels["high_raw"],
                    panels["low_raw"],
                    name_map,
                    cfg,  # type: ignore[arg-type]
                )
                st = nav_stats(bt["nav"], periods_per_year=52.0)
                fails = _trade_fail_rates(bt["trades"])
                to_med = (
                    float(bt["nav"]["turnover_used"].median())
                    if not bt["nav"].empty and "turnover_used" in bt["nav"].columns
                    else float("nan")
                )
                to_mean = (
                    float(bt["nav"]["turnover_used"].mean())
                    if not bt["nav"].empty and "turnover_used" in bt["nav"].columns
                    else float("nan")
                )
                d10_d1 = float("nan")
                if not deciles.empty:
                    g = deciles.set_index("group")["mean_ret"]
                    if 10 in g.index and 1 in g.index:
                        d10_d1 = float(g.loc[10] - g.loc[1])

                # IC / premium summary fields
                ic_means = {}
                icirs = {}
                if pack["ic"] is not None and not pack["ic"].empty:
                    for _, r in pack["ic"].iterrows():
                        ic_means[f"ic_{r['factor']}"] = r.get("ic_mean")
                        icirs[f"icir_{r['factor']}"] = r.get("icir")
                prem_t = {}
                if not prem_sum.empty:
                    for _, r in prem_sum.iterrows():
                        prem_t[f"t_{r['factor']}"] = r.get("t_nw")
                        prem_t[f"prem_{r['factor']}"] = r.get("mean")

                out = out_root / tag
                out.mkdir(parents=True, exist_ok=True)
                bt["nav"].to_csv(out / "nav.csv", index=False, encoding="utf-8-sig")
                bt["trades"].to_csv(out / "trades.csv", index=False, encoding="utf-8-sig")
                pack["ic"].to_csv(out / "ic_summary.csv", index=False, encoding="utf-8-sig")
                if not prem_sum.empty:
                    prem_sum.to_csv(out / "premium_summary.csv", index=False, encoding="utf-8-sig")
                deciles.to_csv(out / "deciles.csv", index=False, encoding="utf-8-sig")
                with open(out / "config.json", "w", encoding="utf-8") as f:
                    json.dump(
                        {k: (str(v) if isinstance(v, date) else v) for k, v in asdict(cfg).items()},
                        f,
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    )
                (out / "summary.txt").write_text(
                    f"{tag}\n{st}\nfm_r2_mean={avg_r2} d10_d1={d10_d1} to_med={to_med}\n",
                    encoding="utf-8",
                )

                calmar = (
                    float(st["ann_return"] / abs(st["max_dd"]))
                    if st.get("max_dd") and abs(st["max_dd"]) > 1e-12
                    else float("nan")
                )
                row = {
                    "tag": tag,
                    "exp": exp,
                    "r2": r2,
                    "top_n": n,
                    "ann": st.get("ann_return"),
                    "total": st.get("total_return"),
                    "ann_vol": st.get("ann_vol"),
                    "sharpe": st.get("sharpe"),
                    "max_dd": st.get("max_dd"),
                    "calmar": calmar,
                    "to_med": to_med,
                    "to_mean": to_mean,
                    "to_ann": to_med * 52 if np.isfinite(to_med) else np.nan,
                    "fm_r2_mean": avg_r2,
                    "d10_d1": d10_d1,
                    "n_periods": st.get("n_periods"),
                    **fails,
                    **ic_means,
                    **icirs,
                    **prem_t,
                }
                rows.append(row)
                print(
                    f"stats ann={st.get('ann_return')} dd={st.get('max_dd')} "
                    f"sharpe={st.get('sharpe')} d10_d1={d10_d1}",
                    flush=True,
                )

    sdf = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    sdf.to_csv(out_root / "r1_summary.csv", index=False, encoding="utf-8-sig")
    print("\n======== R1 SUMMARY (top by sharpe) ========", flush=True)
    cols = [
        c
        for c in [
            "tag", "exp", "r2", "top_n", "ann", "max_dd", "sharpe", "calmar",
            "d10_d1", "fm_r2_mean", "to_med", "to_ann", "sell_fail_raw",
        ]
        if c in sdf.columns
    ]
    print(sdf[cols].to_string(index=False), flush=True)

    # 降级判定（规格 §10）
    best = sdf.iloc[0]
    ic_cols = [c for c in sdf.columns if c.startswith("ic_") and not c.startswith("icir_")]
    mean_ic = float(np.nanmean([best[c] for c in ic_cols])) if ic_cols else float("nan")
    kill_reasons = []
    if mean_ic < 0 or (np.isfinite(mean_ic) and abs(mean_ic) < 0.005):
        kill_reasons.append(f"mean_ic={mean_ic:.4f} not clearly positive")
    if not (np.isfinite(best["d10_d1"]) and best["d10_d1"] > 0):
        kill_reasons.append("d10_d1 not positive")
    if np.isfinite(best["fm_r2_mean"]) and best["fm_r2_mean"] < 0.01:
        kill_reasons.append(f"fm_r2_mean={best['fm_r2_mean']:.4f}<1%")
    if (best.get("ann") or 0) < 0 or (best.get("sharpe") or 0) < 0:
        kill_reasons.append("ann or sharpe < 0")

    verdict = "fail_framework" if kill_reasons else "signal_ok"
    note = (
        f"best={best['tag']} ann={best['ann']} dd={best['max_dd']} sharpe={best['sharpe']}\n"
        f"vs hold3 champ +12.2%/-26.2%/0.36; half_run1 -7.5%/-48%/-0.21\n"
        f"verdict={verdict}\n"
        + ("\n".join(kill_reasons) if kill_reasons else "IC/decile/R2/PnL checks passed at best tag\n")
    )
    (out_root / "r1_verdict.txt").write_text(note, encoding="utf-8")
    print("\n======== R1 VERDICT ========", flush=True)
    print(note, flush=True)
    print(f"wrote {out_root / 'r1_summary.csv'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
