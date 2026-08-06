# -*- coding: utf-8 -*-
"""
Tick 本地缓存（全项目统一口径）：

  data/ticks/{YYYYMMDD}/{6位代码}.parquet   （主格式，五档展平 + zstd/snappy）
  data/ticks/{YYYYMMDD}/{6位代码}.pkl       （旧格式，只读回退；有数据）

读取顺序：进程内内存 → 本地 parquet → 本地 pkl → QMT 拉取。
落盘条件：规范化后通过 is_full_day_ticks；不完整数据仅用内存、不落盘。
读盘时若发现不完整，删除该文件并改走 QMT。
新写入只写 parquet（盘口展平为 ask1..ask5 等标量列）；读出时拼回 list 列供真突破/滑点使用。
警告：pkl 本身就是可用 tick 副本。没有同代码 parquet 前禁止删除 pkl。
本地迁移（无下载）：tools/convert_tick_pkl_to_parquet.py

内存缓存生命周期：回测按「加载一天 → 用完 → 清一天」调用
clear_tick_memory_cache(trade_date=...)，避免跨日囤积导致十几 GB。
"""
from __future__ import annotations

import os
import shutil
import time as time_module
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

TradeDateInput = Union[date, datetime, str]

_MEMORY_CACHE: Dict[tuple, Any] = {}

# 落盘保留列（另含 ask1..ask5 / bid1..bid5 / ask1_vol.. / bid1_vol..）
_DISK_BASE_COLS = (
    "time_ts",
    "lastPrice",
    "open",
    "high",
    "low",
    "lastClose",
    "amount",
    "volume",
)
_DROP_ON_DISK = frozenset(
    {
        "time",
        "datetime",
        "askPrice",
        "bidPrice",
        "askVol",
        "bidVol",
        "askVolume",
        "bidVolume",
        "pe",
        "settlementPrice",
        "lastSettlementPrice",
        "openInt",
        "stockStatus",
        "pvolume",
        "tickvol",
        "transactionNum",
    }
)
TICK_RETENTION_DAYS = 65  # 兼容旧调用；空间足够时不再按此砍天数
# 按盘符剩余空间清理：低于 MIN 才删最旧日目录，删到 TARGET 或只剩 MIN_KEEP_DAYS
TICK_MIN_FREE_GB = 40.0
TICK_TARGET_FREE_GB = 60.0
TICK_MIN_KEEP_DAYS = 20


def clear_tick_memory_cache(
    trade_date: Optional[TradeDateInput] = None,
) -> int:
    """
    释放进程内 tick 内存缓存。

    - trade_date 给定：只删该交易日所有 (code6, date) 条目
    - trade_date 为 None：清空全部

    返回删除的条目数。磁盘文件不受影响。
    """
    if trade_date is None:
        n = len(_MEMORY_CACHE)
        _MEMORY_CACHE.clear()
        return n
    d, _ = parse_trade_date(trade_date)
    iso = d.isoformat()
    keys = [k for k in list(_MEMORY_CACHE.keys()) if len(k) >= 2 and k[1] == iso]
    for k in keys:
        _MEMORY_CACHE.pop(k, None)
    return len(keys)


def tick_memory_cache_size() -> int:
    """当前进程内缓存的 (股票, 日期) 条数。"""
    return len(_MEMORY_CACHE)


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def norm_code6(code: str) -> str:
    c = (code or "").strip().replace(".", "")
    if len(c) < 6:
        c = c.zfill(6) if c else ""
    else:
        c = c[:6]
    return c


def qmt_result_empty(raw: Any) -> bool:
    """QMT get_market_data_ex 单条结果是否为空（DataFrame 不能用 `or []` 判断）。"""
    if raw is None:
        return True
    try:
        import pandas as pd

        if isinstance(raw, pd.DataFrame):
            return raw.empty
    except ImportError:
        pass
    try:
        return len(raw) == 0
    except Exception:
        return True


def parse_trade_date(trade_date: TradeDateInput) -> Tuple[date, str]:
    if isinstance(trade_date, datetime):
        d = trade_date.date()
        return d, d.strftime("%Y%m%d")
    if isinstance(trade_date, date):
        return trade_date, trade_date.strftime("%Y%m%d")
    s = str(trade_date or "").strip().replace("-", "").replace("/", "")[:8]
    if len(s) == 8 and s.isdigit():
        return date(int(s[0:4]), int(s[4:6]), int(s[6:8])), s
    raise ValueError(f"invalid trade_date: {trade_date!r}")


def tick_cache_path(code_6: str, trade_date: TradeDateInput) -> str:
    """主路径：parquet。"""
    c6 = norm_code6(code_6)
    _, ymd = parse_trade_date(trade_date)
    return os.path.join(project_root(), "data", "ticks", ymd, f"{c6}.parquet")


def tick_cache_path_legacy_pkl(code_6: str, trade_date: TradeDateInput) -> str:
    """旧 pkl 路径（只读回退）。"""
    c6 = norm_code6(code_6)
    _, ymd = parse_trade_date(trade_date)
    return os.path.join(project_root(), "data", "ticks", ymd, f"{c6}.pkl")


def tick_day_dir(trade_date: TradeDateInput) -> str:
    _, ymd = parse_trade_date(trade_date)
    return os.path.join(project_root(), "data", "ticks", ymd)


def tick_cache_file_ready(code_6: str, trade_date: TradeDateInput) -> bool:
    """本地是否已有可用 tick 文件（parquet 或旧 pkl）。"""
    for path in (
        tick_cache_path(code_6, trade_date),
        tick_cache_path_legacy_pkl(code_6, trade_date),
    ):
        try:
            if os.path.isfile(path) and os.path.getsize(path) > 32:
                return True
        except OSError:
            continue
    return False


def full_stock_code(code_6: str) -> str:
    c6 = norm_code6(code_6)
    if c6.startswith("6"):
        return f"{c6}.SH"
    if c6.startswith(("0", "3")):
        return f"{c6}.SZ"
    if c6.startswith(("4", "8", "920")):
        return f"{c6}.BJ"
    return f"{c6}.SZ"


def _level_scalar(raw: Any, idx: int) -> float:
    try:
        if isinstance(raw, (list, tuple)):
            if idx >= len(raw):
                return 0.0
            v = raw[idx]
        else:
            return 0.0
        if v is None:
            return 0.0
        return float(v)
    except Exception:
        return 0.0


def flatten_depth_columns(df: Any) -> Any:
    """把 askPrice/bidPrice/askVol/bidVol 的 list 展平为 ask1..ask5 等标量列。"""
    try:
        import pandas as pd
    except ImportError:
        return df
    if df is None or not isinstance(df, pd.DataFrame) or len(df) == 0:
        return df
    data = df
    already = all(f"ask{i}" in data.columns for i in range(1, 6)) and all(
        f"bid{i}" in data.columns for i in range(1, 6)
    )
    if already:
        return data

    ap = data["askPrice"] if "askPrice" in data.columns else None
    bp = data["bidPrice"] if "bidPrice" in data.columns else None
    av = data["askVol"] if "askVol" in data.columns else (
        data["askVolume"] if "askVolume" in data.columns else None
    )
    bv = data["bidVol"] if "bidVol" in data.columns else (
        data["bidVolume"] if "bidVolume" in data.columns else None
    )

    for i in range(1, 6):
        idx = i - 1
        if ap is not None and f"ask{i}" not in data.columns:
            data[f"ask{i}"] = ap.map(lambda x, j=idx: _level_scalar(x, j))
        if bp is not None and f"bid{i}" not in data.columns:
            data[f"bid{i}"] = bp.map(lambda x, j=idx: _level_scalar(x, j))
        if av is not None and f"ask{i}_vol" not in data.columns:
            data[f"ask{i}_vol"] = av.map(lambda x, j=idx: _level_scalar(x, j))
        if bv is not None and f"bid{i}_vol" not in data.columns:
            data[f"bid{i}_vol"] = bv.map(lambda x, j=idx: _level_scalar(x, j))
        # 已有 askPrice1 风格时也映射过来
        for src, dst in (
            (f"askPrice{i}", f"ask{i}"),
            (f"bidPrice{i}", f"bid{i}"),
            (f"askVol{i}", f"ask{i}_vol"),
            (f"bidVol{i}", f"bid{i}_vol"),
            (f"askVolume{i}", f"ask{i}_vol"),
            (f"bidVolume{i}", f"bid{i}_vol"),
        ):
            if src in data.columns and dst not in data.columns:
                data[dst] = pd.to_numeric(data[src], errors="coerce").fillna(0.0)
    return data


def expand_depth_lists(df: Any) -> Any:
    """读盘后：若有 ask1..ask5，拼回 askPrice/askVol 等 list 列。"""
    try:
        import pandas as pd
    except ImportError:
        return df
    if df is None or not isinstance(df, pd.DataFrame) or len(df) == 0:
        return df
    data = df
    has_flat = all(f"ask{i}" in data.columns for i in range(1, 6))
    has_list = "askPrice" in data.columns

    if has_flat and (not has_list or not isinstance(data["askPrice"].iloc[0], (list, tuple))):
        asks = [data[f"ask{i}"].astype(float).fillna(0.0) for i in range(1, 6)]
        bids = [
            data[f"bid{i}"].astype(float).fillna(0.0)
            if f"bid{i}" in data.columns
            else pd.Series([0.0] * len(data), index=data.index)
            for i in range(1, 6)
        ]
        ask_vols = [
            data[f"ask{i}_vol"].astype(float).fillna(0.0)
            if f"ask{i}_vol" in data.columns
            else pd.Series([0.0] * len(data), index=data.index)
            for i in range(1, 6)
        ]
        bid_vols = [
            data[f"bid{i}_vol"].astype(float).fillna(0.0)
            if f"bid{i}_vol" in data.columns
            else pd.Series([0.0] * len(data), index=data.index)
            for i in range(1, 6)
        ]
        data["askPrice"] = [
            [float(asks[j].iloc[r]) for j in range(5)] for r in range(len(data))
        ]
        data["bidPrice"] = [
            [float(bids[j].iloc[r]) for j in range(5)] for r in range(len(data))
        ]
        data["askVol"] = [
            [float(ask_vols[j].iloc[r]) for j in range(5)] for r in range(len(data))
        ]
        data["bidVol"] = [
            [float(bid_vols[j].iloc[r]) for j in range(5)] for r in range(len(data))
        ]
    return data


def prepare_tick_for_disk(df: Any) -> Optional[Any]:
    """规范化 + 展平五档 + 裁列，供 parquet 落盘。"""
    try:
        import pandas as pd
    except ImportError:
        return None
    data = coerce_tick_dataframe(df)
    if data is None or len(data) == 0:
        return None
    if "time_ts" not in data.columns:
        if "time" in data.columns:
            data["time_ts"] = data["time"]
        else:
            return None
    data = flatten_depth_columns(data)
    keep: List[str] = [c for c in _DISK_BASE_COLS if c in data.columns]
    for i in range(1, 6):
        for c in (f"ask{i}", f"bid{i}", f"ask{i}_vol", f"bid{i}_vol"):
            if c in data.columns:
                keep.append(c)
    # 补齐缺失的五档列，保证 schema 稳定
    out = data.loc[:, [c for c in keep if c in data.columns]].copy()
    for i in range(1, 6):
        for c in (f"ask{i}", f"bid{i}", f"ask{i}_vol", f"bid{i}_vol"):
            if c not in out.columns:
                out[c] = 0.0
    for c in out.columns:
        if c == "time_ts":
            out[c] = pd.to_numeric(out[c], errors="coerce").astype("int64")
        else:
            out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0).astype("float64")
    return out


def prepare_tick_for_use(df: Any) -> Optional[Any]:
    """读盘后展开五档 list，并补 datetime。"""
    try:
        import pandas as pd
    except ImportError:
        return None
    if df is None:
        return None
    try:
        data = df if isinstance(df, pd.DataFrame) else pd.DataFrame(df)
    except Exception:
        return None
    if len(data) == 0:
        return None
    data = expand_depth_lists(data)
    if "time" not in data.columns and "time_ts" in data.columns:
        data["time"] = data["time_ts"]
    data = coerce_tick_dataframe(data)
    if data is None or len(data) == 0:
        return None
    return expand_depth_lists(data)


def normalize_tick_dataframe(raw: Any) -> Optional[Any]:
    """
    将 QMT tick 原始表规范为回测/分析共用格式：
    time_ts, datetime(Asia/Shanghai), lastPrice>0，仅保留交易时段（含 9:15 起集合竞价段）。
    """
    if raw is None:
        return None
    try:
        import pandas as pd
    except ImportError:
        return None

    try:
        data = pd.DataFrame(raw) if not isinstance(raw, pd.DataFrame) else raw.copy()
    except Exception:
        return None
    if len(data) == 0 or "time" not in data.columns:
        return None

    data = data.sort_values("time")
    if "lastPrice" in data.columns:
        data = data[data["lastPrice"] > 0]
    if len(data) == 0:
        return None

    data["time_ts"] = data["time"]
    if hasattr(data["time"].dtype, "kind") and data["time"].dtype.kind in ("i", "u", "f"):
        data["datetime"] = pd.to_datetime(data["time"], unit="ms")
    else:
        data["datetime"] = pd.to_datetime(data["time"])

    dt = data["datetime"]
    if dt.dt.tz is None:
        dt = dt.dt.tz_localize("UTC").dt.tz_convert("Asia/Shanghai")
    else:
        dt = dt.dt.tz_convert("Asia/Shanghai")
    data["datetime"] = dt
    # 含 15:00 收盘竞价及约 15:05–15:30 盘后；供盘后量能读盘，不再截断在 15:00
    mask = (
        ((dt.dt.hour == 9) & (dt.dt.minute >= 15))
        | (dt.dt.hour == 10)
        | ((dt.dt.hour == 11) & (dt.dt.minute <= 30))
        | ((dt.dt.hour >= 13) & (dt.dt.hour < 15))
        | ((dt.dt.hour == 15) & (dt.dt.minute <= 31))
    )
    data = data[mask].copy()
    if len(data) == 0:
        return None
    return flatten_depth_columns(data)


def coerce_tick_dataframe(df: Any) -> Optional[Any]:
    """
    将磁盘/内存中的 tick 表整理为撮合可读的格式（补 datetime、lastPrice、时区）。
    失败返回 None。
    """
    if df is None:
        return None
    try:
        import pandas as pd
    except ImportError:
        return None

    try:
        if not isinstance(df, pd.DataFrame):
            data = pd.DataFrame(df)
        else:
            data = df.copy()
    except Exception:
        return None

    if len(data) == 0:
        return None

    if "time" not in data.columns and "time_ts" in data.columns:
        data["time"] = data["time_ts"]

    if "datetime" not in data.columns and "time" in data.columns:
        return normalize_tick_dataframe(data)

    if "datetime" in data.columns:
        try:
            data["datetime"] = pd.to_datetime(data["datetime"], errors="coerce")
        except Exception:
            return None
        data = data[data["datetime"].notna()]
        if len(data) == 0:
            return None
        dt = data["datetime"]
        if getattr(dt.dt, "tz", None) is None:
            if "time" in data.columns:
                try:
                    t0 = data["time"].iloc[0]
                    if hasattr(t0, "item"):
                        t0 = t0.item()
                    if isinstance(t0, (int, float)) and float(t0) > 1e12:
                        data["datetime"] = pd.to_datetime(data["time"], unit="ms", errors="coerce")
                        data["datetime"] = (
                            data["datetime"].dt.tz_localize("UTC").dt.tz_convert("Asia/Shanghai")
                        )
                    else:
                        data["datetime"] = (
                            data["datetime"].dt.tz_localize("Asia/Shanghai")
                        )
                except Exception:
                    try:
                        data["datetime"] = (
                            data["datetime"].dt.tz_localize("Asia/Shanghai")
                        )
                    except Exception:
                        pass
            else:
                try:
                    data["datetime"] = data["datetime"].dt.tz_localize("Asia/Shanghai")
                except Exception:
                    pass
        else:
            try:
                data["datetime"] = data["datetime"].dt.tz_convert("Asia/Shanghai")
            except Exception:
                pass

    if "lastPrice" not in data.columns:
        for alt in ("last_price", "price", "last", "matchPrice"):
            if alt in data.columns:
                data["lastPrice"] = data[alt]
                break

    if "lastPrice" not in data.columns or "datetime" not in data.columns:
        return None

    try:
        data = data[data["lastPrice"] > 0]
    except Exception:
        return None
    return data if len(data) > 0 else None


def is_full_day_ticks(df: Any) -> bool:
    """
    粗略判断 tick 是否覆盖「整天」交易时段。
    必须同时看到集合竞价/开盘附近与 14:55 后 tick，才允许落盘，避免残缺缓存被永久复用。
    """
    try:
        if df is None or len(df) == 0:
            return False
        data = coerce_tick_dataframe(df)
        if data is None or len(data) == 0 or "datetime" not in data.columns:
            return False
        dt = data["datetime"]
        if hasattr(dt.dt, "tz") and dt.dt.tz is not None:
            dt = dt.dt.tz_convert("Asia/Shanghai")
        has_open = bool(((dt.dt.hour == 9) & (dt.dt.minute <= 35)).any()) or bool(
            ((dt.dt.hour == 9) & (dt.dt.minute >= 15) & (dt.dt.minute <= 25)).any()
        )
        has_close = bool(((dt.dt.hour == 14) & (dt.dt.minute >= 55)).any()) or bool(
            ((dt.dt.hour == 15) & (dt.dt.minute == 0)).any()
        )
        return has_open and has_close
    except Exception:
        return False


def _write_parquet_file(df: Any, path: str) -> bool:
    """写 parquet；优先 zstd，旧环境回退 snappy/gzip。"""
    tmp = path + ".tmp"
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except Exception:
        pass

    # 1) pyarrow 直写
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pandas(df, preserve_index=False)
        for comp in ("zstd", "snappy", "gzip"):
            try:
                pq.write_table(table, tmp, compression=comp)
                os.replace(tmp, path)
                return True
            except Exception:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass
    except Exception:
        pass

    # 2) pandas.to_parquet
    for comp in ("zstd", "snappy", "gzip"):
        try:
            df.to_parquet(tmp, compression=comp, index=False)
            os.replace(tmp, path)
            return True
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
    return False


def _read_parquet_file(path: str) -> Optional[Any]:
    try:
        import pandas as pd

        return pd.read_parquet(path)
    except Exception:
        try:
            import pyarrow.parquet as pq

            return pq.read_table(path).to_pandas()
        except Exception:
            return None


def read_tick_cache(code_6: str, trade_date: TradeDateInput) -> Optional[Any]:
    c6 = norm_code6(code_6)
    if not c6:
        return None

    candidates: List[Tuple[str, str]] = [
        (tick_cache_path(c6, trade_date), "parquet"),
        (tick_cache_path_legacy_pkl(c6, trade_date), "pkl"),
    ]
    for path, kind in candidates:
        if not os.path.exists(path):
            continue
        try:
            import pandas as pd

            if kind == "parquet":
                df = _read_parquet_file(path)
            else:
                df = pd.read_pickle(path)
            data = prepare_tick_for_use(df)
            if data is not None and len(data) > 0:
                if is_full_day_ticks(data):
                    return data
                try:
                    os.remove(path)
                except Exception:
                    pass
        except Exception:
            pass
    return None


def write_tick_cache(code_6: str, trade_date: TradeDateInput, df: Any) -> bool:
    """仅当 tick 覆盖整天交易时段时写入 parquet。"""
    if df is None or len(df) == 0:
        return False
    if not is_full_day_ticks(df):
        return False
    disk_df = prepare_tick_for_disk(df)
    if disk_df is None or len(disk_df) == 0:
        return False
    try:
        path = tick_cache_path(code_6, trade_date)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not _write_parquet_file(disk_df, path):
            return False
        # 同代码旧 pkl 可删，避免双份占盘
        legacy = tick_cache_path_legacy_pkl(code_6, trade_date)
        if os.path.isfile(legacy):
            try:
                os.remove(legacy)
            except Exception:
                pass
        return True
    except Exception:
        return False


def fetch_tick_from_qmt(code_6: str, trade_date: TradeDateInput) -> Optional[Any]:
    c6 = norm_code6(code_6)
    if not c6:
        return None
    _, ymd = parse_trade_date(trade_date)
    try:
        import xtquant.xtdata as xtdata
    except ImportError:
        return None

    full_code = full_stock_code(c6)
    start_str = ymd + "091500"
    end_str = ymd + "153100"
    try:
        xtdata.download_history_data(full_code, "tick", start_str, end_str)
        time_module.sleep(0.05)
    except Exception:
        pass
    try:
        df_map = xtdata.get_market_data_ex(
            [], [full_code], period="tick", start_time=start_str, end_time=end_str, count=-1
        )
    except Exception:
        return None
    if not isinstance(df_map, dict) or full_code not in df_map or qmt_result_empty(df_map.get(full_code)):
        return None
    return df_map[full_code]


def load_tick_data(
    code_6: str,
    trade_date: TradeDateInput,
    *,
    use_memory_cache: bool = True,
    allow_xtdata_fallback: bool = True,
    allow_on_demand: bool = True,
    on_demand_timeout_sec: Optional[float] = None,
) -> Optional[Any]:
    """
    加载单只股票单日 tick：先读 parquet/pkl。
    allow_on_demand=True 且 qmt_mode=builtin/standalone 时，缺文件可经 data_sync_request
    请求大 QMT 落盘后再读（不直连 miniQMT）。
    allow_xtdata_fallback=True 时才回退 xtquant.xtdata（默认兼容旧调用；推荐值/回测请关）。
    完整 tick 才落盘；不完整时若走 xtdata 仍可能返回当次数据但不落盘。
    """
    c6 = norm_code6(code_6)
    if not c6:
        return None
    d, _ = parse_trade_date(trade_date)
    mem_key = (c6, d.isoformat())

    if use_memory_cache:
        cached = _MEMORY_CACHE.get(mem_key)
        if cached is not None and len(cached) > 0:
            coerced = prepare_tick_for_use(cached)
            if coerced is not None and len(coerced) > 0:
                _MEMORY_CACHE[mem_key] = coerced
                return coerced

    disk = read_tick_cache(c6, trade_date)
    if disk is not None and len(disk) > 0:
        if use_memory_cache:
            _MEMORY_CACHE[mem_key] = disk
        return disk

    if allow_on_demand:
        try:
            from utils.data_sync_request import (
                ensure_tick_dataframe,
                use_on_demand_qmt_sync,
            )
        except ImportError:
            use_on_demand_qmt_sync = lambda: False  # type: ignore[assignment, misc]
            ensure_tick_dataframe = None  # type: ignore[assignment]
        if use_on_demand_qmt_sync() and callable(ensure_tick_dataframe):
            kw = {}
            if on_demand_timeout_sec is not None:
                kw["timeout_sec"] = float(on_demand_timeout_sec)
            disk = ensure_tick_dataframe(c6, d, **kw)
            if disk is not None and len(disk) > 0:
                if use_memory_cache:
                    _MEMORY_CACHE[mem_key] = disk
                return disk

    if not allow_xtdata_fallback:
        return None

    raw = fetch_tick_from_qmt(c6, trade_date)
    data = normalize_tick_dataframe(raw)
    data = prepare_tick_for_use(data) if data is not None else None
    if data is None or len(data) == 0:
        return None

    write_tick_cache(c6, trade_date, data)
    if use_memory_cache:
        _MEMORY_CACHE[mem_key] = data
    return data


def load_ticks_for_codes(
    stock_codes_6: List[str],
    trade_date: TradeDateInput,
    *,
    use_memory_cache: bool = True,
    allow_on_demand: bool = True,
    allow_xtdata_fallback: bool = True,
) -> Dict[str, Any]:
    """批量加载：先统一读缓存；allow_on_demand/allow_xtdata_fallback 为 False 时本地缺失即跳过。"""
    result: Dict[str, Any] = {}
    codes: List[str] = []
    for c in stock_codes_6 or []:
        c6 = norm_code6(c)
        if c6 and c6 not in codes:
            codes.append(c6)
    if not codes:
        return result

    d_obj, _ = parse_trade_date(trade_date)
    missing: List[str] = []

    for c6 in codes:
        mem_key = (c6, d_obj.isoformat())
        if use_memory_cache:
            cached = _MEMORY_CACHE.get(mem_key)
            if cached is not None and len(cached) > 0:
                coerced = prepare_tick_for_use(cached)
                if coerced is not None and len(coerced) > 0:
                    _MEMORY_CACHE[mem_key] = coerced
                    result[c6] = coerced
                    continue

        disk = read_tick_cache(c6, trade_date)
        if disk is not None and len(disk) > 0:
            if use_memory_cache:
                _MEMORY_CACHE[mem_key] = disk
            result[c6] = disk
            continue
        missing.append(c6)

    if not missing:
        return result

    if not allow_on_demand and not allow_xtdata_fallback:
        return result

    try:
        from utils.data_sync_request import use_on_demand_qmt_sync, wait_tick_cache
    except ImportError:
        use_on_demand_qmt_sync = lambda: False  # type: ignore[assignment, misc]
        wait_tick_cache = None  # type: ignore[assignment]

    if allow_on_demand and use_on_demand_qmt_sync() and callable(wait_tick_cache):
        try:
            from utils.data_sync_request import (
                BACKTEST_TICK_TIMEOUT_SEC,
                tick_pool_wait_timeout_sec,
                tick_sync_unavailable,
                wait_tick_cache_pool,
            )
        except ImportError:
            BACKTEST_TICK_TIMEOUT_SEC = 90.0  # type: ignore
            tick_sync_unavailable = lambda c, d: False  # type: ignore
            wait_tick_cache_pool = None  # type: ignore
            tick_pool_wait_timeout_sec = lambda n: 90.0  # type: ignore

        wait_list = [
            c for c in missing if not tick_sync_unavailable(c, trade_date)
        ]
        skipped = [c for c in missing if tick_sync_unavailable(c, trade_date)]
        if wait_list and wait_tick_cache_pool is not None:
            wait_sec = min(
                float(BACKTEST_TICK_TIMEOUT_SEC),
                tick_pool_wait_timeout_sec(len(wait_list)),
            )
            ready_codes, still_missing = wait_tick_cache_pool(
                wait_list,
                trade_date,
                timeout_sec=wait_sec,
            )
            for c6 in ready_codes:
                disk = read_tick_cache(c6, trade_date)
                if disk is not None and len(disk) > 0:
                    if use_memory_cache:
                        _MEMORY_CACHE[(c6, d_obj.isoformat())] = disk
                    result[c6] = disk
            missing = list(still_missing) + skipped
        else:
            missing = wait_list + skipped
        if not missing:
            return result

    if not allow_xtdata_fallback:
        return result

    try:
        import xtquant.xtdata as xtdata
    except ImportError:
        xtdata = None  # type: ignore

    if xtdata is not None:
        _, ymd = parse_trade_date(trade_date)
        start_str = ymd + "091500"
        end_str = ymd + "153100"
        full_codes = [full_stock_code(c) for c in missing]
        try:
            for fc in full_codes:
                try:
                    xtdata.download_history_data(fc, "tick", start_str, end_str)
                except Exception:
                    pass
            df_map = xtdata.get_market_data_ex(
                [], full_codes, period="tick", start_time=start_str, end_time=end_str, count=-1
            )
        except Exception:
            df_map = None

        if isinstance(df_map, dict) and df_map:
            still: List[str] = []
            for c6, fc in zip(missing, full_codes):
                if fc not in df_map or qmt_result_empty(df_map.get(fc)):
                    still.append(c6)
                    continue
                data = normalize_tick_dataframe(df_map[fc])
                data = prepare_tick_for_use(data) if data is not None else None
                if data is None or len(data) == 0:
                    still.append(c6)
                    continue
                write_tick_cache(c6, trade_date, data)
                if use_memory_cache:
                    _MEMORY_CACHE[(c6, d_obj.isoformat())] = data
                result[c6] = data
            missing = still

    # 上面已做 pool 等待 / xtdata 回退；此处禁止再按只 ensure（默认 240s/只会把回测拖死）
    for c6 in missing:
        if c6 in result:
            continue
        df = load_tick_data(
            c6,
            trade_date,
            use_memory_cache=use_memory_cache,
            allow_on_demand=allow_on_demand,
            allow_xtdata_fallback=allow_xtdata_fallback,
        )
        if df is not None and len(df) > 0:
            result[c6] = df

    return result


def list_tick_day_dirs() -> List[str]:
    """返回 data/ticks 下形如 YYYYMMDD 的子目录名（升序）。"""
    root = os.path.join(project_root(), "data", "ticks")
    if not os.path.isdir(root):
        return []
    out: List[str] = []
    for name in os.listdir(root):
        if len(name) == 8 and name.isdigit() and os.path.isdir(os.path.join(root, name)):
            out.append(name)
    out.sort()
    return out


def _disk_free_bytes(path: str) -> Optional[int]:
    """返回 path 所在盘剩余字节；失败返回 None。"""
    try:
        return int(shutil.disk_usage(path).free)
    except Exception:
        return None


def _dir_size_bytes(path: str) -> int:
    total = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(path):
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    except Exception:
        return 0
    return total


def purge_old_tick_cache_dirs(
    keep_days: Optional[int] = None,
    *,
    min_free_gb: float = TICK_MIN_FREE_GB,
    target_free_gb: float = TICK_TARGET_FREE_GB,
    min_keep_days: int = TICK_MIN_KEEP_DAYS,
    dry_run: bool = False,
) -> List[str]:
    """
    清理旧 tick 日目录。默认按磁盘剩余空间，不固定砍到 N 天。

    - keep_days 有值：兼容旧行为，只保留最新 keep_days 天。
    - 默认：仅当 ticks 所在盘剩余 < min_free_gb 时，从最旧日开始删，
      直到剩余 >= target_free_gb，或目录数只剩 min_keep_days。
      空间充足时不删，可存超过三个月。

    返回已删除（或 dry_run 将删除）的目录名列表。
    """
    root = os.path.join(project_root(), "data", "ticks")
    days = list_tick_day_dirs()
    if not days:
        return []

    if keep_days is not None:
        keep = max(1, int(keep_days))
        if len(days) <= keep:
            return []
        to_drop = days[: len(days) - keep]
        removed: List[str] = []
        for ymd in to_drop:
            path = os.path.join(root, ymd)
            if dry_run:
                removed.append(ymd)
                continue
            try:
                shutil.rmtree(path)
                removed.append(ymd)
            except Exception:
                pass
        return removed

    min_keep = max(1, int(min_keep_days or TICK_MIN_KEEP_DAYS))
    min_free = float(min_free_gb if min_free_gb is not None else TICK_MIN_FREE_GB)
    target_free = float(target_free_gb if target_free_gb is not None else TICK_TARGET_FREE_GB)
    if target_free < min_free:
        target_free = min_free

    probe = root if os.path.isdir(root) else project_root()
    free_b = _disk_free_bytes(probe)
    if free_b is None:
        return []
    min_b = int(min_free * (1024.0 ** 3))
    target_b = int(target_free * (1024.0 ** 3))
    if free_b >= min_b:
        return []

    removed: List[str] = []
    remaining = list(days)
    sim_free = free_b

    while remaining and len(remaining) > min_keep:
        if sim_free >= target_b:
            break
        ymd = remaining[0]
        path = os.path.join(root, ymd)
        size = _dir_size_bytes(path) if dry_run else 0
        if dry_run:
            remaining.pop(0)
            removed.append(ymd)
            sim_free += max(0, size)
            continue
        try:
            shutil.rmtree(path)
            remaining.pop(0)
            removed.append(ymd)
        except Exception:
            remaining.pop(0)
            continue
        free_now = _disk_free_bytes(probe)
        if free_now is not None:
            sim_free = free_now
        else:
            break
    return removed
