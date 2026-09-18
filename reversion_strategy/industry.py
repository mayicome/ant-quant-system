# -*- coding: utf-8 -*-
"""申万一级（SW1）行业映射与截面处理。"""
from __future__ import annotations

from typing import Dict, List, Sequence

import numpy as np
import pandas as pd

from utils.qmt_sector_store import get_qmt_sector_store, split_sector_tags


def load_sw1_map(codes: Sequence[str]) -> Dict[str, str]:
    """code6 → SW1 标签；无标签为 UNK。"""
    store = get_qmt_sector_store()
    out: Dict[str, str] = {}
    for c in codes:
        code = str(c).zfill(6)
        tags = split_sector_tags(store.sectors_for_stock(code))
        inds = tags.get("industries") or []
        out[code] = str(inds[0]) if inds else "UNK"
    return out


def demean_by_industry(s: pd.Series, ind_map: Dict[str, str]) -> pd.Series:
    ind = pd.Series({c: ind_map.get(str(c).zfill(6), "UNK") for c in s.index}, index=s.index)
    return s - s.groupby(ind).transform("mean")


def industry_residualize_frame(
    y: pd.Series,
    X: pd.DataFrame,
    ind_map: Dict[str, str],
) -> tuple[pd.Series, pd.DataFrame]:
    """对 y 与各列按行业去均值（等价于行业哑变量残差，不含截距共线问题）。"""
    cols = [c for c in y.index if c in X.index]
    if not cols:
        return y, X
    y2 = demean_by_industry(y.reindex(cols), ind_map)
    X2 = pd.DataFrame({k: demean_by_industry(X[k].reindex(cols), ind_map) for k in X.columns})
    return y2, X2


def select_top_n_with_industry_cap(
    score_row: pd.Series,
    n: int,
    ind_map: Dict[str, str],
    *,
    skip_top_k: int = 0,
    cap_k: int = 0,
) -> List[str]:
    s = score_row.dropna().sort_values(ascending=False)
    k = max(0, int(skip_top_k))
    ranked = list(s.index[k:])
    if cap_k <= 0:
        return ranked[:n]
    counts: Dict[str, int] = {}
    out: List[str] = []
    for code in ranked:
        ind = ind_map.get(str(code).zfill(6), "UNK")
        if counts.get(ind, 0) >= cap_k:
            continue
        out.append(code)
        counts[ind] = counts.get(ind, 0) + 1
        if len(out) >= n:
            break
    return out


def portfolio_industry_stats(
    holdings: Sequence[str],
    ind_map: Dict[str, str],
    weights: Dict[str, float] | None = None,
) -> dict:
    if not holdings:
        return {"n_ind": 0, "max_w": 0.0, "hhi": 0.0}
    if weights is None:
        w = {c: 1.0 / len(holdings) for c in holdings}
    else:
        w = weights
    by: Dict[str, float] = {}
    for c in holdings:
        ind = ind_map.get(str(c).zfill(6), "UNK")
        by[ind] = by.get(ind, 0.0) + float(w.get(c, 0.0))
    vals = list(by.values())
    return {
        "n_ind": len(by),
        "max_w": float(max(vals)) if vals else 0.0,
        "hhi": float(sum(v * v for v in vals)),
    }
