# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from trend_strategy.config import FACTOR_NAMES, TrendStrategyConfig
from trend_strategy.factors import winsorize_cross_section


def week_returns_hfq(
    close_hfq: pd.DataFrame,
    week_ends: Sequence,
) -> pd.DataFrame:
    """r(T→T+1) = P(T+1)/P(T)-1，index 为 T（收益实现于下一周末）。"""
    we = list(week_ends)
    rows = []
    idx = []
    for i in range(len(we) - 1):
        t0, t1 = we[i], we[i + 1]
        if t0 not in close_hfq.index or t1 not in close_hfq.index:
            continue
        r = close_hfq.loc[t1] / close_hfq.loc[t0] - 1.0
        rows.append(r)
        idx.append(t0)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, index=idx)


def cross_section_ols(
    y: pd.Series,
    X: pd.DataFrame,
) -> Tuple[Optional[pd.Series], float]:
    """
    y = α + X β + ε，返回 (coeffs 含 const, r2)。
    coeffs index: ['const', *X.columns]
    """
    df = pd.concat([y.rename("y"), X], axis=1).dropna()
    if len(df) < X.shape[1] + 5:
        return None, np.nan
    yv = df["y"].to_numpy(dtype=float)
    Xv = df.drop(columns=["y"]).to_numpy(dtype=float)
    # 加截距
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
    cfg: TrendStrategyConfig,
) -> dict:
    """
    每期横截面回归得到 f(k,T)；并生成滚动预测得分。
    week_ret index = T（当期周末），值为到下一周末的收益。
    """
    names = list(cfg.factor_names)
    premium_rows = []
    r2_list = []
    score = pd.DataFrame(np.nan, index=list(week_ends), columns=z_factors[names[0]].columns)

    # 只在有下一期收益的 T 上估溢价
    hist_f: List[pd.Series] = []
    hist_dates: List = []

    for T in week_ends:
        if T not in week_ret.index:
            # 无下一期收益：仍可用历史溢价预测
            pass
        else:
            y = week_ret.loc[T]
            y = winsorize_cross_section(y, cfg.winsor_q)
            if T not in pass_mask.index:
                continue
            passed = pass_mask.loc[T].fillna(False)
            cols = [c for c in y.index if passed.get(c, False)]
            if len(cols) < 20:
                continue
            X = pd.DataFrame({k: z_factors[k].loc[T, cols] for k in names})
            y_sub = y.reindex(cols)
            coef, r2 = cross_section_ols(y_sub, X)
            if coef is None:
                continue
            prem = coef.drop(labels=["const"], errors="ignore")
            premium_rows.append(prem.rename(T))
            r2_list.append({"date": T, "r2": r2, "n": len(cols)})
            hist_f.append(prem)
            hist_dates.append(T)

        # 预测：用过去 W 期已实现溢价（不含需要未来收益才能得到的「当期」）
        # 若刚把 T 的溢价 append 了，预测 T 时不应包含它 → 用 hist[:-1]
        # 若本轮没估出溢价，用全部 hist
        if T in week_ret.index and hist_dates and hist_dates[-1] == T:
            usable = hist_f[:-1]
        else:
            usable = hist_f
        if len(usable) < max(4, min(12, cfg.estimate_window // 4)):
            continue
        window = usable[-cfg.estimate_window :]
        f_bar = pd.concat(window, axis=1).mean(axis=1)
        if T not in pass_mask.index or T not in z_factors[names[0]].index:
            continue
        passed = pass_mask.loc[T].fillna(False)
        cols = passed[passed].index
        if len(cols) == 0:
            continue
        s = pd.Series(0.0, index=cols, dtype=float)
        for k in names:
            s = s + z_factors[k].loc[T, cols].astype(float) * float(f_bar.get(k, 0.0))
        score.loc[T, cols] = s

    premium = pd.DataFrame(premium_rows)
    if not premium.empty:
        premium.index.name = "date"
    fm_r2 = pd.DataFrame(r2_list)
    return {
        "premium": premium,
        "fm_r2": fm_r2,
        "score": score,
        "hist_dates": hist_dates,
    }


def newey_west_tstat(series: pd.Series, lags: int = 4) -> float:
    """对溢价时间序列做 NW t 统计量。"""
    x = series.dropna().to_numpy(dtype=float)
    T = len(x)
    if T < lags + 5:
        return float("nan")
    mu = x.mean()
    # HAC variance of mean
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
