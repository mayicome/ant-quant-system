"""
交易日历：委托仓库根 utils.trading_day（xtdata / akshare），与板块选股一致。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional, Set, Tuple


def _ensure_repo_root():
    try:
        from repo_path import ensure_repo_root_on_sys_path

        ensure_repo_root_on_sys_path()
    except ImportError:
        import os
        import sys

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)


def get_trading_dates_set_for_range(start_date: date, end_date: date) -> Set[date]:
    _ensure_repo_root()
    from utils.trading_day import get_trading_dates_set_for_range as _fn

    return _fn(start_date, end_date)


def get_trading_dates_in_range_sorted(start_date: date, end_date: date) -> List[date]:
    _ensure_repo_root()
    from utils.trading_day import get_trading_dates_in_range_sorted as _fn

    return _fn(start_date, end_date)


def next_trading_day_after(d: date) -> Optional[date]:
    lo = d + timedelta(days=1)
    hi = d + timedelta(days=400)
    lst = get_trading_dates_in_range_sorted(lo, hi)
    return lst[0] if lst else None


def first_trading_day_on_or_after(d: date) -> Optional[date]:
    hi = d + timedelta(days=60)
    lst = get_trading_dates_in_range_sorted(d, hi)
    return lst[0] if lst else None


def backtest_window_from_selection_day(
    selection_t: date,
    *,
    start_next_trading_day: bool,
    hold_trading_days: int,
) -> Tuple[Optional[date], Optional[date], str]:
    if hold_trading_days < 1:
        return None, None, "持有交易日数须 >= 1"

    if start_next_trading_day:
        start = next_trading_day_after(selection_t)
        hint = "T+1 起"
    else:
        start = first_trading_day_on_or_after(selection_t)
        hint = "T 当日起"

    if start is None:
        return None, None, "无法取得回测起始日（请确认交易日历可用）"

    hi = start + timedelta(days=400)
    lst = get_trading_dates_in_range_sorted(start, hi)
    if len(lst) < hold_trading_days:
        return (
            None,
            None,
            f"从 {start} 起交易日不足：需要 {hold_trading_days} 天，仅 {len(lst)} 天",
        )
    end = lst[hold_trading_days - 1]
    return start, end, hint


def sim_hold_days_covering_entry_window(
    entry_window_trading_days: int,
    sell_hold_trading_days: int,
) -> int:
    """覆盖「最晚买入日 + 成交后持有」的总长度（entry + hold − 1）。

    批量回测已改为：仿真长度=策略「运行交易日数」，持有留给下一轮；
    本函数保留给需要「一枪打满运行+持有」的调用方。
    """
    try:
        ew = int(entry_window_trading_days)
    except (TypeError, ValueError):
        ew = 1
    try:
        sh = int(sell_hold_trading_days)
    except (TypeError, ValueError):
        sh = 1
    ew = max(1, ew)
    sh = max(1, sh)
    return max(sh, ew + sh - 1)


def trading_day_window_from_start(
    start: date, hold_trading_days: int
) -> Tuple[Optional[date], Optional[date], str]:
    if hold_trading_days < 1:
        return None, None, "持有交易日数须 >= 1"
    lst = get_trading_dates_in_range_sorted(start, start + timedelta(days=400))
    if len(lst) < hold_trading_days:
        return (
            None,
            None,
            f"从 {start} 起交易日不足：需要 {hold_trading_days} 天，仅 {len(lst)} 天",
        )
    start_eff = lst[0]
    end = lst[hold_trading_days - 1]
    return start_eff, end, ""
