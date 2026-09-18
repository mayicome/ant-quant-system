# -*- coding: utf-8 -*-
"""复用趋势 FM；配置为 TechRevConfig（字段兼容）。"""
from __future__ import annotations

from typing import Dict, Sequence

import pandas as pd

from tech_rev_strategy.config import TechRevConfig
from trend_strategy.fm import (
    cross_section_ols,
    newey_west_tstat,
    premium_summary,
    run_fama_macbeth as _run_fm,
    week_returns_hfq,
)

__all__ = [
    "week_returns_hfq",
    "cross_section_ols",
    "run_fama_macbeth",
    "premium_summary",
    "newey_west_tstat",
    "fm_r2_mean",
]


def run_fama_macbeth(
    z_factors: Dict[str, pd.DataFrame],
    pass_mask: pd.DataFrame,
    week_ret: pd.DataFrame,
    week_ends: Sequence,
    cfg: TechRevConfig,
) -> dict:
    return _run_fm(z_factors, pass_mask, week_ret, week_ends, cfg)  # type: ignore[arg-type]


def fm_r2_mean(fm_r2: pd.DataFrame) -> float:
    if fm_r2 is None or fm_r2.empty or "r2" not in fm_r2.columns:
        return float("nan")
    return float(fm_r2["r2"].mean())
