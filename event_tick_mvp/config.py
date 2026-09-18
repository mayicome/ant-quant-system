# -*- coding: utf-8 -*-
"""MVP-v1 配置与参数扫描网格（与规格摘要对齐）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Tuple


@dataclass
class MvpConfig:
    """单次回测运行配置（一组参数点）。"""

    # ---- 第 2 层：超跌缩量 ----
    drawdown_n: int = 5
    """近 N 日高点窗口；扫描 [3,5,7]。"""
    drawdown_threshold: float = -0.08
    """N 日回撤上限（更负更苛刻）；扫描约 -0.12 ~ -0.06。"""
    volume_ratio_max: float = 0.5
    """当日成交额 / 过去 20 日日均成交额上限；扫描 0.3 ~ 0.7。"""
    volume_ma_days: int = 20
    cond_a_bearish: bool = False
    """附加：T 日收阴 close < open。"""
    cond_b_body_ratio: bool = False
    """附加：实体/振幅占比（见 body_ratio_min）。"""
    body_ratio_min: float = 0.5
    """cond_b：|close-open| / (high-low) ≥ 该值；振幅为 0 时视为不满足。"""
    cond_c_no_new_low: bool = False
    """附加：T 日最低价不低于近 N 日（T-N…T-1）最低价。"""

    # ---- 第 3 层：入场过滤 ----
    open_amt_window_min: int = 20
    """开盘后累计成交额窗口（分钟）；扫描 [20,40]。"""
    open_amt_min_yuan: float = 8_000_000.0
    """窗口累计成交额下限（元）。"""
    impact_amt_frac: float = 0.15
    """计划买入金额 ≤ 窗口累计成交额 × 该比例。"""
    limit_down_streak_min: int = 2
    """连续一字跌停天数阈值。"""
    post_limit_down_ban_days: int = 5
    """复牌后禁买交易日数（首个非一字跌停日为 Day0）。"""
    industry_cap: int = 4
    """单申万主行业最大持仓只数；扫描 [3,4,5]。"""

    # ---- 第 4 层：买入 / 仓位 ----
    buy_alpha: float = 0.02
    """回踩区间下沿：prev_close * (1 - alpha)。"""
    buy_beta: float = 0.03
    """回踩区间上沿：prev_close * (1 + beta)。"""
    max_weight: float = 0.08
    """单票最大权重占净值；扫描 0.05 ~ 0.10。"""
    defer_buy_max_days: int = 1
    """涨停买不进：最多递延完整交易日数。"""
    defer_sell_max_days: int = 1
    """跌停卖不出：最多递延完整交易日数。"""

    # ---- 第 4 层：出场（基线初值，可扫）----
    take_profit: float = 0.08
    stop_loss: float = 0.05
    max_hold_trading_days: int = 5

    # ---- 成本 ----
    stamp_tax_sell: float = 0.001
    """卖出印花税。"""
    round_trip_cost: float = 0.0025
    """双边含佣金滑点（敏感性：0.0018 / 0.0025）。"""

    # ---- 回测工程 ----
    bar_freq: str = "1m"
    """'tick' | '1m'；MVP 允许 1 分钟降级。"""
    initial_cash: float = 1_000_000.0
    list_min_trading_days: int = 120
    """上市不足该交易日数剔除。"""

    # 缺行业映射时：拒绝开仓（规格建议）
    reject_if_no_industry: bool = True

    max_buy_replay: int = 0
    """buy_monitor 盘中回放上限；0=全候选（基线复测）。>0 时按超跌深度截断。"""

    tag_board_rank_posthoc: bool = True
    """事后给成交打 T-1 东财板块名次标签（不改选股规则）。"""

    extra: Dict[str, Any] = field(default_factory=dict)


def default_scan_grids() -> Dict[str, Sequence[Any]]:
    """规格中的参数扫描范围（网格由调用方组合，避免笛卡尔爆炸）。"""
    return {
        "drawdown_n": [3, 5, 7],
        "drawdown_threshold": [-0.12, -0.10, -0.08, -0.06],
        "volume_ratio_max": [0.3, 0.5, 0.7],
        "addon_mode": [
            "none",
            "bearish",
            "no_new_low",
            "bearish+no_new_low",
        ],
        "open_amt_window_min": [20, 40],
        "industry_cap": [3, 4, 5],
        "max_weight": [0.05, 0.10],
        "round_trip_cost": [0.0018, 0.0025],
        "take_profit": [0.06, 0.08, 0.10],
        "stop_loss": [0.04, 0.05, 0.06],
        "max_hold_trading_days": [3, 5],
    }


def apply_addon_mode(cfg: MvpConfig, mode: str) -> MvpConfig:
    """附加条件开关预设。"""
    cfg.cond_a_bearish = False
    cfg.cond_b_body_ratio = False
    cfg.cond_c_no_new_low = False
    m = (mode or "none").strip().lower()
    if m in ("none", "", "baseline"):
        return cfg
    if "bearish" in m:
        cfg.cond_a_bearish = True
    if "body" in m:
        cfg.cond_b_body_ratio = True
    if "no_new_low" in m or "nonewlow" in m:
        cfg.cond_c_no_new_low = True
    return cfg
