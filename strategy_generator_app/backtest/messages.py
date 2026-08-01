# -*- coding: utf-8 -*-
"""回测报错文案（随 qmt_mode 区分 builtin / mini）。"""
from __future__ import annotations

from datetime import date

try:
    from strategy_generator_app.qmt_mode_config import (
        allow_xtdata_daily_fallback,
        use_on_demand_daily_sync,
    )
except ImportError:
    from qmt_mode_config import (  # type: ignore
        allow_xtdata_daily_fallback,
        use_on_demand_daily_sync,
    )


def morning_prices_empty_message(trade_date: date) -> str:
    d = trade_date.isoformat()
    if use_on_demand_daily_sync():
        return (
            f"[{d}] 获取早盘行情失败：daily_cache 无数据。"
            "请确认大 QMT 模型交易已启动，并查看 data/data_sync_requests.json 同步是否完成。"
        )
    if allow_xtdata_daily_fallback():
        return (
            f"[{d}] 获取早盘行情失败：未返回数据。"
            "请确认 MiniQMT 已连接、xtquant 可用，或 data/daily_cache 已有该日数据。"
        )
    return (
        f"[{d}] 获取早盘行情失败：data/daily_cache 无该日数据。"
        "请先补全日线缓存。"
    )


def morning_prices_zero_message(trade_date: date) -> str:
    d = trade_date.isoformat()
    if use_on_demand_daily_sync():
        return (
            f"[{d}] 早盘行情中所有标的（含持仓）价格为 0："
            "daily_cache 未就绪或 short_history；请确认大 QMT 已同步日线。"
        )
    if allow_xtdata_daily_fallback():
        return (
            f"[{d}] 早盘行情中所有标的（含持仓）价格为 0："
            "请检查 MiniQMT/xtdata 或 daily_cache 是否含该日及股票池数据。"
        )
    return (
        f"[{d}] 早盘行情中所有标的（含持仓）价格为 0："
        "请检查 data/daily_cache。"
    )
