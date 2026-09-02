# -*- coding: utf-8 -*-
"""
回测/选股缺本地缓存时，向大 QMT 内置策略提交拉取请求（日线 CSV / 日 tick parquet）。
外部进程只读写 data/data_sync_requests.json 与落盘文件，不直连 xtdata。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUESTS_PATH = os.path.join(_PROJECT_ROOT, "data", "data_sync_requests.json")
DAILY_FULL_DIR = os.path.join(_PROJECT_ROOT, "data", "daily_full")
# 岳教授新建票：上市以来全量日线（与 rolling daily_cache 的 2025-01-01 窗口分离）
FULL_HISTORY_MODE = "full_history"
FULL_HISTORY_FROM_DATE = date(1991, 1, 2)
DEFAULT_FULL_DAILY_TIMEOUT_SEC = 1800.0
DEFAULT_POLL_SEC = 0.5
DEFAULT_DAILY_TIMEOUT_SEC = 180
DEFAULT_TICK_TIMEOUT_SEC = 240
BACKTEST_TICK_TIMEOUT_SEC = 90.0
TICK_POOL_STALL_SEC = 30.0
TICK_POOL_TIMEOUT_MIN_SEC = 45.0
TICK_POOL_TIMEOUT_MAX_SEC = 300.0
TICK_POOL_SEC_PER_CODE = 3.0
DEFAULT_POOL_DAILY_TIMEOUT_SEC = 120.0
POOL_DAILY_SEC_PER_CODE = 1.5
POOL_DAILY_TIMEOUT_MIN_SEC = 120.0
POOL_DAILY_TIMEOUT_MAX_SEC = 7200.0
POOL_DAILY_SMALL_POOL_MAX = 5
POOL_DAILY_SMALL_POOL_MIN_SEC = 30.0
POOL_DAILY_STALL_SEC = 45.0
# 大池等待：无进展容忍随缺少数放宽（盘中约 1 只/秒，避免误杀）
POOL_DAILY_STALL_SEC_PER_MISSING = 0.9
POOL_DAILY_STALL_MAX_SEC = 300.0
MAX_RETRIES = 3
MIN_DAILY_CACHE_READY = 1
MIN_DAILY_BARS_MA120 = 120
POOL_MAX_DATE_LAG_DAYS = 10
# 回测引擎内逐只兜底：预热后不应再长等
BACKTEST_ENSURE_DAILY_TIMEOUT_SEC = 8.0

try:
    from utils.daily_cache_reader import (
        csv_path_for_code,
        load_daily_from_cache,
        to_full_stock_code,
    )
except ImportError:
    from daily_cache_reader import (  # type: ignore[no-redef]
        csv_path_for_code,
        load_daily_from_cache,
        to_full_stock_code,
    )

try:
    from utils.qmt_execution_config import get_qmt_mode
except ImportError:
    def get_qmt_mode(default: str = "mini") -> str:  # type: ignore[misc]
        return default


def use_on_demand_qmt_sync() -> bool:
    """builtin/standalone：缺缓存时走 QMT 按需同步，不用外部 xtdata。"""
    return get_qmt_mode() in ("builtin", "standalone")


_SOFT_MISS_REASONS = frozenset(
    {"empty_history", "local_miss", "invalid_0", "no_ctx"}
)


def _clear_soft_miss_cache(codes: Iterable[str]) -> int:
    """提交日线请求时清掉 tick 侧软 miss，避免 empty_history 挡日线同步。"""
    miss_path = os.path.join(_PROJECT_ROOT, "data", "daily_cache", "sync_miss_codes.json")
    if not os.path.isfile(miss_path):
        return 0
    try:
        with open(miss_path, "r", encoding="utf-8") as f:
            payload = json.load(f) or {}
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    bucket = payload.get("codes")
    if not isinstance(bucket, dict):
        return 0
    cleared = 0
    for raw in codes or []:
        full = to_full_stock_code(str(raw or ""))
        if not full or full not in bucket:
            continue
        meta = bucket.get(full)
        reason = str((meta or {}).get("reason") or "") if isinstance(meta, dict) else ""
        if reason in _SOFT_MISS_REASONS or not reason:
            bucket.pop(full, None)
            cleared += 1
    if cleared:
        try:
            _atomic_write_json(miss_path, payload)
        except Exception:
            return 0
    return cleared


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _parse_date(raw: Any) -> Optional[date]:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    """原子写 JSON。用唯一临时文件，避免多进程共用 path.tmp 互相踩掉（WinError 2）。"""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp", prefix=".dsr_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        last_err: Optional[BaseException] = None
        for attempt in range(12):
            try:
                os.replace(tmp, path)
                tmp = ""
                return
            except OSError as e:
                last_err = e
                # Windows 偶发占用：后半程改 copy 兜底
                if attempt >= 7 and os.path.isfile(tmp):
                    try:
                        shutil.copy2(tmp, path)
                        tmp = ""
                        return
                    except OSError:
                        pass
                time.sleep(0.05 * (attempt + 1))
        if last_err is not None:
            raise last_err
        raise OSError("atomic write failed: %s" % path)
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _empty_requests() -> Dict[str, Any]:
    return {"version": 1, "daily": {}, "tick": {}}


def load_requests() -> Dict[str, Any]:
    if not os.path.isfile(REQUESTS_PATH):
        return _empty_requests()
    try:
        with open(REQUESTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _empty_requests()
        data.setdefault("version", 1)
        data.setdefault("daily", {})
        data.setdefault("tick", {})
        return data
    except Exception:
        return _empty_requests()


def save_requests(data: Dict[str, Any]) -> None:
    _atomic_write_json(REQUESTS_PATH, data)


_DAILY_FAILED_PRUNE_KEY = "daily_failed_pruned_on"


def _is_daily_failed_trace(meta: Dict[str, Any]) -> bool:
    st = str(meta.get("status") or "")
    retries = int(meta.get("retries") or 0)
    return st == "failed" or (st == "pending" and retries >= MAX_RETRIES)


def _daily_meta_stamp(meta: Dict[str, Any]) -> Optional[date]:
    for key in ("updated_at", "last_attempt_at", "requested_at", "through_date"):
        d = _parse_date(meta.get(key))
        if d is not None:
            return d
    return None


def prune_stale_daily_failures(*, today: Optional[date] = None) -> int:
    """跨自然日清除按需日线失败痕迹，使监控「失败」数每日归零。

    仅删除 stamp < today 的 failed / 重试耗尽条目；当日失败保留。
    每天最多写盘一次（由 daily_failed_pruned_on 标记）。
    """
    today = today or date.today()
    today_s = today.isoformat()
    data = load_requests()
    if str(data.get(_DAILY_FAILED_PRUNE_KEY) or "") == today_s:
        return 0
    daily = data.get("daily")
    if not isinstance(daily, dict):
        daily = {}
        data["daily"] = daily
    removed = 0
    for code in list(daily.keys()):
        meta = daily.get(code)
        if not isinstance(meta, dict):
            continue
        if not _is_daily_failed_trace(meta):
            continue
        stamp = _daily_meta_stamp(meta)
        if stamp is None or stamp < today:
            daily.pop(code, None)
            removed += 1
    data[_DAILY_FAILED_PRUNE_KEY] = today_s
    try:
        save_requests(data)
    except Exception:
        logger.exception("prune_stale_daily_failures 写盘失败")
        return 0
    if removed:
        logger.info("已清除跨日按需日线失败痕迹 %d 条", removed)
    return removed


def list_failed_tick_requests(*, limit: int = 200) -> List[Dict[str, str]]:
    """列出按需分时 failed 记录（供监控详情）。"""
    data = load_requests()
    tick_root = data.get("tick") or {}
    out: List[Dict[str, str]] = []
    if not isinstance(tick_root, dict):
        return out
    for c6, days in tick_root.items():
        if not isinstance(days, dict):
            continue
        for ymd, meta in days.items():
            if not isinstance(meta, dict):
                continue
            st = str(meta.get("status") or "")
            retries = int(meta.get("retries") or 0)
            if st != "failed" and not (st == "pending" and retries >= MAX_RETRIES):
                continue
            out.append(
                {
                    "code": str(c6).zfill(6)[:6],
                    "day": str(ymd)[:8],
                    "error": str(meta.get("last_error") or meta.get("status") or "failed")[:120],
                }
            )
    out.sort(key=lambda x: (x["day"], x["code"]), reverse=True)
    if limit and limit > 0:
        return out[: int(limit)]
    return out


def clear_failed_tick_requests() -> int:
    """清除全部按需分时 failed / 重试耗尽记录（监控告警用，不影响 pending）。"""
    data = load_requests()
    tick_root = data.get("tick")
    if not isinstance(tick_root, dict):
        return 0
    removed = 0
    for c6 in list(tick_root.keys()):
        days = tick_root.get(c6)
        if not isinstance(days, dict):
            continue
        for ymd in list(days.keys()):
            meta = days.get(ymd)
            if not isinstance(meta, dict):
                continue
            st = str(meta.get("status") or "")
            retries = int(meta.get("retries") or 0)
            if st == "failed" or (st == "pending" and retries >= MAX_RETRIES):
                days.pop(ymd, None)
                removed += 1
        if not days:
            tick_root.pop(c6, None)
    if removed:
        save_requests(data)
        logger.info("已清除按需分时失败记录 %d 条", removed)
    return removed


PoolProgressFn = Optional[Callable[[int, int, str], None]]


def count_pending_sync() -> Tuple[int, int]:
    """返回 (pending_daily 条数, pending_tick 条数)。"""
    prune_stale_daily_failures()
    data = load_requests()
    daily_n = 0
    daily = data.get("daily") or {}
    if isinstance(daily, dict):
        for meta in daily.values():
            if not isinstance(meta, dict):
                continue
            status = str(meta.get("status") or "")
            if status == "pending" and int(meta.get("retries") or 0) < MAX_RETRIES:
                daily_n += 1
    tick_n = 0
    tick_root = data.get("tick") or {}
    if isinstance(tick_root, dict):
        for days in tick_root.values():
            if not isinstance(days, dict):
                continue
            for meta in days.values():
                if not isinstance(meta, dict):
                    continue
                if str(meta.get("status") or "") == "pending":
                    if int(meta.get("retries") or 0) < MAX_RETRIES:
                        tick_n += 1
    return daily_n, tick_n


def _expected_cache_last_date(through_date: date) -> date:
    """盘中/盘前：期望 CSV 末日为上一交易日；收盘同步后可为 through_date。"""
    now = datetime.now()
    if through_date < now.date():
        return through_date
    # 15:35 前不指望「今日」K 已完整落盘
    if (now.hour, now.minute) < (15, 35):
        try:
            from utils.trading_day import previous_tradeday

            return previous_tradeday(through_date)
        except Exception:
            d = through_date - timedelta(days=1)
            while d.weekday() >= 5:
                d -= timedelta(days=1)
            return d
    return through_date


def _peek_daily_cache_last_date(code: str) -> Optional[date]:
    """只读 CSV 末尾估末日，避免 ready 轮询反复全量 read_csv。"""
    try:
        path = csv_path_for_code(code)
    except Exception:
        return None
    if not path or not os.path.isfile(path):
        return None
    try:
        size = os.path.getsize(path)
        if size < 8:
            return None
        with open(path, "rb") as f:
            f.seek(max(0, size - 4096))
            chunk = f.read().decode("utf-8", errors="ignore")
        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        for ln in reversed(lines):
            low = ln.lower()
            if low.startswith("date") or "," not in ln:
                continue
            raw = ln.split(",", 1)[0].strip().strip('"')[:10]
            try:
                return datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                continue
    except Exception:
        return None
    return None


def _daily_cache_ready(code: str, through_date: Optional[date]) -> bool:
    """有可用日线缓存即可（不要求满 120 根；MA120 由策略/计算器自行处理）。"""
    through_date = _clamp_through_date(through_date) if through_date is not None else None
    today = date.today()
    # 快路径：文件存在且末日够新 → 视为就绪（轮询用，避免反复读全表）
    if through_date is not None:
        last = _peek_daily_cache_last_date(code)
        if last is not None:
            # 未来日期 K 线视为损坏，强制重同步
            if last > today:
                return False
            expected = _expected_cache_last_date(through_date)
            if last >= expected and last >= through_date - timedelta(days=POOL_MAX_DATE_LAG_DAYS):
                return True
            # 末日明显偏旧 → 未就绪；无需再全量读
            if last < expected:
                return False
    df = load_daily_from_cache(code, through_date=through_date)
    if df is None or getattr(df, "empty", True):
        return False
    if len(df) < MIN_DAILY_CACHE_READY:
        return False
    if through_date is not None and "date" in df.columns:
        try:
            last = df["date"].max()
            if hasattr(last, "date") and callable(last.date):
                last = last.date()
            if last > today:
                return False
            expected = _expected_cache_last_date(through_date)
            if last < expected:
                return False
            if last < through_date - timedelta(days=POOL_MAX_DATE_LAG_DAYS):
                return False
        except Exception:
            pass
    return True


def _tick_cache_ready(code_6: str, trade_date: date) -> bool:
    """预热/队列用：只看本地文件是否存在，避免整表读 parquet。"""
    try:
        from utils.tick_data_cache import tick_cache_file_ready
    except ImportError:
        from tick_data_cache import tick_cache_file_ready  # type: ignore[no-redef]
    return bool(tick_cache_file_ready(code_6, trade_date))


def _clamp_through_date(through_date: Optional[date]) -> date:
    """回测持有窗口常把 through 推到未来；日线落盘不得晚于今天。"""
    end_d = through_date or date.today()
    today = date.today()
    if end_d > today:
        return today
    return end_d


def full_daily_csv_path(code: str, adjust: str | None = None) -> str:
    from utils.daily_cache_reader import full_daily_csv_path as _p

    return _p(code, adjust=adjust)


def _full_daily_ready(code: str, through_date: Optional[date]) -> bool:
    """全量日线落盘就绪：末日够新，且起点接近上市/1991（拒绝近 2 年截断窗）。"""
    try:
        import pandas as pd
    except Exception:
        return False
    end_d = _clamp_through_date(through_date)
    path = full_daily_csv_path(code)
    if not os.path.isfile(path):
        return False
    try:
        df = pd.read_csv(path, encoding="utf-8")
    except Exception:
        return False
    if df is None or getattr(df, "empty", True) or "date" not in df.columns:
        return False
    try:
        dates = pd.to_datetime(df["date"], errors="coerce").dropna()
    except Exception:
        return False
    if dates.empty or len(dates) < 4:
        return False
    last = dates.max().date()
    first = dates.min().date()
    expected = _expected_cache_last_date(end_d)
    if last < expected and last < end_d - timedelta(days=POOL_MAX_DATE_LAG_DAYS):
        return False
    # 请求从 1991 起：若首根仍在 2020 年后且不足 ~15 年交易日，区分「次新真全量」与「近 2 年截断窗」
    if first > date(2020, 1, 1) and len(dates) < 2500:
        span_cal = (end_d - first).days
        span_td = max(1, int(span_cal * 250 / 365))
        # 刚上市可能只有几十根；满约半年仍用 50 下限防近窗截断
        min_bars = 15 if span_cal <= 180 else 50
        looks_like_ipo = len(dates) >= max(min_bars, int(span_td * 0.65))
        if span_cal > 400 * 3 and not looks_like_ipo:
            return False
        if (
            first > date(2023, 1, 1)
            and len(dates) < 900
            and not looks_like_ipo
        ):
            return False
    return True


def submit_daily_requests(
    codes: Iterable[str],
    *,
    through_date: Optional[date] = None,
) -> List[str]:
    """提交日线同步请求；返回本次新提交的完整代码列表。"""
    prune_stale_daily_failures()
    end_d = _clamp_through_date(through_date)
    end_s = end_d.isoformat()
    data = load_requests()
    daily: Dict[str, Any] = data.setdefault("daily", {})
    submitted: List[str] = []
    for raw in codes or []:
        full = to_full_stock_code(str(raw or ""))
        if not full:
            continue
        if _daily_cache_ready(full, end_d):
            continue
        prev = daily.get(full) if isinstance(daily.get(full), dict) else {}
        prev_end = _parse_date(prev.get("through_date"))
        prev_retries = int(prev.get("retries") or 0)
        prev_status = str(prev.get("status") or "")
        # 全量请求勿被普通 rolling 请求覆盖跳过
        if str(prev.get("mode") or "") == FULL_HISTORY_MODE and prev_status == "pending":
            continue
        if prev_status == "short_history":
            short_bars = 0
            try:
                short_bars = int(prev.get("short_vol_bars") or 0)
            except (TypeError, ValueError):
                short_bars = 0
            # short_vol_bars<=0：空拉取被误标，必须允许再次提交
            if short_bars > 0:
                la = str(prev.get("last_attempt_at") or prev.get("updated_at") or "")
                try:
                    last_d = datetime.strptime(la[:19], "%Y-%m-%dT%H:%M:%S").date()
                except ValueError:
                    last_d = None
                if last_d is not None and last_d >= end_d:
                    continue
        # 仅跳过「进行中且未耗尽重试」的同一 through 请求
        if (
            prev_status == "pending"
            and prev_retries < MAX_RETRIES
            and prev_end is not None
            and prev_end >= end_d
            and str(prev.get("mode") or "") != FULL_HISTORY_MODE
        ):
            continue
        daily[full] = {
            "through_date": end_s,
            "requested_at": _now_iso(),
            "status": "pending",
            "retries": 0,
        }
        submitted.append(full)
    if submitted:
        _clear_soft_miss_cache(submitted)
        save_requests(data)
        logger.info(
            "已提交日线同步请求 %d 只（through=%s）→ 大 QMT 将写入 data/daily_cache/",
            len(submitted),
            end_s,
        )
    return submitted


def submit_full_daily_requests(
    codes: Iterable[str],
    *,
    through_date: Optional[date] = None,
    from_date: Optional[date] = None,
    force: bool = False,
) -> List[str]:
    """提交「上市以来全量日线」请求 → 大 QMT 内置 download，写入 data/daily_full/。

    与 rolling daily_cache（约从 2025-01-01）分离；供岳教授新建票建档。
    """
    prune_stale_daily_failures()
    end_d = _clamp_through_date(through_date)
    start_d = from_date or FULL_HISTORY_FROM_DATE
    end_s = end_d.isoformat()
    start_s = start_d.isoformat()
    data = load_requests()
    daily: Dict[str, Any] = data.setdefault("daily", {})
    submitted: List[str] = []
    for raw in codes or []:
        full = to_full_stock_code(str(raw or ""))
        if not full:
            continue
        if not force and _full_daily_ready(full, end_d):
            continue
        prev = daily.get(full) if isinstance(daily.get(full), dict) else {}
        prev_status = str(prev.get("status") or "")
        prev_retries = int(prev.get("retries") or 0)
        if (
            not force
            and str(prev.get("mode") or "") == FULL_HISTORY_MODE
            and prev_status == "pending"
            and prev_retries < MAX_RETRIES
        ):
            # 刷新 through，保持排队
            meta = dict(prev)
            meta["through_date"] = end_s
            meta["from_date"] = start_s
            meta["requested_at"] = _now_iso()
            daily[full] = meta
            continue
        daily[full] = {
            "mode": FULL_HISTORY_MODE,
            "from_date": start_s,
            "through_date": end_s,
            "requested_at": _now_iso(),
            "status": "pending",
            "retries": 0,
        }
        if force:
            daily[full]["force_refresh"] = True
        submitted.append(full)
    if submitted:
        _clear_soft_miss_cache(submitted)
        save_requests(data)
        logger.info(
            "已提交全量日线请求 %d 只（from=%s through=%s）→ 大 QMT → data/daily_full/",
            len(submitted),
            start_s,
            end_s,
        )
    return submitted


def ensure_full_daily(
    code: str,
    *,
    through_date: Optional[date] = None,
    timeout_sec: float = DEFAULT_FULL_DAILY_TIMEOUT_SEC,
    poll_sec: float = 2.0,
) -> bool:
    """提交并等待全量日线落盘；成功返回 True。"""
    full = to_full_stock_code(code)
    if not full:
        return False
    end_d = _clamp_through_date(through_date)
    if _full_daily_ready(full, end_d):
        return True
    submit_full_daily_requests([full], through_date=end_d)
    deadline = time.time() + max(30.0, float(timeout_sec))
    while time.time() < deadline:
        if _full_daily_ready(full, end_d):
            return True
        # 失败耗尽则早退
        data = load_requests()
        meta = (data.get("daily") or {}).get(full) or {}
        if str(meta.get("status") or "") == "failed":
            logger.warning(
                "[%s] 全量日线请求失败: %s",
                full,
                meta.get("last_error") or "failed",
            )
            return False
        time.sleep(max(0.5, float(poll_sec)))
    logger.warning("[%s] 等待全量日线超时（%.0fs）", full, timeout_sec)
    return False


def load_full_daily(
    code: str,
    *,
    through_date: Optional[date] = None,
    adjust: str | None = None,
) -> Optional["pd.DataFrame"]:
    """读取 daily_full[/daily_full_qfq]/{code}.csv；不自动提交请求。"""
    try:
        import pandas as pd
    except Exception:
        return None
    full = to_full_stock_code(code)
    path = full_daily_csv_path(full, adjust=adjust)
    if not os.path.isfile(path):
        return None
    try:
        df = pd.read_csv(path, encoding="utf-8")
    except Exception as e:
        logger.warning("[%s] 读取 daily_full 失败: %s", full, e)
        return None
    if df is None or df.empty or "date" not in df.columns:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    if through_date is not None:
        end_d = _clamp_through_date(through_date)
        df = df[df["date"].dt.date <= end_d]
    if df.empty:
        return None
    return df.sort_values("date").reset_index(drop=True)


def submit_tick_requests(
    codes_6: Iterable[str],
    trade_date: date,
) -> List[str]:
    """提交单日 tick 同步请求。"""
    ymd = trade_date.strftime("%Y%m%d")
    data = load_requests()
    tick_root: Dict[str, Any] = data.setdefault("tick", {})
    submitted: List[str] = []
    for raw in codes_6 or []:
        c6 = "".join(ch for ch in str(raw or "") if ch.isdigit())[:6].zfill(6)
        if not c6 or c6 == "000000":
            continue
        if _tick_cache_ready(c6, trade_date):
            continue
        bucket = tick_root.setdefault(c6, {})
        if not isinstance(bucket, dict):
            bucket = {}
            tick_root[c6] = bucket
        prev = bucket.get(ymd) if isinstance(bucket.get(ymd), dict) else {}
        prev_status = str(prev.get("status") or "")
        if prev_status == "failed":
            continue
        if prev_status == "pending" and int(prev.get("retries") or 0) < MAX_RETRIES:
            continue
        bucket[ymd] = {
            "requested_at": _now_iso(),
            "status": "pending",
            "retries": int(prev.get("retries") or 0),
        }
        submitted.append(c6)
    if submitted:
        save_requests(data)
        logger.info(
            "已提交 tick 同步请求 %d 只（%s）→ 大 QMT 将写入 data/ticks/%s/",
            len(submitted),
            trade_date.isoformat(),
            ymd,
        )
    return submitted


def _pump_ui_events() -> None:
    try:
        from PyQt5.QtWidgets import QApplication

        QApplication.processEvents()
    except Exception:
        pass


def pool_daily_wait_timeout_sec(code_count: int) -> float:
    """按仍缺日线的数量估算等待超时（非股票池总规模）。"""
    n = max(0, int(code_count))
    if n <= 0:
        return 0.0
    if n <= POOL_DAILY_SMALL_POOL_MAX:
        return max(POOL_DAILY_SMALL_POOL_MIN_SEC, n * POOL_DAILY_SEC_PER_CODE * 2)
    return max(
        POOL_DAILY_TIMEOUT_MIN_SEC,
        min(POOL_DAILY_TIMEOUT_MAX_SEC, n * POOL_DAILY_SEC_PER_CODE),
    )


def _daily_unavailable_today(code: str, through_date: Optional[date]) -> bool:
    """今日短期内不会再补齐有效 daily_cache（真 short_history / 硬 failed）。"""
    end_d = through_date or date.today()
    data = load_requests()
    daily = data.get("daily") or {}
    if not isinstance(daily, dict):
        return False
    meta = daily.get(code) if isinstance(daily.get(code), dict) else {}
    status = str(meta.get("status") or "")
    last_err = str(meta.get("last_error") or "").strip().lower()
    if status == "failed":
        # miss_cache_empty_history 等软失败：允许再次提交后继续等
        if last_err.startswith("miss_cache_") and not last_err.startswith(
            "miss_cache_delisted"
        ):
            return False
        if last_err.startswith("pool_invalid_0_"):
            return False
        return True
    if status != "short_history":
        return False
    # 误标的 short_history（0 根）不算「今日不可用」
    try:
        short_bars = int(meta.get("short_vol_bars") or 0)
    except (TypeError, ValueError):
        short_bars = 0
    if short_bars <= 0:
        return False
    la = str(meta.get("last_attempt_at") or meta.get("updated_at") or "")
    if not la:
        return False
    try:
        last_d = datetime.strptime(la[:19], "%Y-%m-%dT%H:%M:%S").date()
    except ValueError:
        return False
    return last_d >= end_d


def _tick_request_meta(code_6: str, trade_date: date) -> Dict[str, Any]:
    c6 = "".join(ch for ch in str(code_6 or "") if ch.isdigit())[:6].zfill(6)
    ymd = trade_date.strftime("%Y%m%d")
    data = load_requests()
    tick_root = data.get("tick") or {}
    if not isinstance(tick_root, dict):
        return {}
    bucket = tick_root.get(c6) if isinstance(tick_root.get(c6), dict) else {}
    meta = bucket.get(ymd) if isinstance(bucket, dict) and isinstance(bucket.get(ymd), dict) else {}
    return dict(meta) if isinstance(meta, dict) else {}


def tick_sync_unavailable(code_6: str, trade_date: date) -> bool:
    """QMT 已放弃同步（如停牌 empty_tick、重试耗尽）→ 回测不必再等。"""
    if _tick_cache_ready(code_6, trade_date):
        return False
    meta = _tick_request_meta(code_6, trade_date)
    status = str(meta.get("status") or "")
    if status == "failed":
        return True
    if int(meta.get("retries") or 0) >= MAX_RETRIES:
        return True
    return False


def tick_pool_wait_timeout_sec(code_count: int) -> float:
    n = max(0, int(code_count))
    if n <= 0:
        return 0.0
    return min(
        TICK_POOL_TIMEOUT_MAX_SEC,
        max(TICK_POOL_TIMEOUT_MIN_SEC, n * TICK_POOL_SEC_PER_CODE),
    )


def wait_daily_cache(
    code: str,
    *,
    through_date: Optional[date] = None,
    timeout_sec: float = DEFAULT_DAILY_TIMEOUT_SEC,
    poll_sec: float = DEFAULT_POLL_SEC,
) -> bool:
    full = to_full_stock_code(code)
    if not full:
        return False
    through_date = _clamp_through_date(through_date)
    if _daily_cache_ready(full, through_date):
        return True
    if _daily_unavailable_today(full, through_date):
        return False
    submit_daily_requests([full], through_date=through_date)
    deadline = time.time() + max(1.0, float(timeout_sec))
    while time.time() < deadline:
        if _daily_cache_ready(full, through_date):
            return True
        if _daily_unavailable_today(full, through_date):
            return False
        _pump_ui_events()
        time.sleep(max(0.2, float(poll_sec)))
    return _daily_cache_ready(full, through_date)


def wait_daily_cache_pool(
    codes: Iterable[str],
    *,
    through_date: Optional[date] = None,
    timeout_sec: Optional[float] = None,
    poll_sec: float = DEFAULT_POLL_SEC,
    on_progress: PoolProgressFn = None,
) -> Tuple[List[str], List[str]]:
    """批量等待股票池日线落盘（共享超时，避免逐只 180s 卡死）。"""
    end_d = _clamp_through_date(through_date)
    fulls: List[str] = []
    seen: Set[str] = set()
    for raw in codes or []:
        full = to_full_stock_code(str(raw or ""))
        if full and full not in seen:
            seen.add(full)
            fulls.append(full)
    fulls.sort()
    if not fulls:
        return [], []

    need = [f for f in fulls if not _daily_cache_ready(f, end_d)]
    if not need:
        return fulls, []
    if need:
        submit_daily_requests(need, through_date=end_d)

    wait_sec = (
        pool_daily_wait_timeout_sec(len(need))
        if timeout_sec is None
        else min(float(timeout_sec), pool_daily_wait_timeout_sec(len(need)) + 60.0)
    )
    stall_limit = float(POOL_DAILY_STALL_SEC)
    if len(need) > 20:
        stall_limit = max(
            stall_limit,
            min(
                float(POOL_DAILY_STALL_MAX_SEC),
                float(POOL_DAILY_STALL_SEC)
                + len(need) * float(POOL_DAILY_STALL_SEC_PER_MISSING),
            ),
        )
    deadline = time.time() + max(1.0, float(wait_sec))
    ready_count = len(fulls) - len(need)
    stall_since = time.time()
    last_progress_ts = 0.0
    while time.time() < deadline:
        ready = [f for f in fulls if _daily_cache_ready(f, end_d)]
        if on_progress and time.time() - last_progress_ts >= 1.0:
            on_progress(len(ready), len(fulls), "日线")
            last_progress_ts = time.time()
        if len(ready) >= len(fulls):
            return ready, []
        missing_now = sorted(set(fulls) - set(ready))
        if missing_now and all(_daily_unavailable_today(f, end_d) for f in missing_now):
            return ready, missing_now
        if len(ready) > ready_count:
            ready_count = len(ready)
            stall_since = time.time()
        elif time.time() - stall_since >= stall_limit:
            return ready, missing_now
        _pump_ui_events()
        time.sleep(max(0.2, float(poll_sec)))

    ready_set = {f for f in fulls if _daily_cache_ready(f, end_d)}
    missing = sorted(set(fulls) - ready_set)
    return sorted(ready_set), missing


def wait_tick_cache_pool(
    codes_6: Iterable[str],
    trade_date: date,
    *,
    timeout_sec: Optional[float] = None,
    poll_sec: float = DEFAULT_POLL_SEC,
    on_progress: PoolProgressFn = None,
) -> Tuple[List[str], List[str]]:
    """单日多股批量等 tick；识别 failed/停牌，无进展时提前结束。"""
    c6_list: List[str] = []
    seen: Set[str] = set()
    for raw in codes_6 or []:
        c6 = "".join(ch for ch in str(raw or "") if ch.isdigit())[:6].zfill(6)
        if not c6 or c6 == "000000" or c6 in seen:
            continue
        seen.add(c6)
        c6_list.append(c6)
    c6_list.sort()
    if not c6_list:
        return [], []

    need = [
        c
        for c in c6_list
        if not _tick_cache_ready(c, trade_date) and not tick_sync_unavailable(c, trade_date)
    ]
    if not need:
        ready = [c for c in c6_list if _tick_cache_ready(c, trade_date)]
        skip = sorted(set(c6_list) - set(ready))
        return ready, skip

    submit_tick_requests(need, trade_date)
    wait_sec = (
        tick_pool_wait_timeout_sec(len(need))
        if timeout_sec is None
        else float(timeout_sec)
    )
    deadline = time.time() + max(1.0, wait_sec)
    ready_count = len(c6_list) - len(need)
    stall_since = time.time()
    last_progress_ts = 0.0
    day_label = trade_date.isoformat()
    while time.time() < deadline:
        ready = [c for c in c6_list if _tick_cache_ready(c, trade_date)]
        if on_progress and time.time() - last_progress_ts >= 1.0:
            on_progress(len(ready), len(c6_list), f"tick {day_label}")
            last_progress_ts = time.time()
        if len(ready) >= len(c6_list):
            return ready, []
        missing_now = [
            c
            for c in c6_list
            if c not in ready and not tick_sync_unavailable(c, trade_date)
        ]
        if not missing_now:
            return ready, sorted(set(c6_list) - set(ready))
        if missing_now and all(tick_sync_unavailable(c, trade_date) for c in missing_now):
            return ready, sorted(set(c6_list) - set(ready))
        if len(ready) > ready_count:
            ready_count = len(ready)
            stall_since = time.time()
        elif time.time() - stall_since >= TICK_POOL_STALL_SEC:
            return ready, sorted(set(c6_list) - set(ready))
        _pump_ui_events()
        time.sleep(max(0.2, float(poll_sec)))

    ready_set = {c for c in c6_list if _tick_cache_ready(c, trade_date)}
    missing = sorted(set(c6_list) - ready_set)
    return sorted(ready_set), missing


def wait_tick_cache(
    code_6: str,
    trade_date: date,
    *,
    timeout_sec: float = DEFAULT_TICK_TIMEOUT_SEC,
    poll_sec: float = DEFAULT_POLL_SEC,
) -> bool:
    c6 = "".join(ch for ch in str(code_6 or "") if ch.isdigit())[:6].zfill(6)
    if not c6 or c6 == "000000":
        return False
    if _tick_cache_ready(c6, trade_date):
        return True
    if tick_sync_unavailable(c6, trade_date):
        return False
    ready, _ = wait_tick_cache_pool(
        [c6],
        trade_date,
        timeout_sec=timeout_sec,
        poll_sec=poll_sec,
    )
    return c6 in ready


def ensure_daily_dataframe(
    stock_code: str,
    *,
    through_date: Optional[date] = None,
    timeout_sec: float = DEFAULT_DAILY_TIMEOUT_SEC,
):
    """builtin 回测：缺缓存则请求 QMT 同步后读 daily_cache。"""
    full = to_full_stock_code(stock_code)
    if not full:
        return None
    through_date = _clamp_through_date(through_date)
    if not _daily_cache_ready(full, through_date):
        ok = wait_daily_cache(
            full,
            through_date=through_date,
            timeout_sec=timeout_sec,
        )
        if not ok:
            logger.warning(
                "[%s] 日线同步超时（through=%s）；请确认大 QMT 模型交易已启动",
                full,
                through_date.isoformat(),
            )
            return None
    return load_daily_from_cache(full, through_date=through_date)


def ensure_tick_dataframe(
    code_6: str,
    trade_date: date,
    *,
    timeout_sec: float = DEFAULT_TICK_TIMEOUT_SEC,
):
    """缺本地 tick 则请求大 QMT 同步后读 data/ticks；不直连 xtdata。"""
    c6 = "".join(ch for ch in str(code_6 or "") if ch.isdigit())[:6].zfill(6)
    if not c6 or c6 == "000000":
        return None
    try:
        from utils.tick_data_cache import read_tick_cache
    except ImportError:
        from tick_data_cache import read_tick_cache  # type: ignore[no-redef]
    if _tick_cache_ready(c6, trade_date):
        return read_tick_cache(c6, trade_date)
    # QMT 已判失败/重试耗尽时勿再空等 timeout
    if tick_sync_unavailable(c6, trade_date):
        return None
    ok = wait_tick_cache(c6, trade_date, timeout_sec=timeout_sec)
    if not ok:
        logger.warning(
            "[%s] tick 同步超时（%s）；请确认大 QMT 模型交易已启动且已绑定 download_history_data",
            c6,
            trade_date.isoformat(),
        )
        return None
    return read_tick_cache(c6, trade_date)


def list_pending_daily(limit: int = 20) -> List[Tuple[str, date, Dict[str, Any]]]:
    prune_stale_daily_failures()
    data = load_requests()
    pending: List[Tuple[str, date, Dict[str, Any], str]] = []
    daily = data.get("daily") or {}
    if not isinstance(daily, dict):
        return []
    for code, meta in daily.items():
        if not isinstance(meta, dict):
            continue
        if str(meta.get("status") or "") != "pending":
            continue
        if int(meta.get("retries") or 0) >= MAX_RETRIES:
            continue
        td = _parse_date(meta.get("through_date")) or date.today()
        pending.append((str(code), td, meta, str(meta.get("requested_at") or "")))
    pending.sort(key=lambda x: x[3])
    return [(c, td, m) for c, td, m, _ in pending[: max(1, int(limit))]]


def list_pending_ticks(limit: int = 10) -> List[Tuple[str, date, Dict[str, Any]]]:
    data = load_requests()
    out: List[Tuple[str, date, Dict[str, Any]]] = []
    tick_root = data.get("tick") or {}
    if not isinstance(tick_root, dict):
        return out
    for c6, days in tick_root.items():
        if not isinstance(days, dict):
            continue
        for ymd, meta in days.items():
            if not isinstance(meta, dict):
                continue
            if str(meta.get("status") or "") != "pending":
                continue
            if int(meta.get("retries") or 0) >= MAX_RETRIES:
                continue
            try:
                td = datetime.strptime(str(ymd)[:8], "%Y%m%d").date()
            except ValueError:
                continue
            out.append((str(c6).zfill(6)[:6], td, meta))
            if len(out) >= max(1, int(limit)):
                return out
    return out


def _set_daily_status(code: str, through_date: date, status: str, *, error: str = "") -> None:
    data = load_requests()
    daily = data.setdefault("daily", {})
    full = to_full_stock_code(code)
    meta = daily.get(full) if isinstance(daily.get(full), dict) else {}
    meta = dict(meta)
    meta["through_date"] = through_date.isoformat()
    meta["status"] = status
    meta["updated_at"] = _now_iso()
    if error:
        meta["last_error"] = error[:200]
    daily[full] = meta
    save_requests(data)


def _set_tick_status(code_6: str, trade_date: date, status: str, *, error: str = "") -> None:
    data = load_requests()
    tick_root = data.setdefault("tick", {})
    c6 = "".join(ch for ch in str(code_6 or "") if ch.isdigit())[:6].zfill(6)
    bucket = tick_root.setdefault(c6, {})
    if not isinstance(bucket, dict):
        bucket = {}
        tick_root[c6] = bucket
    ymd = trade_date.strftime("%Y%m%d")
    meta = bucket.get(ymd) if isinstance(bucket.get(ymd), dict) else {}
    meta = dict(meta)
    meta["status"] = status
    meta["updated_at"] = _now_iso()
    if error:
        meta["last_error"] = error[:200]
    bucket[ymd] = meta
    save_requests(data)


def mark_daily_done(code: str, through_date: date) -> None:
    _set_daily_status(code, through_date, "done")


def mark_daily_failed(code: str, through_date: date, reason: str) -> None:
    data = load_requests()
    daily = data.setdefault("daily", {})
    full = to_full_stock_code(code)
    meta = daily.get(full) if isinstance(daily.get(full), dict) else {}
    meta = dict(meta)
    meta["through_date"] = through_date.isoformat()
    meta["status"] = "pending"
    meta["retries"] = int(meta.get("retries") or 0) + 1
    meta["last_error"] = str(reason or "")[:200]
    meta["updated_at"] = _now_iso()
    if int(meta["retries"]) >= MAX_RETRIES:
        meta["status"] = "failed"
    daily[full] = meta
    save_requests(data)


def mark_tick_done(code_6: str, trade_date: date) -> None:
    _set_tick_status(code_6, trade_date, "done")


def mark_tick_failed(code_6: str, trade_date: date, reason: str) -> None:
    data = load_requests()
    tick_root = data.setdefault("tick", {})
    c6 = "".join(ch for ch in str(code_6 or "") if ch.isdigit())[:6].zfill(6)
    bucket = tick_root.setdefault(c6, {})
    if not isinstance(bucket, dict):
        bucket = {}
        tick_root[c6] = bucket
    ymd = trade_date.strftime("%Y%m%d")
    meta = bucket.get(ymd) if isinstance(bucket.get(ymd), dict) else {}
    meta = dict(meta)
    meta["retries"] = int(meta.get("retries") or 0) + 1
    meta["last_error"] = str(reason or "")[:200]
    meta["updated_at"] = _now_iso()
    reason_s = str(reason or "").strip().lower()
    if reason_s in ("empty_tick", "empty", "suspended", "halted"):
        meta["status"] = "failed"
        meta["retries"] = MAX_RETRIES
    elif int(meta["retries"]) >= MAX_RETRIES:
        meta["status"] = "failed"
    else:
        meta["status"] = "pending"
    bucket[ymd] = meta
    save_requests(data)
