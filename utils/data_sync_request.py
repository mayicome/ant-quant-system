# -*- coding: utf-8 -*-
"""
回测/选股缺本地缓存时，向大 QMT 内置策略提交拉取请求（日线 CSV / 日 tick parquet）。
外部进程只读写 data/data_sync_requests.json 与落盘文件，不直连 xtdata。
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUESTS_PATH = os.path.join(_PROJECT_ROOT, "data", "data_sync_requests.json")
DEFAULT_POLL_SEC = 0.5
DEFAULT_DAILY_TIMEOUT_SEC = 180
DEFAULT_TICK_TIMEOUT_SEC = 240
BACKTEST_TICK_TIMEOUT_SEC = 90.0
TICK_POOL_STALL_SEC = 30.0
TICK_POOL_TIMEOUT_MIN_SEC = 45.0
TICK_POOL_TIMEOUT_MAX_SEC = 300.0
TICK_POOL_SEC_PER_CODE = 3.0
DEFAULT_POOL_DAILY_TIMEOUT_SEC = 120.0
POOL_DAILY_SEC_PER_CODE = 4.5
POOL_DAILY_TIMEOUT_MIN_SEC = 120.0
POOL_DAILY_TIMEOUT_MAX_SEC = 900.0
POOL_DAILY_SMALL_POOL_MAX = 5
POOL_DAILY_SMALL_POOL_MIN_SEC = 30.0
POOL_DAILY_STALL_SEC = 45.0
MAX_RETRIES = 3
MIN_DAILY_CACHE_READY = 1
MIN_DAILY_BARS_MA120 = 120
POOL_MAX_DATE_LAG_DAYS = 10

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
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    for attempt in range(8):
        try:
            os.replace(tmp, path)
            return
        except OSError:
            if attempt >= 7:
                raise
            time.sleep(0.05 * (attempt + 1))


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


PoolProgressFn = Optional[Callable[[int, int, str], None]]


def count_pending_sync() -> Tuple[int, int]:
    """返回 (pending_daily 条数, pending_tick 条数)。"""
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


def _daily_cache_ready(code: str, through_date: Optional[date]) -> bool:
    """有可用日线缓存即可（不要求满 120 根；MA120 由策略/计算器自行处理）。"""
    df = load_daily_from_cache(code, through_date=through_date)
    if df is None or getattr(df, "empty", True):
        return False
    if len(df) < MIN_DAILY_CACHE_READY:
        return False
    if through_date is not None and "date" in df.columns:
        try:
            last = df["date"].max()
            if last < through_date - timedelta(days=POOL_MAX_DATE_LAG_DAYS):
                return False
        except Exception:
            pass
    return True


def _tick_cache_ready(code_6: str, trade_date: date) -> bool:
    try:
        from utils.tick_data_cache import read_tick_cache
    except ImportError:
        from tick_data_cache import read_tick_cache  # type: ignore[no-redef]
    df = read_tick_cache(code_6, trade_date)
    return df is not None and len(df) > 0


def submit_daily_requests(
    codes: Iterable[str],
    *,
    through_date: Optional[date] = None,
) -> List[str]:
    """提交日线同步请求；返回本次新提交的完整代码列表。"""
    end_d = through_date or date.today()
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
    end_d = through_date or date.today()
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
        elif time.time() - stall_since >= POOL_DAILY_STALL_SEC:
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
                (through_date or date.today()).isoformat(),
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
