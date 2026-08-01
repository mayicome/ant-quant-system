# -*- coding: utf-8 -*-
"""策略生成器写入 rules_armed.json 的 strategy_pool_watch（仅订阅，不碰 tasks/watch_codes）。"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from utils.ant_rules_io_ext import (
    RULES_VERSION,
    collect_subscribe_codes,
    default_paths,
    load_json,
    normalize_watch_codes,
    prune_results_stocks,
    save_json_atomic,
)


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def code_6_to_full(code: str) -> str:
    c6 = "".join(ch for ch in str(code or "") if ch.isdigit())[:6].zfill(6)
    if c6 == "000000":
        return ""
    if c6.startswith(("5", "6")):
        return f"{c6}.SH"
    if c6.startswith(("4", "8", "920")):
        return f"{c6}.BJ"
    return f"{c6}.SZ"


def codes_6_to_full(codes_6: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in codes_6 or []:
        full = code_6_to_full(raw)
        if full and full not in seen:
            seen.add(full)
            out.append(full)
    return sorted(out)


def _load_rules(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {
            "version": RULES_VERSION,
            "trade_date": "",
            "updated_at": "",
            "tasks": [],
            "watch_codes": [],
            "strategy_pool_watch": [],
        }
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def get_strategy_pool_watch(root: Optional[str] = None) -> List[str]:
    rules_path, _ = default_paths(root or _project_root())
    data = _load_rules(rules_path)
    return normalize_watch_codes(data.get("strategy_pool_watch"))


def set_strategy_pool_watch(codes_6: List[str], *, root: Optional[str] = None) -> bool:
    """运行开始：写入 strategy_pool_watch；运行结束由 clear_strategy_pool_watch 释放。"""
    rules_path, _ = default_paths(root or _project_root())
    data = _load_rules(rules_path)
    new_watch = codes_6_to_full(codes_6)
    old_watch = normalize_watch_codes(data.get("strategy_pool_watch"))
    if old_watch == new_watch:
        return False
    data.setdefault("version", RULES_VERSION)
    data["strategy_pool_watch"] = new_watch
    data["subscribe_codes"] = collect_subscribe_codes(
        data.get("tasks") or [],
        data.get("watch_codes"),
        new_watch,
    )
    data["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    data.setdefault("tasks", data.get("tasks") or [])
    data.setdefault("watch_codes", normalize_watch_codes(data.get("watch_codes")))
    save_json_atomic(rules_path, data)
    return True


def _prune_results_to_live(root: str, keep_codes: List[str]) -> int:
    """立刻修剪 results.json，不等待大 QMT shadow 热加载。"""
    _, results_path = default_paths(root)
    if not os.path.isfile(results_path):
        return 0
    try:
        results = load_json(results_path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0
    removed = prune_results_stocks(results, keep_codes)
    if removed:
        save_json_atomic(results_path, results)
    return removed


def clear_strategy_pool_watch(*, root: Optional[str] = None) -> int:
    """生成任务结束或启动时清空 strategy_pool_watch；并修剪 results.json。"""
    base = root or _project_root()
    rules_path, _ = default_paths(base)
    data = _load_rules(rules_path)
    prev = normalize_watch_codes(data.get("strategy_pool_watch"))
    if not prev:
        keep = collect_subscribe_codes(
            data.get("tasks") or [],
            data.get("watch_codes"),
            None,
        )
        _prune_results_to_live(base, keep)
        return 0
    data["strategy_pool_watch"] = []
    keep = collect_subscribe_codes(
        data.get("tasks") or [],
        data.get("watch_codes"),
        None,
    )
    data["subscribe_codes"] = keep
    data["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    save_json_atomic(rules_path, data)
    removed = _prune_results_to_live(base, keep)
    if removed:
        print(
            "[strategy_pool_watch] pruned results.stocks removed=%d keep=%d"
            % (removed, len(keep))
        )
    return len(prev)
