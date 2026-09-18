# -*- coding: utf-8 -*-
"""
技术反转双路径实验：
  Path1 — ICIR 固定权重打分（§10.1，锁死符号，告别 A≡B）
  Path2 — FM 底座 r1_A_r2=0.25_n20 + hold3 组合约束

  python tools/run_tech_rev_paths.py
  python tools/run_tech_rev_paths.py --max-stocks 200
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from reversion_strategy.filters import apply_liquidity_pct_and_vol_filters
from reversion_strategy.industry import load_sw1_map
from tech_rev_strategy.config import FACTOR_NAMES, NEGATE_DEFAULT, TechRevConfig
from tech_rev_strategy.factors import compute_raw_factors, standardize_on_dates
from tech_rev_strategy.fm import fm_r2_mean, premium_summary, run_fama_macbeth, week_returns_hfq
from tech_rev_strategy.pipeline import _trade_fail_rates
from tech_rev_strategy.score import build_icir_scores
from trend_strategy.data import (
    align_calendar_from_panels,
    build_panels,
    list_available_codes,
    load_universe_meta,
)
from trend_strategy.engine import run_backtest
from trend_strategy.filters import build_tradable_mask
from trend_strategy.validate import factor_ic_table, nav_stats, prediction_deciles
from trend_strategy.weeks import week_end_dates

# Path2：FM 底座 + 约束
R2_VARIANTS: List[Tuple[str, dict]] = [
    ("r2_FM_BASE", {}),
    ("r2_CAP4", {"industry_cap_k": 4}),
    ("r2_CAP3", {"industry_cap_k": 3}),
    ("r2_CAP5", {"industry_cap_k": 5}),
    ("r2_LQ05", {"min_avg_amount": 0.5e8}),
    ("r2_ATR10", {"vol_filter": "atr", "vol_drop_q": 0.10}),
    ("r2_CAP4_LQ05", {"industry_cap_k": 4, "min_avg_amount": 0.5e8}),
    ("r2_CAP4_ATR10", {"industry_cap_k": 4, "vol_filter": "atr", "vol_drop_q": 0.10}),
    ("r2_CAP4_LQ05_ATR10", {"industry_cap_k": 4, "min_avg_amount": 0.5e8, "vol_filter": "atr", "vol_drop_q": 0.10}),
]

PATH1: List[Tuple[str, str]] = [
    ("p1_ICIR", "icir_w"),
    ("p1_EQUAL", "equal_sign"),
]


def _bt_weeks(score, week_ends, hist_dates, start, top_n, estimate_window):
    bt = [d for d in week_ends if d in score.index and score.loc[d].notna().sum() >= top_n]
    bt = [d for d in bt if d >= start]
    if len(hist_dates) >= estimate_window:
        first_ok = hist_dates[estimate_window - 1]
        bt = [d for d in bt if d > first_ok]
    elif len(hist_dates) >= max(4, estimate_window // 4):
        first_ok = hist_dates[max(0, min(len(hist_dates) - 1, max(4, estimate_window // 4) - 1))]
        bt = [d for d in bt if d > first_ok]
    return bt


def _d10_d1(deciles: pd.DataFrame) -> float:
    if deciles is None or deciles.empty:
        return float("nan")
    g = deciles.set_index("group")["mean_ret"]
    if 10 in g.index and 1 in g.index:
        return float(g.loc[10] - g.loc[1])
    return float("nan")


def _run_one(
    *,
    tag: str,
    cfg: TechRevConfig,
    score: pd.DataFrame,
    hist_dates,
    week_ends,
    all_days,
    panels,
    name_map,
    ind_map,
    start: date,
    out_root: Path,
    ic_tbl: Optional[pd.DataFrame] = None,
    prem_sum: Optional[pd.DataFrame] = None,
    avg_fm_r2: float = float("nan"),
    week_ret: Optional[pd.DataFrame] = None,
    pass_mask: Optional[pd.DataFrame] = None,
) -> dict:
    bt_weeks = _bt_weeks(score, week_ends, hist_dates, start, cfg.top_n, cfg.estimate_window)
    print(f"[{tag}] backtest weeks={len(bt_weeks)}", flush=True)
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
        industry_map=ind_map,
    )
    st = nav_stats(bt["nav"], periods_per_year=52.0)
    fails = _trade_fail_rates(bt["trades"])
    to_med = (
        float(bt["nav"]["turnover_used"].median())
        if not bt["nav"].empty and "turnover_used" in bt["nav"].columns
        else float("nan")
    )
    deciles = (
        prediction_deciles(score, week_ret, pass_mask)
        if week_ret is not None and pass_mask is not None
        else pd.DataFrame()
    )
    d10 = _d10_d1(deciles)
    calmar = (
        float(st["ann_return"] / abs(st["max_dd"]))
        if st.get("max_dd") and abs(st["max_dd"]) > 1e-12
        else float("nan")
    )

    out = out_root / tag
    out.mkdir(parents=True, exist_ok=True)
    bt["nav"].to_csv(out / "nav.csv", index=False, encoding="utf-8-sig")
    bt["trades"].to_csv(out / "trades.csv", index=False, encoding="utf-8-sig")
    if ic_tbl is not None and not ic_tbl.empty:
        ic_tbl.to_csv(out / "ic_summary.csv", index=False, encoding="utf-8-sig")
    if prem_sum is not None and not prem_sum.empty:
        prem_sum.to_csv(out / "premium_summary.csv", index=False, encoding="utf-8-sig")
    if not deciles.empty:
        deciles.to_csv(out / "deciles.csv", index=False, encoding="utf-8-sig")
    with open(out / "config.json", "w", encoding="utf-8") as f:
        json.dump(
            {k: (str(v) if isinstance(v, date) else v) for k, v in asdict(cfg).items()},
            f,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    (out / "summary.txt").write_text(f"{tag}\n{st}\nd10_d1={d10}\n", encoding="utf-8")

    row = {
        "tag": tag,
        "path": tag.split("_")[0] if tag.startswith(("p1", "r2")) else "?",
        "score_mode": cfg.score_mode,
        "industry_cap_k": cfg.industry_cap_k,
        "min_avg_amount": cfg.min_avg_amount,
        "vol_filter": cfg.vol_filter,
        "vol_drop_q": cfg.vol_drop_q,
        "ann": st.get("ann_return"),
        "total": st.get("total_return"),
        "ann_vol": st.get("ann_vol"),
        "sharpe": st.get("sharpe"),
        "max_dd": st.get("max_dd"),
        "calmar": calmar,
        "to_med": to_med,
        "to_ann": to_med * 52 if np.isfinite(to_med) else np.nan,
        "fm_r2_mean": avg_fm_r2,
        "d10_d1": d10,
        "n_periods": st.get("n_periods"),
        **fails,
    }
    print(
        f"[{tag}] ann={st.get('ann_return')} dd={st.get('max_dd')} sharpe={st.get('sharpe')}",
        flush=True,
    )
    return row


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2023-01-01")
    p.add_argument("--end", default="2026-09-01")
    p.add_argument("--max-stocks", type=int, default=None)
    p.add_argument("--out-root", default="history_data/reversion_strategy/tech_rev_fm")
    p.add_argument("--paths", default="1,2", help="comma: 1=ICIR, 2=FM constraints")
    args = p.parse_args(argv)

    start = datetime.strptime(args.start[:10], "%Y-%m-%d").date()
    end = datetime.strptime(args.end[:10], "%Y-%m-%d").date()
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    want = {x.strip() for x in args.paths.split(",") if x.strip()}

    meta = load_universe_meta(exclude_bj=True)
    name_map = dict(zip(meta["code"].astype(str), meta["name"].astype(str)))
    avail = set(list_available_codes("hfq")) & set(list_available_codes("none"))
    uni = [c for c in meta["code"].astype(str).tolist() if c in avail]
    if args.max_stocks:
        uni = uni[: args.max_stocks]

    base = TechRevConfig.for_group(
        "A", start_date=start, end_date=end, top_n=20, r2_threshold=0.25, r2_min_pool=800
    )
    if args.max_stocks:
        base.r2_min_pool = max(20, len(uni) // 3)

    load_start = start - timedelta(days=400 + 7 * 52)
    print(f"[paths] loading panels uni={len(uni)} ...", flush=True)
    panels = build_panels(uni, start=load_start, end=end)
    all_days = align_calendar_from_panels(panels)
    close_hfq = panels["close_hfq"]
    high_px = panels["high_raw"] if not panels.get("high_raw", pd.DataFrame()).empty else close_hfq
    low_px = panels["low_raw"] if not panels.get("low_raw", pd.DataFrame()).empty else close_hfq
    print(f"[paths] trade_days={len(all_days)}", flush=True)

    ind_map = load_sw1_map(list(close_hfq.columns))
    warm = start - timedelta(days=400 + 7 * 52)
    week_ends = [d for d in week_end_dates(all_days) if warm <= d <= end]

    print("[paths] base mask + raw factors ...", flush=True)
    mask_core = build_tradable_mask(
        close_hfq,
        panels["amount"],
        panels["close_raw"],
        panels["volume_raw"],
        meta,
        trade_days=all_days,
        cfg=base,  # type: ignore[arg-type]
    )
    raw = compute_raw_factors(close_hfq, panels["amount"], slope_window=30)
    week_ret = week_returns_hfq(close_hfq, week_ends)

    mask_cache: Dict[tuple, pd.DataFrame] = {}
    z_cache: Dict[tuple, tuple] = {}

    def get_mask(cfg: TechRevConfig) -> pd.DataFrame:
        key = (
            float(cfg.min_avg_amount),
            float(cfg.liquidity_pct or 0),
            str(cfg.vol_filter or "none"),
            float(cfg.vol_drop_q or 0),
        )
        if key in mask_cache:
            return mask_cache[key]
        if abs(cfg.min_avg_amount - base.min_avg_amount) > 1:
            cfg_m = replace(base, min_avg_amount=cfg.min_avg_amount)
            m0 = build_tradable_mask(
                close_hfq,
                panels["amount"],
                panels["close_raw"],
                panels["volume_raw"],
                meta,
                trade_days=all_days,
                cfg=cfg_m,  # type: ignore[arg-type]
            )
        else:
            m0 = mask_core
        m1 = apply_liquidity_pct_and_vol_filters(
            m0, close_hfq, panels["amount"], cfg, high_hfq=high_px, low_hfq=low_px
        )
        mask_cache[key] = m1
        return m1

    def get_z(cfg: TechRevConfig):
        mk = (
            float(cfg.min_avg_amount),
            float(cfg.liquidity_pct or 0),
            str(cfg.vol_filter or "none"),
            float(cfg.vol_drop_q or 0),
            float(cfg.r2_threshold or -1),
            tuple(cfg.negate_factors),
            int(cfg.r2_min_pool),
        )
        if mk in z_cache:
            return z_cache[mk]
        mask = get_mask(cfg)
        z, pm, thr = standardize_on_dates(
            raw,
            mask,
            week_ends,
            r2_threshold=cfg.r2_threshold,
            r2_min_pool=cfg.r2_min_pool,
            winsor_q=cfg.winsor_q,
            factor_names=FACTOR_NAMES,
            negate_factors=cfg.negate_factors,
        )
        z_cache[mk] = (z, pm, thr)
        return z_cache[mk]

    path1_rows: List[dict] = []
    path2_rows: List[dict] = []

    # —— Path 1: ICIR / equal ——
    if "1" in want:
        print("\n======== PATH1 ICIR / EQUAL ========", flush=True)
        cfg1 = replace(base, score_mode="icir_w", industry_cap_k=0)
        z, pm, _ = get_z(cfg1)
        ic_tbl = factor_ic_table(z, week_ret, pm)
        for tag, mode in PATH1:
            cfg = replace(cfg1, score_mode=mode)
            print(f"\n===== {tag} mode={mode} =====", flush=True)
            sc = build_icir_scores(z, pm, week_ret, week_ends, cfg)
            row = _run_one(
                tag=tag,
                cfg=cfg,
                score=sc["score"],
                hist_dates=sc["hist_dates"],
                week_ends=week_ends,
                all_days=all_days,
                panels=panels,
                name_map=name_map,
                ind_map=ind_map,
                start=start,
                out_root=out_root,
                ic_tbl=ic_tbl,
                week_ret=week_ret,
                pass_mask=pm,
            )
            row["path"] = "p1"
            path1_rows.append(row)

        sdf1 = pd.DataFrame(path1_rows).sort_values("sharpe", ascending=False)
        sdf1.to_csv(out_root / "path1_icir_summary.csv", index=False, encoding="utf-8-sig")
        print("\nPATH1 SUMMARY", flush=True)
        print(sdf1[["tag", "ann", "max_dd", "sharpe", "d10_d1", "to_med"]].to_string(index=False), flush=True)

    # —— Path 2: FM + constraints ——
    if "2" in want:
        print("\n======== PATH2 FM + CONSTRAINTS ========", flush=True)
        fm_cache: Dict[tuple, dict] = {}

        def get_fm(cfg: TechRevConfig):
            z, pm, _ = get_z(cfg)
            key = (
                float(cfg.min_avg_amount),
                str(cfg.vol_filter),
                float(cfg.vol_drop_q or 0),
                float(cfg.r2_threshold or -1),
            )
            if key in fm_cache:
                return fm_cache[key], z, pm
            print(f"[r2] FM key={key} ...", flush=True)
            fm = run_fama_macbeth(z, pm, week_ret, week_ends, cfg)
            pack = {
                "fm": fm,
                "ic": factor_ic_table(z, week_ret, pm),
                "prem": premium_summary(fm["premium"]) if not fm["premium"].empty else pd.DataFrame(),
                "avg_r2": fm_r2_mean(fm["fm_r2"]),
            }
            fm_cache[key] = pack
            return pack, z, pm

        for tag, overs in R2_VARIANTS:
            cfg = replace(base, score_mode="fm", **overs)
            print(f"\n===== {tag} {overs} =====", flush=True)
            pack, z, pm = get_fm(cfg)
            fm = pack["fm"]
            row = _run_one(
                tag=tag,
                cfg=cfg,
                score=fm["score"],
                hist_dates=fm["hist_dates"],
                week_ends=week_ends,
                all_days=all_days,
                panels=panels,
                name_map=name_map,
                ind_map=ind_map,
                start=start,
                out_root=out_root,
                ic_tbl=pack["ic"],
                prem_sum=pack["prem"],
                avg_fm_r2=pack["avg_r2"],
                week_ret=week_ret,
                pass_mask=pm,
            )
            row["path"] = "r2"
            path2_rows.append(row)

        sdf2 = pd.DataFrame(path2_rows).sort_values("sharpe", ascending=False)
        sdf2.to_csv(out_root / "r2_summary.csv", index=False, encoding="utf-8-sig")
        print("\nPATH2 / R2 SUMMARY", flush=True)
        cols = ["tag", "ann", "max_dd", "sharpe", "d10_d1", "to_med", "industry_cap_k", "min_avg_amount", "vol_filter"]
        print(sdf2[[c for c in cols if c in sdf2.columns]].to_string(index=False), flush=True)

    # —— 三方对照 ——
    baselines = pd.DataFrame(
        [
            {"tag": "hold3_X_CAP4_LQ05_ATR10", "ann": 0.122065, "max_dd": -0.262173, "sharpe": 0.363346},
            {"tag": "tech_rev_fm_r1_A_r2.25_n20", "ann": 0.108329, "max_dd": -0.488979, "sharpe": 0.255965},
            {"tag": "trend_half_run1", "ann": -0.074755, "max_dd": -0.483808, "sharpe": -0.205512},
        ]
    )
    parts = [baselines]
    if path1_rows:
        parts.append(pd.DataFrame(path1_rows)[["tag", "ann", "max_dd", "sharpe"]])
    if path2_rows:
        parts.append(pd.DataFrame(path2_rows)[["tag", "ann", "max_dd", "sharpe"]])
    cmp = pd.concat(parts, ignore_index=True).sort_values("sharpe", ascending=False)
    cmp.to_csv(out_root / "paths_compare.csv", index=False, encoding="utf-8-sig")
    print("\n======== COMPARE ========", flush=True)
    print(cmp.to_string(index=False), flush=True)
    print(f"wrote under {out_root}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
