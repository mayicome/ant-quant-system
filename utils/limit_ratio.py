#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A 股涨跌停幅度：沪深主板 ST/*ST 自 2026-07-06 起由 ±5% 调整为 ±10%。"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional, Tuple, Union

# 沪深主板 ST/*ST 涨跌幅调整生效日（含当日）
ST_MAIN_BOARD_LIMIT_CHANGE_DATE = date(2026, 7, 6)

DateLike = Union[date, datetime, None]


def normalize_stock_code(stock_code: str) -> str:
    code = (stock_code or "").strip()
    if "." in code:
        code = code.split(".", 1)[0].strip()
    m = re.search(r"(\d{6})", code)
    if m:
        code = m.group(1)
    if code.isdigit():
        return code[:6].zfill(6)
    return code


def is_st_stock(stock_name: str) -> bool:
    return "ST" in (stock_name or "").upper()


def is_main_board(stock_code: str) -> bool:
    code = normalize_stock_code(stock_code)
    if code.startswith(("300", "301", "688", "689")):
        return False
    if code.startswith(("8", "4", "920")):
        return False
    return True


def _coerce_date(value: DateLike) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    # pandas.Timestamp 等
    to_pydatetime = getattr(value, "to_pydatetime", None)
    if callable(to_pydatetime):
        try:
            return to_pydatetime().date()
        except Exception:
            pass
    date_fn = getattr(value, "date", None)
    if callable(date_fn):
        try:
            return date_fn()
        except Exception:
            pass
    return None


def get_limit_ratio(
    stock_code: str,
    stock_name: str = "",
    as_of_date: DateLike = None,
) -> float:
    """
    返回涨跌停幅度（小数，如 0.10 表示 10%）。
    创业板/科创板/北交所按板块；沪深主板 ST/*ST 在 2026-07-06 前为 5%，之后为 10%。
    """
    code = normalize_stock_code(stock_code)
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("8", "4", "920")):
        return 0.30

    if is_st_stock(stock_name) and is_main_board(code):
        ref = _coerce_date(as_of_date) or date.today()
        if ref >= ST_MAIN_BOARD_LIMIT_CHANGE_DATE:
            return 0.10
        return 0.05

    return 0.10


def get_limit_multipliers(
    stock_code: str,
    stock_name: str = "",
    as_of_date: DateLike = None,
) -> Tuple[float, float]:
    """返回 (涨停乘数, 跌停乘数)，如 (1.10, 0.90)。"""
    ratio = get_limit_ratio(stock_code, stock_name, as_of_date)
    return 1.0 + ratio, 1.0 - ratio
