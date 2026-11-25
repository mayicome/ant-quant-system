# -*- coding: utf-8 -*-
"""强势留半：纯函数，供运行时与单测共用。"""

from typing import Any, Dict, Optional, Tuple


def as_float(val: Any, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def as_int(val: Any, default: int = 0) -> int:
    if val is None or val == "":
        return default
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def full_lot(shares: int) -> int:
    shares = int(shares or 0)
    if shares < 100:
        return 0
    return (shares // 100) * 100


def half_lot(shares: int) -> int:
    total = full_lot(shares)
    if total < 100:
        return 0
    return max(100, int(total * 0.5 / 100) * 100)


def half_remaining_volume(
    avail: int,
    price: float = 0.0,
    min_order_amount: float = 5000.0,
) -> Tuple[int, str]:
    """到期强势：卖剩余仓的一半；剩余不够一手或不够最小金额则并入全卖。"""
    avail = full_lot(avail)
    if avail < 100:
        return 0, "no_avail"
    half = half_lot(avail)
    remain = avail - half
    if remain < 100:
        return avail, "absorb_lot"
    if min_order_amount > 0 and price > 0 and remain * float(price) < float(min_order_amount):
        return avail, "absorb_min_amount"
    if half < 100:
        return avail, "absorb_lot"
    return half, "half"


def classify_strength(
    last_price: float,
    cost_price: float,
    period_high: float,
    profit_keep_pct: float = 6.0,
    trail_pullback_pct: float = 5.0,
) -> Dict[str, Any]:
    """到期 14:56：浮盈>=门槛 且 相对持有期最高价回撤<=幅度 → 强势。成本缺失当弱势。"""
    lp = as_float(last_price)
    cost = as_float(cost_price)
    hp = as_float(period_high)
    if hp <= 0:
        hp = lp
    profit_pct = 0.0
    if cost > 0 and lp > 0:
        profit_pct = (lp - cost) / cost * 100.0
    pullback_pct = 0.0
    if hp > 0 and lp > 0:
        pullback_pct = (hp - lp) / hp * 100.0
    if cost <= 0 or lp <= 0:
        strong = False
        reason = "no_cost_or_price"
    else:
        strong = (profit_pct >= float(profit_keep_pct) - 1e-12) and (
            pullback_pct <= float(trail_pullback_pct) + 1e-12
        )
        reason = "strong" if strong else "weak"
    return {
        "strong": bool(strong),
        "reason": reason,
        "profit_pct": profit_pct,
        "pullback_pct": pullback_pct,
        "last_price": lp,
        "cost_price": cost,
        "period_high": hp,
        "clear_volume_mode": "half_remaining" if strong else "remaining",
    }


def eval_no_new_high(
    period_high: Optional[float],
    period_high_prev: Optional[float],
    streak: int,
    need_days: int = 3,
) -> Dict[str, Any]:
    """14:56：今天最高价严格高于此前持有期最高价则连续天数归零，否则 +1。
    若没有此前高点，当天不改 streak、也不清仓。"""
    hp = as_float(period_high) if period_high is not None else 0.0
    prev = as_float(period_high_prev) if period_high_prev is not None else 0.0
    try:
        streak = int(streak or 0)
    except (TypeError, ValueError):
        streak = 0
    if streak < 0:
        streak = 0
    try:
        need = int(need_days or 3)
    except (TypeError, ValueError):
        need = 3
    if need < 1:
        need = 1
    if prev <= 0 or hp <= 0:
        return {
            "streak": streak,
            "is_new_high": False,
            "should_clear": False,
            "reason": "no_prev_high",
            "need_days": need,
        }
    if hp > prev + 1e-12:
        return {
            "streak": 0,
            "is_new_high": True,
            "should_clear": False,
            "reason": "new_high_reset",
            "need_days": need,
        }
    new_streak = streak + 1
    return {
        "streak": new_streak,
        "is_new_high": False,
        "should_clear": new_streak >= need,
        "reason": "no_new_high",
        "need_days": need,
    }


def tick_session_high(row: Optional[Dict[str, Any]], last_price: float) -> float:
    """用本笔最新价和行情里的当日最高，取较大值。"""
    hi = as_float(last_price)
    if not isinstance(row, dict):
        return hi
    for key in ("high", "highPrice", "todayHigh", "high_price"):
        v = as_float(row.get(key))
        if v > hi:
            hi = v
    return hi


def rollover_and_update_high(
    state: Dict[str, Any],
    *,
    today: str,
    session_high: float,
    seed_high: float = 0.0,
) -> Dict[str, Any]:
    """跨日时把截至昨收的最高价固化为 period_high_prev，再把今天的高点写入 period_high。"""
    st = dict(state or {})
    today = str(today or "").strip()[:10]
    prev_date = str(st.get("high_date") or "").strip()[:10]
    period_high = as_float(st.get("period_high"))
    period_high_prev = as_float(st.get("period_high_prev"))
    if seed_high > 0:
        if period_high <= 0 or seed_high > period_high:
            # 仅在尚无当日高点、或种子更高时抬升；跨日已固化的 prev 不被今日种子抬错
            if prev_date == today or not prev_date:
                period_high = max(period_high, seed_high)
            else:
                period_high = max(period_high, seed_high)
        if period_high_prev <= 0:
            period_high_prev = seed_high
    if today and prev_date and prev_date != today:
        if period_high > 0:
            period_high_prev = period_high
        prev_date = today
    elif today and not prev_date:
        prev_date = today
        if period_high_prev <= 0 and seed_high > 0:
            period_high_prev = seed_high
    sess = as_float(session_high)
    if sess > period_high:
        period_high = sess
    if seed_high > period_high:
        period_high = seed_high
    st["period_high"] = period_high if period_high > 0 else None
    st["period_high_prev"] = period_high_prev if period_high_prev > 0 else None
    st["high_date"] = today
    if "no_new_high_streak" not in st:
        st["no_new_high_streak"] = 0
    if "last_eval_date" not in st:
        st["last_eval_date"] = ""
    st["kind"] = "keep_half"
    return st
