# -*- coding: utf-8 -*-
"""选股日之后、某日之前，日线是否已触达 MA（对齐跌MA单点：low <= 早盘「N日」触发价）。

早盘「10日」= 不含当日的近 9 根收盘均（与 backtest data_provider 早盘一致）。
用于实盘冷启动：池子含多日旧票时，跳过「历史上已经第一次跌破」的腿。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional, Tuple


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


def _load_daily(code6: str, through: date):
    """只读本地 daily_cache CSV（不走 load_daily_dataframe）。

    load_daily_dataframe 在缓存「偏旧」时会扫 tick 补洞，整池检查会卡死 UI。
    缺数时跳过该票（不当已触达）。
    """
    try:
        from utils.daily_cache_reader import load_daily_from_cache

        return load_daily_from_cache(code6, through_date=through)
    except Exception:
        try:
            from daily_cache_reader import load_daily_from_cache as _ld  # type: ignore

            return _ld(code6, through_date=through)
        except Exception:
            return None


def _calendar_span_days(entry_window: int) -> int:
    """入场窗只需约 entry_window 个交易日，勿用 +400 天（会超出日历缓存并反复拉 akshare）。"""
    ew = max(1, int(entry_window or 1))
    return max(45, ew * 3 + 30)


def _trading_days_sorted(lo: date, hi: date) -> list:
    if lo > hi:
        lo, hi = hi, lo
    try:
        from strategy_generator_app.trading_calendar import get_trading_dates_in_range_sorted

        return list(get_trading_dates_in_range_sorted(lo, hi) or [])
    except Exception:
        pass
    try:
        from trading_calendar import get_trading_dates_in_range_sorted as _g2  # type: ignore

        return list(_g2(lo, hi) or [])
    except Exception:
        out = []
        d = lo
        while d <= hi:
            if d.weekday() < 5:
                out.append(d)
            d += timedelta(days=1)
        return out


def _bounds_from_sorted_cal(
    sel: date, entry_window: int, cal: list
) -> Tuple[Optional[date], Optional[date]]:
    ew = max(1, int(entry_window or 1))
    after = [d for d in cal if d > sel]
    if not after:
        return None, None
    start = after[0]
    end = after[ew - 1] if len(after) >= ew else after[-1]
    return start, end


def already_touched_ma_in_entry_window(
    stock_code: Any,
    *,
    selection_date: Any,
    before_date: Any,
    ma_period: int = 10,
    entry_start: Any = None,
    entry_end: Any = None,
) -> Tuple[bool, Optional[date]]:
    """选股后挂单窗内、before_date 之前（不含当日）是否已有 low<=触发价。

    返回 (已触达?, 首次触达日)。
    无日线/无法判定时返回 (False, None)——不拦挂单（避免缺数漏买）。
    """
    c6 = _norm_code(stock_code)
    sel = _as_date(selection_date)
    before = _as_date(before_date)
    if not c6 or sel is None or before is None:
        return False, None

    period = max(2, int(ma_period or 10))
    need_prior = period - 1  # 早盘「N日」用近 need_prior 根收盘

    start = _as_date(entry_start)
    if start is None:
        try:
            from strategy_generator_app.trading_calendar import next_trading_day_after

            start = next_trading_day_after(sel)
        except Exception:
            try:
                from trading_calendar import next_trading_day_after as _n2

                start = _n2(sel)
            except Exception:
                start = sel + timedelta(days=1)
                while start.weekday() >= 5:
                    start += timedelta(days=1)
    if start is None:
        return False, None

    end_cap = _as_date(entry_end)
    # 只扫到 before 的前一自然日范围内的交易日；且不超过挂单窗末日
    last = before - timedelta(days=1)
    if end_cap is not None and end_cap < last:
        last = end_cap
    if last < start:
        return False, None

    df = _load_daily(c6, through=before)
    if df is None:
        return False, None
    try:
        if getattr(df, "empty", True):
            return False, None
    except Exception:
        return False, None

    # 统一列
    cols = {str(c).lower(): c for c in df.columns}
    date_col = cols.get("date")
    if date_col is None:
        for c in df.columns:
            if "date" in str(c).lower() or "时间" in str(c) or "日期" in str(c):
                date_col = c
                break
    close_col = cols.get("close") or cols.get("收盘")
    low_col = cols.get("low") or cols.get("最低")
    if date_col is None or close_col is None or low_col is None:
        return False, None

    try:
        import pandas as pd

        work = df[[date_col, close_col, low_col]].copy()
        work["_d"] = pd.to_datetime(work[date_col], errors="coerce").dt.date
        work["_c"] = pd.to_numeric(work[close_col], errors="coerce")
        work["_l"] = pd.to_numeric(work[low_col], errors="coerce")
        work = work.dropna(subset=["_d", "_c"]).sort_values("_d").reset_index(drop=True)
    except Exception:
        return False, None
    if work.empty:
        return False, None

    dates = work["_d"].tolist()
    closes = work["_c"].tolist()
    lows = work["_l"].tolist()

    for i, d in enumerate(dates):
        if d < start or d > last:
            continue
        if i < need_prior:
            continue
        # 早盘触发价：不含当日的近 need_prior 根收盘均
        prior = closes[i - need_prior : i]
        if len(prior) < need_prior:
            continue
        try:
            trig = round(float(sum(prior)) / float(need_prior), 2)
            low_v = float(lows[i])
        except (TypeError, ValueError):
            continue
        if trig <= 0 or low_v != low_v or low_v <= 0:
            continue
        if low_v <= trig + 1e-9:
            return True, d
    return False, None


def _entry_window_bounds(
    sel: date,
    entry_window: int,
    *,
    cal: Optional[list] = None,
) -> Tuple[Optional[date], Optional[date]]:
    """选股日后第 1～entry_window 个交易日 → (start, end)。"""
    ew = max(1, int(entry_window or 1))
    if cal is not None:
        return _bounds_from_sorted_cal(sel, ew, cal)
    span = _calendar_span_days(ew)
    lst = _trading_days_sorted(sel + timedelta(days=1), sel + timedelta(days=span))
    if not lst:
        start = sel + timedelta(days=1)
        while start.weekday() >= 5:
            start += timedelta(days=1)
        d = start
        n = 0
        while True:
            if d.weekday() < 5:
                n += 1
                if n >= ew:
                    return start, d
            d += timedelta(days=1)
    return _bounds_from_sorted_cal(sel, ew, lst)


def scan_codes_already_touched_ma(
    selection_date_by_code: Optional[dict],
    *,
    before_date: Any = None,
    entry_window: int = 10,
    ma_period: int = 10,
    codes: Optional[list] = None,
) -> list:
    """批量扫描：返回已触达列表 [{code, selection_date, touch_date}, ...]。

    整池只拉一次交易日历，避免每只股票 +400 天触发反复联网刷新日历。
    """
    sel_map = selection_date_by_code if isinstance(selection_date_by_code, dict) else {}
    before = _as_date(before_date) or date.today()
    out: list = []
    if codes is None:
        keys = list(sel_map.keys())
    else:
        keys = list(codes)

    # 先收集 (code, sel)，再一次性取日历
    pairs: list = []
    seen = set()
    for raw in keys:
        c6 = _norm_code(raw)
        if not c6 or c6 in seen:
            continue
        seen.add(c6)
        sel = _as_date(sel_map.get(c6) or sel_map.get(raw))
        if sel is None:
            for k, v in sel_map.items():
                if _norm_code(k) == c6:
                    sel = _as_date(v)
                    break
        if sel is None:
            continue
        pairs.append((c6, sel))
    if not pairs:
        return out

    ew = max(1, int(entry_window or 1))
    span = _calendar_span_days(ew)
    dmin = min(s for _, s in pairs)
    dmax = max(max(s for _, s in pairs), before) + timedelta(days=span)
    cal = _trading_days_sorted(dmin, dmax)

    for c6, sel in pairs:
        start, end = _entry_window_bounds(sel, ew, cal=cal)
        hit, touch_d = already_touched_ma_in_entry_window(
            c6,
            selection_date=sel,
            before_date=before,
            ma_period=ma_period,
            entry_start=start,
            entry_end=end,
        )
        if hit:
            out.append(
                {
                    "code": c6,
                    "selection_date": sel.isoformat(),
                    "touch_date": touch_d.isoformat() if touch_d else "",
                }
            )
    out.sort(key=lambda r: (r.get("selection_date") or "", r.get("code") or ""))
    return out
