# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from trend_strategy.config import TrendStrategyConfig
from utils.limit_ratio import is_st_stock


def build_tradable_mask(
    close_hfq: pd.DataFrame,
    amount: pd.DataFrame,
    close_raw: pd.DataFrame,
    volume_raw: pd.DataFrame,
    meta: pd.DataFrame,
    trade_days: list,
    cfg: TrendStrategyConfig,
) -> pd.DataFrame:
    """硬过滤逐日布尔面板。"""
    codes = list(close_hfq.columns)
    idx = pd.Index(close_hfq.index)
    mask = close_hfq.notna() & (close_hfq > 0)

    name_map = dict(zip(meta["code"].astype(str), meta["name"].astype(str)))
    list_map = {str(c): ld for c, ld in zip(meta["code"], meta["list_date"])}

    if cfg.exclude_st:
        st_cols = [c for c in codes if is_st_stock(name_map.get(c, ""))]
        if st_cols:
            mask.loc[:, st_cols] = False

    vol = volume_raw.reindex(index=idx, columns=codes) if volume_raw is not None else None
    cr = close_raw.reindex(index=idx, columns=codes) if close_raw is not None else None
    if vol is not None:
        suspended = vol.fillna(0) <= 0
        if cr is not None:
            suspended = suspended & cr.eq(cr.shift(1)).fillna(False)
        mask &= ~suspended.fillna(False)

    amt = amount.reindex(index=idx, columns=codes)
    mask &= amt.fillna(0) > 0

    avg_amt = amt.rolling(cfg.liquidity_window, min_periods=cfg.liquidity_window).mean()
    mask &= avg_amt >= cfg.min_avg_amount

    # 上市交易日数：trade_days 位置差
    td = list(trade_days)
    pos = {d: i for i, d in enumerate(td)}
    pos_arr = np.array([pos.get(d, -1) for d in idx], dtype=float)
    list_age = pd.DataFrame(np.nan, index=idx, columns=codes)
    for c in codes:
        ld = list_map.get(c)
        if ld is None or (isinstance(ld, float) and np.isnan(ld)):
            s = close_hfq[c].dropna()
            ld = s.index[0] if len(s) else None
        if ld is None:
            list_age[c] = np.nan
            continue
        first_i = None
        for d in td:
            if d >= ld:
                first_i = pos[d]
                break
        if first_i is None:
            list_age[c] = np.nan
            continue
        age = pos_arr - float(first_i) + 1.0
        age[pos_arr < 0] = np.nan
        list_age[c] = age

    mask &= list_age >= float(cfg.min_list_days)
    return mask.fillna(False)
