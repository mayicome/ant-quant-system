# -*- coding: utf-8 -*-
"""实盘交易相关全局配置（data/config.ini [Trading]）。"""
from __future__ import annotations

import configparser
import os
from typing import Optional

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def trading_config_path() -> str:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "data", "config.ini")


def read_trading_bool(key: str, default: bool = False) -> bool:
    try:
        path = trading_config_path()
        if not os.path.exists(path):
            return bool(default)
        config = configparser.ConfigParser()
        config.read(path, encoding="utf-8")
        if "Trading" not in config:
            return bool(default)
        value = config.get("Trading", key, fallback="1" if default else "0")
        return str(value).strip().lower() in _TRUTHY
    except Exception:
        return bool(default)


def write_trading_bool(key: str, value: bool) -> bool:
    try:
        path = trading_config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        config = configparser.ConfigParser()
        if os.path.exists(path):
            config.read(path, encoding="utf-8")
        if "Trading" not in config:
            config.add_section("Trading")
        config.set("Trading", key, "1" if value else "0")
        with open(path, "w", encoding="utf-8") as f:
            config.write(f)
        return True
    except Exception:
        return False


def breakthrough_buy_require_true_breakthrough(default: bool = False) -> bool:
    return read_trading_bool("breakthrough_buy_require_true_breakthrough", default=default)


def breakthrough_buy_probe_enabled(default: bool = False) -> bool:
    """突破买入试探建仓：上穿后先买 20%，确认窗口内再决定是否补买剩余 80%（基于规则 volume）。"""
    return read_trading_bool("breakthrough_buy_probe_enabled", default=default)


def breakthrough_buy_require_break_below_trigger(default: bool = False) -> bool:
    """突破买入须先跌破触发价，再上穿时才允许买入（与 require_true_breakthrough 同为全局开关）。"""
    return read_trading_bool("breakthrough_buy_require_break_below_trigger", default=default)


def non_early_order_sell_smart_sell_enabled(default: bool = False) -> bool:
    """任务设置：非提前下单的卖出任务是否走智能卖出流程（弹性卖出、提前下单卖出不受影响）。"""
    return read_trading_bool("non_early_order_sell_smart_sell", default=default)
