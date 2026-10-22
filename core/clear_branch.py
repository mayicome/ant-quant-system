# -*- coding: utf-8 -*-
"""清仓腿 T 点封板分支（到点快照，非累计观察）。

策略意图带 clear_branch_at 后，交易系统在 T 的第一个可用 tick：
- 快照是否封板（现价贴涨停且卖一=0，对齐 is_limit_up_sealed）
- 已封板 → 改为 breakthrough_sell，price=涨停价（现价 < 涨停才触发）
- 未封板 → 保留原清仓规则（single_sell / best_sell）并允许触发
- 当天一直封到收盘 → 开板腿不触发（breakthrough 条件不满足）

T 前图表只显示一个待分支节点；T 后同一规则原地变成生效类型。
"""
from __future__ import annotations

from typing import Any

from core.rule_activation import (
    _parse_hms,
    _resolve_limit_prices,
    _tick_time,
    first_ask_volume,
    is_limit_up_sealed,
)

CHOICE_LIMIT_CLEAR = "limit_clear"
CHOICE_OPEN_SELL = "open_sell"


def _rule_type_of(rule: dict) -> str:
    return (rule.get("type") or rule.get("rule_type") or "").strip()


def has_clear_branch(rule: dict) -> bool:
    if not isinstance(rule, dict):
        return False
    return bool(str(rule.get("clear_branch_at") or "").strip())


def clear_branch_is_resolved(rule: dict) -> bool:
    cb = rule.get("clear_branch") if isinstance(rule, dict) else None
    return isinstance(cb, dict) and bool(cb.get("resolved"))


def clear_branch_allows_trigger(rule: dict) -> bool:
    """未到点分支前不允许触发；分支完成后按最终规则类型正常触发。"""
    if not has_clear_branch(rule):
        return True
    return clear_branch_is_resolved(rule)


def clear_branch_chart_suffix(rule: dict) -> str:
    """图表节点第二行：待分支 / 已分支说明。"""
    if not has_clear_branch(rule):
        return ""
    at = str(rule.get("clear_branch_at") or "").strip()
    cb = rule.get("clear_branch") if isinstance(rule.get("clear_branch"), dict) else {}
    if not cb.get("resolved"):
        return f"\n{at}待分支·到点看封板"
    choice = str(cb.get("choice") or "")
    if choice == CHOICE_OPEN_SELL:
        return f"\n{at}已分支·开板突破卖"
    return f"\n{at}已分支·原清仓"


def normalize_clear_branch(rule: dict) -> None:
    """确保 clear_branch 状态字典存在（不改变 resolved）。"""
    if not has_clear_branch(rule):
        return
    cb = rule.get("clear_branch")
    if not isinstance(cb, dict):
        cb = {}
        rule["clear_branch"] = cb
    cb.setdefault("resolved", False)
    cb.setdefault("choice", None)
    cb.setdefault("sealed_at_T", None)
    if "orig_type" not in cb:
        cb["orig_type"] = _rule_type_of(rule)
    if "orig_name" not in cb:
        cb["orig_name"] = str(rule.get("name") or "")
    if "orig_price" not in cb:
        try:
            px = float(rule.get("price") or rule.get("trigger_price") or 0)
        except (TypeError, ValueError):
            px = 0.0
        cb["orig_price"] = px


def _limit_up_for_rule(
    rule: dict, tick_data: dict, stock_code: str, stock_name: str
) -> float:
    for key in ("limit_up",):
        try:
            v = float(rule.get(key) or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            return v
    cb = rule.get("clear_branch") if isinstance(rule.get("clear_branch"), dict) else {}
    try:
        v = float(cb.get("limit_up") or 0)
    except (TypeError, ValueError):
        v = 0.0
    if v > 0:
        return v
    lu, _ = _resolve_limit_prices(stock_code, tick_data, stock_name)
    return float(lu or 0)


def _apply_open_sell_branch(rule: dict, limit_up: float) -> None:
    """封板分支：改为涨停价突破卖出。"""
    normalize_clear_branch(rule)
    cb = rule["clear_branch"]
    cb["orig_type"] = cb.get("orig_type") or _rule_type_of(rule)
    cb["orig_name"] = cb.get("orig_name") or str(rule.get("name") or "")
    try:
        cb["orig_price"] = float(
            rule.get("price") or rule.get("trigger_price") or cb.get("orig_price") or 0
        )
    except (TypeError, ValueError):
        pass
    for k in (
        "trigger_price",
        "drop_percent",
        "pullback_price",
        "room_blend_start",
        "confirm_ticks",
        "cooldown_after_extreme_ticks",
        "dynamic_thresholds",
    ):
        rule.pop(k, None)
    rule["type"] = "breakthrough_sell"
    rule["rule_type"] = "breakthrough_sell"
    rule["price"] = round(float(limit_up), 4)
    rule["limit_up"] = round(float(limit_up), 4)
    orig_name = str(cb.get("orig_name") or rule.get("name") or "清仓").strip()
    rule["name"] = f"{orig_name}·开板突破卖"
    cb["resolved"] = True
    cb["choice"] = CHOICE_OPEN_SELL
    cb["sealed_at_T"] = True
    cb["limit_up"] = round(float(limit_up), 4)


def _apply_limit_clear_branch(rule: dict, *, sealed: bool) -> None:
    """未封板：保留原清仓规则类型与价格。"""
    normalize_clear_branch(rule)
    cb = rule["clear_branch"]
    cb["resolved"] = True
    cb["choice"] = CHOICE_LIMIT_CLEAR
    cb["sealed_at_T"] = bool(sealed)


def process_clear_branch_tick(
    rule: dict,
    tick_data: dict,
    *,
    stock_code: str,
    stock_name: str = "",
) -> bool:
    """
    对单条带 clear_branch_at 的规则处理一个 tick。
    返回 True 表示规则有变更（需持久化 / 刷新图表 / 同步武装）。
    """
    if not isinstance(rule, dict):
        return False
    if not rule.get("enabled", True):
        return False
    if rule.get("executed", False):
        return False
    if not has_clear_branch(rule):
        return False

    normalize_clear_branch(rule)
    cb = rule["clear_branch"]
    if cb.get("resolved"):
        return False

    activate_at = _parse_hms(rule.get("clear_branch_at"))
    if activate_at is None:
        _apply_limit_clear_branch(rule, sealed=False)
        return True

    tick_dt = _tick_time(tick_data)
    if tick_dt.time() < activate_at:
        return False

    current_price = float(tick_data.get("lastPrice") or 0)
    if current_price <= 0:
        return False

    limit_up = _limit_up_for_rule(rule, tick_data, stock_code, stock_name)
    if limit_up <= 0:
        return False

    ask_vol = first_ask_volume(tick_data)
    sealed = bool(is_limit_up_sealed(current_price, limit_up, ask_vol))
    if sealed:
        _apply_open_sell_branch(rule, limit_up)
    else:
        _apply_limit_clear_branch(rule, sealed=False)
        rule["limit_up"] = round(float(limit_up), 4)
        cb["limit_up"] = round(float(limit_up), 4)
    return True


def process_clear_branch_for_task(task: dict, tick_data: dict) -> bool:
    """处理任务内所有 clear_branch 规则。返回是否有变更。"""
    if not isinstance(task, dict):
        return False
    stock_code = str(task.get("stock_code") or tick_data.get("stock_code") or "")
    stock_name = str(task.get("stock_name") or "")
    params = task.get("params")
    if not isinstance(params, dict):
        return False
    rules = params.get("rules")
    if not isinstance(rules, list):
        return False
    changed = False
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if process_clear_branch_tick(
            rule, tick_data, stock_code=stock_code, stock_name=stock_name
        ):
            changed = True
    return changed
