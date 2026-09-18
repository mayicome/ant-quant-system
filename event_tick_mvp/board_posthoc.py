# -*- coding: utf-8 -*-
"""事后板块名次标签（不改选股 / 开仓规则）。

对每笔买入：取开仓日前一交易日的东财行业/概念榜，挂到 fill.meta，
再按买卖配对做分组观察（胜率、期望、止损占比）。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from event_tick_mvp.types import FillRecord


def _prev_board_day(as_of: date, *, max_back: int = 10) -> Optional[date]:
    """向前找最近有 industry_rank CSV 的交易日。"""
    import os

    from utils.eastmoney_board_rank_ctx import board_rank_csv_paths, default_board_rank_dir

    base = default_board_rank_dir()
    for i in range(1, max_back + 1):
        d = as_of - timedelta(days=i)
        paths = board_rank_csv_paths(d, rank_dir=base)
        ind = paths.get("industry") or ""
        if ind and os.path.isfile(ind):
            return d
    return None


class BoardRankTagger:
    """按日缓存 code→最佳行业/概念名次。"""

    def __init__(self) -> None:
        self._cache: Dict[date, Dict[str, Any]] = {}

    def _maps_for(self, rank_day: date) -> Dict[str, Any]:
        if rank_day in self._cache:
            return self._cache[rank_day]
        try:
            from utils.eastmoney_board_rank_ctx import build_code_owned_board_rank_maps

            maps = build_code_owned_board_rank_maps(rank_day, rank_kind="chg")
        except Exception as e:
            maps = {
                "error": str(e),
                "code_best_industry": {},
                "code_best_concept": {},
            }
        self._cache[rank_day] = maps
        return maps

    def tag_buy_fill(self, fill: FillRecord) -> None:
        if fill.side != "buy":
            return
        entry_d = fill.ts.date() if hasattr(fill.ts, "date") else fill.ts
        if not isinstance(entry_d, date):
            return
        rank_day = _prev_board_day(entry_d)
        meta = dict(fill.meta or {})
        meta["board_rank_as_of"] = rank_day.isoformat() if rank_day else None
        if rank_day is None:
            meta["industry_rank_t1"] = None
            meta["concept_rank_t1"] = None
            fill.meta = meta
            return
        maps = self._maps_for(rank_day)
        ind = (maps.get("code_best_industry") or {}).get(fill.code) or {}
        con = (maps.get("code_best_concept") or {}).get(fill.code) or {}
        meta["industry_rank_t1"] = ind.get("rank")
        meta["industry_name_t1"] = ind.get("name")
        meta["industry_chg_t1"] = ind.get("chg")
        meta["concept_rank_t1"] = con.get("rank")
        meta["concept_name_t1"] = con.get("name")
        meta["concept_chg_t1"] = con.get("chg")
        if maps.get("error"):
            meta["board_rank_error"] = str(maps.get("error"))
        fill.meta = meta


def _rank_bucket(rank: Optional[int]) -> str:
    if rank is None:
        return "missing"
    try:
        r = int(rank)
    except (TypeError, ValueError):
        return "missing"
    if r <= 10:
        return "1-10"
    if r <= 30:
        return "11-30"
    if r <= 50:
        return "31-50"
    return "50+"


def summarize_board_posthoc(fills: Sequence[FillRecord]) -> Dict[str, Any]:
    """买卖配对后，按开仓时 T-1 行业/概念名次分组观察。"""
    buy_by_code: Dict[str, FillRecord] = {}
    pairs: List[Tuple[FillRecord, FillRecord, float]] = []
    for f in fills:
        if f.side == "buy":
            buy_by_code[f.code] = f
        elif f.side == "sell" and f.code in buy_by_code:
            b = buy_by_code.pop(f.code)
            pnl = (f.price - b.price) / b.price if b.price else 0.0
            pairs.append((b, f, pnl))

    def _group(key: str) -> Dict[str, Any]:
        buckets: Dict[str, List[Tuple[float, str]]] = defaultdict(list)
        for b, s, pnl in pairs:
            raw = (b.meta or {}).get(key)
            try:
                rk = int(raw) if raw is not None else None
            except (TypeError, ValueError):
                rk = None
            buckets[_rank_bucket(rk)].append((pnl, str(s.reason or "")))

        out: Dict[str, Any] = {}
        order = ["1-10", "11-30", "31-50", "50+", "missing"]
        for name in order:
            rows = buckets.get(name) or []
            if not rows:
                continue
            n = len(rows)
            wins = [p for p, _ in rows if p >= 0]
            losses = [p for p, _ in rows if p < 0]
            sl = sum(1 for _, r in rows if r == "stop_loss")
            exp = sum(p for p, _ in rows) / n
            out[name] = {
                "n": n,
                "win_rate": round(len(wins) / n, 4),
                "avg_pnl": round(exp, 6),
                "expectancy": round(exp, 6),
                "stop_loss_pct": round(sl / n, 4),
                "payoff_ratio": (
                    round((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)), 4)
                    if wins and losses
                    else None
                ),
            }
        return out

    tagged = sum(
        1
        for b, _, _ in pairs
        if (b.meta or {}).get("industry_rank_t1") is not None
        or (b.meta or {}).get("concept_rank_t1") is not None
    )
    return {
        "n_pairs": len(pairs),
        "n_tagged": tagged,
        "by_industry_rank_t1": _group("industry_rank_t1"),
        "by_concept_rank_t1": _group("concept_rank_t1"),
        "note": "事后观察分组，未改动开仓规则",
    }
