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
