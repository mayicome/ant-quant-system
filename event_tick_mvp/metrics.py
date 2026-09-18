# -*- coding: utf-8 -*-
"""回测输出指标（规格清单）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from event_tick_mvp.types import FillRecord


@dataclass
class BacktestMetrics:
    n_trades: int = 0
    win_rate: Optional[float] = None
    payoff_ratio: Optional[float] = None
    """盈亏比：平均盈利 / 平均亏损绝对值。"""
    exit_take_profit_pct: Optional[float] = None
    exit_stop_loss_pct: Optional[float] = None
    exit_max_hold_pct: Optional[float] = None
    defer_buy_count: int = 0
    defer_sell_count: int = 0
    # 组合层（有 NAV 序列后再填）
    ann_return: Optional[float] = None
    max_dd: Optional[float] = None
    calmar: Optional[float] = None
    sharpe: Optional[float] = None
    ann_turnover: Optional[float] = None
    extra: Dict[str, float] = field(default_factory=dict)

    def passes_mvp_baseline(self, min_trades: int = 300) -> bool:
        return int(self.n_trades) > int(min_trades)


def compute_metrics(fills: Sequence[FillRecord]) -> BacktestMetrics:
    """由成交记录粗算单笔统计；配对买卖的完整实现后续补强。"""
    sells = [f for f in fills if f.side == "sell"]
    m = BacktestMetrics(n_trades=len(sells))
    if not sells:
        return m
    # 若 fill.meta 带 pnl / reason，可细化
    reasons = [str(f.reason or "") for f in sells]
    n = len(reasons)

    def _pct(key: str) -> float:
        return sum(1 for r in reasons if r == key) / n

    m.exit_take_profit_pct = _pct("take_profit")
    m.exit_stop_loss_pct = _pct("stop_loss")
    m.exit_max_hold_pct = _pct("max_hold")
    m.defer_buy_count = sum(
        1 for f in fills if f.side == "buy" and bool((f.meta or {}).get("deferred"))
    )
    m.defer_sell_count = sum(
        1 for f in fills if f.side == "sell" and bool((f.meta or {}).get("deferred"))
    )
    return m
