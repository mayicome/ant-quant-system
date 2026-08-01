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
    if through_date is not None:
        df = df[df["date"] <= through_date]
    if df.empty:
        return None

    df = df.sort_values("date")
    # amount 为可选列（旧缓存可能没有）；有则保留数值
    if "amount" in df.columns:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    dt = pd.to_datetime(df["date"])
    df["time"] = (dt.astype("int64") // 10**6)
    return df.reset_index(drop=True)


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


def load_daily_dataframe(
    stock_code: str,
    *,
    through_date: Optional[date] = None,
    allow_xtdata_fallback: bool = True,
    allow_on_demand: bool = True,
) -> Optional[pd.DataFrame]:
    """优先 daily_cache；builtin 下缺数据可请求大 QMT 同步落盘；mini 可选 xtdata 回退。"""
    df = load_daily_from_cache(stock_code, through_date=through_date)
    if df is not None:
        return df
    if allow_on_demand:
        try:
            from utils.data_sync_request import ensure_daily_dataframe, use_on_demand_qmt_sync
        except ImportError:
            use_on_demand_qmt_sync = lambda: False  # type: ignore[assignment, misc]
            ensure_daily_dataframe = None  # type: ignore[assignment]
        if use_on_demand_qmt_sync() and callable(ensure_daily_dataframe):
            return ensure_daily_dataframe(stock_code, through_date=through_date)
    if not allow_xtdata_fallback:
        return None
    logger.info("[%s] daily_cache 无数据，回退 xtdata", stock_code)
    return load_daily_xtdata_fallback(stock_code, through_date=through_date)
