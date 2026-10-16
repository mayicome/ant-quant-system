# -*- coding: utf-8 -*-
"""外部主程序用：统一从 UTF-8 源码导入，勿用 QMT 部署目录下的 GBK 副本。

QMT 侧的 load_json / save_json_atomic 已改成空操作，避免模型交易里出现文件读写。
本模块给交易界面恢复真正的读写，规则才能写入 data/rules_armed.json。
"""
import json
import os
import shutil
import tempfile
import time
from typing import Any, Dict

from qmt_builtin.src.ant_rules_io import (  # noqa: F401
    RESULTS_FLUSH_INTERVAL_SEC,
    RESULTS_PATH,
    RESULTS_VERSION,
    RULES_ARMED_PATH,
    RULES_RELOAD_INTERVAL_SEC,
    RULES_VERSION,
    append_stock_event,
    collect_subscribe_codes,
    default_paths,
    empty_results,
    extract_tick_high_low,
    extract_tick_open,
    extract_tick_price,
    load_results_prices,
    load_rules_armed,
    normalize_armed_task,
    normalize_watch_codes,
    prune_results_stocks,
    rules_file_signature,
    update_price_snapshot,
)


def _formula_results() -> Dict[str, Any]:
    """策略日志还原出的结果。还没读到 ANTRES 行时保持空，避免把旧文件又读回来。"""
    try:
        from utils.qmt_formula_result_log import current

        data = current()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_account_positions_snapshot(path: str):
    data = _formula_results()
    if not data:
        return None, {}
    acc = data.get("account") if isinstance(data.get("account"), dict) else None
    pos = data.get("positions") if isinstance(data.get("positions"), dict) else {}
    if not acc and not pos:
        return None, {}
    return acc or None, pos


def load_orders_snapshot(path: str) -> list:
    data = _formula_results()
    orders = data.get("orders") if data else None
    return list(orders) if isinstance(orders, list) else []


def load_broker_orders_snapshot(path: str) -> list:
    data = _formula_results()
    orders = data.get("broker_orders") if data else None
    return list(orders) if isinstance(orders, list) else []


def load_elastic_states_snapshot(path: str) -> dict:
    data = _formula_results()
    states = data.get("elastic_states") if data else None
    return dict(states) if isinstance(states, dict) else {}


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def save_json_atomic(path: str, data: Dict[str, Any]) -> None:
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        last_err = None
        for attempt in range(12):
            try:
                os.replace(tmp, path)
                tmp = ""
                return
            except OSError as e:
                last_err = e
                if attempt >= 7:
                    try:
                        shutil.copy2(tmp, path)
                        tmp = ""
                        return
                    except OSError:
                        pass
                time.sleep(0.08 * (attempt + 1))
        if last_err is not None:
            raise last_err
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
