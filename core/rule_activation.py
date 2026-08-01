# -*- coding: utf-8 -*-
"""单点卖出 / 突破卖出的可配置延迟激活（观察期 + 触价/封板条件）。"""
from __future__ import annotations

import math
from datetime import datetime, time as dt_time
from typing import Any, Dict, List, Optional, Tuple

from core.scheduled_clear_manager import calculate_limit_prices

ACTIVATION_ELIGIBLE_TYPES = frozenset({"single_sell", "breakthrough_sell"})


def _rule_type_of(rule: dict) -> str:
    return (rule.get("type") or rule.get("rule_type") or "").strip()


MODE_SUPPRESS = "suppress_if_reached"
MODE_REQUIRE = "require_if_reached"
CHECK_PRICE = "price"
CHECK_LIMIT_SEALED = "limit_sealed"

DEFAULT_SEAL_CONSECUTIVE = 2
PRICE_TOUCH_EPS = 0.011


def first_ask_volume(tick_or_row: Any) -> int:
    """取卖一量；兼容 askVol / askVolume 为标量或列表。"""
    if tick_or_row is None:
        return 0
    getter = tick_or_row.get if hasattr(tick_or_row, "get") else None
    raw = None
    if getter is not None:
        raw = getter("askVol")
        if raw is None:
            raw = getter("askVolume")
    else:
        try:
            raw = tick_or_row["askVol"]
        except Exception:
            raw = None
    return _first_volume(raw)


def is_limit_up_sealed(
    last_price: float,
    limit_up: float,
    ask_vol: int = 0,
    *,
    eps: float = PRICE_TOUCH_EPS,
) -> bool:
    """
    涨停封板：现价贴涨停且卖一量为 0（无卖盘）。
    与 activation 的 limit_sealed 判定口径一致（不含连续 tick 计数）。
    """
    try:
        lp = float(last_price or 0)
        lu = float(limit_up or 0)
    except (TypeError, ValueError):
        return False
    if lu <= 0 or lp <= 0:
        return False
    if lp < lu - float(eps):
        return False
    return int(ask_vol or 0) <= 0


def _parse_hms(value: Any) -> Optional[dt_time]:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        parts = s.split(":")
        if len(parts) == 2:
            h, m = int(parts[0]), int(parts[1])
            return dt_time(h, m, 0)
        if len(parts) >= 3:
            h, m, sec = int(parts[0]), int(parts[1]), int(parts[2])
            return dt_time(h, m, sec)
    except (TypeError, ValueError):
        return None
    return None


def _tick_time(tick_data: dict) -> datetime:
    t = tick_data.get("time")
    if isinstance(t, datetime):
        return t
    return datetime.now()


def _first_volume(raw) -> int:
    if isinstance(raw, (list, tuple)) and raw:
        try:
            return int(raw[0] or 0)
        except (TypeError, ValueError):
            return 0
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _resolve_limit_prices(stock_code: str, tick_data: dict, stock_name: str = "") -> Tuple[float, float]:
    pre_close = float(
        tick_data.get("lastClose")
        or tick_data.get("preClose")
        or tick_data.get("pre_close")
        or 0
    )
    if pre_close <= 0:
        return 0.0, 0.0
    return calculate_limit_prices(stock_code, pre_close, stock_name)


def has_activation_config(rule: dict) -> bool:
    act = rule.get("activation")
    if not isinstance(act, dict):
        return False
    return _parse_hms(act.get("activate_at")) is not None


def normalize_activation(rule: dict) -> None:
    """补齐 activation 字段；无 activate_at 时视为立即生效。"""
    if _rule_type_of(rule) not in ACTIVATION_ELIGIBLE_TYPES:
        return
    act = rule.get("activation")
    if not isinstance(act, dict):
        return
    activate_at = _parse_hms(act.get("activate_at"))
    if activate_at is None:
        act["activated"] = True
        act["resolved"] = True
        return
    act.setdefault("mode", MODE_SUPPRESS)
    act.setdefault("check", CHECK_PRICE)
    act.setdefault("reached", False)
    act.setdefault("seal_streak", 0)
    act.setdefault("resolved", False)
    act.setdefault("activated", False)
    level = act.get("level")
    if level is not None:
        try:
            act["level"] = round(float(level), 4)
        except (TypeError, ValueError):
            act["level"] = None


def rule_activation_allows_trigger(rule: dict) -> bool:
    """是否已过激活决策且允许参与触发检查。"""
    if _rule_type_of(rule) not in ACTIVATION_ELIGIBLE_TYPES:
        return True
    if not rule.get("enabled", True):
        return False
    if not has_activation_config(rule):
        return True
    act = rule.get("activation") or {}
    if not act.get("resolved", False):
        return False
    return bool(act.get("activated", False))


def rule_activation_status_label(rule: dict) -> str:
    if not has_activation_config(rule):
        return ""
    act = rule.get("activation") or {}
    if not act.get("resolved", False):
        at = str(act.get("activate_at") or "")
        return f"待激活({at})"
    if act.get("activated", False):
        return "已激活"
    return "已跳过"


def rule_activation_condition_label(mode: str, check: str) -> str:
    """激活条件的中文简述（图表/列表用）。"""
    mode = str(mode or MODE_SUPPRESS).strip()
    check = str(check or CHECK_PRICE).strip()
    if mode == MODE_SUPPRESS:
        if check == CHECK_LIMIT_SEALED:
            return "早封则跳过"
        return "触价则跳过"
    if check == CHECK_LIMIT_SEALED:
        return "须封板"
    return "须触价"


def rule_activation_detail_text(rule: dict) -> str:
    """单行激活说明，供规则列表等使用。"""
    if not has_activation_config(rule):
        return ""
    act = rule.get("activation") or {}
    at = str(act.get("activate_at") or "").strip()
    cond = rule_activation_condition_label(act.get("mode"), act.get("check"))
    status = rule_activation_status_label(rule)
    if status:
        return f"激活{at}·{cond}·{status}"
    return f"激活{at}·{cond}"


def rule_activation_chart_suffix(rule: dict) -> str:
    """图表节点第二行：激活时刻与条件（含已决状态）。"""
    if not has_activation_config(rule):
        return ""
    act = rule.get("activation") or {}
    at = str(act.get("activate_at") or "").strip()
    cond = rule_activation_condition_label(act.get("mode"), act.get("check"))
    if not act.get("resolved", False):
        return f"\n{at}激活·{cond}"
    if act.get("activated", False):
        return f"\n{at}激活·{cond}·已激活"
    return f"\n{at}激活·{cond}·已跳过"


def _resolve_level(rule: dict, tick_data: dict, stock_code: str, stock_name: str) -> float:
    act = rule.get("activation") or {}
    level = act.get("level")
    try:
        lv = float(level or 0)
    except (TypeError, ValueError):
        lv = 0.0
    if lv > 0:
        return lv
    check = str(act.get("check") or CHECK_PRICE).strip()
    if check == CHECK_LIMIT_SEALED:
        limit_up, _ = _resolve_limit_prices(stock_code, tick_data, stock_name)
        return limit_up
    return 0.0


def _update_reached(rule: dict, tick_data: dict, *, stock_code: str, stock_name: str = "") -> None:
    act = rule.get("activation") or {}
    if act.get("resolved", False):
        return
    current_price = float(tick_data.get("lastPrice") or 0)
    if current_price <= 0:
        return
    level = _resolve_level(rule, tick_data, stock_code, stock_name)
    if level <= 0:
        return
    check = str(act.get("check") or CHECK_PRICE).strip()
    reached_now = False
    if check == CHECK_LIMIT_SEALED:
        limit_up, _ = _resolve_limit_prices(stock_code, tick_data, stock_name)
        if limit_up <= 0:
            limit_up = _resolve_level(rule, tick_data, stock_code, stock_name)
        if limit_up <= 0:
            return
        ask_vol = _first_volume(tick_data.get("askVol") or tick_data.get("askVolume"))
        sealed_px = current_price >= limit_up - PRICE_TOUCH_EPS
        if sealed_px and ask_vol == 0:
            streak = int(act.get("seal_streak") or 0) + 1
            act["seal_streak"] = streak
            if streak >= DEFAULT_SEAL_CONSECUTIVE:
                reached_now = True
        else:
            act["seal_streak"] = 0
    else:
        reached_now = current_price + 1e-9 >= level - PRICE_TOUCH_EPS
    if reached_now:
        act["reached"] = True


def _resolve_activation(rule: dict, *, tick_dt: datetime, stock_code: str) -> bool:
    """到 activate_at 后做一次激活决策。返回 state 是否变化。"""
    act = rule.get("activation") or {}
    if act.get("resolved", False):
        return False
    activate_at = _parse_hms(act.get("activate_at"))
    if activate_at is None:
        act["activated"] = True
        act["resolved"] = True
        return True
    if tick_dt.time() < activate_at:
        return False

    reached = bool(act.get("reached", False))
    mode = str(act.get("mode") or MODE_SUPPRESS).strip()
    if mode == MODE_REQUIRE:
        act["activated"] = reached
    else:
        act["activated"] = not reached
    act["resolved"] = True
    act["seal_streak"] = 0
    return True


def process_activation_tick(
    rule: dict,
    tick_data: dict,
    *,
    stock_code: str,
    stock_name: str = "",
) -> bool:
    """
    对单条规则处理一个 tick 的激活逻辑。
    返回 True 表示 rule.activation 状态有变更（需持久化）。
    """
    if _rule_type_of(rule) not in ACTIVATION_ELIGIBLE_TYPES:
        return False
    if not rule.get("enabled", True):
        return False
    if rule.get("executed", False):
        return False
    if not has_activation_config(rule):
        return False

    normalize_activation(rule)
    act = rule.setdefault("activation", {})
    tick_dt = _tick_time(tick_data)
    activate_at = _parse_hms(act.get("activate_at"))
    if activate_at is None:
        return False

    changed = False
    if tick_dt.time() < activate_at:
        before = bool(act.get("reached", False))
        _update_reached(rule, tick_data, stock_code=stock_code, stock_name=stock_name)
        if bool(act.get("reached", False)) != before:
            changed = True
        if int(act.get("seal_streak") or 0) != 0 and not act.get("reached", False):
            changed = True
    else:
        if _resolve_activation(rule, tick_dt=tick_dt, stock_code=stock_code):
            changed = True
    return changed


def process_activation_for_task(task: dict, tick_data: dict) -> bool:
    """处理任务内所有可激活规则。返回是否有变更。"""
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
        if process_activation_tick(
            rule, tick_data, stock_code=stock_code, stock_name=stock_name
        ):
            changed = True
    return changed


def build_activation_dict(
    *,
    activate_at: Optional[str] = None,
    mode: str = MODE_SUPPRESS,
    level: Optional[float] = None,
    check: str = CHECK_PRICE,
    activated: Optional[bool] = None,
) -> Dict[str, Any]:
    """构造 activation 配置（策略生成 / UI 使用）。"""
    out: Dict[str, Any] = {
        "mode": mode,
        "check": check,
        "reached": False,
        "seal_streak": 0,
        "resolved": False,
    }
    if activate_at:
        out["activate_at"] = str(activate_at).strip()
    if level is not None and float(level or 0) > 0:
        out["level"] = round(float(level), 4)
    if activated is not None:
        out["activated"] = bool(activated)
    elif activate_at:
        out["activated"] = False
    else:
        out["activated"] = True
        out["resolved"] = True
    return out
