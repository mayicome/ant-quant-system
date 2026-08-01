"""
回测指标：收益、年化、最大回撤、胜率、交易次数等。
支持仅买入策略：持仓胜率、平均持仓浮盈率（基于期末仍持仓的盯市）。
"""

from typing import List, Dict, Any, Optional


def compute_metrics(
    equity_curve: List[Dict[str, Any]],
    trades: List[Dict[str, Any]],
    initial_cash: float,
    final_positions: Optional[Dict[str, Dict[str, Any]]] = None,
    last_prices: Optional[Dict[str, float]] = None,
    initial_positions: Optional[Dict[str, Dict[str, Any]]] = None,
    buy_and_hold_total: Optional[float] = None,
) -> Dict[str, Any]:
    """
    根据权益曲线与成交明细计算常用指标。
    若传入 final_positions 与 last_prices，则额外计算仅买入策略的持仓胜率、平均持仓浮盈率。
    若传入 initial_positions，则初始权益 = initial_cash + sum(持仓数量*成本)，用于收益率与回撤基准。
    若同时传入 initial_positions 与 buy_and_hold_total，则计算持股不动收益率与超额收益（策略收益 - 持股不动收益）。

    equity_curve: [ {"date", "total"}, ... ] 按日期升序
    trades: [ {"date", "side", "price", "volume", "amount", "commission"}, ... ]
    initial_cash: 初始资金
    final_positions: 可选，{ code: {"volume", "cost"}, ... } 期末仍持仓
    last_prices: 可选，{ code: 期末价 }，与 final_positions 配合用于持仓胜率/平均浮盈率
    initial_positions: 可选，{ code: {"volume", "cost"}, ... } 回测起始时的持仓，用于计算初始权益
    buy_and_hold_total: 可选，期初持仓「持股不动」到期末的资产，用于对比

    返回: {
        "total_return": float,
        "buy_and_hold_return": float (仅当有 buy_and_hold_total 时),
        "excess_return": float (仅当有 buy_and_hold_total 时),
        "annual_return": float, ...
    }
    """
    # 初始权益 = 现金 + 初始持仓市值（按成本计）
    initial_equity = float(initial_cash)
    if initial_positions:
        for pos in initial_positions.values():
            vol = int(pos.get("volume") or 0)
            cost = float(pos.get("cost") or 0)
            initial_equity += vol * cost

    if not equity_curve or initial_equity <= 0:
        out = {
            "total_return": 0.0,
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "max_drawdown_date": "",
            "trade_count": 0,
            "win_count": 0,
            "win_rate": 0.0,
            "final_total": initial_equity,
            "position_count": 0,
            "position_hit_rate": 0.0,
            "avg_position_unrealized_pct": 0.0,
        }
        if buy_and_hold_total is not None:
            out["buy_and_hold_return"] = 0.0
            out["excess_return"] = 0.0
        return out

    totals = [e["total"] for e in equity_curve]
    final_total = totals[-1] if totals else initial_equity
    total_return = (final_total - initial_equity) / initial_equity if initial_equity else 0.0

    n_days = len(equity_curve)
    years = n_days / 365.0 if n_days else 0
    annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0.0

    peak = initial_equity
    max_dd = 0.0
    max_dd_date = ""
    for e, t in zip(equity_curve, totals):
        if t > peak:
            peak = t
        dd = (peak - t) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
            max_dd_date = e.get("date", "")

    win_count = 0  # 有平仓时可按单笔盈亏算；仅买入时用持仓胜率
    trade_count = len(trades)

    position_count = 0
    position_hit_rate = 0.0
    avg_position_unrealized_pct = 0.0
    if final_positions and last_prices:
        position_count = len(final_positions)
        wins = 0
        returns = []
        for code, pos in final_positions.items():
            cost = float(pos.get("cost") or 0)
            if cost <= 0:
                continue
            last_p = last_prices.get(code)
            if last_p is None or last_p <= 0:
                continue
            if last_p > cost:
                wins += 1
            returns.append((last_p - cost) / cost)
        if position_count > 0:
            position_hit_rate = wins / position_count
        if returns:
            avg_position_unrealized_pct = sum(returns) / len(returns) * 100.0

    result = {
        "total_return": round(total_return, 4),
        "annual_return": round(annual_return, 4),
        "max_drawdown": round(max_dd, 4),
        "max_drawdown_date": max_dd_date,
        "trade_count": trade_count,
        "win_count": win_count,
        "win_rate": round(win_count / trade_count, 4) if trade_count else 0.0,
        "final_total": round(final_total, 2),
        "position_count": position_count,
        "position_hit_rate": round(position_hit_rate, 4),
        "avg_position_unrealized_pct": round(avg_position_unrealized_pct, 2),
    }
    if buy_and_hold_total is not None and initial_equity > 0:
        bh_return = (buy_and_hold_total - initial_equity) / initial_equity
        result["buy_and_hold_return"] = round(bh_return, 4)
        result["excess_return"] = round(total_return - bh_return, 4)
    return result
