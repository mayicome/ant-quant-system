# -*- coding: utf-8 -*-
"""日线面板：后复权 OHLC + 不复权 OHLC + 成交额。"""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from utils.daily_adjust_paths import cache_dir_for, full_dir_for
from utils.daily_cache_reader import load_daily_bars, to_full_stock_code

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ALL_A_CSV = _PROJECT_ROOT / "data" / "all_a_stocks.csv"


def _parse_ymd(val) -> Optional[date]:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    digits = "".join(ch for ch in str(val) if ch.isdigit())
    if len(digits) < 8:
        return None
    try:
        return datetime.strptime(digits[:8], "%Y%m%d").date()
    except ValueError:
        return None


def is_bj_code(code: str) -> bool:
    c = str(code).split(".")[0].zfill(6)
    return c.startswith(("8", "4", "920"))


def load_universe_meta(
    csv_path: Optional[Path] = None,
    *,
    exclude_bj: bool = True,
) -> pd.DataFrame:
    path = Path(csv_path) if csv_path else _ALL_A_CSV
    df = pd.read_csv(path, dtype=str, encoding="utf-8")
    cols = list(df.columns)
    if len(cols) < 3:
        raise ValueError(f"all_a_stocks.csv 列不足: {cols}")
    code_col, name_col, list_col = cols[0], cols[1], cols[2]
    for c in ("证券代码", "代码", "code"):
        if c in df.columns:
            code_col = c
            break
    for c in ("证券简称", "名称", "name"):
        if c in df.columns:
            name_col = c
            break
    for c in ("上市日期", "list_date"):
        if c in df.columns:
            list_col = c
            break
    out = pd.DataFrame(
        {
            "code": df[code_col].map(lambda x: str(x).split(".")[0].zfill(6)),
            "name": df[name_col].astype(str),
            "list_date": df[list_col].map(_parse_ymd),
        }
    )
    out = out.dropna(subset=["code"]).drop_duplicates("code")
    if exclude_bj:
        out = out[~out["code"].map(is_bj_code)]
    return out.reset_index(drop=True)


def list_available_codes(adjust: str = "hfq") -> List[str]:
    codes: set[str] = set()
    for root in (full_dir_for(adjust), cache_dir_for(adjust)):
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            if not name.endswith(".csv"):
                continue
            full = name[:-4].upper()
            if full.endswith(".BJ"):
                continue
            code6 = full.split(".", 1)[0] if "." in full else full
            if code6.isdigit() and not is_bj_code(code6):
                codes.add(code6.zfill(6))
    return sorted(codes)


def _read_one_csv(path: Path) -> Optional[pd.DataFrame]:
    if not path.is_file():
        return None
    try:
        raw = pd.read_csv(path)
    except Exception:
        return None
    if raw is None or raw.empty or "date" not in raw.columns or "close" not in raw.columns:
        return None
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce").dt.date
    for c in ("open", "high", "low", "close", "volume", "amount"):
        if c in raw.columns:
            raw[c] = pd.to_numeric(raw[c], errors="coerce")
    raw = raw.dropna(subset=["date", "close"]).sort_values("date")
    raw = raw.drop_duplicates("date", keep="last")
    return raw.reset_index(drop=True)


def load_stock_ohlcv(code: str, adjust: str) -> Optional[pd.DataFrame]:
    try:
        df = load_daily_bars(code, adjust=adjust)
        if df is not None and not df.empty:
            return df
    except Exception:
        pass
    full_code = to_full_stock_code(code)
    for root in (full_dir_for(adjust), cache_dir_for(adjust)):
        p = Path(root) / f"{full_code}.csv"
        df = _read_one_csv(p)
        if df is not None and not df.empty:
            return df
    return None


def build_panels(
    codes: Iterable[str],
    *,
    adjust_hfq: str = "hfq",
    adjust_raw: str = "none",
    start: Optional[date] = None,
    end: Optional[date] = None,
    progress_every: int = 500,
) -> Dict[str, pd.DataFrame]:
    """
    宽表面板：
      close/open/high/low_hfq, amount,
      open/close/high/low/volume_raw
    """
    series_bags = {
        "close_hfq": {},
        "open_hfq": {},
        "high_hfq": {},
        "low_hfq": {},
        "amount": {},
        "open_raw": {},
        "close_raw": {},
        "high_raw": {},
        "low_raw": {},
        "volume_raw": {},
    }

    codes_list = list(codes)
    for i, code in enumerate(codes_list, 1):
        hfq = load_stock_ohlcv(code, adjust_hfq)
        raw = load_stock_ohlcv(code, adjust_raw)
        if hfq is None or hfq.empty:
            continue
        hfq = hfq.set_index("date").sort_index()
        if start:
            hfq = hfq[hfq.index >= start]
        if end:
            hfq = hfq[hfq.index <= end]
        if hfq.empty:
            continue
        series_bags["close_hfq"][code] = hfq["close"]
        if "open" in hfq.columns:
            series_bags["open_hfq"][code] = hfq["open"]
        if "high" in hfq.columns:
            series_bags["high_hfq"][code] = hfq["high"]
        if "low" in hfq.columns:
            series_bags["low_hfq"][code] = hfq["low"]
        if "amount" in hfq.columns:
            series_bags["amount"][code] = hfq["amount"]

        if raw is not None and not raw.empty:
            raw = raw.set_index("date").sort_index()
            if start:
                raw = raw[raw.index >= start]
            if end:
                raw = raw[raw.index <= end]
            series_bags["open_raw"][code] = raw["open"]
            series_bags["close_raw"][code] = raw["close"]
            if "high" in raw.columns:
                series_bags["high_raw"][code] = raw["high"]
            if "low" in raw.columns:
                series_bags["low_raw"][code] = raw["low"]
            if "volume" in raw.columns:
                series_bags["volume_raw"][code] = raw["volume"]
            if "amount" in raw.columns:
                series_bags["amount"][code] = raw["amount"]

        if progress_every and i % progress_every == 0:
            print(f"[panel] loaded {i}/{len(codes_list)} ...", flush=True)

    def _wide(d: dict) -> pd.DataFrame:
        if not d:
            return pd.DataFrame()
        return pd.DataFrame(d).sort_index()

    return {k: _wide(v) for k, v in series_bags.items()}


def align_calendar_from_panels(panels: Dict[str, pd.DataFrame]) -> List[date]:
    close = panels.get("close_hfq")
    if close is None or close.empty:
        return []
    min_n = max(50, int(0.05 * close.shape[1]))
    cnt = close.notna().sum(axis=1)
    return sorted(d for d, n in cnt.items() if n >= min_n)
