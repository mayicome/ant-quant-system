# -*- coding: utf-8 -*-
"""布林 %b / MA10 归一化斜率 — 选股与卖出共用。"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional, Sequence, Tuple, Union

DateLike = Union[date, datetime, str, None]


def as_date(d: DateLike) -> Optional[date]:
    if d is None or d == "":
        return None
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    try:
        return date.fromisoformat(str(d).strip()[:10])
    except Exception:
        return None


def ma(closes: Sequence[float], n: int) -> Optional[float]:
    p = max(1, int(n or 1))
    if closes is None or len(closes) < p:
        return None
    try:
        window = [float(x) for x in closes[-p:]]
    except (TypeError, ValueError):
        return None
    if any(x != x or x <= 0 for x in window):
        return None
    return float(sum(window)) / float(p)


def boll_bands(
    closes: Sequence[float], period: int = 20, k: float = 2.0
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """(mid, upper, lower)；样本标准差 ddof=1。"""
    p = max(2, int(period or 20))
    if closes is None or len(closes) < p:
        return None, None, None
    try:
        window = [float(x) for x in closes[-p:]]
    except (TypeError, ValueError):
        return None, None, None
    if any(x != x or x <= 0 for x in window):
        return None, None, None
    mean = sum(window) / float(p)
    var = sum((x - mean) ** 2 for x in window) / float(p - 1)
    if var != var or var < 0:
        return None, None, None
    std = var ** 0.5
    kk = float(k)
    return mean, mean + kk * std, mean - kk * std


def pct_b(price: float, upper: Optional[float], lower: Optional[float]) -> Optional[float]:
    if price is None or upper is None or lower is None:
        return None
    try:
        p = float(price)
        u = float(upper)
        lo = float(lower)
    except (TypeError, ValueError):
        return None
    width = u - lo
    if width <= 0 or p != p:
        return None
    return (p - lo) / width


def ma10_slope_norm(
    closes: Sequence[float],
    *,
    period: int = 10,
    slope_days: int = 5,
) -> Tuple[Optional[float], Optional[float], List[float]]:
    """近 slope_days 日 MA10 对时间 0..n-1 线性回归。

    返回 (k, Slope_norm, ma_series)。Slope_norm = k / mean(MA10)。
    """
    p = max(1, int(period or 10))
    n = max(2, int(slope_days or 5))
    need = p + n - 1
    if closes is None or len(closes) < need:
        return None, None, []
    mas: List[float] = []
    for i in range(n):
        end = len(closes) - (n - 1 - i)
        m = ma(closes[:end], p)
        if m is None:
            return None, None, mas
        mas.append(float(m))
    xs = list(range(n))
    mean_x = sum(xs) / float(n)
    mean_y = sum(mas) / float(n)
    if mean_y == 0 or mean_y != mean_y:
        return None, None, mas
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x <= 0:
        return None, None, mas
    cov_xy = sum((xs[i] - mean_x) * (mas[i] - mean_y) for i in range(n))
    k = cov_xy / var_x
    return float(k), float(k / mean_y), mas


def closes_through(daily_data, as_of: DateLike) -> Optional[List[float]]:
    if daily_data is None:
        return None
    try:
        if len(daily_data) == 0:
            return None
    except Exception:
        return None
    as_d = as_date(as_of)
    rows: List[float] = []
    for _, r in daily_data.iterrows():
        dd = as_date(r.get("_d") or r.get("date"))
        if dd is None:
            continue
        if as_d is not None and dd > as_d:
            continue
        try:
            c = float(r.get("close"))
        except (TypeError, ValueError):
            continue
        if c == c and c > 0:
            rows.append(c)
    return rows if rows else None


def load_closes_for_code(
    stock_code: str,
    as_of: DateLike,
    *,
    prefer_daily_full_before: Tuple[int, int, int] = (2026, 1, 1),
) -> Tuple[Optional[List[float]], str]:
    """读日线收盘；as_of < 2026-01-01 优先 daily_full。"""
    as_d = as_date(as_of)
    src = "none"
    df = None
    use_full = False
    if as_d is not None:
        try:
            cutoff = date(*prefer_daily_full_before)
            use_full = as_d < cutoff
        except Exception:
            use_full = as_d.year < 2026
    if use_full:
        try:
            from utils.data_sync_request import load_full_daily

            df = load_full_daily(stock_code, through_date=as_d, adjust="none")
            if df is not None and len(df) > 0:
                src = "daily_full"
        except Exception:
            df = None
    if df is None or getattr(df, "empty", True):
        try:
            from utils.daily_cache_reader import load_daily_from_cache

            df = load_daily_from_cache(stock_code, through_date=as_d, adjust="none")
            if df is not None and len(df) > 0:
                src = "daily_cache"
        except Exception:
            df = None
    closes = closes_through(df, as_d)
    return closes, src
