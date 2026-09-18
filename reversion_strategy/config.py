# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Tuple


FACTOR_NAMES: Tuple[str, ...] = ("REV5", "RSI14", "BIAS20", "VSHRK", "VDRAIN")
# 超跌主因子（验收主力）；企稳确认为辅助
MAIN_FACTORS: Tuple[str, ...] = ("REV5", "RSI14", "BIAS20")
CONFIRM_FACTORS: Tuple[str, ...] = ("VSHRK", "VDRAIN")
# 理论方向：超跌/企稳确认均为负向（低值 → 高预期收益）
FACTOR_THEORY_SIGN: dict = {k: -1.0 for k in FACTOR_NAMES}


@dataclass
class ReversionStrategyConfig:
    """回归策略规格 v1.1 默认参数。"""

    # 池与过滤
    min_list_days: int = 120
    liquidity_window: int = 20
    min_avg_amount: float = 1e8  # 元
    exclude_bj: bool = True
    exclude_st: bool = True
    # 连续跌停过滤：过去 N 日收盘封死跌停 ≥ M 次
    cont_limit_down_window: int = 5
    cont_limit_down_min: int = 2

    # 因子
    winsor_q: Tuple[float, float] = (0.01, 0.99)
    rsi_period: int = 14
    atr_short: int = 10
    atr_long: int = 20

    # FM
    estimate_window: int = 52  # 决策期数（hold_weeks>1 时每个决策期跨 hold_weeks 个自然周）
    # 决策溢价只用下标 ≤ 决策序 − premium_lag（B 口径下默认 2 个决策期）
    premium_lag: int = 2
    drop_alpha_in_score: bool = True
    # 持有/调仓周期（自然周个数）；1=每周；2/3=隔周/每三周调仓，FM 左侧同步拉长
    hold_weeks: int = 1
    # 选股得分：fm=Σβ·f̄；theory_sign=Σ(−z)；icir_w=滚动|ICIR|加权的理论方向
    score_mode: str = "fm"
    # 避开预测最极端的前 skip_top_k 名，再取 top_n（缓解接刀）
    skip_top_k: int = 0

    # 行业：none | z（SW1 内去均值再 z）| res（FM 前行业残差化）
    industry_mode: str = "none"
    # 选股每 SW1 最多 k 只；0=不限制
    industry_cap_k: int = 0

    # 大盘过滤：off | bear_skip | bull_only | no_kill | no_kill_bear | csi_ma
    # 决策日 T 判定；禁新买、不强制清仓
    regime_mode: str = "off"
    regime_ma_window: int = 60

    # 流动性：绝对额仍用 min_avg_amount；若 liquidity_pct>0 则再要求截面均额分位 ≥ 该值
    liquidity_pct: float = 0.0
    # 波动剔除：none | atr | rv；丢掉 ATR%/RV 最高的 vol_drop_q 分位
    vol_filter: str = "none"
    vol_drop_q: float = 0.0

    # 组合
    top_n: int = 20
    max_turnover: float = 0.30
    cost_roundtrip: float = 0.0015
    initial_cash: float = 1_000_000.0

    # 止损（后复权摊薄成本）
    stop_loss: float = -0.08
    enable_stop_loss: bool = True

    # 回测区间
    start_date: Optional[date] = None
    end_date: Optional[date] = None

    # 执行：开盘 vs 涨跌停价容差（规格 0.005）
    limit_eps: float = 0.005
    max_buy_defer_periods: int = 1
    max_sell_defer_priority: int = 1

    factor_names: Tuple[str, ...] = field(default_factory=lambda: FACTOR_NAMES)

    @property
    def cost_one_side(self) -> float:
        return self.cost_roundtrip / 2.0
