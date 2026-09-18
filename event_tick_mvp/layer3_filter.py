# -*- coding: utf-8 -*-
"""第 3 层：盘中过滤 → buy_monitor_set（不做板块强弱）。"""
from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta
from typing import Dict, List, Optional, Sequence, Set

import pandas as pd

from event_tick_mvp.config import MvpConfig
from event_tick_mvp.types import AccountState, WatchItem


def _session_open_dt(trade_date: date) -> datetime:
    return datetime.combine(trade_date, dt_time(9, 30, 0))


def open_window_amount(bars: pd.DataFrame, trade_date: date, window_min: int) -> float:
    """开盘后 window_min 分钟累计成交额。

    支持：
    - amount_cum：累计成交额（取窗口末值）
    - amount：分钟增量（窗口内求和）
    """
    if bars is None or bars.empty:
        return 0.0
    start = _session_open_dt(trade_date)
    end = start + timedelta(minutes=max(0, int(window_min)))
    df = bars.copy()
    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"])
        try:
            if getattr(df["datetime"].dt, "tz", None) is not None:
                df["datetime"] = df["datetime"].dt.tz_localize(None)
        except Exception:
            pass
        df = df.set_index("datetime")
    if not isinstance(df.index, pd.DatetimeIndex):
        return 0.0
    try:
        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_localize(None)
    except Exception:
        pass
    mask = (df.index >= start) & (df.index < end)
    sub = df.loc[mask]
    if sub.empty:
        return 0.0
    if "amount_cum" in sub.columns:
        v = pd.to_numeric(sub["amount_cum"], errors="coerce").dropna()
        return float(v.iloc[-1]) if len(v) else 0.0
    if "amount" in sub.columns:
        return float(pd.to_numeric(sub["amount"], errors="coerce").fillna(0).sum())
    return 0.0


def _is_one_word_limit_down_row(
    row,
    prev_close: float,
    *,
    code: str,
    name: str,
    as_of: date,
    amp_eps: float = 0.002,
) -> bool:
    if prev_close <= 0:
        return False
    try:
        from utils.limit_ratio import get_limit_ratio

        ratio = float(get_limit_ratio(code, name, as_of))
    except Exception:
        ratio = 0.10
    limit_px = round(prev_close * (1.0 - ratio), 2)
    try:
        hi = float(row["high"])
        lo = float(row["low"])
        cl = float(row["close"])
    except Exception:
        return False
    if hi <= 0 or lo <= 0 or cl <= 0:
        return False
    # 振幅极小且收在跌停附近
    if (hi - lo) / prev_close > amp_eps:
        return False
    if abs(cl - limit_px) / prev_close > 0.005 and abs(lo - limit_px) / prev_close > 0.005:
        return False
    return cl <= limit_px * 1.002


def is_ban_after_limit_down_streak(
    code: str,
    trade_date: date,
    *,
    daily: pd.DataFrame,
    name: str = "",
    streak_min: int = 2,
    ban_days: int = 5,
) -> bool:
    """连续一字跌停 ≥ streak_min 后，自首个非一字跌停日起禁买 ban_days 个交易日。"""
    if daily is None or daily.empty or "date" not in daily.columns:
        return False
    df = daily.sort_values("date").reset_index(drop=True)
    if len(df) < streak_min + 1:
        return False

    # 找到 <= trade_date 的行
    df = df[df["date"] <= trade_date].copy()
    if len(df) < streak_min + 1:
        return False

    flags: List[bool] = []
    for i in range(len(df)):
        if i == 0:
            flags.append(False)
            continue
        prev_c = float(df.iloc[i - 1]["close"])
        as_of = df.iloc[i]["date"]
        if not isinstance(as_of, date):
            as_of = pd.Timestamp(as_of).date()
        flags.append(
            _is_one_word_limit_down_row(
                df.iloc[i], prev_c, code=code, name=name, as_of=as_of
            )
        )

    # 找最近一段：连续一字跌停结束日（首个非一字）作为 Day0
    # 扫描到 trade_date
    i = len(flags) - 1
    # 若当日仍在一字跌停中，也禁止买入
    if flags[i]:
        # 往前数 streak
        k = i
        while k >= 0 and flags[k]:
            k -= 1
        streak = i - k
        if streak >= streak_min:
            return True
        return False

    # 当日非一字：找最近结束的 streak
    j = i
    # j 是非一字；看 j-1 往前是否有 streak
    end = j  # Day0 = 首个非一字日 = 当前日或更早
    # 找到最近一个「结束点」：某日非一字，且前一日起连续 streak_min 一字
    for end_idx in range(i, streak_min - 1, -1):
        if flags[end_idx]:
            continue
        # end_idx 非一字，检查其前连续一字
        k = end_idx - 1
        while k >= 0 and flags[k]:
            k -= 1
        streak = end_idx - 1 - k
        if streak >= streak_min:
            # Day0 = end_idx 对应日期；禁买 ban_days 个交易日（含 Day0）
            day0 = df.iloc[end_idx]["date"]
            if not isinstance(day0, date):
                day0 = pd.Timestamp(day0).date()
            # 交易日序列：df['date'] 从 end_idx 起
            window = list(df.iloc[end_idx:]["date"])
            ban_set = set()
            for d in window[:ban_days]:
                dd = d if isinstance(d, date) else pd.Timestamp(d).date()
                ban_set.add(dd)
            return trade_date in ban_set
    return False


def filter_to_buy_monitor(
    watch_list: Sequence[WatchItem],
    *,
    trade_date: date,
    account: AccountState,
    cfg: MvpConfig,
    bars_by_code: Dict[str, pd.DataFrame],
    daily_by_code: Optional[Dict[str, pd.DataFrame]] = None,
    names: Optional[Dict[str, str]] = None,
    planned_buy_yuan: Optional[Dict[str, float]] = None,
) -> List[WatchItem]:
    """全部条件通过才进入 buy_monitor_set。"""
    held: Set[str] = set(account.position_codes())
    industry_count: Dict[str, int] = {}
    for p in account.positions.values():
        ind = str(p.industry or "").strip() or "_UNKNOWN_"
        industry_count[ind] = industry_count.get(ind, 0) + 1

    planned_buy_yuan = planned_buy_yuan or {}
    daily_by_code = daily_by_code or {}
    names = names or {}
    out: List[WatchItem] = []

    nav = float(account.nav or 0.0) or (
        float(account.cash)
        + sum(float(p.entry_price) * int(p.volume) for p in account.positions.values())
    )
    default_plan = float(cfg.max_weight) * float(nav)

    for w in watch_list:
        c6 = w.code
        if c6 in held:
            continue

        ind = str(w.industry or "").strip()
        if not ind:
            if cfg.reject_if_no_industry:
                continue
            ind = "_UNKNOWN_"
        if industry_count.get(ind, 0) >= int(cfg.industry_cap):
            continue

        if is_ban_after_limit_down_streak(
            c6,
            trade_date,
            daily=daily_by_code.get(c6, pd.DataFrame()),
            name=names.get(c6, ""),
            streak_min=int(cfg.limit_down_streak_min),
            ban_days=int(cfg.post_limit_down_ban_days),
        ):
            continue

        bars = bars_by_code.get(c6)
        win_amt = open_window_amount(
            bars if bars is not None else pd.DataFrame(),
            trade_date,
            int(cfg.open_amt_window_min),
        )
        if win_amt < float(cfg.open_amt_min_yuan):
            continue

        plan = float(planned_buy_yuan.get(c6, default_plan))
        if plan > win_amt * float(cfg.impact_amt_frac):
            continue

        out.append(w)
    return out
