# -*- coding: utf-8 -*-
"""大盘状态：禁新买判定（决策日 T）。"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

BACKDROP_BEAR = "空头趋势底色"
BACKDROP_BULL = "多头趋势底色"
PULSE_KILL = "杀跌脉冲"


def load_regime_daily(
    path: Optional[Path] = None,
) -> pd.DataFrame:
    p = path or (ROOT / "data" / "market_regime" / "market_regime_daily.csv")
    df = pd.read_csv(p)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df.set_index("trade_date").sort_index()


def load_csi_close(path: Optional[Path] = None) -> pd.Series:
    p = path or (ROOT / "data" / "index_cache" / "000985.SH.csv")
    df = pd.read_csv(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    s = df.set_index("date")["close"].astype(float).sort_index()
    s.name = "csi_close"
    return s


def build_allow_new_buys(
    decision_dates,
    *,
    mode: str,
    ma_window: int = 60,
    regime_df: Optional[pd.DataFrame] = None,
    csi_close: Optional[pd.Series] = None,
) -> Dict[date, bool]:
    """
    返回决策日 → 是否允许新开仓。
    标签缺失日：允许买入（coverage 不足时不误伤），由汇总 coverage_warn 提示。
    """
    mode = (mode or "off").strip().lower()
    out: Dict[date, bool] = {d: True for d in decision_dates}
    if mode in ("", "off", "none"):
        return out

    if mode == "csi_ma":
        if csi_close is None:
            csi_close = load_csi_close()
        ma = csi_close.rolling(int(ma_window), min_periods=max(5, int(ma_window) // 2)).mean()
        for d in decision_dates:
            if d not in csi_close.index or d not in ma.index:
                out[d] = True
                continue
            px, m = float(csi_close.loc[d]), float(ma.loc[d])
            if not (px == px and m == m):
                out[d] = True
                continue
            out[d] = px >= m
        return out

    if regime_df is None:
        regime_df = load_regime_daily()

    for d in decision_dates:
        if d not in regime_df.index:
            out[d] = True
            continue
        row = regime_df.loc[d]
        backdrop = str(row.get("backdrop", "") or "")
        pulse = str(row.get("pulse", "") or "")
        if mode == "bear_skip":
            out[d] = backdrop != BACKDROP_BEAR
        elif mode == "bull_only":
            out[d] = backdrop == BACKDROP_BULL
        elif mode == "no_kill":
            out[d] = pulse != PULSE_KILL
        elif mode == "no_kill_bear":
            out[d] = not (backdrop == BACKDROP_BEAR or pulse == PULSE_KILL)
        else:
            out[d] = True
    return out


def regime_coverage_stats(
    decision_dates,
    allow: Dict[date, bool],
    regime_df: Optional[pd.DataFrame] = None,
    *,
    mode: str,
) -> dict:
    mode = (mode or "off").strip().lower()
    n = len(decision_dates)
    blocked = sum(1 for d in decision_dates if not allow.get(d, True))
    labeled = 0
    if mode not in ("", "off", "none", "csi_ma") and regime_df is not None:
        labeled = sum(1 for d in decision_dates if d in regime_df.index)
    return {
        "regime_block_frac": blocked / max(n, 1),
        "regime_label_cov": (labeled / max(n, 1)) if mode not in ("", "off", "none", "csi_ma") else 1.0,
        "coverage_warn": bool(
            mode not in ("", "off", "none", "csi_ma") and labeled < 0.5 * n
        ),
    }
