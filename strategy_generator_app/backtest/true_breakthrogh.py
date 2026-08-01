# -*- coding: utf-8 -*-
"""
历史拼写兼容层（breakthrogh / evalute）。

旧版实盘代码可能从本模块导入；请优先使用 true_breakthrough.py。
"""
from strategy_generator_app.backtest.true_breakthrough import (  # noqa: F401
    compute_true_breakthrough_tick_metrics,
    evaluate_true_breakthrough_tick,
    evaluate_true_breakthrough_tick_with_detail,
    format_true_breakthrough_conditions_detail,
    infer_tick_vol_to_shares_multiplier,
    is_breakthrough_buy_price_cross_tick,
    max_cond1_breakthrough_volume_from_recent,
    per_tick_trade_volumes_list,
    round_price_like_display,
)

# 旧拼写别名
evalute_true_breakthrogh_tick_with_detail = evaluate_true_breakthrough_tick_with_detail
evalute_true_breakthrogh_tick = evaluate_true_breakthrough_tick

__all__ = [
    "compute_true_breakthrough_tick_metrics",
    "evaluate_true_breakthrough_tick",
    "evaluate_true_breakthrough_tick_with_detail",
    "evalute_true_breakthrogh_tick",
    "evalute_true_breakthrogh_tick_with_detail",
    "format_true_breakthrough_conditions_detail",
    "infer_tick_vol_to_shares_multiplier",
    "is_breakthrough_buy_price_cross_tick",
    "max_cond1_breakthrough_volume_from_recent",
    "per_tick_trade_volumes_list",
    "round_price_like_display",
]
