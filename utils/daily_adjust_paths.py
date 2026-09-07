# -*- coding: utf-8 -*-
"""日线复权目录：none（默认）、qfq（前复权）、hfq（后复权）并行存储。"""
from __future__ import annotations

import os
from datetime import date
from typing import Literal

AdjustKind = Literal["none", "qfq", "hfq"]

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA = os.path.join(_PROJECT_ROOT, "data")

# QMT ContextInfo.get_market_data_ex dividend_type
QMT_DIVIDEND_NONE = "none"
QMT_DIVIDEND_QFQ = "front"
QMT_DIVIDEND_HFQ = "back"

DIR_BY_ADJUST = {
    "none": {
        "cache": "daily_cache",
        "full": "daily_full",
    },
    "qfq": {
        "cache": "daily_cache_qfq",
        "full": "daily_full_qfq",
    },
    "hfq": {
        "cache": "daily_cache_hfq",
        "full": "daily_full_hfq",
    },
}


def normalize_adjust(adjust: str | None) -> AdjustKind:
    a = str(adjust or "none").strip().lower()
    if a in ("qfq", "front", "前复权", "front_ratio"):
        return "qfq"
    if a in ("hfq", "back", "后复权", "back_ratio"):
        return "hfq"
    return "none"


def cache_dir_for(adjust: str | None = None) -> str:
    kind = normalize_adjust(adjust)
    return os.path.join(_DATA, DIR_BY_ADJUST[kind]["cache"])


def full_dir_for(adjust: str | None = None) -> str:
    kind = normalize_adjust(adjust)
    return os.path.join(_DATA, DIR_BY_ADJUST[kind]["full"])


def qmt_dividend_type(adjust: str | None) -> str:
    kind = normalize_adjust(adjust)
    if kind == "qfq":
        return QMT_DIVIDEND_QFQ
    if kind == "hfq":
        return QMT_DIVIDEND_HFQ
    return QMT_DIVIDEND_NONE


def cache_qfq_floor_date(today: date | None = None) -> date:
    """daily_cache_qfq / daily_cache_hfq 只保留当年及以后（自然年切分）。"""
    today = today or date.today()
    return date(today.year, 1, 1)


def full_qfq_cap_date(today: date | None = None) -> date:
    """daily_full_qfq / daily_full_hfq 只保留至去年末（自然年）。"""
    today = today or date.today()
    return date(today.year - 1, 12, 31)


# 别名：后复权同样用自然年切分
cache_hfq_floor_date = cache_qfq_floor_date
full_hfq_cap_date = full_qfq_cap_date
