# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict, Sequence

import numpy as np
import pandas as pd
from scipy import stats


def spearman_ic_series(
    factor: pd.DataFrame,
    forward_ret: pd.DataFrame,
    mask: pd.DataFrame | None = None,
) -> pd.Series:
    """每期因子 vs 下期收益 Spearman IC；forward_ret.index 对齐因子日期。"""
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


def ic_summary(ic: pd.Series) -> dict:
    ic = ic.dropna()
    if ic.empty:
        return {"ic_mean": np.nan, "ic_std": np.nan, "icir": np.nan, "n": 0}
    mu = float(ic.mean())
    sd = float(ic.std(ddof=1)) if len(ic) > 1 else np.nan
    return {
        "ic_mean": mu,
        "ic_std": sd,
        "icir": (mu / sd) if sd and abs(sd) > 1e-12 else np.nan,
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
        s = ic_summary(ic)
        s["factor"] = name
        rows.append(s)
    return pd.DataFrame(rows)[["factor", "ic_mean", "ic_std", "icir", "n"]]


def prediction_deciles(
    score: pd.DataFrame,
    week_ret: pd.DataFrame,
    pass_mask: pd.DataFrame,
    n_groups: int = 10,
) -> pd.DataFrame:
    """按预测分 n 组，返回各组平均下期收益。"""
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


def factor_corr_and_vif(
    z_factors: Dict[str, pd.DataFrame],
    pass_mask: pd.DataFrame,
    sample_dates: Sequence | None = None,
) -> dict:
    """抽样日期上合并截面，算相关矩阵与 VIF。"""
    names = list(z_factors.keys())
    frames = []
    dates = list(sample_dates) if sample_dates is not None else list(pass_mask.index)
    # 最多取 26 期减轻内存
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
    for i, name in enumerate(names):
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
