# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

import numpy as np
import pandas as pd

from trend_strategy.config import TrendStrategyConfig
from trend_strategy.weeks import next_trade_day
from utils.limit_ratio import get_limit_ratio


@dataclass
class DeferredBuy:
    code: str
    periods_left: int  # 剩余可顺延期数（含本次尝试前）


@dataclass
class DeferredSell:
    code: str
    priority_left: int  # 优先卖出剩余期数
    shares: float


@dataclass
class PortfolioState:
    cash: float
    shares: Dict[str, float] = field(default_factory=dict)  # 可卖持仓
    frozen: Dict[str, float] = field(default_factory=dict)  # T+1 冻结（当日买）
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
    ratio = get_limit_ratio(code, name, as_of)
    lu = round(pre_close * (1.0 + ratio), 2)
    # 一字涨停：开高低收都贴涨停
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
    ratio = get_limit_ratio(code, name, as_of)
    ld = round(pre_close * (1.0 - ratio), 2)
    near = lambda p: p <= ld + eps
    return near(open_px) and near(high) and near(low) and near(close)


def _nav(state: PortfolioState, prices: Dict[str, float]) -> float:
    equity = state.cash
    for d in (state.shares, state.frozen):
        for c, sh in d.items():
            px = prices.get(c, state.last_prices.get(c, 0.0))
            equity += sh * px
    return float(equity)


def _settle_t1(state: PortfolioState) -> None:
    for c, sh in list(state.frozen.items()):
        state.shares[c] = state.shares.get(c, 0.0) + sh
        del state.frozen[c]


def select_top_n(score_row: pd.Series, n: int) -> List[str]:
    s = score_row.dropna().sort_values(ascending=False)
    return list(s.index[:n])


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
) -> dict:
    """
    周调仓回测（方案 A：账本不复权）。
    决策日 T（周末收盘后）→ 下一交易日开盘执行。
    """
    state = PortfolioState(cash=cfg.initial_cash)
    records = []
    trade_logs = []
    we = [d for d in week_ends if d in score.index]

    for i, T in enumerate(we):
        exec_day = next_trade_day(trade_days, T)
        if exec_day is None:
            break
        if exec_day not in open_raw.index or exec_day not in close_raw.index:
            continue

        # 开盘前：释放上一执行日的 T+1 冻结
        _settle_t1(state)

        target = select_top_n(score.loc[T], cfg.top_n)
        target_set: Set[str] = set(target)

        # 盯市价：用执行日开盘估 NAV（调仓基准）
        open_px = open_raw.loc[exec_day]
        close_px = close_raw.loc[exec_day]
        high_px = high_raw.loc[exec_day] if high_raw is not None and not high_raw.empty else open_px
        low_px = low_raw.loc[exec_day] if low_raw is not None and not low_raw.empty else open_px

        # 昨收：上一交易日不复权收盘
        prev_day = None
        for d in reversed(list(trade_days)):
            if d < exec_day:
                prev_day = d
                break
        pre_close = close_raw.loc[prev_day] if prev_day is not None and prev_day in close_raw.index else close_px

        prices_open = {c: float(open_px[c]) for c in open_px.index if np.isfinite(open_px[c])}
        nav0 = _nav(state, prices_open)
        if nav0 <= 0:
            break

        idle_cash0 = float(state.cash)  # 期初闲置现金：建仓不受换手帽
        sell_notional = 0.0  # 本期卖出成交额（替换额度来源）
        spent_sell_turnover = 0.0

        # --- 处理递延卖出优先 ---
        still_deferred_sell: Dict[str, DeferredSell] = {}
        for code, ds in list(state.deferred_sells.items()):
            if code in target_set:
                # 仍在目标 → 取消递延卖出
                continue
            sh = state.shares.get(code, 0.0)
            if sh <= 0:
                continue
            name = name_map.get(code, "")
            pc = float(pre_close.get(code, np.nan)) if code in pre_close.index else np.nan
            o = prices_open.get(code, np.nan)
            h = float(high_px.get(code, o)) if code in high_px.index else o
            l = float(low_px.get(code, o)) if code in low_px.index else o
            cpx = float(close_px.get(code, o)) if code in close_px.index else o
            blocked = _is_one_word_limit_down(code, name, pc, o, h, l, cpx, exec_day, cfg.limit_eps)
            if blocked:
                if ds.priority_left > 0:
                    still_deferred_sell[code] = DeferredSell(code, ds.priority_left - 1, sh)
                else:
                    still_deferred_sell[code] = DeferredSell(code, 0, sh)
                trade_logs.append({"date": exec_day, "code": code, "side": "sell_defer", "reason": "limit_down"})
                continue
            remain = max(0.0, cfg.max_turnover * nav0 - spent_sell_turnover)
            if sh * o > remain:
                if remain < o * 100:
                    still_deferred_sell[code] = DeferredSell(code, max(ds.priority_left, 1), sh)
                    trade_logs.append({"date": exec_day, "code": code, "side": "sell_defer", "reason": "turnover"})
                    continue
                sh = remain / o
            proceeds = sh * o
            cost = proceeds * cfg.cost_one_side
            state.cash += proceeds - cost
            left = state.shares.get(code, 0.0) - sh
            state.shares[code] = 0.0 if left <= 1e-9 else left
            state.last_prices[code] = o
            sell_notional += proceeds
            spent_sell_turnover += proceeds
            trade_logs.append({"date": exec_day, "code": code, "side": "sell", "shares": sh, "px": o, "cost": cost})
            if left > 1e-9:
                still_deferred_sell[code] = DeferredSell(code, max(ds.priority_left, 1), left)
        state.deferred_sells = still_deferred_sell

        # --- 普通卖出：持仓不在目标 ---
        held = {c: sh for c, sh in state.shares.items() if sh > 0}
        for code, sh in held.items():
            if code in target_set:
                continue
            if code in state.deferred_sells:
                continue
            name = name_map.get(code, "")
            pc = float(pre_close.get(code, np.nan)) if code in pre_close.index else np.nan
            o = prices_open.get(code, np.nan)
            if not np.isfinite(o) or o <= 0:
                continue
            h = float(high_px.get(code, o)) if code in high_px.index else o
            l = float(low_px.get(code, o)) if code in low_px.index else o
            cpx = float(close_px.get(code, o)) if code in close_px.index else o
            blocked = _is_one_word_limit_down(code, name, pc, o, h, l, cpx, exec_day, cfg.limit_eps)
            if blocked:
                state.deferred_sells[code] = DeferredSell(code, cfg.max_sell_defer_priority, sh)
                trade_logs.append({"date": exec_day, "code": code, "side": "sell_defer", "reason": "limit_down"})
                continue
            # 卖出受换手帽
            proceeds_full = sh * o
            remain = max(0.0, cfg.max_turnover * nav0 - spent_sell_turnover)
            if proceeds_full > remain:
                if remain < o * 100:
                    # 额度不够，整笔递延到下期（保持持仓）
                    state.deferred_sells[code] = DeferredSell(code, cfg.max_sell_defer_priority, sh)
                    trade_logs.append({"date": exec_day, "code": code, "side": "sell_defer", "reason": "turnover"})
                    continue
                sh = remain / o
            proceeds = sh * o
            cost = proceeds * cfg.cost_one_side
            state.cash += proceeds - cost
            state.shares[code] = state.shares.get(code, 0.0) - sh
            if state.shares[code] <= 1e-9:
                state.shares[code] = 0.0
            state.last_prices[code] = o
            sell_notional += proceeds
            spent_sell_turnover += proceeds
            trade_logs.append({"date": exec_day, "code": code, "side": "sell", "shares": sh, "px": o, "cost": cost})

        # 刷新 NAV（卖出后）
        nav1 = _nav(state, prices_open)
        target_w = 1.0 / cfg.top_n
        target_value = {c: nav1 * target_w for c in target}

        def _pos_value(code: str) -> float:
            sh = state.shares.get(code, 0.0) + state.frozen.get(code, 0.0)
            return sh * prices_open.get(code, 0.0)

        turnover_budget = cfg.max_turnover * nav0
        spent_turnover = spent_sell_turnover

        # 超配减仓（计入换手）
        for code in list(state.shares.keys()):
            if code not in target_set:
                continue
            cur_v = _pos_value(code)
            tgt_v = target_value.get(code, 0.0)
            if cur_v <= tgt_v * 1.01:
                continue
            o = prices_open.get(code)
            if not o or o <= 0:
                continue
            sell_v = cur_v - tgt_v
            if spent_sell_turnover + sell_v > turnover_budget:
                sell_v = max(0.0, turnover_budget - spent_sell_turnover)
            if sell_v <= 0:
                continue
            sh_sell = sell_v / o
            sh_avail = state.shares.get(code, 0.0)
            sh_sell = min(sh_sell, sh_avail)
            if sh_sell <= 0:
                continue
            name = name_map.get(code, "")
            pc = float(pre_close.get(code, np.nan)) if code in pre_close.index else np.nan
            h = float(high_px.get(code, o)) if code in high_px.index else o
            l = float(low_px.get(code, o)) if code in low_px.index else o
            cpx = float(close_px.get(code, o)) if code in close_px.index else o
            if _is_one_word_limit_down(code, name, pc, o, h, l, cpx, exec_day, cfg.limit_eps):
                continue
            proceeds = sh_sell * o
            cost = proceeds * cfg.cost_one_side
            state.cash += proceeds - cost
            state.shares[code] = sh_avail - sh_sell
            sell_notional += proceeds
            spent_sell_turnover += proceeds
            spent_turnover = spent_sell_turnover
            trade_logs.append({"date": exec_day, "code": code, "side": "sell_rebal", "shares": sh_sell, "px": o})

        nav2 = _nav(state, prices_open)
        free_cash_left = idle_cash0  # 可无换手约束动用的期初现金（按买入扣减）
        replace_cash_left = min(sell_notional, turnover_budget)  # 卖出所得中可用于再投资的额度

        # --- 递延买入优先 ---
        new_deferred_buys: Dict[str, DeferredBuy] = {}
        pending_buys: List[tuple] = []

        for code, db in state.deferred_buys.items():
            if code not in target_set:
                continue
            pending_buys.append((0, code, db.periods_left))
        for code in target:
            if code in state.deferred_buys:
                continue
            pending_buys.append((1, code, cfg.max_buy_defer_periods))

        for prio, code, periods_left in sorted(pending_buys, key=lambda x: x[0]):
            o = prices_open.get(code)
            if not o or not np.isfinite(o) or o <= 0:
                continue
            tgt_v = target_value.get(code, nav2 / cfg.top_n)
            cur_v = _pos_value(code)
            buy_v = max(0.0, tgt_v - cur_v)
            if buy_v <= 0:
                continue

            from_free = min(buy_v, free_cash_left)
            from_replace = min(max(0.0, buy_v - from_free), replace_cash_left)
            buy_v = from_free + from_replace
            if buy_v <= 0:
                continue
            if buy_v < max(1000.0, o * 100):
                continue

            name = name_map.get(code, "")
            pc = float(pre_close.get(code, np.nan)) if code in pre_close.index else np.nan
            h = float(high_px.get(code, o)) if code in high_px.index else o
            l = float(low_px.get(code, o)) if code in low_px.index else o
            cpx = float(close_px.get(code, o)) if code in close_px.index else o
            blocked = _is_one_word_limit_up(code, name, pc, o, h, l, cpx, exec_day, cfg.limit_eps)
            if blocked:
                if prio == 1:
                    new_deferred_buys[code] = DeferredBuy(code, cfg.max_buy_defer_periods)
                    trade_logs.append({"date": exec_day, "code": code, "side": "buy_defer", "reason": "limit_up"})
                elif periods_left > 1:
                    new_deferred_buys[code] = DeferredBuy(code, periods_left - 1)
                    trade_logs.append({"date": exec_day, "code": code, "side": "buy_defer", "reason": "limit_up"})
                else:
                    trade_logs.append({"date": exec_day, "code": code, "side": "buy_abandon", "reason": "limit_up"})
                continue

            cost = buy_v * cfg.cost_one_side
            if state.cash < buy_v + cost:
                buy_v = state.cash / (1.0 + cfg.cost_one_side)
                cost = buy_v * cfg.cost_one_side
                # 按比例压缩 free/replace 已扣额度在下面重算
            if buy_v < max(1000.0, o * 100):
                continue
            sh = buy_v / o
            state.cash -= buy_v + cost
            state.frozen[code] = state.frozen.get(code, 0.0) + sh
            state.last_prices[code] = o
            # 扣减额度
            use_free = min(from_free, buy_v)
            use_rep = buy_v - use_free
            free_cash_left = max(0.0, free_cash_left - use_free)
            replace_cash_left = max(0.0, replace_cash_left - use_rep)
            spent_turnover += buy_v
            trade_logs.append({"date": exec_day, "code": code, "side": "buy", "shares": sh, "px": o, "cost": cost})

        for code, db in state.deferred_buys.items():
            if code in new_deferred_buys or code not in target_set:
                continue
            if code in target_set:
                new_deferred_buys[code] = db
        state.deferred_buys = new_deferred_buys

        # 收盘盯市
        prices_close = {
            c: float(close_px[c])
            for c in close_px.index
            if np.isfinite(close_px[c])
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
                "cash": state.cash,
                "n_hold": sum(1 for sh in state.shares.values() if sh > 0)
                + sum(1 for sh in state.frozen.values() if sh > 0),
                "turnover_used": spent_sell_turnover / nav0 if nav0 else 0.0,
            }
        )

    nav_df = pd.DataFrame(records)
    trades_df = pd.DataFrame(trade_logs)
    return {"nav": nav_df, "trades": trades_df, "final_state": state}
