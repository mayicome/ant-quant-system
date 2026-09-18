# -*- coding: utf-8 -*-
"""
hold=3 第四轮：底座锁定 R3 冠军 X_CAP4_LQ05_ATR10，做最后一轮挖潜。
若相对该底座无明显提升且仍远低于硬门槛 → 建议废止 v1 回归策略。
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

from reversion_strategy.config import FACTOR_NAMES, ReversionStrategyConfig
from reversion_strategy.data import (
    align_calendar_from_panels,
    build_panels,
    list_available_codes,
    load_universe_meta,
)
from reversion_strategy.engine import run_backtest
from reversion_strategy.factors import compute_raw_factors, standardize_on_dates
from reversion_strategy.filters import apply_liquidity_pct_and_vol_filters, build_tradable_mask
from reversion_strategy.fm import open_to_open_week_returns, premium_summary, run_fama_macbeth
from reversion_strategy.industry import load_sw1_map, portfolio_industry_stats
from reversion_strategy.regime import build_allow_new_buys, load_csi_close, load_regime_daily, regime_coverage_stats
from reversion_strategy.validate import evaluate_gates, factor_ic_table, nav_stats, prediction_deciles
from reversion_strategy.weeks import week_end_dates

# R4 底座 = R3 冠军
CHAMP = dict(industry_cap_k=4, min_avg_amount=0.5e8, vol_filter="atr", vol_drop_q=0.10)

R4: List[Tuple[str, str, dict]] = [
    ("R4_BASE", "base", {}),
    # 换手
    ("R4_TO40", "to", {"max_turnover": 0.40}),
    ("R4_TO50", "to", {"max_turnover": 0.50}),
    # N / 行业帽
    ("R4_N15", "n", {"top_n": 15}),
    ("R4_N25", "n", {"top_n": 25}),
    ("R4_N30", "n", {"top_n": 30}),
    ("R4_CAP5", "cap", {"industry_cap_k": 5}),
    ("R4_CAP6", "cap", {"industry_cap_k": 6}),
    ("R4_N30_CAP5", "n", {"top_n": 30, "industry_cap_k": 5}),
    # ATR / 流动性微调
    ("R4_ATR05", "vol", {"vol_drop_q": 0.05}),
    ("R4_ATR15", "vol", {"vol_drop_q": 0.15}),
    ("R4_LQ03", "liq", {"min_avg_amount": 0.3e8}),
    ("R4_LQ08", "liq", {"min_avg_amount": 0.8e8}),
    # 大盘：禁买 vs 半仓
    ("R4_MA60_BLOCK", "reg", {"regime_mode": "csi_ma", "regime_ma_window": 60}),
    ("R4_MA60_HALF", "reg", {"exposure_mode": "csi_ma_half", "regime_ma_window": 60}),
    ("R4_MA120_HALF", "reg", {"exposure_mode": "csi_ma_half", "regime_ma_window": 120}),
    # 止损 / 打分 / 因子
    ("R4_STOP8", "stop", {"enable_stop_loss": True, "stop_loss": -0.08}),
    ("R4_STOP6", "stop", {"enable_stop_loss": True, "stop_loss": -0.06}),
    ("R4_SKIP3", "score", {"skip_top_k": 3}),
    ("R4_DROP_RSI", "fac", {"factor_names": tuple(f for f in FACTOR_NAMES if f != "RSI14")}),
    ("R4_W78", "fm", {"estimate_window": 78}),
    # hold=4（决策网格变）
    ("R4_HOLD4", "hold", {"hold_weeks": 4}),
]


def _bt_weeks(score, decision_weeks, hist_dates, cfg: ReversionStrategyConfig):
    need = cfg.top_n + int(cfg.skip_top_k or 0)
    bt = [d for d in decision_weeks if d in score.index and score.loc[d].notna().sum() >= need]
    if cfg.start_date:
        bt = [d for d in bt if d >= cfg.start_date]
    min_hist = max(cfg.premium_lag + 4, min(12, cfg.estimate_window // 4))
    if len(hist_dates) >= min_hist:
        first_ok = hist_dates[min(len(hist_dates) - 1, min_hist - 1)]
        if len(hist_dates) >= cfg.estimate_window + cfg.premium_lag:
            first_ok = hist_dates[cfg.estimate_window + cfg.premium_lag - 1]
        bt = [d for d in bt if d > first_ok]
    return bt


def _sell_fail_hard(trades: pd.DataFrame) -> float:
    if trades is None or trades.empty:
        return float("nan")
    n_sell = int((trades["side"] == "sell").sum())
    n_def = int((trades["side"] == "sell_defer").sum())
    n_hard = int(
        ((trades["side"] == "sell_defer") & (~trades["reason"].fillna("").eq("turnover_partial"))).sum()
    ) if "reason" in trades.columns else n_def
    return n_hard / max(n_sell + n_def, 1)


def _ind_metrics(trades: pd.DataFrame, ind_map: Dict[str, str]) -> dict:
    buys = trades[trades["side"] == "buy"] if trades is not None and not trades.empty else pd.DataFrame()
    if buys.empty:
        return {"ind_n_mean": np.nan, "ind_hhi_mean": np.nan}
    buys = buys.copy()
    buys["date"] = pd.to_datetime(buys["date"]).dt.date
    rows = []
    for _, g in buys.groupby("date"):
        codes = list(g["code"].astype(str).str.zfill(6).unique())
        rows.append(portfolio_industry_stats(codes, ind_map))
    df = pd.DataFrame(rows)
    return {"ind_n_mean": float(df["n_ind"].mean()), "ind_hhi_mean": float(df["hhi"].mean())}


def _split_ann(nav: pd.DataFrame, hold_weeks: int) -> dict:
    if nav is None or nav.empty or "exec_date" not in nav.columns:
        return {"ann_2324": np.nan, "ann_2526": np.nan}
    n = nav.copy()
    n["exec_date"] = pd.to_datetime(n["exec_date"])
    out = {}
    for key, lo, hi in (("ann_2324", "2023-01-01", "2024-12-31"), ("ann_2526", "2025-01-01", "2026-12-31")):
        sub = n[(n["exec_date"] >= lo) & (n["exec_date"] <= hi)]
        st = nav_stats(sub, hold_weeks=hold_weeks) if len(sub) >= 4 else {}
        out[key] = st.get("ann_return", float("nan"))
    return out


def _build_half_scale(decision_dates, csi_close: pd.Series, ma_window: int, half: float = 0.5) -> Dict:
    ma = csi_close.rolling(int(ma_window), min_periods=max(5, int(ma_window) // 2)).mean()
    out = {}
    for d in decision_dates:
        if d not in csi_close.index or d not in ma.index:
            out[d] = 1.0
            continue
        px, m = float(csi_close.loc[d]), float(ma.loc[d])
        if not (px == px and m == m):
            out[d] = 1.0
        else:
            out[d] = float(half) if px < m else 1.0
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2023-01-01")
    p.add_argument("--end", default="2026-09-01")
    p.add_argument("--max-stocks", type=int, default=None)
    p.add_argument("--out-root", default="history_data/reversion_strategy/hold3_round4")
    p.add_argument("--tags", default=None)
    args = p.parse_args(argv)

    start = datetime.strptime(args.start[:10], "%Y-%m-%d").date()
    end = datetime.strptime(args.end[:10], "%Y-%m-%d").date()
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    want = {t.strip() for t in args.tags.split(",")} if args.tags else None
    variants = [v for v in R4 if want is None or v[0] in want]

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
        score_mode="fm",
        top_n=20,
        **CHAMP,
    )
    # 必须与 R3 相同预热起点：Wilder RSI/ATR 依赖序列起点，拉长面板会改变全样本因子
    load_start = start - timedelta(days=400 + 7 * 52 * 3)
    print(f"[r4] loading panels uni={len(uni)} load_start={load_start} ...", flush=True)
    panels = build_panels(uni, start=load_start, end=end)
    all_days = align_calendar_from_panels(panels)
    close_hfq = panels["close_hfq"]
    high_hfq = panels["high_hfq"] if not panels["high_hfq"].empty else close_hfq
    low_hfq = panels["low_hfq"] if not panels["low_hfq"].empty else close_hfq
    print(f"[r4] trade_days={len(all_days)}", flush=True)

    ind_map = load_sw1_map(list(close_hfq.columns))
    print("[r4] raw factors ...", flush=True)
    raw = compute_raw_factors(
        close_hfq, high_hfq, low_hfq, panels["amount"],
        rsi_period=base.rsi_period, atr_short=base.atr_short, atr_long=base.atr_long,
    )
    csi_close = load_csi_close()
    try:
        regime_df = load_regime_daily()
    except Exception:
        regime_df = pd.DataFrame()

    # 与 R3 对齐：hold=3 决策网格相位固定（warm 用 W=52×hold=3），避免 [::H] 相位漂移
    week_ends_full = week_end_dates(all_days)
    warm3 = start - timedelta(days=400 + 7 * 52 * 3)
    week_ends_h3 = [d for d in week_ends_full if warm3 <= d <= end]
    warm4 = start - timedelta(days=400 + 7 * 52 * 4)
    week_ends_h4 = [d for d in week_ends_full if warm4 <= d <= end]

    mask_cache, z_cache, fm_cache = {}, {}, {}
    week_ret_cache = {}

    def decision_grid(hold: int):
        h = max(1, int(hold))
        if h == 4:
            return week_ends_h4[::4]
        return week_ends_h3[:: max(1, h)]

    def get_week_ret(hold: int):
        if hold not in week_ret_cache:
            dw = decision_grid(hold)
            week_ret_cache[hold] = (
                dw,
                open_to_open_week_returns(panels["open_hfq"], dw, all_days, hold_weeks=1),
            )
        return week_ret_cache[hold]

    def mask_key(cfg):
        return (float(cfg.min_avg_amount), float(cfg.liquidity_pct or 0),
                str(cfg.vol_filter or "none"), float(cfg.vol_drop_q or 0))

    def get_mask(cfg):
        mk = mask_key(cfg)
        if mk in mask_cache:
            return mask_cache[mk]
        cfg_m = replace(base, min_avg_amount=cfg.min_avg_amount, hold_weeks=cfg.hold_weeks)
        m0 = build_tradable_mask(
            close_hfq, panels["amount"], panels["close_raw"], panels["volume_raw"],
            meta, trade_days=all_days, cfg=cfg_m, name_map=name_map,
        )
        m1 = apply_liquidity_pct_and_vol_filters(
            m0, close_hfq, panels["amount"], cfg, high_hfq=high_hfq, low_hfq=low_hfq,
        )
        mask_cache[mk] = m1
        return m1

    def get_fm(cfg):
        hold = max(1, int(cfg.hold_weeks or 3))
        dw, week_ret = get_week_ret(hold)
        ind_z = "z" if str(cfg.industry_mode).lower() == "z" else "none"
        fnames = tuple(cfg.factor_names)
        fk = mask_key(cfg) + (ind_z, str(cfg.industry_mode), fnames, hold, int(cfg.estimate_window))
        if fk in fm_cache:
            return fm_cache[fk], dw, week_ret
        print(f"[r4] FM {fk[:4]}... factors={fnames} hold={hold}", flush=True)
        mask = get_mask(cfg)
        z, pass_mask = standardize_on_dates(
            raw, mask, dw, winsor_q=cfg.winsor_q, factor_names=fnames,
            industry_mode=ind_z, industry_map=ind_map,
        )
        fm = run_fama_macbeth(z, pass_mask, week_ret, dw, cfg, industry_map=ind_map)
        pack = {"fm": fm, "z": z, "pass_mask": pass_mask, "ic": factor_ic_table(z, week_ret, pass_mask)}
        fm_cache[fk] = pack
        return pack, dw, week_ret

    rows = []
    for tag, axis, overs in variants:
        # exposure_mode 不是 config 字段
        overs = dict(overs)
        exposure_mode = overs.pop("exposure_mode", None)
        cfg = replace(base, **overs)
        print(f"\n===== {tag} {overs} exp={exposure_mode} =====", flush=True)
        pack, dw, week_ret = get_fm(cfg)
        fm = pack["fm"]
        score = fm["score"].dropna(how="all")
        prem_sum = premium_summary(fm["premium"]) if not fm["premium"].empty else pd.DataFrame()
        deciles = prediction_deciles(score, week_ret, pack["pass_mask"])
        bt_weeks = _bt_weeks(score, dw, fm["hist_dates"], cfg)

        allow = build_allow_new_buys(
            dw, mode=str(cfg.regime_mode or "off"), ma_window=int(cfg.regime_ma_window or 60),
            regime_df=regime_df if not regime_df.empty else None, csi_close=csi_close,
        )
        exp_scale = None
        if exposure_mode == "csi_ma_half":
            exp_scale = _build_half_scale(dw, csi_close, int(cfg.regime_ma_window or 60), half=0.5)
            allow = {d: True for d in dw}  # 半仓模式仍允许调仓买入

        cov = regime_coverage_stats(
            bt_weeks, allow, regime_df if not regime_df.empty else None, mode=str(cfg.regime_mode)
        )
        if exposure_mode == "csi_ma_half":
            blocked = sum(1 for d in bt_weeks if exp_scale.get(d, 1.0) < 0.999)
            cov = {**cov, "regime_block_frac": blocked / max(len(bt_weeks), 1), "coverage_warn": False}

        hold = max(1, int(cfg.hold_weeks or 3))
        bt = run_backtest(
            score, bt_weeks, all_days,
            panels["open_raw"], panels["close_raw"], panels["open_hfq"], close_hfq,
            name_map, cfg, industry_map=ind_map, allow_new_buys=allow, exposure_scale=exp_scale,
        )
        gates = evaluate_gates(pack["ic"], prem_sum, deciles, bt["nav"], bt["trades"], hold_weeks=hold)
        st = nav_stats(bt["nav"], hold_weeks=hold)
        to_med = float(bt["nav"]["turnover_used"].median()) if not bt["nav"].empty else float("nan")
        ind_m = _ind_metrics(bt["trades"], ind_map)
        split = _split_ann(bt["nav"], hold)

        out = out_root / tag
        out.mkdir(parents=True, exist_ok=True)
        bt["nav"].to_csv(out / "nav.csv", index=False, encoding="utf-8-sig")
        bt["trades"].to_csv(out / "trades.csv", index=False, encoding="utf-8-sig")
        gates.to_csv(out / "gates.csv", index=False, encoding="utf-8-sig")
        with open(out / "config.json", "w", encoding="utf-8") as f:
            json.dump({**{k: (str(v) if isinstance(v, date) else v) for k, v in asdict(cfg).items()},
                       "exposure_mode": exposure_mode}, f, ensure_ascii=False, indent=2, default=str)
        (out / "summary.txt").write_text(f"{tag}\n{st}\n{split}\n", encoding="utf-8")

        row = {
            "tag": tag, "axis": axis,
            "ann": st.get("ann_return"), "total": st.get("total_return"),
            "sharpe": st.get("sharpe"), "max_dd": st.get("max_dd"),
            "to_med": to_med, "n_periods": st.get("n_periods"),
            "sell_fail_hard": _sell_fail_hard(bt["trades"]),
            **ind_m, **cov, **split,
        }
        rows.append(row)
        print(f"stats ann={st.get('ann_return')} dd={st.get('max_dd')} sharpe={st.get('sharpe')}", flush=True)

    sdf = pd.DataFrame(rows)
    # 相对 R3 冠军硬基准（避免 R4_BASE 复现失败时误判）
    R3_CHAMP = {"ann": 0.122065, "max_dd": -0.262173, "sharpe": 0.363346}
    if (sdf["tag"] == "R4_BASE").any():
        b = sdf.loc[sdf["tag"] == "R4_BASE"].iloc[0]
        sdf["d_ann"] = sdf["ann"] - float(b["ann"])
        sdf["d_dd"] = sdf["max_dd"] - float(b["max_dd"])
        sdf["ok_ann"] = sdf["d_ann"] >= -0.03
        sdf["clear_up"] = (sdf["d_ann"] >= 0.02) | ((sdf["d_dd"] >= 0.05) & (sdf["d_ann"] >= -0.03))
        sdf["d_ann_r3"] = sdf["ann"] - R3_CHAMP["ann"]
        sdf["d_dd_r3"] = sdf["max_dd"] - R3_CHAMP["max_dd"]
        sdf["clear_up_r3"] = (sdf["d_ann_r3"] >= 0.02) | (
            (sdf["d_dd_r3"] >= 0.05) & (sdf["d_ann_r3"] >= -0.03)
        )
    sdf = sdf.sort_values("sharpe", ascending=False)
    sdf.to_csv(out_root / "r4_summary.csv", index=False, encoding="utf-8-sig")

    print("\n======== R4 SUMMARY ========", flush=True)
    cols = [
        c
        for c in [
            "tag", "ann", "max_dd", "sharpe", "d_ann", "d_dd", "clear_up",
            "d_ann_r3", "d_dd_r3", "clear_up_r3", "ann_2324", "ann_2526", "sell_fail_hard",
        ]
        if c in sdf.columns
    ]
    print(sdf[cols].to_string(index=False), flush=True)

    champ = sdf.loc[sdf["tag"] == "R4_BASE"].iloc[0] if (sdf["tag"] == "R4_BASE").any() else None
    # 废止判定以相对 R3 冠军为准
    ups = sdf[(sdf.get("clear_up_r3", False) == True) & (sdf["tag"] != "R4_BASE")] if "clear_up_r3" in sdf.columns else sdf.iloc[0:0]
    base_ann = float(champ["ann"]) if champ is not None else 0
    base_dd = float(champ["max_dd"]) if champ is not None else -1
    base_sh = float(champ["sharpe"]) if champ is not None else 0
    repro_ok = abs(base_ann - R3_CHAMP["ann"]) < 0.015 and abs(base_dd - R3_CHAMP["max_dd"]) < 0.03
    hard_pass = base_ann >= 0.15 and base_dd >= -0.15 and base_sh >= 1.0
    print("\n======== R4 VERDICT ========", flush=True)
    print(
        f"repro_R3_champ={'OK' if repro_ok else 'FAIL'} R4_BASE ann={base_ann:.2%} dd={base_dd:.2%} sharpe={base_sh:.2f}",
        flush=True,
    )
    if hard_pass:
        print("PASS_HARD: base meets v1 gates.", flush=True)
        verdict = "pass"
    elif len(ups):
        best = ups.sort_values("sharpe", ascending=False).iloc[0]
        print(
            f"IMPROVED_VS_R3: {best['tag']} ann={best['ann']:.2%} dd={best['max_dd']:.2%} "
            f"(d_ann_r3={best['d_ann_r3']:+.2%} d_dd_r3={best['d_dd_r3']:+.2%})",
            flush=True,
        )
        verdict = "improved"
    else:
        print(
            f"KILL: no clear lift vs R3 champ (ann={R3_CHAMP['ann']:.2%} dd={R3_CHAMP['max_dd']:.2%} sharpe={R3_CHAMP['sharpe']:.2f}); "
            f"far from gates 15%/-15%/1.0. Recommend retiring reversion v1.",
            flush=True,
        )
        verdict = "kill"
    (out_root / "verdict.txt").write_text(verdict + "\n", encoding="utf-8")
    print(f"wrote {out_root / 'r4_summary.csv'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
