# -*- coding: utf-8 -*-
"""读取大 QMT 内置同步的 data/daily_cache/ 日线 CSV。"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(_PROJECT_ROOT, "data", "daily_cache")
MANIFEST_PATH = os.path.join(CACHE_DIR, "manifest.json")
MIN_VALID_CLOSE = 0.0

# 进程内日线 DF 缓存：(code, mtime) -> 全量已清洗 DataFrame；through_date 再切片
_DAILY_DF_CACHE: Dict[tuple, pd.DataFrame] = {}
_DAILY_DF_CACHE_MAX = 512


def get_cache_dir() -> str:
    return CACHE_DIR


def load_manifest() -> Dict[str, Any]:
    if not os.path.isfile(MANIFEST_PATH):
        return {}
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_sync_trade_date() -> Optional[date]:
    raw = str(load_manifest().get("sync_trade_date") or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def to_full_stock_code(stock_code: str) -> str:
    code = str(stock_code or "").strip().upper()
    if "." in code:
        return code
    # 6/5 开头：沪市 A 股 / 沪市 ETF·基金；8/4/920：北交所；其余默认深市
    if code.startswith(("5", "6")):
        return f"{code}.SH"
    if code.startswith(("8", "4", "920")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def csv_path_for_code(stock_code: str) -> str:
    return os.path.join(get_cache_dir(), to_full_stock_code(stock_code) + ".csv")


def cache_file_exists(stock_code: str) -> bool:
    return os.path.isfile(csv_path_for_code(stock_code))


def _valid_close_mask(df: pd.DataFrame) -> pd.Series:
    close = pd.to_numeric(df.get("close"), errors="coerce").fillna(0)
    vol = pd.to_numeric(df.get("volume"), errors="coerce").fillna(0)
    o = pd.to_numeric(df.get("open"), errors="coerce").fillna(0)
    h = pd.to_numeric(df.get("high"), errors="coerce").fillna(0)
    l = pd.to_numeric(df.get("low"), errors="coerce").fillna(0)
    ok_close = close > MIN_VALID_CLOSE
    not_zero_fill = ~((vol <= 0) & (o == h) & (h == l) & (l == close))
    return ok_close & not_zero_fill


def load_daily_from_cache(
    stock_code: str,
    *,
    through_date: Optional[date] = None,
) -> Optional[pd.DataFrame]:
    """从 daily_cache 读取日线，返回含 date、time 列的 DataFrame（time 为毫秒时间戳）。"""
    path = csv_path_for_code(stock_code)
    if not os.path.isfile(path):
        return None
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        mtime = None
    cache_key = (str(stock_code), mtime)
    full_df = _DAILY_DF_CACHE.get(cache_key)
    if full_df is None:
        try:
            raw = pd.read_csv(path, encoding="utf-8")
        except Exception as e:
            logger.warning("[%s] 读取 daily_cache 失败: %s", stock_code, e)
            return None
        if raw is None or raw.empty:
            return None

        need_cols = ("date", "open", "high", "low", "close", "volume")
        for col in need_cols:
            if col not in raw.columns:
                logger.warning("[%s] daily_cache 缺少列 %s", stock_code, col)
                return None

        df = raw.loc[_valid_close_mask(raw)].copy()
        if df.empty:
            return None

        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
        df = df.dropna(subset=["date"])
        if df.empty:
            return None

        df = df.sort_values("date")
        # 回补/并发写入可能产生同日多行，读时保留最后一条
        df = df.drop_duplicates(subset=["date"], keep="last")
        # amount 为可选列（旧缓存可能没有）；有则保留数值
        if "amount" in df.columns:
            df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
        dt = pd.to_datetime(df["date"])
        df["time"] = (dt.astype("int64") // 10**6)
        full_df = df.reset_index(drop=True)
        _DAILY_DF_CACHE[cache_key] = full_df
        while len(_DAILY_DF_CACHE) > _DAILY_DF_CACHE_MAX:
            try:
                _DAILY_DF_CACHE.pop(next(iter(_DAILY_DF_CACHE)))
            except Exception:
                break
    if through_date is not None:
        out = full_df[full_df["date"] <= through_date]
        if out.empty:
            return None
        return out.reset_index(drop=True)
    return full_df


def load_daily_xtdata_fallback(
    stock_code: str,
    *,
    through_date: Optional[date] = None,
    history_days: int = 1095,
) -> Optional[pd.DataFrame]:
    """xtdata 回退：本地 cache 缺失或偏短时补拉。"""
    try:
        import xtquant.xtdata as xtdata
    except Exception as e:
        logger.warning("[%s] xtdata 不可用: %s", stock_code, e)
        return None

    try:
        xtdata.enable_hello = False
    except Exception:
        pass

    full_code = to_full_stock_code(stock_code)
    end_date = through_date or date.today()
    start_date = end_date - timedelta(days=history_days)
    # 日线接口通常要 YYYYMMDD；部分环境也接受带时分秒，两种都试
    start_day = start_date.strftime("%Y%m%d")
    end_day = end_date.strftime("%Y%m%d")
    start_candidates = (start_day, start_day + "000000")
    end_candidates = (end_day, end_day + "235959")

    downloaded = False
    for s, e in zip(start_candidates, end_candidates):
        try:
            xtdata.download_history_data(full_code, "1d", s, e)
            downloaded = True
            break
        except Exception as ex:
            logger.warning("[%s] download_history_data 失败(%s~%s): %s", stock_code, s, e, ex)

    raw = None
    last_err: Optional[Exception] = None
    for s, e in zip(start_candidates, end_candidates):
        try:
            raw = xtdata.get_market_data_ex(
                [],
                [full_code],
                period="1d",
                start_time=s,
                end_time=e,
                count=-1,
            )
            if isinstance(raw, dict) and full_code in raw and len(raw[full_code]) > 0:
                break
            raw = None
        except Exception as ex:
            last_err = ex
            raw = None
    if raw is None:
        if last_err is not None:
            logger.error("[%s] get_market_data_ex 失败: %s", stock_code, last_err)
        return None

    if not isinstance(raw, dict) or full_code not in raw or len(raw[full_code]) == 0:
        return None

    stock_data = raw[full_code]
    daily_data = pd.DataFrame(
        {
            "time": stock_data["time"],
            "open": stock_data["open"],
            "high": stock_data["high"],
            "low": stock_data["low"],
            "close": stock_data["close"],
            "volume": stock_data["volume"],
        }
    )
    if "amount" in stock_data.columns:
        daily_data["amount"] = stock_data["amount"]
    daily_data = daily_data.loc[_valid_close_mask(daily_data)].copy()
    if daily_data.empty:
        return None

    daily_data.set_index("time", inplace=True)
    time_index = pd.to_datetime(daily_data.index, unit="ms")
    if time_index.tz is None:
        time_index = time_index.tz_localize("UTC").tz_convert("Asia/Shanghai")
    else:
        time_index = time_index.tz_convert("Asia/Shanghai")
    daily_data["date"] = pd.Series(time_index, index=daily_data.index).dt.date
    if through_date is not None:
        daily_data = daily_data[daily_data["date"] <= through_date]
    if daily_data.empty:
        return None
    return daily_data.sort_values("date").reset_index(drop=True)


def merge_and_save_daily_cache(
    stock_code: str,
    extra_df: pd.DataFrame,
    *,
    keep_bars: int = 300,
) -> bool:
    """把补拉到的日线合并进 data/daily_cache（拉长本地历史，供下次直接用）。"""
    if extra_df is None or getattr(extra_df, "empty", True):
        return False
    try:
        path = csv_path_for_code(stock_code)
        old = None
        if os.path.isfile(path):
            try:
                old = pd.read_csv(path, encoding="utf-8")
            except Exception:
                old = None
        parts = []
        if old is not None and not old.empty:
            parts.append(old)
        parts.append(extra_df.reset_index(drop=True))
        merged = pd.concat(parts, ignore_index=True)
        if "date" not in merged.columns:
            return False
        merged["date"] = pd.to_datetime(merged["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        merged = merged.dropna(subset=["date"])
        for col in ("open", "high", "low", "close", "volume"):
            if col in merged.columns:
                merged[col] = pd.to_numeric(merged[col], errors="coerce")
        if "amount" in merged.columns:
            merged["amount"] = pd.to_numeric(merged["amount"], errors="coerce")
        merged = merged.drop_duplicates(subset=["date"], keep="last")
        merged = merged.sort_values("date")
        if keep_bars > 0 and len(merged) > keep_bars:
            merged = merged.iloc[-keep_bars:]
        cols = [c for c in ("date", "open", "high", "low", "close", "volume", "amount") if c in merged.columns]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        merged.loc[:, cols].to_csv(path, index=False, encoding="utf-8")
        return True
    except Exception as e:
        logger.warning("[%s] 合并写入 daily_cache 失败: %s", stock_code, e)
        return False


def _code6(stock_code: str) -> str:
    return "".join(ch for ch in str(stock_code or "") if ch.isdigit())[:6].zfill(6)


def _daily_bar_from_tick_parquet(code_6: str, day: date) -> Optional[Dict[str, Any]]:
    """用本地 data/ticks/{YYYYMMDD}/{code}.parquet 合成一根日 K。"""
    try:
        from utils.tick_data_cache import tick_cache_path, read_tick_cache
    except Exception:
        return None
    c6 = _code6(code_6)
    if not c6 or c6 == "000000":
        return None
    path = ""
    try:
        path = tick_cache_path(c6, day)
    except Exception:
        path = ""
    if not path or not os.path.isfile(path):
        return None
    try:
        raw = read_tick_cache(c6, day)
    except Exception as e:
        logger.debug("[%s] 读 tick 合成日线失败 %s: %s", c6, day, e)
        return None
    if raw is None:
        return None
    try:
        if isinstance(raw, pd.DataFrame):
            df = raw
        else:
            df = pd.DataFrame(raw)
    except Exception:
        return None
    if df is None or df.empty:
        return None

    def _series(names):
        for n in names:
            if n in df.columns:
                s = pd.to_numeric(df[n], errors="coerce")
                s = s[s > 0]
                if not s.empty:
                    return s
        return None

    px = _series(("lastPrice", "last_price", "price", "close"))
    if px is None or px.empty:
        return None
    opens = _series(("open", "openPrice", "todayOpen"))
    highs = _series(("high", "highPrice", "todayHigh"))
    lows = _series(("low", "lowPrice", "todayLow"))
    vols = _series(("volume", "vol", "lastVol"))
    amts = _series(("amount",))
    bar = {
        "date": day.strftime("%Y-%m-%d"),
        "open": float(opens.iloc[0]) if opens is not None else float(px.iloc[0]),
        "high": float(highs.max()) if highs is not None else float(px.max()),
        "low": float(lows.min()) if lows is not None else float(px.min()),
        "close": float(px.iloc[-1]),
        "volume": float(vols.iloc[-1]) if vols is not None else 0.0,
        "amount": float(amts.iloc[-1]) if amts is not None else 0.0,
    }
    if bar["close"] <= 0:
        return None
    return bar


def backfill_daily_gaps_from_local_ticks(
    stock_code: str,
    *,
    through_date: Optional[date] = None,
    max_days: int = 10,
) -> bool:
    """daily_cache 落后时，用已落盘的 tick 补缺交易日（不依赖盘中 QMT 日线同步）。"""
    full = to_full_stock_code(stock_code)
    c6 = _code6(full)
    if not c6:
        return False
    end = through_date or date.today()
    try:
        from utils.data_sync_request import _expected_cache_last_date
        from utils.trading_day import is_tradeday

        expected = _expected_cache_last_date(end)
    except Exception:
        expected = end
        is_tradeday = lambda d: d.weekday() < 5  # type: ignore

    last = None
    path = csv_path_for_code(full)
    if os.path.isfile(path):
        try:
            with open(path, "rb") as f:
                size = os.path.getsize(path)
                f.seek(max(0, size - 4096))
                chunk = f.read().decode("utf-8", errors="ignore")
            for ln in reversed([x.strip() for x in chunk.splitlines() if x.strip()]):
                if ln.lower().startswith("date") or "," not in ln:
                    continue
                raw = ln.split(",", 1)[0].strip().strip('"')[:10]
                try:
                    last = datetime.strptime(raw, "%Y-%m-%d").date()
                    break
                except ValueError:
                    continue
        except Exception:
            last = None

    if last is not None and last >= expected:
        return False

    start = (last + timedelta(days=1)) if last is not None else (expected - timedelta(days=max_days))
    bars: list = []
    d = start
    scanned = 0
    while d <= expected and scanned < max_days * 2:
        scanned += 1
        if is_tradeday(d):
            bar = _daily_bar_from_tick_parquet(c6, d)
            if bar:
                bars.append(bar)
        d += timedelta(days=1)
    if not bars:
        return False
    extra = pd.DataFrame(bars)
    ok = merge_and_save_daily_cache(full, extra)
    if ok:
        # 清进程内 DF 缓存，避免继续读旧末日
        dead = [k for k in list(_DAILY_DF_CACHE.keys()) if str(k[0]).upper() == full.upper()]
        for k in dead:
            _DAILY_DF_CACHE.pop(k, None)
        logger.info("[%s] 已从本地 tick 补日线 %d 根（至 %s）", full, len(bars), expected)
    return ok


def load_daily_dataframe(
    stock_code: str,
    *,
    through_date: Optional[date] = None,
    allow_xtdata_fallback: bool = True,
    allow_on_demand: bool = True,
    on_demand_timeout_sec: Optional[float] = None,
) -> Optional[pd.DataFrame]:
    """优先 daily_cache；偏旧/缺失时可先用本地 tick 补洞，再请求大 QMT 同步；mini 可选 xtdata 回退。

    注意：仅有 CSV 不够。若末日早于期望交易日（如全市场停在上上周），必须补齐，
    否则图表/昨收会把过期收盘价当成昨收（例：603137 显示 28.98）。
    """
    df = load_daily_from_cache(stock_code, through_date=through_date)
    cache_fresh = df is not None and not getattr(df, "empty", True)
    if cache_fresh and through_date is not None:
        try:
            from utils.data_sync_request import _daily_cache_ready

            full = to_full_stock_code(stock_code)
            if full and not _daily_cache_ready(full, through_date):
                cache_fresh = False
        except Exception:
            pass

    if cache_fresh:
        return df

    # 本地 tick 已有缺日时，先补日线，避免盘中干等 QMT 日线队列
    try:
        if backfill_daily_gaps_from_local_ticks(stock_code, through_date=through_date):
            df2 = load_daily_from_cache(stock_code, through_date=through_date)
            if df2 is not None and not getattr(df2, "empty", True):
                try:
                    from utils.data_sync_request import _daily_cache_ready

                    full = to_full_stock_code(stock_code)
                    if full and _daily_cache_ready(full, through_date or date.today()):
                        return df2
                    df = df2
                except Exception:
                    return df2
    except Exception as e:
        logger.debug("[%s] tick 补日线异常: %s", stock_code, e)

    if allow_on_demand:
        try:
            from utils.data_sync_request import ensure_daily_dataframe, use_on_demand_qmt_sync
        except ImportError:
            use_on_demand_qmt_sync = lambda: False  # type: ignore[assignment, misc]
            ensure_daily_dataframe = None  # type: ignore[assignment]
        if use_on_demand_qmt_sync() and callable(ensure_daily_dataframe):
            kw = {}
            if on_demand_timeout_sec is not None:
                kw["timeout_sec"] = float(on_demand_timeout_sec)
            synced = ensure_daily_dataframe(stock_code, through_date=through_date, **kw)
            if synced is not None and not getattr(synced, "empty", True):
                return synced
            if df is not None and not getattr(df, "empty", True):
                logger.warning(
                    "[%s] daily_cache 偏旧且同步未就绪，暂用本地末日数据",
                    stock_code,
                )
                return df
            return None

    if df is not None and not getattr(df, "empty", True):
        return df
    if not allow_xtdata_fallback:
        return None
    logger.info("[%s] daily_cache 无数据，回退 xtdata", stock_code)
    return load_daily_xtdata_fallback(stock_code, through_date=through_date)
