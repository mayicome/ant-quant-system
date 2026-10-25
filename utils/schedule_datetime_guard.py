# -*- coding: utf-8 -*-
"""预约/定时日期安全：约 N 个交易日钳位 + 禁止滚轮改段。

交易系统「定时加载」、策略生成「定时生成」共用，避免误触滚到 2028 等远期。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional, Tuple, Union

from PyQt5.QtCore import QObject, QEvent, QDate
from PyQt5.QtWidgets import QDateTimeEdit

DateLike = Union[date, datetime]


def schedule_max_date(
    from_day: Optional[date] = None,
    trading_days: int = 14,
    calendar_fallback_days: int = 21,
) -> date:
    """可选日期上限：约 N 个交易日；日历失败时退回约 calendar_fallback_days 自然日。"""
    start = from_day or date.today()
    if isinstance(start, datetime):
        start = start.date()
    if not isinstance(start, date):
        start = date.today()
    try:
        from utils.trading_day import is_tradeday
    except Exception:
        is_tradeday = None  # type: ignore
    found = []
    d = start
    need = max(1, int(trading_days or 14))
    for _ in range(max(need * 4, 60)):
        try:
            traded = bool(is_tradeday(d)) if is_tradeday is not None else (d.weekday() < 5)
        except Exception:
            traded = d.weekday() < 5
        if traded:
            found.append(d)
            if len(found) >= need:
                return found[-1]
        d = d + timedelta(days=1)
    return start + timedelta(days=max(1, int(calendar_fallback_days or 21)))


def schedule_qdate_bounds(
    trading_days: int = 14,
    calendar_fallback_days: int = 21,
) -> Tuple[QDate, QDate]:
    """返回 (min_qdate, max_qdate)：最小约今天，最大约之后 N 个交易日。"""
    today = datetime.now().date()
    max_d = schedule_max_date(
        today, trading_days=trading_days, calendar_fallback_days=calendar_fallback_days
    )
    if max_d < today:
        max_d = today + timedelta(days=max(1, int(calendar_fallback_days or 21)))
    return (
        QDate(today.year, today.month, today.day),
        QDate(max_d.year, max_d.month, max_d.day),
    )


class ScheduleNoWheelFilter(QObject):
    """吞掉 QDateTimeEdit（及子控件）滚轮，避免误滚年份/日期段。"""

    def eventFilter(self, obj, event):
        try:
            if event is not None and event.type() == QEvent.Wheel:
                return True
        except Exception:
            pass
        return False


def apply_schedule_datetime_bounds(
    dt_edit: QDateTimeEdit,
    trading_days: int = 14,
    calendar_fallback_days: int = 21,
) -> Tuple[QDate, QDate]:
    """给控件与日历弹层设置最小/最大日期，返回 (min, max)。"""
    min_qd, max_qd = schedule_qdate_bounds(
        trading_days=trading_days, calendar_fallback_days=calendar_fallback_days
    )
    dt_edit.setMinimumDate(min_qd)
    dt_edit.setMaximumDate(max_qd)
    try:
        cal = dt_edit.calendarWidget()
        if cal is not None:
            cal.setMinimumDate(min_qd)
            cal.setMaximumDate(max_qd)
    except Exception:
        pass
    return min_qd, max_qd


def install_schedule_no_wheel(dt_edit: QDateTimeEdit, parent: Optional[QObject] = None) -> ScheduleNoWheelFilter:
    """安装滚轮过滤；调用方须把返回值挂到实例属性以免被 GC。"""
    filt = ScheduleNoWheelFilter(parent if parent is not None else dt_edit)
    dt_edit.installEventFilter(filt)
    try:
        le = dt_edit.lineEdit() if hasattr(dt_edit, "lineEdit") else None
        if le is not None:
            le.installEventFilter(filt)
    except Exception:
        pass
    try:
        for child in dt_edit.findChildren(QObject):
            try:
                child.installEventFilter(filt)
            except Exception:
                pass
    except Exception:
        pass
    return filt


def apply_schedule_datetime_guard(
    dt_edit: QDateTimeEdit,
    parent: Optional[QObject] = None,
    trading_days: int = 14,
    calendar_fallback_days: int = 21,
) -> ScheduleNoWheelFilter:
    """一次完成：钳位 + 禁滚轮。返回 filter，须由调用方保活。"""
    apply_schedule_datetime_bounds(
        dt_edit, trading_days=trading_days, calendar_fallback_days=calendar_fallback_days
    )
    return install_schedule_no_wheel(dt_edit, parent=parent)


def schedule_date_in_bounds(
    run_day: DateLike,
    trading_days: int = 14,
    calendar_fallback_days: int = 21,
) -> Tuple[bool, QDate, QDate]:
    """校验预约日期是否在 [今天, 约 N 个交易日] 内。返回 (ok, min_qd, max_qd)。"""
    min_qd, max_qd = schedule_qdate_bounds(
        trading_days=trading_days, calendar_fallback_days=calendar_fallback_days
    )
    if isinstance(run_day, datetime):
        d = run_day.date()
    else:
        d = run_day
    run_qd = QDate(d.year, d.month, d.day)
    ok = not (run_qd < min_qd or run_qd > max_qd)
    return ok, min_qd, max_qd


SCHEDULE_BOUNDS_HINT = "预约日期须在今天起约 14 个交易日内（约两周），请重新选择。"
