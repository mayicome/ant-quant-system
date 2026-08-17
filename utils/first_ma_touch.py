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
    try:
        from utils.daily_cache_reader import load_daily_dataframe

        return load_daily_dataframe(code6, through_date=through, allow_xtdata_fallback=True)
    except Exception:
        try:
            from daily_cache_reader import load_daily_dataframe as _ld  # type: ignore

            return _ld(code6, through_date=through, allow_xtdata_fallback=True)
        except Exception:
            return None


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
) -> Tuple[Optional[date], Optional[date]]:
    """选股日后第 1～entry_window 个交易日 → (start, end)。"""
    ew = max(1, int(entry_window or 1))
    try:
        from strategy_generator_app.trading_calendar import (
            get_trading_dates_in_range_sorted,
            next_trading_day_after,
        )

        start = next_trading_day_after(sel)
        if start is None:
            return None, None
        lst = get_trading_dates_in_range_sorted(start, start + timedelta(days=400))
        if not lst:
            return start, start
        end = lst[ew - 1] if len(lst) >= ew else lst[-1]
        return start, end
    except Exception:
        pass
    try:
        from trading_calendar import (  # type: ignore
            get_trading_dates_in_range_sorted as _g2,
            next_trading_day_after as _n2,
        )

        start = _n2(sel)
        if start is None:
            return None, None
        lst = _g2(start, start + timedelta(days=400))
        if not lst:
            return start, start
        end = lst[ew - 1] if len(lst) >= ew else lst[-1]
        return start, end
    except Exception:
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


def scan_codes_already_touched_ma(
    selection_date_by_code: Optional[dict],
    *,
    before_date: Any = None,
    entry_window: int = 10,
    ma_period: int = 10,
    codes: Optional[list] = None,
) -> list:
    """批量扫描：返回已触达列表 [{code, selection_date, touch_date}, ...]。"""
    sel_map = selection_date_by_code if isinstance(selection_date_by_code, dict) else {}
    before = _as_date(before_date) or date.today()
    out: list = []
    if codes is None:
        keys = list(sel_map.keys())
    else:
        keys = list(codes)
    seen = set()
    for raw in keys:
        c6 = _norm_code(raw)
        if not c6 or c6 in seen:
            continue
        seen.add(c6)
        sel = _as_date(sel_map.get(c6) or sel_map.get(raw))
        if sel is None:
            # 尝试无前导零等宽松键
            for k, v in sel_map.items():
                if _norm_code(k) == c6:
                    sel = _as_date(v)
                    break
        if sel is None:
            continue
        start, end = _entry_window_bounds(sel, entry_window)
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
