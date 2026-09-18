# -*- coding: utf-8 -*-
"""技术反转策略 v1.0（技术因子 FM，规格 docs/技术反转策略规格书 v1.0.md）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Tuple

# 半周压缩技术因子名（与趋势 half pack 一致）
FACTOR_NAMES: Tuple[str, ...] = ("MOM10", "SLOPE30", "BRK10", "VPC3", "MAALG")
# 默认取负的因子（实验组 A：BRK10 保留正向）
NEGATE_DEFAULT: Tuple[str, ...] = ("MOM10", "SLOPE30", "VPC3", "MAALG")
# 实验组 B：全部取负
NEGATE_ALL: Tuple[str, ...] = ("MOM10", "SLOPE30", "BRK10", "VPC3", "MAALG")


@dataclass
class TechRevConfig:
    """技术反转 v1.0 默认参数。"""

    # 池
    min_list_days: int = 120
    liquidity_window: int = 20
    min_avg_amount: float = 1e8
    exclude_bj: bool = True
    exclude_st: bool = True

    # 因子 / R²
    slope_window: int = 30
    r2_threshold: Optional[float] = None  # None → 截面中位数
    r2_min_pool: int = 800  # 规格：过滤后过小则放宽
    winsor_q: Tuple[float, float] = (0.01, 0.99)
    # A=混合（BRK 正向）；B=纯反转（BRK 也取负）
    exp_group: str = "A"
    negate_factors: Tuple[str, ...] = field(default_factory=lambda: NEGATE_DEFAULT)

    # FM
    estimate_window: int = 52
    drop_alpha_in_score: bool = True

    # 组合
    top_n: int = 20
    max_turnover: float = 0.30
    cost_roundtrip: float = 0.0015
    initial_cash: float = 1_000_000.0
    # 组合层约束（r2；r1 默认关闭）
    industry_cap_k: int = 0
    liquidity_pct: float = 0.0
    vol_filter: str = "none"  # none | atr | rv
    vol_drop_q: float = 0.0
    # 打分：fm | icir_w | equal_sign
    score_mode: str = "fm"
    icir_window: int = 52
    # ICIR 打分时：已取负暴露的理论符号全为 +1（暴露高→预期收益高）
    score_sign: dict = field(default_factory=dict)

    start_date: Optional[date] = None
    end_date: Optional[date] = None

    limit_eps: float = 0.011
    max_buy_defer_periods: int = 1
    max_sell_defer_priority: int = 1

    factor_names: Tuple[str, ...] = field(default_factory=lambda: FACTOR_NAMES)

    @property
    def cost_one_side(self) -> float:
        return self.cost_roundtrip / 2.0

    @property
    def periods_per_year(self) -> float:
        return 52.0

    @classmethod
    def for_group(cls, exp: str = "A", **kwargs) -> "TechRevConfig":
        exp = (exp or "A").strip().upper()
        neg = NEGATE_ALL if exp == "B" else NEGATE_DEFAULT
        return cls(exp_group=exp, negate_factors=tuple(neg), **kwargs)
