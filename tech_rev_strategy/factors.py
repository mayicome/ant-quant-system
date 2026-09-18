# -*- coding: utf-8 -*-
"""技术反转因子：半周窗口技术因子 + R² 过滤 + z 后按实验组取负。"""
from __future__ import annotations

from typing import Dict, Iterable, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from tech_rev_strategy.config import FACTOR_NAMES, TechRevConfig
from trend_strategy.factors import (
    compute_raw_factors as _trend_raw,
    winsorize_cross_section,
    zscore_cross_section,
)


def compute_raw_factors(
    close_hfq: pd.DataFrame,
    amount: pd.DataFrame,
    *,
    slope_window: int = 30,
) -> Dict[str, pd.DataFrame]:
    return _trend_raw(
        close_hfq,
        amount,
        slope_window=slope_window,
        factor_pack="half",
    )


def standardize_on_dates(
    raw: Dict[str, pd.DataFrame],
    mask: pd.DataFrame,
    dates: Sequence,
    *,
    r2_threshold: Optional[float],
    r2_min_pool: int,
    winsor_q: Tuple[float, float],
    factor_names=FACTOR_NAMES,
    negate_factors: Sequence[str] = (),
) -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame, pd.Series]:
    """
    R² 过滤 → winsorize → z-score → 对指定因子取负（规格 §2.3）。
    返回的 z 即为输入 FM 的暴露 β。
    """
    slope_r2 = raw["slope_r2"]
    codes = mask.columns
    use_dates = [d for d in dates if d in mask.index]
    z_out = {k: pd.DataFrame(np.nan, index=use_dates, columns=codes) for k in factor_names}
    pass_mask = pd.DataFrame(False, index=use_dates, columns=codes)
    thr_series = pd.Series(np.nan, index=use_dates, dtype=float)
    neg_set = {str(x) for x in negate_factors}

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
            if name in neg_set:
                z = -z
            z_out[name].loc[dt, z.index] = z

    return z_out, pass_mask, thr_series


def compute_factor_bundle(
    close_hfq: pd.DataFrame,
    amount: pd.DataFrame,
    tradable_mask: pd.DataFrame,
    cfg: TechRevConfig,
    week_ends: Optional[Iterable] = None,
) -> dict:
    raw = compute_raw_factors(close_hfq, amount, slope_window=cfg.slope_window)
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
        negate_factors=cfg.negate_factors,
    )
    return {"raw": raw, "z": z, "pass_mask": pass_mask, "r2_threshold_used": thr}
