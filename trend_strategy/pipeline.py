# -*- coding: utf-8 -*-
"""端到端流水线：面板 → 过滤 → 因子 → FM → 验证 → 回测。"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

import pandas as pd

from trend_strategy.config import TrendStrategyConfig
from trend_strategy.data import (
    align_calendar_from_panels,
    build_panels,
    list_available_codes,
    load_universe_meta,
)
from trend_strategy.engine import run_backtest
from trend_strategy.filters import build_tradable_mask
from trend_strategy.factors import compute_factor_bundle
from trend_strategy.fm import premium_summary, run_fama_macbeth, week_returns_hfq
from trend_strategy.validate import factor_corr_and_vif, factor_ic_table, prediction_deciles
from trend_strategy.weeks import week_end_dates


def run_pipeline(
    cfg: TrendStrategyConfig,
    *,
    max_stocks: Optional[int] = None,
    out_dir: Optional[Path] = None,
    codes: Optional[List[str]] = None,
) -> dict:
    meta = load_universe_meta(exclude_bj=cfg.exclude_bj)
    name_map = dict(zip(meta["code"].astype(str), meta["name"].astype(str)))

    avail = set(list_available_codes("hfq")) & set(list_available_codes("none"))
    uni = [c for c in meta["code"].astype(str).tolist() if c in avail]
    if codes:
        want = {str(c).zfill(6) for c in codes}
        uni = [c for c in uni if c in want]
    if max_stocks and len(uni) > max_stocks:
        uni = uni[:max_stocks]

    print(f"[pipeline] universe={len(uni)} stocks", flush=True)

    # 为斜率窗口预留历史
    load_start = cfg.start_date
    if load_start is not None:
        load_start = load_start - timedelta(days=400)

    panels = build_panels(uni, start=load_start, end=cfg.end_date)
    close_hfq = panels["close_hfq"]
    if close_hfq.empty:
        raise RuntimeError("后复权面板为空，请检查 daily_cache_hfq / daily_full_hfq")

    all_days = align_calendar_from_panels(panels)
    if not all_days:
        raise RuntimeError("交易日日历为空")
    print(f"[pipeline] trade_days={len(all_days)} range={all_days[0]}..{all_days[-1]}", flush=True)

    # 小样本时放宽 R² 过滤后的最小池
    if len(uni) < cfg.r2_min_pool:
        cfg.r2_min_pool = max(20, len(uni) // 3)

    print("[pipeline] building tradable mask ...", flush=True)
    mask = build_tradable_mask(
        close_hfq,
        panels["amount"],
        panels["close_raw"],
        panels["volume_raw"],
        meta,
        trade_days=all_days,
        cfg=cfg,
    )

    week_ends_all = week_end_dates(all_days)
    week_ends = list(week_ends_all)
    if cfg.start_date:
        # 因子/溢价需要 start 之前的周，便于滚动窗口；回测再截断
        warm = cfg.start_date - timedelta(days=400)
        week_ends = [d for d in week_ends if d >= warm]
    if cfg.end_date:
        week_ends = [d for d in week_ends if d <= cfg.end_date]
    print(f"[pipeline] week_ends={len(week_ends)}", flush=True)

    print("[pipeline] computing factors (SLOPE60 may take a while) ...", flush=True)
    bundle = compute_factor_bundle(close_hfq, panels["amount"], mask, cfg, week_ends=week_ends)
    z = bundle["z"]
    pass_mask = bundle["pass_mask"]

    week_ret = week_returns_hfq(close_hfq, week_ends)
    print("[pipeline] Fama-MacBeth ...", flush=True)
    fm = run_fama_macbeth(z, pass_mask, week_ret, week_ends, cfg)

    print("[pipeline] validation ...", flush=True)
    ic_tbl = factor_ic_table(z, week_ret, pass_mask)
    deciles = prediction_deciles(fm["score"], week_ret, pass_mask)
    prem_sum = premium_summary(fm["premium"]) if not fm["premium"].empty else pd.DataFrame()
    diag = factor_corr_and_vif(z, pass_mask, sample_dates=week_ends)

    # 回测：有足够滚动窗口后的得分
    score = fm["score"].dropna(how="all")
    bt_weeks = [d for d in week_ends if d in score.index and score.loc[d].notna().sum() >= cfg.top_n]
    if cfg.start_date:
        bt_weeks = [d for d in bt_weeks if d >= cfg.start_date]
    if len(fm["hist_dates"]) >= min(12, cfg.estimate_window // 4):
        first_ok = fm["hist_dates"][min(len(fm["hist_dates"]) - 1, max(0, min(12, cfg.estimate_window // 4) - 1))]
        if len(fm["hist_dates"]) >= cfg.estimate_window:
            first_ok = fm["hist_dates"][cfg.estimate_window - 1]
        bt_weeks = [d for d in bt_weeks if d > first_ok]

    print(f"[pipeline] backtest weeks={len(bt_weeks)}", flush=True)
    bt = run_backtest(
        score,
        bt_weeks,
        all_days,
        panels["open_raw"],
        panels["close_raw"],
        panels["high_raw"],
        panels["low_raw"],
        name_map,
        cfg,
    )

    result = {
        "config": {k: (str(v) if isinstance(v, date) else v) for k, v in asdict(cfg).items()},
        "n_stocks": len(uni),
        "ic": ic_tbl,
        "deciles": deciles,
        "premium_summary": prem_sum,
        "fm_r2": fm["fm_r2"],
        "corr": diag["corr"],
        "vif": diag["vif"],
        "nav": bt["nav"],
        "trades": bt["trades"],
        "premium": fm["premium"],
        "r2_threshold_used": bundle["r2_threshold_used"],
    }

    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        ic_tbl.to_csv(out_dir / "ic_summary.csv", index=False, encoding="utf-8-sig")
        deciles.to_csv(out_dir / "deciles.csv", index=False, encoding="utf-8-sig")
        if not prem_sum.empty:
            prem_sum.to_csv(out_dir / "premium_summary.csv", index=False, encoding="utf-8-sig")
        if not fm["premium"].empty:
            fm["premium"].to_csv(out_dir / "premium_timeseries.csv", encoding="utf-8-sig")
        if not fm["fm_r2"].empty:
            fm["fm_r2"].to_csv(out_dir / "fm_r2.csv", index=False, encoding="utf-8-sig")
        if not diag["corr"].empty:
            diag["corr"].to_csv(out_dir / "factor_corr.csv", encoding="utf-8-sig")
        if not diag["vif"].empty:
            diag["vif"].to_csv(out_dir / "factor_vif.csv", encoding="utf-8-sig", header=["vif"])
        bt["nav"].to_csv(out_dir / "nav.csv", index=False, encoding="utf-8-sig")
        bt["trades"].to_csv(out_dir / "trades.csv", index=False, encoding="utf-8-sig")
        with open(out_dir / "config.json", "w", encoding="utf-8") as f:
            json.dump(result["config"], f, ensure_ascii=False, indent=2, default=str)
        # 简报
        lines = [
            f"stocks={len(uni)} weeks={len(week_ends)} bt_weeks={len(bt_weeks)}",
            "=== IC ===",
            ic_tbl.to_string(index=False),
            "=== Premium ===",
            prem_sum.to_string(index=False) if not prem_sum.empty else "(empty)",
            "=== Deciles ===",
            deciles.to_string(index=False) if not deciles.empty else "(empty)",
        ]
        if not bt["nav"].empty:
            nav0 = float(bt["nav"]["nav"].iloc[0])
            nav1 = float(bt["nav"]["nav"].iloc[-1])
            lines.append(f"=== NAV {nav0:.2f} -> {nav1:.2f} ({nav1 / nav0 - 1:.2%}) ===")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        print(f"[pipeline] wrote {out_dir}", flush=True)

    return result
