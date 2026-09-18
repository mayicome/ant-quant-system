# -*- coding: utf-8 -*-
"""核心数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class WatchItem:
    """第 2 层产出：进入观察池的一只股票（供 T+1 盘中使用）。"""

    code: str
    """6 位代码。"""
    as_of: date
    """识别日 T。"""
    prev_close: float
    """T 日不复权收盘价（买入回踩基准）。"""
    drawdown: float
    volume_ratio: float
    industry: str = ""
    """申万主行业；可空，第 3 层按配置拒绝。"""
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Position:
    """第 4 层持仓对象。"""

    stock_code: str
    entry_time: datetime
    entry_price: float
    volume: int
    entry_date: date
    hold_days_counter: int = 0
    max_profit: float = 0.0
    max_loss: float = 0.0
    industry: str = ""
    available_to_sell: bool = False
    """T+1：买入当日不可卖。"""
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderIntent:
    side: str
    """'buy' | 'sell'"""
    code: str
    volume: int
    reason: str
    created_at: datetime
    limit_price: Optional[float] = None
    """None 表示市价意图（撮合层再处理）。"""
    deferred: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FillRecord:
    side: str
    code: str
    volume: int
    price: float
    ts: datetime
    reason: str
    cost: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AccountState:
    cash: float
    positions: Dict[str, Position] = field(default_factory=dict)
    nav: float = 0.0

    def position_codes(self) -> List[str]:
        return list(self.positions.keys())


@dataclass
class DayContext:
    """单个交易日盘中上下文。"""

    trade_date: date
    watch_list: List[WatchItem]
    """昨日收盘第 2 层产出。"""
    buy_monitor_set: List[WatchItem] = field(default_factory=list)
    deferred_buys: List[OrderIntent] = field(default_factory=list)
    deferred_sells: List[OrderIntent] = field(default_factory=list)
