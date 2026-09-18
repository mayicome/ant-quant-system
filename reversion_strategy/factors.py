# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict, Iterable, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from reversion_strategy.config import FACTOR_NAMES, ReversionStrategyConfig


def winsorize_cross_section(
    s: pd.Series,
    q: Tuple[float, float] = (0.01, 0.99),
) -> pd.Series:
    x = s.astype(float)
    valid = x.dropna()
    if len(valid) < 5:
        return x
    lo, hi = np.nanpercentile(valid.values, [q[0] * 100, q[1] * 100])
    return x.clip(lo, hi)


def zscore_cross_section(s: pd.Series) -> pd.Series:
    x = s.astype(float)
    mu = x.mean(skipna=True)
    sd = x.std(skipna=True, ddof=0)
    if sd is None or not np.isfinite(sd) or sd < 1e-12:
        return x * np.nan
    return (x - mu) / sd


def _wilder_rsi_1d(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder RSI：初值用前 period 日简单平均，之后 Wilder 平滑。"""
    n = len(close)
    out = np.full(n, np.nan)
    if n < period + 1:
        return out
    delta = np.diff(close, prepend=np.nan)
    gain = np.where(np.isfinite(delta) & (delta > 0), delta, 0.0)
    loss = np.where(np.isfinite(delta) & (delta < 0), -delta, 0.0)
    # 第一个 RSI 点在 index=period（用 close[1..period] 的涨跌，共 period 个）
    au = np.nanmean(gain[1 : period + 1])
    ad = np.nanmean(loss[1 : period + 1])
    if ad < 1e-18 and au < 1e-18:
        out[period] = np.nan
    elif ad < 1e-18:
        out[period] = 100.0
    else:
        out[period] = 100.0 * au / (au + ad)
    for i in range(period + 1, n):
        if not np.isfinite(close[i]) or not np.isfinite(close[i - 1]):
            out[i] = np.nan
            # 仍更新平滑，避免断裂后永久失效：用 0 增量
            g = gain[i] if np.isfinite(gain[i]) else 0.0
            l = loss[i] if np.isfinite(loss[i]) else 0.0
        else:
            g = gain[i]
            l = loss[i]
        au = (au * (period - 1) + g) / period
        ad = (ad * (period - 1) + l) / period
        if ad < 1e-18 and au < 1e-18:
            out[i] = np.nan
        elif ad < 1e-18:
            out[i] = 100.0
        else:
            out[i] = 100.0 * au / (au + ad)
    return out


def _wilder_atr_1d(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """Wilder ATR。"""
    n = len(close)
    atr = np.full(n, np.nan)
    if n < period + 1:
        return atr
    prev_c = np.roll(close, 1)
    prev_c[0] = np.nan
    tr_candidates = np.vstack(
        [
            high - low,
            np.abs(high - prev_c),
            np.abs(low - prev_c),
        ]
    )
    with np.errstate(all="ignore"):
        tr = np.nanmax(tr_candidates, axis=0)
    # 第一个 ATR：前 period 个 TR 的简单平均（从 index period-1 起有 period 个 TR 若从 0）
    # 标准：ATR[period-1] = mean(TR[0:period])，但 TR[0] 可能缺 prev_c → 从 1 开始
    start = 1
    if start + period - 1 >= n:
        return atr
    atr[start + period - 1] = np.nanmean(tr[start : start + period])
    for i in range(start + period, n):
        prev = atr[i - 1]
        if not np.isfinite(prev) or not np.isfinite(tr[i]):
            atr[i] = np.nan
            continue
        atr[i] = (prev * (period - 1) + tr[i]) / period
    return atr


def compute_raw_factors(
    close_hfq: pd.DataFrame,
    high_hfq: pd.DataFrame,
    low_hfq: pd.DataFrame,
    amount: pd.DataFrame,
    *,
    rsi_period: int = 14,
    atr_short: int = 10,
    atr_long: int = 20,
) -> Dict[str, pd.DataFrame]:
    close = close_hfq.sort_index()
    high = high_hfq.reindex(index=close.index, columns=close.columns)
    low = low_hfq.reindex(index=close.index, columns=close.columns)
    amt = amount.reindex(index=close.index, columns=close.columns)

    rev5 = close / close.shift(5) - 1.0
    ma20 = close.rolling(20, min_periods=20).mean()
    bias20 = (close - ma20) / ma20
    vol5 = amt.rolling(5, min_periods=5).mean()
    vol20 = amt.rolling(20, min_periods=20).mean()
    vdrain = vol5 / vol20.replace(0, np.nan) - 1.0

    # RSI / ATR 按列
    rsi_mat = np.full(close.shape, np.nan)
    atr_s_mat = np.full(close.shape, np.nan)
    atr_l_mat = np.full(close.shape, np.nan)
    c_vals = close.to_numpy(dtype=float, copy=True)
    h_vals = high.to_numpy(dtype=float, copy=True)
    l_vals = low.to_numpy(dtype=float, copy=True)
    for j in range(c_vals.shape[1]):
        col_c = c_vals[:, j]
        if not np.isfinite(col_c).any():
            continue
        rsi_mat[:, j] = _wilder_rsi_1d(col_c, rsi_period)
        atr_s_mat[:, j] = _wilder_atr_1d(h_vals[:, j], l_vals[:, j], col_c, atr_short)
        atr_l_mat[:, j] = _wilder_atr_1d(h_vals[:, j], l_vals[:, j], col_c, atr_long)

    rsi14 = pd.DataFrame(rsi_mat, index=close.index, columns=close.columns)
    atr10 = pd.DataFrame(atr_s_mat, index=close.index, columns=close.columns)
    atr20 = pd.DataFrame(atr_l_mat, index=close.index, columns=close.columns)
    vshrk = atr10 / atr20.replace(0, np.nan) - 1.0

    return {
        "REV5": rev5,
        "RSI14": rsi14,
        "BIAS20": bias20,
        "VSHRK": vshrk,
        "VDRAIN": vdrain,
    }


def standardize_on_dates(
    raw: Dict[str, pd.DataFrame],
    mask: pd.DataFrame,
    dates: Sequence,
    *,
    winsor_q: Tuple[float, float],
    factor_names=FACTOR_NAMES,
    industry_mode: str = "none",
    industry_map: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame]:
    """周决策日：winsorize →（可选行业去均值）→ z-score。"""
    from reversion_strategy.industry import demean_by_industry

    codes = mask.columns
    use_dates = [d for d in dates if d in mask.index]
    z_out = {k: pd.DataFrame(np.nan, index=use_dates, columns=codes) for k in factor_names}
    pass_mask = pd.DataFrame(False, index=use_dates, columns=codes)
    ind_mode = (industry_mode or "none").strip().lower()
    use_ind = ind_mode == "z" and industry_map is not None

    for dt in use_dates:
        m = mask.loc[dt].fillna(False).astype(bool)
        ok = m.copy()
        for name in factor_names:
            ok &= raw[name].loc[dt].notna()
        pass_mask.loc[dt] = ok
        idx = ok[ok].index
        if len(idx) < 5:
            continue
        for name in factor_names:
            s = raw[name].loc[dt, idx]
            w = winsorize_cross_section(s, winsor_q)
            if use_ind:
                w = demean_by_industry(w, industry_map)
            z = zscore_cross_section(w)
            z_out[name].loc[dt, z.index] = z

    return z_out, pass_mask


def compute_factor_bundle(
    close_hfq: pd.DataFrame,
    high_hfq: pd.DataFrame,
    low_hfq: pd.DataFrame,
    amount: pd.DataFrame,
    tradable_mask: pd.DataFrame,
    cfg: ReversionStrategyConfig,
    week_ends: Optional[Iterable] = None,
    industry_map: Optional[Dict[str, str]] = None,
) -> dict:
    raw = compute_raw_factors(
        close_hfq,
        high_hfq,
        low_hfq,
        amount,
        rsi_period=cfg.rsi_period,
        atr_short=cfg.atr_short,
        atr_long=cfg.atr_long,
    )
    mask = tradable_mask.reindex(index=close_hfq.index, columns=close_hfq.columns).fillna(False)
    dates = list(week_ends) if week_ends is not None else list(mask.index)
    z, pass_mask = standardize_on_dates(
        raw,
        mask,
        dates,
        winsor_q=cfg.winsor_q,
        factor_names=cfg.factor_names,
        industry_mode=str(getattr(cfg, "industry_mode", "none") or "none"),
        industry_map=industry_map,
    )
    return {"raw": raw, "z": z, "pass_mask": pass_mask}
