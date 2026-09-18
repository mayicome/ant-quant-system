# -*- coding: utf-8 -*-
"""数据访问：日线（不复权）+ tick→1m；回放范围 = watch_list ∪ 持仓。"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import pandas as pd

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def project_root() -> str:
    return _PROJECT_ROOT


def list_tick_days(
    start: Optional[date] = None,
    end: Optional[date] = None,
    *,
    root: Optional[str] = None,
    min_files: int = 0,
) -> List[date]:
    """列出 data/ticks 下有目录的交易日（升序），可裁剪区间。

    min_files>0 时仅保留该日 parquet/pkl 数量达到阈值的日期
    （早期目录往往只有几十只票，无法做全市场回测）。
    """
    base = Path(root or os.path.join(project_root(), "data", "ticks"))
    if not base.is_dir():
        return []
    out: List[date] = []
    for p in sorted(base.iterdir()):
        if not p.is_dir() or not p.name.isdigit() or len(p.name) != 8:
            continue
        try:
            d = date(int(p.name[:4]), int(p.name[4:6]), int(p.name[6:8]))
        except ValueError:
            continue
        if start and d < start:
            continue
        if end and d > end:
            continue
        if min_files > 0:
            n = sum(
                1
                for f in p.iterdir()
                if f.is_file() and f.suffix.lower() in (".parquet", ".pkl")
            )
            if n < int(min_files):
                continue
        out.append(d)
    return out


def tick_codes_for_day(trade_date: date, *, root: Optional[str] = None) -> Set[str]:
    """某日本地已落盘 tick 的 code6 集合。"""
    base = Path(root or os.path.join(project_root(), "data", "ticks"))
    p = base / trade_date.strftime("%Y%m%d")
    if not p.is_dir():
        return set()
    out: Set[str] = set()
    for f in p.iterdir():
        if not f.is_file() or f.suffix.lower() not in (".parquet", ".pkl"):
            continue
        c6 = f.stem.zfill(6)[-6:]
        if len(c6) == 6 and c6.isdigit():
            out.add(c6)
    return out


def load_universe_codes() -> List[str]:
    path = os.path.join(project_root(), "data", "a_share_universe.json")
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    codes = raw.get("codes") if isinstance(raw, dict) else raw
    out: List[str] = []
    for c in codes or []:
        c6 = str(c).split(".")[0].zfill(6)[-6:]
        if len(c6) == 6:
            out.append(c6)
    return out


def load_name_map() -> Dict[str, str]:
    path = os.path.join(project_root(), "data", "all_a_stocks.csv")
    out: Dict[str, str] = {}
    if not os.path.isfile(path):
        return _names_from_info_json()
    try:
        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    except Exception:
        try:
            df = pd.read_csv(path, dtype=str, encoding="gbk")
        except Exception:
            return _names_from_info_json()
    code_col = None
    name_col = None
    for c in df.columns:
        cs = str(c)
        if code_col is None and ("代码" in cs or cs.lower() in ("code", "stock_code")):
            code_col = c
        if name_col is None and ("名称" in cs or "名字" in cs or cs.lower() == "name"):
            name_col = c
    if code_col is None:
        return _names_from_info_json()
    for _, row in df.iterrows():
        c6 = str(row.get(code_col) or "").split(".")[0].zfill(6)[-6:]
        if len(c6) != 6:
            continue
        out[c6] = str(row.get(name_col) or "") if name_col else ""
    return out if out else _names_from_info_json()


def _names_from_info_json() -> Dict[str, str]:
    path = os.path.join(project_root(), "data", "all_a_stock_info.json")
    out: Dict[str, str] = {}
    if not os.path.isfile(path):
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return out
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if not isinstance(v, dict):
            continue
        c6 = str(k).split(".")[0].zfill(6)[-6:]
        out[c6] = str(v.get("name") or "")
    return out


def load_industry_map(info_path: Optional[str] = None) -> Dict[str, str]:
    """code6 -> 主行业（all_a_stock_info['industry']）。"""
    path = info_path or os.path.join(project_root(), "data", "all_a_stock_info.json")
    out: Dict[str, str] = {}
    if not os.path.isfile(path):
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return out
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if not isinstance(v, dict):
            continue
        c6 = str(k).split(".")[0].zfill(6)[-6:]
        ind = str(v.get("industry") or "").strip()
        out[c6] = ind
    return out


def replay_codes(
    watch_codes: Sequence[str],
    position_codes: Sequence[str],
) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for c in list(watch_codes) + list(position_codes):
        c6 = str(c or "").strip().split(".")[0].zfill(6)[-6:]
        if len(c6) != 6 or c6 in seen:
            continue
        seen.add(c6)
        out.append(c6)
    return out


class ProjectDailyStore:
    """进程内缓存日线 DF，按 as_of 切片。"""

    def __init__(self, root: Optional[str] = None):
        self.root = root or project_root()
        self._cache: Dict[str, pd.DataFrame] = {}

    def load_daily(self, code: str, end: date, lookback: int = 400) -> pd.DataFrame:
        # 仅读本地 daily_cache，禁止走 QMT 按需同步（回测扫描全市场时会卡死）
        from utils.daily_cache_reader import load_daily_from_cache

        c6 = str(code).split(".")[0].zfill(6)[-6:]
        if c6 not in self._cache:
            df = load_daily_from_cache(c6, through_date=None, adjust="none")
            if df is None or getattr(df, "empty", True):
                self._cache[c6] = pd.DataFrame()
            else:
                d = df.copy()
                if "date" in d.columns:
                    d["date"] = pd.to_datetime(d["date"]).dt.date
                    d = d.sort_values("date")
                self._cache[c6] = d
        df = self._cache[c6]
        if df is None or df.empty or "date" not in df.columns:
            return pd.DataFrame()
        sub = df[df["date"] <= end]
        if lookback and len(sub) > lookback:
            sub = sub.iloc[-lookback:]
        return sub.copy()


def load_ticks_1m(code: str, trade_date: date) -> pd.DataFrame:
    """读取当日 tick，聚合成 1 分钟 bar。

    列：datetime, open, high, low, close, amount_cum, amount（分钟增量）, volume_cum
    amount 字段在源数据为累计成交额。
    聚合结果缓存到 data/ticks_1m/{YYYYMMDD}/{code}.parquet 加速重复回测。
    """
    import os

    from utils.tick_data_cache import read_tick_cache, tick_cache_path

    c6 = str(code).split(".")[0].zfill(6)[-6:]
    ymd = trade_date.strftime("%Y%m%d")
    cache_path = os.path.join(project_root(), "data", "ticks_1m", ymd, f"{c6}.parquet")
    if os.path.isfile(cache_path):
        try:
            cached = pd.read_parquet(cache_path)
            if cached is not None and not cached.empty and "close" in cached.columns:
                return cached
        except Exception:
            pass

    # 优先直接读 parquet 落盘列，避免 prepare_tick_for_use 全量拷贝
    pq = tick_cache_path(c6, trade_date)
    raw = None
    if os.path.isfile(pq):
        try:
            cols = ["time_ts", "lastPrice", "amount", "volume", "ask1", "bid1"]
            raw = pd.read_parquet(pq, columns=cols)
            # time_ts 为 UTC epoch ms；转为上海墙钟 naive
            raw["datetime"] = (
                pd.to_datetime(raw["time_ts"], unit="ms", utc=True)
                .dt.tz_convert("Asia/Shanghai")
                .dt.tz_localize(None)
            )
        except Exception:
            raw = None
    if raw is None:
        raw = read_tick_cache(c6, trade_date)
        if raw is None or getattr(raw, "empty", True):
            return pd.DataFrame()
        raw = raw.copy()

    if "datetime" not in raw.columns and "time_ts" in raw.columns:
        raw["datetime"] = pd.to_datetime(raw["time_ts"], unit="ms")
    if "datetime" not in raw.columns:
        return pd.DataFrame()
    df = raw
    df["datetime"] = pd.to_datetime(df["datetime"])
    try:
        if getattr(df["datetime"].dt, "tz", None) is not None:
            df["datetime"] = df["datetime"].dt.tz_localize(None)
    except Exception:
        pass
    px = pd.to_numeric(df.get("lastPrice"), errors="coerce")
    amt = pd.to_numeric(df.get("amount"), errors="coerce").fillna(0.0)
    vol = pd.to_numeric(df.get("volume"), errors="coerce").fillna(0.0)
    ask1 = pd.to_numeric(df.get("ask1"), errors="coerce") if "ask1" in df.columns else None
    bid1 = pd.to_numeric(df.get("bid1"), errors="coerce") if "bid1" in df.columns else None
    work = pd.DataFrame(
        {
            "datetime": df["datetime"],
            "lastPrice": px,
            "amount_cum": amt,
            "volume_cum": vol,
        }
    )
    if ask1 is not None:
        work["ask1"] = ask1
    if bid1 is not None:
        work["bid1"] = bid1
    work = work.dropna(subset=["lastPrice", "datetime"]).sort_values("datetime")
    if work.empty:
        return pd.DataFrame()
    work = work.set_index("datetime")
    ohlc = work["lastPrice"].resample("1min").ohlc()
    ohlc.columns = ["open", "high", "low", "close"]
    amt_cum = work["amount_cum"].resample("1min").last().ffill()
    vol_cum = work["volume_cum"].resample("1min").last().ffill()
    out = ohlc.copy()
    out["amount_cum"] = amt_cum
    out["volume_cum"] = vol_cum
    out["amount"] = out["amount_cum"].diff().clip(lower=0).fillna(out["amount_cum"])
    if "ask1" in work.columns:
        out["ask1"] = work["ask1"].resample("1min").last()
    if "bid1" in work.columns:
        out["bid1"] = work["bid1"].resample("1min").last()
    out = out.dropna(subset=["close"])
    try:
        t = out.index.time
        mask = [
            (
                (x >= datetime.strptime("09:30", "%H:%M").time())
                and (x <= datetime.strptime("11:30", "%H:%M").time())
            )
            or (
                (x >= datetime.strptime("13:00", "%H:%M").time())
                and (x <= datetime.strptime("15:00", "%H:%M").time())
            )
            for x in t
        ]
        out = out.loc[mask]
    except Exception:
        pass
    out = out.reset_index()
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        out.to_parquet(cache_path, index=False)
    except Exception:
        pass
    return out


def open_window_amount_from_ticks(code: str, trade_date: date, window_min: int) -> float:
    """廉价取开盘后 window_min 分钟累计成交额（只读 parquet 两列，不聚 1m）。"""
    import os

    from utils.tick_data_cache import tick_cache_path, tick_cache_path_legacy_pkl

    c6 = str(code).split(".")[0].zfill(6)[-6:]
    pq = tick_cache_path(c6, trade_date)
    start = datetime.combine(trade_date, datetime.strptime("09:30", "%H:%M").time())
    end = start + timedelta(minutes=max(0, int(window_min)))
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    if os.path.isfile(pq):
        try:
            df = pd.read_parquet(pq, columns=["time_ts", "amount"])
            ts = pd.to_numeric(df["time_ts"], errors="coerce")
            amt = pd.to_numeric(df["amount"], errors="coerce")
            mask = (ts >= start_ms) & (ts < end_ms)
            sub = amt.loc[mask].dropna()
            if len(sub):
                return float(sub.iloc[-1])
            # 窗口内无点：取 end 之前最后累计额
            sub2 = amt.loc[ts <= end_ms].dropna()
            return float(sub2.iloc[-1]) if len(sub2) else 0.0
        except Exception:
            pass

    # pkl 回退：走完整读取
    from utils.tick_data_cache import read_tick_cache

    raw = read_tick_cache(c6, trade_date)
    if raw is None or getattr(raw, "empty", True):
        return 0.0
    if "amount" not in raw.columns:
        return 0.0
    if "datetime" in raw.columns:
        dt = pd.to_datetime(raw["datetime"])
        try:
            if getattr(dt.dt, "tz", None) is not None:
                dt = dt.dt.tz_localize(None)
        except Exception:
            pass
        mask = (dt >= start) & (dt < end)
        sub = pd.to_numeric(raw.loc[mask, "amount"], errors="coerce").dropna()
        if len(sub):
            return float(sub.iloc[-1])
        sub2 = pd.to_numeric(raw.loc[dt <= end, "amount"], errors="coerce").dropna()
        return float(sub2.iloc[-1]) if len(sub2) else 0.0
    return 0.0
