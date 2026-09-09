# -*- coding: utf-8 -*-
"""
周调仓引擎（方案 A：账本不复权）。

硬约束：
- 开盘价缺失/非有限 → 禁止成交，记 sell_defer/buy_defer(missing_px)，绝不写入 NaN cash
- 换手上限：按排名预构建目标组合（与回归策略同构），避免「卖不掉→永久顶帽」
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

from trend_strategy.config import TrendStrategyConfig
from trend_strategy.weeks import next_trade_day, prev_trade_day
from utils.limit_ratio import get_limit_ratio


@dataclass
class DeferredBuy:
    code: str
    periods_left: int


@dataclass
class DeferredSell:
    code: str
    priority_left: int
    shares: float
    reason: str = "limit_down"


@dataclass
class PortfolioState:
    cash: float
    shares: Dict[str, float] = field(default_factory=dict)
    frozen: Dict[str, float] = field(default_factory=dict)
    deferred_buys: Dict[str, DeferredBuy] = field(default_factory=dict)
    deferred_sells: Dict[str, DeferredSell] = field(default_factory=dict)
    last_prices: Dict[str, float] = field(default_factory=dict)


def _is_one_word_limit_up(
    code: str,
    name: str,
    pre_close: float,
    open_px: float,
    high: float,
    low: float,
    close: float,
    as_of,
    eps: float,
) -> bool:
    if pre_close <= 0 or not np.isfinite(open_px):
        return False
    if not all(np.isfinite(x) for x in (high, low, close)):
        return False
    ratio = get_limit_ratio(code, name, as_of)
    lu = round(pre_close * (1.0 + ratio), 2)
    near = lambda p: p >= lu - eps
    return near(open_px) and near(high) and near(low) and near(close)


def _is_one_word_limit_down(
    code: str,
    name: str,
    pre_close: float,
    open_px: float,
    high: float,
    low: float,
    close: float,
    as_of,
    eps: float,
) -> bool:
    if pre_close <= 0 or not np.isfinite(open_px):
        return False
    if not all(np.isfinite(x) for x in (high, low, close)):
        return False
    ratio = get_limit_ratio(code, name, as_of)
    ld = round(pre_close * (1.0 - ratio), 2)
    near = lambda p: p <= ld + eps
    return near(open_px) and near(high) and near(low) and near(close)


def _finite_px(px: float) -> bool:
    return px is not None and np.isfinite(px) and float(px) > 0


def _nav(state: PortfolioState, prices: Dict[str, float]) -> float:
    """缺价用 last_prices；仍缺则跳过该腿（不引入 NaN）。"""
    if not np.isfinite(state.cash):
        return float("nan")
    equity = float(state.cash)
    for d in (state.shares, state.frozen):
        for c, sh in d.items():
            if sh <= 0:
                continue
            px = prices.get(c, np.nan)
            if not _finite_px(px):
                px = state.last_prices.get(c, np.nan)
            if not _finite_px(px):
                continue
            equity += sh * float(px)
    return float(equity)


def _settle_t1(state: PortfolioState) -> None:
    for c, sh in list(state.frozen.items()):
        state.shares[c] = state.shares.get(c, 0.0) + sh
        del state.frozen[c]


def select_top_n(
    score_row: pd.Series,
    n: int,
    *,
    industry_map: Optional[Dict[str, str]] = None,
    industry_cap_k: int = 0,
) -> List[str]:
    if industry_cap_k and industry_map is not None:
        from reversion_strategy.industry import select_top_n_with_industry_cap

        return select_top_n_with_industry_cap(
            score_row, n, industry_map, skip_top_k=0, cap_k=industry_cap_k
        )
    s = score_row.dropna().sort_values(ascending=False)
    return list(s.index[:n])


def apply_turnover_cap_by_rank(
    score_row: pd.Series,
    held: Sequence[str],
    ideal_target: Sequence[str],
    *,
    top_n: int,
    max_turnover: float,
) -> List[str]:
    """
    按排名强制换手帽：
    - 替换只数 ≤ floor(max_turnover * top_n)；未卖出旧仓占槽，禁止新票挤出
    - 持仓不足 top_n 时补仓（不占换手）
    """
    held_list = [c for c in held if c]
    held_set = set(held_list)
    ideal = list(ideal_target)[:top_n]
    if not held_set:
        return ideal

    max_replace = int(np.floor(max_turnover * top_n + 1e-9))
    ideal_set = set(ideal)

    def _score(c: str) -> float:
        if c in score_row.index:
            try:
                return float(score_row.get(c))
            except (TypeError, ValueError):
                return float("-inf")
        return float("-inf")

    exit_cands = sorted([c for c in held_list if c not in ideal_set], key=_score)
    entry_cands = [c for c in ideal if c not in held_set]
    n_replace = min(len(exit_cands), len(entry_cands), max(0, max_replace))
    sold = set(exit_cands[:n_replace])
    bought = entry_cands[:n_replace]
    entry_rest = entry_cands[n_replace:]

    kept = [c for c in held_list if c not in sold]
    kept_ideal = [c for c in ideal if c in kept]
    kept_other = sorted([c for c in kept if c not in ideal_set], key=_score, reverse=True)

    final: List[str] = []
    seen: Set[str] = set()

    def _add(code: str) -> None:
        if code in seen or len(final) >= top_n:
            return
        final.append(code)
        seen.add(code)

    for c in kept_ideal:
        _add(c)
    for c in kept_other:
        _add(c)
    for c in bought:
        _add(c)
    for c in entry_rest:
        _add(c)
    return final[:top_n]


def _ohlc_at(
    code: str,
    open_px: float,
    high_px: pd.Series,
    low_px: pd.Series,
    close_px: pd.Series,
) -> Tuple[float, float, float]:
    h = float(high_px.get(code, open_px)) if code in high_px.index else open_px
    l = float(low_px.get(code, open_px)) if code in low_px.index else open_px
    c = float(close_px.get(code, open_px)) if code in close_px.index else open_px
    return h, l, c


def run_backtest(
    score: pd.DataFrame,
    week_ends: Sequence,
    trade_days: Sequence,
    open_raw: pd.DataFrame,
    close_raw: pd.DataFrame,
    high_raw: pd.DataFrame,
    low_raw: pd.DataFrame,
    name_map: Dict[str, str],
    cfg: TrendStrategyConfig,
    *,
    industry_map: Optional[Dict[str, str]] = None,
) -> dict:
    """
    周调仓回测（方案 A：账本不复权）。
    决策日 T（周末收盘后）→ 下一交易日开盘执行。
    """
    state = PortfolioState(cash=cfg.initial_cash)
    records = []
    trade_logs = []
    we = [d for d in week_ends if d in score.index]
    td = list(trade_days)

    for i, T in enumerate(we):
        exec_day = next_trade_day(td, T)
        if exec_day is None:
            break
        if exec_day not in open_raw.index or exec_day not in close_raw.index:
            continue

        _settle_t1(state)

        score_row = score.loc[T]
        ideal = select_top_n(
            score_row,
            cfg.top_n,
            industry_map=industry_map,
            industry_cap_k=int(getattr(cfg, "industry_cap_k", 0) or 0),
        )
        held_now = [c for c, sh in state.shares.items() if sh > 0] + [
            c for c, sh in state.frozen.items() if sh > 0
        ]
        target = apply_turnover_cap_by_rank(
            score_row,
            held_now,
            ideal,
            top_n=cfg.top_n,
            max_turnover=cfg.max_turnover,
        )
        target_set: Set[str] = set(target)

        open_px = open_raw.loc[exec_day]
        close_px = close_raw.loc[exec_day]
        high_px = high_raw.loc[exec_day] if high_raw is not None and not high_raw.empty and exec_day in high_raw.index else open_px
        low_px = low_raw.loc[exec_day] if low_raw is not None and not low_raw.empty and exec_day in low_raw.index else open_px

        prev_day = prev_trade_day(td, exec_day)
        pre_close = close_raw.loc[prev_day] if prev_day is not None and prev_day in close_raw.index else close_px

        prices_open = {
            c: float(open_px[c])
            for c in open_px.index
            if _finite_px(float(open_px[c]) if np.isfinite(open_px[c]) else np.nan)
        }
        # 用 last_prices 补 NAV 盯市，避免缺价腿把 equity 算丢（但不用于成交）
        nav0 = _nav(state, prices_open)
        if not np.isfinite(nav0) or nav0 <= 0:
            trade_logs.append({"date": exec_day, "code": "", "side": "abort", "reason": "bad_nav"})
            break

        idle_cash0 = float(state.cash)
        sell_notional = 0.0

        # —— 卖出队列：递延优先，再普通（不在目标）——
        prev_deferred = dict(state.deferred_sells)
        state.deferred_sells = {}
        sell_queue: List[Tuple[int, str, float, int, str]] = []
        for code, ds in prev_deferred.items():
            if code in target_set:
                continue
            sh = state.shares.get(code, 0.0)
            if sh > 0:
                sell_queue.append((0, code, sh, ds.priority_left, ds.reason))
        for code, sh in list(state.shares.items()):
            if sh <= 0 or code in target_set or code in prev_deferred:
                continue
            sell_queue.append((1, code, sh, cfg.max_sell_defer_priority, "exit"))

        for _, code, sh, prio_left, _why in sorted(sell_queue, key=lambda x: x[0]):
            o = prices_open.get(code, np.nan)
            if not _finite_px(o):
                state.deferred_sells[code] = DeferredSell(code, max(prio_left - 1, 0), sh, reason="missing_px")
                trade_logs.append({"date": exec_day, "code": code, "side": "sell_defer", "reason": "missing_px"})
                continue
            name = name_map.get(code, "")
            pc = float(pre_close.get(code, np.nan)) if code in pre_close.index else np.nan
            h, l, cpx = _ohlc_at(code, o, high_px, low_px, close_px)
            if _is_one_word_limit_down(code, name, pc, o, h, l, cpx, exec_day, cfg.limit_eps):
                state.deferred_sells[code] = DeferredSell(code, max(prio_left - 1, 0), sh, reason="limit_down")
                trade_logs.append({"date": exec_day, "code": code, "side": "sell_defer", "reason": "limit_down"})
                continue
            proceeds = sh * o
            cost = proceeds * cfg.cost_one_side
            if not np.isfinite(proceeds) or not np.isfinite(cost):
                state.deferred_sells[code] = DeferredSell(code, max(prio_left - 1, 0), sh, reason="bad_notional")
                trade_logs.append({"date": exec_day, "code": code, "side": "sell_defer", "reason": "bad_notional"})
                continue
            state.cash += proceeds - cost
            state.shares[code] = 0.0
            state.last_prices[code] = o
            sell_notional += proceeds
            trade_logs.append({"date": exec_day, "code": code, "side": "sell", "shares": sh, "px": o, "cost": cost})

        if not np.isfinite(state.cash):
            trade_logs.append({"date": exec_day, "code": "", "side": "abort", "reason": "cash_nan"})
            break

        nav1 = _nav(state, prices_open)
        if not np.isfinite(nav1) or nav1 <= 0:
            trade_logs.append({"date": exec_day, "code": "", "side": "abort", "reason": "bad_nav_after_sell"})
            break

        target_w = 1.0 / max(cfg.top_n, 1)
        target_value = {c: nav1 * target_w for c in target}

        def _pos_value(code: str) -> float:
            sh = state.shares.get(code, 0.0) + state.frozen.get(code, 0.0)
            px = prices_open.get(code, state.last_prices.get(code, 0.0))
            if not _finite_px(px):
                return 0.0
            return sh * float(px)

        # 超配减仓（目标内）——不另占「替换只数」预算；金额上仍受现金逻辑约束
        for code in list(state.shares.keys()):
            if code not in target_set:
                continue
            cur_v = _pos_value(code)
            tgt_v = target_value.get(code, 0.0)
            if cur_v <= tgt_v * 1.01:
                continue
            o = prices_open.get(code, np.nan)
            if not _finite_px(o):
                continue
            name = name_map.get(code, "")
            pc = float(pre_close.get(code, np.nan)) if code in pre_close.index else np.nan
            h, l, cpx = _ohlc_at(code, o, high_px, low_px, close_px)
            if _is_one_word_limit_down(code, name, pc, o, h, l, cpx, exec_day, cfg.limit_eps):
                continue
            sell_v = cur_v - tgt_v
            sh_sell = min(sell_v / o, state.shares.get(code, 0.0))
            if sh_sell <= 0:
                continue
            proceeds = sh_sell * o
            cost = proceeds * cfg.cost_one_side
            if not np.isfinite(proceeds):
                continue
            state.cash += proceeds - cost
            state.shares[code] = state.shares.get(code, 0.0) - sh_sell
            if state.shares[code] <= 1e-9:
                state.shares[code] = 0.0
            state.last_prices[code] = o
            sell_notional += proceeds
            trade_logs.append({"date": exec_day, "code": code, "side": "sell_rebal", "shares": sh_sell, "px": o, "cost": cost})

        free_cash_left = idle_cash0
        # 替换买入预算：本期实际卖出所得（排名帽已限制替换规模，此处不再二次砍 30%）
        replace_cash_left = sell_notional

        new_deferred_buys: Dict[str, DeferredBuy] = {}
        pending_buys: List[Tuple[int, str, int]] = []
        for code, db in state.deferred_buys.items():
            if code in target_set:
                pending_buys.append((0, code, db.periods_left))
        for code in target:
            if code not in state.deferred_buys:
                pending_buys.append((1, code, cfg.max_buy_defer_periods))

        for prio, code, periods_left in sorted(pending_buys, key=lambda x: x[0]):
            o = prices_open.get(code, np.nan)
            if not _finite_px(o):
                if prio == 1:
                    new_deferred_buys[code] = DeferredBuy(code, cfg.max_buy_defer_periods)
                elif periods_left > 1:
                    new_deferred_buys[code] = DeferredBuy(code, periods_left - 1)
                trade_logs.append({"date": exec_day, "code": code, "side": "buy_defer", "reason": "missing_px"})
                continue

            tgt_v = target_value.get(code, nav1 / cfg.top_n)
            cur_v = _pos_value(code)
            buy_v = max(0.0, tgt_v - cur_v)
            if buy_v <= 0:
                continue
            from_free = min(buy_v, free_cash_left)
            from_replace = min(max(0.0, buy_v - from_free), replace_cash_left)
            buy_v = from_free + from_replace
            if buy_v < max(1000.0, o * 100):
                continue

            name = name_map.get(code, "")
            pc = float(pre_close.get(code, np.nan)) if code in pre_close.index else np.nan
            h, l, cpx = _ohlc_at(code, o, high_px, low_px, close_px)
            if _is_one_word_limit_up(code, name, pc, o, h, l, cpx, exec_day, cfg.limit_eps):
                if prio == 1:
                    new_deferred_buys[code] = DeferredBuy(code, cfg.max_buy_defer_periods)
                elif periods_left > 1:
                    new_deferred_buys[code] = DeferredBuy(code, periods_left - 1)
                side = "buy_defer" if (prio == 1 or periods_left > 1) else "buy_abandon"
                trade_logs.append({"date": exec_day, "code": code, "side": side, "reason": "limit_up"})
                continue

            cost = buy_v * cfg.cost_one_side
            if state.cash < buy_v + cost:
                buy_v = state.cash / (1.0 + cfg.cost_one_side)
                cost = buy_v * cfg.cost_one_side
            if buy_v < max(1000.0, o * 100) or not np.isfinite(buy_v):
                continue
            sh = buy_v / o
            state.cash -= buy_v + cost
            if not np.isfinite(state.cash):
                trade_logs.append({"date": exec_day, "code": code, "side": "abort", "reason": "cash_nan_buy"})
                break
            state.frozen[code] = state.frozen.get(code, 0.0) + sh
            state.last_prices[code] = o
            use_free = min(from_free, buy_v)
            free_cash_left = max(0.0, free_cash_left - use_free)
            replace_cash_left = max(0.0, replace_cash_left - (buy_v - use_free))
            trade_logs.append({"date": exec_day, "code": code, "side": "buy", "shares": sh, "px": o, "cost": cost})

        for code, db in state.deferred_buys.items():
            if code in new_deferred_buys or code not in target_set:
                continue
            new_deferred_buys[code] = db
        state.deferred_buys = new_deferred_buys

        prices_close = {
            c: float(close_px[c])
            for c in close_px.index
            if _finite_px(float(close_px[c]) if np.isfinite(close_px[c]) else np.nan)
        }
        for c, sh in list(state.shares.items()) + list(state.frozen.items()):
            if c in prices_close:
                state.last_prices[c] = prices_close[c]
        nav_eod = _nav(state, prices_close)
        records.append(
            {
                "decision_date": T,
                "exec_date": exec_day,
                "nav": nav_eod,
                "cash": state.cash if np.isfinite(state.cash) else np.nan,
                "n_hold": sum(1 for sh in state.shares.values() if sh > 0)
                + sum(1 for sh in state.frozen.values() if sh > 0),
                "turnover_used": (sell_notional / nav0) if nav0 else 0.0,
            }
        )

    return {
        "nav": pd.DataFrame(records),
        "trades": pd.DataFrame(trade_logs),
        "final_state": state,
    }
