# -*- coding: utf-8 -*-
"""
hold=3 第三轮 R3a：行业中性 / 大盘过滤 / 波动·流动性（单轴）。
底座：五因子 + 关止损 + FM + N=20 + hold=3。
共享面板与原始因子；按 mask / 行业标准化 / FM / 回测参数缓存。
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
from reversion_strategy.regime import (
    build_allow_new_buys,
    load_csi_close,
    load_regime_daily,
    regime_coverage_stats,
)
from reversion_strategy.validate import evaluate_gates, factor_ic_table, nav_stats, prediction_deciles
from reversion_strategy.weeks import week_end_dates

# tag, axis, overrides dict
R3A: List[Tuple[str, str, dict]] = [
    ("BASE", "base", {}),
    # A 行业
    ("A_IND_Z", "A", {"industry_mode": "z"}),
    ("A_IND_CAP2", "A", {"industry_cap_k": 2}),
    ("A_IND_CAP3", "A", {"industry_cap_k": 3}),
    ("A_IND_CAP4", "A", {"industry_cap_k": 4}),
    ("A_IND_RES", "A", {"industry_mode": "res"}),
    # B 大盘
    ("B_BEAR_SKIP", "B", {"regime_mode": "bear_skip"}),
    ("B_BULL_ONLY", "B", {"regime_mode": "bull_only"}),
    ("B_NO_KILL", "B", {"regime_mode": "no_kill"}),
    ("B_NO_KILL_BEAR", "B", {"regime_mode": "no_kill_bear"}),
    ("B_CSI_MA20", "B", {"regime_mode": "csi_ma", "regime_ma_window": 20}),
    ("B_CSI_MA60", "B", {"regime_mode": "csi_ma", "regime_ma_window": 60}),
    # C 流动/波动
    ("C_LQ05", "C", {"min_avg_amount": 0.5e8}),
    ("C_LQ20", "C", {"min_avg_amount": 2e8}),
    ("C_LQP30", "C", {"liquidity_pct": 0.30}),
    ("C_LQP50", "C", {"liquidity_pct": 0.50}),
    ("C_VOLATR10", "C", {"vol_filter": "atr", "vol_drop_q": 0.10}),
    ("C_VOLATR20", "C", {"vol_filter": "atr", "vol_drop_q": 0.20}),
    ("C_VOLRV10", "C", {"vol_filter": "rv", "vol_drop_q": 0.10}),
    ("C_VOLRV20", "C", {"vol_filter": "rv", "vol_drop_q": 0.20}),
]

# R3b：各轴优胜交叉（R3a 后手工确认；默认一并收录）
R3B: List[Tuple[str, str, dict]] = [
    ("X_CAP4_LQ05", "AB", {"industry_cap_k": 4, "min_avg_amount": 0.5e8}),
    ("X_CAP4_ATR10", "AC", {"industry_cap_k": 4, "vol_filter": "atr", "vol_drop_q": 0.10}),
    ("X_CAP4_MA60", "AB", {"industry_cap_k": 4, "regime_mode": "csi_ma", "regime_ma_window": 60}),
    ("X_CAP4_LQ05_ATR10", "ABC", {"industry_cap_k": 4, "min_avg_amount": 0.5e8, "vol_filter": "atr", "vol_drop_q": 0.10}),
]

ALL_VARIANTS = R3A + R3B


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


def _mask_key(cfg: ReversionStrategyConfig) -> tuple:
    return (
        float(cfg.min_avg_amount),
        float(cfg.liquidity_pct or 0),
        str(cfg.vol_filter or "none"),
        float(cfg.vol_drop_q or 0),
    )


def _z_key(cfg: ReversionStrategyConfig, mk: tuple) -> tuple:
    return mk + (str(cfg.industry_mode or "none"),)


def _fm_key(cfg: ReversionStrategyConfig, zk: tuple) -> tuple:
    # res 改变 FM；z 已在 zk 里
    return zk + (str(cfg.industry_mode or "none"),)


def _sell_fail_hard(trades: pd.DataFrame) -> float:
    if trades is None or trades.empty:
        return float("nan")
    n_sell = int((trades["side"] == "sell").sum())
    n_def = int((trades["side"] == "sell_defer").sum())
    if "reason" in trades.columns:
        n_hard = int(
            (
                (trades["side"] == "sell_defer")
                & (~trades["reason"].fillna("").eq("turnover_partial"))
            ).sum()
        )
    else:
        n_hard = n_def
    return n_hard / max(n_sell + n_def, 1)


def _holding_industry_metrics(trades: pd.DataFrame, nav: pd.DataFrame, ind_map: Dict[str, str]) -> dict:
    """用期末近似：各决策日后仍持有的买入净敞粗算；简化为 trades 最后一日持仓不可得时用 buy-sell 累计。"""
    if trades is None or trades.empty or nav is None or nav.empty:
        return {"ind_n_mean": np.nan, "ind_max_w_mean": np.nan, "ind_hhi_mean": np.nan}
    # 简化：每个 exec 日统计当日 buy 的行业分散（近似目标组合）
    buys = trades[trades["side"] == "buy"].copy()
    if buys.empty:
        return {"ind_n_mean": np.nan, "ind_max_w_mean": np.nan, "ind_hhi_mean": np.nan}
    buys["date"] = pd.to_datetime(buys["date"]).dt.date
    rows = []
    for d, g in buys.groupby("date"):
        codes = list(g["code"].astype(str).str.zfill(6).unique())
        st = portfolio_industry_stats(codes, ind_map)
        rows.append(st)
    if not rows:
        return {"ind_n_mean": np.nan, "ind_max_w_mean": np.nan, "ind_hhi_mean": np.nan}
    df = pd.DataFrame(rows)
    return {
        "ind_n_mean": float(df["n_ind"].mean()),
        "ind_max_w_mean": float(df["max_w"].mean()),
        "ind_hhi_mean": float(df["hhi"].mean()),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2023-01-01")
    p.add_argument("--end", default="2026-09-01")
    p.add_argument("--max-stocks", type=int, default=None)
    p.add_argument("--out-root", default="history_data/reversion_strategy/hold3_round3")
    p.add_argument("--stage", default="all", choices=["r3a", "r3b", "all"], help="which grid")
    p.add_argument("--tags", default=None, help="comma-separated tags to run (default by --stage)")
    args = p.parse_args(argv)

    start = datetime.strptime(args.start[:10], "%Y-%m-%d").date()
    end = datetime.strptime(args.end[:10], "%Y-%m-%d").date()
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    pool = ALL_VARIANTS if args.stage == "all" else (R3A if args.stage == "r3a" else R3B)
    want = None
    if args.tags:
        want = {t.strip() for t in args.tags.split(",") if t.strip()}
    variants = [v for v in pool if want is None or v[0] in want]

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
    )
    load_start = start - timedelta(days=400 + 7 * base.estimate_window * 3)
    print(f"[r3] loading panels uni={len(uni)} ...", flush=True)
    panels = build_panels(uni, start=load_start, end=end)
    all_days = align_calendar_from_panels(panels)
    print(f"[r3] trade_days={len(all_days)}", flush=True)

    close_hfq = panels["close_hfq"]
    high_hfq = panels["high_hfq"] if not panels["high_hfq"].empty else close_hfq
    low_hfq = panels["low_hfq"] if not panels["low_hfq"].empty else close_hfq

    print("[r3] SW1 map ...", flush=True)
    ind_map = load_sw1_map(list(close_hfq.columns))
    print(f"[r3] sw1 mapped={sum(1 for v in ind_map.values() if v != 'UNK')}/{len(ind_map)}", flush=True)

    print("[r3] base tradable mask ...", flush=True)
    mask_core = build_tradable_mask(
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
    print(f"[r3] decision_weeks={len(decision_weeks)}", flush=True)

    print("[r3] raw factors once ...", flush=True)
    raw = compute_raw_factors(
        close_hfq,
        high_hfq,
        low_hfq,
        panels["amount"],
        rsi_period=base.rsi_period,
        atr_short=base.atr_short,
        atr_long=base.atr_long,
    )
    week_ret = open_to_open_week_returns(
        panels["open_hfq"], decision_weeks, all_days, hold_weeks=1
    )

    print("[r3] regime / csi ...", flush=True)
    try:
        regime_df = load_regime_daily()
    except Exception as e:
        print(f"[r3] regime load failed: {e}", flush=True)
        regime_df = pd.DataFrame()
    csi_close = load_csi_close()

    mask_cache: Dict[tuple, pd.DataFrame] = {}
    z_cache: Dict[tuple, tuple] = {}
    fm_cache: Dict[tuple, dict] = {}
    allow_cache: Dict[tuple, Dict] = {}

    def get_mask(cfg: ReversionStrategyConfig) -> pd.DataFrame:
        mk = _mask_key(cfg)
        if mk in mask_cache:
            return mask_cache[mk]
        # 绝对流动性：若与 base 不同需重算 core；简化：从 core 派生时若阈值更高则收紧 amount
        cfg_m = replace(base, min_avg_amount=cfg.min_avg_amount)
        if abs(cfg.min_avg_amount - base.min_avg_amount) > 1:
            m0 = build_tradable_mask(
                close_hfq,
                panels["amount"],
                panels["close_raw"],
                panels["volume_raw"],
                meta,
                trade_days=all_days,
                cfg=cfg_m,
                name_map=name_map,
            )
        else:
            m0 = mask_core
        m1 = apply_liquidity_pct_and_vol_filters(
            m0,
            close_hfq,
            panels["amount"],
            cfg,
            high_hfq=high_hfq,
            low_hfq=low_hfq,
        )
        mask_cache[mk] = m1
        return m1

    def get_z(cfg: ReversionStrategyConfig):
        mk = _mask_key(cfg)
        # z 模式用 industry_mode=z；res/none 用 plain z
        ind_for_z = "z" if str(cfg.industry_mode).lower() == "z" else "none"
        zk = mk + (ind_for_z,)
        if zk in z_cache:
            return z_cache[zk]
        mask = get_mask(cfg)
        z, pass_mask = standardize_on_dates(
            raw,
            mask,
            decision_weeks,
            winsor_q=cfg.winsor_q,
            factor_names=cfg.factor_names,
            industry_mode=ind_for_z,
            industry_map=ind_map,
        )
        z_cache[zk] = (z, pass_mask)
        return z_cache[zk]

    def get_fm(cfg: ReversionStrategyConfig):
        z, pass_mask = get_z(cfg)
        ind_for_z = "z" if str(cfg.industry_mode).lower() == "z" else "none"
        fk = _mask_key(cfg) + (ind_for_z, str(cfg.industry_mode or "none"))
        if fk in fm_cache:
            return fm_cache[fk]
        print(f"[r3] FM key={fk} ...", flush=True)
        fm = run_fama_macbeth(
            z, pass_mask, week_ret, decision_weeks, cfg, industry_map=ind_map
        )
        fm_cache[fk] = {
            "fm": fm,
            "z": z,
            "pass_mask": pass_mask,
            "ic": factor_ic_table(z, week_ret, pass_mask),
        }
        return fm_cache[fk]

    def get_allow(cfg: ReversionStrategyConfig):
        key = (str(cfg.regime_mode or "off"), int(cfg.regime_ma_window or 60))
        if key in allow_cache:
            return allow_cache[key]
        allow = build_allow_new_buys(
            decision_weeks,
            mode=str(cfg.regime_mode or "off"),
            ma_window=int(cfg.regime_ma_window or 60),
            regime_df=regime_df if not regime_df.empty else None,
            csi_close=csi_close,
        )
        allow_cache[key] = allow
        return allow

    rows = []
    for tag, axis, overs in variants:
        cfg = replace(
            base,
            **{k: v for k, v in overs.items()},
        )
        print(f"\n===== {tag} axis={axis} {overs} =====", flush=True)
        pack = get_fm(cfg)
        fm = pack["fm"]
        score = fm["score"].dropna(how="all")
        premium = fm["premium"]
        prem_sum = premium_summary(premium) if not premium.empty else pd.DataFrame()
        deciles = prediction_deciles(score, week_ret, pack["pass_mask"])
        bt_weeks = _bt_weeks(score, decision_weeks, fm["hist_dates"], cfg)
        allow = get_allow(cfg)
        cov = regime_coverage_stats(
            bt_weeks, allow, regime_df if not regime_df.empty else None, mode=str(cfg.regime_mode)
        )
        print(f"[r3] backtest periods={len(bt_weeks)} block_frac={cov['regime_block_frac']:.2%}", flush=True)
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
            industry_map=ind_map,
            allow_new_buys=allow,
        )
        gates = evaluate_gates(
            pack["ic"], prem_sum, deciles, bt["nav"], bt["trades"], hold_weeks=3
        )
        st = nav_stats(bt["nav"], hold_weeks=3)
        to_med = (
            float(bt["nav"]["turnover_used"].median())
            if bt["nav"] is not None and not bt["nav"].empty
            else float("nan")
        )
        ind_m = _holding_industry_metrics(bt["trades"], bt["nav"], ind_map)
        pool_n = float(pack["pass_mask"].loc[bt_weeks].sum(axis=1).mean()) if bt_weeks else float("nan")

        out = out_root / tag
        out.mkdir(parents=True, exist_ok=True)
        bt["nav"].to_csv(out / "nav.csv", index=False, encoding="utf-8-sig")
        bt["trades"].to_csv(out / "trades.csv", index=False, encoding="utf-8-sig")
        gates.to_csv(out / "gates.csv", index=False, encoding="utf-8-sig")
        if not prem_sum.empty:
            prem_sum.to_csv(out / "premium_summary.csv", index=False, encoding="utf-8-sig")
        with open(out / "config.json", "w", encoding="utf-8") as f:
            json.dump(
                {k: (str(v) if isinstance(v, date) else v) for k, v in asdict(cfg).items()},
                f,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        (out / "summary.txt").write_text(f"{tag}\n{st}\n{cov}\n", encoding="utf-8")

        row = {
            "tag": tag,
            "axis": axis,
            **{f"p_{k}": v for k, v in overs.items()},
            "ann": st.get("ann_return"),
            "total": st.get("total_return"),
            "sharpe": st.get("sharpe"),
            "max_dd": st.get("max_dd"),
            "to_med": to_med,
            "n_periods": st.get("n_periods"),
            "sell_fail_raw": float(gates.loc[gates.gate == "sell_fail_rate", "value"].iloc[0])
            if (gates["gate"] == "sell_fail_rate").any()
            else np.nan,
            "sell_fail_hard": _sell_fail_hard(bt["trades"]),
            "pool_n_mean": pool_n,
            **ind_m,
            **cov,
        }
        rows.append(row)
        print(f"stats {st} dd={st.get('max_dd')} cov_warn={cov['coverage_warn']}", flush=True)

    sdf = pd.DataFrame(rows)
    # 合并已有 R3a 汇总（若存在），便于一次看全表
    prev_path = out_root / "r3_summary.csv"
    if prev_path.exists() and args.stage == "r3b" and not sdf.empty:
        prev = pd.read_csv(prev_path)
        sdf = pd.concat([prev[~prev["tag"].isin(set(sdf["tag"]))], sdf], ignore_index=True)
    if not sdf.empty and (sdf["tag"] == "BASE").any():
        b = sdf.loc[sdf["tag"] == "BASE"].iloc[0]
        sdf["d_ann"] = sdf["ann"] - float(b["ann"])
        sdf["d_dd"] = sdf["max_dd"] - float(b["max_dd"])
        sdf["ok_ann"] = sdf["d_ann"] >= -0.03
    if not sdf.empty:
        sdf = sdf.sort_values(["axis", "ann"], ascending=[True, False])
    sdf.to_csv(out_root / "r3_summary.csv", index=False, encoding="utf-8-sig")
    print("\n======== R3 SUMMARY ========", flush=True)
    cols = [
        c
        for c in [
            "tag",
            "axis",
            "ann",
            "max_dd",
            "sharpe",
            "d_ann",
            "d_dd",
            "ok_ann",
            "sell_fail_hard",
            "regime_block_frac",
            "coverage_warn",
            "ind_hhi_mean",
            "pool_n_mean",
        ]
        if c in sdf.columns
    ]
    print(sdf[cols].to_string(index=False), flush=True)
    print(f"wrote {out_root / 'r3_summary.csv'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
