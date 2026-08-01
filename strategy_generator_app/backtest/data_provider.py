"""
回测用历史行情：
1) 日频：按「截止日期 as_of_date」提供与实盘一致的 prices 结构（用于策略信号生成）。
2) Tick：按交易日加载 tick 数据，用于 tick 级成交模拟。
保证无前视：均线、涨跌停等均只使用 as_of_date 及之前的日线数据。
"""

from datetime import date
from typing import List, Dict, Any, Callable, Optional

# 供 simulator 使用：tick 表需有 time_ts(或 time), lastPrice；可选 open
import time as _time_module
import os
import math
import logging

logger = logging.getLogger(__name__)

# tick 数据：统一走 utils.tick_data_cache（先读 data/ticks/...pkl，无则 QMT，拉完必落盘）
_TICK_DATA_CACHE = None  # 兼容旧引用；实际缓存见 utils.tick_data_cache._MEMORY_CACHE
# 日线早盘/收盘视角按 (code6, date) 缓存，批量回测跨选股日重复命中同一标的时不再反复调 QMT。
_DAILY_MORNING_PRICE_CACHE: Dict[tuple, Dict[str, Any]] = {}
_DAILY_EOD_PRICE_CACHE: Dict[tuple, Dict[str, Any]] = {}
_TICK_CACHE_IMPORT_ERROR: Optional[str] = None


def _repo_root() -> str:
    from .tick_cache_loader import repo_root as _root

    return _root()


def _ensure_repo_root_on_sys_path() -> str:
    import sys

    root = _repo_root()
    if root in sys.path:
        try:
            sys.path.remove(root)
        except ValueError:
            pass
    sys.path.insert(0, root)
    return root


def _tick_cache_module_path() -> str:
    from .tick_cache_loader import tick_cache_file_path

    return tick_cache_file_path()


def _import_tick_data_cache():
    """加载 tick_data_cache（按文件路径，避免 utils 包名冲突）。"""
    global _TICK_CACHE_IMPORT_ERROR
    mod_path = _tick_cache_module_path()
    if not os.path.isfile(mod_path):
        _TICK_CACHE_IMPORT_ERROR = (
            f"缺少 tick 缓存模块：\n"
            f"  期望路径: {mod_path}\n"
            f"  （仓库根 = strategy_generator_app 的上一级）\n"
            "请将 utils/tick_data_cache.py 与 utils/__init__.py 放在该 utils 目录下。"
        )
        raise ModuleNotFoundError(_TICK_CACHE_IMPORT_ERROR)
    try:
        from .tick_cache_loader import tick_data_cache_module

        mod = tick_data_cache_module()
        fn = getattr(mod, "load_ticks_for_codes", None)
        if not callable(fn):
            raise ImportError("tick_data_cache 中缺少 load_ticks_for_codes")
        _TICK_CACHE_IMPORT_ERROR = None
        return fn
    except Exception as exc:
        _TICK_CACHE_IMPORT_ERROR = (
            f"加载 tick_data_cache 失败：{exc}\n"
            f"  文件: {mod_path}\n"
            "（按 strategy_generator_app 上一级/utils/tick_data_cache.py 定位，与盘符无关）"
        )
        raise ModuleNotFoundError(_TICK_CACHE_IMPORT_ERROR) from exc


def _tick_cache_attr(name: str, default: Any = None) -> Any:
    try:
        from .tick_cache_loader import tick_data_cache_module

        return getattr(tick_data_cache_module(), name, default)
    except Exception:
        return default


def _norm_code6(code: str) -> str:
    c = (code or "").strip()
    if len(c) < 6:
        c = c.zfill(6)
    return c[:6]


def _date_cache_key(d: date) -> str:
    return d.isoformat()


def _full_code(code_6: str) -> str:
    code_6 = _norm_code6(code_6)
    if code_6.startswith("6"):
        return f"{code_6}.SH"
    if code_6.startswith(("0", "3")):
        return f"{code_6}.SZ"
    if code_6.startswith(("4", "8", "920")):
        return f"{code_6}.BJ"
    return f"{code_6}.SZ"


def _limit_up_down_ratios(
    code_6: str,
    stock_name: str = "",
    as_of_date=None,
) -> tuple[float, float]:
    """回测用涨跌停幅度（与 utils.limit_ratio 对齐）。"""
    from utils.limit_ratio import get_limit_multipliers

    return get_limit_multipliers(code_6, stock_name, as_of_date)


def _stock_price_round(value: float, precision: int = 2) -> float:
    """A 股价格标准四舍五入（避免 Python round 的银行家舍入）。"""
    multiplier = 10 ** int(precision)
    return math.floor(float(value) * multiplier + 0.5) / multiplier


def _price_precision_for_code(code_6: str) -> int:
    try:
        import sys

        _ensure_repo_root_on_sys_path()
        from core.utils.security_type import SecurityTypeUtil

        return int(SecurityTypeUtil.get_price_precision(code_6))
    except Exception:
        return 2


def _find_high_column(data: Any) -> Optional[str]:
    for name in ("high", "最高价", "High", "HIGH"):
        if name in getattr(data, "columns", []):
            return name
    return None


def _prior_n_trading_day_high(
    highs: Any,
    n: int = 4,
    *,
    exclude_today: bool = True,
) -> Optional[float]:
    """最近 n 个交易日最高价；exclude_today=True 时不含最后一根 K（当日）。"""
    try:
        series = highs.astype(float)
    except Exception:
        return None
    cnt = len(series)
    if cnt < n:
        return None
    if exclude_today and cnt >= n + 1:
        seg = series.iloc[-(n + 1) : -1]
    else:
        seg = series.iloc[-n:]
    if len(seg) < n:
        return None
    v = float(seg.max())
    return v if v > 0 else None


def _find_open_column(data: Any) -> Optional[str]:
    if not hasattr(data, "columns"):
        return None
    for name in ("open", "开盘价", "Open", "OPEN"):
        if name in data.columns:
            return name
    return None


def _daily_bar_last_open(data: Any, open_col: Optional[str], pd: Any) -> Optional[float]:
    """当日 K 最后一行开盘价；空/0/NaN 视为无效。"""
    if not open_col or open_col not in getattr(data, "columns", []):
        return None
    try:
        v = data[open_col].iloc[-1]
        if v is None or (hasattr(pd, "isna") and pd.isna(v)):
            return None
        f = float(v)
        if f > 0 and not math.isnan(f):
            return f
    except Exception:
        pass
    return None


def _find_close_column(data: Any) -> Optional[str]:
    for name in ("close", "收盘价", "Close", "CLOSE"):
        if name in getattr(data, "columns", []):
            return name
    return None


def _allow_xtdata_fallback() -> bool:
    try:
        from strategy_generator_app.qmt_mode_config import allow_xtdata_daily_fallback
    except ImportError:
        from qmt_mode_config import allow_xtdata_daily_fallback
    return allow_xtdata_daily_fallback()


def _load_daily_df(code_6: str, through_date: date) -> tuple[Optional[Any], str]:
    """返回 (DataFrame|None, source) source: cache | xtdata | miss。"""
    import pandas as pd

    _ensure_repo_root_on_sys_path()
    from utils.daily_cache_reader import cache_file_exists, load_daily_dataframe

    had_cache = cache_file_exists(code_6)
    df = load_daily_dataframe(
        code_6,
        through_date=through_date,
        allow_xtdata_fallback=_allow_xtdata_fallback(),
        allow_on_demand=True,
    )
    if df is None or (hasattr(df, "empty") and df.empty):
        return None, "miss"
    if had_cache:
        return df, "cache"
    return df, "xtdata"


def _sort_daily_df(data: Any) -> Any:
    """daily_cache 的 time 可能既是 index 又是列，避免 sort_values 歧义。"""
    if data is None or (hasattr(data, "empty") and data.empty):
        return data
    if "time" in getattr(data, "columns", []):
        try:
            if getattr(data.index, "name", None) == "time" or (
                len(data.index.names) == 1 and data.index.names[0] == "time"
            ):
                return data.sort_index()
            return data.sort_values("time")
        except ValueError:
            return data.sort_index()
    return data.sort_index() if hasattr(data, "sort_index") else data


def _build_eod_row_from_df(
    data: Any,
    code_6: str,
    as_of_date: date,
    get_stock_name: Optional[Callable[[str], str]],
) -> Optional[Dict[str, Any]]:
    import pandas as pd

    if data is None or (hasattr(data, "empty") and data.empty):
        return None
    data = _sort_daily_df(data)
    close_col = _find_close_column(data)
    if close_col is None:
        return None
    closes = data[close_col].astype(float)
    high_col = _find_high_column(data)
    highs = data[high_col].astype(float) if high_col else None
    n = len(closes)
    if n == 0:
        return None
    current = float(closes.iloc[-1])
    pre_close = float(closes.iloc[-2]) if n >= 2 else current
    row: Dict[str, Any] = {
        "current": current,
        "最新价": current,
        "pre_close": pre_close,
        "昨收盘": pre_close,
    }
    if highs is not None:
        h4 = _prior_n_trading_day_high(highs, 4, exclude_today=False)
        if h4 is not None:
            row["前高（4日）"] = round(h4, 2)
    for period, key in [(5, "5日"), (10, "10日"), (20, "20日"), (30, "30日"), (60, "60日"), (120, "120日")]:
        days_needed = period - 1
        if n >= days_needed and days_needed > 0:
            row[key] = round(float(closes.iloc[-days_needed:].mean()), 2)
    nm = (get_stock_name(code_6) if get_stock_name else "") or ""
    up_ratio, down_ratio = _limit_up_down_ratios(code_6, nm, as_of_date)
    prec = _price_precision_for_code(code_6)
    row["涨停板"] = _stock_price_round(pre_close * up_ratio, prec)
    row["跌停板"] = _stock_price_round(pre_close * down_ratio, prec)
    return row


def _build_morning_row_from_df(
    data: Any,
    code_6: str,
    trade_date: date,
    get_stock_name: Optional[Callable[[str], str]],
    *,
    xtdata: Any = None,
    start_str: str = "",
    end_str: str = "",
) -> Optional[Dict[str, Any]]:
    import pandas as pd

    if data is None or (hasattr(data, "empty") and data.empty):
        return None
    data = _sort_daily_df(data)
    close_col = _find_close_column(data)
    if close_col is None:
        return None
    closes = data[close_col].astype(float)
    high_col = _find_high_column(data)
    highs = data[high_col].astype(float) if high_col else None
    n = len(closes)
    if n == 0:
        return None

    pre_close = float(closes.iloc[-2]) if n >= 2 else float(closes.iloc[-1])
    if xtdata is not None and start_str and end_str:
        today_open = _resolve_morning_today_open(
            xtdata, pd, _full_code(code_6), data, start_str, end_str, pre_close
        )
    else:
        open_col = _find_open_column(data)
        today_open = _daily_bar_last_open(data, open_col, pd)
        if today_open is None or today_open <= 0:
            today_open = float(pre_close)

    row: Dict[str, Any] = {
        "pre_close": pre_close,
        "昨收盘": pre_close,
        "current": today_open,
        "最新价": today_open,
        "今开盘": today_open,
    }
    if highs is not None:
        h4 = _prior_n_trading_day_high(highs, 4, exclude_today=True)
        if h4 is not None:
            row["前高（4日）"] = round(h4, 2)
    closes_prior = closes.iloc[:-1] if n >= 2 else closes
    n_prior = len(closes_prior)
    for period, key in [(5, "5日"), (10, "10日"), (20, "20日"), (30, "30日"), (60, "60日"), (120, "120日")]:
        days_needed = period - 1
        if n_prior >= days_needed and days_needed > 0:
            row[key] = round(float(closes_prior.iloc[-days_needed:].mean()), 2)
    nm = (get_stock_name(code_6) if get_stock_name else "") or ""
    up_ratio, down_ratio = _limit_up_down_ratios(code_6, nm, trade_date)
    prec = _price_precision_for_code(code_6)
    row["涨停板"] = _stock_price_round(pre_close * up_ratio, prec)
    row["跌停板"] = _stock_price_round(pre_close * down_ratio, prec)
    return row


def _fetch_daily_price_rows(
    missing: List[str],
    through_date: date,
    get_stock_name: Optional[Callable[[str], str]],
    *,
    morning: bool,
) -> Dict[str, Dict[str, Any]]:
    """批量拉取缺失日线视角 prices；builtin 下仅 daily_cache，无 xtdata 回退。"""
    out: Dict[str, Dict[str, Any]] = {}
    cache_n = miss_n = 0
    xtdata_n = 0
    xtdata = None
    start_str = end_str = ""
    use_xtdata = _allow_xtdata_fallback()
    if morning and use_xtdata:
        try:
            import xtquant.xtdata as xtdata  # type: ignore[assignment]
            from datetime import timedelta

            xtdata.enable_hello = False
            start_date = through_date - timedelta(days=400)
            start_str = start_date.strftime("%Y%m%d")
            end_str = through_date.strftime("%Y%m%d")
        except Exception:
            xtdata = None

    for code_6 in missing:
        df, src = _load_daily_df(code_6, through_date)
        if df is None:
            miss_n += 1
            continue
        if src == "cache":
            cache_n += 1
        elif src == "xtdata":
            xtdata_n += 1
        else:
            miss_n += 1
            continue
        if morning:
            row = _build_morning_row_from_df(
                df,
                code_6,
                through_date,
                get_stock_name,
                xtdata=xtdata,
                start_str=start_str,
                end_str=end_str,
            )
            cache_key = _DAILY_MORNING_PRICE_CACHE
        else:
            row = _build_eod_row_from_df(df, code_6, through_date, get_stock_name)
            cache_key = _DAILY_EOD_PRICE_CACHE
        if not row:
            miss_n += 1
            continue
        cache_key[(code_6, _date_cache_key(through_date))] = row
        out[code_6] = dict(row)

    label = "早盘" if morning else "EOD"
    if missing:
        if use_xtdata:
            logger.info(
                "%s prices %s: daily_cache=%d xtdata_fallback=%d miss=%d (of %d)",
                label,
                through_date.isoformat(),
                cache_n,
                xtdata_n,
                miss_n,
                len(missing),
            )
        else:
            logger.warning(
                "%s prices %s: daily_cache=%d miss=%d (of %d)；"
                "缺数据已/将提交大 QMT 同步（data/data_sync_requests.json）",
                label,
                through_date.isoformat(),
                cache_n,
                miss_n,
                len(missing),
            )
    return out


def _open_from_full_tick(xtdata: Any, full_code: str) -> Optional[float]:
    """与 key_price_calculator.py 一致：日线 open 未就绪时用快照开盘价。"""
    try:
        tmap = xtdata.get_full_tick([full_code])
        tk = tmap.get(full_code) if isinstance(tmap, dict) else None
        cand = None
        if isinstance(tk, dict):
            cand = (
                tk.get("open")
                or tk.get("openPrice")
                or tk.get("open_price")
                or tk.get("todayOpen")
            )
        elif tk is not None:
            cand = (
                getattr(tk, "open", None)
                or getattr(tk, "openPrice", None)
                or getattr(tk, "open_price", None)
                or getattr(tk, "todayOpen", None)
            )
        if cand is not None:
            f = float(cand)
            if f > 0 and not math.isnan(f):
                return f
    except Exception:
        pass
    return None


def _resolve_morning_today_open(
    xtdata: Any,
    pd: Any,
    full_code: str,
    data: Any,
    start_str: str,
    end_str: str,
    pre_close: float,
) -> float:
    """
    早盘回测/模拟用「今开盘」（与实盘 price_provider 口径一致）：
    优先当日日线 open；若为 0/空则重试拉日线；仍失败则 get_full_tick（多轮短间隔重试）；最后回退昨收。
    """
    open_col = _find_open_column(data)
    today_open = _daily_bar_last_open(data, open_col, pd)

    if today_open is None or today_open <= 0:
        for attempt in range(3):
            try:
                xtdata.download_history_data(full_code, "1d", start_str, end_str)
            except Exception:
                pass
            _time_module.sleep(0.12 + 0.08 * attempt)
            try:
                df2 = xtdata.get_market_data_ex(
                    [], [full_code], period="1d", start_time=start_str, end_time=end_str, count=-1
                )
            except Exception:
                continue
            if full_code not in df2 or len(df2[full_code]) == 0:
                continue
            data2 = pd.DataFrame(df2[full_code])
            if "time" not in data2.columns:
                data2 = data2.reset_index()
            data2 = data2.sort_values("time") if "time" in data2.columns else data2
            oc = _find_open_column(data2)
            today_open = _daily_bar_last_open(data2, oc, pd)
            if today_open is not None and today_open > 0:
                break

    if today_open is None or today_open <= 0:
        for attempt in range(3):
            ft = _open_from_full_tick(xtdata, full_code)
            if ft is not None and ft > 0:
                today_open = ft
                break
            _time_module.sleep(0.12 + 0.08 * attempt)

    if today_open is None or today_open <= 0:
        return float(pre_close)
    return float(today_open)


def _get_ticks_dir() -> str:
    root_fn = _tick_cache_attr("project_root")
    if callable(root_fn):
        return os.path.join(root_fn(), "data", "ticks")
    return os.path.join(_repo_root(), "data", "ticks")


def _get_tick_cache_path(stock_code_6: str, trade_date: date) -> str:
    path_fn = _tick_cache_attr("tick_cache_path")
    if callable(path_fn):
        return path_fn(stock_code_6, trade_date)
    code_6 = _norm_code6(stock_code_6)
    day = trade_date.strftime("%Y%m%d")
    return os.path.join(_get_ticks_dir(), day, f"{code_6}.parquet")


def get_historical_prices_for_date(
    stock_codes_6: List[str],
    as_of_date: date,
    get_stock_name: Optional[Callable[[str], str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    获取在 as_of_date 收盘后可知的行情，结构与 get_prices_with_key_points 一致。
    日线读 data/daily_cache；builtin 下缺数据会请求大 QMT 同步落盘（无外部 xtdata）。
    """
    result: Dict[str, Dict[str, Any]] = {}
    if not stock_codes_6:
        return result

    codes_6: List[str] = []
    for c in stock_codes_6:
        c6 = _norm_code6(c)
        if not c6:
            continue
        if c6 not in codes_6:
            codes_6.append(c6)
        ck = (c6, _date_cache_key(as_of_date))
        if ck in _DAILY_EOD_PRICE_CACHE:
            result[c6] = dict(_DAILY_EOD_PRICE_CACHE[ck])
        else:
            result[c6] = {"current": 0.0, "pre_close": 0.0}

    missing = [c for c in codes_6 if (c, _date_cache_key(as_of_date)) not in _DAILY_EOD_PRICE_CACHE]
    if missing:
        fetched = _fetch_daily_price_rows(
            missing, as_of_date, get_stock_name, morning=False
        )
        for code_6, row in fetched.items():
            result[code_6] = row

    return result


def get_historical_prices_for_morning(
    stock_codes_6: List[str],
    trade_date: date,
    get_stock_name: Optional[Callable[[str], str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    获取「当日早盘」视角的行情，用于回测引擎在开盘附近生成策略（与实盘早盘口径一致）。
    日线读 data/daily_cache；builtin 下缺数据会请求大 QMT 同步；今开盘取自 cache 当日 open。
    """
    result: Dict[str, Dict[str, Any]] = {}
    if not stock_codes_6:
        return result

    codes_6: List[str] = []
    for c in stock_codes_6:
        c6 = _norm_code6(c)
        if not c6:
            continue
        if c6 not in codes_6:
            codes_6.append(c6)
        ck = (c6, _date_cache_key(trade_date))
        if ck in _DAILY_MORNING_PRICE_CACHE:
            result[c6] = dict(_DAILY_MORNING_PRICE_CACHE[ck])
        else:
            result[c6] = {"current": 0.0, "pre_close": 0.0}

    missing = [c for c in codes_6 if (c, _date_cache_key(trade_date)) not in _DAILY_MORNING_PRICE_CACHE]
    if missing:
        fetched = _fetch_daily_price_rows(
            missing, trade_date, get_stock_name, morning=True
        )
        for code_6, row in fetched.items():
            result[code_6] = row

    return result


def load_tick_data_for_date(
    stock_code_6: str,
    trade_date: date,
) -> Optional[Any]:
    """
    加载单只股票在 trade_date 当日的 tick 数据（含集合竞价 9:15-9:25、连续竞价 9:30-11:30、13:00-15:00）。
    先读 data/ticks/{YYYYMMDD}/{code}.pkl；无则 QMT；仅完整 tick 才写入 pkl。
    """
    load_fn = _tick_cache_attr("load_tick_data")
    if callable(load_fn):
        try:
            return load_fn(stock_code_6, trade_date, use_memory_cache=True)
        except Exception:
            return None
    return None


def get_prices_at_time(
    stock_codes_6: List[str],
    trade_date: date,
    time_str: str,
    ticks_by_stock: Optional[Dict[str, Any]] = None,
) -> Dict[str, float]:
    """
    获取在 trade_date 当日、指定时刻（含）之前最后一笔 tick 的价格，用于「策略生成时间」晚于 9:30 时的最新价。
    time_str 格式 "HH:mm" 或 "HH:mm:ss"，如 "09:45"。
    ticks_by_stock: 可选，已加载的 tick 字典；传入可避免重复读 pkl/QMT。
    返回 { "000001": float, ... }，取不到的标的不在 dict 中。
    """
    from datetime import time as dt_time
    parts = (time_str or "").strip().split(":")
    h = int(parts[0]) if len(parts) > 0 else 23
    m = int(parts[1]) if len(parts) > 1 else 59
    sec = int(parts[2]) if len(parts) > 2 else 59
    target = dt_time(h, m, sec)
    result: Dict[str, float] = {}

    codes: List[str] = []
    for c in stock_codes_6 or []:
        c6 = _norm_code6(c)
        if c6 and c6 not in codes:
            codes.append(c6)
    if not codes:
        return result

    tick_map = ticks_by_stock
    if tick_map is None:
        tick_map = load_ticks_for_codes(codes, trade_date)
    elif not isinstance(tick_map, dict):
        tick_map = load_ticks_for_codes(codes, trade_date)

    for code_6 in codes:
        df = tick_map.get(code_6) if isinstance(tick_map, dict) else None
        if df is None or len(df) == 0 or "datetime" not in df.columns:
            continue
        price_col = "lastPrice" if "lastPrice" in df.columns else "last_price"
        if price_col not in df.columns:
            continue
        dt = df["datetime"]
        if hasattr(dt.dt, "tz") and dt.dt.tz is not None:
            dt = dt.dt.tz_convert("Asia/Shanghai")
        for i in range(len(df) - 1, -1, -1):
            t = df["datetime"].iloc[i]
            tick_t = t.time() if hasattr(t, "time") else getattr(t, "time", lambda: None)()
            if tick_t is not None and tick_t <= target:
                try:
                    result[code_6] = float(df[price_col].iloc[i] or 0)
                except (ValueError, TypeError):
                    pass
                break
    return result


def get_today_high_low_at_time(
    stock_codes_6: List[str],
    trade_date: date,
    time_str: str,
    ticks_by_stock: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, float]]:
    """
    获取“截至指定时刻（含）”的今日最高/今日最低，用于策略生成时点的 prices。

    回测 tick 数据没有显式 high/low 字段时，使用 lastPrice 的最大/最小值近似。
    若指定时刻前没有 tick，则该股票不返回（与实盘一致；调用方不得用今开盘填今日高低/现价）。
    ticks_by_stock: 可选，已加载的 tick 字典；传入可避免重复读 pkl/QMT。
    """
    from datetime import time as dt_time

    parts = (time_str or "").strip().split(":")
    h = int(parts[0]) if len(parts) > 0 else 23
    m = int(parts[1]) if len(parts) > 1 else 59
    sec = int(parts[2]) if len(parts) > 2 else 59
    target = dt_time(h, m, sec)

    result: Dict[str, Dict[str, float]] = {}
    codes: List[str] = []
    for c in stock_codes_6 or []:
        c6 = _norm_code6(c)
        if c6 and c6 not in codes:
            codes.append(c6)
    if not codes:
        return result

    tick_map = ticks_by_stock
    if tick_map is None:
        tick_map = load_ticks_for_codes(codes, trade_date)
    elif not isinstance(tick_map, dict):
        tick_map = load_ticks_for_codes(codes, trade_date)

    for code_6 in codes:
        df = tick_map.get(code_6) if isinstance(tick_map, dict) else None
        if df is None or len(df) == 0 or "datetime" not in df.columns:
            continue

        if "lastPrice" in df.columns:
            price_col = "lastPrice"
        elif "last_price" in df.columns:
            price_col = "last_price"
        else:
            continue

        high_col = None
        low_col = None
        for c in ("highPrice", "high_price", "high"):
            if c in df.columns:
                high_col = c
                break
        for c in ("lowPrice", "low_price", "low"):
            if c in df.columns:
                low_col = c
                break

        last_idx = None
        for i in range(len(df) - 1, -1, -1):
            t = df["datetime"].iloc[i]
            tick_t = t.time() if hasattr(t, "time") else getattr(t, "time", lambda: None)()
            if tick_t is not None and tick_t <= target:
                last_idx = i
                break

        if last_idx is None:
            continue

        sub = df.iloc[: last_idx + 1]
        try:
            if high_col:
                high_val = float(sub[high_col].max())
            else:
                high_val = float(sub[price_col].max())
            if low_col:
                low_val = float(sub[low_col].min())
            else:
                low_val = float(sub[price_col].min())
        except (TypeError, ValueError):
            continue

        if high_val <= 0 or low_val <= 0:
            continue

        result[code_6] = {
            "今日最高": round(high_val, 2),
            "今日最低": round(low_val, 2),
        }

    return result


def load_ticks_for_codes(
    stock_codes_6: List[str],
    trade_date: date,
) -> Dict[str, Any]:
    """
    批量加载多只股票在 trade_date 的 tick 数据。
    先读本地 pkl，无则 QMT；仅完整 tick 才写入 pkl。
    """
    global _TICK_CACHE_IMPORT_ERROR
    try:
        _load_batch = _import_tick_data_cache()
        return _load_batch(stock_codes_6, trade_date, use_memory_cache=True)
    except ModuleNotFoundError:
        if _TICK_CACHE_IMPORT_ERROR and not getattr(load_ticks_for_codes, "_warned", False):
            print(f"[tick_cache] load_ticks_for_codes 失败: {_TICK_CACHE_IMPORT_ERROR}")
            load_ticks_for_codes._warned = True  # type: ignore[attr-defined]
        return {}
    except Exception as exc:
        if not getattr(load_ticks_for_codes, "_other_warned", False):
            import traceback

            print(f"[tick_cache] load_ticks_for_codes 失败: {exc}")
            traceback.print_exc()
            load_ticks_for_codes._other_warned = True  # type: ignore[attr-defined]
        return {}


def clear_tick_memory_cache(trade_date: Optional[date] = None) -> int:
    """
    释放 tick 进程内内存缓存（按日或全部）。
    回测应在每个交易日撮合结束后调用，避免跨日囤积。
    """
    fn = _tick_cache_attr("clear_tick_memory_cache")
    if not callable(fn):
        return 0
    try:
        return int(fn(trade_date) or 0)
    except Exception:
        return 0
