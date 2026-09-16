# -*- coding: utf-8 -*-
"""买入规则：相对昨收涨幅超限则停止未执行买入。

规则字段：
  halt_on_open_gain: True 时启用（历史字段名保留，语义为昨收涨幅熔断）
落状态：
  enabled=False
  halt_reason=\"open_gain\"
  halt_detail: 可读说明
阈值：主板 5%，其它板块 10%。
口径：max(最高, 现价) 相对昨收涨幅；任何时候达到阈值即熔断。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

BUY_RULE_TYPES_OPEN_GAIN_HALT = frozenset(
    {
        "single_buy",
        "best_buy",
        "breakthrough_buy",
        "cage_buy",
        "grid_buy",
    }
)

HALT_REASON_OPEN_GAIN = "open_gain"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return float(value) != 0.0
    s = str(value).strip().lower()
    return s in ("1", "true", "yes", "y", "on", "是")


def rule_wants_open_gain_halt(rule: Any) -> bool:
    if not isinstance(rule, dict):
        return False
    rtype = str(rule.get("type") or rule.get("rule_type") or "").strip()
    if rtype not in BUY_RULE_TYPES_OPEN_GAIN_HALT:
        return False
    return _truthy(rule.get("halt_on_open_gain"))


def open_gain_halt_threshold_pct(
    stock_code: str = "",
    stock_name: str = "",
) -> float:
    """返回百分比阈值（如 5.0 表示 5%）。主板 5，其它 10。"""
    try:
        from utils.limit_ratio import is_main_board

        if is_main_board(stock_code or ""):
            return 5.0
    except Exception:
        code = str(stock_code or "").strip()
        if "." in code:
            code = code.split(".", 1)[0]
        digits = "".join(c for c in code if c.isdigit())
        code6 = digits.zfill(6)[-6:] if digits else ""
        if code6 and not code6.startswith(("300", "301", "688", "689", "8", "4", "920")):
            return 5.0
    return 10.0


def _f(v: Any) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    if x != x:  # NaN
        return 0.0
    return x


def resolve_prev_close_and_ref_price(
    *,
    prev_close: Any = None,
    last_price: Any = None,
    high_price: Any = None,
    tick_or_row: Any = None,
) -> Tuple[float, float]:
    """返回 (昨收, 用于涨幅的参考价=max(最高,现价))。"""
    prev = _f(prev_close)
    last = _f(last_price)
    high = _f(high_price)
    getter = None
    if tick_or_row is not None and hasattr(tick_or_row, "get"):
        getter = tick_or_row.get
    if getter is not None:
        if prev <= 0:
            for k in (
                "lastClose",
                "last_close",
                "preClose",
                "prevClose",
                "pre_close",
                "prev_close",
                "昨收盘价",
                "昨收",
            ):
                prev = _f(getter(k))
                if prev > 0:
                    break
        if last <= 0:
            for k in ("lastPrice", "last_price", "price", "last", "最新价"):
                last = _f(getter(k))
                if last > 0:
                    break
        if high <= 0:
            for k in ("high", "highPrice", "todayHigh", "今日最高"):
                high = _f(getter(k))
                if high > 0:
                    break
    ref = max(high, last)
    return prev, ref


# 兼容旧调用名（曾按今开解析）
def resolve_open_and_ref_price(
    *,
    open_price: Any = None,
    last_price: Any = None,
    high_price: Any = None,
    tick_or_row: Any = None,
    prev_close: Any = None,
) -> Tuple[float, float]:
    """兼容旧名：实际返回 (昨收, max(最高,现价))。open_price 忽略。"""
    return resolve_prev_close_and_ref_price(
        prev_close=prev_close,
        last_price=last_price,
        high_price=high_price,
        tick_or_row=tick_or_row,
    )


def open_gain_pct(prev_close: float, ref_price: float) -> Optional[float]:
    """相对昨收涨幅（百分比）。无法计算时返回 None。"""
    prev = _f(prev_close)
    r = _f(ref_price)
    if prev <= 0 or r <= 0:
        return None
    return (r / prev - 1.0) * 100.0


def should_halt_on_open_gain(
    rule: Any,
    *,
    stock_code: str = "",
    stock_name: str = "",
    prev_close: Any = None,
    last_price: Any = None,
    high_price: Any = None,
    tick_or_row: Any = None,
    open_price: Any = None,  # 兼容旧调用，忽略
) -> Tuple[bool, str]:
    """是否应因相对昨收涨幅超限停止该买入规则。

    返回 (should_halt, detail)。
    """
    if not rule_wants_open_gain_halt(rule):
        return False, ""
    if isinstance(rule, dict):
        if rule.get("executed"):
            return False, ""
        if str(rule.get("halt_reason") or "").strip() == HALT_REASON_OPEN_GAIN:
            return False, ""  # 已停过
        if rule.get("enabled") is False and rule.get("halt_reason"):
            return False, ""

    prev, ref = resolve_prev_close_and_ref_price(
        prev_close=prev_close,
        last_price=last_price,
        high_price=high_price,
        tick_or_row=tick_or_row,
    )
    gain = open_gain_pct(prev, ref)
    if gain is None:
        return False, ""
    thr = open_gain_halt_threshold_pct(stock_code, stock_name)
    if gain + 1e-9 < thr:
        return False, ""
    detail = (
        f"相对昨收涨幅 {gain:.2f}% ≥ {thr:.0f}% "
        f"(昨收={prev:.4f}, 参={ref:.4f})"
    )
    return True, detail


def apply_open_gain_halt(rule: Dict[str, Any], detail: str = "") -> bool:
    """写入停止状态。返回是否本次新写入。"""
    if not isinstance(rule, dict):
        return False
    if str(rule.get("halt_reason") or "").strip() == HALT_REASON_OPEN_GAIN:
        return False
    if rule.get("executed"):
        return False
    rule["enabled"] = False
    rule["halt_reason"] = HALT_REASON_OPEN_GAIN
    if detail:
        rule["halt_detail"] = str(detail)
    return True
