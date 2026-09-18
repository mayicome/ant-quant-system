# -*- coding: utf-8 -*-
"""周调仓引擎：方案 A 账本 + 开盘涨跌停递延 + 排名换手帽 + 后复权止损。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

from reversion_strategy.config import ReversionStrategyConfig
from reversion_strategy.weeks import next_trade_day, prev_trade_day
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


@dataclass
class PortfolioState:
    cash: float
    shares: Dict[str, float] = field(default_factory=dict)
    frozen: Dict[str, float] = field(default_factory=dict)
    deferred_buys: Dict[str, DeferredBuy] = field(default_factory=dict)
    deferred_sells: Dict[str, DeferredSell] = field(default_factory=dict)
    last_prices: Dict[str, float] = field(default_factory=dict)
    # 后复权摊薄成本（止损用）
    cost_hfq: Dict[str, float] = field(default_factory=dict)
    shares_basis: Dict[str, float] = field(default_factory=dict)  # 与 cost 对应的股数
    pending_stops: Set[str] = field(default_factory=set)


def _limit_price(code: str, name: str, pre_close: float, up: bool, as_of) -> float:
    if not np.isfinite(pre_close) or pre_close <= 0:
        return float("nan")
    ratio = get_limit_ratio(code, name, as_of)
    return round(pre_close * (1.0 + ratio if up else 1.0 - ratio), 2)


def _is_open_at_limit(
    code: str,
    name: str,
    pre_close: float,
    open_px: float,
    as_of,
    eps: float,
    *,
    up: bool,
) -> bool:
    """递延口径：|开盘价 − 涨/跌停价| ≤ eps。"""
    if not np.isfinite(open_px) or not np.isfinite(pre_close) or pre_close <= 0:
        return False
    lim = _limit_price(code, name, pre_close, up=up, as_of=as_of)
    if not np.isfinite(lim):
        return False
    return abs(float(open_px) - lim) <= eps


def _nav(state: PortfolioState, prices: Dict[str, float]) -> float:
    if not np.isfinite(state.cash):
        return float("nan")
    equity = float(state.cash)
    for d in (state.shares, state.frozen):
        for c, sh in d.items():
            if sh <= 0:
                continue
            px = prices.get(c, np.nan)
            if px is None or not np.isfinite(px) or px <= 0:
                px = state.last_prices.get(c, np.nan)
            if px is None or not np.isfinite(px) or px <= 0:
                continue
            equity += sh * float(px)
    return float(equity)


def _settle_t1(state: PortfolioState) -> None:
    for c, sh in list(state.frozen.items()):
        state.shares[c] = state.shares.get(c, 0.0) + sh
        del state.frozen[c]


def _update_cost_basis(state: PortfolioState, code: str, buy_shares: float, buy_px_hfq: float) -> None:
    if buy_shares <= 0 or not np.isfinite(buy_px_hfq) or buy_px_hfq <= 0:
        return
    old_sh = state.shares_basis.get(code, 0.0)
    old_cost = state.cost_hfq.get(code, buy_px_hfq)
    new_sh = old_sh + buy_shares
    if new_sh <= 1e-12:
        return
    state.cost_hfq[code] = (old_cost * old_sh + buy_px_hfq * buy_shares) / new_sh
    state.shares_basis[code] = new_sh


def _reduce_cost_basis(state: PortfolioState, code: str, sell_shares: float) -> None:
    old_sh = state.shares_basis.get(code, 0.0)
    if old_sh <= 0:
        return
    left = max(0.0, old_sh - sell_shares)
    if left <= 1e-12:
        state.shares_basis.pop(code, None)
        state.cost_hfq.pop(code, None)
    else:
        state.shares_basis[code] = left


def select_top_n(
    score_row: pd.Series,
    n: int,
    *,
    skip_top_k: int = 0,
    industry_map: Optional[Dict[str, str]] = None,
    industry_cap_k: int = 0,
) -> List[str]:
    if industry_cap_k and industry_map is not None:
        from reversion_strategy.industry import select_top_n_with_industry_cap

        return select_top_n_with_industry_cap(
            score_row, n, industry_map, skip_top_k=skip_top_k, cap_k=industry_cap_k
        )
    s = score_row.dropna().sort_values(ascending=False)
    k = max(0, int(skip_top_k))
    return list(s.index[k : k + n])


def apply_turnover_cap_by_rank(
    score_row: pd.Series,
    held: Sequence[str],
    ideal_target: Sequence[str],
    *,
    top_n: int,
    max_turnover: float,
) -> List[str]:
    """
    按排名强制换手帽（硬约束）：
    - 替换只数 ≤ floor(max_turnover * top_n)；未卖出的旧仓必须占槽，禁止用新票挤掉
    - 持仓不足 top_n 时，用 ideal 剩余股票补仓（不占换手）
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
            v = score_row.get(c)
            try:
                return float(v)
            except (TypeError, ValueError):
                return float("-inf")
        return float("-inf")

    exit_cands = sorted(
        [c for c in held_list if c not in ideal_set],
        key=_score,  # 低分先卖
    )
    entry_cands = [c for c in ideal if c not in held_set]  # ideal 已按高分排序

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

    # 1) 保留仓（含换手帽保护的未卖出旧仓）必须先入座
    for c in kept_ideal:
        _add(c)
    for c in kept_other:
        _add(c)
    # 2) 替换买入
    for c in bought:
        _add(c)
    # 3) 仅当仍不足 top_n（欠配/有现金空位）才补新票 —— 绝不挤掉 kept
    for c in entry_rest:
        _add(c)

    return final[:top_n]


def run_backtest(
    score: pd.DataFrame,
    week_ends: Sequence,
    trade_days: Sequence,
    open_raw: pd.DataFrame,
    close_raw: pd.DataFrame,
    open_hfq: pd.DataFrame,
    close_hfq: pd.DataFrame,
    name_map: Dict[str, str],
    cfg: ReversionStrategyConfig,
    *,
    industry_map: Optional[Dict[str, str]] = None,
    allow_new_buys: Optional[Dict] = None,
    exposure_scale: Optional[Dict] = None,
) -> dict:
    state = PortfolioState(cash=cfg.initial_cash)
    records = []
    trade_logs = []
    we = [d for d in week_ends if d in score.index]
    td = list(trade_days)

    for i, T in enumerate(we):
        exec_day = next_trade_day(td, T)
        if exec_day is None or exec_day not in open_raw.index:
            continue
        next_T = we[i + 1] if i + 1 < len(we) else None
        next_exec = next_trade_day(td, next_T) if next_T is not None else None

        _settle_t1(state)

        # —— 开盘：执行挂起的止损卖出 ——
        if cfg.enable_stop_loss and state.pending_stops:
            pre_day = prev_trade_day(td, exec_day)
            pre_close = close_raw.loc[pre_day] if pre_day is not None and pre_day in close_raw.index else None
            open_px = open_raw.loc[exec_day]
            still_stops: Set[str] = set()
            for code in list(state.pending_stops):
                sh = state.shares.get(code, 0.0)
                if sh <= 0:
                    continue
                o = float(open_px.get(code, np.nan)) if code in open_px.index else np.nan
                if not np.isfinite(o) or o <= 0:
                    still_stops.add(code)
                    continue
                name = name_map.get(code, "")
                pc = float(pre_close.get(code, np.nan)) if pre_close is not None and code in pre_close.index else np.nan
                if _is_open_at_limit(code, name, pc, o, exec_day, cfg.limit_eps, up=False):
                    still_stops.add(code)
                    trade_logs.append({"date": exec_day, "code": code, "side": "stop_defer", "reason": "limit_down"})
                    continue
                proceeds = sh * o
                cost = proceeds * cfg.cost_one_side
                state.cash += proceeds - cost
                state.shares[code] = 0.0
                _reduce_cost_basis(state, code, sh)
                state.last_prices[code] = o
                trade_logs.append({"date": exec_day, "code": code, "side": "stop_sell", "shares": sh, "px": o, "cost": cost})
            state.pending_stops = still_stops

        # —— 目标组合（排名换手帽）——
        score_row = score.loc[T]
        scale = 1.0
        if exposure_scale is not None:
            try:
                scale = float(exposure_scale.get(T, 1.0))
            except (TypeError, ValueError):
                scale = 1.0
        scale = min(1.0, max(0.0, scale))
        eff_n = max(1, int(round(cfg.top_n * scale))) if scale < 1.0 - 1e-12 else int(cfg.top_n)
        ideal_raw = select_top_n(
            score_row,
            eff_n,
            skip_top_k=int(getattr(cfg, "skip_top_k", 0) or 0),
            industry_map=industry_map,
            industry_cap_k=int(getattr(cfg, "industry_cap_k", 0) or 0),
        )
        held_now = [c for c, sh in state.shares.items() if sh > 0] + [
            c for c, sh in state.frozen.items() if sh > 0
        ]
        held_set_now = set(held_now)
        allow_buy = True if allow_new_buys is None else bool(allow_new_buys.get(T, True))
        # 禁新买：理想组合不含新票，仍可按换手卖掉跌出名单的旧仓
        ideal = [c for c in ideal_raw if c in held_set_now] if not allow_buy else ideal_raw
        target = apply_turnover_cap_by_rank(
            score_row,
            held_now,
            ideal,
            top_n=eff_n,
            max_turnover=cfg.max_turnover,
        )
        if not allow_buy:
            target = [c for c in target if c in held_set_now]
        target_set: Set[str] = set(target)

        open_px = open_raw.loc[exec_day]
        close_px = close_raw.loc[exec_day] if exec_day in close_raw.index else open_px
        open_hfq_row = open_hfq.loc[exec_day] if exec_day in open_hfq.index else None

        prev_day = prev_trade_day(td, exec_day)
        pre_close = close_raw.loc[prev_day] if prev_day is not None and prev_day in close_raw.index else close_px

        prices_open = {c: float(open_px[c]) for c in open_px.index if np.isfinite(open_px[c]) and float(open_px[c]) > 0}
        nav0 = _nav(state, prices_open)
        if not np.isfinite(nav0) or nav0 <= 0:
            break

        idle_cash0 = float(state.cash)
        sell_notional = 0.0
        spent_sell_turnover = 0.0
        turnover_budget = cfg.max_turnover * nav0

        # 递延卖出优先，再普通卖出（不在目标）
        prev_deferred = dict(state.deferred_sells)
        state.deferred_sells = {}
        sell_queue: List[tuple] = []
        for code, ds in prev_deferred.items():
            if code in target_set:
                continue
            sh = state.shares.get(code, 0.0)
            if sh > 0:
                sell_queue.append((0, code, sh, ds.priority_left))
        for code, sh in list(state.shares.items()):
            if sh <= 0 or code in target_set:
                continue
            if code in prev_deferred:
                continue
            sell_queue.append((1, code, sh, cfg.max_sell_defer_priority))

        for _, code, sh, prio_left in sorted(sell_queue, key=lambda x: x[0]):
            o = prices_open.get(code, np.nan)
            if not np.isfinite(o) or o <= 0:
                state.deferred_sells[code] = DeferredSell(code, max(prio_left - 1, 0), sh)
                trade_logs.append({"date": exec_day, "code": code, "side": "sell_defer", "reason": "missing_px"})
                continue
            name = name_map.get(code, "")
            pc = float(pre_close.get(code, np.nan)) if code in pre_close.index else np.nan
            if _is_open_at_limit(code, name, pc, o, exec_day, cfg.limit_eps, up=False):
                state.deferred_sells[code] = DeferredSell(code, max(prio_left - 1, 0), sh)
                trade_logs.append({"date": exec_day, "code": code, "side": "sell_defer", "reason": "limit_down"})
                continue
            remain = max(0.0, turnover_budget - spent_sell_turnover)
            full = sh * o
            if full > remain + 1e-9:
                if remain < o * 100:
                    # 额度不够一手：整笔递延，保持持仓（换手硬帽）
                    state.deferred_sells[code] = DeferredSell(code, max(prio_left, 1), sh)
                    trade_logs.append({"date": exec_day, "code": code, "side": "sell_defer", "reason": "turnover"})
                    continue
                sh = remain / o
            proceeds = sh * o
            cost = proceeds * cfg.cost_one_side
            if not np.isfinite(proceeds) or not np.isfinite(cost):
                state.deferred_sells[code] = DeferredSell(code, max(prio_left - 1, 0), sh)
                trade_logs.append({"date": exec_day, "code": code, "side": "sell_defer", "reason": "bad_notional"})
                continue
            state.cash += proceeds - cost
            left = state.shares.get(code, 0.0) - sh
            state.shares[code] = 0.0 if left <= 1e-9 else left
            _reduce_cost_basis(state, code, sh)
            state.last_prices[code] = o
            sell_notional += proceeds
            spent_sell_turnover += proceeds
            trade_logs.append({"date": exec_day, "code": code, "side": "sell", "shares": sh, "px": o, "cost": cost})
            if left > 1e-9:
                state.deferred_sells[code] = DeferredSell(code, max(prio_left, 1), left)
                trade_logs.append({"date": exec_day, "code": code, "side": "sell_defer", "reason": "turnover_partial"})

        if not np.isfinite(state.cash):
            trade_logs.append({"date": exec_day, "code": "", "side": "abort", "reason": "cash_nan"})
            break

        nav1 = _nav(state, prices_open)
        # 权重按名义 top_n：eff_n < top_n 时自然留出现金缓冲
        target_w = 1.0 / max(cfg.top_n, 1)
        target_value = {c: nav1 * target_w for c in target}

        def _pos_value(code: str) -> float:
            sh = state.shares.get(code, 0.0) + state.frozen.get(code, 0.0)
            return sh * prices_open.get(code, 0.0)

        free_cash_left = idle_cash0
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
            if not allow_buy and code not in held_set_now:
                trade_logs.append({"date": exec_day, "code": code, "side": "buy_abandon", "reason": "regime_block"})
                continue
            o = prices_open.get(code)
            if not o or not np.isfinite(o) or o <= 0:
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
            if _is_open_at_limit(code, name, pc, o, exec_day, cfg.limit_eps, up=True):
                if prio == 1:
                    new_deferred_buys[code] = DeferredBuy(code, cfg.max_buy_defer_periods)
                elif periods_left > 1:
                    new_deferred_buys[code] = DeferredBuy(code, periods_left - 1)
                trade_logs.append(
                    {
                        "date": exec_day,
                        "code": code,
                        "side": "buy_defer" if periods_left > 1 or prio == 1 else "buy_abandon",
                        "reason": "limit_up",
                    }
                )
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
            # 后复权成本
            hfq_px = (
                float(open_hfq_row.get(code, np.nan))
                if open_hfq_row is not None and code in open_hfq_row.index
                else np.nan
            )
            _update_cost_basis(state, code, sh, hfq_px)
            use_free = min(from_free, buy_v)
            free_cash_left = max(0.0, free_cash_left - use_free)
            replace_cash_left = max(0.0, replace_cash_left - (buy_v - use_free))
            trade_logs.append({"date": exec_day, "code": code, "side": "buy", "shares": sh, "px": o, "cost": cost})

        for code, db in state.deferred_buys.items():
            if code in new_deferred_buys or code not in target_set:
                continue
            new_deferred_buys[code] = db
        state.deferred_buys = new_deferred_buys

        # —— 日间：止损检查（exec_day 收盘 → next_exec 前一日）——
        if cfg.enable_stop_loss:
            day_start = exec_day
            day_end = next_exec if next_exec is not None else (td[-1] if td else exec_day)
            for d in td:
                if d < day_start:
                    continue
                if d >= day_end:
                    break
                if d not in close_hfq.index:
                    continue
                # 非 exec_day 的开盘：执行已挂起止损
                if d > exec_day and state.pending_stops:
                    pre_d = prev_trade_day(td, d)
                    pre_c = close_raw.loc[pre_d] if pre_d is not None and pre_d in close_raw.index else None
                    o_row = open_raw.loc[d] if d in open_raw.index else None
                    if o_row is not None:
                        still: Set[str] = set()
                        for code in list(state.pending_stops):
                            sh = state.shares.get(code, 0.0)
                            if sh <= 0:
                                continue
                            o = float(o_row.get(code, np.nan)) if code in o_row.index else np.nan
                            if not np.isfinite(o) or o <= 0:
                                still.add(code)
                                continue
                            name = name_map.get(code, "")
                            pc = float(pre_c.get(code, np.nan)) if pre_c is not None and code in pre_c.index else np.nan
                            if _is_open_at_limit(code, name, pc, o, d, cfg.limit_eps, up=False):
                                still.add(code)
                                trade_logs.append({"date": d, "code": code, "side": "stop_defer", "reason": "limit_down"})
                                continue
                            proceeds = sh * o
                            cost = proceeds * cfg.cost_one_side
                            state.cash += proceeds - cost
                            state.shares[code] = 0.0
                            _reduce_cost_basis(state, code, sh)
                            state.last_prices[code] = o
                            trade_logs.append({"date": d, "code": code, "side": "stop_sell", "shares": sh, "px": o})
                        state.pending_stops = still

                # 收盘止损检查（含 exec_day）
                row_hfq = close_hfq.loc[d]
                for code, sh in list(state.shares.items()):
                    if sh <= 0 or code in state.pending_stops:
                        continue
                    cost_px = state.cost_hfq.get(code)
                    if cost_px is None or cost_px <= 0:
                        continue
                    px = float(row_hfq.get(code, np.nan)) if code in row_hfq.index else np.nan
                    if not np.isfinite(px) or px <= 0:
                        continue
                    if px / cost_px - 1.0 <= cfg.stop_loss:
                        state.pending_stops.add(code)
                        trade_logs.append(
                            {
                                "date": d,
                                "code": code,
                                "side": "stop_trigger",
                                "px_hfq": px,
                                "cost_hfq": cost_px,
                                "ret": px / cost_px - 1.0,
                            }
                        )

        prices_close = {c: float(close_px[c]) for c in close_px.index if np.isfinite(close_px[c])}
        for c, sh in list(state.shares.items()) + list(state.frozen.items()):
            if c in prices_close:
                state.last_prices[c] = prices_close[c]
        nav_eod = _nav(state, prices_close)
        records.append(
            {
                "decision_date": T,
                "exec_date": exec_day,
                "nav": nav_eod,
                "cash": state.cash,
                "n_hold": sum(1 for sh in state.shares.values() if sh > 0)
                + sum(1 for sh in state.frozen.values() if sh > 0),
                "turnover_used": spent_sell_turnover / nav0 if nav0 else 0.0,
                "n_stop_pending": len(state.pending_stops),
            }
        )

    return {
        "nav": pd.DataFrame(records),
        "trades": pd.DataFrame(trade_logs),
        "final_state": state,
    }
