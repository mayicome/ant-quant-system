# -*- coding: utf-8 -*-
"""QMT 执行模式配置（data/config.ini [Account] qmt_mode）。"""
from __future__ import annotations

import configparser
import os
from typing import Literal

QmtMode = Literal["mini", "builtin", "standalone"]

_VALID_MODES = frozenset({"mini", "builtin", "standalone"})


def _config_path() -> str:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "data", "config.ini")


def get_qmt_mode(default: QmtMode = "mini") -> QmtMode:
    """读取 qmt_mode：mini=外部 xtdata；builtin/standalone=读 results.json 现价心跳。"""
    fallback = default if default in _VALID_MODES else "mini"
    try:
        path = _config_path()
        if not os.path.isfile(path):
            return fallback
        cfg = configparser.ConfigParser()
        cfg.read(path, encoding="utf-8")
        if "Account" not in cfg:
            return fallback
        raw = str(cfg.get("Account", "qmt_mode", fallback=fallback)).strip().lower()
        if raw in _VALID_MODES:
            return raw  # type: ignore[return-value]
    except Exception:
        pass
    return fallback


def use_builtin_price_feed() -> bool:
    """是否由 QMT 内置策略经 results.json 提供现价（状态栏/图表竖线）。"""
    return get_qmt_mode() in ("builtin", "standalone")


def use_builtin_order_execution() -> bool:
    """builtin/standalone：下单由大 QMT 内置 passorder，禁止主程序 xt_trader.order_stock。"""
    return use_builtin_price_feed()


def skip_external_quote_subscribe() -> bool:
    """builtin/standalone 模式下跳过 MiniQMT xtdata 订阅，避免双路行情冲突。"""
    return use_builtin_price_feed()


def allow_qmt_client_auto_restart() -> bool:
    """是否允许主程序调用 qmt_login 杀进程重启 QMT。builtin/standalone 下大 QMT 由模型交易维护。"""
    return get_qmt_mode() == "mini"


def relax_xt_trader_health_check() -> bool:
    """
    builtin/standalone：行情与下单由大 QMT 模型交易 + results.json；
    外部 xt_trader 仅用于资产/持仓展示，不可用时不应触发重连风暴。
    """
    return use_builtin_price_feed()


def requires_path_qmt() -> bool:
    """mini 模式必须配置 path_qmt；builtin/standalone 下可留空（资金/持仓/现价走 results.json）。"""
    return get_qmt_mode() == "mini"
