# -*- coding: utf-8 -*-
"""第 2 层：候选池 + 超跌缩量低吸事件 → watch_list（日频收盘）。"""
from __future__ import annotations

from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Set

import pandas as pd

from event_tick_mvp.config import MvpConfig
from event_tick_mvp.types import WatchItem


def is_excluded_board_or_st(code: str, name: str = "") -> bool:
    """剔北交所、ST/*ST。"""
    c = str(code or "").split(".")[0].zfill(6)[-6:]
    n = str(name or "").upper()
    if "ST" in n:
        return True
    if c.startswith(("8", "4", "920")):
        return True
    return False


def filter_universe(
    codes: Sequence[str],
    *,
    names: Optional[Dict[str, str]] = None,
    listed_trading_days: Optional[Dict[str, int]] = None,
    suspended: Optional[Set[str]] = None,
    min_listed_days: int = 120,
) -> List[str]:
    """基础静态池：主板/创业/科创；剔北交、ST、上市不足、停牌。"""
    names = names or {}
    listed_trading_days = listed_trading_days or {}
    suspended = suspended or set()
    out: List[str] = []
    for raw in codes:
        c6 = str(raw or "").split(".")[0].zfill(6)[-6:]
        if len(c6) != 6:
            continue
        if c6 in suspended:
            continue
        if is_excluded_board_or_st(c6, names.get(c6, "")):
            continue
        n_listed = listed_trading_days.get(c6)
        if n_listed is not None and int(n_listed) < int(min_listed_days):
            continue
        # 允许 00/60 主板、30 创业、68 科创
        if not (
            c6.startswith(("00", "60", "30", "68"))
        ):
            continue
        out.append(c6)
    return out


def event_oversold_shrink(
    daily: pd.DataFrame,
    *,
    cfg: MvpConfig,
    as_of: Optional[date] = None,
) -> Optional[Dict[str, float]]:
    """单票 T 日是否触发超跌缩量。

    要求 daily 按日期升序，列含：open, high, low, close, amount（成交额）。
    成功返回指标 dict；否则 None。
    """
    if daily is None or daily.empty:
        return None
    df = daily.copy()
    # 统一列名
    colmap = {str(c).lower(): c for c in df.columns}
    def _col(*cands):
        for c in cands:
            if c in df.columns:
                return c
            if c.lower() in colmap:
                return colmap[c.lower()]
        return None

    c_open = _col("open", "开盘")
    c_high = _col("high", "最高")
    c_low = _col("low", "最低")
    c_close = _col("close", "收盘")
    c_amt = _col("amount", "成交额", "money")
    if not all([c_high, c_low, c_close, c_amt]):
        return None

    n = max(1, int(cfg.drawdown_n))
    ma_n = max(1, int(cfg.volume_ma_days))
    need = n + ma_n + 2
    if len(df) < need:
        return None

    row = df.iloc[-1]
    close_t = float(row[c_close])
    if close_t <= 0:
        return None

    # 近 N 日高点：T-N … T-1（不含 T）
    hist = df.iloc[-(n + 1) : -1]
    if len(hist) < n:
        return None
    high_n = float(hist[c_high].max())
    if high_n <= 0:
        return None
    drawdown = close_t / high_n - 1.0
    if drawdown > float(cfg.drawdown_threshold):
        # 回撤不够深
        return None

    # 缩量：当日成交额 / 过去 20 日日均（不含当日）
    hist_amt = df.iloc[-(ma_n + 1) : -1][c_amt].astype(float)
    if len(hist_amt) < ma_n or float(hist_amt.mean()) <= 0:
        return None
    vol_ratio = float(row[c_amt]) / float(hist_amt.mean())
    if vol_ratio > float(cfg.volume_ratio_max):
        return None

    # 附加条件
    if cfg.cond_a_bearish:
        if c_open is None:
            return None
        if float(row[c_close]) >= float(row[c_open]):
            return None

    if cfg.cond_b_body_ratio:
        if c_open is None:
            return None
        hi, lo = float(row[c_high]), float(row[c_low])
        op, cl = float(row[c_open]), float(row[c_close])
        amp = hi - lo
        if amp <= 0:
            return None
        body = abs(cl - op) / amp
        if body < float(cfg.body_ratio_min):
            return None

    if cfg.cond_c_no_new_low:
        low_t = float(row[c_low])
        prior_low = float(hist[c_low].min())
        if low_t < prior_low:
            return None

    return {
        "drawdown": drawdown,
        "volume_ratio": vol_ratio,
        "prev_close": close_t,
    }


def build_watch_list(
    as_of: date,
    universe: Sequence[str],
    load_daily,
    *,
    cfg: MvpConfig,
    industry_map: Optional[Dict[str, str]] = None,
) -> List[WatchItem]:
    """对 universe 逐票跑事件，产出 watch_list。"""
    industry_map = industry_map or {}
    out: List[WatchItem] = []
    for code in universe:
        c6 = str(code).split(".")[0].zfill(6)[-6:]
        try:
            daily = load_daily(c6, as_of)
        except Exception:
            continue
        hit = event_oversold_shrink(daily, cfg=cfg, as_of=as_of)
        if not hit:
            continue
        out.append(
            WatchItem(
                code=c6,
                as_of=as_of,
                prev_close=float(hit["prev_close"]),
                drawdown=float(hit["drawdown"]),
                volume_ratio=float(hit["volume_ratio"]),
                industry=str(industry_map.get(c6) or ""),
            )
        )
    return out
