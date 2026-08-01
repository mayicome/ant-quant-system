#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""订单列表会话过滤：只保留当日（含夜市）委托，丢掉几天前的残留。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, time as dt_time
from typing import Any, Optional


def parse_order_datetime(raw: Any) -> Optional[datetime]:
    """尽量从 QMT/本地字段解析出带日期的委托时间。"""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, (int, float)):
        try:
            n = float(raw)
            if n <= 0:
                return None
            # 纯 HHMMSS（如 104225）没有日期
            if n < 1_000_000:
                return None
            # 毫秒 / 秒时间戳
            if n > 1e12:
                return datetime.fromtimestamp(n / 1000.0)
            if n > 1e9:
                return datetime.fromtimestamp(n)
            # YYYYMMDDHHMMSS
            s = str(int(n))
            if len(s) >= 14:
                return datetime.strptime(s[:14], "%Y%m%d%H%M%S")
            if len(s) == 8:
                return datetime.strptime(s, "%Y%m%d")
        except Exception:
            return None

    s = str(raw).strip()
    if not s or s in ("0", "None", "none"):
        return None
    try:
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00").split("+")[0])
        if " " in s and ":" in s:
            # 2026-07-14 10:42:25
            return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        digits = "".join(ch for ch in s if ch.isdigit())
        if len(digits) >= 14:
            return datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
        if len(digits) == 8:
            return datetime.strptime(digits, "%Y%m%d")
        # 仅 HH:MM:SS → 无日期
        if ":" in s and len(s) <= 8:
            return None
    except Exception:
        return None
    return None


def _session_anchor_date(now: Optional[datetime] = None) -> date:
    """当前应展示委托所属的「交易日」锚点。"""
    now = now or datetime.now()
    today = now.date()
    try:
        from utils.trading_day import is_tradeday, last_tradeday_on_or_before, is_after_reference_switch

        if is_tradeday(today):
            # 交易日 REFERENCE_SWITCH 后，夜市单归属下一交易日，但仍可能看到「今日已报」单
            return today
        # 非交易日：锚到最近已过交易日（便于周末看周五单时仍过滤掉更早残留）
        last_td = last_tradeday_on_or_before(today)
        return last_td or today
    except Exception:
        return today


def is_current_session_order(
    *,
    order_time: Any = None,
    at: Any = None,
    order_at: Any = None,
    updated_at: Any = None,
    now: Optional[datetime] = None,
) -> bool:
    """
    是否属于当前应展示的委托。

    规则：
    - 能解析出日期：须为「今日」；或「上一交易日 15:00 后」（夜市挂单窗口）
    - 解析不出日期（仅 HH:MM:SS）：放行（通常来自当日 query，日期已被剥掉）
    """
    now = now or datetime.now()
    today = now.date()

    dt = None
    for raw in (order_at, at, order_time, updated_at):
        dt = parse_order_datetime(raw)
        if dt is not None:
            break
    if dt is None:
        return True

    d = dt.date()
    if d == today:
        return True

    # 夜市：上一自然日 15:00 后
    if d == today - timedelta(days=1) and dt.time() >= dt_time(15, 0):
        return True

    # 跨周末：上一交易日 15:00 后 → 周末仍可显示该批夜市单
    try:
        from utils.trading_day import last_tradeday_on_or_before, is_tradeday

        anchor = _session_anchor_date(now)
        last_td = last_tradeday_on_or_before(today - timedelta(days=1))
        if last_td and d == last_td and dt.time() >= dt_time(15, 0):
            # 且尚未进入「再下一个」交易日盘中（简单：今天不是交易日，或今天开盘前）
            if not is_tradeday(today) or now.time() < dt_time(9, 15):
                return True
        # 锚点交易日当天的单
        if d == anchor:
            return True
    except Exception:
        pass

    # 超过 1 个自然日且不在夜市窗口 → 视为过期
    return False


def filter_order_records(records):
    """过滤 list[dict] 订单记录，保留当前会话。"""
    out = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        if is_current_session_order(
            order_time=rec.get("order_time"),
            at=rec.get("at"),
            order_at=rec.get("order_at"),
            updated_at=rec.get("updated_at"),
        ):
            out.append(rec)
    return out
