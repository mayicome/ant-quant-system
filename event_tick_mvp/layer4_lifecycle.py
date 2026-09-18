# -*- coding: utf-8 -*-
"""第 4 层：买入触发 + 持仓生命周期（T+1 / 止盈止损 / 递延）。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from event_tick_mvp.config import MvpConfig
from event_tick_mvp.types import (
    AccountState,
    FillRecord,
    OrderIntent,
    Position,
    WatchItem,
)


def mark_to_market_nav(account: AccountState, last_prices: Dict[str, float]) -> float:
    equity = float(account.cash)
    for code, pos in account.positions.items():
        px = float(last_prices.get(code) or pos.entry_price or 0.0)
        equity += px * int(pos.volume)
    account.nav = equity
    return equity


def in_buy_price_band(
    last_price: float,
    prev_close: float,
    *,
    alpha: float,
    beta: float,
) -> bool:
    if prev_close <= 0 or last_price <= 0:
        return False
    lo = prev_close * (1.0 - float(alpha))
    hi = prev_close * (1.0 + float(beta))
    return lo <= last_price <= hi


def plan_buy_volume(nav: float, price: float, cfg: MvpConfig) -> int:
    if price <= 0 or nav <= 0:
        return 0
    budget = float(cfg.max_weight) * float(nav)
    shares = int(budget // (price * 100)) * 100
    return max(0, shares)


def _limit_bound(code: str, name: str, prev_close: float, as_of: date) -> Tuple[float, float]:
    try:
        from utils.limit_ratio import get_limit_ratio

        r = float(get_limit_ratio(code, name, as_of))
    except Exception:
        r = 0.10
    return round(prev_close * (1 + r), 2), round(prev_close * (1 - r), 2)


def is_limit_up(price: float, limit_up: float, ask1: Optional[float] = None) -> bool:
    if limit_up <= 0 or price <= 0:
        return False
    if ask1 is not None and float(ask1) <= 0:
        return True
    return price >= limit_up * 0.998


def is_limit_down(price: float, limit_down: float, bid1: Optional[float] = None) -> bool:
    if limit_down <= 0 or price <= 0:
        return False
    if bid1 is not None and float(bid1) <= 0:
        return True
    return price <= limit_down * 1.002


def try_open_buy(
    item: WatchItem,
    *,
    last_price: float,
    ts: datetime,
    account: AccountState,
    cfg: MvpConfig,
    ask1: Optional[float] = None,
    name: str = "",
) -> Optional[OrderIntent]:
    if not in_buy_price_band(
        last_price, item.prev_close, alpha=cfg.buy_alpha, beta=cfg.buy_beta
    ):
        return None
    lu, _ld = _limit_bound(item.code, name, item.prev_close, item.as_of)
    if is_limit_up(last_price, lu, ask1):
        return OrderIntent(
            side="buy",
            code=item.code,
            volume=0,
            reason="defer_buy_limit_up",
            created_at=ts,
            deferred=True,
            meta={"prev_close": item.prev_close, "industry": item.industry},
        )
    nav = mark_to_market_nav(account, {item.code: last_price})
    vol = plan_buy_volume(nav, last_price, cfg)
    if vol < 100:
        return None
    fee_rate = float(cfg.round_trip_cost) * 0.5
    need = vol * last_price * (1.0 + fee_rate)
    if need > float(account.cash):
        return None
    return OrderIntent(
        side="buy",
        code=item.code,
        volume=vol,
        reason="oversold_pullback_buy",
        created_at=ts,
        meta={"prev_close": item.prev_close, "industry": item.industry},
    )


def eval_exit_signal(
    pos: Position,
    *,
    last_price: float,
    trade_date: date,
    cfg: MvpConfig,
) -> Optional[str]:
    if pos.entry_date >= trade_date:
        return None
    if last_price <= 0 or pos.entry_price <= 0:
        return None
    ret = last_price / float(pos.entry_price) - 1.0
    if ret >= float(cfg.take_profit):
        return "take_profit"
    if ret <= -float(cfg.stop_loss):
        return "stop_loss"
    if int(pos.hold_days_counter) >= int(cfg.max_hold_trading_days):
        return "max_hold"
    return None


def on_bar_for_positions(
    account: AccountState,
    *,
    last_prices: Dict[str, float],
    trade_date: date,
    ts: datetime,
    cfg: MvpConfig,
    bid1_by_code: Optional[Dict[str, float]] = None,
    names: Optional[Dict[str, str]] = None,
) -> List[OrderIntent]:
    bid1_by_code = bid1_by_code or {}
    names = names or {}
    intents: List[OrderIntent] = []
    for code, pos in list(account.positions.items()):
        if pos.entry_date >= trade_date:
            continue
        px = float(last_prices.get(code) or 0.0)
        reason = eval_exit_signal(pos, last_price=px, trade_date=trade_date, cfg=cfg)
        if not reason:
            continue
        # 跌停则递延
        prev = float(pos.entry_price)
        # 用昨收近似：持仓 meta 可存；此处用 entry 附近不够，改用当日价相对跌停需昨收
        # 简化：若 bid1==0 视为跌停
        bid1 = bid1_by_code.get(code)
        deferred = bid1 is not None and float(bid1) <= 0
        intents.append(
            OrderIntent(
                side="sell",
                code=code,
                volume=int(pos.volume),
                reason=("defer_sell_limit_down" if deferred else reason),
                created_at=ts,
                deferred=deferred,
                meta={"exit_reason": reason, "name": names.get(code, "")},
            )
        )
    return intents


def eod_update_positions(
    account: AccountState,
    *,
    trade_date: date,
    last_prices: Dict[str, float],
) -> None:
    for _code, pos in account.positions.items():
        if pos.entry_date < trade_date:
            pos.hold_days_counter = int(pos.hold_days_counter) + 1
            pos.available_to_sell = True
        px = float(last_prices.get(pos.stock_code) or 0.0)
        if px > 0 and pos.entry_price > 0:
            ret = px / float(pos.entry_price) - 1.0
            pos.max_profit = max(float(pos.max_profit), ret)
            pos.max_loss = min(float(pos.max_loss), ret)
    mark_to_market_nav(account, last_prices)


def apply_fill(account: AccountState, fill: FillRecord, *, industry: str = "") -> None:
    if fill.side == "buy":
        cost = float(fill.price) * int(fill.volume) + float(fill.cost)
        account.cash -= cost
        account.positions[fill.code] = Position(
            stock_code=fill.code,
            entry_time=fill.ts,
            entry_price=float(fill.price),
            volume=int(fill.volume),
            entry_date=fill.ts.date(),
            industry=industry,
            available_to_sell=False,
        )
    elif fill.side == "sell":
        pos = account.positions.get(fill.code)
        proceeds = float(fill.price) * int(fill.volume) - float(fill.cost)
        account.cash += proceeds
        if pos:
            left = int(pos.volume) - int(fill.volume)
            if left <= 0:
                account.positions.pop(fill.code, None)
            else:
                pos.volume = left


def fill_from_intent(
    intent: OrderIntent,
    *,
    price: float,
    cfg: MvpConfig,
) -> Optional[FillRecord]:
    if intent.deferred or intent.volume <= 0 or price <= 0:
        return None
    notional = float(price) * int(intent.volume)
    if intent.side == "buy":
        cost = notional * (float(cfg.round_trip_cost) * 0.5)
    else:
        cost = notional * (float(cfg.round_trip_cost) * 0.5 + float(cfg.stamp_tax_sell))
    return FillRecord(
        side=intent.side,
        code=intent.code,
        volume=int(intent.volume),
        price=float(price),
        ts=intent.created_at,
        reason=str(intent.reason),
        cost=float(cost),
        meta=dict(intent.meta or {}),
    )
