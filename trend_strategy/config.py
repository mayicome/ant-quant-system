# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Tuple


# v1.2 周频因子名
FACTOR_NAMES: Tuple[str, ...] = ("MOM20", "SLOPE60", "BRK20", "VPC5", "MAALG")
# 半周压缩因子名
FACTOR_NAMES_HALF: Tuple[str, ...] = ("MOM10", "SLOPE30", "BRK10", "VPC3", "MAALG")


@dataclass
class TrendStrategyConfig:
    """趋势策略参数。rebalance_mode=week 为 v1.2；half 为每 3 交易日压缩版。"""

    # 池与过滤
    min_list_days: int = 120
    liquidity_window: int = 20
    min_avg_amount: float = 1e8  # 元
    exclude_bj: bool = True
    exclude_st: bool = True

    # 调仓网格：week=ISO 周末日；half=每 decision_step_days 个交易日
    rebalance_mode: str = "week"  # week | half
    decision_step_days: int = 3

    # 因子 / R²
    factor_pack: str = "week"  # week | half — 决定因子窗口与命名
    slope_window: int = 60
    r2_threshold: Optional[float] = None  # None → 用当期截面中位数
    r2_min_pool: int = 100  # 过滤后过少则放宽到分位数更低
    winsor_q: Tuple[float, float] = (0.01, 0.99)

    # FM（按调仓期数计，非自然周）
    estimate_window: int = 52
    drop_alpha_in_score: bool = True

    # 组合
    top_n: int = 20
    max_turnover: float = 0.30
    cost_roundtrip: float = 0.0015  # 双边打包
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

    @property
    def periods_per_year(self) -> float:
        """用于年化：周频≈52；每 3 交易日≈252/3。"""
        mode = (self.rebalance_mode or "week").strip().lower()
        if mode == "half":
            return 252.0 / max(1, int(self.decision_step_days or 3))
        return 52.0

    @classmethod
    def half_week(cls, **kwargs) -> "TrendStrategyConfig":
        """适度压缩默认：每 3 交易日 + 短窗口因子 + W=40 + 换手 45% + 成本 0.20%。"""
        defaults = dict(
            rebalance_mode="half",
            decision_step_days=3,
            factor_pack="half",
            factor_names=FACTOR_NAMES_HALF,
            slope_window=30,
            estimate_window=40,
            max_turnover=0.45,
            cost_roundtrip=0.0020,
            r2_threshold=0.30,  # 短周期噪声大，固定偏低阈值；可再扫 0.3–0.6
        )
        defaults.update(kwargs)
        return cls(**defaults)
