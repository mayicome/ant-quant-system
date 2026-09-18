# -*- coding: utf-8 -*-
"""ICIR / 等权符号打分（规格 §10.1：抛弃 FM 溢价，固定方向合成）。"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
from scipy import stats

from tech_rev_strategy.config import TechRevConfig


def _period_ic(factor_row: pd.Series, ret_row: pd.Series, passed: pd.Series) -> float:
    cols = passed[passed.fillna(False)].index
    if len(cols) < 20:
        return float("nan")
    df = pd.concat(
        [factor_row.reindex(cols).astype(float).rename("f"), ret_row.reindex(cols).astype(float).rename("r")],
        axis=1,
    ).dropna()
    if len(df) < 20:
        return float("nan")
    ic, _ = stats.spearmanr(df["f"], df["r"])
    return float(ic)


def build_icir_scores(
    z_factors: Dict[str, pd.DataFrame],
    pass_mask: pd.DataFrame,
    week_ret: pd.DataFrame,
    week_ends: Sequence,
    cfg: TechRevConfig,
) -> dict:
    """
    每期：用 ≤ T-1 已实现 IC 窗口估 ICIR，权重 w=ICIR/Σ|ICIR|（可负），
    score = Σ w_k · β_k。β 已是取负后暴露，期望 ICIR>0。
    """
    names = list(cfg.factor_names)
    we = list(week_ends)
    we_pos = {d: i for i, d in enumerate(we)}
    mode = str(getattr(cfg, "score_mode", "icir_w") or "icir_w").strip().lower()
    win = int(getattr(cfg, "icir_window", cfg.estimate_window) or 52)

    ic_hist: Dict[str, List[float]] = {k: [] for k in names}
    ic_dates: List = []
    score = pd.DataFrame(np.nan, index=we, columns=z_factors[names[0]].columns)
    weight_rows = []

    for T in we:
        # 记录本期 IC（需下期收益；供后续决策使用）
        if T in week_ret.index and T in pass_mask.index:
            passed = pass_mask.loc[T]
            for k in names:
                ic_hist[k].append(_period_ic(z_factors[k].loc[T], week_ret.loc[T], passed))
            ic_dates.append(T)

        i = we_pos[T]
        if i < 1:
            continue
        if T not in pass_mask.index:
            continue
        passed = pass_mask.loc[T].fillna(False)
        cols = passed[passed].index
        if len(cols) == 0:
            continue

        s = pd.Series(0.0, index=cols, dtype=float)
        if mode == "equal_sign":
            # 等权：暴露已取负，直接等权相加
            for k in names:
                s = s + z_factors[k].loc[T, cols].astype(float)
            score.loc[T, cols] = s
            continue

        # icir_w：仅用决策前已实现 IC（ic_dates < T，即不含当期）
        usable = [j for j, d in enumerate(ic_dates) if d < T]
        if len(usable) < max(4, min(12, win // 4)):
            continue
        use = usable[-win:]
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
            weights[k] = icir
        wsum = sum(abs(v) for v in weights.values())
        if wsum < 1e-12:
            continue
        w_norm = {k: weights[k] / wsum for k in names}
        weight_rows.append({"date": T, **w_norm})
        for k in names:
            s = s + z_factors[k].loc[T, cols].astype(float) * float(w_norm[k])
        score.loc[T, cols] = s

    return {
        "score": score,
        "weights": pd.DataFrame(weight_rows),
        "ic_dates": ic_dates,
        "hist_dates": ic_dates,  # 与 FM 对齐：回测起点用足够 IC 历史
    }
