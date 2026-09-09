# -*- coding: utf-8 -*-
"""趋势策略（暴露×溢价 / Fama-MacBeth）。周频见 docs/trend_strategy_spec_v1.2.md；半周压缩用 TrendStrategyConfig.half_week()。"""

from trend_strategy.config import FACTOR_NAMES, FACTOR_NAMES_HALF, TrendStrategyConfig

__all__ = ["TrendStrategyConfig", "FACTOR_NAMES", "FACTOR_NAMES_HALF"]
