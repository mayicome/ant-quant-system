# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict, Sequence

import numpy as np
import pandas as pd
from scipy import stats

from reversion_strategy.config import MAIN_FACTORS
from reversion_strategy.weeks import next_trade_day


def spearman_ic_series(
    factor: pd.DataFrame,
    forward_ret: pd.DataFrame,
    mask: pd.DataFrame | None = None,
) -> pd.Series:
    ics = {}
    for dt in forward_ret.index:
        if dt not in factor.index:
            continue
        f = factor.loc[dt]
        r = forward_ret.loc[dt]
        if mask is not None and dt in mask.index:
            m = mask.loc[dt].fillna(False)
            f = f[m]
            r = r.reindex(f.index)
        df = pd.concat([f.rename("f"), r.rename("r")], axis=1).dropna()
        if len(df) < 20:
            continue
        ic, _ = stats.spearmanr(df["f"], df["r"])
        ics[dt] = float(ic)
    return pd.Series(ics).sort_index()


def ic_summary(ic: pd.Series, *, abs_icir: bool = False) -> dict:
    ic = ic.dropna()
    if ic.empty:
        return {"ic_mean": np.nan, "ic_std": np.nan, "icir": np.nan, "abs_ic_mean": np.nan, "n": 0}
    mu = float(ic.mean())
    sd = float(ic.std(ddof=1)) if len(ic) > 1 else np.nan
    icir = (mu / sd) if sd and abs(sd) > 1e-12 else np.nan
    if abs_icir and icir == icir:
        icir = abs(icir)
    return {
        "ic_mean": mu,
        "ic_std": sd,
        "icir": icir,
        "abs_ic_mean": abs(mu),
        "n": int(len(ic)),
    }


def factor_ic_table(
    z_factors: Dict[str, pd.DataFrame],
    week_ret: pd.DataFrame,
    pass_mask: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    for name, fac in z_factors.items():
        ic = spearman_ic_series(fac, week_ret, pass_mask)
        s = ic_summary(ic, abs_icir=True)
        s["factor"] = name
        rows.append(s)
    return pd.DataFrame(rows)[["factor", "ic_mean", "abs_ic_mean", "ic_std", "icir", "n"]]


def ic_decay_table(
    z_factors: Dict[str, pd.DataFrame],
    open_hfq: pd.DataFrame,
    week_ends: Sequence,
    trade_days: Sequence,
    pass_mask: pd.DataFrame,
    horizons: Sequence[int] = (1, 2, 3),
) -> pd.DataFrame:
    """周五因子 vs 未来 h 周 B 口径开盘→开盘收益的 IC。"""
    we = list(week_ends)
    rows = []
    for h in horizons:
        # 构造 forward return：OpenExec(w+h) / OpenExec(w) - 1，index=w
        rec = []
        idx = []
        for i in range(len(we) - h):
            t0 = we[i]
            t_h = we[i + h]
            e0 = next_trade_day(trade_days, t0)
            eh = next_trade_day(trade_days, t_h)
            if e0 is None or eh is None:
                continue
            if e0 not in open_hfq.index or eh not in open_hfq.index:
                continue
            rec.append(open_hfq.loc[eh] / open_hfq.loc[e0] - 1.0)
            idx.append(t0)
        if not rec:
            continue
        fwd = pd.DataFrame(rec, index=idx)
        for name, fac in z_factors.items():
            ic = spearman_ic_series(fac, fwd, pass_mask)
            s = ic_summary(ic, abs_icir=True)
            rows.append({"factor": name, "horizon_weeks": h, **s})
    return pd.DataFrame(rows)


def prediction_deciles(
    score: pd.DataFrame,
    week_ret: pd.DataFrame,
    pass_mask: pd.DataFrame,
    n_groups: int = 10,
) -> pd.DataFrame:
    records = []
    for dt in week_ret.index:
        if dt not in score.index:
            continue
        m = pass_mask.loc[dt].fillna(False) if dt in pass_mask.index else None
        s = score.loc[dt]
        r = week_ret.loc[dt]
        if m is not None:
            s = s[m]
        df = pd.concat([s.rename("score"), r.reindex(s.index).rename("ret")], axis=1).dropna()
        if len(df) < n_groups * 5:
            continue
        try:
            df["grp"] = pd.qcut(df["score"], n_groups, labels=False, duplicates="drop")
        except ValueError:
            continue
        g = df.groupby("grp")["ret"].mean()
        for grp, val in g.items():
            records.append({"date": dt, "group": int(grp) + 1, "ret": float(val)})
    if not records:
        return pd.DataFrame(columns=["group", "mean_ret"])
    long = pd.DataFrame(records)
    return long.groupby("group")["ret"].mean().rename("mean_ret").reset_index()


def top_bottom_spread_t(deciles: pd.DataFrame, top_k: int = 3) -> dict:
    """前 k 组 vs 后 k 组均值差（截面分组已平均，无逐期 t；返回价差）。"""
    if deciles.empty or "group" not in deciles.columns:
        return {"spread": np.nan, "top_mean": np.nan, "bot_mean": np.nan}
    d = deciles.sort_values("group")
    n = len(d)
    if n < top_k * 2:
        return {"spread": np.nan, "top_mean": np.nan, "bot_mean": np.nan}
    top = float(d.tail(top_k)["mean_ret"].mean())
    bot = float(d.head(top_k)["mean_ret"].mean())
    return {"spread": top - bot, "top_mean": top, "bot_mean": bot}


def factor_corr_and_vif(
    z_factors: Dict[str, pd.DataFrame],
    pass_mask: pd.DataFrame,
    sample_dates: Sequence | None = None,
) -> dict:
    names = list(z_factors.keys())
    frames = []
    dates = list(sample_dates) if sample_dates is not None else list(pass_mask.index)
    if len(dates) > 26:
        step = max(1, len(dates) // 26)
        dates = dates[::step][:26]
    for dt in dates:
        if dt not in pass_mask.index:
            continue
        m = pass_mask.loc[dt].fillna(False)
        cols = m[m].index
        if len(cols) < 50:
            continue
        block = pd.DataFrame({k: z_factors[k].loc[dt, cols] for k in names}).dropna()
        if len(block) >= 50:
            frames.append(block)
    if not frames:
        return {"corr": pd.DataFrame(), "vif": pd.Series(dtype=float)}
    data = pd.concat(frames, axis=0)
    corr = data.corr()
    vif = {}
    for name in names:
        y = data[name]
        X = data.drop(columns=[name])
        Xa = np.column_stack([np.ones(len(X)), X.to_numpy(dtype=float)])
        try:
            beta, _, _, _ = np.linalg.lstsq(Xa, y.to_numpy(dtype=float), rcond=None)
            yhat = Xa @ beta
            ss_tot = float(((y - y.mean()) ** 2).sum())
            ss_res = float(((y - yhat) ** 2).sum())
            r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-18 else 0.0
            vif[name] = (1.0 / (1.0 - r2)) if r2 < 0.999 else np.inf
        except Exception:
            vif[name] = np.nan
    return {"corr": corr, "vif": pd.Series(vif)}


def nav_stats(nav: pd.DataFrame, *, hold_weeks: int = 1) -> dict:
    if nav is None or nav.empty or "nav" not in nav.columns:
        return {}
    s = nav["nav"].astype(float)
    if len(s) < 2:
        return {"total_return": np.nan}
    r = s.pct_change().dropna()
    total = float(s.iloc[-1] / s.iloc[0] - 1.0)
    n = len(r)
    # 每个观测跨 hold_weeks 个自然周 → 一年约 52/hold_weeks 个决策期
    h = max(1, int(hold_weeks))
    periods_per_year = 52.0 / h
    ann = (1.0 + total) ** (periods_per_year / max(n, 1)) - 1.0 if n > 0 else np.nan
    vol = float(r.std(ddof=1) * np.sqrt(periods_per_year)) if len(r) > 1 else np.nan
    sharpe = float(ann / vol) if vol and vol > 1e-12 else np.nan
    peak = s.cummax()
    dd = float(((s - peak) / peak).min())
    return {
        "total_return": total,
        "ann_return": ann,
        "ann_vol": vol,
        "sharpe": sharpe,
        "max_dd": dd,
        "n_periods": int(n),
        "hold_weeks": h,
    }


def evaluate_gates(
    ic_tbl: pd.DataFrame,
    prem_sum: pd.DataFrame,
    deciles: pd.DataFrame,
    nav: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    hold_weeks: int = 1,
) -> pd.DataFrame:
    """对照规格 §7 硬门槛（样本内滚动即样本外；此处仅汇总指标，不改门槛）。"""
    rows = []
    main = [f for f in MAIN_FACTORS if ic_tbl is not None and not ic_tbl.empty and f in set(ic_tbl["factor"])]
    if main and not ic_tbl.empty:
        sub = ic_tbl[ic_tbl["factor"].isin(main)]
        rows.append({"gate": "main_|IC|_mean", "value": float(sub["abs_ic_mean"].mean()), "threshold": 0.03, "pass": float(sub["abs_ic_mean"].mean()) >= 0.03})
        rows.append({"gate": "main_ICIR_abs", "value": float(sub["icir"].mean()), "threshold": 0.3, "pass": float(sub["icir"].mean()) >= 0.3})
    if prem_sum is not None and not prem_sum.empty and main:
        subp = prem_sum[prem_sum["factor"].isin(main)]
        if not subp.empty and "t_nw" in subp.columns:
            tmean = float(subp["t_nw"].abs().mean())
            rows.append({"gate": "main_|t|_mean", "value": tmean, "threshold": 2.0, "pass": tmean >= 2.0})
    sp = top_bottom_spread_t(deciles)
    rows.append({"gate": "decile_spread", "value": sp["spread"], "threshold": None, "pass": None})
    st = nav_stats(nav, hold_weeks=hold_weeks)
    if st:
        rows.append({"gate": "ann_return", "value": st.get("ann_return"), "threshold": 0.15, "pass": (st.get("ann_return") or -1) >= 0.15})
        rows.append({"gate": "max_dd", "value": st.get("max_dd"), "threshold": -0.15, "pass": (st.get("max_dd") or -1) >= -0.15})
        rows.append({"gate": "sharpe", "value": st.get("sharpe"), "threshold": 1.0, "pass": (st.get("sharpe") or -1) >= 1.0})
    if trades is not None and not trades.empty and "side" in trades.columns:
        n_buy = int((trades["side"] == "buy").sum())
        n_buy_fail = int(trades["side"].isin(["buy_defer", "buy_abandon"]).sum())
        n_sell = int((trades["side"] == "sell").sum())
        n_sell_fail = int((trades["side"] == "sell_defer").sum())
        buy_fail_rate = n_buy_fail / max(n_buy + n_buy_fail, 1)
        sell_fail_rate = n_sell_fail / max(n_sell + n_sell_fail, 1)
        rows.append({"gate": "buy_fail_rate", "value": buy_fail_rate, "threshold": 0.10, "pass": buy_fail_rate <= 0.10})
        rows.append({"gate": "sell_fail_rate", "value": sell_fail_rate, "threshold": 0.10, "pass": sell_fail_rate <= 0.10})
        if "reason" in trades.columns:
            hard = trades[(trades["side"] == "sell_defer") & (~trades["reason"].fillna("").eq("turnover_partial"))]
            n_hard = int(len(hard))
            sell_fail_hard = n_hard / max(n_sell + n_sell_fail, 1)
            rows.append(
                {
                    "gate": "sell_fail_hard",
                    "value": sell_fail_hard,
                    "threshold": 0.10,
                    "pass": sell_fail_hard <= 0.10,
                }
            )
    return pd.DataFrame(rows)
