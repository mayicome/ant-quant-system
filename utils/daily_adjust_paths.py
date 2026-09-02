# -*- coding: utf-8 -*-
"""日线复权目录：none（默认）与 qfq（前复权）并行存储。"""
from __future__ import annotations

import os
from datetime import date
from typing import Literal

AdjustKind = Literal["none", "qfq"]

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA = os.path.join(_PROJECT_ROOT, "data")

# QMT ContextInfo.get_market_data_ex dividend_type
QMT_DIVIDEND_NONE = "none"
QMT_DIVIDEND_QFQ = "front"

DIR_BY_ADJUST = {
    "none": {
        "cache": "daily_cache",
        "full": "daily_full",
    },
    "qfq": {
        "cache": "daily_cache_qfq",
        "full": "daily_full_qfq",
    },
}


def normalize_adjust(adjust: str | None) -> AdjustKind:
    a = str(adjust or "none").strip().lower()
    if a in ("qfq", "front", "前复权", "front_ratio"):
        return "qfq"
    return "none"


def cache_dir_for(adjust: str | None = None) -> str:
    kind = normalize_adjust(adjust)
    return os.path.join(_DATA, DIR_BY_ADJUST[kind]["cache"])


def full_dir_for(adjust: str | None = None) -> str:
    kind = normalize_adjust(adjust)
    return os.path.join(_DATA, DIR_BY_ADJUST[kind]["full"])


def qmt_dividend_type(adjust: str | None) -> str:
    return QMT_DIVIDEND_QFQ if normalize_adjust(adjust) == "qfq" else QMT_DIVIDEND_NONE


def cache_qfq_floor_date(today: date | None = None) -> date:
    """daily_cache_qfq 只保留当年及以后（自然年切分，后续可换交易日历）。"""
    today = today or date.today()
    return date(today.year, 1, 1)


def full_qfq_cap_date(today: date | None = None) -> date:
    """daily_full_qfq 只保留至去年末（自然年）。"""
    today = today or date.today()
    return date(today.year - 1, 12, 31)
