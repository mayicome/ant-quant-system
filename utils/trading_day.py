#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
交易日判断工具
使用 akshare 替代 chncal
"""

import json
import os
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
# 同日缓存缺「今天」时是否已尝试过强制刷新（避免死循环）
_same_day_missing_today_refresh_on: Optional[date] = None
# 今日 weekday 兜底告警是否已打印
_today_weekday_fallback_logged_on: Optional[date] = None
# 当天日历源已失败：不要在逐日判断里反复打新浪
_calendar_build_failed_on: Optional[date] = None
# 警告标志（避免重复打印警告）
_warning_printed = False
# 成功标志（避免重复打印成功信息）
_success_printed = False


def _safe_print(msg: str) -> None:
    """启动器把子进程 stdout 重定向到日志时，控制台编码常是 GBK，特殊符号不能把窗口打崩。"""
    try:
        print(msg)
    except UnicodeEncodeError:
        try:
            print(msg.encode("gbk", errors="replace").decode("gbk"))
        except Exception:
            pass


def invalidate_trading_day_cache() -> None:
    """清除交易日历缓存。QMT 刚连接或重连后调用，避免早盘不完整日历被缓存一整天。"""
    global _trade_date_cache, _cache_date_range, _cache_built_on
    global _same_day_missing_today_refresh_on, _calendar_build_failed_on
    _trade_date_cache = None
    _cache_date_range = None
    _cache_built_on = None
    _same_day_missing_today_refresh_on = None
    _calendar_build_failed_on = None


def _calendar_disk_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", "trade_calendar.json")


def _calendar_disk_candidates() -> List[str]:
    out: List[str] = []
    try:
        out.append(_calendar_disk_path())
    except Exception:
        pass
    try:
        from ant_qmt_paths import DATA_DIR  # type: ignore

        out.append(os.path.join(str(DATA_DIR), "trade_calendar.json"))
    except Exception:
        pass
    try:
        out.append(
            "D:\\"
            + "\u8682\u8681\u91cf\u5316\u7cfb\u7edf"
            + "\\data\\trade_calendar.json"
        )
    except Exception:
        pass
    return out


def _load_disk_trade_dates() -> Set[date]:
    path = ""
    for cand in _calendar_disk_candidates():
        try:
            if cand and os.path.isfile(cand):
                path = cand
                break
        except Exception:
            continue
    if not path:
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        raw = payload.get("dates") if isinstance(payload, dict) else None
        out: Set[date] = set()
        for item in raw or []:
            s = str(item or "").strip()[:10]
            if len(s) == 10 and s[4] == "-" and s[7] == "-":
                try:
                    out.add(datetime.strptime(s, "%Y-%m-%d").date())
                except ValueError:
                    continue
        return out
    except Exception:
        return set()


def _save_disk_trade_dates(dates: Set[date]) -> None:
    if not dates:
        return
    path = _calendar_disk_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "saved_on": date.today().isoformat(),
            "dates": [d.isoformat() for d in sorted(dates)],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        pass


def _fetch_sina_trade_dates() -> Set[date]:
    """经当前环境代理拉新浪交易日历（akshare）。"""
    import akshare as ak

    if hasattr(ak, "tool_trade_date_hist_sina"):
        trade_date_df = ak.tool_trade_date_hist_sina()
    elif hasattr(ak, "tool") and hasattr(ak.tool, "trade_date_hist_sina"):
        trade_date_df = ak.tool.trade_date_hist_sina()
    else:
        raise RuntimeError("akshare 没有新浪交易日历接口")
    if trade_date_df is None or getattr(trade_date_df, "empty", True):
        raise RuntimeError("akshare 返回的交易日历为空")
    if "trade_date" not in trade_date_df.columns:
        if "date" in trade_date_df.columns:
            trade_date_df = trade_date_df.rename(columns={"date": "trade_date"})
        else:
            trade_date_df = trade_date_df.rename(columns={trade_date_df.columns[0]: "trade_date"})
    parsed = pd.to_datetime(trade_date_df["trade_date"], errors="coerce").dt.date
    return {d for d in parsed.tolist() if isinstance(d, date)}


def _apply_trade_dates(
    dates: Set[date], cache_start: date, cache_end: date, today: date, source: str
) -> bool:
    global _trade_date_cache, _cache_date_range, _cache_built_on, _success_printed
    window = {d for d in dates if cache_start <= d <= cache_end}
    if not window:
        return False
    if _trade_date_cache:
        _trade_date_cache = set(_trade_date_cache) | window
    else:
        _trade_date_cache = window
    _cache_date_range = (cache_start, cache_end)
    _cache_built_on = today
    if not _success_printed:
        _success_printed = True
        _safe_print(f"成功从 {source} 获取交易日历（批量）")
        _safe_print(
            f"  - 缓存范围: {cache_start} 至 {cache_end} "
            f"(共{len(_trade_date_cache)}个交易日)"
        )
    return True


def _build_trade_date_cache(cache_start: date, cache_end: date, today: date) -> bool:
    """从新浪 / 本地 trade_calendar.json 构建交易日历缓存，成功返回 True。

    不再使用 xtdata.get_trading_dates（大 QMT 侧不可靠）。
    """
    global _trade_date_cache, _cache_date_range, _cache_built_on, _warning_printed, _success_printed

    # 1) 新浪交易日历（成功则写回磁盘）
    try:
        sina_dates = _fetch_sina_trade_dates()
        if _apply_trade_dates(sina_dates, cache_start, cache_end, today, "新浪交易日历"):
            _save_disk_trade_dates(sina_dates)
            return True
        raise RuntimeError("新浪交易日历筛选后为空")
    except Exception as e_ak:
        # 2) 本地磁盘缓存
        disk_dates = _load_disk_trade_dates()
        if disk_dates and _apply_trade_dates(
            disk_dates, cache_start, cache_end, today, "本地交易日历缓存"
        ):
            return True
        if _trade_date_cache and (
            today in _trade_date_cache or _cache_covers_today(today)
        ):
            _cache_date_range = (cache_start, cache_end)
            _cache_built_on = today
            return True
        if not _warning_printed:
            _warning_printed = True
            _safe_print(
                f"警告: 交易日历获取失败（新浪失败且无可用本地缓存），"
                f"按非交易日处理（{type(e_ak).__name__}）"
            )
        return False


def _today_weekday_fallback(check_date: date) -> bool:
    """日历不可用时的兜底：失败闭合为非交易日。

    旧逻辑按 Mon–Fri=交易日，会把国庆等法定休市误判为开市。
    """
    global _today_weekday_fallback_logged_on
    today = date.today()
    if check_date != today:
        return False
    if _today_weekday_fallback_logged_on != today:
        _today_weekday_fallback_logged_on = today
        _safe_print(
            f"警告: 交易日历未覆盖今日({today})，按非交易日处理（失败闭合）；"
            f"请检查 data/trade_calendar.json 或稍后重连刷新日历"
        )
    return False


def _cache_covers_today(today: date) -> bool:
    """缓存是否已覆盖到今天（有今天或更晚的交易日），可据此认定「缺今天=休市」。"""
    if not _trade_date_cache:
        return False
    try:
        return max(_trade_date_cache) >= today
    except ValueError:
        return False


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
    1) 新浪交易日历（akshare），成功则写回 data/trade_calendar.json
    2) 本地 trade_calendar.json
    3) 仍失败则按非交易日（失败闭合，不用 weekday）
    """
    global _trade_date_cache, _cache_date_range, _cache_built_on, _same_day_missing_today_refresh_on
    global _calendar_build_failed_on

    if check_date is None:
        check_date = date.today()

    today = date.today()
    if _trade_date_cache is None and _calendar_build_failed_on == today:
        disk = _load_disk_trade_dates()
        if disk:
            lo, hi = min(disk), max(disk)
            if lo <= check_date <= hi:
                return check_date in disk
        # 失败闭合：勿 weekday 兜底
        return False
    cache_start = today.replace(year=today.year - 3)  # 从3年前开始
    cache_end = today.replace(year=today.year + 1)    # 到明年结束
    # 历史回测/选股若问到更早日期，把缓存起点前推（含约 60 自然日缓冲，供「前 N 交易日」）
    if check_date < cache_start:
        cache_start = check_date - timedelta(days=60)

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

    # 同日：缓存未覆盖到今天时再刷一次；已覆盖且缺今天=休市，不必再刷
    same_day_incomplete_cache = (
        missing_today_in_cache
        and check_date == today
        and _same_day_missing_today_refresh_on != today
        and not _cache_covers_today(today)
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
        # 已有缓存但问到更早日期时，合并扩窗，避免把近期日历冲掉
        if (
            _cache_date_range is not None
            and check_date < _cache_date_range[0]
            and _trade_date_cache
        ):
            cache_start = min(cache_start, check_date - timedelta(days=60))
            cache_end = max(cache_end, _cache_date_range[1])
        if not _build_trade_date_cache(cache_start, cache_end, today):
            _calendar_build_failed_on = today
            # 构建失败时仍优先磁盘：覆盖范围内缺日=休市，勿把国庆等周中假当交易日
            disk = _load_disk_trade_dates()
            if disk:
                lo, hi = min(disk), max(disk)
                if lo <= check_date <= hi:
                    return check_date in disk
            return False
        _calendar_build_failed_on = None

    if _trade_date_cache and check_date in _trade_date_cache:
        return True

    # 日历已覆盖到今天：不在集合内 = 休市（含周五法定假日），禁止再按工作日兜底
    if check_date == today and _cache_covers_today(today):
        return False

    # 磁盘日历覆盖范围内：缺日=休市（国庆等），优先于工作日兜底
    disk = _load_disk_trade_dates()
    if disk:
        lo, hi = min(disk), max(disk)
        if lo <= check_date <= hi:
            return check_date in disk

    # 仅日历失败/过期未覆盖到今天时，才对今日做工作日兜底
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
    """返回区间内交易日集合（新浪/本地日历缓存）。"""
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    _warm_trade_date_cache(start_date, end_date)
    if _trade_date_cache:
        return {d for d in _trade_date_cache if start_date <= d <= end_date}
    # 日历源失败时不要用工作日冒充，也不要逐日再打网络
    return set()


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

