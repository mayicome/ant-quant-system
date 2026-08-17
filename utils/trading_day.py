#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
交易日判断工具
使用 akshare 替代 chncal
"""

from datetime import date, datetime, timedelta, time as dt_time
from typing import Optional, Any, List, Set, Tuple
import pandas as pd

# 关键价/昨收从「当日复盘基准」切换为「次日布局基准」的时刻（默认 16:00，便于 15:00–16:00 复盘）
REFERENCE_SWITCH_TIME = dt_time(16, 0)


# 交易日历缓存（避免重复获取）
_trade_date_cache = None
_cache_date_range = None
# 记录缓存构建当天；用于跨自然日后对“今天是否交易日”做一次刷新兜底
_cache_built_on = None
# 同日早盘 xtdata 日历不含“今天”时，是否已尝试过强制刷新（避免死循环）
_same_day_missing_today_refresh_on: Optional[date] = None
# 今日 weekday 兜底告警是否已打印
_today_weekday_fallback_logged_on: Optional[date] = None
# 警告标志（避免重复打印警告）
_warning_printed = False
# 成功标志（避免重复打印成功信息）
_success_printed = False


def invalidate_trading_day_cache() -> None:
    """清除交易日历缓存。QMT 刚连接或重连后调用，避免早盘不完整日历被缓存一整天。"""
    global _trade_date_cache, _cache_date_range, _cache_built_on, _same_day_missing_today_refresh_on
    _trade_date_cache = None
    _cache_date_range = None
    _cache_built_on = None
    _same_day_missing_today_refresh_on = None


def _build_trade_date_cache(cache_start: date, cache_end: date, today: date) -> bool:
    """从 xtdata / akshare 构建交易日历缓存，成功返回 True。"""
    global _trade_date_cache, _cache_date_range, _cache_built_on, _warning_printed, _success_printed

    def _parse_ts_to_date(ts_val: Any) -> Optional[date]:
        if isinstance(ts_val, (int, float)):
            ts_float = float(ts_val)
            d = datetime.fromtimestamp(ts_float / 1000.0 if ts_float > 1e10 else ts_float).date()
            return d
        if isinstance(ts_val, str):
            ds = ts_val.replace("-", "").strip()[:8]
            if ds.isdigit():
                return datetime.strptime(ds, "%Y%m%d").date()
        return None

    trade_dates_set = set()
    last_xt_error = RuntimeError("xtdata 未调用")
    xtdata_had_partial = False

    # 1) xtdata
    try:
        import xtquant.xtdata as xtdata
        try:
            xtdata.enable_hello = False
        except Exception:
            pass

        start_str = cache_start.strftime("%Y%m%d")
        end_str = cache_end.strftime("%Y%m%d")
        trading_dates_ts = xtdata.get_trading_dates("SH", start_time=start_str, end_time=end_str)

        trade_dates_set = set()
        for ts_val in (trading_dates_ts or []):
            d = _parse_ts_to_date(ts_val)
            if d:
                trade_dates_set.add(d)

        if trade_dates_set:
            if today in trade_dates_set:
                _trade_date_cache = trade_dates_set
                _cache_date_range = (cache_start, cache_end)
                _cache_built_on = today
                if not _success_printed:
                    print("✓ 成功从 xtdata 获取交易日历")
                    print(f"  - 缓存范围: {cache_start} 至 {cache_end} (共{len(_trade_date_cache)}个交易日)")
                    _success_printed = True
                return True
            # xtdata 有历史日历但缺「今天」（早盘 QMT 未就绪时常见），继续用 akshare 补充
            _trade_date_cache = trade_dates_set
            xtdata_had_partial = True
            last_xt_error = RuntimeError("xtdata 日历未包含今日")
    except Exception as e_xt:
        last_xt_error = e_xt
    else:
        if not trade_dates_set:
            last_xt_error = RuntimeError("xtdata 返回的交易日期为空")

    # 2) akshare（xtdata 失败或缺「今天」时）
    try:
        import akshare as ak
        trade_date_df = None
        if hasattr(ak, "tool_trade_date_hist_sina"):
            trade_date_df = ak.tool_trade_date_hist_sina()
        elif hasattr(ak, "tool") and hasattr(ak.tool, "trade_date_hist_sina"):
            trade_date_df = ak.tool.trade_date_hist_sina()

        if trade_date_df is None or getattr(trade_date_df, "empty", True):
            raise RuntimeError("akshare 返回的交易日历为空")

        if "trade_date" not in trade_date_df.columns:
            if "date" in trade_date_df.columns:
                trade_date_df = trade_date_df.rename(columns={"date": "trade_date"})
            else:
                first_col = trade_date_df.columns[0]
                trade_date_df = trade_date_df.rename(columns={first_col: "trade_date"})

        trade_date_df["trade_date"] = pd.to_datetime(trade_date_df["trade_date"]).dt.date
        trade_date_df = trade_date_df[
            (trade_date_df["trade_date"] >= cache_start)
            & (trade_date_df["trade_date"] <= cache_end)
        ]
        akshare_set = set(trade_date_df["trade_date"].tolist())
        if not akshare_set:
            raise RuntimeError("akshare 筛选后交易日历为空")

        if _trade_date_cache:
            _trade_date_cache = _trade_date_cache | akshare_set
        else:
            _trade_date_cache = akshare_set
        _cache_date_range = (cache_start, cache_end)
        _cache_built_on = today
        if not _success_printed:
            src = "akshare 补充" if xtdata_had_partial else "akshare"
            print(f"✓ 成功从 {src} 获取交易日历（批量）")
            print(f"  - 缓存范围: {cache_start} 至 {cache_end} (共{len(_trade_date_cache)}个交易日)")
            _success_printed = True
        return True
    except Exception as e_ak:
        if _trade_date_cache and today in _trade_date_cache:
            _cache_date_range = (cache_start, cache_end)
            _cache_built_on = today
            return True
        if not _warning_printed:
            print(
                f"警告: 交易日历获取失败（xtdata/akshare均失败），"
                f"使用简单周末判断（{type(last_xt_error).__name__}/{type(e_ak).__name__}）"
            )
            _warning_printed = True
        return False


def _today_weekday_fallback(check_date: date) -> bool:
    """日历源均不可用或未收录「今天」时，对当日 Mon–Fri 做兜底（不含法定节假日）。"""
    global _today_weekday_fallback_logged_on
    today = date.today()
    if check_date != today or check_date.weekday() >= 5:
        return False
    if _today_weekday_fallback_logged_on != today:
        print(
            f"警告: 交易日历未包含今日({today})，暂按工作日兜底判定为交易日；"
            f"若今日为法定节假日，请稍后重连 QMT 或重启程序刷新日历"
        )
        _today_weekday_fallback_logged_on = today
    return True


def previous_tradeday(from_date: Optional[date] = None) -> date:
    """返回 from_date（默认今天）之前最近一个交易日。"""
    d = (from_date or date.today()) - timedelta(days=1)
    for _ in range(40):
        if is_tradeday(d):
            return d
        d -= timedelta(days=1)
    return d


def is_tradeday(check_date: Optional[date] = None) -> bool:
    """
    判断指定日期是否为交易日。

    优先策略：
    1) 用 xtdata.get_trading_dates() 取交易日历（不依赖 akshare 的 is_trade_date API）
    2) xtdata 获取失败才回退到 akshare 的交易日历（不调用 is_trade_date 逐日接口）
    3) 仍失败则回退简单“周末判断”
    """
    global _trade_date_cache, _cache_date_range, _cache_built_on, _same_day_missing_today_refresh_on

    if check_date is None:
        check_date = date.today()

    today = date.today()
    cache_start = today.replace(year=today.year - 3)  # 从3年前开始
    cache_end = today.replace(year=today.year + 1)    # 到明年结束

    missing_today_in_cache = (
        _trade_date_cache is not None and today not in _trade_date_cache
    )

    # 跨自然日：昨日及更早构建的缓存不含今天
    stale_cross_day_cache = (
        _cache_built_on is not None
        and _cache_built_on < today
        and check_date >= today
        and check_date not in _trade_date_cache
    )

    # 同日早盘：xtdata 在 QMT 未就绪时可能返回不含「今天」的日历，
    # 旧逻辑因 _cache_built_on == today 不会刷新，导致全天误判为非交易日。
    same_day_incomplete_cache = (
        missing_today_in_cache
        and check_date == today
        and _same_day_missing_today_refresh_on != today
    )

    need_refresh = (
        _trade_date_cache is None
        or _cache_date_range is None
        or check_date < _cache_date_range[0]
        or check_date > _cache_date_range[1]
        or stale_cross_day_cache
        or same_day_incomplete_cache
    )

    if need_refresh:
        if same_day_incomplete_cache:
            _same_day_missing_today_refresh_on = today
        if not _build_trade_date_cache(cache_start, cache_end, today):
            return _today_weekday_fallback(check_date) if check_date == today else check_date.weekday() < 5

    if _trade_date_cache and check_date in _trade_date_cache:
        return True

    if check_date == today and _today_weekday_fallback(check_date):
        return True

    return False


def next_tradeday_datetime_at(
    hour: int = 9,
    minute: int = 25,
    second: int = 10,
    from_dt: Optional[datetime] = None,
    max_scan_days: int = 366,
) -> datetime:
    """
    返回严格晚于 from_dt 的「最近一个交易日」在指定时刻的 datetime（本地时间）。
    用于策略定时生成等：取尚未到来的最近一场 09:25:10 类预约默认值。
    """
    base = (from_dt or datetime.now()).replace(microsecond=0)
    d = base.date()
    for _ in range(max(1, int(max_scan_days or 366))):
        if is_tradeday(d):
            candidate = datetime(d.year, d.month, d.day, int(hour), int(minute), int(second))
            if candidate > base:
                return candidate
        d += timedelta(days=1)
    # 极端兜底：日历不可用时的下一个工作日同一时刻
    d = base.date() + timedelta(days=1)
    for _ in range(14):
        if d.weekday() < 5:
            return datetime(d.year, d.month, d.day, int(hour), int(minute), int(second))
        d += timedelta(days=1)
    return base + timedelta(hours=1)


def last_tradeday_on_or_before(check_date: Optional[date] = None) -> Optional[date]:
    """返回不晚于 check_date 的最近一个交易日（含 check_date 本身）。"""
    if check_date is None:
        check_date = date.today()

    if is_tradeday(check_date):
        return check_date

    if _trade_date_cache:
        candidates = [d for d in _trade_date_cache if d <= check_date]
        if candidates:
            return max(candidates)

    d = check_date
    for _ in range(15):
        d -= timedelta(days=1)
        if is_tradeday(d):
            return d
    return None


def _warm_trade_date_cache(start_date: date, end_date: date) -> None:
    """确保交易日历缓存覆盖 [start_date, end_date]。"""
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    is_tradeday(start_date)
    is_tradeday(end_date)


def get_trading_dates_set_for_range(start_date: date, end_date: date) -> Set[date]:
    """返回区间内交易日集合（优先 xtdata/akshare 缓存）。"""
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    _warm_trade_date_cache(start_date, end_date)
    if _trade_date_cache:
        return {d for d in _trade_date_cache if start_date <= d <= end_date}
    out: Set[date] = set()
    cur = start_date
    while cur <= end_date:
        if is_tradeday(cur):
            out.add(cur)
        cur += timedelta(days=1)
    return out


def get_trading_dates(count: int, as_of_date: Optional[date] = None) -> List[date]:
    """获取最近 N 个交易日。"""
    if count <= 0:
        return []
    if as_of_date is not None:
        end_date = as_of_date
    else:
        current_time = datetime.now()
        current_date = current_time.date()
        include_today = current_time.hour >= 15
        end_date = current_date if include_today else current_date - timedelta(days=1)
    start_date = end_date - timedelta(days=max(count * 3, 90))
    s = get_trading_dates_set_for_range(start_date, end_date)
    out = sorted(d for d in s if d <= end_date)
    if len(out) >= count:
        return out[-count:]
    return out


def get_trading_dates_in_range_sorted(start_date: date, end_date: date) -> List[date]:
    """返回 [start_date, end_date] 内全部交易日，升序。"""
    return sorted(get_trading_dates_set_for_range(start_date, end_date))


def is_after_reference_switch(check_dt: Optional[datetime] = None) -> bool:
    """交易日是否已过 REFERENCE_SWITCH_TIME（切换为次日布局基准）；非交易日视为已切换。"""
    if check_dt is None:
        check_dt = datetime.now()
    d = check_dt.date()
    if not is_tradeday(d):
        return True
    t = check_dt.time() if isinstance(check_dt, datetime) else datetime.now().time()
    return t >= REFERENCE_SWITCH_TIME


# 收盘后「复盘」窗口起点（至 REFERENCE_SWITCH_TIME 前）
MARKET_CLOSE_TIME = dt_time(15, 0)

_LAYOUT_BASIS_TOOLTIP = (
    "布局基准说明：\n"
    "· 交易日 15:00–{switch}：复盘 — 昨收/涨跌停仍按今日盘中（上一交易日收盘为昨收）\n"
    "· 交易日 {switch} 后：次日准备 — 昨收切为今日收盘，夜市与次日规则按新基准\n"
    "· 非交易日：视为次日准备"
).format(switch=REFERENCE_SWITCH_TIME.strftime("%H:%M"))


def get_layout_basis_status(
    check_dt: Optional[datetime] = None,
) -> Tuple[str, str, str]:
    """
    返回状态栏用的布局基准阶段。

    Returns:
        (phase_id, short_label, tooltip)
        phase_id: "review" | "next_day" | "intraday" | ""
    """
    if check_dt is None:
        check_dt = datetime.now()
    d = check_dt.date()
    t = check_dt.time() if isinstance(check_dt, datetime) else datetime.now().time()
    tip = _LAYOUT_BASIS_TOOLTIP
    switch_hm = REFERENCE_SWITCH_TIME.strftime("%H:%M")

    if not is_tradeday(d):
        return (
            "next_day",
            "次日准备 · 休市基准",
            tip,
        )

    if MARKET_CLOSE_TIME <= t < REFERENCE_SWITCH_TIME:
        return (
            "review",
            f"复盘 15:00–{switch_hm}",
            tip,
        )

    if is_after_reference_switch(check_dt):
        # 交易日 ≥16:00，或逻辑上已切次日
        return (
            "next_day",
            "次日准备 · 昨收=今日收盘",
            tip,
        )

    # 交易日、尚未到复盘窗口（含盘中）
    return ("intraday", "", tip)

