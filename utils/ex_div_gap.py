# -*- coding: utf-8 -*-
"""疑似除权（开盘相对昨收缺口过大）检测。

判定：某日 开盘/昨收 - 1 < -(该股涨跌停幅度 + eps)。
普通跌停开盘约等于一个跌停幅；再深通常为除权/特殊复牌。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional, Tuple


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _norm_code(code: Any) -> str:
    s = str(code or "").strip().upper()
    if "." in s:
        s = s.split(".", 1)[0]
    digits = "".join(c for c in s if c.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)[-6:]


def _as_date(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


def limit_ratio(stock_code: Any, stock_name: Any = "", as_of: Any = None) -> float:
    name = str(stock_name or "").upper()
    code = _norm_code(stock_code)
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("8", "4", "920")):
        return 0.30
    if "ST" in name:
        ref = _as_date(as_of) or date.today()
        if ref >= date(2026, 7, 6):
            return 0.10
        return 0.05
    return 0.10


def find_ex_div_gap(
    stock_code: Any,
    *,
    stock_name: Any = "",
    through_date: Any = None,
    lookback: int = 20,
    eps: float = 0.005,
    daily_df: Any = None,
    project_root: Optional[str] = None,
) -> Tuple[bool, Optional[float], Optional[date]]:
    """近 lookback 个交易日（截止 through_date，含当日）是否出现疑似除权。

    返回 (存在缺口?, 最深缺口小数, 缺口日)。
    """
    c6 = _norm_code(stock_code)
    through = _as_date(through_date) or date.today()
    lb = max(1, int(lookback))
    eps = float(eps)

    df = daily_df
    if df is None:
        try:
            from utils.daily_cache_reader import load_daily_from_cache

            df = load_daily_from_cache(c6, through_date=through)
        except Exception:
            try:
                from daily_cache_reader import load_daily_from_cache  # type: ignore

                df = load_daily_from_cache(c6, through_date=through)
            except Exception:
                df = None
    if df is None or getattr(df, "empty", True):
        return False, None, None
    if "open" not in getattr(df, "columns", []) or "close" not in getattr(df, "columns", []):
        return False, None, None

    try:
        dd = df.copy()
        dd["_d"] = dd["date"].map(_as_date)
        dd = dd.dropna(subset=["_d"]).sort_values("_d")
        dd = dd[dd["_d"] <= through]
    except Exception:
        return False, None, None
    if dd is None or dd.empty or len(dd) < 2:
        return False, None, None

    window = dd.tail(lb + 1)
    closes_by_d = {}
    opens_by_d = {}
    dates = []
    for _, r in window.iterrows():
        d = _as_date(r.get("_d"))
        try:
            o = float(r.get("open"))
            c = float(r.get("close"))
        except (TypeError, ValueError):
            continue
        if d is None or not (o == o and o > 0 and c == c and c > 0):
            continue
        closes_by_d[d] = c
        opens_by_d[d] = o
        dates.append(d)
    dates = sorted(set(dates))
    check_dates = [d for d in dates if d <= through][-lb:]
    worst = None
    worst_d = None
    for d in check_dates:
        prev_closes = [closes_by_d[x] for x in dates if x < d]
        if not prev_closes:
            continue
        pc = float(prev_closes[-1])
        o = float(opens_by_d.get(d) or 0)
        if pc <= 0 or o <= 0:
            continue
        gap = (o / pc) - 1.0
        thr = -(limit_ratio(c6, stock_name, d) + eps)
        if gap < thr:
            if worst is None or gap < worst:
                worst = gap
                worst_d = d
    return worst is not None, worst, worst_d


def has_ex_div_gap(
    stock_code: Any,
    *,
    stock_name: Any = "",
    through_date: Any = None,
    lookback: int = 20,
    eps: float = 0.005,
    daily_df: Any = None,
    project_root: Optional[str] = None,
) -> bool:
    hit, _, _ = find_ex_div_gap(
        stock_code,
        stock_name=stock_name,
        through_date=through_date,
        lookback=lookback,
        eps=eps,
        daily_df=daily_df,
        project_root=project_root,
    )
    return bool(hit)


def _fprice(v: Any) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    if x != x or x <= 0:
        return 0.0
    return float(x)


def quote_pre_close_from_prices(prices_row: Any) -> float:
    """行情昨收（除权日开盘前通常已是除权参考价）。"""
    if not isinstance(prices_row, dict):
        return 0.0
    for k in ("昨收盘", "昨收", "pre_close", "preClose", "last_close", "lastClose"):
        x = _fprice(prices_row.get(k))
        if x > 0:
            return x
    return 0.0


def quote_open_from_prices(prices_row: Any) -> float:
    """行情今开（竞价后可见；开盘前可能为 0）。"""
    if not isinstance(prices_row, dict):
        return 0.0
    for k in ("今开盘", "今开", "open", "today_open", "todayOpen"):
        x = _fprice(prices_row.get(k))
        if x > 0:
            return x
    return 0.0


def last_cache_close(
    stock_code: Any,
    *,
    through_date: Any = None,
    daily_df: Any = None,
) -> Tuple[float, Optional[date]]:
    """日线缓存最近一根收盘价（未复权序列，除权日前仍是旧价）。"""
    c6 = _norm_code(stock_code)
    through = _as_date(through_date) or date.today()
    df = daily_df
    if df is None:
        try:
            from utils.daily_cache_reader import load_daily_from_cache

            df = load_daily_from_cache(c6, through_date=through)
        except Exception:
            try:
                from daily_cache_reader import load_daily_from_cache  # type: ignore

                df = load_daily_from_cache(c6, through_date=through)
            except Exception:
                df = None
    if df is None or getattr(df, "empty", True) or "close" not in getattr(df, "columns", []):
        return 0.0, None
    try:
        dd = df.copy()
        dd["_d"] = dd["date"].map(_as_date)
        dd = dd.dropna(subset=["_d"]).sort_values("_d")
        dd = dd[dd["_d"] <= through]
    except Exception:
        return 0.0, None
    if dd is None or dd.empty:
        return 0.0, None
    # 若已有「当日」未复权 K 线，用其前一日收盘作对照；否则用最后一根
    last = dd.iloc[-1]
    last_d = _as_date(last.get("_d"))
    if last_d is not None and last_d >= through and len(dd) >= 2:
        prev = dd.iloc[-2]
        return _fprice(prev.get("close")), _as_date(prev.get("_d"))
    return _fprice(last.get("close")), last_d


def find_preclose_adj_mismatch(
    stock_code: Any,
    *,
    prices_row: Any = None,
    through_date: Any = None,
    daily_df: Any = None,
    min_ratio: float = 0.08,
) -> Tuple[bool, str]:
    """日线缓存昨收 vs 行情昨收大幅偏离 → 当日除权（开盘前 9:26 即可判）。

    例：瑞达期货转增后缓存仍约 21，行情昨收已约 14。
    """
    cache_close, cache_d = last_cache_close(
        stock_code, through_date=through_date, daily_df=daily_df
    )
    quote_pc = quote_pre_close_from_prices(prices_row)
    if cache_close <= 0 or quote_pc <= 0:
        return False, ""
    ratio = quote_pc / cache_close
    diff = abs(ratio - 1.0)
    if diff + 1e-12 < float(min_ratio):
        return False, ""
    ds = cache_d.isoformat() if cache_d else "?"
    return (
        True,
        "preclose_mismatch cache=%.2f@%s quote=%.2f ratio=%.3f"
        % (cache_close, ds, quote_pc, ratio),
    )


def find_open_vs_cache_gap(
    stock_code: Any,
    *,
    stock_name: Any = "",
    prices_row: Any = None,
    through_date: Any = None,
    daily_df: Any = None,
    eps: float = 0.005,
) -> Tuple[bool, str]:
    """行情今开相对日线缓存昨收的缺口（行情昨收尚未调整时的兜底）。"""
    cache_close, cache_d = last_cache_close(
        stock_code, through_date=through_date, daily_df=daily_df
    )
    o = quote_open_from_prices(prices_row)
    if cache_close <= 0 or o <= 0:
        return False, ""
    gap = (o / cache_close) - 1.0
    thr = -(limit_ratio(stock_code, stock_name, through_date) + float(eps))
    if gap >= thr:
        return False, ""
    ds = cache_d.isoformat() if cache_d else "?"
    return (
        True,
        "open_vs_cache_gap open=%.2f cache=%.2f@%s gap=%.2f%%"
        % (o, cache_close, ds, gap * 100.0),
    )


def should_block_buy_ex_div(
    stock_code: Any,
    *,
    stock_name: Any = "",
    through_date: Any = None,
    lookback: int = 20,
    eps: float = 0.005,
    prices_row: Any = None,
    daily_df: Any = None,
    min_preclose_adj_ratio: float = 0.08,
    project_root: Optional[str] = None,
) -> Tuple[bool, str]:
    """买入策略是否因除权停买。

    1) 近 lookback 日线开盘缺口（原逻辑）
    2) 日线昨收 vs 行情昨收大幅偏离（除权日开盘前可用）
    3) 今开相对日线昨收超跌停缺口（行情昨收未调时的兜底）
    """
    hit, gap, d = find_ex_div_gap(
        stock_code,
        stock_name=stock_name,
        through_date=through_date,
        lookback=lookback,
        eps=eps,
        daily_df=daily_df,
        project_root=project_root,
    )
    if hit:
        ds = d.isoformat() if d else "?"
        g = "" if gap is None else ("%.2f%%" % (float(gap) * 100.0))
        return True, "daily_open_gap day=%s gap=%s" % (ds, g)

    mism, detail = find_preclose_adj_mismatch(
        stock_code,
        prices_row=prices_row,
        through_date=through_date,
        daily_df=daily_df,
        min_ratio=min_preclose_adj_ratio,
    )
    if mism:
        return True, detail

    og, detail2 = find_open_vs_cache_gap(
        stock_code,
        stock_name=stock_name,
        prices_row=prices_row,
        through_date=through_date,
        daily_df=daily_df,
        eps=eps,
    )
    if og:
        return True, detail2
    return False, ""


def should_skip_lu_leg_ex_div(
    stock_code: Any,
    *,
    stock_name: Any = "",
    through_date: Any = None,
    prices_row: Any = None,
    daily_df: Any = None,
    min_preclose_adj_ratio: float = 0.08,
    eps: float = 0.005,
) -> Tuple[bool, str]:
    """卖出「近涨停腿」是否因除权日失真而跳过。

    只用「当日」信号（日线昨收 vs 行情昨收 / 今开 vs 日线昨收），
    不用近 N 日开盘缺口回看，避免除权后多日误伤近涨停腿。
    开盘涨幅腿、清仓腿不受影响。
    """
    mism, detail = find_preclose_adj_mismatch(
        stock_code,
        prices_row=prices_row,
        through_date=through_date,
        daily_df=daily_df,
        min_ratio=min_preclose_adj_ratio,
    )
    if mism:
        return True, detail
    og, detail2 = find_open_vs_cache_gap(
        stock_code,
        stock_name=stock_name,
        prices_row=prices_row,
        through_date=through_date,
        daily_df=daily_df,
        eps=eps,
    )
    if og:
        return True, detail2
    return False, ""

