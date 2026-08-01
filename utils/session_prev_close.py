#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""会话基准昨收：盘中用 QMT lastClose；REFERENCE_SWITCH 后切到「今日收盘」作为次日昨收。"""

from __future__ import annotations

from datetime import datetime, time as dt_time
from typing import Dict, Optional, Tuple

from utils.trading_day import REFERENCE_SWITCH_TIME, is_after_reference_switch, is_tradeday

# stock_code -> (as_of_date, next_basis: bool, price)
_cache: Dict[str, Tuple[object, bool, float]] = {}


def _now(now: Optional[datetime] = None) -> datetime:
    return now if now is not None else datetime.now()


def _fetch_prev_close_from_calculator(stock_code: str) -> float:
    try:
        from key_price_calculator import KeyPriceCalculator

        key_points = KeyPriceCalculator().calculate_key_points(stock_code)
        if not key_points:
            return 0.0
        for item in key_points:
            if isinstance(item, dict) and item.get("name") == "昨收盘":
                p = float(item.get("price") or 0)
                return p if p > 0 else 0.0
    except Exception:
        return 0.0
    return 0.0


def clear_session_prev_close_cache(stock_code: Optional[str] = None) -> None:
    if stock_code is None:
        _cache.clear()
    else:
        _cache.pop(stock_code, None)


def resolve_session_prev_close(
    stock_code: str,
    *,
    qmt_last_close: float = 0.0,
    last_price: float = 0.0,
    now: Optional[datetime] = None,
) -> float:
    """
    返回当前布局应对齐的「昨收」：
    - 交易日 09:30～REFERENCE_SWITCH 前：QMT lastClose（上一交易日收盘）
    - 交易日 REFERENCE_SWITCH 后 / 非交易日 / 交易日 09:30 前：
      优先 key_price_calculator（已含时间切日逻辑），其次 last_price，最后才退回 qmt_last_close
    """
    code = (stock_code or "").strip()
    if not code:
        return float(qmt_last_close or 0) or float(last_price or 0)

    now = _now(now)
    today = now.date()
    t = now.time()
    next_basis = is_after_reference_switch(now)
    qmt_pc = float(qmt_last_close or 0)
    last_px = float(last_price or 0)

    # 盘中：QMT lastClose 即为昨收
    if is_tradeday(today) and (not next_basis) and t >= dt_time(9, 30):
        return qmt_pc if qmt_pc > 0 else last_px

    cached = _cache.get(code)
    if cached is not None:
        cached_date, cached_next, cached_px = cached
        if cached_date == today and cached_next == next_basis and cached_px > 0:
            # 盘后缓存若仍像未切日昨收，且有更新的 last_price，则刷新
            if (
                next_basis
                and last_px > 0
                and abs(last_px - cached_px) > 1e-9
                and (qmt_pc <= 0 or abs(cached_px - qmt_pc) < 1e-9)
            ):
                _cache[code] = (today, next_basis, float(last_px))
                return float(last_px)
            return float(cached_px)

    price = _fetch_prev_close_from_calculator(code)

    # 盘后：日线可能尚未含今日收盘时，计算器仍可能给出「上一交易日」；
    # 若结果与 QMT lastClose 一致而 last_price 不同，则更信最新价≈今日收盘。
    if next_basis and last_px > 0:
        if price <= 0:
            price = last_px
        elif qmt_pc > 0 and abs(price - qmt_pc) < 1e-9 and abs(last_px - price) > 1e-9:
            price = last_px

    # 早盘/非交易：计算器失败时尽量用 QMT（早盘 lastClose 偶发等于现价，宁可保留也不要 0）
    if price <= 0 and qmt_pc > 0:
        price = qmt_pc

    if price <= 0 and last_px > 0:
        price = last_px

    if price > 0:
        _cache[code] = (today, next_basis, float(price))
    return float(price or 0)


def warm_session_prev_close_cache(stock_codes, now: Optional[datetime] = None) -> int:
    """预热缓存（早盘 9:30 前，或盘后 REFERENCE_SWITCH 后）。返回成功条数。"""
    now = _now(now)
    today = now.date()
    t = now.time()
    next_basis = is_after_reference_switch(now)

    # 盘中不需要预热（直接用 QMT lastClose）
    if is_tradeday(today) and (not next_basis) and t >= dt_time(9, 30):
        return 0

    ok = 0
    for stock_code in stock_codes or []:
        code = str(stock_code or "").strip()
        if not code:
            continue
        px = resolve_session_prev_close(code, qmt_last_close=0.0, last_price=0.0, now=now)
        if px > 0:
            ok += 1
    return ok
