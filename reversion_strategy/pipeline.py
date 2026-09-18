# -*- coding: utf-8 -*-
"""端到端：面板 → 过滤 → 因子 → FM(B, ≤w−2) → 验证 → 回测。"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

import pandas as pd

from reversion_strategy.config import ReversionStrategyConfig
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
    factor_corr_and_vif,
    factor_ic_table,
    ic_decay_table,
    nav_stats,
    prediction_deciles,
)
from reversion_strategy.weeks import week_end_dates


def run_pipeline(
    cfg: ReversionStrategyConfig,
    *,
    max_stocks: Optional[int] = None,
    out_dir: Optional[Path] = None,
    codes: Optional[List[str]] = None,
    panels: Optional[dict] = None,
    meta: Optional[pd.DataFrame] = None,
    uni: Optional[List[str]] = None,
    all_days: Optional[list] = None,
    name_map: Optional[dict] = None,
) -> dict:
    if meta is None:
        meta = load_universe_meta(exclude_bj=cfg.exclude_bj)
    if name_map is None:
        name_map = dict(zip(meta["code"].astype(str), meta["name"].astype(str)))

    if uni is None:
        avail = set(list_available_codes("hfq")) & set(list_available_codes("none"))
        uni = [c for c in meta["code"].astype(str).tolist() if c in avail]
        if codes:
            want = {str(c).zfill(6) for c in codes}
            uni = [c for c in uni if c in want]
        if max_stocks and len(uni) > max_stocks:
            uni = uni[:max_stocks]

    print(f"[reversion] universe={len(uni)} stocks factors={cfg.factor_names}", flush=True)

    hold = max(1, int(getattr(cfg, "hold_weeks", 1) or 1))
    if panels is None:
        load_start = cfg.start_date
        if load_start is not None:
            load_start = load_start - timedelta(days=400 + 7 * cfg.estimate_window * hold)
        panels = build_panels(uni, start=load_start, end=cfg.end_date)

    close_hfq = panels["close_hfq"]
    if close_hfq.empty:
        raise RuntimeError("后复权面板为空，请检查 daily_cache_hfq / daily_full_hfq")
    if panels["open_hfq"].empty:
        raise RuntimeError("后复权开盘价缺失，B 口径收益无法计算")

    if all_days is None:
        all_days = align_calendar_from_panels(panels)
    if not all_days:
        raise RuntimeError("交易日日历为空")
    print(f"[reversion] trade_days={len(all_days)} range={all_days[0]}..{all_days[-1]}", flush=True)

    print("[reversion] building tradable mask ...", flush=True)
    mask = build_tradable_mask(
        close_hfq,
        panels["amount"],
        panels["close_raw"],
        panels["volume_raw"],
        meta,
        trade_days=all_days,
        cfg=cfg,
        name_map=name_map,
    )

    week_ends_all = week_end_dates(all_days)
    week_ends = list(week_ends_all)
    hold = max(1, int(getattr(cfg, "hold_weeks", 1) or 1))
    warm_days = 400 + 7 * cfg.estimate_window * hold
    if cfg.start_date:
        warm = cfg.start_date - timedelta(days=warm_days)
        week_ends = [d for d in week_ends if d >= warm]
    if cfg.end_date:
        week_ends = [d for d in week_ends if d <= cfg.end_date]
    # 决策网格：每 hold 个自然周调仓一次；相邻决策点的开盘→开盘收益 = hold 周持有
    decision_weeks = week_ends[::hold]
    print(
        f"[reversion] week_ends={len(week_ends)} decision_weeks={len(decision_weeks)} hold_weeks={hold}",
        flush=True,
    )

    print("[reversion] computing factors (RSI/ATR may take a while) ...", flush=True)
    high_hfq = panels["high_hfq"] if not panels["high_hfq"].empty else close_hfq
    low_hfq = panels["low_hfq"] if not panels["low_hfq"].empty else close_hfq
    bundle = compute_factor_bundle(
        close_hfq,
        high_hfq,
        low_hfq,
        panels["amount"],
        mask,
        cfg,
        week_ends=decision_weeks,
    )
    z = bundle["z"]
    pass_mask = bundle["pass_mask"]

    # decision_weeks 已按 hold 抽样，hold_weeks=1 即跨 hold 个自然周
    week_ret = open_to_open_week_returns(
        panels["open_hfq"], decision_weeks, all_days, hold_weeks=1
    )
    print(
        f"[reversion] Fama-MacBeth (B open→open, {hold}w hold, premium lag ≤ d-{cfg.premium_lag}) ...",
        flush=True,
    )
    fm = run_fama_macbeth(z, pass_mask, week_ret, decision_weeks, cfg)

    print("[reversion] validation ...", flush=True)
    ic_tbl = factor_ic_table(z, week_ret, pass_mask)
    decay = ic_decay_table(z, panels["open_hfq"], week_ends, all_days, pass_mask)
    deciles = prediction_deciles(fm["score"], week_ret, pass_mask)
    prem_sum = premium_summary(fm["premium"]) if not fm["premium"].empty else pd.DataFrame()
    diag = factor_corr_and_vif(z, pass_mask, sample_dates=decision_weeks)

    score = fm["score"].dropna(how="all")
    bt_weeks = [
        d for d in decision_weeks if d in score.index and score.loc[d].notna().sum() >= cfg.top_n
    ]
    if cfg.start_date:
        bt_weeks = [d for d in bt_weeks if d >= cfg.start_date]
    min_hist = max(cfg.premium_lag + 4, min(12, cfg.estimate_window // 4))
    if len(fm["hist_dates"]) >= min_hist:
        first_ok = fm["hist_dates"][min(len(fm["hist_dates"]) - 1, min_hist - 1)]
        if len(fm["hist_dates"]) >= cfg.estimate_window + cfg.premium_lag:
            first_ok = fm["hist_dates"][cfg.estimate_window + cfg.premium_lag - 1]
        bt_weeks = [d for d in bt_weeks if d > first_ok]

    print(f"[reversion] backtest decision periods={len(bt_weeks)}", flush=True)
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
        ic_tbl, prem_sum, deciles, bt["nav"], bt["trades"], hold_weeks=hold
    )
    stats = nav_stats(bt["nav"], hold_weeks=hold)

    result = {
        "config": {k: (str(v) if isinstance(v, date) else v) for k, v in asdict(cfg).items()},
        "n_stocks": len(uni),
        "ic": ic_tbl,
        "ic_decay": decay,
        "deciles": deciles,
        "premium_summary": prem_sum,
        "fm_r2": fm["fm_r2"],
        "corr": diag["corr"],
        "vif": diag["vif"],
        "nav": bt["nav"],
        "trades": bt["trades"],
        "premium": fm["premium"],
        "gates": gates,
        "nav_stats": stats,
    }

    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        ic_tbl.to_csv(out_dir / "ic_summary.csv", index=False, encoding="utf-8-sig")
        if not decay.empty:
            decay.to_csv(out_dir / "ic_decay.csv", index=False, encoding="utf-8-sig")
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
        gates.to_csv(out_dir / "gates.csv", index=False, encoding="utf-8-sig")
        with open(out_dir / "config.json", "w", encoding="utf-8") as f:
            json.dump(result["config"], f, ensure_ascii=False, indent=2, default=str)
        lines = [
            f"stocks={len(uni)} calendar_weeks={len(week_ends)} decision_periods={len(bt_weeks)} hold_weeks={hold}",
            f"FM left-hand: open→open (B); hold={hold}w; premium lag ≤ d-{cfg.premium_lag}",
            "=== IC ===",
            ic_tbl.to_string(index=False),
            "=== Premium ===",
            prem_sum.to_string(index=False) if not prem_sum.empty else "(empty)",
            "=== Deciles ===",
            deciles.to_string(index=False) if not deciles.empty else "(empty)",
            "=== Gates ===",
            gates.to_string(index=False) if not gates.empty else "(empty)",
            f"=== NAV stats === {stats}",
        ]
        if not bt["nav"].empty:
            nav0 = float(bt["nav"]["nav"].iloc[0])
            nav1 = float(bt["nav"]["nav"].iloc[-1])
            lines.append(f"NAV {nav0:.2f} -> {nav1:.2f} ({nav1 / nav0 - 1:.2%})")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        print(f"[reversion] wrote {out_dir}", flush=True)

    return result
