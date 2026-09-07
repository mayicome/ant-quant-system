# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Tuple


FACTOR_NAMES: Tuple[str, ...] = ("MOM20", "SLOPE60", "BRK20", "VPC5", "MAALG")


@dataclass
class TrendStrategyConfig:
    """v1.2 默认参数。"""

    # 池与过滤
    min_list_days: int = 120
    liquidity_window: int = 20
    min_avg_amount: float = 1e8  # 元
    exclude_bj: bool = True
    exclude_st: bool = True

    # 因子 / R²
    slope_window: int = 60
    r2_threshold: Optional[float] = None  # None → 用当期截面中位数
    r2_min_pool: int = 100  # 过滤后过少则放宽到分位数更低
    winsor_q: Tuple[float, float] = (0.01, 0.99)

    # FM
    estimate_window: int = 52  # 周
    drop_alpha_in_score: bool = True

    # 组合
    top_n: int = 20
    max_turnover: float = 0.30
    cost_roundtrip: float = 0.0015  # 双边打包；实现拆成单边 0.075%
    initial_cash: float = 1_000_000.0

    # 回测区间（可选）
    start_date: Optional[date] = None
    end_date: Optional[date] = None

    # 执行
    limit_eps: float = 0.011
    max_buy_defer_periods: int = 1
    max_sell_defer_priority: int = 1

    factor_names: Tuple[str, ...] = field(default_factory=lambda: FACTOR_NAMES)

    @property
    def cost_one_side(self) -> float:
        return self.cost_roundtrip / 2.0
