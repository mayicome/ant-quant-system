# -*- coding: utf-8 -*-
"""Fama-MacBeth：B 口径开盘→开盘周收益；决策溢价 ≤ w−2。"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from reversion_strategy.config import FACTOR_THEORY_SIGN, ReversionStrategyConfig
from reversion_strategy.factors import winsorize_cross_section
from reversion_strategy.weeks import next_trade_day
from scipy import stats


def open_to_open_week_returns(
    open_hfq: pd.DataFrame,
    week_ends: Sequence,
    trade_days: Sequence,
    *,
    hold_weeks: int = 1,
) -> pd.DataFrame:
    """
    B 口径开盘→开盘收益。
    hold_weeks=1：相邻决策周 OpenExec(w)→OpenExec(w+1)
    hold_weeks=H：决策序列若已按每 H 周抽样，则相邻决策点天然跨 H 周；
                  若传入全量 week_ends，则用 we[i]→we[i+H]。
    index = 起始决策日（与 β 配对）。
    """
    we = list(week_ends)
    h = max(1, int(hold_weeks))
    rows = []
    idx = []
    for i in range(len(we) - h):
        t0, t1 = we[i], we[i + h]
        e0 = next_trade_day(trade_days, t0)
        e1 = next_trade_day(trade_days, t1)
        if e0 is None or e1 is None:
            continue
        if e0 not in open_hfq.index or e1 not in open_hfq.index:
            continue
        r = open_hfq.loc[e1] / open_hfq.loc[e0] - 1.0
        rows.append(r)
        idx.append(t0)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, index=idx)


def cross_section_ols(
    y: pd.Series,
    X: pd.DataFrame,
) -> Tuple[Optional[pd.Series], float]:
    df = pd.concat([y.rename("y"), X], axis=1).dropna()
    if len(df) < X.shape[1] + 5:
        return None, np.nan
    yv = df["y"].to_numpy(dtype=float)
    Xv = df.drop(columns=["y"]).to_numpy(dtype=float)
    Xa = np.column_stack([np.ones(len(Xv)), Xv])
    try:
        beta, _, _, _ = np.linalg.lstsq(Xa, yv, rcond=None)
    except Exception:
        return None, np.nan
    yhat = Xa @ beta
    ss_tot = float(((yv - yv.mean()) ** 2).sum())
    ss_res = float(((yv - yhat) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-18 else np.nan
    names = ["const"] + list(df.drop(columns=["y"]).columns)
    return pd.Series(beta, index=names), float(r2)


def run_fama_macbeth(
    z_factors: Dict[str, pd.DataFrame],
    pass_mask: pd.DataFrame,
    week_ret: pd.DataFrame,
    week_ends: Sequence,
    cfg: ReversionStrategyConfig,
    industry_map: Optional[Dict[str, str]] = None,
) -> dict:
    """
    每期横截面回归得 f(k,w)（可预计算存档）。
    预测时滚动均值仅用下标 ≤ w−2 的已实现溢价。
    score_mode:
      fm — Σ β·f̄
      theory_sign — Σ (−z)（理论负向等权）
      icir_w — 滚动 |ICIR| 加权的 Σ (−z)
    """
    names = list(cfg.factor_names)
    we = list(week_ends)
    we_pos = {d: i for i, d in enumerate(we)}
    lag = int(cfg.premium_lag)  # 2
    mode = str(getattr(cfg, "score_mode", "fm") or "fm").strip().lower()

    premium_rows = []
    r2_list = []
    score = pd.DataFrame(np.nan, index=we, columns=z_factors[names[0]].columns)

    hist_f: List[pd.Series] = []
    hist_dates: List = []
    # 滚动 IC 序列（用于 icir_w）
    ic_hist: Dict[str, List[float]] = {k: [] for k in names}
    ic_hist_dates: List = []

    for T in we:
        if T in week_ret.index:
            y = winsorize_cross_section(week_ret.loc[T], cfg.winsor_q)
            if T in pass_mask.index:
                passed = pass_mask.loc[T].fillna(False)
                cols = [c for c in y.index if passed.get(c, False)]
                if len(cols) >= 20:
                    X = pd.DataFrame({k: z_factors[k].loc[T, cols] for k in names})
                    y_sub = y.reindex(cols)
                    if str(getattr(cfg, "industry_mode", "none") or "none").strip().lower() == "res":
                        from reversion_strategy.industry import industry_residualize_frame

                        y_sub, X = industry_residualize_frame(y_sub, X, industry_map or {})
                    coef, r2 = cross_section_ols(y_sub, X)
                    if coef is not None:
                        prem = coef.drop(labels=["const"], errors="ignore")
                        premium_rows.append(prem.rename(T))
                        r2_list.append({"date": T, "r2": r2, "n": len(cols)})
                        hist_f.append(prem)
                        hist_dates.append(T)
                    # IC（与回归同期，仅作滚动权重原料；预测时只用滞后）
                    for k in names:
                        df = pd.concat(
                            [
                                z_factors[k].loc[T, cols].astype(float).rename("f"),
                                y_sub.rename("r"),
                            ],
                            axis=1,
                        ).dropna()
                        if len(df) >= 20:
                            ic, _ = stats.spearmanr(df["f"], df["r"])
                            ic_hist[k].append(float(ic))
                        else:
                            ic_hist[k].append(float("nan"))
                    ic_hist_dates.append(T)

        i = we_pos[T]
        if i < lag:
            continue
        if T not in pass_mask.index or T not in z_factors[names[0]].index:
            continue
        passed = pass_mask.loc[T].fillna(False)
        cols = passed[passed].index
        if len(cols) == 0:
            continue

        s = pd.Series(0.0, index=cols, dtype=float)
        if mode == "theory_sign":
            for k in names:
                sign = float(FACTOR_THEORY_SIGN.get(k, -1.0))
                s = s + z_factors[k].loc[T, cols].astype(float) * sign
        elif mode == "icir_w":
            cutoff = we[i - lag]
            # 用截止日前的 IC 窗口估 ICIR
            usable_idx = [j for j, d in enumerate(ic_hist_dates) if d <= cutoff]
            if len(usable_idx) < max(4, min(12, cfg.estimate_window // 4)):
                continue
            use = usable_idx[-cfg.estimate_window :]
            weights = {}
            for k in names:
                arr = np.array([ic_hist[k][j] for j in use], dtype=float)
                arr = arr[np.isfinite(arr)]
                if len(arr) < 4:
                    weights[k] = 0.0
                    continue
                mu = float(arr.mean())
                sd = float(arr.std(ddof=1)) if len(arr) > 1 else np.nan
                icir = (mu / sd) if sd and abs(sd) > 1e-12 else 0.0
                # 反转因子 IC 为负属正常：权重用 |ICIR|
                weights[k] = abs(icir)
            wsum = sum(weights.values())
            if wsum < 1e-12:
                continue
            for k in names:
                sign = float(FACTOR_THEORY_SIGN.get(k, -1.0))
                w = weights[k] / wsum
                s = s + z_factors[k].loc[T, cols].astype(float) * sign * w
        else:
            # 默认 fm
            cutoff = we[i - lag]
            usable = [hist_f[j] for j, d in enumerate(hist_dates) if d <= cutoff]
            if len(usable) < max(4, min(12, cfg.estimate_window // 4)):
                continue
            window = usable[-cfg.estimate_window :]
            f_bar = pd.concat(window, axis=1).mean(axis=1)
            for k in names:
                s = s + z_factors[k].loc[T, cols].astype(float) * float(f_bar.get(k, 0.0))
        score.loc[T, cols] = s

    premium = pd.DataFrame(premium_rows)
    if not premium.empty:
        premium.index.name = "date"
    return {
        "premium": premium,
        "fm_r2": pd.DataFrame(r2_list),
        "score": score,
        "hist_dates": hist_dates,
    }


def newey_west_tstat(series: pd.Series, lags: int = 4) -> float:
    x = series.dropna().to_numpy(dtype=float)
    T = len(x)
    if T < lags + 5:
        return float("nan")
    mu = x.mean()
    e = x - mu
    gamma0 = float(np.dot(e, e) / T)
    var = gamma0
    for lag in range(1, lags + 1):
        w = 1.0 - lag / (lags + 1.0)
        gamma = float(np.dot(e[lag:], e[:-lag]) / T)
        var += 2.0 * w * gamma
    se = np.sqrt(max(var, 0.0) / T)
    if se < 1e-18:
        return float("nan")
    return float(mu / se)


def premium_summary(premium: pd.DataFrame, lags: int = 4) -> pd.DataFrame:
    rows = []
    for col in premium.columns:
        s = premium[col].dropna()
        rows.append(
            {
                "factor": col,
                "mean": float(s.mean()) if len(s) else np.nan,
                "std": float(s.std(ddof=1)) if len(s) > 1 else np.nan,
                "t_nw": newey_west_tstat(s, lags=lags),
                "n": int(len(s)),
            }
        )
    return pd.DataFrame(rows)
