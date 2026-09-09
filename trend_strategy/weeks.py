# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, timedelta
from typing import List, Sequence


def week_end_dates(trade_days: Sequence[date]) -> List[date]:
    """每个自然周的最后一个交易日（周五休市则用当周末日）。"""
    if not trade_days:
        return []
    days = sorted(trade_days)
    out: List[date] = []
    # ISO week key: (year, week)
    buckets: dict[tuple, date] = {}
    for d in days:
        iso = d.isocalendar()
        key = (iso[0], iso[1])
        prev = buckets.get(key)
        if prev is None or d > prev:
            buckets[key] = d
    out = [buckets[k] for k in sorted(buckets.keys())]
    return out


def every_n_trade_days(
    trade_days: Sequence[date],
    n: int = 3,
    *,
    offset: int = 0,
) -> List[date]:
    """
    交易日等步长决策网格（半周：n=3）。
    offset：从第 offset 个交易日起步（0-based），再每 n 日取一点。
    """
    days = sorted(trade_days)
    step = max(1, int(n))
    off = max(0, int(offset))
    if off >= len(days):
        return []
    return list(days[off::step])


def decision_dates_for_config(
    trade_days: Sequence[date],
    *,
    rebalance_mode: str = "week",
    decision_step_days: int = 3,
) -> List[date]:
    mode = (rebalance_mode or "week").strip().lower()
    if mode == "half":
        return every_n_trade_days(trade_days, n=decision_step_days, offset=0)
    return week_end_dates(trade_days)


def next_trade_day(trade_days: Sequence[date], d: date) -> date | None:
    for x in trade_days:
        if x > d:
            return x
    return None


def prev_trade_day(trade_days: Sequence[date], d: date) -> date | None:
    prev = None
    for x in trade_days:
        if x >= d:
            break
        prev = x
    return prev


def trading_days_since(list_date: date | None, as_of: date, trade_days: Sequence[date]) -> int:
    """上市日至 as_of（含）之间的交易日数。list_date 未知时返回 0。"""
    if list_date is None:
        return 0
    n = 0
    for d in trade_days:
        if d < list_date:
            continue
        if d > as_of:
            break
        n += 1
    return n


def clip_range(
    trade_days: Sequence[date],
    start: date | None,
    end: date | None,
) -> List[date]:
    out = list(trade_days)
    if start:
        out = [d for d in out if d >= start]
    if end:
        out = [d for d in out if d <= end]
    return out
