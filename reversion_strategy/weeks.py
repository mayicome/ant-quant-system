# -*- coding: utf-8 -*-
"""周历与交易日工具 — 与趋势策略同构，本地副本避免交叉依赖。"""
from __future__ import annotations

from datetime import date
from typing import List, Sequence


def week_end_dates(trade_days: Sequence[date]) -> List[date]:
    """每个自然周的最后一个交易日（周五休市则用当周末日）。"""
    if not trade_days:
        return []
    days = sorted(trade_days)
    buckets: dict[tuple, date] = {}
    for d in days:
        iso = d.isocalendar()
        key = (iso[0], iso[1])
        prev = buckets.get(key)
        if prev is None or d > prev:
            buckets[key] = d
    return [buckets[k] for k in sorted(buckets.keys())]


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
