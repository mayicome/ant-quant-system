# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from reversion_strategy.config import ReversionStrategyConfig
from utils.limit_ratio import ST_MAIN_BOARD_LIMIT_CHANGE_DATE, get_limit_ratio, is_main_board, is_st_stock


def close_sealed_limit_down_panel(
    close_raw: pd.DataFrame,
    name_map: Dict[str, str],
    eps: float,
) -> pd.DataFrame:
    """逐日「收盘封死跌停」布尔面板（向量化）。"""
    codes = list(close_raw.columns)
    pre = close_raw.shift(1)
    sealed = pd.DataFrame(False, index=close_raw.index, columns=codes)

    # 按板块静态涨跌幅分组（非 ST）
    groups: Dict[float, list] = {}
    st_codes = []
    for c in codes:
        name = name_map.get(c, "")
        if is_st_stock(name) and is_main_board(c):
            st_codes.append(c)
            continue
        # 用任意日取板块比例（非 ST 与日期无关）
        r = get_limit_ratio(c, name, None)
        groups.setdefault(r, []).append(c)

    for ratio, cols in groups.items():
        ld = (pre[cols] * (1.0 - ratio)).round(2)
        sealed[cols] = (close_raw[cols] - ld).abs() <= eps

    # 主板 ST：2026-07-06 前后比例不同
    if st_codes:
        idx = close_raw.index
        before = pd.Index([d for d in idx if d < ST_MAIN_BOARD_LIMIT_CHANGE_DATE])
        after = pd.Index([d for d in idx if d >= ST_MAIN_BOARD_LIMIT_CHANGE_DATE])
        for ratio, days in ((0.05, before), (0.10, after)):
            if len(days) == 0:
                continue
            ld = (pre.loc[days, st_codes] * (1.0 - ratio)).round(2)
            sealed.loc[days, st_codes] = (close_raw.loc[days, st_codes] - ld).abs() <= eps

    # 无效昨收不算
    sealed = sealed & pre.notna() & (pre > 0) & close_raw.notna()
    return sealed.fillna(False)


def build_tradable_mask(
    close_hfq: pd.DataFrame,
    amount: pd.DataFrame,
    close_raw: pd.DataFrame,
    volume_raw: pd.DataFrame,
    meta: pd.DataFrame,
    trade_days: list,
    cfg: ReversionStrategyConfig,
    name_map: Dict[str, str] | None = None,
) -> pd.DataFrame:
    """硬过滤逐日布尔面板（含连续跌停过滤）。"""
    codes = list(close_hfq.columns)
    idx = pd.Index(close_hfq.index)
    mask = close_hfq.notna() & (close_hfq > 0)

    if name_map is None:
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
            continue
        first_i = None
        for d in td:
            if d >= ld:
                first_i = pos[d]
                break
        if first_i is None:
            continue
        age = pos_arr - float(first_i) + 1.0
        age[pos_arr < 0] = np.nan
        list_age[c] = age
    mask &= list_age >= float(cfg.min_list_days)

    if cr is not None and cfg.cont_limit_down_min > 0:
        sealed = close_sealed_limit_down_panel(cr, name_map, cfg.limit_eps)
        cnt = sealed.rolling(cfg.cont_limit_down_window, min_periods=1).sum()
        mask &= ~(cnt >= float(cfg.cont_limit_down_min))

    return mask.fillna(False)


def apply_liquidity_pct_and_vol_filters(
    mask: pd.DataFrame,
    close_hfq: pd.DataFrame,
    amount: pd.DataFrame,
    cfg: ReversionStrategyConfig,
    *,
    high_hfq: pd.DataFrame | None = None,
    low_hfq: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    硬过滤之后追加：
    - liquidity_pct>0：可交易集合内 20d 均额截面分位 ≥ p
    - vol_filter=atr|rv：剔除波动最高 vol_drop_q 分位
    """
    out = mask.copy()
    codes = list(out.columns)
    idx = out.index
    amt = amount.reindex(index=idx, columns=codes)
    avg_amt = amt.rolling(cfg.liquidity_window, min_periods=cfg.liquidity_window).mean()

    pct = float(getattr(cfg, "liquidity_pct", 0.0) or 0.0)
    if pct > 0:
        rank = avg_amt.where(mask).rank(axis=1, pct=True, method="average")
        out &= rank.isna() | (rank >= pct)

    vol_mode = str(getattr(cfg, "vol_filter", "none") or "none").strip().lower()
    q = float(getattr(cfg, "vol_drop_q", 0.0) or 0.0)
    if vol_mode in ("atr", "rv") and q > 0:
        cl = close_hfq.reindex(index=idx, columns=codes)
        if vol_mode == "rv":
            vol = cl.pct_change(fill_method=None).rolling(20, min_periods=10).std()
        else:
            hi = high_hfq.reindex(index=idx, columns=codes) if high_hfq is not None else cl
            lo = low_hfq.reindex(index=idx, columns=codes) if low_hfq is not None else cl
            prev = cl.shift(1)
            tr = pd.DataFrame(
                np.maximum.reduce(
                    [
                        (hi - lo).abs().to_numpy(dtype=float),
                        (hi - prev).abs().fillna(0).to_numpy(dtype=float),
                        (lo - prev).abs().fillna(0).to_numpy(dtype=float),
                    ]
                ),
                index=idx,
                columns=codes,
            )
            atr = tr.rolling(20, min_periods=10).mean()
            vol = atr / cl.replace(0, np.nan)

        vol_masked = vol.where(out)
        vrank = vol_masked.rank(axis=1, pct=True, method="average")
        thr = 1.0 - q
        out &= ~(vrank.notna() & (vrank > thr))

    return out.fillna(False)
