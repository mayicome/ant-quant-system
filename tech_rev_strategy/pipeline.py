# -*- coding: utf-8 -*-
"""端到端：面板 → 过滤 → 技术反转因子（取负）→ FM → 验证 → 回测。"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

from tech_rev_strategy.config import TechRevConfig
from tech_rev_strategy.factors import compute_factor_bundle
from tech_rev_strategy.fm import fm_r2_mean, premium_summary, run_fama_macbeth, week_returns_hfq
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


def _trade_fail_rates(trades: pd.DataFrame) -> dict:
    if trades is None or trades.empty or "side" not in trades.columns:
        return {
            "buy_defer_rate": np.nan,
            "sell_defer_rate": np.nan,
            "sell_fail_raw": np.nan,
        }
    n_buy = int((trades["side"] == "buy").sum())
    n_buy_d = int(trades["side"].isin(["buy_defer", "buy_abandon"]).sum())
    n_sell = int((trades["side"] == "sell").sum())
    n_sell_d = int((trades["side"] == "sell_defer").sum())
    return {
        "buy_defer_rate": n_buy_d / max(n_buy + n_buy_d, 1),
        "sell_defer_rate": n_sell_d / max(n_sell + n_sell_d, 1),
        "sell_fail_raw": n_sell_d / max(n_sell + n_sell_d, 1),
    }


def run_pipeline(
    cfg: TechRevConfig,
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

    print(
        f"[tech_rev] uni={len(uni)} exp={cfg.exp_group} negate={cfg.negate_factors} "
        f"r2={cfg.r2_threshold} N={cfg.top_n}",
        flush=True,
    )

    load_start = cfg.start_date
    if load_start is not None:
        # 与趋势周频一致：固定预热，避免 Wilder/斜率窗口起点漂移
        load_start = load_start - timedelta(days=400 + 7 * 52)

    if panels is None:
        panels = build_panels(uni, start=load_start, end=cfg.end_date)
    close_hfq = panels["close_hfq"]
    if close_hfq.empty:
        raise RuntimeError("后复权面板为空")

    if all_days is None:
        all_days = align_calendar_from_panels(panels)
    if not all_days:
        raise RuntimeError("交易日日历为空")

    if len(uni) < cfg.r2_min_pool:
        cfg.r2_min_pool = max(20, len(uni) // 3)

    mask = build_tradable_mask(
        close_hfq,
        panels["amount"],
        panels["close_raw"],
        panels["volume_raw"],
        meta,
        trade_days=all_days,
        cfg=cfg,  # type: ignore[arg-type]
    )

    week_ends = week_end_dates(all_days)
    if cfg.start_date:
        warm = cfg.start_date - timedelta(days=400 + 7 * 52)
        week_ends = [d for d in week_ends if d >= warm]
    if cfg.end_date:
        week_ends = [d for d in week_ends if d <= cfg.end_date]
    print(f"[tech_rev] week_ends={len(week_ends)}", flush=True)

    print("[tech_rev] factors ...", flush=True)
    bundle = compute_factor_bundle(close_hfq, panels["amount"], mask, cfg, week_ends=week_ends)
    z, pass_mask = bundle["z"], bundle["pass_mask"]

    week_ret = week_returns_hfq(close_hfq, week_ends)
    print("[tech_rev] Fama-MacBeth ...", flush=True)
    fm = run_fama_macbeth(z, pass_mask, week_ret, week_ends, cfg)

    ic_tbl = factor_ic_table(z, week_ret, pass_mask)
    deciles = prediction_deciles(fm["score"], week_ret, pass_mask)
    prem_sum = premium_summary(fm["premium"]) if not fm["premium"].empty else pd.DataFrame()
    diag = factor_corr_and_vif(z, pass_mask, sample_dates=week_ends)
    avg_fm_r2 = fm_r2_mean(fm["fm_r2"])

    score = fm["score"].dropna(how="all")
    bt_weeks = [d for d in week_ends if d in score.index and score.loc[d].notna().sum() >= cfg.top_n]
    if cfg.start_date:
        bt_weeks = [d for d in bt_weeks if d >= cfg.start_date]
    if len(fm["hist_dates"]) >= min(12, cfg.estimate_window // 4):
        first_ok = fm["hist_dates"][
            min(len(fm["hist_dates"]) - 1, max(0, min(12, cfg.estimate_window // 4) - 1))
        ]
        if len(fm["hist_dates"]) >= cfg.estimate_window:
            first_ok = fm["hist_dates"][cfg.estimate_window - 1]
        bt_weeks = [d for d in bt_weeks if d > first_ok]

    print(f"[tech_rev] backtest weeks={len(bt_weeks)}", flush=True)
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
    stats = nav_stats(bt["nav"], periods_per_year=cfg.periods_per_year)
    fails = _trade_fail_rates(bt["trades"])
    to_med = (
        float(bt["nav"]["turnover_used"].median())
        if bt["nav"] is not None and not bt["nav"].empty and "turnover_used" in bt["nav"].columns
        else float("nan")
    )
    to_mean = (
        float(bt["nav"]["turnover_used"].mean())
        if bt["nav"] is not None and not bt["nav"].empty and "turnover_used" in bt["nav"].columns
        else float("nan")
    )
    # 年化换手≈ median * 52
    to_ann = to_med * 52.0 if np.isfinite(to_med) else float("nan")

    # 分位价差
    d10_d1 = float("nan")
    if deciles is not None and not deciles.empty and "group" in deciles.columns:
        g = deciles.set_index("group")["mean_ret"]
        if 10 in g.index and 1 in g.index:
            d10_d1 = float(g.loc[10] - g.loc[1])

    result = {
        "config": {k: (str(v) if isinstance(v, date) else v) for k, v in asdict(cfg).items()},
        "n_stocks": len(uni),
        "ic": ic_tbl,
        "deciles": deciles,
        "premium_summary": prem_sum,
        "fm_r2": fm["fm_r2"],
        "fm_r2_mean": avg_fm_r2,
        "corr": diag["corr"],
        "vif": diag["vif"],
        "nav": bt["nav"],
        "trades": bt["trades"],
        "premium": fm["premium"],
        "r2_threshold_used": bundle["r2_threshold_used"],
        "nav_stats": stats,
        "to_med": to_med,
        "to_mean": to_mean,
        "to_ann": to_ann,
        "d10_d1": d10_d1,
        **fails,
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
        pd.DataFrame([{**stats, **fails, "to_med": to_med, "to_ann": to_ann, "fm_r2_mean": avg_fm_r2, "d10_d1": d10_d1}]).to_csv(
            out_dir / "nav_stats.csv", index=False, encoding="utf-8-sig"
        )
        with open(out_dir / "config.json", "w", encoding="utf-8") as f:
            json.dump(result["config"], f, ensure_ascii=False, indent=2, default=str)
        lines = [
            f"exp={cfg.exp_group} r2={cfg.r2_threshold} N={cfg.top_n} weeks={len(week_ends)} bt={len(bt_weeks)}",
            f"negate={cfg.negate_factors}",
            "=== IC (post-negate exposures) ===",
            ic_tbl.to_string(index=False),
            "=== Premium ===",
            prem_sum.to_string(index=False) if not prem_sum.empty else "(empty)",
            "=== Deciles ===",
            deciles.to_string(index=False) if not deciles.empty else "(empty)",
            f"=== NAV === {stats}",
            f"fm_r2_mean={avg_fm_r2} d10_d1={d10_d1} to_med={to_med} to_ann={to_ann} fails={fails}",
        ]
        if not bt["nav"].empty:
            nav0 = float(bt["nav"]["nav"].iloc[0])
            nav1 = float(bt["nav"]["nav"].iloc[-1])
            lines.append(f"NAV {nav0:.2f} -> {nav1:.2f} ({nav1 / nav0 - 1:.2%})")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        print(f"[tech_rev] wrote {out_dir}", flush=True)

    return result
