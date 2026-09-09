# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict, Iterable, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from trend_strategy.config import FACTOR_NAMES, TrendStrategyConfig


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


def _slope_r2_1d(y: np.ndarray, window: int) -> Tuple[np.ndarray, np.ndarray]:
    """y 已是 ln(P)，等间隔回归。"""
    n = len(y)
    slope = np.full(n, np.nan)
    r2 = np.full(n, np.nan)
    if n < window:
        return slope, r2
    t = np.arange(window, dtype=np.float64)
    t -= t.mean()
    sxx = float(t @ t)
    shape = (n - window + 1, window)
    strides = (y.strides[0], y.strides[0])
    try:
        windows = np.lib.stride_tricks.as_strided(y, shape=shape, strides=strides)
    except Exception:
        windows = None
    if windows is not None and windows.flags["OWNDATA"] is False:
        wmean = windows.mean(axis=1)
        yc = windows - wmean[:, None]
        b = (yc * t).sum(axis=1) / sxx
        yhat = b[:, None] * t
        ss_tot = (yc * yc).sum(axis=1)
        ss_res = ((yc - yhat) ** 2).sum(axis=1)
        good = np.isfinite(windows).all(axis=1) & (ss_tot > 1e-18)
        out_b = np.where(good, b * 252.0, np.nan)
        out_r2 = np.where(good, 1.0 - ss_res / ss_tot, np.nan)
        slope[window - 1 :] = out_b
        r2[window - 1 :] = out_r2
        return slope, r2

    for i in range(window - 1, n):
        yy = y[i - window + 1 : i + 1]
        if not np.isfinite(yy).all():
            continue
        yc = yy - yy.mean()
        b = float(t @ yc) / sxx
        resid = yc - b * t
        ss_tot = float(yc @ yc)
        ss_res = float(resid @ resid)
        slope[i] = b * 252.0
        r2[i] = (1.0 - ss_res / ss_tot) if ss_tot > 1e-18 else np.nan
    return slope, r2


def compute_raw_factors(
    close_hfq: pd.DataFrame,
    amount: pd.DataFrame,
    *,
    slope_window: int = 60,
    factor_pack: str = "week",
) -> Dict[str, pd.DataFrame]:
    """
    factor_pack=week：v1.2 MOM20/SLOPE60/BRK20/VPC5/MAALG(5-10-20)
    factor_pack=half：MOM10/SLOPE30/BRK10/VPC3/MAALG(3-6-12)
    """
    close = close_hfq.sort_index()
    amt = amount.reindex(index=close.index, columns=close.columns)
    pack = (factor_pack or "week").strip().lower()

    if pack == "half":
        # MOM10：剔除最近 1 日，10 日中期短动量
        mom = close.shift(1) / close.shift(11) - 1.0
        h = close.shift(1).rolling(10, min_periods=10).max()
        brk = (close - h) / h
        r = close / close.shift(3) - 1.0
        vol_s = amt.rolling(3, min_periods=3).mean()
        vol_l = amt.rolling(10, min_periods=10).mean()
        vpc = r * (vol_s / vol_l.replace(0, np.nan))
        ma_a = close.rolling(3, min_periods=3).mean()
        ma_b = close.rolling(6, min_periods=6).mean()
        ma_c = close.rolling(12, min_periods=12).mean()
        maalg = (ma_a - ma_b) / ma_b + (ma_b - ma_c) / ma_c
        slope_window = int(slope_window or 30)
        names = ("MOM10", "SLOPE30", "BRK10", "VPC3", "MAALG")
    else:
        mom = close.shift(1) / close.shift(21) - 1.0
        h = close.shift(1).rolling(20, min_periods=20).max()
        brk = (close - h) / h
        r = close / close.shift(5) - 1.0
        vol_s = amt.rolling(5, min_periods=5).mean()
        vol_l = amt.rolling(20, min_periods=20).mean()
        vpc = r * (vol_s / vol_l.replace(0, np.nan))
        ma_a = close.rolling(5, min_periods=5).mean()
        ma_b = close.rolling(10, min_periods=10).mean()
        ma_c = close.rolling(20, min_periods=20).mean()
        maalg = (ma_a - ma_b) / ma_b + (ma_b - ma_c) / ma_c
        slope_window = int(slope_window or 60)
        names = ("MOM20", "SLOPE60", "BRK20", "VPC5", "MAALG")

    log_close = np.log(close.where(close > 0))
    slope_mat = np.full(log_close.shape, np.nan)
    r2_mat = np.full(log_close.shape, np.nan)
    values = log_close.to_numpy(dtype=float, copy=True)
    for j in range(values.shape[1]):
        col = values[:, j]
        if not np.isfinite(col).any():
            continue
        s, r2 = _slope_r2_1d(col, slope_window)
        slope_mat[:, j] = s
        r2_mat[:, j] = r2

    slope = pd.DataFrame(slope_mat, index=close.index, columns=close.columns)
    slope_r2 = pd.DataFrame(r2_mat, index=close.index, columns=close.columns)

    return {
        names[0]: mom,
        names[1]: slope,
        names[2]: brk,
        names[3]: vpc,
        names[4]: maalg,
        "slope_r2": slope_r2,
    }


def standardize_on_dates(
    raw: Dict[str, pd.DataFrame],
    mask: pd.DataFrame,
    dates: Sequence,
    *,
    r2_threshold: Optional[float],
    r2_min_pool: int,
    winsor_q: Tuple[float, float],
    factor_names=FACTOR_NAMES,
) -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame, pd.Series]:
    """仅在指定决策日做 R² 过滤 + winsorize + z-score。"""
    slope_r2 = raw["slope_r2"]
    codes = mask.columns
    use_dates = [d for d in dates if d in mask.index]
    z_out = {k: pd.DataFrame(np.nan, index=use_dates, columns=codes) for k in factor_names}
    pass_mask = pd.DataFrame(False, index=use_dates, columns=codes)
    thr_series = pd.Series(np.nan, index=use_dates, dtype=float)

    for dt in use_dates:
        m = mask.loc[dt].fillna(False).astype(bool)
        r2 = slope_r2.loc[dt]
        r2_ok_pool = r2[m].dropna()
        if r2_ok_pool.empty:
            continue

        thr = float(r2_ok_pool.median()) if r2_threshold is None else float(r2_threshold)
        passed = m & (r2 >= thr)
        if int(passed.sum()) < r2_min_pool:
            thr = float(r2_ok_pool.quantile(0.25))
            passed = m & (r2 >= thr)
        thr_series.loc[dt] = thr
        pass_mask.loc[dt] = passed

        idx = passed[passed].index
        if len(idx) < 5:
            continue
        for name in factor_names:
            s = raw[name].loc[dt, idx]
            w = winsorize_cross_section(s, winsor_q)
            z = zscore_cross_section(w)
            z_out[name].loc[dt, z.index] = z

    return z_out, pass_mask, thr_series


def compute_factor_bundle(
    close_hfq: pd.DataFrame,
    amount: pd.DataFrame,
    tradable_mask: pd.DataFrame,
    cfg: TrendStrategyConfig,
    week_ends: Optional[Iterable] = None,
) -> dict:
    pack = str(getattr(cfg, "factor_pack", "week") or "week")
    raw = compute_raw_factors(
        close_hfq,
        amount,
        slope_window=cfg.slope_window,
        factor_pack=pack,
    )
    mask = tradable_mask.reindex(index=close_hfq.index, columns=close_hfq.columns).fillna(False)
    dates = list(week_ends) if week_ends is not None else list(mask.index)
    z, pass_mask, thr = standardize_on_dates(
        raw,
        mask,
        dates,
        r2_threshold=cfg.r2_threshold,
        r2_min_pool=cfg.r2_min_pool,
        winsor_q=cfg.winsor_q,
        factor_names=cfg.factor_names,
    )
    return {
        "raw": raw,
        "z": z,
        "pass_mask": pass_mask,
        "r2_threshold_used": thr,
    }
