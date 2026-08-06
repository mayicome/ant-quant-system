# -*- coding: utf-8 -*-
"""策略生成器读取 data/config.ini 的 qmt_mode，与主程序一致。"""
from __future__ import annotations

from typing import Literal

QmtMode = Literal["mini", "builtin", "standalone"]


def get_qmt_mode(default: QmtMode = "mini") -> QmtMode:
    from utils.qmt_execution_config import get_qmt_mode as _get

    return _get(default=default)


def use_builtin_price_feed() -> bool:
    from utils.qmt_execution_config import use_builtin_price_feed as _use

    return _use()


def allow_xtdata_daily_fallback() -> bool:
    """回测日线：仅 mini 模式允许 daily_cache 缺失时由本机 xtdata 直接拉取。"""
    return get_qmt_mode() == "mini"


def use_on_demand_daily_sync() -> bool:
    """builtin/standalone：缺日线时提交请求，由大 QMT 拉取并写入 daily_cache。"""
    return get_qmt_mode() in ("builtin", "standalone")


def backtest_tick_local_only() -> bool:
    """回测 tick：仅读本地 data/ticks，缺失不向 QMT/xtdata 按需拉取。"""
    return True


def backtest_preflight_hint_lines() -> list[str]:
    """回测 Tab 与启动日志用的数据依赖说明。"""
    try:
        from strategy_generator_app.backtest.preflight import (
            backtest_preflight_hint_lines as _lines,
        )

        return _lines()
    except ImportError:
        from backtest.preflight import backtest_preflight_hint_lines as _lines  # type: ignore

        return _lines()


def startup_status_lines() -> list[str]:
    mode = get_qmt_mode()
    lines = [
        f"[配置] qmt_mode={mode}（与 data/config.ini [Account] 一致）",
    ]
    if use_on_demand_daily_sync():
        lines.append(
            "[配置] 回测日线：读 data/daily_cache；缺数据时请求大 QMT 同步落盘（无外部 xtdata）"
        )
    elif allow_xtdata_daily_fallback():
        lines.append("[配置] 回测日线：优先 data/daily_cache，缺失时本机 xtdata 回退（mini 模式）")
    else:
        lines.append("[配置] 回测日线：仅 data/daily_cache")
    if use_on_demand_daily_sync():
        lines.append(
            "[配置] 回测 tick：仅 data/ticks 本地文件；缺失不会向大 QMT 按需下载"
        )
    elif allow_xtdata_daily_fallback():
        lines.append("[配置] 回测 tick：仅 data/ticks 本地文件；缺失不会 xtdata 拉取")
    else:
        lines.append("[配置] 回测 tick：仅 data/ticks 本地文件")
    if mode in ("builtin", "standalone"):
        lines.append(
            "[配置] 实盘生成：results.json 现价/今开 + daily_cache 昨收/均线；超时失败不回退"
        )
        lines.extend(backtest_preflight_hint_lines())
    else:
        lines.append("[配置] 实盘生成：MiniQMT xtdata + xttrader")
        lines.extend(backtest_preflight_hint_lines())
    return lines
