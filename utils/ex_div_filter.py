# -*- coding: utf-8 -*-
"""疑似除权/除息检测（基于不复权 OHLC，供选股过滤与实盘对齐）。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional, Union

DateLike = Union[date, datetime, str, None]

DEFAULT_EX_DIV_LOOKBACK_DAYS = 20
# 涨跌幅超过该阈值且非涨跌停 → 疑似除权
_EX_DIV_RET_THRESHOLD = 0.05


def _as_date(d: DateLike) -> Optional[date]:
    if d is None or d == "":
        return None
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    try:
        return date.fromisoformat(str(d).strip()[:10])
    except ValueError:
        return None


def _limit_ret(code6: str) -> float:
    c = str(code6 or "").strip().zfill(6)
    if c.startswith(("300", "301", "688", "689")):
        return 0.195
    return 0.095


def suspected_ex_div_bar(
    prev_close: float,
    open_px: float,
    close_px: float,
    *,
    code6: str = "",
) -> bool:
    """相邻两交易日：相对前收的跳空是否像除权/除息（非涨跌停）。"""
    try:
        pc = float(prev_close)
        cl = float(close_px)
        op = float(open_px or 0)
    except (TypeError, ValueError):
        return False
    if pc <= 0 or cl <= 0:
        return False
    ret = cl / pc - 1.0
    lim = _limit_ret(code6)
    if abs(ret) >= lim - 0.004:
        return False
    if abs(ret) >= _EX_DIV_RET_THRESHOLD:
        return True
    if op > 0:
        gap = op / pc - 1.0
        if abs(gap) >= 0.04 and abs(ret - gap) < 0.025:
            return True
    return False


def has_suspected_ex_div_in_last_n_trading_days(
    daily_df: Any,
    as_of_date: DateLike,
    n: int = DEFAULT_EX_DIV_LOOKBACK_DAYS,
    *,
    code6: str = "",
) -> bool:
    """as_of 及之前最近 n 个交易日内是否出现疑似除权 K 线。"""
    if daily_df is None:
        return False
    try:
        if len(daily_df) < 2:
            return False
    except Exception:
        return False
    as_d = _as_date(as_of_date)
    n = max(1, int(n or 1))
    try:
        import pandas as pd

        df = daily_df.copy()
        if "date" not in df.columns:
            return False
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        if as_d is not None:
            df = df[df["date"].dt.date <= as_d]
        if len(df) < 2:
            return False
        df = df.sort_values("date").reset_index(drop=True)
        tail = df.tail(n + 1)
        for i in range(1, len(tail)):
            prev = tail.iloc[i - 1]
            cur = tail.iloc[i]
            try:
                pc = float(prev["close"])
                op = float(cur.get("open") or 0)
                cl = float(cur["close"])
            except (TypeError, ValueError, KeyError):
                continue
            if suspected_ex_div_bar(pc, op, cl, code6=code6):
                return True
        return False
    except Exception:
        return False
