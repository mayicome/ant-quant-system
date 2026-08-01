# -*- coding: utf-8 -*-
"""MA5 价格带 + 硬pass 共用工具（生成任务 / 图上手工 / 实盘判定）。"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def rule_has_price_band(rule: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(rule, dict):
        return False
    try:
        lo = float(
            rule.get("band_low")
            if rule.get("band_low") is not None
            else (rule.get("price_low") or 0)
        )
        hi = float(
            rule.get("band_high")
            if rule.get("band_high") is not None
            else (rule.get("price_high") or 0)
        )
    except (TypeError, ValueError):
        return False
    return lo > 0 and hi > 0 and hi >= lo


def get_price_band(rule: Dict[str, Any]) -> Tuple[float, float]:
    lo = float(
        rule.get("band_low")
        if rule.get("band_low") is not None
        else (rule.get("price_low") or 0)
    )
    hi = float(
        rule.get("band_high")
        if rule.get("band_high") is not None
        else (rule.get("price_high") or 0)
    )
    return lo, hi


def get_band_accept_low(rule: Dict[str, Any]) -> Optional[float]:
    raw = rule.get("band_accept_low")
    if raw is None or str(raw).strip() == "":
        raw = rule.get("accept_band_low")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        alo = float(raw)
    except (TypeError, ValueError):
        return None
    return alo if alo > 0 else None


def extract_best_ask(tick_or_row: Any) -> Optional[float]:
    """从 tick/dict/row 取卖一；无效则 None。"""
    if tick_or_row is None:
        return None
    ask = None
    if isinstance(tick_or_row, dict):
        raw = tick_or_row.get("askPrice")
        if raw is None:
            raw = tick_or_row.get("ask_price")
        if isinstance(raw, (list, tuple)) and raw:
            ask = raw[0]
        else:
            ask = raw
    else:
        for key in ("askPrice", "ask_price"):
            if hasattr(tick_or_row, "get"):
                try:
                    raw = tick_or_row.get(key)
                except Exception:
                    raw = None
            else:
                raw = getattr(tick_or_row, key, None)
            if raw is None:
                continue
            if isinstance(raw, (list, tuple)) and raw:
                ask = raw[0]
            else:
                ask = raw
            break
    try:
        v = float(ask)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def estimate_band_buy_ref_price(
    last_price: float,
    tick_or_row: Any = None,
    *,
    best_ask: Optional[float] = None,
    slippage: float = 0.0,
    precision: int = 2,
) -> float:
    """
    价格带硬上沿判定用的买入参考价：卖一(+滑点)，无卖一则用现价。
    与回测 _calc_fill_price / 图上抢卖一逻辑对齐：死卡 MA5 时用此价与 band_high 比。
    """
    try:
        px = float(last_price or 0)
    except (TypeError, ValueError):
        px = 0.0
    ask = best_ask
    if ask is None or float(ask or 0) <= 0:
        ask = extract_best_ask(tick_or_row)
    try:
        base = float(ask) if ask is not None and float(ask) > 0 else px
    except (TypeError, ValueError):
        base = px
    try:
        slip = float(slippage or 0)
    except (TypeError, ValueError):
        slip = 0.0
    if slip < 0:
        slip = 0.0
    try:
        return round(float(base) + slip, int(precision))
    except (TypeError, ValueError):
        return float(base) + slip


def band_hard_pass_reason(
    *,
    last_price: float,
    band_low: float,
    band_high: float,
    accept_low: Optional[float] = None,
    buy_ref_price: Optional[float] = None,
) -> Optional[str]:
    """
    首次真突破后的硬pass原因；无需硬pass 则 None。
    - 现价 < 有效下沿（深位）
    - 买入参考价（卖一/成交预估）> 硬上沿 band_high（默认即 MA5）
    """
    try:
        px = float(last_price)
    except (TypeError, ValueError):
        return None
    try:
        blo = float(band_low)
        bhi = float(band_high)
    except (TypeError, ValueError):
        return None
    ref = buy_ref_price
    try:
        ref_px = float(ref) if ref is not None and float(ref) > 0 else px
    except (TypeError, ValueError):
        ref_px = px

    if accept_low is not None:
        try:
            alo = float(accept_low)
        except (TypeError, ValueError):
            alo = None
        if alo is not None and alo > 0 and px + 1e-12 < alo:
            return (
                f"首次真突破放弃: 现价={px:.2f}<有效下沿={alo:.2f}"
                f"（监控带[{blo:.2f},{bhi:.2f}]）"
            )
    if bhi > 0 and ref_px > bhi + 1e-12:
        return (
            f"首次真突破放弃: 买入参考价={ref_px:.2f}>硬上沿MA5={bhi:.2f}"
            f"（现价={px:.2f}，监控带[{blo:.2f},{bhi:.2f}]）"
        )
    return None


def build_ma5_band_fields(
    ma5: float,
    *,
    band_pct: float = 0.03,
    accept_pct: float = 0.01,
    limit_up: float = 0.0,
    limit_down: float = 0.0,
    precision: int = 2,
) -> Optional[Dict[str, float]]:
    """由 MA5 与带宽生成 band_low/high/accept_low 与 price(=上沿)。"""
    try:
        ma5 = float(ma5)
        band_pct = float(band_pct)
        accept_pct = float(accept_pct)
    except (TypeError, ValueError):
        return None
    if ma5 <= 0:
        return None
    if band_pct < 0:
        band_pct = 0.0
    if band_pct > 0.2:
        band_pct = 0.2
    if accept_pct < 0:
        accept_pct = 0.0
    if accept_pct > band_pct:
        accept_pct = band_pct

    raw_high = round(ma5, precision)
    raw_low = round(ma5 * (1.0 - band_pct), precision)
    raw_accept = round(ma5 * (1.0 - accept_pct), precision)

    band_high = raw_high
    band_low = raw_low
    if limit_up and limit_up > 0:
        band_high = min(band_high, round(float(limit_up), precision))
    if limit_down and limit_down > 0:
        band_low = max(band_low, round(float(limit_down), precision))
    if band_low > band_high:
        return None

    band_accept_low = min(max(raw_accept, band_low), band_high)
    return {
        "price": band_high,
        "band_low": band_low,
        "band_high": band_high,
        "band_accept_low": band_accept_low,
    }


def stamp_band_breakthrough_defaults(rule: Dict[str, Any]) -> Dict[str, Any]:
    """价格带规则：强制真突破 window、不要求先跌破、不走试探。"""
    if not rule_has_price_band(rule):
        return rule
    rule["require_true_breakthrough"] = True
    rule["true_breakthrough_cond1_mode"] = str(
        rule.get("true_breakthrough_cond1_mode") or "window"
    ).strip() or "window"
    if rule.get("true_breakthrough_window_sec") is None:
        rule["true_breakthrough_window_sec"] = 45
    rule["require_break_below"] = False
    return rule
