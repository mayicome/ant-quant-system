#coding:gbk
"""15:35 全 A 日线同步 + on-demand；manifest 失败恢复见 15:35 / init。"""
import csv
import json
import os
import sys
import threading
import time
from datetime import date, datetime, timedelta, time as dt_time
from typing import Any, Dict, List, Optional, Tuple

DAILY_SYNC_VERSION = "20260811.01"
INTRADAY_PRIORITY_DAILY_LIMIT = 1
INTRADAY_PRIORITY_DAILY_LIMIT_MAX = 8
# ??????????????????? 1 ??????????????? download_history_data(1d) ??????????????????????
# 非连续竞价交易时段（9:00-9:30、午休、15:00-15:30）：约 1 秒 1 只
INTRADAY_ON_DEMAND_DAILY_INTERVAL_SEC = 1.0
# 连续竞价（9:30-11:30、13:00-15:00）：约 10 秒 1 只，避免抢行情线程
CONTINUOUS_QUOTE_ON_DEMAND_DAILY_INTERVAL_SEC = 10.0
INTRADAY_ON_DEMAND_DAILY_BATCH = 1
POOL_SLICE_SLEEP_SEC = 0.5
POOL_COLD_SLICE_DAYS = 80
POOL_COLD_SLICE_MAX = 3
POOL_POST_DOWNLOAD_SLEEP_SEC = 3.0
POOL_TAIL_DOWNLOAD_SLEEP_SEC = 5.0
# After-hours post-download pause between codes (keep short).
FULL_POST_DOWNLOAD_SLEEP_SEC = 0.2
# Delay before chaining tick full sync after daily gate opens.
TICK_CHAIN_DELAY_SEC = 30
MIN_STORAGE_BARS = 1
POOL_INTRADAY_MIN_BARS = 120
POOL_MAX_DATE_LAG_DAYS = 10
SYNC_HOUR = 15
SYNC_MINUTE = 35
FAILED_RECOVERY_DELAY_SEC = 60
# Market-hours FORCE: soft wall-clock quantum between API calls (cannot abort
# mid get_market_data_ex). Idle gap lets quotes catch up; no full pause.
# Realistic block ~= one ContextInfo call; keep intraday batch 1-3.
FORCE_INTRADAY_SLICE_SEC = 2.5
FORCE_INTRADAY_IDLE_SEC = 3.0
FORCE_INIT_DELAY_SEC = 5
# Retain / backfill daily bars from this floor through latest trade date (inclusive).
# ~2025-01-01 .. 2026-07-31 ~ 380-400 A-share sessions; calendar span ~620d with buffer.
BACKFILL_START_YMD = "20250101"
BACKFILL_START_DATE = date(2025, 1, 1)
LOOKBACK_CALENDAR_DAYS = 620
# Soft count floor / ContextInfo count-mode hint (storage trim is date-based).
MIN_BACKFILL_BARS = 380
# Earliest bar within this many days of effective start counts as full history
# (first A-share session after New Year is often 01-02 / 01-03).
BACKFILL_FIRST_DATE_SLACK_DAYS = 14
# Bump when window/quality rule changes so completed same-day sync re-runs once.
# v6: IPO OpenDate/list_date = effective start (no forever retry).
# Daytime FORCE uses time-slice + idle gap (not a full stop).
# v7: FORCE/cold get uses date-range (BACKFILL_START..end), not count-only last-N.
# No FORCE flag => daily/pipeline never year-backfill / never gate on first-date.
QUALITY_VERSION = 7
# Touch under data/daily_cache/ to force one-shot backfill.
# Empty file => start at BACKFILL_START_DATE; optional JSON {"start":"YYYYMMDD"}
# or plain YYYYMMDD text overrides the floor for this FORCE run.
# Runs during market hours via short time-slices. Cleared on complete.
# No FORCE_PAUSE / DAILY_SYNC_PAUSE full-stop flags.
FORCE_BACKFILL_FLAG_NAME = "FORCE_YEAR_BACKFILL"
# Touch under daily_cache/ to discard a false mid-run checkpoint (progress/ok/fail)
# on the next catch-up entry ?? survives live time-slice overwrites of manifest.json.
RESET_FORCE_PROGRESS_FLAG_NAME = "RESET_FORCE_PROGRESS"
# Cached truncated-first universe so FORCE slices do not re-scan all CSVs (~6s).
FORCE_ORDERED_CACHE_NAME = "force_ordered_codes.json"
# Do not reset failed/exhausted pool codes more often than this (seconds).
POOL_REQUEUE_COOLDOWN_SEC = 900.0
# ContextInfo 1d batch: large after hours; tiny during session (quotes first).
# Intraday: 8 codes/call; count-mode often returns partial (~124) for cold codes
# so date-range + capped per-code dig follows without pinning quotes.
CTX_BATCH_SIZE = 80
CTX_BATCH_SIZE_INTRADAY = 8
CTX_BATCH_SLEEP_SEC = 0.05
CTX_INCREMENTAL_LOOKBACK_DAYS = 15
CTX_BACKFILL_COUNT = 420
# Soft short_hist during FORCE: miss_cache short_history for rest of trade day
# (intraday skip); after-hours dig retries once. Avoids incomplete?progress=0
# re-walk storms that burn slices without advancing coverage.
# Cap per-code digs inside one batch so intraday slices stay short.
CTX_SHORT_DIG_EACH_INTRADAY = 2
CTX_SHORT_DIG_EACH_AFTER_HOURS = 8
DOWNLOAD_CHUNK = 80
DOWNLOAD_SLEEP_SEC = 0.1
READ_RETRY_COUNT = 3
# Fast path: fewer read retries when subscribe already warmed (~1-2min).
READ_RETRY_COUNT_FAST = 1
MAX_DOWNLOAD_ROUNDS = 2
# Progress line every N codes or ~interval (default quiet; DAILY_SYNC_VERBOSE=1 for per-code).
PROGRESS_EVERY = 100
PROGRESS_INTERVAL_SEC = 15.0
CSV_FIELDS = ("date", "open", "high", "low", "close", "volume", "amount")
# Persistent miss cache under daily_cache/; TTL by reason.
# local_miss: empty datadir miss; empty_history: no bars after ContextInfo.
# short_history: soft / recoverable ? short TTL, never treat as permanent.
MISS_CACHE_NAME = "sync_miss_codes.json"
MISS_TTL_DAYS = {
    "empty_history": 30,
    "delisted": 90,
    "no_ctx": 7,
    "invalid_0": 14,
    "today_halt": 0,
    "suspended": 0,
    "local_miss": 0,
    # Listing unknown / IPO short history: do not hammer forever.
    "ipo_unknown": 14,
    # Soft partial history (ContextInfo returned ~124): expire same trade day.
    "short_history": 0,
}
# Prefer ContextInfo.get_market_data_ex(period=1d); get_full_tick only after hours.
# Broker pull for missing local history: host-QMT builtin download_history_data
# (same bind as tick via ant_tick_cache_io / builtins). NOT miniQMT xtdata.
# xtdata download_history_* / miniQMT RPC kept off by default.
ENABLE_XTDATA_DOWNLOAD = False
_XTDATA_RPC_OK = False  # type: Optional[bool]
_XTDATA_RPC_DEAD_REASON = "disabled"
_MISS_CACHE: Optional[Dict[str, Any]] = None
_MISS_CACHE_DIRTY = False
_MISS_LOG_COUNT = 0
_LIST_DATE_BY_CODE: Optional[Dict[str, date]] = None
_FORCE_SLICE_IDLE_UNTIL = 0.0
_FORCE_IDLE_LOG_TS = 0.0
_BUILTIN_DL_BIND_LOGGED = False
# In-process FORCE ordered universe (disk-backed; survives slice idle gaps).
_FORCE_ORDERED_CODES: Optional[List[str]] = None
_FORCE_ORDERED_END: str = ""

try:
    from ant_qmt_paths import PROJECT_ROOT
except ImportError:
    from qmt_builtin.ant_qmt_paths import PROJECT_ROOT

try:
    from ant_rules_io import save_json_atomic
except ImportError:
    from qmt_builtin.ant_rules_io import save_json_atomic

_BUILTIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _BUILTIN_DIR not in sys.path:
    sys.path.insert(0, _BUILTIN_DIR)

_SYNC_DONE_END_DATE = ""
_FAILED_RECOVERY_DUE_AT = 0.0
_FAILED_RECOVERY_ATTEMPTED = False
_SYNC_RUNNING = False
_FAIL_LOG_COUNT = 0
_TICK_CHAIN_DUE_AT = 0.0
_TICK_CHAIN_WAIT_LOG_TS = 0.0
# Once per sync_trade_date: reopen completed gate if CSV bars still lag.
_STALE_REOPEN_CHECKED_END = ""


def _daily_sync_verbose() -> bool:
    """Chatty logs when DAILY_SYNC_VERBOSE=1: per-code miss/fail/syncing/ok, batch ctx."""
    v = str(os.environ.get("DAILY_SYNC_VERBOSE") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _is_quote_rpc_error(exc_or_msg: Any) -> bool:
    s = str(exc_or_msg or "")
    return (
        "????????" in s
        or "????????" in s
        or "Unable to connect" in s
    )


def _mark_xtdata_rpc_dead(reason: str) -> None:
    """????????????? optional-unavailable / hint???????????"""
    global _XTDATA_RPC_OK, _XTDATA_RPC_DEAD_REASON
    _XTDATA_RPC_OK = False
    msg = str(reason or "unknown")
    if msg and msg != _XTDATA_RPC_DEAD_REASON:
        _XTDATA_RPC_DEAD_REASON = msg


def _xtdata_rpc_alive() -> bool:
    """??????????? download ????????????? True??????????"""
    if not ENABLE_XTDATA_DOWNLOAD:
        return False
    return _XTDATA_RPC_OK is not False


def _probe_xtdata_rpc(xtdata) -> Tuple[bool, str]:
    """?????????????? miniQMT / xtdata RPC??"""
    del xtdata
    if not ENABLE_XTDATA_DOWNLOAD:
        return False, "disabled"
    return False, _XTDATA_RPC_DEAD_REASON or "disabled"


def _notify_quote_rpc_dead(end_s: str, detail: str) -> None:
    """?????????? download RPC ???????????????????? ContextInfo????"""
    del end_s, detail
    return


def _intraday_priority_daily_limit(pool_size: int) -> int:
    """?????????????????15 ??? 1 ?/????????????????? 8 ?/???"""
    n = max(0, int(pool_size))
    if n <= 15:
        return INTRADAY_PRIORITY_DAILY_LIMIT
    return min(INTRADAY_PRIORITY_DAILY_LIMIT_MAX, max(2, (n + 14) // 15))


def _data_paths() -> Tuple[str, str, str, str]:
    base = PROJECT_ROOT.rstrip("\\/")
    cache_dir = os.path.join(base, "data", "daily_cache")
    universe = os.path.join(base, "data", "a_share_universe.json")
    manifest = os.path.join(base, "data", "daily_cache", "manifest.json")
    return base, cache_dir, universe, manifest


def _force_backfill_flag_path(cache_dir: Optional[str] = None) -> str:
    if cache_dir:
        return os.path.join(cache_dir, FORCE_BACKFILL_FLAG_NAME)
    _, cdir, _, _ = _data_paths()
    return os.path.join(cdir, FORCE_BACKFILL_FLAG_NAME)


def _force_backfill_requested(cache_dir: Optional[str] = None) -> bool:
    try:
        return os.path.isfile(_force_backfill_flag_path(cache_dir))
    except Exception:
        return False


def _parse_ymd_to_date(raw: Any) -> Optional[date]:
    s = str(raw or "").strip().replace("-", "").replace("/", "")
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None


def _parse_force_backfill_start(cache_dir: Optional[str] = None) -> Optional[date]:
    """Read optional start from FORCE_YEAR_BACKFILL; empty/invalid => None."""
    path = _force_backfill_flag_path(cache_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = (f.read() or "").strip()
    except Exception:
        return None
    if not text:
        return None
    if text.startswith("{"):
        try:
            obj = json.loads(text)
        except Exception:
            return None
        if isinstance(obj, dict):
            return _parse_ymd_to_date(obj.get("start") or obj.get("backfill_start"))
        return None
    return _parse_ymd_to_date(text)


def _resolve_backfill_start(
    end_d: date, cache_dir: Optional[str] = None
) -> date:
    """FORCE override start if present; else BACKFILL_START_DATE. Never after end_d."""
    override = _parse_force_backfill_start(cache_dir)
    floor = override if override is not None else BACKFILL_START_DATE
    if end_d < floor:
        return end_d
    return floor


def _backfill_start_ymd(
    end_d: Optional[date] = None, cache_dir: Optional[str] = None
) -> str:
    d = end_d or date.today()
    return _resolve_backfill_start(d, cache_dir).strftime("%Y%m%d")


def _reset_force_progress_flag_path(cache_dir: Optional[str] = None) -> str:
    _, cache_dir2, _, _ = _data_paths()
    d = cache_dir or cache_dir2
    return os.path.join(d, RESET_FORCE_PROGRESS_FLAG_NAME)


def _consume_reset_force_progress(
    old_manifest: Dict[str, Any],
    cache_dir: Optional[str] = None,
    manifest_path: Optional[str] = None,
) -> Dict[str, Any]:
    """If RESET_FORCE_PROGRESS exists, zero checkpoint so false passes are retried."""
    path = _reset_force_progress_flag_path(cache_dir)
    if not os.path.isfile(path):
        return old_manifest
    prev = int(old_manifest.get("progress") or 0)
    cleared = dict(old_manifest or {})
    cleared["progress"] = 0
    cleared["ok_count"] = 0
    cleared["skip_count"] = 0
    cleared["fail_count"] = 0
    cleared["miss_skip_count"] = 0
    cleared["soft_short_hist"] = 0
    cleared["failed_codes"] = []
    cleared["started_at"] = ""
    cleared["finished_at"] = ""
    cleared["last_code"] = ""
    cleared["status"] = "paused"
    cleared["pause_reason"] = "reset_force_progress"
    cleared["primary_source"] = ""
    cleared["note"] = (
        "RESET_FORCE_PROGRESS consumed; prev_progress=%d; truncated retry from 0"
        % prev
    )
    cleared["runner_version"] = DAILY_SYNC_VERSION
    try:
        if manifest_path:
            _save_manifest(manifest_path, cleared)
    except Exception as e:
        print("[日线同步] 重置 FORCE 进度写 manifest 失败: %s" % e)
    try:
        os.remove(path)
    except Exception as e:
        print("[日线同步] 删除 FORCE 进度文件失败: %s" % e)
    print(
        "[日线同步] 已重置 FORCE 进度: prev_progress=%d -> 0"
        % prev
    )
    return cleared


def _clear_force_backfill_flag(cache_dir: Optional[str] = None) -> None:
    path = _force_backfill_flag_path(cache_dir)
    try:
        if os.path.isfile(path):
            os.remove(path)
            print("[日线同步] 已清除 FORCE 回填标记 %s" % path)
    except Exception as e:
        print("[日线同步] 清除 FORCE 回填标记失败: %s" % e)
    _clear_force_ordered_cache(cache_dir)


def _force_ordered_cache_path(cache_dir: Optional[str] = None) -> str:
    _, cache_dir2, _, _ = _data_paths()
    d = cache_dir or cache_dir2
    return os.path.join(d, FORCE_ORDERED_CACHE_NAME)


def _clear_force_ordered_cache(cache_dir: Optional[str] = None) -> None:
    """Drop in-memory + disk FORCE ordered universe (complete / incomplete rebuild)."""
    global _FORCE_ORDERED_CODES, _FORCE_ORDERED_END
    _FORCE_ORDERED_CODES = None
    _FORCE_ORDERED_END = ""
    path = _force_ordered_cache_path(cache_dir)
    try:
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


def _load_force_ordered_codes(
    cache_dir: str, end_s: str
) -> Optional[List[str]]:
    """Return cached truncated-first codes for this trade date, or None."""
    global _FORCE_ORDERED_CODES, _FORCE_ORDERED_END
    if (
        _FORCE_ORDERED_CODES
        and _FORCE_ORDERED_END == end_s
        and len(_FORCE_ORDERED_CODES) > 0
    ):
        return list(_FORCE_ORDERED_CODES)
    path = _force_ordered_cache_path(cache_dir)
    try:
        if not os.path.isfile(path):
            return None
        import json

        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            return None
        if str(payload.get("sync_trade_date") or "") != end_s:
            return None
        codes = payload.get("codes") or []
        if not isinstance(codes, list) or not codes:
            return None
        out = [str(c) for c in codes if str(c)]
        if not out:
            return None
        _FORCE_ORDERED_CODES = out
        _FORCE_ORDERED_END = end_s
        return list(out)
    except Exception as e:
        print("[日线同步] 加载有序 FORCE 缓存失败: %s" % e)
        return None


def _save_force_ordered_codes(
    cache_dir: str,
    end_s: str,
    codes: List[str],
    truncated_n: int,
    warm_n: int,
) -> None:
    """Persist truncated-first universe for subsequent FORCE time-slices."""
    global _FORCE_ORDERED_CODES, _FORCE_ORDERED_END
    out = [str(c) for c in codes if str(c)]
    _FORCE_ORDERED_CODES = out
    _FORCE_ORDERED_END = end_s
    payload = {
        "version": 1,
        "sync_trade_date": end_s,
        "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "count": len(out),
        "truncated": int(truncated_n),
        "warm": int(warm_n),
        "runner_version": DAILY_SYNC_VERSION,
        "codes": out,
    }
    try:
        save_json_atomic(_force_ordered_cache_path(cache_dir), payload)
    except Exception as e:
        print("[日线同步] 保存有序 FORCE 缓存失败: %s" % e)


def _apply_force_ordered_cache(
    filtered: List[str], cached: List[str]
) -> List[str]:
    """Reorder filtered by cached order; append any codes missing from cache."""
    if not cached:
        return filtered
    want = set(filtered)
    ordered: List[str] = []
    seen = set()
    for code in cached:
        if code in want and code not in seen:
            ordered.append(code)
            seen.add(code)
    for code in filtered:
        if code not in seen:
            ordered.append(code)
            seen.add(code)
    return ordered


def _clear_halt_miss_for_force(cache_dir: Optional[str] = None) -> int:
    """Drop today_halt/suspended miss entries so FORCE can extend truncated history."""
    global _MISS_CACHE_DIRTY
    payload = _miss_cache_load(cache_dir)
    codes = payload.get("codes") or {}
    if not isinstance(codes, dict) or not codes:
        return 0
    drop = [
        code
        for code, meta in codes.items()
        if isinstance(meta, dict)
        and str(meta.get("reason") or "") in ("today_halt", "suspended")
    ]
    for code in drop:
        codes.pop(code, None)
    if drop:
        _MISS_CACHE_DIRTY = True
        _miss_cache_save(cache_dir, force=True)
        print(
            "[日线同步] 已清除 %d 条今日停牌/暂停 miss（强制补数）"
            % len(drop)
        )
    return len(drop)


def _clear_soft_short_miss_for_main_chain(cache_dir: Optional[str] = None) -> int:
    """Drop FORCE soft_short miss so main-chain incremental can refresh those codes."""
    global _MISS_CACHE_DIRTY
    payload = _miss_cache_load(cache_dir)
    codes = payload.get("codes") or {}
    if not isinstance(codes, dict) or not codes:
        return 0
    drop = [
        code
        for code, meta in codes.items()
        if isinstance(meta, dict)
        and str(meta.get("reason") or "") == "short_history"
    ]
    for code in drop:
        codes.pop(code, None)
    if drop:
        _MISS_CACHE_DIRTY = True
        _miss_cache_save(cache_dir, force=True)
        print(
            "[日线同步] 已清除 %d 条 short_history miss（主链）"
            % len(drop)
        )
    return len(drop)


def _clear_false_delisted_miss_with_recent_csv(
    cache_dir: Optional[str] = None,
    end_d: Optional[date] = None,
    max_age_days: int = 15,
) -> int:
    """
    Drop delisted miss entries whose local CSV still has a recent last bar.
    Guards against QMT OpenDate-as-ExpireDate poison (esp. 688* STAR mass-skip).
    """
    global _MISS_CACHE_DIRTY
    if end_d is None:
        end_d = date.today()
    if cache_dir:
        cdir = cache_dir
    else:
        _, cdir, _, _ = _data_paths()
    payload = _miss_cache_load(cdir)
    codes = payload.get("codes") or {}
    if not isinstance(codes, dict) or not codes:
        return 0
    drop: List[str] = []
    for code, meta in list(codes.items()):
        if not isinstance(meta, dict):
            continue
        if str(meta.get("reason") or "") != "delisted":
            continue
        csv_p = os.path.join(cdir, str(code) + ".csv")
        last_d = _last_date_in_csv(csv_p)
        if last_d is None:
            continue
        if (end_d - last_d).days <= int(max_age_days):
            drop.append(str(code))
    for code in drop:
        codes.pop(code, None)
    if drop:
        _MISS_CACHE_DIRTY = True
        _miss_cache_save(cdir, force=True)
        print(
            "[日线同步] 已清除 %d 条误判退市 miss（CSV 末日在 %dd 内）"
            % (len(drop), int(max_age_days))
        )
    return len(drop)


def _count_stale_csv_before_end(cache_dir: str, end_d: date) -> int:
    """Count daily_cache/*.csv whose last bar date is strictly before end_d."""
    stale = 0
    try:
        names = os.listdir(cache_dir)
    except Exception:
        return 0
    for name in names:
        if not name.endswith(".csv"):
            continue
        last_d = _last_date_in_csv(os.path.join(cache_dir, name))
        if last_d is not None and last_d < end_d:
            stale += 1
    return stale


def _maybe_reopen_completed_for_stale_bars(end_d: date) -> bool:
    """
    Reopen same-day completed gate when local CSV last bars still lag end_d.

    Typical after false-delisted miss clear left codes unwalked while manifest
    was already completed. Triggers pipeline/init incremental only ? never arms
    FORCE_YEAR_BACKFILL.
    """
    global _SYNC_DONE_END_DATE, _STALE_REOPEN_CHECKED_END
    if _force_backfill_requested():
        return False
    end_s = end_d.isoformat()
    if _STALE_REOPEN_CHECKED_END == end_s:
        return False
    _, cache_dir, _, manifest_path = _data_paths()
    manifest = _load_manifest(manifest_path)
    if not _already_synced_to_end(manifest, end_d):
        _STALE_REOPEN_CHECKED_END = end_s
        return False

    cleared = _clear_false_delisted_miss_with_recent_csv(cache_dir, end_d=end_d)
    miss_skip = int(manifest.get("miss_skip_count") or 0)
    # miss_skip>0 on a completed day is the poison fingerprint (even after clear).
    if cleared <= 0 and miss_skip <= 0:
        _STALE_REOPEN_CHECKED_END = end_s
        return False

    stale_n = _count_stale_csv_before_end(cache_dir, end_d)
    _STALE_REOPEN_CHECKED_END = end_s
    if stale_n <= 0 and cleared <= 0:
        return False

    reopened = dict(manifest)
    reopened["status"] = ""
    reopened["pause_reason"] = "stale_after_miss_clear"
    reopened["progress"] = 0
    reopened["stale_reopen_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    reopened["stale_reopen_count"] = int(stale_n)
    reopened["stale_reopen_cleared_miss"] = int(cleared)
    try:
        _save_manifest(manifest_path, reopened)
    except Exception as e:
        print("[日线同步] 清理过期 miss 记录失败: %s" % e)
        return False
    _SYNC_DONE_END_DATE = ""
    print(
        "[日线同步] 重开已完成增量 stale_csv=%d cleared_miss=%d miss_skip_was=%d end=%s（无强制补数）"
        % (stale_n, cleared, miss_skip, end_s)
    )
    return True


def _abandon_stale_force_partial(
    old_manifest: Dict[str, Any],
    cache_dir: Optional[str] = None,
    manifest_path: Optional[str] = None,
) -> Dict[str, Any]:
    """When FORCE flag is gone, drop FORCE mid-run / incomplete so gate can finish."""
    if _force_backfill_requested(cache_dir):
        return old_manifest
    status = str(old_manifest.get("status") or "")
    if status not in ("running", "incomplete", "paused"):
        return old_manifest
    pause = str(old_manifest.get("pause_reason") or "")
    trigger = str(old_manifest.get("trigger") or "")
    force_like = (
        pause in ("defer_after_hours", "incomplete_retry", "time_slice", "reset_force_progress")
        or "force" in trigger.lower()
        or int(old_manifest.get("soft_short_hist") or 0) > 0
        or status == "incomplete"
    )
    if not force_like and status != "running":
        return old_manifest
    cleared = dict(old_manifest or {})
    cleared["status"] = ""
    cleared["progress"] = 0
    cleared["progress_total"] = 0
    cleared["pause_reason"] = ""
    cleared["finished_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    cleared["note"] = (
        "FORCE absent; abandoned stale partial (was status=%s trigger=%s); "
        "next catch-up is today incremental"
        % (status, trigger or "-")
    )
    cleared["runner_version"] = DAILY_SYNC_VERSION
    try:
        if manifest_path:
            _save_manifest(manifest_path, cleared)
    except Exception as e:
        print("[日线同步] 写 miss 缓存失败: %s" % e)
    _clear_force_ordered_cache(cache_dir)
    _clear_soft_short_miss_for_main_chain(cache_dir)
    print(
        "[日线同步] 已放弃过期强制补数部分进度 status=%s trigger=%s （强制补数标志不存在）"
        % (status, trigger or "-")
    )
    return cleared


def _miss_cache_path(cache_dir: Optional[str] = None) -> str:
    if cache_dir:
        return os.path.join(cache_dir, MISS_CACHE_NAME)
    _, cdir, _, _ = _data_paths()
    return os.path.join(cdir, MISS_CACHE_NAME)


def _miss_cache_load(cache_dir: Optional[str] = None) -> Dict[str, Any]:
    global _MISS_CACHE
    if _MISS_CACHE is not None:
        return _MISS_CACHE
    path = _miss_cache_path(cache_dir)
    payload = {"version": 1, "codes": {}}  # type: Dict[str, Any]
    try:
        import json

        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
            if isinstance(raw, dict):
                codes = raw.get("codes")
                if not isinstance(codes, dict):
                    codes = {}
                payload = {"version": int(raw.get("version") or 1), "codes": codes}
    except Exception:
        pass
    _MISS_CACHE = payload
    return payload


def _miss_cache_save(cache_dir: Optional[str] = None, force: bool = False) -> None:
    global _MISS_CACHE_DIRTY
    if not force and not _MISS_CACHE_DIRTY:
        return
    payload = _miss_cache_load(cache_dir)
    path = _miss_cache_path(cache_dir)
    try:
        save_json_atomic(path, payload)
        _MISS_CACHE_DIRTY = False
    except Exception as e:
        print("[日线同步] miss 缓存保存失败: %s" % e)


def _miss_until_date(reason: str, fail_day: date) -> date:
    ttl = int(MISS_TTL_DAYS.get(str(reason or ""), 7))
    if ttl <= 0:
        return fail_day
    return fail_day + timedelta(days=ttl)


def _miss_cache_get(code: str, cache_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    payload = _miss_cache_load(cache_dir)
    codes = payload.get("codes") or {}
    meta = codes.get(str(code or "").strip().upper())
    return meta if isinstance(meta, dict) else None


def _miss_cache_active(
    code: str, today: date, cache_dir: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    meta = _miss_cache_get(code, cache_dir)
    if not meta:
        return None
    until_s = str(meta.get("until") or "").strip()[:10]
    if not until_s:
        return meta
    try:
        until_d = datetime.strptime(until_s, "%Y-%m-%d").date()
    except ValueError:
        return meta
    if until_d < today:
        return None
    return meta


def _miss_cache_put(
    code: str,
    reason: str,
    fail_day: date,
    cache_dir: Optional[str] = None,
) -> None:
    global _MISS_CACHE_DIRTY, _MISS_LOG_COUNT
    full = str(code or "").strip().upper()
    if not full:
        return
    reason_s = str(reason or "empty_history").strip() or "empty_history"
    # ???????????????????? miss
    if reason_s in ("transient", "rpc_dead", "timeout", "error"):
        return
    # ???? datadir ?????? ?? ????????????? local miss ???? 30 ?? empty_history
    if reason_s in ("empty_history_fast", "local_miss", "local_empty"):
        reason_s = "local_miss"
    payload = _miss_cache_load(cache_dir)
    codes = payload.setdefault("codes", {})
    prev = codes.get(full) if isinstance(codes.get(full), dict) else {}
    fail_count = int(prev.get("fail_count") or 0) + 1
    # ????????? local_miss ??????? tick ????? empty_history?????????????? empty_history??
    until_d = _miss_until_date(reason_s, fail_day)
    # ???????? until ?????
    try:
        prev_until = datetime.strptime(
            str(prev.get("until") or "")[:10], "%Y-%m-%d"
        ).date()
        if prev_until > until_d and str(prev.get("reason") or "") == reason_s:
            until_d = prev_until
    except ValueError:
        pass
    codes[full] = {
        "reason": reason_s,
        "last_fail_date": fail_day.isoformat(),
        "fail_count": fail_count,
        "until": until_d.isoformat(),
        "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _MISS_CACHE_DIRTY = True
    if _daily_sync_verbose() and _MISS_LOG_COUNT < 8:
        _MISS_LOG_COUNT += 1
        print(
            "[日线同步] miss缓存 +%s reason=%s until=%s fail_count=%d"
            % (full, reason_s, until_d.isoformat(), fail_count)
        )


def _miss_cache_clear(code: str, cache_dir: Optional[str] = None) -> None:
    global _MISS_CACHE_DIRTY
    full = str(code or "").strip().upper()
    payload = _miss_cache_load(cache_dir)
    codes = payload.get("codes") or {}
    if full in codes:
        codes.pop(full, None)
        _MISS_CACHE_DIRTY = True


def _miss_reason_from_fail(reason: Optional[str]) -> Optional[str]:
    s = str(reason or "").strip().lower()
    if not s:
        return None
    # Soft partial history: same-trade-day miss (intraday skip; after-hours dig).
    if s.startswith("short_hist"):
        return "short_history"
    if s.startswith("miss_cache_") or s in ("today_halt", "suspended", "delisted"):
        return s.replace("miss_cache_", "") if s.startswith("miss_cache_") else s
    # ????? / ?? miss ?????????????? local_miss?????? empty_history
    if s in ("empty_history_fast", "local_miss", "local_empty") or "local_miss" in s:
        return "local_miss"
    if s.endswith("_fast") and "empty" in s:
        return "local_miss"
    if "empty_history" in s or s in ("empty", "empty_tick"):
        return "empty_history"
    if s.startswith("invalid_0_valid") or s.startswith("invalid_0_"):
        return "invalid_0"
    if "no_ctx" in s or s == "no_ctx_no_rpc":
        return "no_ctx"
    # \u9000\u5e02 = delisted marker in Chinese instrument names
    if "delist" in s or "\u9000\u5e02" in str(reason or ""):
        return "delisted"
    if "halt" in s or "suspend" in s or "\u505c\u724c" in str(reason or ""):
        return "today_halt"
    return None


def _parse_expire_ymd(val: Any) -> Optional[date]:
    if val is None or val == "":
        return None
    digits = "".join(ch for ch in str(val) if ch.isdigit())
    if len(digits) < 8:
        return None
    ymd = digits[:8]
    # Far-future / broker sentinels are not real expire dates.
    if ymd >= "90000000" or ymd in ("10000991", "99999999"):
        return None
    try:
        return datetime.strptime(ymd, "%Y%m%d").date()
    except ValueError:
        return None


def _instrument_detail(ContextInfo, xtdata, code: str) -> Dict[str, Any]:
    full = str(code or "").strip().upper()
    if not full:
        return {}
    for owner in (ContextInfo, xtdata):
        if owner is None:
            continue
        for fn_name in ("get_instrumentdetail", "get_instrument_detail"):
            fn = getattr(owner, fn_name, None)
            if not callable(fn):
                continue
            try:
                det = fn(full)
            except Exception:
                det = None
            if isinstance(det, dict) and det:
                return det
    return {}


def _classify_instrument_status(
    ContextInfo, xtdata, code: str, today: date
) -> Optional[str]:
    """
    Return miss reason or None.
    - delisted: name contains \u9000\u5e02, or ExpireDate past and not an OpenDate mirror
    - today_halt: is_suspended_stock
    IsTrading=False alone is NOT delisted (pre-market / halt noise).
    InstrumentStatus==0 alone is NOT delisted (STAR false positives).
    QMT often mirrors OpenDate into ExpireDate for live 688* STAR names ?? ignore that.
    """
    full = str(code or "").strip().upper()
    if ContextInfo is not None:
        fn = getattr(ContextInfo, "is_suspended_stock", None)
        if callable(fn):
            try:
                if bool(fn(full)):
                    return "today_halt"
            except Exception:
                pass
    det = _instrument_detail(ContextInfo, xtdata, full)
    if not det:
        return None
    name = str(
        det.get("InstrumentName")
        or det.get("instrumentName")
        or det.get("InstrumentDisplayName")
        or ""
    )
    if "\u9000\u5e02" in name:
        return "delisted"
    exp = _parse_expire_ymd(det.get("ExpireDate") or det.get("expireDate"))
    if exp is not None and exp <= today:
        open_d = None
        for key in (
            "OpenDate",
            "open_date",
            "ListDate",
            "list_date",
            "CreateDate",
        ):
            open_d = _parse_expire_ymd(det.get(key))
            if open_d is not None:
                break
        # OpenDate mirrored into ExpireDate (common STAR poison) ?? not delisted.
        if open_d is not None and (exp == open_d or exp < open_d):
            return None
        if exp.year < 1991:
            return None
        return "delisted"
    return None


def _load_xtdata():
    import xtquant.xtdata as xtdata
    try:
        xtdata.enable_hello = False
    except Exception:
        pass
    return xtdata


def _normalize_symbol(raw: Any) -> Optional[str]:
    s = str(raw or "").strip().upper()
    if not s:
        return None
    if "." in s:
        return s
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) < 6:
        return None
    code_6 = digits[:6].zfill(6)
    if code_6.startswith("6"):
        return code_6 + ".SH"
    if code_6.startswith(("4", "8")) or code_6.startswith("920"):
        return code_6 + ".BJ"
    return code_6 + ".SZ"


def _is_main_a_share(code: str) -> bool:
    code_6 = "".join(ch for ch in str(code or "") if ch.isdigit())[:6].zfill(6)
    if len(code_6) != 6:
        return False
    return code_6[0] in ("0", "3", "4", "6", "8", "9")


def _fetch_universe(xtdata, ContextInfo=None) -> List[str]:
    # ????? unicode ?????????????????????????????????A????
    sectors = (
        "\u6caa\u6df1A\u80a1",  # ????A??
        "\u4e0a\u8bc1A\u80a1",  # ???A??
        "\u6df1\u8bc1A\u80a1",  # ???A??
        "\u4eac\u5e02A\u80a1",  # ????A???????????
        "\u6caa\u6df1\u4eacA\u80a1",  # ????A??????????????
    )
    raw = []
    owners: List[Any] = []
    if ContextInfo is not None:
        owners.append(("ctx", ContextInfo))
    owners.append(("xt", xtdata))
    for owner_label, owner in owners:
        got = []
        for sec in sectors:
            fn = getattr(owner, "get_stock_list_in_sector", None)
            if not callable(fn):
                continue
            try:
                got.extend(fn(sec) or [])
            except Exception:
                continue
        if got:
            raw = got
            if _daily_sync_verbose():
                print("[日线同步] 股票池来源=%s n_raw=%d" % (owner_label, len(raw)))
            break
    if not raw:
        # ???????????????????????????
        try:
            import json

            _, _, universe_path, _ = _data_paths()
            with open(universe_path, "r", encoding="utf-8") as f:
                payload = json.load(f) or {}
            raw = list(payload.get("codes") or [])
            if _daily_sync_verbose():
                print("[日线同步] 股票池回退列表 n=%d" % len(raw))
        except Exception as e:
            print("[日线同步] 股票池回退失败: %s" % e)
            raw = []
    out = []
    seen = set()
    for item in raw:
        full = _normalize_symbol(item)
        if not full or full in seen:
            continue
        if not _is_main_a_share(full):
            continue
        seen.add(full)
        out.append(full)
    out.sort()
    return out


def _parse_trade_date_from_ts(ts_val: Any) -> Optional[date]:
    if ts_val is None:
        return None
    if isinstance(ts_val, datetime):
        return ts_val.date()
    if isinstance(ts_val, date):
        return ts_val
    # ????/??????????????? epoch ??????
    # ?????? 1785081600000??2026-07-27??? 8 ??????? YYYYMMDD=17850816??
    try:
        ts = float(ts_val)
    except (TypeError, ValueError):
        ts = None
    if ts is not None and ts >= 1e11:
        if ts > 1e12:
            ts = ts / 1000.0
        try:
            return datetime.fromtimestamp(ts).date()
        except (OSError, OverflowError, ValueError):
            return None
    s = str(ts_val).strip()
    if not s:
        return None
    # ContextInfo ???? index/stime ???? YYYYMMDD / YYYY-MM-DD / YYYYMMDDhhmmss
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        try:
            return datetime.strptime(digits[:8], "%Y%m%d").date()
        except ValueError:
            pass
    if ts is None:
        return None
    if ts > 1e12:
        ts = ts / 1000.0
    try:
        return datetime.fromtimestamp(ts).date()
    except (OSError, OverflowError, ValueError):
        return None


def _log_fetch_fail(code: str, start_s: str, end_s: str, reason: str) -> None:
    global _FAIL_LOG_COUNT
    if not _daily_sync_verbose():
        return
    if _FAIL_LOG_COUNT >= 5:
        return
    _FAIL_LOG_COUNT += 1
    print(
        "[日线同步] 拉取为空 %s range=%s..%s reason=%s"
        % (code, start_s, end_s, reason)
    )


def _trading_dates_between(xtdata, start_d: date, end_d: date) -> List[date]:
    if end_d < start_d:
        return []
    try:
        arr = xtdata.get_trading_dates(
            "SH",
            start_d.strftime("%Y%m%d"),
            end_d.strftime("%Y%m%d"),
        ) or []
    except Exception:
        arr = []
    out = []
    for ts in arr:
        d = _parse_trade_date_from_ts(ts)
        if d is not None and start_d <= d <= end_d:
            out.append(d)
    if out:
        return sorted(set(out))
    # ?????????????
    cur = start_d
    while cur <= end_d:
        if cur.weekday() < 5:
            out.append(cur)
        cur += timedelta(days=1)
    return out


def _is_tradeday(xtdata, day: date) -> bool:
    ds = day.strftime("%Y%m%d")
    try:
        arr = xtdata.get_trading_dates("SH", ds, ds) or []
    except Exception:
        arr = []
    if not arr:
        return day.weekday() < 5
    for ts in arr:
        d = _parse_trade_date_from_ts(ts)
        if d == day:
            return True
    return False


def _last_tradeday_on_or_before(xtdata, day: date, lookback_days: int = 90) -> date:
    start_d = day - timedelta(days=lookback_days)
    dates = _trading_dates_between(xtdata, start_d, day)
    if dates:
        return dates[-1]
    cur = day
    for _ in range(lookback_days):
        if cur.weekday() < 5:
            return cur
        cur -= timedelta(days=1)
    return day


def _last_tradeday_before(xtdata, day: date, lookback_days: int = 90) -> date:
    start_d = day - timedelta(days=lookback_days)
    end_d = day - timedelta(days=1)
    dates = _trading_dates_between(xtdata, start_d, end_d)
    if dates:
        return dates[-1]
    cur = day - timedelta(days=1)
    for _ in range(lookback_days):
        if cur.weekday() < 5:
            return cur
        cur -= timedelta(days=1)
    return day - timedelta(days=1)


def _resolve_sync_end_date(xtdata, now: datetime) -> date:
    """???????????????????????????????"""
    today = now.date()
    if _is_tradeday(xtdata, today):
        if (now.hour, now.minute) >= (SYNC_HOUR, SYNC_MINUTE):
            return today
        return _last_tradeday_before(xtdata, today)
    return _last_tradeday_on_or_before(xtdata, today)


def _last_weekday_before(day: date) -> date:
    """?????????????????????????????????????????"""
    cur = day - timedelta(days=1)
    for _ in range(14):
        if cur.weekday() < 5:
            return cur
        cur -= timedelta(days=1)
    return day - timedelta(days=1)


def _pool_daily_write_end_date(
    xtdata, request_end_d: date, now: Optional[datetime] = None
) -> date:
    """??/????????????????????????15:35 ????????????? K??

    ????????????? through_date ????????????????????????????? K????????? OHLCV????
    """
    now = now or datetime.now()
    today = now.date()
    if request_end_d > today:
        request_end_d = today
    if request_end_d < today:
        return request_end_d
    if (now.hour, now.minute) < (SYNC_HOUR, SYNC_MINUTE):
        if xtdata is not None:
            if _is_tradeday(xtdata, today):
                return _last_tradeday_before(xtdata, today)
            return _last_tradeday_on_or_before(xtdata, today - timedelta(days=1))
        return _last_weekday_before(today)
    return request_end_d


def _bar_trade_date(bar: Dict[str, Any]) -> Optional[date]:
    d_s = str(bar.get("date") or "")
    if not d_s:
        return None
    try:
        return datetime.strptime(d_s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _remove_bars_after_date(
    rows_by_date: Dict[str, Dict[str, Any]], max_d: date
) -> int:
    removed = 0
    for d_s in list(rows_by_date.keys()):
        try:
            d = datetime.strptime(d_s[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if d > max_d:
            del rows_by_date[d_s]
            removed += 1
    return removed


def _clip_bars_on_or_before(
    bars: List[Dict[str, Any]], max_d: date
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for bar in bars or []:
        d = _bar_trade_date(bar)
        if d is not None and d <= max_d:
            out.append(bar)
    return out


def _pool_strip_forming_csv(csv_path: str, write_end_d: date) -> bool:
    """??????????????????????? K ???"""
    rows = _read_csv_rows(csv_path)
    if not rows:
        return False
    if _remove_bars_after_date(rows, write_end_d) <= 0:
        return False
    try:
        _write_daily_cache_csv(
            cache_dir, code, rows, ContextInfo=ContextInfo, xtdata=xtdata
        )
        return True
    except Exception:
        return False


def _read_csv_rows(path: str) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    if not os.path.isfile(path):
        return rows
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                d = str(row.get("date") or "").strip()
                if not d:
                    continue
                rows[d] = row
    except Exception:
        return {}
    return rows


def _write_csv_atomic(path: str, rows_by_date: Dict[str, Dict[str, Any]]) -> None:
    # Hard cap: never persist bars after today (backtest through_date may be future).
    _remove_bars_after_date(rows_by_date, date.today())
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    ordered = sorted(rows_by_date.keys())
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for d in ordered:
            row = rows_by_date[d]
            writer.writerow(
                {
                    "date": d,
                    "open": row.get("open", ""),
                    "high": row.get("high", ""),
                    "low": row.get("low", ""),
                    "close": row.get("close", ""),
                    "volume": row.get("volume", ""),
                    "amount": row.get("amount", ""),
                }
            )
    for attempt in range(8):
        try:
            os.replace(tmp, path)
            return
        except OSError:
            if attempt >= 7:
                raise
            time.sleep(0.05 * (attempt + 1))


def _cache_qfq_dir(cache_dir: str) -> str:
    base = os.path.dirname(cache_dir.rstrip("\\/"))
    return os.path.join(base, "daily_cache_qfq")


def _full_qfq_dir() -> str:
    return os.path.join(_data_paths()[0], "data", "daily_full_qfq")


def _qfq_cache_floor_date() -> date:
    return date(date.today().year, 1, 1)


def _qfq_full_cap_date() -> date:
    return date(date.today().year - 1, 12, 31)


def _rows_date_bounds(rows: Dict[str, Dict[str, Any]]) -> Tuple[Optional[date], Optional[date]]:
    ds: List[date] = []
    for k in rows or {}:
        try:
            ds.append(datetime.strptime(str(k)[:10], "%Y-%m-%d").date())
        except ValueError:
            continue
    if not ds:
        return None, None
    return min(ds), max(ds)


def _trim_rows_before(rows: Dict[str, Dict[str, Any]], floor_d: date) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in (rows or {}).items():
        try:
            if datetime.strptime(str(k)[:10], "%Y-%m-%d").date() >= floor_d:
                out[k] = v
        except ValueError:
            continue
    return out


def _trim_rows_after(rows: Dict[str, Dict[str, Any]], cap_d: date) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in (rows or {}).items():
        try:
            if datetime.strptime(str(k)[:10], "%Y-%m-%d").date() <= cap_d:
                out[k] = v
        except ValueError:
            continue
    return out


def _mirror_qfq_cache_csv(
    cache_dir: str,
    code: str,
    rows_none: Dict[str, Dict[str, Any]],
    ContextInfo=None,
    xtdata=None,
) -> None:
    """同步写入 daily_cache_qfq（前复权）；窗口：当年 1/1 .. rows 末日。"""
    if not rows_none or ContextInfo is None:
        return
    d0, d1 = _rows_date_bounds(rows_none)
    if d0 is None or d1 is None:
        return
    floor_d = _qfq_cache_floor_date()
    start_d = max(d0, floor_d)
    end_d = d1
    if start_d > end_d:
        return
    batch_map, _ = _batch_fetch_1d_bars(
        ContextInfo,
        xtdata,
        [code],
        start_d,
        end_d,
        prefer_count=-1,
        quality_min=1,
        dividend_type="front",
    )
    bars = _sanitize_bars(batch_map.get(code) or [])
    if not bars:
        return
    qfq_dir = _cache_qfq_dir(cache_dir)
    os.makedirs(qfq_dir, exist_ok=True)
    qfq_path = os.path.join(qfq_dir, code + ".csv")
    qfq_rows = _read_csv_rows(qfq_path)
    _merge_bars(qfq_rows, bars)
    qfq_rows = _trim_rows_for_storage(qfq_rows)
    qfq_rows = _trim_rows_before(qfq_rows, floor_d)
    _write_csv_atomic(qfq_path, qfq_rows)


def _mirror_qfq_full_csv(
    code: str,
    start_d: date,
    end_d: date,
    ContextInfo=None,
    xtdata=None,
) -> None:
    """按需全量成功后写入 daily_full_qfq（前复权，裁至去年末）。"""
    if ContextInfo is None:
        return
    cap_d = _qfq_full_cap_date()
    eff_end = min(end_d, cap_d)
    if start_d > eff_end:
        return
    batch_map, _ = _batch_fetch_1d_bars(
        ContextInfo,
        xtdata,
        [code],
        start_d,
        eff_end,
        prefer_count=-1,
        quality_min=1,
        dividend_type="front",
    )
    bars = _sanitize_bars(batch_map.get(code) or [])
    if len(bars) < 4:
        return
    rows: Dict[str, Dict[str, Any]] = {}
    _merge_bars(rows, bars)
    rows = _trim_rows_after(rows, cap_d)
    if len(rows) < 4:
        return
    qfq_dir = _full_qfq_dir()
    os.makedirs(qfq_dir, exist_ok=True)
    _write_csv_atomic(os.path.join(qfq_dir, code + ".csv"), rows)


def _write_daily_cache_csv(
    cache_dir: str,
    code: str,
    rows: Dict[str, Dict[str, Any]],
    ContextInfo=None,
    xtdata=None,
) -> None:
    csv_path = os.path.join(cache_dir, code + ".csv")
    _write_csv_atomic(csv_path, rows)
    try:
        _mirror_qfq_cache_csv(cache_dir, code, rows, ContextInfo=ContextInfo, xtdata=xtdata)
    except Exception as e:
        print("[日线同步] qfq cache mirror %s: %s" % (code, e))


def _last_date_in_csv(path: str) -> Optional[date]:
    rows = _read_csv_rows(path)
    if not rows:
        return None
    try:
        return datetime.strptime(max(rows.keys()), "%Y-%m-%d").date()
    except ValueError:
        return None


def _peek_csv_last_date(path: str) -> Optional[date]:
    """????????????????????? stall ?????????? CSV??"""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 4096), os.SEEK_SET)
            chunk = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return None
    last = None
    for line in chunk.splitlines():
        if not line or line.startswith("date"):
            continue
        d_s = line.split(",", 1)[0].strip()[:10]
        try:
            d = datetime.strptime(d_s, "%Y-%m-%d").date()
        except ValueError:
            continue
        if last is None or d > last:
            last = d
    return last


def _pool_csv_tail_ready(
    code: str, cache_dir: str, through_date: Optional[date] = None
) -> bool:
    """??????CSV ?????????????????????????? stall ???????"""
    end_d = through_date or date.today()
    check_d = _pool_daily_write_end_date(None, end_d)
    last = _peek_csv_last_date(os.path.join(cache_dir, code + ".csv"))
    return last is not None and last >= check_d


def _range_strings(start_d: date, end_d: date) -> Tuple[str, str]:
    return (
        start_d.strftime("%Y%m%d") + "000000",
        end_d.strftime("%Y%m%d") + "235959",
    )


def _short_range_strings(start_d: date, end_d: date) -> Tuple[str, str]:
    return start_d.strftime("%Y%m%d"), end_d.strftime("%Y%m%d")


def _tick_download_bind_fns():
    # type: () -> Tuple[Any, Any]
    """Reuse tick bind/resolve for host-QMT builtins.download_history_data."""
    try:
        from ant_tick_cache_io import (
            bind_download_history_data,
            resolve_download_history_data,
        )
    except ImportError:
        from qmt_builtin.ant_tick_cache_io import (
            bind_download_history_data,
            resolve_download_history_data,
        )
    return bind_download_history_data, resolve_download_history_data


def _ensure_builtin_download_bound() -> bool:
    """Bind strategy/builtins download_history_data (entry already binds on init)."""
    global _BUILTIN_DL_BIND_LOGGED
    try:
        bind_fn, resolve_fn = _tick_download_bind_fns()
        ok = bool(bind_fn(None))
        fn = resolve_fn() if ok else None
        ready = bool(ok and callable(fn))
        if not _BUILTIN_DL_BIND_LOGGED:
            _BUILTIN_DL_BIND_LOGGED = True
            if not ready:
                print("[日线同步] 内置 download_history_data 绑定=no")
        return ready
    except Exception as e:
        if not _BUILTIN_DL_BIND_LOGGED:
            _BUILTIN_DL_BIND_LOGGED = True
            print(
                "[日线同步] 内置 download_history_data 绑定错误: %s: %s"
                % (type(e).__name__, e)
            )
        return False


def _download_1d_via_builtin(
    codes: List[str], start_d: date, end_d: date
) -> Tuple[int, str]:
    """Pull day bars from broker via host-QMT download_history_data.

    Official form (same family as tick):
      download_history_data("000001.SZ", "1d", "20250101", "20260731")
    Bind source: ant_tick_cache_io -> builtins / strategy globals.
    Does NOT use xtdata / ENABLE_XTDATA_DOWNLOAD.
    Returns (ok_count, detail).
    """
    if not codes:
        return 0, "empty"
    try:
        bind_fn, resolve_fn = _tick_download_bind_fns()
        bind_fn(None)
        fn = resolve_fn()
    except Exception as e:
        return 0, "bind_err_%s" % type(e).__name__
    if not callable(fn):
        return 0, "no_builtin_download_history_data"
    s_ymd, e_ymd = _short_range_strings(start_d, end_d)
    ok = 0
    last_err = ""
    period_used = ""
    for c in codes:
        fc = _normalize_symbol(c) or str(c or "").strip().upper()
        if not fc:
            continue
        done = False
        for period in ("1d", "day"):
            try:
                fn(fc, period, s_ymd, e_ymd)
                ok += 1
                period_used = period
                done = True
                break
            except TypeError:
                try:
                    fn(fc, period, s_ymd, e_ymd, None)
                    ok += 1
                    period_used = period
                    done = True
                    break
                except Exception as e:
                    last_err = "%s: %s" % (type(e).__name__, e)
            except Exception as e:
                last_err = "%s: %s" % (type(e).__name__, e)
                continue
        if not done and not last_err:
            last_err = "call_failed"
    if ok > 0:
        return ok, "period=%s ok=%d/%d" % (period_used or "1d", ok, len(codes))
    return 0, last_err or "all_failed"


def _bars_need_broker_1d_dl(
    bars: Optional[List[Dict[str, Any]]],
    end_d: date,
    list_date: Optional[date] = None,
) -> bool:
    """True when FORCE/full backfill still short or first date late vs effective start."""
    return not _bars_meet_requirement(
        bars or [], True, 1, end_d=end_d, list_date=list_date
    )


def _stock_data_to_bars(stock_data: Any) -> List[Dict[str, Any]]:
    if stock_data is None:
        return []
    try:
        import pandas as pd
    except Exception:
        pd = None

    rows: List[Dict[str, Any]] = []
    if pd is not None and isinstance(stock_data, pd.DataFrame):
        if stock_data.empty:
            return []
        df = stock_data
        for idx, row in df.iterrows():
            # QMT??index ??? time/stime??ContextInfo ???? index=stime
            ts = idx
            for col in ("time", "stime", "Time"):
                if col in df.columns:
                    try:
                        val = row.get(col)
                    except Exception:
                        val = None
                    if val is not None and str(val).strip() != "":
                        ts = val
                        break
            d = _parse_trade_date_from_ts(ts)
            if d is None:
                continue
            rows.append(
                {
                    "date": d.strftime("%Y-%m-%d"),
                    "open": row.get("open", ""),
                    "high": row.get("high", ""),
                    "low": row.get("low", ""),
                    "close": row.get("close", ""),
                    "volume": row.get("volume", ""),
                    "amount": row.get("amount", "") if "amount" in df.columns else "",
                }
            )
        return rows

    if isinstance(stock_data, dict):
        try:
            times = stock_data.get("time") or []
            opens = stock_data.get("open") or []
            highs = stock_data.get("high") or []
            lows = stock_data.get("low") or []
            closes = stock_data.get("close") or []
            volumes = stock_data.get("volume") or []
            amounts = stock_data.get("amount") or []
        except (KeyError, TypeError):
            return []
        n = min(
            len(times),
            len(opens),
            len(highs),
            len(lows),
            len(closes),
            len(volumes),
        )
        for i in range(n):
            d = _parse_trade_date_from_ts(times[i])
            if d is None:
                continue
            rows.append(
                {
                    "date": d.strftime("%Y-%m-%d"),
                    "open": opens[i],
                    "high": highs[i],
                    "low": lows[i],
                    "close": closes[i],
                    "volume": volumes[i],
                    "amount": amounts[i] if i < len(amounts) else "",
                }
            )
    return rows


def _to_float(val: Any) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _is_valid_bar(bar: Dict[str, Any]) -> bool:
    """???? K ?????? QMT ?????volume=0 ?? OHLC ????????"""
    close = _to_float(bar.get("close"))
    if close <= 0.0:
        return False
    vol = _to_float(bar.get("volume"))
    o = _to_float(bar.get("open"))
    h = _to_float(bar.get("high"))
    l = _to_float(bar.get("low"))
    if vol <= 0 and o == h == l == close:
        return False
    return True


def _sanitize_bars(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [bar for bar in bars if _is_valid_bar(bar)]


def _rows_to_bars(rows_by_date: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    bars: List[Dict[str, Any]] = []
    for d, row in (rows_by_date or {}).items():
        if not isinstance(row, dict):
            continue
        bars.append(
            {
                "date": str(d),
                "open": row.get("open", ""),
                "high": row.get("high", ""),
                "low": row.get("low", ""),
                "close": row.get("close", ""),
                "volume": row.get("volume", ""),
                "amount": row.get("amount", ""),
            }
        )
    return bars


def _filter_pool_bars(bars: List[Dict[str, Any]], end_d: date) -> List[Dict[str, Any]]:
    """Keep bars from BACKFILL_START_DATE through end_d (inclusive, no future slack)."""
    min_d = _backfill_start_for(end_d)
    max_d = end_d
    out: List[Dict[str, Any]] = []
    for bar in _sanitize_bars(bars):
        d_s = str(bar.get("date") or "")
        try:
            d = datetime.strptime(d_s[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if min_d <= d <= max_d:
            out.append(bar)
    return out


def _pool_trade_bars(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep volume>0 bars only."""
    out: List[Dict[str, Any]] = []
    for bar in bars or []:
        if _to_float(bar.get("volume")) > 0:
            out.append(bar)
    return out


def _pool_storage_reason(
    bars: List[Dict[str, Any]], end_d: date, xtdata=None, list_date: Optional[date] = None
) -> Optional[str]:
    """Reject reason; None=OK. Prefer coverage from effective backfill start."""
    bars = _filter_pool_bars(bars, end_d)
    trade = _pool_trade_bars(bars)
    if len(trade) < MIN_STORAGE_BARS:
        return "vol_%d" % len(trade)
    earliest = None
    last_d = None
    closes: List[float] = []
    for bar in trade:
        d_s = str(bar.get("date") or "")
        try:
            d = datetime.strptime(d_s[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if earliest is None or d < earliest:
            earliest = d
        if last_d is None or d > last_d:
            last_d = d
        closes.append(_to_float(bar.get("close")))
    floor = _backfill_start_for(end_d)
    effective = _effective_backfill_start(end_d, list_date)
    slack = timedelta(days=BACKFILL_FIRST_DATE_SLACK_DAYS)
    # Truncated mid-window vs effective start (IPO OpenDate ok).
    # Pool gate: QMT ???????????????????~330????????????3?????
    # ???? FORCE ?????? 380 ?????????? CSV ?????????? requeue??
    if earliest is not None and earliest > effective + slack:
        if not _history_coverage_ok(
            earliest, end_d, list_date=list_date, bar_count=len(trade)
        ):
            if len(trade) < POOL_INTRADAY_MIN_BARS:
                return "hist_%d" % len(trade)
            if _force_backfill_requested() and len(trade) < MIN_BACKFILL_BARS:
                return "hist_%d" % len(trade)
    if last_d is None:
        return "no_date"
    lag_limit = end_d - timedelta(days=POOL_MAX_DATE_LAG_DAYS)
    if last_d < lag_limit:
        if xtdata is not None:
            ref = _last_tradeday_on_or_before(xtdata, end_d)
            if last_d < ref - timedelta(days=POOL_MAX_DATE_LAG_DAYS):
                return "lag_%s" % last_d.isoformat()
        else:
            return "lag_%s" % last_d.isoformat()
    if len(closes) >= 8:
        uc = len(set(round(c, 4) for c in closes))
        if uc < 8:
            return "uc_%d" % uc
    return None


def _pool_quality_reason(
    bars: List[Dict[str, Any]], end_d: date, xtdata=None
) -> Optional[str]:
    return _pool_storage_reason(bars, end_d, xtdata=xtdata)


def _pool_bars_quality_ok(bars: List[Dict[str, Any]], end_d: date, xtdata=None, code: str = "") -> bool:
    """???? CSV / ???????????????"""
    list_d = _csv_list_date(code) if code else None
    return _pool_storage_reason(bars, end_d, xtdata=xtdata, list_date=list_d) is None


def _count_valid_rows(rows_by_date: Dict[str, Dict[str, Any]]) -> int:
    return sum(1 for row in rows_by_date.values() if _is_valid_bar(row))


def _backfill_start_for(end_d: date, cache_dir: Optional[str] = None) -> date:
    """Floor date for fetch/completeness; never after end_d. FORCE may override."""
    return _resolve_backfill_start(end_d, cache_dir)


def _trim_rows_for_storage(
    rows_by_date: Dict[str, Dict[str, Any]], keep_bars: int = MIN_BACKFILL_BARS
) -> Dict[str, Dict[str, Any]]:
    """Pass-through: do not drop earlier bars (incremental keeps history).

    Upper bound is handled by _remove_bars_after_date elsewhere.
    keep_bars unused (compat).
    """
    del keep_bars
    return dict(rows_by_date or {})


def _recent_start_date(end_d: date, cache_dir: Optional[str] = None) -> date:
    """Cold/FORCE fetch window start (resolved floor; not rolling N days)."""
    return _backfill_start_for(end_d, cache_dir)


def _bars_meet_requirement(
    bars: List[Dict[str, Any]],
    full_backfill: bool,
    min_required: int,
    end_d: Optional[date] = None,
    list_date: Optional[date] = None,
) -> bool:
    valid = _sanitize_bars(bars)
    if not full_backfill:
        return len(valid) >= min_required
    if len(valid) < 1:
        return False
    if end_d is None:
        # Conservative: require soft min when caller did not pass end_d.
        return len(valid) >= max(min_required, min(MIN_BACKFILL_BARS, 200))
    earliest = _bars_earliest_date(valid)
    # Full backfill: first-date coverage only. Do NOT accept last-N count
    # (e.g. 380 bars from ~Apr 2025) as complete history.
    return _history_coverage_ok(
        earliest, end_d, list_date=list_date, bar_count=len(valid)
    )


def _filter_bars_by_range(
    bars: List[Dict[str, Any]], start_d: date, end_d: date
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for bar in bars:
        d_s = str(bar.get("date") or "")
        try:
            d = datetime.strptime(d_s, "%Y-%m-%d").date()
        except ValueError:
            continue
        if start_d <= d <= end_d:
            out.append(bar)
    return out


def _extract_stock_payload(data: Any, code: str) -> Optional[Any]:
    if data is None:
        return None
    if isinstance(data, dict):
        for key in (code, str(code).upper(), str(code).lower()):
            if key in data:
                return data[key]
        bare = str(code or "").split(".")[0]
        for key in (bare, bare.upper(), bare.lower()):
            if key and key in data:
                return data[key]
        return None
    return data


def _market_data_dict_to_bars(data: Any, code: str) -> List[Dict[str, Any]]:
    """???? get_market_data ?????????"""
    if not isinstance(data, dict):
        return []
    times = None
    for key in ("time", "Time"):
        val = data.get(key)
        if val is not None:
            try:
                if hasattr(val, "loc"):
                    times = val.loc[code]
                elif code in val:
                    times = val[code]
            except Exception:
                pass
            if times is None:
                times = val
            break
    if times is None:
        return []

    def _series(field: str):
        val = data.get(field)
        if val is None:
            return None
        try:
            if hasattr(val, "loc"):
                return val.loc[code]
            if isinstance(val, dict) and code in val:
                return val[code]
        except Exception:
            return None
        return val

    opens = _series("open")
    highs = _series("high")
    lows = _series("low")
    closes = _series("close")
    volumes = _series("volume")
    amounts = _series("amount")
    try:
        n = len(times)
    except TypeError:
        return []
    rows: List[Dict[str, Any]] = []
    for i in range(n):
        d = _parse_trade_date_from_ts(times[i])
        if d is None:
            continue
        rows.append(
            {
                "date": d.strftime("%Y-%m-%d"),
                "open": opens[i] if opens is not None else "",
                "high": highs[i] if highs is not None else "",
                "low": lows[i] if lows is not None else "",
                "close": closes[i] if closes is not None else "",
                "volume": volumes[i] if volumes is not None else "",
                "amount": amounts[i] if amounts is not None else "",
            }
        )
    return rows


def _load_list_date_map() -> Dict[str, date]:
    """Load listing dates from data/all_a_stocks.csv (and optional JSON)."""
    global _LIST_DATE_BY_CODE
    if _LIST_DATE_BY_CODE is not None:
        return _LIST_DATE_BY_CODE
    out: Dict[str, date] = {}
    base = PROJECT_ROOT.rstrip("\\/")
    csv_path = os.path.join(base, "data", "all_a_stocks.csv")
    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fields = list(reader.fieldnames or [])
            code_key = None
            date_key = None
            # Prefer exact Chinese headers; fall back to first/last columns.
            want_code = "\u8bc1\u5238\u4ee3\u7801"  # ????
            want_date = "\u4e0a\u5e02\u65e5\u671f"  # ????
            for name in fields:
                if name == want_code or "code" in str(name).lower():
                    code_key = name
                if name == want_date or "list" in str(name).lower() or "date" in str(name).lower():
                    date_key = name
            if code_key is None and fields:
                code_key = fields[0]
            if date_key is None and len(fields) >= 3:
                date_key = fields[2]
            elif date_key is None and fields:
                date_key = fields[-1]
            for row in reader:
                code6 = "".join(
                    ch for ch in str(row.get(code_key) or "") if ch.isdigit()
                )
                if len(code6) > 6:
                    code6 = code6[-6:]
                if len(code6) != 6:
                    continue
                d = _parse_expire_ymd(row.get(date_key))
                if d is not None:
                    out[code6] = d
    except Exception as e:
        print("[日线同步] 读取 csv 列表日期失败: %s" % e)
    # Optional JSON overlays (OpenDate / list_date / ????).
    for name in ("all_a_stock_info.json", "all_a_stock_info_em_boards.json"):
        jpath = os.path.join(base, "data", name)
        if not os.path.isfile(jpath):
            continue
        try:
            import json

            payload = json.load(open(jpath, "r", encoding="utf-8"))
        except Exception:
            continue
        items = []
        if isinstance(payload, dict):
            items = list(payload.values()) if payload else []
            # Also allow code -> date string map
            for k, v in payload.items():
                code6 = "".join(ch for ch in str(k) if ch.isdigit())
                if len(code6) > 6:
                    code6 = code6[-6:]
                if len(code6) != 6:
                    continue
                if isinstance(v, dict):
                    items.append(v)
                else:
                    d = _parse_expire_ymd(v)
                    if d is not None and code6 not in out:
                        out[code6] = d
        elif isinstance(payload, list):
            items = payload
        for row in items:
            if not isinstance(row, dict):
                continue
            code6 = "".join(
                ch
                for ch in str(
                    row.get("stock_code")
                    or row.get("????")
                    or row.get("code")
                    or ""
                )
                if ch.isdigit()
            )
            if len(code6) > 6:
                code6 = code6[-6:]
            if len(code6) != 6 or code6 in out:
                continue
            d = None
            for key in (
                "????",
                "OpenDate",
                "open_date",
                "list_date",
                "ListDate",
            ):
                d = _parse_expire_ymd(row.get(key))
                if d is not None:
                    break
            if d is not None:
                out[code6] = d
    _LIST_DATE_BY_CODE = out
    if _daily_sync_verbose():
        print("[日线同步] 从 CSV 收集上市日 n=%d" % len(out))
    return out


def _code6(code: str) -> str:
    digits = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(digits) > 6:
        digits = digits[-6:]
    return digits


def _csv_list_date(code: str) -> Optional[date]:
    m = _load_list_date_map()
    return m.get(_code6(code))


def _effective_backfill_start(end_d: date, list_date: Optional[date] = None) -> date:
    """max(BACKFILL_START, listing_date) ? IPO after floor starts at OpenDate."""
    floor = _backfill_start_for(end_d)
    if list_date is None:
        return floor
    if list_date > floor:
        return list_date
    return floor


def _instrument_list_date(ContextInfo, xtdata, code: str) -> Optional[date]:
    """IPO / list date: CSV first, then QMT instrument OpenDate."""
    d = _csv_list_date(code)
    if d is not None:
        return d
    det = _instrument_detail(ContextInfo, xtdata, code)
    if not det:
        return None
    for key in (
        "OpenDate",
        "open_date",
        "ListDate",
        "list_date",
        "CreateDate",
        "create_date",
    ):
        d = _parse_expire_ymd(det.get(key))
        if d is not None:
            return d
    return None


def _rows_earliest_date(rows_by_date: Dict[str, Dict[str, Any]]) -> Optional[date]:
    earliest = None
    for d_s, row in (rows_by_date or {}).items():
        if not _is_valid_bar(row):
            continue
        try:
            d = datetime.strptime(str(d_s)[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if earliest is None or d < earliest:
            earliest = d
    return earliest


def _bars_earliest_date(bars: List[Dict[str, Any]]) -> Optional[date]:
    earliest = None
    for bar in _sanitize_bars(bars) or []:
        try:
            d = datetime.strptime(str(bar.get("date") or "")[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if earliest is None or d < earliest:
            earliest = d
    return earliest


def _history_coverage_ok(
    earliest: Optional[date],
    end_d: date,
    list_date: Optional[date] = None,
    bar_count: int = 0,
) -> bool:
    """True when first bar is near effective start (BACKFILL_START or IPO OpenDate)."""
    if earliest is None:
        return False
    floor = _backfill_start_for(end_d)
    effective = _effective_backfill_start(end_d, list_date)
    slack = timedelta(days=BACKFILL_FIRST_DATE_SLACK_DAYS)
    # IPO / late list: accept first bar on/near listing (never require 2025-01-02).
    if earliest <= effective + slack:
        return True
    # Soft accept long history with mild holiday gap vs floor.
    if bar_count >= MIN_BACKFILL_BARS and earliest <= floor + timedelta(days=45):
        return True
    # Unknown listing: soft-accept only short IPO-like series (not truncated
    # ~123/300-bar local windows that start mid-year).
    if list_date is None and 1 <= bar_count < 120:
        if earliest > floor + slack and (end_d - earliest).days <= 400:
            if bar_count >= max(5, int((end_d - earliest).days * 0.3)):
                return True
    return False


def _needs_full_backfill(
    csv_path: str,
    cache_dir: Optional[str] = None,
    end_d: Optional[date] = None,
    list_date: Optional[date] = None,
) -> bool:
    """True when CSV needs year-window refresh vs effective start (IPO-aware).

    Without FORCE_YEAR_BACKFILL: never year-backfill (main chain = incremental).
    With FORCE: refresh missing/empty/truncated vs listing/floor.
    IPO after BACKFILL_START with first bar near OpenDate = OK (no forever retry).
    """
    # Main daily / pipeline catch-up: today incremental only; year dig is FORCE.
    if not _force_backfill_requested(cache_dir):
        return False
    rows = _read_csv_rows(csv_path)
    if not rows:
        return True
    valid_dates = sorted(d for d, row in rows.items() if _is_valid_bar(row))
    if not valid_dates:
        return True
    try:
        earliest = datetime.strptime(str(valid_dates[0])[:10], "%Y-%m-%d").date()
    except ValueError:
        return True
    end = end_d or date.today()
    if list_date is None:
        # Derive code from filename: 600000.SH.csv / 600000.csv
        base = os.path.basename(csv_path or "")
        code = base[:-4] if base.lower().endswith(".csv") else base
        list_date = _csv_list_date(code)
    effective = _effective_backfill_start(end, list_date)
    slack = timedelta(days=BACKFILL_FIRST_DATE_SLACK_DAYS)
    if earliest <= effective + slack:
        return False
    # Unknown listing + soft IPO-like coverage: stop treating as truncated.
    if list_date is None and _history_coverage_ok(
        earliest, end, list_date=None, bar_count=len(valid_dates)
    ):
        return False
    span_have = max(1, (end - earliest).days)
    span_want = max(1, (end - effective).days)
    # Classic truncated rolling cache (e.g. last ~120-250 bars only)
    if span_have < int(span_want * 0.55):
        return True
    if len(valid_dates) < min(MIN_BACKFILL_BARS, 200):
        # IPO with known list_date already passed earliest check; here listing
        # is early or unknown ? still short vs window.
        if list_date is not None and list_date > _backfill_start_for(end) + slack:
            return False
        return True
    # FORCE: refresh when first date is late vs effective (true truncate).
    return True


def _tick_field_float(row: Dict[str, Any], keys: Tuple[str, ...]) -> float:
    for key in keys:
        if key not in row:
            continue
        v = _to_float(row.get(key))
        if v > 0:
            return v
    return 0.0


def _tick_row_to_daily_bar(row: Any, day: date) -> Optional[Dict[str, Any]]:
    """??????????????????????????? UI ???????????????? xtdata download????"""
    if not isinstance(row, dict):
        return None
    close = _tick_field_float(
        row, ("lastPrice", "last_price", "price", "close", "last")
    )
    if close <= 0:
        return None
    open_px = _tick_field_float(
        row, ("open", "openPrice", "open_price", "todayOpen")
    )
    high_px = _tick_field_float(
        row, ("high", "highPrice", "todayHigh")
    )
    low_px = _tick_field_float(
        row, ("low", "lowPrice", "todayLow")
    )
    if open_px <= 0:
        open_px = close
    if high_px <= 0:
        high_px = max(open_px, close)
    if low_px <= 0:
        low_px = min(open_px, close)
    vol = _tick_field_float(row, ("volume", "vol", "pvolume"))
    amt = _tick_field_float(row, ("amount", "turnover"))
    # ??????????? 0???? OHLC ?????
    bar = {
        "date": day.strftime("%Y-%m-%d"),
        "open": open_px,
        "high": high_px,
        "low": low_px,
        "close": close,
        "volume": vol,
        "amount": amt,
    }
    if not _is_valid_bar(bar):
        return None
    return bar


def _lookup_tick_row(tick_map: Dict[str, Any], code: str) -> Optional[Dict[str, Any]]:
    if not isinstance(tick_map, dict):
        return None
    full = str(code or "").strip()
    bare = full.split(".")[0] if full else ""
    keys = (
        full,
        full.upper(),
        full.lower(),
        bare,
        bare.upper(),
        ("%s.SZ" % bare) if bare else "",
        ("%s.SH" % bare) if bare else "",
        ("%s.BJ" % bare) if bare else "",
        ("%s.sz" % bare.lower()) if bare else "",
        ("%s.sh" % bare.lower()) if bare else "",
        ("%s.bj" % bare.lower()) if bare else "",
    )
    for key in keys:
        if not key:
            continue
        row = tick_map.get(key)
        if isinstance(row, dict):
            return row
    # ??????????? list/tuple ???
    for key in keys:
        if not key:
            continue
        row = tick_map.get(key)
        if isinstance(row, (list, tuple)) and row and isinstance(row[0], dict):
            return row[0]
    return None


def _fetch_today_bars_from_full_tick(
    xtdata,
    codes: List[str],
    end_d: date,
    ContextInfo=None,
) -> Dict[str, Dict[str, Any]]:
    """???? get_full_tick ?? ????????????? ContextInfo???????? 58610 RPC????"""
    out: Dict[str, Dict[str, Any]] = {}
    if not codes:
        return out
    # Session: never call full_tick (blocks quotes); defer to after-hours.
    if _in_intraday_blocking_window():
        return out
    code_list = [str(c).strip().upper() for c in codes if str(c or "").strip()]
    if not code_list:
        return out
    tick_map = None
    source = ""
    if ContextInfo is not None and hasattr(ContextInfo, "get_full_tick"):
        try:
            tick_map = ContextInfo.get_full_tick(list(code_list))
            if isinstance(tick_map, dict) and tick_map:
                source = "ctx"
        except Exception as e:
            print("[日线同步] ContextInfo.get_full_tick 失败: %s" % e)
            tick_map = None
    # get_full_tick ?????????????? download_history???????? xtdata download ??????
    if (not isinstance(tick_map, dict) or not tick_map) and xtdata is not None:
        fn = getattr(xtdata, "get_full_tick", None)
        if callable(fn):
            try:
                tick_map = fn(list(code_list))
                if isinstance(tick_map, dict) and tick_map:
                    source = "xt"
            except Exception as e:
                if _is_quote_rpc_error(e):
                    # ????????????? ContextInfo ??????
                    if ENABLE_XTDATA_DOWNLOAD:
                        _mark_xtdata_rpc_dead(str(e))
                else:
                    print("[日线同步] xtdata.get_full_tick 失败: %s" % e)
                tick_map = None
    if not isinstance(tick_map, dict) or not tick_map:
        return out
    for code in code_list:
        row = _lookup_tick_row(tick_map, code)
        bar = _tick_row_to_daily_bar(row, end_d) if row else None
        if bar:
            out[code] = bar
    if out and source and _daily_sync_verbose():
        print(
            "[日线同步] full_tick K线 source=%s hit=%d/%d day=%s"
            % (source, len(out), len(code_list), end_d.isoformat())
        )
    return out


def _dataframe_like_to_bars(payload: Any, start_d: date, end_d: date) -> List[Dict[str, Any]]:
    bars = _sanitize_bars(_stock_data_to_bars(payload))
    return _filter_bars_by_range(bars, start_d, end_d)


def _ex_result_to_bars_by_code(
    data: Any,
    codes: List[str],
    start_d: date,
    end_d: date,
) -> Dict[str, List[Dict[str, Any]]]:
    """Parse get_market_data_ex / get_local_data result ?? {code: bars}."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    if data is None:
        return out
    if isinstance(data, dict):
        for code in codes:
            payload = None
            for key in (code, str(code).upper(), str(code).lower()):
                if key in data:
                    payload = data[key]
                    break
            if payload is None:
                # field-dict layout from legacy get_market_data
                bars = _sanitize_bars(
                    _filter_bars_by_range(
                        _market_data_dict_to_bars(data, code), start_d, end_d
                    )
                )
                if bars:
                    out[code] = bars
                continue
            bars = _dataframe_like_to_bars(payload, start_d, end_d)
            if bars:
                out[code] = bars
        return out
    # single-code frame
    if len(codes) == 1:
        bars = _dataframe_like_to_bars(data, start_d, end_d)
        if bars:
            out[codes[0]] = bars
    return out


def _ctx_call_get_market_data_ex(
    ContextInfo,
    codes: List[str],
    start_time: str,
    end_time: str,
    count: int,
    *,
    subscribe: bool,
    dividend_type: str = "none",
) -> Optional[Any]:
    """Call ContextInfo.get_market_data_ex with ??QMT signature variants."""
    if ContextInfo is None:
        return None
    fn = getattr(ContextInfo, "get_market_data_ex", None)
    if not callable(fn):
        return None
    ohlcv = ["open", "high", "low", "close", "volume", "amount"]
    code_list = list(codes)
    su = getattr(ContextInfo, "set_universe", None)
    if callable(su):
        try:
            su(code_list)
        except Exception:
            pass
    div = str(dividend_type or "none")
    attempts = (
        {
            "fields": ohlcv,
            "stock_code": code_list,
            "period": "1d",
            "start_time": start_time,
            "end_time": end_time,
            "count": count,
            "dividend_type": div,
            "fill_data": False,
            "subscribe": subscribe,
        },
        {
            "field_list": ohlcv,
            "stock_list": code_list,
            "period": "1d",
            "start_time": start_time,
            "end_time": end_time,
            "count": count,
            "dividend_type": div,
            "fill_data": False,
            "subscribe": subscribe,
        },
        {
            "fields": [],
            "stock_code": code_list,
            "period": "1d",
            "start_time": start_time,
            "end_time": end_time,
            "count": count,
            "dividend_type": div,
            "fill_data": False,
            "subscribe": subscribe,
        },
    )
    for kwargs in attempts:
        try:
            return fn(**kwargs)
        except TypeError:
            try:
                return fn(
                    ohlcv,
                    code_list,
                    "1d",
                    start_time,
                    end_time,
                    count,
                    div,
                    False,
                    subscribe,
                )
            except Exception:
                continue
        except Exception:
            continue
    return None


def _take_longer_bars(
    cur: Optional[List[Dict[str, Any]]],
    nxt: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    a = _sanitize_bars(cur or [])
    b = _sanitize_bars(nxt or [])
    if len(b) > len(a):
        return b
    if len(b) == len(a) and b:
        ea = _bars_earliest_date(a)
        eb = _bars_earliest_date(b)
        if ea is not None and eb is not None and eb < ea:
            return b
    return a


def _merge_bar_lists(
    cur: Optional[List[Dict[str, Any]]],
    nxt: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Union by date; prefer earlier coverage over last-N length alone."""
    by_d = {}  # type: Dict[str, Dict[str, Any]]
    for bars in (cur or [], nxt or []):
        for bar in _sanitize_bars(bars):
            d = str(bar.get("date") or "")[:10]
            if d:
                by_d[d] = bar
    return [by_d[k] for k in sorted(by_d.keys())]


def _batch_fetch_1d_bars(
    ContextInfo,
    xtdata,
    codes: List[str],
    start_d: date,
    end_d: date,
    *,
    prefer_count: int = -1,
    quality_min: int = 0,
    dividend_type: str = "none",
) -> Tuple[Dict[str, List[Dict[str, Any]]], str]:
    """
    Batch 1d OHLCV via ContextInfo.get_market_data_ex(subscribe=False).

    FORCE/cold always uses explicit start_time/end_time (BACKFILL_START..end)
    first. Count-mode (empty start/end + count=N) only as fallback ? it returns
    last N local bars and often truncates first date to ~Apr (N~320) or ~Jan23
    (N~123). After broker download, callers must re-get with the same range.
    """
    out: Dict[str, List[Dict[str, Any]]] = {}
    del xtdata  # no xtdata get_market_data_ex fallback here
    if not codes:
        return out, "empty"
    code_list = [str(c).strip().upper() for c in codes if str(c or "").strip()]
    if not code_list:
        return out, "empty"
    s_short, e_short = _short_range_strings(start_d, end_d)
    count_hint = int(prefer_count) if prefer_count and prefer_count > 0 else -1
    source = "empty"
    need_n = int(quality_min) if int(quality_min or 0) > 0 else (
        MIN_BACKFILL_BARS if count_hint > 0 else 1
    )
    slack = timedelta(days=BACKFILL_FIRST_DATE_SLACK_DAYS)

    if ContextInfo is None:
        return out, source

    def _is_short(code: str) -> bool:
        bars = out.get(code) or []
        if len(bars) < 1:
            return True
        if need_n <= 1:
            return False
        earliest = _bars_earliest_date(bars)
        if earliest is None:
            return True
        # Late vs requested fetch start => still short (count/local window).
        if earliest > start_d + slack:
            return True
        return len(bars) < need_n

    def _short_codes() -> List[str]:
        return [c for c in code_list if _is_short(c)]

    # Primary: always date-range (start_time/end_time from BACKFILL_START..end).
    data = _ctx_call_get_market_data_ex(
        ContextInfo,
        code_list,
        s_short,
        e_short,
        -1,
        subscribe=False,
        dividend_type=dividend_type,
    )
    parsed = _ex_result_to_bars_by_code(data, code_list, start_d, end_d)
    if parsed:
        for code, bars in parsed.items():
            if bars:
                out[code] = bars
        source = "ctx_ex_range"

    # Count-mode fallback only for still-short (last-N local window).
    short = _short_codes()
    if short and count_hint > 0:
        data = _ctx_call_get_market_data_ex(
            ContextInfo,
            short,
            "",
            "",
            count_hint,
            subscribe=False,
            dividend_type=dividend_type,
        )
        parsed = _ex_result_to_bars_by_code(data, short, start_d, end_d)
        improved = False
        if parsed:
            for code, bars in parsed.items():
                merged = _merge_bar_lists(out.get(code), bars)
                if merged and len(merged) > len(out.get(code) or []):
                    out[code] = merged
                    improved = True
        if improved:
            source = (
                "ctx_ex_count" if source == "empty" else source + "+count"
            )

    # get_local_data for still-short (not only fully missing).
    short = _short_codes()
    if short:
        data = _call_market_api(
            ContextInfo,
            "get_local_data",
            [],
            short,
            "1d",
            s_short,
            e_short,
            -1,
            allow_subscribe=False,
        )
        parsed = _ex_result_to_bars_by_code(data, short, start_d, end_d)
        improved = False
        if parsed:
            for code, bars in parsed.items():
                merged = _merge_bar_lists(out.get(code), bars)
                if merged and (
                    code not in out or len(merged) > len(out.get(code) or [])
                ):
                    out[code] = merged
                    improved = True
        if improved:
            source = (
                "ctx_local_data" if source == "empty" else source + "+local_data"
            )

    # Per-code dig: single-code date-range first; count only if still short.
    short = _short_codes()
    if short:
        max_each = (
            CTX_SHORT_DIG_EACH_INTRADAY
            if _in_intraday_blocking_window()
            else CTX_SHORT_DIG_EACH_AFTER_HOURS
        )
        dug = 0
        for code in short:
            if dug >= max_each:
                break
            dug += 1
            best = list(out.get(code) or [])
            attempts = [(s_short, e_short, -1, "each_range")]
            if count_hint > 0 or need_n > 1:
                attempts.append(
                    ("", "", max(count_hint, CTX_BACKFILL_COUNT), "each_count")
                )
            for st2, et2, cnt2, tag2 in attempts:
                data = _ctx_call_get_market_data_ex(
                    ContextInfo,
                    [code],
                    st2,
                    et2,
                    cnt2,
                    subscribe=False,
                    dividend_type=dividend_type,
                )
                parsed = _ex_result_to_bars_by_code(
                    data, [code], start_d, end_d
                )
                cand = (parsed or {}).get(code) or []
                merged = _merge_bar_lists(best, cand)
                if len(merged) > len(best) or (
                    _bars_earliest_date(merged)
                    and _bars_earliest_date(best)
                    and _bars_earliest_date(merged) < _bars_earliest_date(best)
                ):
                    best = merged
                    source = (
                        tag2 if source == "empty" else source + "+" + tag2
                    )
                earliest = _bars_earliest_date(best)
                if (
                    earliest is not None
                    and earliest <= start_d + slack
                    and len(best) >= max(1, need_n)
                ):
                    break
                # After range, only try count if still short.
                if tag2 == "each_range" and earliest is not None and (
                    earliest <= start_d + slack and len(best) >= need_n
                ):
                    break
            if best:
                out[code] = best

    return out, source


def _download_slice(
    xtdata, code: str, s_short: str, e_short: str, incrementally: bool = True
) -> bool:
    del incrementally  # QMT xtdata ????????????
    if not ENABLE_XTDATA_DOWNLOAD or not _xtdata_rpc_alive():
        return False
    attempts = []
    attempts.append(lambda: xtdata.download_history_data(code, "1d", s_short, e_short))
    dl2 = getattr(xtdata, "download_history_data2", None)
    if callable(dl2):
        for call in (
            lambda: dl2([code], "1d", s_short, e_short),
            lambda: dl2([code], "1d", s_short, e_short, None, None, "none"),
        ):
            attempts.append(call)
    last_err = ""
    for call in attempts:
        try:
            call()
            return True
        except TypeError:
            continue
        except Exception as e:
            last_err = "%s: %s" % (type(e).__name__, e)
            if _is_quote_rpc_error(e):
                _mark_xtdata_rpc_dead(str(e))
                break
            continue
    if last_err:
        global _FAIL_LOG_COUNT
        if _FAIL_LOG_COUNT < 8:
            _FAIL_LOG_COUNT += 1
            print(
                "[日线同步] 下载错误 %s %s-%s | %s"
                % (code, s_short, e_short, last_err)
            )
    return False


def _download_batch(
    xtdata, codes: List[str], start_d: date, end_d: date
) -> str:
    """????????????????????? download ???????????"""
    if not codes:
        return "batch_empty"
    if not ENABLE_XTDATA_DOWNLOAD or not _xtdata_rpc_alive():
        return "batch_skip_disabled"
    s_short, e_short = _short_range_strings(start_d, end_d)
    dl2 = getattr(xtdata, "download_history_data2", None)
    if callable(dl2):
        for label, call in (
            (
                "batch_dl2",
                lambda: dl2(list(codes), "1d", s_short, e_short),
            ),
            (
                "batch_dl2b",
                lambda: dl2(list(codes), "1d", s_short, e_short, None, None, "none"),
            ),
        ):
            try:
                call()
                return label
            except TypeError:
                continue
            except Exception as e:
                print(
                    "[日线同步] %s 失败 n=%d %s-%s | %s: %s"
                    % (label, len(codes), s_short, e_short, type(e).__name__, e)
                )
                if _is_quote_rpc_error(e):
                    _mark_xtdata_rpc_dead(str(e))
                    return "batch_rpc_dead"
    if not _xtdata_rpc_alive():
        return "batch_rpc_dead"
    ok = 0
    for code in codes:
        try:
            xtdata.download_history_data(code, "1d", s_short, e_short)
            ok += 1
        except Exception as e:
            if _is_quote_rpc_error(e):
                _mark_xtdata_rpc_dead(str(e))
                return "batch_rpc_dead"
            try:
                xtdata.download_history_data(code, "1d")
                ok += 1
            except Exception as e2:
                if _is_quote_rpc_error(e2):
                    _mark_xtdata_rpc_dead(str(e2))
                    return "batch_rpc_dead"
    return "batch_each_ok_%d/%d" % (ok, len(codes))


def _ctx_set_universe(ContextInfo, code: str) -> None:
    if ContextInfo is None:
        return
    su = getattr(ContextInfo, "set_universe", None)
    if callable(su):
        try:
            su([code])
        except Exception:
            pass


def _fetch_bars_local_raw(
    xtdata,
    code: str,
    start_d: date,
    end_d: date,
    ContextInfo=None,
) -> List[Dict[str, Any]]:
    """???? ContextInfo get_market_data_ex / get_local_data???? xtdata ?????"""
    batch_map, _src = _batch_fetch_1d_bars(
        ContextInfo, xtdata, [code], start_d, end_d
    )
    best = batch_map.get(code) or []
    if best:
        return best
    s_short, e_short = _short_range_strings(start_d, end_d)
    start_s, end_s = _range_strings(start_d, end_d)
    owners: List[Any] = []
    if ContextInfo is not None:
        owners.append(ContextInfo)
    owners.append(xtdata)
    for owner in owners:
        _ctx_set_universe(ContextInfo, code)
        for st, et in ((s_short, e_short), (start_s, end_s), ("", "")):
            data = _call_market_api(
                owner, "get_local_data", [], [code], "1d", st, et, -1
            )
            if isinstance(data, dict):
                payload = _extract_stock_payload(data, code)
            else:
                payload = data
            if payload is None:
                continue
            bars = _stock_data_to_bars(payload)
            bars = _filter_bars_by_range(bars, start_d, end_d)
            if len(bars) > len(best):
                best = bars
    return best


def _download_pool_bars(
    xtdata,
    code: str,
    start_d: date,
    end_d: date,
    ContextInfo=None,
    *,
    sleep_sec: float = POOL_POST_DOWNLOAD_SLEEP_SEC,
) -> List[Dict[str, Any]]:
    # ???? ContextInfo / ???? get?????????????? download
    batch_map, _src = _batch_fetch_1d_bars(
        ContextInfo, xtdata, [code], start_d, end_d
    )
    raw = batch_map.get(code) or []
    trade = _pool_trade_bars(_filter_pool_bars(raw, end_d))
    last_d = _pool_last_bar_date(trade)
    need_download = len(trade) < POOL_INTRADAY_MIN_BARS or (
        last_d is not None and last_d < end_d
    )
    if need_download and _xtdata_rpc_alive():
        s_short, e_short = _short_range_strings(start_d, end_d)
        _download_slice(xtdata, code, s_short, e_short)
        time.sleep(sleep_sec)
        raw2 = _fetch_bars_local_raw(
            xtdata, code, start_d, end_d, ContextInfo=ContextInfo
        )
        if len(raw2) > len(raw):
            raw = raw2
    return _pool_trade_bars(_filter_pool_bars(raw, end_d))


def _download_daily_bars(
    xtdata,
    code: str,
    start_d: date,
    end_d: date,
    ContextInfo=None,
    *,
    sleep_sec: float = FULL_POST_DOWNLOAD_SLEEP_SEC,
) -> List[Dict[str, Any]]:
    # ??? ContextInfo / ????????? download
    batch_map, _src = _batch_fetch_1d_bars(
        ContextInfo, xtdata, [code], start_d, end_d
    )
    bars = _sanitize_bars(batch_map.get(code) or [])
    if len(bars) >= MIN_STORAGE_BARS:
        return bars
    s_short, e_short = _short_range_strings(start_d, end_d)
    if _xtdata_rpc_alive():
        _download_slice(xtdata, code, s_short, e_short)
        if sleep_sec > 0:
            time.sleep(sleep_sec)
    return _sanitize_bars(
        _fetch_bars_local_raw(xtdata, code, start_d, end_d, ContextInfo)
    )


def _merge_pool_bar_list(merged: Dict[str, Dict[str, Any]], bars: List[Dict[str, Any]]) -> None:
    for bar in bars or []:
        d = str(bar.get("date") or "")
        if d:
            merged[d] = bar


def _pool_bars_sorted(merged: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [merged[k] for k in sorted(merged.keys()) if k]


def _fetch_pool_bars_optimized(
    xtdata,
    code: str,
    end_d: date,
    ContextInfo=None,
) -> List[Dict[str, Any]]:
    """??????????????? BACKFILL_START..end?????/???????????????????>=120?????????????"""
    merged: Dict[str, Dict[str, Any]] = {}
    recent_start = _recent_start_date(end_d)
    list_date = _csv_list_date(code) if code else None

    def _quality_ok(bars_now: List[Dict[str, Any]]) -> bool:
        return _pool_bars_quality_ok(
            bars_now, end_d, xtdata=xtdata, code=code
        )

    _merge_pool_bar_list(
        merged,
        _download_pool_bars(
            xtdata, code, recent_start, end_d, ContextInfo=ContextInfo
        ),
    )
    bars = _pool_bars_sorted(merged)
    if _quality_ok(bars):
        return bars

    # ??????????????? hist/lag??????????????????????????????
    last_d = _pool_last_bar_date(bars)
    if last_d is not None and last_d < end_d:
        tail_start = last_d + timedelta(days=1)
        _merge_pool_bar_list(
            merged,
            _download_pool_bars(
                xtdata,
                code,
                tail_start,
                end_d,
                ContextInfo=ContextInfo,
                sleep_sec=POOL_TAIL_DOWNLOAD_SLEEP_SEC,
            ),
        )
        bars = _pool_bars_sorted(merged)
        if _quality_ok(bars):
            return bars

    # ????????????????? 270 ??????????????????????? >=120 ?????????
    cur = recent_start
    if list_date is not None and list_date > cur:
        cur = list_date
    for _slice_i in range(max(POOL_COLD_SLICE_MAX, 8)):
        if _quality_ok(bars):
            return bars
        slice_end = min(cur + timedelta(days=POOL_COLD_SLICE_DAYS), end_d)
        _merge_pool_bar_list(
            merged,
            _download_pool_bars(
                xtdata,
                code,
                cur,
                slice_end,
                ContextInfo=ContextInfo,
                sleep_sec=POOL_POST_DOWNLOAD_SLEEP_SEC,
            ),
        )
        bars = _pool_bars_sorted(merged)
        cur = slice_end + timedelta(days=1)
        if cur > end_d:
            break

    if _quality_ok(bars):
        return bars

    # ???????????? dig??ContextInfo + download_history??
    dig_bars, _src = _backfill_by_slices(
        xtdata,
        code,
        recent_start,
        end_d,
        ContextInfo=ContextInfo,
        fast=True,
    )
    if dig_bars:
        _merge_pool_bar_list(merged, dig_bars)
        bars = _pool_bars_sorted(merged)
    return bars


def _pool_bars_usable_for_trading(
    bars: List[Dict[str, Any]], end_d: date, xtdata=None
) -> bool:
    """???????? BACKFILL ??????????????????K???????????? write_end?????????"""
    trade = _pool_trade_bars(_filter_pool_bars(bars, end_d))
    if len(trade) < POOL_INTRADAY_MIN_BARS:
        return False
    last_d = _pool_last_bar_date(trade)
    if last_d is None:
        return False
    # ??????????????????????? 10 ????????? 7/31 ???? ok ???? 8/3??
    if last_d < end_d:
        return False
    return True


def _csv_last_trade_date(csv_path: str) -> Optional[date]:
    rows = _read_csv_rows(csv_path)
    if not rows:
        return None
    last_d = None
    for d_s, row in rows.items():
        if not _is_valid_bar(row):
            continue
        try:
            d = datetime.strptime(str(d_s)[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if last_d is None or d > last_d:
            last_d = d
    return last_d


def _merge_rows_keep_newer(
    csv_path: str, new_rows: Dict[str, Dict[str, Any]], write_end_d: date
) -> Dict[str, Dict[str, Any]]:
    """??????? CSV?????????????????????????????????????????"""
    existing = _read_csv_rows(csv_path)
    merged: Dict[str, Dict[str, Any]] = dict(existing)
    for d_s, row in (new_rows or {}).items():
        if not d_s:
            continue
        merged[str(d_s)[:10]] = row
    _remove_bars_after_date(merged, write_end_d)
    return _trim_rows_for_storage(merged)


def _backfill_by_slices(
    xtdata,
    code: str,
    start_d: date,
    end_d: date,
    ContextInfo=None,
    fast: bool = False,
) -> Tuple[List[Dict[str, Any]], str]:
    """Dig 1d history via ContextInfo; broker download_history_data then re-get."""
    fetch_start = start_d if start_d else _recent_start_date(end_d)
    list_date = _csv_list_date(code)
    # 1) Date-range primary (BACKFILL_START/list_date..end); count only fallback.
    batch_map, src = _batch_fetch_1d_bars(
        ContextInfo,
        xtdata,
        [code],
        fetch_start,
        end_d,
        prefer_count=CTX_BACKFILL_COUNT,
        quality_min=MIN_BACKFILL_BARS,
    )
    bars = _sanitize_bars(batch_map.get(code) or [])
    if _bars_meet_requirement(
        bars, True, MIN_BACKFILL_BARS, end_d=end_d, list_date=list_date
    ):
        return bars, "%s_%d" % (src or "ctx", len(bars))
    # 2) Explicit date-range-only dig (no count hint).
    batch_map2, src2 = _batch_fetch_1d_bars(
        ContextInfo,
        xtdata,
        [code],
        fetch_start,
        end_d,
        prefer_count=-1,
        quality_min=MIN_BACKFILL_BARS,
    )
    bars2 = _sanitize_bars(batch_map2.get(code) or [])
    bars = _merge_bar_lists(bars, bars2)
    if bars2 and src2:
        src = src2 or "ctx_ex_range"
    if _bars_meet_requirement(
        bars, True, MIN_BACKFILL_BARS, end_d=end_d, list_date=list_date
    ):
        return bars, "%s_%d" % (src or "ctx", len(bars))
    # fast: skip blocking broker download (quotes first; FORCE batch path downloads).
    if fast:
        return bars, ("local_partial_%d" % len(bars)) if bars else "local_miss_fast"
    # 3) Host-QMT builtin download_history_data(code,"1d",start,end) then re-get
    #    with the SAME date range (not count).
    n_ok, dl_detail = _download_1d_via_builtin([code], fetch_start, end_d)
    if n_ok > 0:
        if FULL_POST_DOWNLOAD_SLEEP_SEC > 0:
            time.sleep(float(FULL_POST_DOWNLOAD_SLEEP_SEC))
        batch_map3, src3 = _batch_fetch_1d_bars(
            ContextInfo,
            xtdata,
            [code],
            fetch_start,
            end_d,
            prefer_count=-1,
            quality_min=MIN_BACKFILL_BARS,
        )
        bars3 = _sanitize_bars(batch_map3.get(code) or [])
        merged = _merge_bar_lists(bars, bars3)
        if len(merged) > len(bars) or (
            _bars_earliest_date(merged)
            and _bars_earliest_date(bars)
            and _bars_earliest_date(merged) < _bars_earliest_date(bars)
        ):
            bars = merged
            src = "builtin_dl+%s" % (src3 or "ctx")
        else:
            bars = merged or bars
            src = "%s+builtin_dl_%s" % (src or "ctx", dl_detail)
    elif dl_detail:
        src = "%s+builtin_dl_miss_%s" % (src or "ctx", dl_detail)
    if _bars_meet_requirement(
        bars, True, MIN_BACKFILL_BARS, end_d=end_d, list_date=list_date
    ):
        return bars, "%s_%d" % (src or "ctx", len(bars))
    # 4) Optional xtdata download (off by default; miniQMT path).
    if _xtdata_rpc_alive():
        dl_bars = _download_daily_bars(
            xtdata,
            code,
            fetch_start,
            end_d,
            ContextInfo=ContextInfo,
        )
        if len(dl_bars) > len(bars):
            bars = dl_bars
            src = "download_%d" % len(bars)
    start_s, end_s = _range_strings(fetch_start, end_d)
    fallback = _sanitize_bars(
        _fetch_valid_bars(
            xtdata,
            code,
            start_s,
            end_s,
            ContextInfo=ContextInfo,
            prefer_range=True,
            min_bars=0,
            fast=False,
        )
    )
    bars = _merge_bar_lists(bars, fallback)
    if fallback and len(fallback) >= len(bars):
        src = "mixed_%d" % len(bars)
    if not bars:
        return [], "empty_after_%s" % (src or "mixed")
    return bars, "%s_%d" % (src or "mixed", len(bars))


def _download_one_code(
    xtdata, code: str, start_s: str, end_s: str, force_full: bool = False
) -> str:
    """?????????????????? range ????????????????????????????"""
    del force_full
    if not ENABLE_XTDATA_DOWNLOAD or not _xtdata_rpc_alive():
        return "disabled"
    start_d = datetime.strptime(start_s[:8], "%Y%m%d").date()
    end_d = datetime.strptime(end_s[:8], "%Y%m%d").date()
    s_short, e_short = _short_range_strings(start_d, end_d)
    if _download_slice(xtdata, code, s_short, e_short):
        return "range_short"
    # ??????????????????????????? 30 ???????????
    wide_start = end_d - timedelta(days=30)
    if wide_start < start_d:
        s_wide, e_wide = _short_range_strings(wide_start, end_d)
        if _download_slice(xtdata, code, s_wide, e_wide):
            return "range_wide30"
    if not _xtdata_rpc_alive():
        return "rpc_dead"
    try:
        xtdata.download_history_data(code, "1d")
        return "nodate_ok"
    except Exception as e:
        if _is_quote_rpc_error(e):
            _mark_xtdata_rpc_dead(str(e))
            return "rpc_dead"
        print(
            "[日线同步] 无日期下载失败 %s | %s: %s"
            % (code, type(e).__name__, e)
        )
        return "none"


def _widen_fetch_start(start_d: date, end_d: date, min_span_days: int = 15) -> date:
    """????????????????????/?????????????? min_span_days????????????"""
    if (end_d - start_d).days >= min_span_days:
        return start_d
    return end_d - timedelta(days=min_span_days)


def _call_market_api(
    owner: Any,
    fn_name: str,
    fields: List[str],
    stocks: List[str],
    period: str,
    start_time: str,
    end_time: str,
    count: int,
    allow_subscribe: bool = True,
) -> Optional[Any]:
    fn = getattr(owner, fn_name, None)
    if not callable(fn):
        return None
    ohlcv = fields or ["open", "high", "low", "close", "volume", "amount"]
    if fn_name == "get_market_data_ex":
        # subscribe=False ?????True ????????? ~1?C2min??
        # ?? subscribe ????????????? allow_subscribe=False??QMT ????? True????
        kw_attempts = (
            {
                "fields": ohlcv,
                "stock_code": stocks,
                "period": period,
                "start_time": start_time,
                "end_time": end_time,
                "count": count,
                "dividend_type": "none",
                "fill_data": False,
                "subscribe": False,
            },
            {
                "field_list": ohlcv,
                "stock_list": stocks,
                "period": period,
                "start_time": start_time,
                "end_time": end_time,
                "count": count,
                "dividend_type": "none",
                "fill_data": False,
                "subscribe": False,
            },
            {
                "fields": ohlcv,
                "stock_code": stocks,
                "period": period,
                "start_time": start_time,
                "end_time": end_time,
                "count": count,
                "dividend_type": "none",
                "fill_data": False,
                "subscribe": True,
            },
            {
                "field_list": ohlcv,
                "stock_list": stocks,
                "period": period,
                "start_time": start_time,
                "end_time": end_time,
                "count": count,
                "dividend_type": "none",
                "fill_data": False,
                "subscribe": True,
            },
        )
        if not allow_subscribe:
            kw_attempts = tuple(
                kw for kw in kw_attempts if kw.get("subscribe") is False
            )
    else:
        kw_attempts = (
            {
                "field_list": ohlcv,
                "stock_list": stocks,
                "period": period,
                "start_time": start_time,
                "end_time": end_time,
                "count": count,
                "dividend_type": "none",
                "fill_data": False,
            },
            {
                "field_list": ohlcv,
                "stock_list": stocks,
                "period": period,
                "start_time": start_time,
                "end_time": end_time,
                "count": count,
            },
        )
    for kwargs in kw_attempts:
        try:
            return fn(**kwargs)
        except TypeError:
            try:
                return fn(ohlcv, stocks, period, start_time, end_time, count)
            except Exception:
                continue
        except Exception:
            continue
    return None


def _fetch_raw_market_data(
    xtdata,
    code: str,
    start_s: str,
    end_s: str,
    ContextInfo=None,
    prefer_range: bool = False,
    allow_subscribe: bool = True,
) -> Tuple[Optional[Any], str, str]:
    """???? (payload, label, fmt)??fmt=frame|field_dict|bars??"""
    owners = []
    if ContextInfo is not None:
        owners.append(("ctx", ContextInfo))
    # xtdata ?? download/RPC ????????????/????????? ~1?C2min???????????
    if xtdata is not None and _xtdata_rpc_alive():
        owners.append(("xt", xtdata))
    if not owners:
        return None, "empty", "frame"

    # fail-fast??allow_subscribe=False??????? get_market_data_ex / get_market_data??
    # ??? subscribe=False ???? ~30?C40s????? get_local_data?????????????
    if not allow_subscribe:
        attempts = (
            (
                "local_short",
                "get_local_data",
                [],
                [code],
                "1d",
                start_s[:8],
                end_s[:8],
                -1,
                "frame",
            ),
        )
    else:
        range_attempts = (
            (
                "ex_short",
                "get_market_data_ex",
                [],
                [code],
                "1d",
                start_s[:8],
                end_s[:8],
                -1,
                "frame",
            ),
            (
                "ex_long",
                "get_market_data_ex",
                [],
                [code],
                "1d",
                start_s,
                end_s,
                -1,
                "frame",
            ),
        )
        legacy_attempts = (
            (
                "md_short",
                "get_market_data",
                [],
                [code],
                "1d",
                start_s[:8],
                end_s[:8],
                -1,
                "field_dict",
            ),
            (
                "md_count",
                "get_market_data",
                [],
                [code],
                "1d",
                "",
                "",
                -1,
                "field_dict",
            ),
        )
        local_attempts = (
            (
                "local_short",
                "get_local_data",
                [],
                [code],
                "1d",
                start_s[:8],
                end_s[:8],
                -1,
                "frame",
            ),
            (
                "local_long",
                "get_local_data",
                [],
                [code],
                "1d",
                start_s,
                end_s,
                -1,
                "frame",
            ),
            (
                "local_count_only",
                "get_local_data",
                [],
                [code],
                "1d",
                "",
                "",
                -1,
                "frame",
            ),
        )
        count_attempts = (
            (
                "ex_count_only",
                "get_market_data_ex",
                [],
                [code],
                "1d",
                "",
                "",
                -1,
                "frame",
            ),
            (
                "ex_sub_count",
                "get_market_data_ex",
                [],
                [code],
                "1d",
                "",
                "",
                CTX_BACKFILL_COUNT,
                "frame",
            ),
        )
        if prefer_range:
            attempts = (
                range_attempts + count_attempts + legacy_attempts + local_attempts
            )
        else:
            attempts = (
                count_attempts + range_attempts + legacy_attempts + local_attempts
            )

    for label, fn_name, fields, stocks, period, st, et, count, fmt in attempts:
        for owner_label, owner in owners:
            if owner_label == "ctx":
                su = getattr(owner, "set_universe", None)
                if callable(su):
                    try:
                        su(list(stocks))
                    except Exception:
                        pass
            data = _call_market_api(
                owner,
                fn_name,
                fields,
                stocks,
                period,
                st,
                et,
                count,
                allow_subscribe=allow_subscribe,
            )
            if fmt == "field_dict":
                bars = _market_data_dict_to_bars(data, code)
                if bars:
                    return bars, "%s_%s" % (owner_label, label), "bars"
                continue
            payload = _extract_stock_payload(data, code)
            if payload is None:
                continue
            try:
                n = len(payload)
            except TypeError:
                n = 1
            if n <= 0:
                continue
            return payload, "%s_%s" % (owner_label, label), "frame"
    return None, "empty", "frame"


def _bars_for_code(
    xtdata,
    code: str,
    start_s: str,
    end_s: str,
    ContextInfo=None,
    prefer_range: bool = False,
    min_bars: int = 1,
    fast: bool = False,
) -> List[Dict[str, Any]]:
    start_d = datetime.strptime(start_s[:8], "%Y%m%d").date()
    end_d = datetime.strptime(end_s[:8], "%Y%m%d").date()
    last_reason = "empty"
    retries = READ_RETRY_COUNT_FAST if fast else READ_RETRY_COUNT
    for attempt in range(retries):
        payload, label, fmt = _fetch_raw_market_data(
            xtdata,
            code,
            start_s,
            end_s,
            ContextInfo=ContextInfo,
            prefer_range=prefer_range or attempt > 0,
            allow_subscribe=not fast,
        )
        last_reason = label
        if payload is None:
            if attempt + 1 < retries:
                time.sleep(DOWNLOAD_SLEEP_SEC + 0.2 * attempt)
            continue
        if fmt == "bars":
            bars = payload
        else:
            bars = _stock_data_to_bars(payload)
        bars = _sanitize_bars(_filter_bars_by_range(bars, start_d, end_d))
        if len(bars) >= min_bars:
            return bars
        if attempt + 1 < retries:
            time.sleep(DOWNLOAD_SLEEP_SEC + 0.2 * attempt)
    _log_fetch_fail(code, start_s, end_s, last_reason)
    return []


def _fetch_valid_bars(
    xtdata,
    code: str,
    start_s: str,
    end_s: str,
    ContextInfo=None,
    prefer_range: bool = True,
    min_bars: int = 0,
    fast: bool = False,
) -> List[Dict[str, Any]]:
    raw = _bars_for_code(
        xtdata,
        code,
        start_s,
        end_s,
        ContextInfo=ContextInfo,
        prefer_range=prefer_range,
        min_bars=min_bars,
        fast=fast,
    )
    return _sanitize_bars(raw)


def _csv_ready_at_end(csv_path: str, end_d: date) -> bool:
    """CSV ?????????? K ??????? end_d??failed_recovery ?????????????????"""
    rows = _read_csv_rows(csv_path)
    valid_n = _count_valid_rows(rows)
    if valid_n < MIN_STORAGE_BARS or not rows:
        return False
    try:
        last = datetime.strptime(max(rows.keys()), "%Y-%m-%d").date()
    except ValueError:
        return False
    if last != end_d:
        return False
    last_row = rows.get(max(rows.keys()))
    return bool(last_row and _is_valid_bar(last_row))


def _sync_one_code(
    xtdata,
    cache_dir: str,
    code: str,
    end_d: date,
    ContextInfo=None,
    sync_source: str = "timer",
    tick_bar: Optional[Dict[str, Any]] = None,
    pref_bars: Optional[List[Dict[str, Any]]] = None,
    batch_tried: bool = False,
) -> Tuple[str, Optional[str], int]:
    """???? (status, fail_reason, valid_row_count)??"""
    end_d = _pool_daily_write_end_date(xtdata, end_d)
    csv_path = os.path.join(cache_dir, code + ".csv")
    full_backfill = _needs_full_backfill(csv_path, cache_dir, end_d=end_d)
    # failed_recovery: skip only when already at end *and* history window is satisfied
    if (
        sync_source == "failed_recovery"
        and (not full_backfill)
        and _csv_ready_at_end(csv_path, end_d)
    ):
        valid_existing = _count_valid_rows(_read_csv_rows(csv_path))
        return "skip", None, valid_existing
    now = datetime.now()
    refresh_today = (now.hour, now.minute) >= (SYNC_HOUR, SYNC_MINUTE)
    list_date = _instrument_list_date(ContextInfo, xtdata, code)
    if full_backfill:
        # Re-pull from effective start (max(BACKFILL_START, OpenDate)); merge.
        start_d = _effective_backfill_start(end_d, list_date)
        rows = _read_csv_rows(csv_path)
    else:
        start_d = _sync_start_date(csv_path, end_d, refresh_today=refresh_today)
        if start_d > end_d:
            valid_existing = _count_valid_rows(_read_csv_rows(csv_path))
            return "skip", None, valid_existing
        rows = _read_csv_rows(csv_path)
    start_s, end_s_range = _range_strings(start_d, end_d)
    # Incremental only needs 1 bar; FORCE/full backfill judged by first-date coverage.
    min_required = 1

    bars: List[Dict[str, Any]] = []
    last_dl = "none"
    # ????? CSV ???? start_d..end_d??????/????????????????????????
    fetch_start = start_d if full_backfill else _widen_fetch_start(start_d, end_d)
    fetch_start_s, fetch_end_s = _range_strings(fetch_start, end_d)

    def _ensure_today_from_tick(cur_bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """???? 1d ?????? end_d ????? get_full_tick ?????????QMT ???????????"""
        nonlocal last_dl, tick_bar
        has_end = any(
            str(b.get("date") or "")[:10] == end_d.isoformat() for b in (cur_bars or [])
        )
        if has_end:
            return cur_bars
        tb = tick_bar
        if tb is None:
            # ???????????????????????????????? batch_tried ??????
            got = _fetch_today_bars_from_full_tick(
                xtdata, [code], end_d, ContextInfo=ContextInfo
            )
            tb = got.get(code)
            if tb:
                tick_bar = tb
        if not tb:
            return cur_bars
        by_d = {
            str(b.get("date") or "")[:10]: b for b in (cur_bars or []) if b.get("date")
        }
        by_d[end_d.isoformat()] = tb
        out_bars = [by_d[k] for k in sorted(by_d.keys())]
        last_dl = (
            "full_tick"
            if last_dl in ("none", "rpc_dead", "no_ctx_no_rpc")
            else "%s+full_tick" % last_dl
        )
        return out_bars

    # ???????? ContextInfo ????????
    if pref_bars:
        bars = _sanitize_bars(_filter_bars_by_range(list(pref_bars), start_d, end_d))
        if bars:
            last_dl = "ctx_batch_%d" % len(bars)

    # Prefetch already tried (or empty). Dig short history; session=fast.
    dig_fast = _in_intraday_blocking_window()
    if batch_tried:
        bars = _ensure_today_from_tick(bars)
        if full_backfill:
            probe = dict(rows)
            _merge_bars(probe, _sanitize_bars(bars))
            earliest = _rows_earliest_date(probe)
            if not _history_coverage_ok(
                earliest,
                end_d,
                list_date=list_date,
                bar_count=_count_valid_rows(probe),
            ):
                more, dig_src = _backfill_by_slices(
                    xtdata,
                    code,
                    start_d,
                    end_d,
                    ContextInfo=ContextInfo,
                    fast=dig_fast,
                )
                if more:
                    by_d = {
                        str(b.get("date") or "")[:10]: b
                        for b in (_sanitize_bars(bars) or [])
                        if b.get("date")
                    }
                    for b in _sanitize_bars(more):
                        d = str(b.get("date") or "")[:10]
                        if d:
                            by_d[d] = b
                    bars = [by_d[k] for k in sorted(by_d.keys())]
                    last_dl = (
                        dig_src
                        if last_dl in ("none", "")
                        else "%s+%s" % (last_dl, dig_src)
                    )
                bars = _ensure_today_from_tick(bars)
        if not _bars_meet_requirement(bars, full_backfill, min_required, end_d=end_d, list_date=list_date):
            if (not full_backfill) and rows and not bars:
                _miss_cache_put(code, "today_halt", end_d, cache_dir)
                return "skip", "batch_miss_today_halt", _count_valid_rows(rows)
            # Soft short_hist: keep partial CSV, never miss_cache (retry later).
            if full_backfill and (rows or bars):
                try:
                    if bars:
                        _merge_bars(rows, bars)
                        rows = _trim_rows_for_storage(rows)
                        _write_daily_cache_csv(
                            cache_dir, code, rows, ContextInfo=ContextInfo, xtdata=xtdata
                        )
                    return (
                        "fail",
                        "short_hist_%d_after_%s"
                        % (_count_valid_rows(rows), last_dl),
                        _count_valid_rows(rows),
                    )
                except Exception as e:
                    return "fail", str(e), 0
            _miss_cache_put(code, "local_miss", end_d, cache_dir)
            return "fail", "local_miss_after_full_tick", 0
        # Incremental: CSV history + today tick only (fill gap without full ctx).
        if (
            (not full_backfill)
            and rows
            and bars
            and start_d < end_d
            and str(last_dl).endswith("full_tick")
            and len(_sanitize_bars(bars)) == 1
            and str((_sanitize_bars(bars)[0] or {}).get("date") or "")[:10]
            == end_d.isoformat()
        ):
            try:
                _merge_bars(rows, bars)
                rows = _trim_rows_for_storage(rows)
                _write_daily_cache_csv(
                    cache_dir, code, rows, ContextInfo=ContextInfo, xtdata=xtdata
                )
                _miss_cache_clear(code, cache_dir)
                return "ok", None, _count_valid_rows(rows)
            except Exception as e:
                return "fail", str(e), 0
    elif full_backfill:
        if not _bars_meet_requirement(bars, True, min_required, end_d=end_d, list_date=list_date):
            bars, last_dl = _backfill_by_slices(
                xtdata,
                code,
                start_d,
                end_d,
                ContextInfo=ContextInfo,
                fast=dig_fast,
            )
            bars = _ensure_today_from_tick(bars)
            if not _bars_meet_requirement(bars, True, min_required, end_d=end_d, list_date=list_date):
                # Soft: partial ContextInfo history must not permanent-miss.
                if rows or bars:
                    try:
                        if bars:
                            _merge_bars(rows, bars)
                            rows = _trim_rows_for_storage(rows)
                            _write_daily_cache_csv(
                            cache_dir, code, rows, ContextInfo=ContextInfo, xtdata=xtdata
                        )
                        return (
                            "fail",
                            "short_hist_%d_after_%s"
                            % (_count_valid_rows(rows), last_dl),
                            _count_valid_rows(rows),
                        )
                    except Exception as e:
                        return "fail", str(e), 0
                _miss_cache_put(code, "local_miss", end_d, cache_dir)
                return "fail", "local_miss_after_full_tick", 0
        else:
            bars = _ensure_today_from_tick(bars)
    else:
        # ?????????????/??????????????????? fail-fast ????
        if not _bars_meet_requirement(bars, False, min_required, end_d=end_d, list_date=list_date):
            bars = _fetch_valid_bars(
                xtdata,
                code,
                start_s,
                end_s_range,
                ContextInfo=ContextInfo,
                prefer_range=True,
                fast=True,
            )
            bars = _filter_bars_by_range(bars, start_d, end_d)
            if bars:
                last_dl = "ctx_fetch_%d" % len(bars)
        if not _bars_meet_requirement(bars, False, min_required, end_d=end_d, list_date=list_date):
            if _xtdata_rpc_alive():
                last_dl = _download_one_code(
                    xtdata, code, fetch_start_s, fetch_end_s, force_full=False
                )
                if last_dl != "rpc_dead":
                    time.sleep(FULL_POST_DOWNLOAD_SLEEP_SEC)
                    bars = _download_daily_bars(
                        xtdata,
                        code,
                        fetch_start,
                        end_d,
                        ContextInfo=ContextInfo,
                        sleep_sec=0,
                    )
                    bars = _filter_bars_by_range(bars, start_d, end_d)
            # ???? ContextInfo ????????? QMT ???? download_history_data???????????????? xtdata??
            if not _bars_meet_requirement(
                bars, False, min_required, end_d=end_d, list_date=list_date
            ):
                _ensure_builtin_download_bound()
                # ?? CSV????????????????????? 15 ????????????
                dl_start = fetch_start if rows else _recent_start_date(end_d)
                n_ok, dl_detail = _download_1d_via_builtin([code], dl_start, end_d)
                if n_ok > 0:
                    # ??????????????????????????????? download ???? get_market_data_ex
                    time.sleep(float(POOL_POST_DOWNLOAD_SLEEP_SEC))
                    use_start = dl_start if not rows else start_d
                    batch_map, src = _batch_fetch_1d_bars(
                        ContextInfo,
                        xtdata,
                        [code],
                        use_start,
                        end_d,
                        prefer_count=-1,
                        quality_min=1,
                    )
                    more = _sanitize_bars(batch_map.get(code) or [])
                    if not more:
                        more = _sanitize_bars(
                            _fetch_bars_local_raw(
                                xtdata, code, use_start, end_d, ContextInfo
                            )
                        )
                    if not more:
                        # ?? fast?????? get_market_data_ex ??????????????? get_local_data??
                        dl_s, dl_e = _range_strings(use_start, end_d)
                        more = _sanitize_bars(
                            _fetch_valid_bars(
                                xtdata,
                                code,
                                dl_s,
                                dl_e,
                                ContextInfo=ContextInfo,
                                prefer_range=True,
                                fast=False,
                            )
                        )
                    more = _filter_bars_by_range(more, use_start, end_d)
                    if more:
                        bars = _merge_bar_lists(bars, more)
                        last_dl = "builtin_dl_%s_%d" % (src or "ok", len(more))
                        print(
                            "[按需同步] 内置下载后读回 %s bars=%d src=%s %s..%s"
                            % (
                                code,
                                len(more),
                                src or "local",
                                use_start.isoformat(),
                                end_d.isoformat(),
                            )
                        )
                    else:
                        last_dl = "builtin_dl_empty_%s" % (dl_detail or "ok")
                        print(
                            "[按需同步] 内置下载已调用但读回空 %s detail=%s range=%s..%s"
                            % (
                                code,
                                dl_detail or "ok",
                                use_start.isoformat(),
                                end_d.isoformat(),
                            )
                        )
                elif last_dl in ("none", ""):
                    last_dl = (
                        "no_ctx_no_rpc"
                        if not dl_detail
                        else "no_ctx+builtin_miss_%s" % dl_detail
                    )
                elif dl_detail:
                    last_dl = "%s+builtin_miss_%s" % (last_dl, dl_detail)
        bars = _ensure_today_from_tick(bars)
        if (
            start_d < end_d
            and str(last_dl).endswith("full_tick")
            and len(_sanitize_bars(bars)) == 1
            and str((_sanitize_bars(bars)[0] or {}).get("date") or "")[:10]
            == end_d.isoformat()
        ):
            if rows:
                try:
                    _merge_bars(rows, bars)
                    rows = _trim_rows_for_storage(rows)
                    _write_daily_cache_csv(
                    cache_dir, code, rows, ContextInfo=ContextInfo, xtdata=xtdata
                )
                    _miss_cache_clear(code, cache_dir)
                    return "ok", None, _count_valid_rows(rows)
                except Exception as e:
                    return "fail", str(e), 0
            return (
                "fail",
                "gap_need_ctx_history_after_%s" % last_dl,
                1,
            )

    valid_n = len(_sanitize_bars(bars))
    bars = _sanitize_bars(bars)
    if not _bars_meet_requirement(bars, full_backfill, min_required, end_d=end_d, list_date=list_date):
        # ??????????????????? K??????????+??????????????????? miss
        if (not full_backfill) and rows and bars:
            try:
                _merge_bars(rows, bars)
                rows = _trim_rows_for_storage(rows)
                _write_daily_cache_csv(
                    cache_dir, code, rows, ContextInfo=ContextInfo, xtdata=xtdata
                )
                _miss_cache_put(code, "today_halt", end_d, cache_dir)
                return "ok", "today_missing_kept_history", _count_valid_rows(rows)
            except Exception as e:
                return "fail", str(e), 0
        if (not full_backfill) and rows and not bars:
            # ?????????/tick ??????????????????? miss
            _miss_cache_put(code, "today_halt", end_d, cache_dir)
            return "skip", "today_halt", _count_valid_rows(rows)
        miss_r = _miss_reason_from_fail(
            "invalid_%d_valid_after_%s" % (valid_n, last_dl)
        )
        if miss_r:
            # invalid_0 ??????? local+tick ??????? local_miss
            if miss_r in ("empty_history", "invalid_0") and "full_tick" in str(last_dl):
                miss_r = "local_miss"
            _miss_cache_put(code, miss_r, end_d, cache_dir)
        return "fail", "invalid_%d_valid_after_%s" % (valid_n, last_dl), valid_n

    if not bars and not rows:
        _miss_cache_put(code, "local_miss", end_d, cache_dir)
        return "fail", "empty", 0
    try:
        _merge_bars(rows, bars)
        valid_written = _count_valid_rows(rows)
        if valid_written < MIN_STORAGE_BARS:
            return (
                "fail",
                "write_check_%d_valid" % valid_written,
                valid_written,
            )
        rows = _trim_rows_for_storage(rows)
        if full_backfill:
            earliest = _rows_earliest_date(rows)
            if not _history_coverage_ok(
                earliest, end_d, list_date=list_date, bar_count=valid_written
            ):
                _write_daily_cache_csv(
                    cache_dir, code, rows, ContextInfo=ContextInfo, xtdata=xtdata
                )  # keep partial merge progress
                # Unknown listing: one attempt then miss ? no infinite FORCE retry.
                if list_date is None and valid_written >= MIN_STORAGE_BARS:
                    _miss_cache_put(code, "ipo_unknown", end_d, cache_dir)
                    return (
                        "ok",
                        "ipo_unknown_first_%s_n_%d"
                        % (
                            earliest.isoformat() if earliest else "none",
                            _count_valid_rows(rows),
                        ),
                        _count_valid_rows(rows),
                    )
                return (
                    "fail",
                    "short_hist_first_%s_n_%d"
                    % (
                        earliest.isoformat() if earliest else "none",
                        _count_valid_rows(rows),
                    ),
                    _count_valid_rows(rows),
                )
        _write_daily_cache_csv(
            cache_dir, code, rows, ContextInfo=ContextInfo, xtdata=xtdata
        )
        _miss_cache_clear(code, cache_dir)
        return "ok", None, _count_valid_rows(rows)
    except Exception as e:
        return "fail", str(e), 0


def _pool_last_bar_date(bars: List[Dict[str, Any]]) -> Optional[date]:
    last_d = None
    for bar in bars or []:
        d_s = str(bar.get("date") or "")
        try:
            d = datetime.strptime(d_s[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if last_d is None or d > last_d:
            last_d = d
    return last_d


def _sync_one_code_pool(
    xtdata,
    cache_dir: str,
    code: str,
    end_d: date,
    ContextInfo=None,
) -> Tuple[str, Optional[str], int]:
    """???????????????????????? 120 ?????????"""
    csv_path = os.path.join(cache_dir, code + ".csv")
    write_end_d = _pool_daily_write_end_date(xtdata, end_d)
    if write_end_d < end_d:
        _pool_strip_forming_csv(csv_path, write_end_d)

    try:
        from ant_data_sync_request import _short_history_stale, load_requests
    except ImportError:
        try:
            from qmt_builtin.ant_data_sync_request import (
                _short_history_stale,
                load_requests,
            )
        except ImportError:
            load_requests = None  # type: ignore[assignment]
            _short_history_stale = None  # type: ignore[assignment]
    if load_requests is not None and _short_history_stale is not None:
        data = load_requests()
        daily = data.get("daily") or {}
        meta = daily.get(code) if isinstance(daily.get(code), dict) else {}
        if str(meta.get("status") or "") == "short_history" and not _short_history_stale(
            meta
        ):
            valid_existing = _count_valid_rows(_read_csv_rows(csv_path))
            return "skip", "short_history_today", valid_existing

    if _pool_csv_ready(code, cache_dir, end_d, xtdata=xtdata):
        valid_existing = _count_valid_rows(_read_csv_rows(csv_path))
        return "skip", None, valid_existing

    fast_bars = _clip_bars_on_or_before(
        _fetch_pool_bars_optimized(xtdata, code, write_end_d, ContextInfo=ContextInfo),
        write_end_d,
    )
    if _pool_bars_quality_ok(fast_bars, write_end_d, xtdata=xtdata, code=code):
        rows = _trim_rows_for_storage(
            {
                str(bar.get("date") or ""): bar
                for bar in fast_bars
                if bar.get("date")
            }
        )
        rows = _merge_rows_keep_newer(csv_path, rows, write_end_d)
        try:
            _write_daily_cache_csv(
            cache_dir, code, rows, ContextInfo=ContextInfo, xtdata=xtdata
        )
            return "ok", None, len(rows)
        except Exception as e:
            return "fail", str(e), 0

    bars = _pool_trade_bars(_filter_pool_bars(fast_bars, write_end_d))
    # ????????? write_end????????????????????????????? 7/31 ??????
    last_fast = _pool_last_bar_date(bars)
    if last_fast is None or last_fast < write_end_d:
        st2, reason2, n2 = _sync_one_code(
            xtdata,
            cache_dir,
            code,
            write_end_d,
            ContextInfo=ContextInfo,
            sync_source="on_demand_pool_lag",
            pref_bars=bars or None,
        )
        if st2 in ("ok", "skip"):
            return st2, reason2, n2
        # ??????????????????????????? CSV
        exist_last = _csv_last_trade_date(csv_path)
        if exist_last is not None and (
            last_fast is None or exist_last > last_fast
        ):
            return "fail", "pool_lag_%s_keep_csv_%s" % (
                last_fast.isoformat() if last_fast else "none",
                exist_last.isoformat(),
            ), _count_valid_rows(_read_csv_rows(csv_path))

    valid_n = len(bars)
    q_reason = _pool_storage_reason(
        bars, write_end_d, xtdata=xtdata, list_date=_csv_list_date(code)
    )
    if valid_n > 0:
        last_new = _pool_last_bar_date(bars)
        exist_last = _csv_last_trade_date(csv_path)
        # ?????????????
        if exist_last is not None and last_new is not None and last_new < exist_last:
            if exist_last >= write_end_d and _pool_bars_usable_for_trading(
                _rows_to_bars(_read_csv_rows(csv_path)), write_end_d, xtdata=xtdata
            ):
                return "ok", "keep_newer_csv", _count_valid_rows(_read_csv_rows(csv_path))
            return "fail", "pool_stale_fetch_%s_csv_%s" % (
                last_new.isoformat(),
                exist_last.isoformat(),
            ), valid_n
        try:
            rows = _trim_rows_for_storage(
                {
                    str(bar.get("date") or ""): bar
                    for bar in bars
                    if bar.get("date")
                }
            )
            rows = _merge_rows_keep_newer(csv_path, rows, write_end_d)
            # ???????????????????????
            merged_last = None
            try:
                if rows:
                    merged_last = datetime.strptime(max(rows.keys())[:10], "%Y-%m-%d").date()
            except ValueError:
                merged_last = None
            if merged_last is None or merged_last < write_end_d:
                return "fail", "pool_invalid_%d_%s" % (
                    len(rows),
                    q_reason or ("lag_%s" % (merged_last.isoformat() if merged_last else "none")),
                ), len(rows)
            _write_daily_cache_csv(
            cache_dir, code, rows, ContextInfo=ContextInfo, xtdata=xtdata
        )
            valid_n = len(rows)
        except Exception as e:
            return "fail", str(e), valid_n
    if q_reason:
        # ?????????? write_end ????? partial hist
        if _pool_bars_usable_for_trading(bars, write_end_d, xtdata=xtdata):
            return "ok", "partial_%s" % q_reason, valid_n
        return "fail", "pool_invalid_%d_%s" % (valid_n, q_reason), valid_n
    if not bars:
        return "fail", "empty", 0
    return "ok", None, valid_n


def _sync_start_date(
    csv_path: str, end_d: date, *, refresh_today: bool = False
) -> date:
    rows = _read_csv_rows(csv_path)
    valid_n = _count_valid_rows(rows)
    if not rows or valid_n < MIN_STORAGE_BARS:
        # Cold empty: year window only under FORCE; else short incremental lookback.
        if _force_backfill_requested():
            return _recent_start_date(end_d)
        return end_d - timedelta(days=int(CTX_INCREMENTAL_LOOKBACK_DAYS))
    try:
        last = datetime.strptime(max(rows.keys()), "%Y-%m-%d").date()
    except ValueError:
        if _force_backfill_requested():
            return _recent_start_date(end_d)
        return end_d - timedelta(days=int(CTX_INCREMENTAL_LOOKBACK_DAYS))
    if last > end_d:
        return end_d + timedelta(days=1)
    if last == end_d:
        if refresh_today:
            return end_d
        return end_d + timedelta(days=1)
    return last + timedelta(days=1)


def _merge_bars(rows_by_date: Dict[str, Dict[str, Any]], bars: List[Dict[str, Any]]) -> int:
    added = 0
    for bar in bars:
        if not _is_valid_bar(bar):
            continue
        d = str(bar.get("date") or "")
        if not d:
            continue
        rows_by_date[d] = bar
        added += 1
    return added


def _load_manifest(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    try:
        import json
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_universe(path: str, codes: List[str], trade_date: str) -> None:
    payload = {
        "version": 1,
        "trade_date": trade_date,
        "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "count": len(codes),
        "codes": codes,
    }
    save_json_atomic(path, payload)


def _save_manifest(path: str, payload: Dict[str, Any]) -> None:
    save_json_atomic(path, payload)


def _already_synced_to_end(manifest: Dict[str, Any], end_d: date) -> bool:
    if _force_backfill_requested():
        return False
    end_s = end_d.isoformat()
    if str(manifest.get("sync_trade_date") or "") != end_s:
        return False
    if int(manifest.get("quality_version") or 0) < QUALITY_VERSION:
        return False
    if str(manifest.get("backfill_start") or "") < _backfill_start_ymd(end_d):
        return False
    if int(manifest.get("min_valid_bars_required") or 0) < MIN_BACKFILL_BARS:
        return False
    return str(manifest.get("status") or "") == "completed"


def _in_intraday_blocking_window(now: Optional[datetime] = None) -> bool:
    """Weekday 09:00-15:30: use soft time-slice (not a full stop)."""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dt_time(9, 0) <= t <= dt_time(15, 30)


def _in_continuous_quote_watch(now: Optional[datetime] = None) -> bool:
    """连续竞价监控窗（与 ant_shadow_strategy 一致）。"""
    now = now or datetime.now()
    t = now.time()
    return (dt_time(9, 30) <= t <= dt_time(11, 30)) or (
        dt_time(13, 0) <= t <= dt_time(15, 0)
    )


def _force_slice_expired(t_slice_start: float, slice_mode: bool) -> bool:
    """True when market-hours quantum exceeded (soft; mid-RPC cannot abort)."""
    if not slice_mode:
        return False
    return (time.time() - float(t_slice_start)) >= float(FORCE_INTRADAY_SLICE_SEC)


def _arm_force_slice_idle() -> None:
    """After a yield, idle before next FORCE slice so quotes can catch up."""
    global _FORCE_SLICE_IDLE_UNTIL
    _FORCE_SLICE_IDLE_UNTIL = time.time() + float(FORCE_INTRADAY_IDLE_SEC)


def _arm_force_defer_after_hours() -> None:
    """Stop intraday FORCE re-walks; resume at daily sync cutoff for dig-only.

    Do NOT park `_FORCE_SLICE_IDLE_UNTIL` until 15:35 ? that reused the short
    quotes-catch-up idle and blocked on_demand for hundreds of seconds with a
    misleading "FORCE idle gap ... quotes catch-up" log. Manifest
    `pause_reason=defer_after_hours` gates re-walks until cutoff.
    """
    global _FORCE_SLICE_IDLE_UNTIL
    now = datetime.now()
    target = now.replace(
        hour=int(SYNC_HOUR), minute=int(SYNC_MINUTE), second=0, microsecond=0
    )
    # Brief cooldown only; long wait is via manifest + maybe_run check.
    _FORCE_SLICE_IDLE_UNTIL = time.time() + 5.0
    remain = max(0.0, (target - now).total_seconds()) if now < target else 0.0
    print(
        "[日线同步] 强制补数 推迟软短深挖至盘后 remain=%.0fs target=%02d:%02d (slice_idle=5s only)"
        % (remain, int(SYNC_HOUR), int(SYNC_MINUTE))
    )


def _past_daily_sync_cutoff(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    return (now.hour, now.minute) >= (SYNC_HOUR, SYNC_MINUTE)


def _manifest_partial_running(manifest: Optional[Dict[str, Any]] = None) -> bool:
    """True when catch-up is mid-flight, paused for quotes, or coverage incomplete."""
    if manifest is None:
        _, _, _, manifest_path = _data_paths()
        manifest = _load_manifest(manifest_path)
    return str(manifest.get("status") or "") in ("running", "incomplete", "paused")


def _should_schedule_failed_manifest_recovery() -> bool:
    """init: schedule FORCE/catch-up; market hours OK (time-sliced)."""
    _, cache_dir, _, manifest_path = _data_paths()
    if _force_backfill_requested(cache_dir):
        return True
    if _manifest_partial_running(_load_manifest(manifest_path)):
        return True
    now = datetime.now()
    if not _past_daily_sync_cutoff(now):
        return False
    manifest = _load_manifest(manifest_path)
    try:
        xtdata = _load_xtdata()
    except Exception:
        return str(manifest.get("status") or "") == "failed"
    end_d = _resolve_sync_end_date(xtdata, now)
    if _maybe_reopen_completed_for_stale_bars(end_d):
        manifest = _load_manifest(manifest_path)
    if _already_synced_to_end(manifest, end_d):
        return False
    return True


def schedule_failed_manifest_recovery_on_init() -> None:
    """init: arm FORCE/catch-up; daytime runs via short time-slices."""
    global _FAILED_RECOVERY_DUE_AT, _FAILED_RECOVERY_ATTEMPTED
    _FAILED_RECOVERY_ATTEMPTED = False
    _FAILED_RECOVERY_DUE_AT = 0.0
    if not _should_schedule_failed_manifest_recovery():
        return
    force = _force_backfill_requested()
    delay = float(FORCE_INIT_DELAY_SEC if force else FAILED_RECOVERY_DELAY_SEC)
    _FAILED_RECOVERY_DUE_AT = time.time() + delay
    print(
        "[日线同步] 补跑已安排 %ds 后（初始化; quality=%d 强制=%s slice=%s idle=%.0fs)"
        % (
            int(delay),
            QUALITY_VERSION,
            "yes" if force else "no",
            "yes" if _in_intraday_blocking_window() else "no",
            float(FORCE_INTRADAY_IDLE_SEC),
        )
    )


def maybe_run_failed_manifest_recovery(ContextInfo=None) -> bool:
    global _FAILED_RECOVERY_DUE_AT, _FAILED_RECOVERY_ATTEMPTED
    if _FAILED_RECOVERY_ATTEMPTED or not _FAILED_RECOVERY_DUE_AT:
        return False
    if time.time() < _FAILED_RECOVERY_DUE_AT:
        return False
    _FAILED_RECOVERY_ATTEMPTED = True
    _FAILED_RECOVERY_DUE_AT = 0.0
    if not _should_schedule_failed_manifest_recovery():
        print("[日线同步] 无需失败清单恢复（条件未满足）")
        if _daily_gate_open_for_tick():
            _schedule_tick_pipeline()
        return False
    print("[日线同步] 开始失败清单恢复")
    ok = run_catch_up_sync(ContextInfo, source="init_catchup")
    # ???? FORCE ??????????? tick???????????????
    if ok and _past_daily_sync_cutoff():
        _schedule_tick_pipeline()
    elif (not ok) and _daily_gate_open_for_tick():
        _schedule_tick_pipeline()
    return ok


def maybe_run_force_year_backfill(ContextInfo=None) -> bool:
    """Periodic: resume FORCE / partial; market hours use time-slice + idle gap."""
    global _FORCE_IDLE_LOG_TS, _FORCE_SLICE_IDLE_UNTIL
    if _SYNC_RUNNING:
        return False
    now_ts = time.time()
    _, cache_dir, _, manifest_path = _data_paths()
    force = _force_backfill_requested(cache_dir)
    manifest = _load_manifest(manifest_path)
    if not force:
        # Do not keep FORCE incomplete/defer loops alive without the flag.
        manifest = _abandon_stale_force_partial(
            manifest, cache_dir=cache_dir, manifest_path=manifest_path
        )
    partial = _manifest_partial_running(manifest)
    if not force and not partial:
        return False
    # After soft_short: wait for after-hours dig (until SYNC_HOUR:MINUTE).
    # Check BEFORE slice-idle so we never mislabel a multi-minute defer as
    # "quotes catch-up", and so on_demand is not blocked via long idle.
    if (
        force
        and str(manifest.get("status") or "") == "incomplete"
        and str(manifest.get("pause_reason") or "") == "defer_after_hours"
        and not _past_daily_sync_cutoff()
    ):
        now = datetime.now()
        target = now.replace(
            hour=int(SYNC_HOUR), minute=int(SYNC_MINUTE), second=0, microsecond=0
        )
        remain_ah = max(0.0, (target - now).total_seconds())
        if now_ts - _FORCE_IDLE_LOG_TS >= 60.0:
            print(
                "[日线同步] 强制补数 盘后推迟（软短）; 盘后深挖于 %02d:%02d remain=%.0fs"
                % (int(SYNC_HOUR), int(SYNC_MINUTE), remain_ah)
            )
            _FORCE_IDLE_LOG_TS = now_ts
        return False
    if now_ts < float(_FORCE_SLICE_IDLE_UNTIL):
        remain_idle = float(_FORCE_SLICE_IDLE_UNTIL) - now_ts
        if remain_idle > float(FORCE_INTRADAY_IDLE_SEC) * 5.0:
            # Stale long park (pre-fix defer_after_hours); clear so dig can arm.
            _FORCE_SLICE_IDLE_UNTIL = 0.0
        else:
            if now_ts - _FORCE_IDLE_LOG_TS >= 60.0:
                print(
                    "[日线同步] 强制补数 空闲间隙 remain=%.0fs（行情补跑）"
                    % max(0.0, remain_idle)
                )
                _FORCE_IDLE_LOG_TS = now_ts
            return False
    print(
        "[日线同步] 强制/部分已武装 强制=%s partial=%s progress=%s/%s "
        "slice=%s quantum=%.0fs idle=%.0fs"
        % (
            "yes" if force else "no",
            "yes" if partial else "no",
            manifest.get("progress") or 0,
            manifest.get("progress_total") or "?",
            "yes" if _in_intraday_blocking_window() else "no",
            float(FORCE_INTRADAY_SLICE_SEC),
            float(FORCE_INTRADAY_IDLE_SEC),
        )
    )
    ok = run_catch_up_sync(ContextInfo, source="force_slice")
    if ok and _past_daily_sync_cutoff():
        _schedule_tick_pipeline()
    return ok


def run_catch_up_sync(ContextInfo=None, source: str = "timer") -> bool:
    """Full / FORCE catch-up. Market hours: short time-slices + idle gap."""
    global _SYNC_DONE_END_DATE, _SYNC_RUNNING, _FAIL_LOG_COUNT, _XTDATA_RPC_OK
    global _MISS_LOG_COUNT
    if _SYNC_RUNNING:
        print("[日线同步] 跳过: 同步进行中 (source=%s)" % source)
        return False

    # Skip while tick full sync owns ContextInfo.
    try:
        try:
            import ant_tick_full_sync_runner as tick_full
        except ImportError:
            import qmt_builtin.ant_tick_full_sync_runner as tick_full
        if bool(getattr(tick_full, "_BUSY", False)):
            print("[日线同步] 跳过: 同步进行中 (source=%s)" % source)
            return False
    except Exception:
        pass

    now = datetime.now()
    _, cache_dir, universe_path, manifest_path = _data_paths()
    old_manifest = _load_manifest(manifest_path)
    old_manifest = _consume_reset_force_progress(
        old_manifest, cache_dir=cache_dir, manifest_path=manifest_path
    )
    old_manifest = _abandon_stale_force_partial(
        old_manifest, cache_dir=cache_dir, manifest_path=manifest_path
    )
    force_year = _force_backfill_requested(cache_dir)
    partial_running = _manifest_partial_running(old_manifest)

    _FAIL_LOG_COUNT = 0
    # Prefer ContextInfo; xtdata download stays disabled unless enabled.
    _XTDATA_RPC_OK = False if not ENABLE_XTDATA_DOWNLOAD else None

    try:
        xtdata = _load_xtdata()
    except Exception as e:
        print("[日线同步] 加载 xtdata 失败: %s" % e)
        return False

    end_d = _resolve_sync_end_date(xtdata, now)
    end_s = end_d.isoformat()
    bf_start_ymd = _backfill_start_ymd(end_d, cache_dir)

    # Skip done unless FORCE / partial resume.
    if _SYNC_DONE_END_DATE == end_s and not force_year and not partial_running:
        print("[日线同步] 跳过: 当日已完成 end=%s source=%s" % (end_s, source))
        return False
    if _already_synced_to_end(old_manifest, end_d):
        _SYNC_DONE_END_DATE = end_s
        print(
            "[日线同步] 跳过: manifest 已完成 end=%s source=%s"
            % (end_s, source)
        )
        return False

    # Daytime: soft time-slice. After hours: continuous.
    slice_mode = _in_intraday_blocking_window()
    builtin_dl_ready = _ensure_builtin_download_bound()
    verbose = _daily_sync_verbose()
    if verbose:
        print(
            "[日线同步] 主路径=ContextInfo.get_market_data_ex(1d) builtin_dl=%s xtdata_dl=off"
            % ("on" if builtin_dl_ready else "miss")
        )
    if ContextInfo is None:
        print(
            "[日线同步] 警告: ContextInfo 为 None; ContextInfo 路径不可用"
        )
    if verbose and (
        force_year or int(old_manifest.get("quality_version") or 0) < QUALITY_VERSION
    ):
        print(
            "[日线同步] 回补已武装 start=%s min_bars~%d quality=%d->%d force=%s slice=%s quantum=%.0fs batch=%d"
            % (
                bf_start_ymd,
                MIN_BACKFILL_BARS,
                int(old_manifest.get("quality_version") or 0),
                QUALITY_VERSION,
                "yes" if force_year else "no",
                "yes" if slice_mode else "no",
                float(FORCE_INTRADAY_SLICE_SEC),
                CTX_BATCH_SIZE_INTRADAY if slice_mode else CTX_BATCH_SIZE,
            )
        )

    _SYNC_RUNNING = True
    started = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    if (
        partial_running
        and str(old_manifest.get("sync_trade_date") or "") == end_s
        and old_manifest.get("started_at")
    ):
        started = str(old_manifest.get("started_at"))
    ok_count = 0
    skip_count = 0
    fail_count = 0
    miss_skip_count = 0
    soft_short_count = 0
    failed_codes: List[str] = []
    universe: List[str] = []
    tick_prefetch: Dict[str, Dict[str, Any]] = {}
    bars_prefetch: Dict[str, List[Dict[str, Any]]] = {}
    primary_source = ""
    optional_rpc_skipped = False
    batch_codes: List[str] = []
    batch_tried = False
    resume_from = 0
    # Slice quantum starts only when sync work begins (after setup/prioritize).
    t_slice_start = 0.0
    sliced_yield = False

    try:
        universe = _fetch_universe(xtdata, ContextInfo=ContextInfo)
        if not universe:
            print("[日线同步] 中止: 股票池为空")
            return False

        os.makedirs(cache_dir, exist_ok=True)
        _save_universe(universe_path, universe, end_s)
        print(
            "[日线同步] 补跑开始 版本=%s source=%s end=%s count=%d"
            % (DAILY_SYNC_VERSION, source, end_s, len(universe))
        )
        if force_year:
            _clear_halt_miss_for_force(cache_dir)
        # Drop false delisted poison (OpenDate mirrored as ExpireDate, esp. 688*).
        _clear_false_delisted_miss_with_recent_csv(cache_dir, end_d=end_d)

        # ???? miss ????????????? TTL ?????/????
        _MISS_LOG_COUNT = 0
        _miss_cache_load(cache_dir)
        filtered: List[str] = []
        for code in universe:
            meta = _miss_cache_active(code, end_d, cache_dir)
            if meta is None:
                filtered.append(code)
                continue
            reason = str(meta.get("reason") or "miss")
            # ?????????????tick ?? empty_history ????????????
            if reason == "delisted":
                miss_skip_count += 1
                skip_count += 1
                continue
            # IPO unknown: do not requeue forever.
            if reason == "ipo_unknown":
                miss_skip_count += 1
                skip_count += 1
                continue
            # short_history: intraday FORCE skips (already tried today); after-hours dig.
            if reason == "short_history":
                if force_year and (not _in_intraday_blocking_window()):
                    filtered.append(code)
                    continue
                miss_skip_count += 1
                skip_count += 1
                continue
            # local_miss / empty_history / invalid_0 / no_ctx??????????
            if reason in (
                "local_miss",
                "empty_history",
                "invalid_0",
                "no_ctx",
            ):
                filtered.append(code)
                continue
            # today_halt: skip only when history already OK; FORCE always retries.
            if reason in ("today_halt", "suspended"):
                csv_p = os.path.join(cache_dir, code + ".csv")
                if force_year or _needs_full_backfill(csv_p, cache_dir, end_d=end_d):
                    filtered.append(code)
                    continue
                if _count_valid_rows(_read_csv_rows(csv_p)) >= MIN_STORAGE_BARS:
                    miss_skip_count += 1
                    skip_count += 1
                    continue
                filtered.append(code)
                continue
            # ??? miss ????????????????
            filtered.append(code)
        if miss_skip_count:
            print(
                "[日线同步] miss缓存 skip=%d remain=%d path=%s"
                % (miss_skip_count, len(filtered), _miss_cache_path(cache_dir))
            )
        # FORCE: truncated-first order. Cache after first scan; reuse on slices.
        if force_year and filtered:
            cached_ord = _load_force_ordered_codes(cache_dir, end_s)
            if cached_ord:
                filtered = _apply_force_ordered_cache(filtered, cached_ord)
                if verbose:
                    print(
                        "[日线同步] 强制补数 复用有序缓存 n=%d（跳过 CSV 重扫）"
                        % len(cached_ord)
                    )
            else:
                cold: List[str] = []
                warm: List[str] = []
                for code in filtered:
                    csv_p = os.path.join(cache_dir, code + ".csv")
                    if _needs_full_backfill(csv_p, cache_dir, end_d=end_d):
                        cold.append(code)
                    else:
                        warm.append(code)
                if cold:
                    if verbose:
                        print(
                            "[日线同步] 强制补数 优先 truncated=%d warm=%d"
                            % (len(cold), len(warm))
                        )
                    filtered = cold + warm
                _save_force_ordered_codes(
                    cache_dir, end_s, filtered, len(cold), len(warm)
                )
        universe = filtered

        total = len(universe)
        # ??????????? time-slice ????? progress??1-based ?????????
        if (
            partial_running
            and str(old_manifest.get("sync_trade_date") or "") == end_s
            and int(old_manifest.get("progress") or 0) > 0
        ):
            resume_from = min(int(old_manifest.get("progress") or 0), total)
            ok_count = int(old_manifest.get("ok_count") or 0)
            skip_count = int(old_manifest.get("skip_count") or 0)
            fail_count = int(old_manifest.get("fail_count") or 0)
            miss_skip_count = max(
                miss_skip_count, int(old_manifest.get("miss_skip_count") or 0)
            )
            soft_short_count = int(old_manifest.get("soft_short_hist") or 0)
            prev_failed = old_manifest.get("failed_codes") or []
            if isinstance(prev_failed, list):
                failed_codes = [str(c) for c in prev_failed[:200]]
            if verbose:
                print(
                    "[日线同步] 从进度恢复=%d/%d ok=%d skip=%d fail=%d 软短=%d"
                    % (
                        resume_from,
                        total,
                        ok_count,
                        skip_count,
                        fail_count,
                        soft_short_count,
                    )
                )

        # Quantum counts download/ContextInfo work only ? not prioritize/setup.
        t_slice_start = time.time()
        t_progress_log = time.time()
        verbose = _daily_sync_verbose()
        for idx, code in enumerate(universe):
            pos = idx + 1
            if pos <= resume_from:
                continue
            # Mid-walk disarm: FORCE flag cleared => stop incomplete loop now.
            if force_year and (not _force_backfill_requested(cache_dir)):
                print(
                    "[日线同步] 强制补数标志运行中被清除; 中止于 %d/%d (source=%s)"
                    "" % (max(0, pos - 1), total, source)
                )
                try:
                    _miss_cache_save(cache_dir, force=True)
                except Exception:
                    pass
                _abandon_stale_force_partial(
                    {
                        "status": "running",
                        "trigger": source,
                        "soft_short_hist": soft_short_count,
                        "progress": max(0, pos - 1),
                        "progress_total": total,
                    },
                    cache_dir=cache_dir,
                    manifest_path=manifest_path,
                )
                return False
            # Upgrade to slice mode if session opens mid-run.
            if _in_intraday_blocking_window():
                slice_mode = True
            # Wall-clock BEFORE each code (and before ContextInfo batch below).
            if _force_slice_expired(t_slice_start, slice_mode):
                sliced_yield = True
                try:
                    _miss_cache_save(cache_dir, force=True)
                    _save_manifest(
                        manifest_path,
                        {
                            "version": 1,
                            "quality_version": QUALITY_VERSION,
                            "backfill_start": bf_start_ymd,
                            "min_valid_bars_required": MIN_BACKFILL_BARS,
                            "sync_trade_date": end_s,
                            "universe_count": len(universe) + miss_skip_count,
                            "ok_count": ok_count,
                            "skip_count": skip_count,
                            "fail_count": fail_count,
                            "miss_skip_count": miss_skip_count,
                            "soft_short_hist": soft_short_count,
                            "failed_codes": failed_codes[:50],
                            "started_at": started,
                            "finished_at": "",
                            "status": "paused",
                            "progress": max(0, pos - 1),
                            "progress_total": total,
                            "last_code": code,
                            "runner_version": DAILY_SYNC_VERSION,
                            "trigger": source,
                            "primary_path": "ContextInfo.get_market_data_ex(1d)",
                            "primary_source": primary_source,
                            "time_sliced": True,
                            "pause_reason": "time_slice",
                        },
                    )
                except Exception:
                    pass
                _arm_force_slice_idle()
                print(
                    "[日线同步] 时间片让出（代码前）after %.1fs progress=%d/%d idle=%.0fs"
                    % (
                        time.time() - t_slice_start,
                        max(0, pos - 1),
                        total,
                        float(FORCE_INTRADAY_IDLE_SEC),
                    )
                )
                return False
            batch_sz = (
                CTX_BATCH_SIZE_INTRADAY if slice_mode else CTX_BATCH_SIZE
            )
            if pos == 1 or pos % batch_sz == 1 or (
                resume_from > 0 and pos == resume_from + 1
            ):
                # Wall-clock BEFORE ContextInfo batch call.
                if _force_slice_expired(t_slice_start, slice_mode):
                    sliced_yield = True
                    try:
                        _miss_cache_save(cache_dir, force=True)
                        _save_manifest(
                            manifest_path,
                            {
                                "version": 1,
                                "quality_version": QUALITY_VERSION,
                                "backfill_start": bf_start_ymd,
                                "min_valid_bars_required": MIN_BACKFILL_BARS,
                                "sync_trade_date": end_s,
                                "universe_count": len(universe) + miss_skip_count,
                                "ok_count": ok_count,
                                "skip_count": skip_count,
                                "fail_count": fail_count,
                                "miss_skip_count": miss_skip_count,
                                "soft_short_hist": soft_short_count,
                                "failed_codes": failed_codes[:50],
                                "started_at": started,
                                "finished_at": "",
                                "status": "paused",
                                "progress": max(0, pos - 1),
                                "progress_total": total,
                                "last_code": code,
                                "runner_version": DAILY_SYNC_VERSION,
                                "trigger": source,
                                "primary_path": "ContextInfo.get_market_data_ex(1d)",
                                "primary_source": primary_source,
                                "time_sliced": True,
                                "pause_reason": "time_slice",
                            },
                        )
                    except Exception:
                        pass
                    _arm_force_slice_idle()
                    print(
                        "[日线同步] 时间片让出（ContextInfo 前）after %.1fs progress=%d/%d idle=%.0fs"
                        % (
                            time.time() - t_slice_start,
                            max(0, pos - 1),
                            total,
                            float(FORCE_INTRADAY_IDLE_SEC),
                        )
                    )
                    return False
                batch_end = min(
                    ((pos - 1) // batch_sz) * batch_sz + batch_sz,
                    total,
                )
                batch_codes = universe[pos - 1 : batch_end]
                batch_tried = False
                if verbose:
                    print(
                        "[日线同步] 批次 %d-%d/%d 开始 (ContextInfo 1d size=%d)"
                        % (pos, batch_end, total, batch_sz)
                    )
                fetch_start = end_d - timedelta(days=CTX_INCREMENTAL_LOOKBACK_DAYS)
                cold_n = 0
                for bc in batch_codes:
                    csv_p = os.path.join(cache_dir, bc + ".csv")
                    if _needs_full_backfill(csv_p, cache_dir, end_d=end_d):
                        cold_n += 1
                prefer_count = -1
                quality_min = 0
                if cold_n >= 1:
                    fetch_start = _recent_start_date(end_d)
                    # Date-range primary; prefer_count is count-fallback hint only.
                    prefer_count = CTX_BACKFILL_COUNT
                    quality_min = MIN_BACKFILL_BARS
                try:
                    bars_prefetch, primary_source = _batch_fetch_1d_bars(
                        ContextInfo,
                        xtdata,
                        batch_codes,
                        fetch_start,
                        end_d,
                        prefer_count=prefer_count,
                        quality_min=quality_min,
                    )
                    batch_tried = True
                    if verbose:
                        print(
                            "[日线同步] 批次 ContextInfo 1d source=%s hit=%d/%d range=%s..%s cold=%d"
                            % (
                                primary_source,
                                len(bars_prefetch),
                                len(batch_codes),
                                fetch_start.isoformat(),
                                end_d.isoformat(),
                                cold_n,
                            )
                        )
                except Exception as e:
                    print(
                        "[日线同步] 批次 ContextInfo 1d 错误: %s: %s"
                        % (type(e).__name__, e)
                    )
                    bars_prefetch = {}
                    primary_source = "error"
                    batch_tried = True

                # FORCE/cold: local rolling window often ~124. Pull from broker via
                # host-QMT download_history_data(1d) then get_market_data_ex again.
                # Same bind as tick; NOT ENABLE_XTDATA_DOWNLOAD / miniQMT.
                if cold_n >= 1 and builtin_dl_ready:
                    short_need = []  # type: List[str]
                    for bc in batch_codes:
                        list_d = _csv_list_date(bc)
                        if _bars_need_broker_1d_dl(
                            bars_prefetch.get(bc), end_d, list_date=list_d
                        ):
                            short_need.append(bc)
                    if short_need:
                        dl_ok = 0
                        dl_tried = 0
                        for bc in short_need:
                            if _force_slice_expired(t_slice_start, slice_mode):
                                sliced_yield = True
                                try:
                                    _miss_cache_save(cache_dir, force=True)
                                    _save_manifest(
                                        manifest_path,
                                        {
                                            "version": 1,
                                            "quality_version": QUALITY_VERSION,
                                            "backfill_start": bf_start_ymd,
                                            "min_valid_bars_required": MIN_BACKFILL_BARS,
                                            "sync_trade_date": end_s,
                                            "universe_count": len(universe)
                                            + miss_skip_count,
                                            "ok_count": ok_count,
                                            "skip_count": skip_count,
                                            "fail_count": fail_count,
                                            "miss_skip_count": miss_skip_count,
                                            "soft_short_hist": soft_short_count,
                                            "failed_codes": failed_codes[:50],
                                            "started_at": started,
                                            "finished_at": "",
                                            "status": "paused",
                                            "progress": max(0, pos - 1),
                                            "progress_total": total,
                                            "last_code": code,
                                            "runner_version": DAILY_SYNC_VERSION,
                                            "trigger": source,
                                            "primary_path": (
                                                "download_history_data(1d)+"
                                                "ContextInfo.get_market_data_ex(1d)"
                                            ),
                                            "primary_source": primary_source,
                                            "time_sliced": True,
                                            "pause_reason": "time_slice",
                                        },
                                    )
                                except Exception:
                                    pass
                                _arm_force_slice_idle()
                                print(
                                    "[日线同步] 时间片让出（前）内置下载 after %.1fs progress=%d/%d dl=%d/%d idle=%.0fs"
                                    % (
                                        time.time() - t_slice_start,
                                        max(0, pos - 1),
                                        total,
                                        dl_ok,
                                        len(short_need),
                                        float(FORCE_INTRADAY_IDLE_SEC),
                                    )
                                )
                                return False
                            list_d = _csv_list_date(bc)
                            eff = _effective_backfill_start(end_d, list_d)
                            n_ok, detail = _download_1d_via_builtin(
                                [bc], eff, end_d
                            )
                            dl_tried += 1
                            if n_ok > 0:
                                dl_ok += 1
                            elif detail and dl_tried <= 3:
                                print(
                                    "[日线同步] 内置下载未命中 %s %s..%s | %s%Y%m%d%Y%m%d"
                                    % (
                                        bc,
                                        eff.strftime(""),
                                        end_d.strftime(""),
                                        detail,
                                    )
                                )
                        if verbose:
                            print(
                                "[日线同步] 内置下载 1d ok=%d/%d cold_short=%d range_floor=%s"
                                % (
                                    dl_ok,
                                    dl_tried,
                                    len(short_need),
                                    bf_start_ymd,
                                )
                            )
                        if dl_ok > 0:
                            if (not slice_mode) and FULL_POST_DOWNLOAD_SLEEP_SEC > 0:
                                time.sleep(float(FULL_POST_DOWNLOAD_SLEEP_SEC))
                            try:
                                # Re-get with SAME date range (not count-only).
                                bars2, src2 = _batch_fetch_1d_bars(
                                    ContextInfo,
                                    xtdata,
                                    short_need,
                                    fetch_start,
                                    end_d,
                                    prefer_count=-1,
                                    quality_min=quality_min or MIN_BACKFILL_BARS,
                                )
                                improved = 0
                                for bc, bl in (bars2 or {}).items():
                                    merged = _merge_bar_lists(
                                        bars_prefetch.get(bc), bl
                                    )
                                    if len(merged) > len(
                                        bars_prefetch.get(bc) or []
                                    ) or (
                                        _bars_earliest_date(merged)
                                        and _bars_earliest_date(
                                            bars_prefetch.get(bc) or []
                                        )
                                        and _bars_earliest_date(merged)
                                        < _bars_earliest_date(
                                            bars_prefetch.get(bc) or []
                                        )
                                    ):
                                        bars_prefetch[bc] = merged
                                        improved += 1
                                    elif merged:
                                        bars_prefetch[bc] = merged
                                primary_source = "%s+builtin_dl_%s" % (
                                    primary_source or "ctx",
                                    src2 or "ctx",
                                )
                                if verbose:
                                    print(
                                        "[日线同步] 内置下载后重取 改善=%d/%d source=%s"
                                        % (improved, len(short_need), src2)
                                    )
                            except Exception as e:
                                print(
                                    "[日线同步] 内置下载后重取 错误: %s: %s"
                                    % (type(e).__name__, e)
                                )

                # Session: skip full_tick batch enrichment (expensive on quotes).
                tick_prefetch = {}
                if not slice_mode:
                    missing_today = []
                    for bc in batch_codes:
                        blist = bars_prefetch.get(bc) or []
                        has_end = any(
                            str(b.get("date") or "")[:10] == end_d.isoformat()
                            for b in blist
                        )
                        if not has_end:
                            missing_today.append(bc)
                    try:
                        tick_prefetch = _fetch_today_bars_from_full_tick(
                            xtdata,
                            missing_today or batch_codes,
                            end_d,
                            ContextInfo=ContextInfo,
                        )
                    except Exception as e:
                        print("[日线同步] 批量 full_tick 补数失败: %s" % e)
                        tick_prefetch = {}

                # Optional xtdata download: after hours only.
                hit_ratio = (
                    float(len(bars_prefetch)) / float(len(batch_codes))
                    if batch_codes
                    else 0.0
                )
                if (not slice_mode) and hit_ratio < 0.2 and _xtdata_rpc_alive():
                    try:
                        pre_tag = _download_batch(
                            xtdata,
                            batch_codes,
                            end_d - timedelta(days=30),
                            end_d,
                        )
                        print(
                            "[日线同步] 可选批量下载 %s n=%d （ContextInfo 命中偏低）"
                            % (pre_tag, len(batch_codes))
                        )
                        if pre_tag == "batch_rpc_dead":
                            optional_rpc_skipped = True
                            _notify_quote_rpc_dead(end_s, pre_tag)
                        else:
                            time.sleep(max(0.2, FULL_POST_DOWNLOAD_SLEEP_SEC))
                            bars2, src2 = _batch_fetch_1d_bars(
                                ContextInfo,
                                xtdata,
                                batch_codes,
                                fetch_start,
                                end_d,
                                prefer_count=-1 if quality_min > 0 else prefer_count,
                                quality_min=quality_min,
                            )
                            for bc, bl in (bars2 or {}).items():
                                if len(bl) > len(bars_prefetch.get(bc) or []):
                                    bars_prefetch[bc] = bl
                            if src2 and src2 != "empty":
                                primary_source = "%s+dl_%s" % (
                                    primary_source or "ctx",
                                    src2,
                                )
                    except Exception as e:
                        print(
                            "[日线同步] 可选下载错误: %s: %s"
                            % (type(e).__name__, e)
                        )
                        if _is_quote_rpc_error(e):
                            optional_rpc_skipped = True
                            _mark_xtdata_rpc_dead(str(e))
                            _notify_quote_rpc_dead(end_s, str(e))
                elif (not slice_mode) and hit_ratio < 0.2:
                    optional_rpc_skipped = True

                if CTX_BATCH_SLEEP_SEC > 0:
                    time.sleep(CTX_BATCH_SLEEP_SEC)

            if verbose:
                print("[日线同步] %d/%d 处理中 %s" % (pos, total, code))

            # Instrument status / halt classification.
            inst_reason = _classify_instrument_status(
                ContextInfo, xtdata, code, end_d
            )
            if inst_reason == "delisted":
                # Local recent bars => classifier false positive; do not miss-skip.
                csv_p = os.path.join(cache_dir, code + ".csv")
                last_d = _last_date_in_csv(csv_p)
                if last_d is not None and (end_d - last_d).days <= 15:
                    if verbose:
                        print(
                            "[按需同步] 已清除误判退市 %s last_bar=%s"
                            % (code, last_d.isoformat())
                        )
                    inst_reason = None
                else:
                    _miss_cache_put(code, "delisted", end_d, cache_dir)
                    skip_count += 1
                    miss_skip_count += 1
                    continue
            if inst_reason == "today_halt":
                csv_p = os.path.join(cache_dir, code + ".csv")
                # During FORCE / truncated history, still sync to extend older bars.
                if (not force_year) and (not _needs_full_backfill(csv_p, cache_dir, end_d=end_d)):
                    if _count_valid_rows(_read_csv_rows(csv_p)) >= MIN_STORAGE_BARS:
                        _miss_cache_put(code, "today_halt", end_d, cache_dir)
                        skip_count += 1
                        continue

            status, reason, row_count = _sync_one_code(
                xtdata,
                cache_dir,
                code,
                end_d,
                ContextInfo=ContextInfo,
                sync_source=source,
                tick_bar=tick_prefetch.get(code),
                pref_bars=bars_prefetch.get(code),
                batch_tried=batch_tried,
            )
            if status == "ok":
                ok_count += 1
                if verbose:
                    print(
                        "[日线同步] 成功 %s valid_rows=%d"
                        % (code, row_count)
                    )
            elif status == "skip":
                skip_count += 1
            else:
                fail_count += 1
                failed_codes.append(code)
                if reason and str(reason).startswith("short_hist"):
                    soft_short_count += 1
                    # Same-trade-day defer: do not re-hammer in later FORCE passes today.
                    try:
                        _miss_cache_put(code, "short_history", end_d, cache_dir)
                    except Exception:
                        pass
                # Default quiet: fail reasons only in summary counts / VERBOSE.
                if verbose and reason:
                    print("[日线同步] 失败 %s: %s" % (code, reason))

            do_progress = (
                pos % PROGRESS_EVERY == 0
                or pos == total
                or (time.time() - t_progress_log) >= PROGRESS_INTERVAL_SEC
            )
            if do_progress:
                t_progress_log = time.time()
                print(
        "[日线同步] 进度 %d/%d ok=%d skip=%d fail=%d 软短=%d miss_skip=%d primary=%s"
                    % (
                        pos,
                        total,
                        ok_count,
                        skip_count,
                        fail_count,
                        soft_short_count,
                        miss_skip_count,
                        primary_source or "-",
                    )
                )
                try:
                    _miss_cache_save(cache_dir, force=False)
                    _save_manifest(
                        manifest_path,
                        {
                            "version": 1,
                            "quality_version": QUALITY_VERSION,
                            "backfill_start": bf_start_ymd,
                            "min_valid_bars_required": MIN_BACKFILL_BARS,
                            "sync_trade_date": end_s,
                            "universe_count": len(universe) + miss_skip_count,
                            "ok_count": ok_count,
                            "skip_count": skip_count,
                            "fail_count": fail_count,
                            "miss_skip_count": miss_skip_count,
                            "soft_short_hist": soft_short_count,
                            "failed_codes": failed_codes[:50],
                            "started_at": started,
                            "finished_at": "",
                            "status": "running",
                            "progress": pos,
                            "progress_total": total,
                            "last_code": code,
                            "runner_version": DAILY_SYNC_VERSION,
                            "trigger": source,
                            "primary_path": "ContextInfo.get_market_data_ex(1d)",
                            "primary_source": primary_source,
                            "time_sliced": bool(slice_mode),
                        },
                    )
                except Exception:
                    pass

            # Yield after code when wall-clock quantum exceeded.
            if pos < total and _force_slice_expired(t_slice_start, slice_mode):
                sliced_yield = True
                try:
                    _miss_cache_save(cache_dir, force=True)
                    _save_manifest(
                        manifest_path,
                        {
                            "version": 1,
                            "quality_version": QUALITY_VERSION,
                            "backfill_start": bf_start_ymd,
                            "min_valid_bars_required": MIN_BACKFILL_BARS,
                            "sync_trade_date": end_s,
                            "universe_count": len(universe) + miss_skip_count,
                            "ok_count": ok_count,
                            "skip_count": skip_count,
                            "fail_count": fail_count,
                            "miss_skip_count": miss_skip_count,
                            "soft_short_hist": soft_short_count,
                            "failed_codes": failed_codes[:50],
                            "started_at": started,
                            "finished_at": "",
                            "status": "paused",
                            "progress": pos,
                            "progress_total": total,
                            "last_code": code,
                            "runner_version": DAILY_SYNC_VERSION,
                            "trigger": source,
                            "primary_path": "ContextInfo.get_market_data_ex(1d)",
                            "primary_source": primary_source,
                            "time_sliced": True,
                            "pause_reason": "time_slice",
                        },
                    )
                except Exception:
                    pass
                _arm_force_slice_idle()
                print(
                    "[日线同步] 时间片让出 after %.1fs progress=%d/%d remain~%d idle=%.0fs"
                    % (
                        time.time() - t_slice_start,
                        pos,
                        total,
                        max(0, total - pos),
                        float(FORCE_INTRADAY_IDLE_SEC),
                    )
                )
                return False

        if sliced_yield:
            return False

        finished = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        _miss_cache_save(cache_dir, force=True)
        # Honesty gate: FORCE/year backfill is not done if most CSVs still start mid-window.
        truncated_left = 0
        sample_n = 0
        floor = _backfill_start_for(end_d)
        slack = timedelta(days=BACKFILL_FIRST_DATE_SLACK_DAYS)
        try:
            # First-date scan; IPO after floor counted OK when earliest ~ OpenDate.
            _load_list_date_map()
            for code in universe:
                csv_p = os.path.join(cache_dir, code + ".csv")
                rows = _read_csv_rows(csv_p)
                sample_n += 1
                if not rows:
                    truncated_left += 1
                    continue
                earliest = _rows_earliest_date(rows)
                list_d = _csv_list_date(code)
                if _history_coverage_ok(
                    earliest,
                    end_d,
                    list_date=list_d,
                    bar_count=_count_valid_rows(rows),
                ):
                    continue
                truncated_left += 1
        except Exception as e:
            print("[日线同步] 收尾写盘失败: %s" % e)
            truncated_left = max(truncated_left, 1)
        coverage_ok_ratio = (
            float(sample_n - truncated_left) / float(sample_n) if sample_n else 0.0
        )
        # Allow a small fail/IPO/miss residue; >15% truncated => not completed.
        # Also count same-day soft_short miss entries filtered out of this walk.
        soft_deferred = 0
        try:
            miss_payload = _miss_cache_load(cache_dir)
            miss_codes = miss_payload.get("codes") or {}
            if isinstance(miss_codes, dict):
                for _mc, _mm in miss_codes.items():
                    if not isinstance(_mm, dict):
                        continue
                    if str(_mm.get("reason") or "") != "short_history":
                        continue
                    if _miss_cache_active(str(_mc), end_d, cache_dir) is None:
                        continue
                    soft_deferred += 1
        except Exception:
            soft_deferred = 0
        force_incomplete = bool(force_year) and sample_n > 0 and (
            coverage_ok_ratio < 0.85 or soft_deferred > 0 or soft_short_count > 0
        )
        if force_incomplete:
            # Mark still-truncated codes so intraday re-walks skip them today.
            try:
                for code in universe:
                    csv_p = os.path.join(cache_dir, code + ".csv")
                    if _needs_full_backfill(csv_p, cache_dir, end_d=end_d):
                        _miss_cache_put(code, "short_history", end_d, cache_dir)
                _miss_cache_save(cache_dir, force=True)
            except Exception as e:
                print("[日线同步] 刷新 miss 覆盖率失败: %s" % e)
            print(
                "[日线同步] 强制补数未完成 coverage_ok=%.1f%% truncated=%d/%d 软短=%d soft_deferred=%d; 保留标志，重置进度以便重试"
                % (
                    100.0 * coverage_ok_ratio,
                    truncated_left,
                    sample_n,
                    soft_short_count,
                    soft_deferred,
                )
            )
            _clear_force_ordered_cache(cache_dir)
            _save_manifest(
                manifest_path,
                {
                    "version": 1,
                    "quality_version": QUALITY_VERSION,
                    "backfill_start": bf_start_ymd,
                    "min_valid_bars_required": MIN_BACKFILL_BARS,
                    "sync_trade_date": end_s,
                    "universe_count": len(universe) + miss_skip_count,
                    "ok_count": ok_count,
                    "skip_count": skip_count,
                    "fail_count": fail_count,
                    "miss_skip_count": miss_skip_count,
                    "soft_short_hist": soft_short_count,
                    "soft_deferred": soft_deferred,
                    "failed_codes": failed_codes[:200],
                    "started_at": started,
                    "finished_at": "",
                    "status": "incomplete",
                    "progress": 0,
                    "progress_total": len(universe),
                    "coverage_ok_ratio": round(coverage_ok_ratio, 4),
                    "truncated_left": truncated_left,
                    "runner_version": DAILY_SYNC_VERSION,
                    "trigger": source,
                    "primary_path": "ContextInfo.get_market_data_ex(1d)",
                    "primary_source": primary_source,
                    "time_sliced": bool(slice_mode),
                    "note": (
                        "first_date coverage below 85% or soft_short deferred; "
                        "after-hours dig"
                        if slice_mode
                        else "first_date coverage below 85%; soft short_hist retry"
                    ),
                    "pause_reason": (
                        "defer_after_hours" if slice_mode else "incomplete_retry"
                    ),
                },
            )
            if slice_mode:
                _arm_force_defer_after_hours()
            return False
        manifest = {
            "version": 1,
            "quality_version": QUALITY_VERSION,
            "backfill_start": bf_start_ymd,
            "min_valid_bars_required": MIN_BACKFILL_BARS,
            "sync_trade_date": end_s,
            "universe_count": len(universe) + miss_skip_count,
            "ok_count": ok_count,
            "skip_count": skip_count,
            "fail_count": fail_count,
            "miss_skip_count": miss_skip_count,
            "soft_short_hist": soft_short_count,
            "failed_codes": failed_codes[:200],
            "started_at": started,
            "finished_at": finished,
            "status": "completed",
            "coverage_ok_ratio": round(coverage_ok_ratio, 4),
            "runner_version": DAILY_SYNC_VERSION,
            "trigger": source,
            "primary_path": "ContextInfo.get_market_data_ex(1d)",
            "primary_source": primary_source,
            "miss_cache": _miss_cache_path(cache_dir),
            "xtdata_download": "on" if ENABLE_XTDATA_DOWNLOAD else "off",
            "xtdata_rpc": "off" if not ENABLE_XTDATA_DOWNLOAD else (
                "dead" if not _xtdata_rpc_alive() else "ok"
            ),
            "fallback": (
                "full_tick"
                if optional_rpc_skipped or not ENABLE_XTDATA_DOWNLOAD
                else ""
            ),
        }
        _save_manifest(manifest_path, manifest)
        _SYNC_DONE_END_DATE = end_s
        _clear_force_backfill_flag(cache_dir)
        print(
            "[日线同步] 完成 end=%s ok=%d skip=%d fail=%d 软短=%d miss_skip=%d primary=%s start=%s min_bars~%d"
            % (
                end_s,
                ok_count,
                skip_count,
                fail_count,
                soft_short_count,
                miss_skip_count,
                primary_source or "-",
                bf_start_ymd,
                MIN_BACKFILL_BARS,
            )
        )
        # ??????????????????? completed??
        try:
            fail_thr = max(50, int(len(universe) * 0.05))
            if fail_count >= fail_thr:
                try:
                    import ant_server_chan as sct
                except ImportError:
                    import qmt_builtin.ant_server_chan as sct
                sct.notify_alert(
                    "?????????????",
                    "??=%s\nok=%d skip=%d fail=%d/%d\nprimary=%s\n"
                    "?????QMT??????????/????????????"
                    % (
                        end_s,
                        ok_count,
                        skip_count,
                        fail_count,
                        len(universe),
                        primary_source or "-",
                    ),
                    alert_key="daily_sync_high_fail_%s" % end_s,
                    cooldown_sec=3600,
                )
        except Exception:
            pass
        return True
    except Exception as e:
        print("[日线同步] 异常: %s" % e)
        try:
            _save_manifest(
                manifest_path,
                {
                    "version": 1,
                    "sync_trade_date": end_s,
                    "universe_count": len(universe),
                    "ok_count": ok_count,
                    "skip_count": skip_count,
                    "fail_count": fail_count,
                    "failed_codes": failed_codes[:200],
                    "started_at": started,
                    "finished_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                    "status": "failed",
                    "error": str(e),
                    "runner_version": DAILY_SYNC_VERSION,
                    "trigger": source,
                    "primary_path": "ContextInfo.get_market_data_ex(1d)",
                },
            )
        except Exception:
            pass
        try:
            try:
                import ant_server_chan as sct
            except ImportError:
                import qmt_builtin.ant_server_chan as sct
            sct.notify_alert(
                "??????????",
                "??=%s\n%s\n?????QMT???????????????"
                % (end_s, e),
                alert_key="daily_sync_failed_%s" % end_s,
                cooldown_sec=3600,
            )
        except Exception:
            pass
        return False
    finally:
        try:
            _miss_cache_save(cache_dir, force=True)
        except Exception:
            pass
        _SYNC_RUNNING = False


def startup_catch_up(ContextInfo=None) -> bool:
    """启动时不追赶全量；正式日线见 15:35 定时 + 按需补齐。"""
    print("[日线同步] 启动跳过追赶；正式同步见 15:35 定时 + 按需补齐")
    return False


def daily_bar_sync(ContextInfo):
    """15:35 定时日线同步；成功后可串行触发 tick 全量 / 盘后量能。"""
    ok = run_catch_up_sync(ContextInfo, source="timer")
    # ???????????????/???????????????????????????
    if ok or _daily_gate_open_for_tick():
        _schedule_tick_pipeline()
    return ok


def _daily_gate_open_for_tick() -> bool:
    """??????????????????????????????? ?? ???????? tick ?????"""
    global _SYNC_RUNNING
    if _SYNC_RUNNING:
        return False
    try:
        xtdata = _load_xtdata()
    except Exception:
        return False
    now = datetime.now()
    if (now.hour, now.minute) < (SYNC_HOUR, SYNC_MINUTE):
        return False
    end_d = _resolve_sync_end_date(xtdata, now)
    end_s = end_d.isoformat()
    if _SYNC_DONE_END_DATE == end_s:
        return True
    _, _, _, manifest_path = _data_paths()
    manifest = _load_manifest(manifest_path)
    if _already_synced_to_end(manifest, end_d):
        return True
    # ????????????? tick/????
    if (
        str(manifest.get("status") or "") == "failed"
        and str(manifest.get("sync_trade_date") or "") == end_s
    ):
        return True
    return False


def _schedule_tick_pipeline() -> None:
    """??????????? tick?????????????????????"""
    global _TICK_CHAIN_DUE_AT
    delay = max(0, int(TICK_CHAIN_DELAY_SEC))
    now = time.time()
    already = bool(_TICK_CHAIN_DUE_AT) and now < float(_TICK_CHAIN_DUE_AT)
    _TICK_CHAIN_DUE_AT = now + float(delay)
    if already:
        return
    due = datetime.fromtimestamp(_TICK_CHAIN_DUE_AT).strftime("%H:%M:%S")
    print(
        "[日线同步] 分笔流水线已安排 %ds 后（约 %s，版本=%s）"
        % (delay, due, DAILY_SYNC_VERSION)
    )


def _tick_chain_delay_ready() -> bool:
    """?????????????????? False????????????????"""
    global _TICK_CHAIN_DUE_AT, _TICK_CHAIN_WAIT_LOG_TS
    if not _TICK_CHAIN_DUE_AT:
        return True
    now = time.time()
    if now >= float(_TICK_CHAIN_DUE_AT):
        _TICK_CHAIN_DUE_AT = 0.0
        return True
    if now - float(_TICK_CHAIN_WAIT_LOG_TS) >= 60.0:
        _TICK_CHAIN_WAIT_LOG_TS = now
        left = int(float(_TICK_CHAIN_DUE_AT) - now)
        print("[日线同步] FORCE 分片空闲中，剩余 %ds 后再跑" % left)
    return False


_TICK_FULL_SYNC_MTIME = 0.0
_SECTOR_SYNC_MTIME = 0.0


def _load_tick_full_sync_runner():
    """????? mtime ??????????? deploy ???????????? tick ????empty universe????"""
    global _TICK_FULL_SYNC_MTIME
    import importlib

    try:
        from ant_qmt_paths import QMT_BUILTIN_DIR
    except Exception:
        try:
            from qmt_builtin.ant_qmt_paths import QMT_BUILTIN_DIR
        except Exception:
            QMT_BUILTIN_DIR = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(str(QMT_BUILTIN_DIR), "ant_tick_full_sync_runner.py")
    try:
        mtime = os.path.getmtime(path) if os.path.isfile(path) else 0.0
    except Exception:
        mtime = 0.0
    try:
        import ant_tick_full_sync_runner as tick_full
    except ImportError:
        import qmt_builtin.ant_tick_full_sync_runner as tick_full
    if _TICK_FULL_SYNC_MTIME != mtime:
        tick_full = importlib.reload(tick_full)
        _TICK_FULL_SYNC_MTIME = mtime
        print(
            "[日线同步] 分笔模块已重载 版本=%s"
            % getattr(tick_full, "TICK_FULL_SYNC_VERSION", "?")
        )
    return tick_full


def _load_sector_sync_runner():
    """?? mtime ???????????????????????????? download_sector_data??"""
    global _SECTOR_SYNC_MTIME
    import importlib

    try:
        from ant_qmt_paths import QMT_BUILTIN_DIR
    except Exception:
        try:
            from qmt_builtin.ant_qmt_paths import QMT_BUILTIN_DIR
        except Exception:
            QMT_BUILTIN_DIR = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(str(QMT_BUILTIN_DIR), "ant_sector_sync_runner.py")
    try:
        mtime = os.path.getmtime(path) if os.path.isfile(path) else 0.0
    except Exception:
        mtime = 0.0
    try:
        import ant_sector_sync_runner as sector
    except ImportError:
        import qmt_builtin.ant_sector_sync_runner as sector
    if _SECTOR_SYNC_MTIME != mtime:
        sector = importlib.reload(sector)
        _SECTOR_SYNC_MTIME = mtime
        print(
            "[日线同步] 板块模块已重载 版本=%s"
            % getattr(sector, "SECTOR_SYNC_VERSION", "?")
        )
    return sector


def _chain_tick_pipeline(ContextInfo) -> None:
    """?????????tick ???? ?? ?????tick ????????????????"""
    try:
        tick_full = _load_tick_full_sync_runner()
        print("[日线同步] 已串行启动 tick 全量 + 盘后量能")
        if hasattr(tick_full, "run_post_daily_pipeline"):
            tick_full.run_post_daily_pipeline(ContextInfo)
        else:
            tick_full.tick_full_sync(ContextInfo)
    except Exception as e:
        print("[日线同步] 清除 FORCE 回填标记失败: %s" % e)


def maybe_catch_up_after_hours_pipeline(ContextInfo=None) -> bool:
    """????????????? tick ? ?? ? ???"""
    now = datetime.now()
    if (now.hour, now.minute) < (SYNC_HOUR, SYNC_MINUTE):
        return False
    if _SYNC_RUNNING:
        return False

    # ??????????????????? tick ??? manual_request ???
    try:
        xtdata = _load_xtdata()
    except Exception:
        xtdata = None
    try:
        if not _is_tradeday(xtdata, now.date()):
            return False
    except Exception:
        if now.weekday() >= 5:
            return False

    # Completed gate can hide codes unblocked after false-delisted miss clear.
    # Reopen once/day for today-incremental (never arms FORCE).
    try:
        end_d = _resolve_sync_end_date(xtdata, now)
        _maybe_reopen_completed_for_stale_bars(end_d)
    except Exception:
        pass

    # ??? tick/??/?????????? pipeline
    try:
        day = now.strftime("%Y%m%d")
        tick_full = _load_tick_full_sync_runner()
        try:
            import ant_after_hours_rank_runner as ar
        except ImportError:
            import qmt_builtin.ant_after_hours_rank_runner as ar
        sector = _load_sector_sync_runner()
        tick_done = False
        if hasattr(tick_full, "_day_already_done"):
            tick_done = bool(tick_full._day_already_done(day))
        # tick ABORT ?????? chain??? miss_cache ???
        if (not tick_done) and hasattr(tick_full, "_abort_hold_active"):
            try:
                held, _reason = tick_full._abort_hold_active(day)
                if held:
                    return False
            except Exception:
                pass
        rank_done = bool(hasattr(ar, "is_rank_done") and ar.is_rank_done(day))
        sector_done = bool(
            hasattr(sector, "is_synced_today") and sector.is_synced_today()
        )
        if (
            _daily_gate_open_for_tick()
            and tick_done
            and rank_done
            and sector_done
        ):
            return True
    except Exception:
        pass

    if not _daily_gate_open_for_tick():
        run_catch_up_sync(ContextInfo, source="pipeline_catchup")
        if _daily_gate_open_for_tick():
            # ???????? tick ?
            _schedule_tick_pipeline()

    if not _daily_gate_open_for_tick():
        return False

    # ????????????????????
    if not _tick_chain_delay_ready():
        return False

    _chain_tick_pipeline(ContextInfo)
    return True


def register_daily_sync_timer(ContextInfo) -> None:
    try:
        ContextInfo.run_time(
            "daily_bar_sync",
            "1d",
            "2024-01-01 %02d:%02d:00" % (SYNC_HOUR, SYNC_MINUTE),
            "SH",
        )
        print(
            "[日线同步] 定时器已注册 %02d:%02d （随后串联 分笔→盘后排名→板块）"
            % (SYNC_HOUR, SYNC_MINUTE)
        )
    except Exception as e:
        print("[日线同步] run_time 注册失败: %s" % e)


_ON_DEMAND_BUSY = False
_ON_DEMAND_DEFER_LOG_TS = 0.0
_ON_DEMAND_STALL_LOG_TS = 0.0
_ON_DEMAND_SKIP_LOG_TS = 0.0
_ON_DEMAND_DAILY_LAST_TS = 0.0
_AUDIT_POOL_LAST_TS = 0.0
AUDIT_POOL_INTERVAL_SEC = 120.0


def _pool_csv_ready(
    code: str,
    cache_dir: str,
    through_date: Optional[date] = None,
    xtdata=None,
) -> bool:
    end_d = through_date or date.today()
    check_d = _pool_daily_write_end_date(xtdata, end_d)
    csv_path = os.path.join(cache_dir, code + ".csv")
    rows = _read_csv_rows(csv_path)
    _remove_bars_after_date(rows, check_d)
    return _pool_bars_quality_ok(_rows_to_bars(rows), check_d, xtdata=xtdata, code=code)


def _purge_bad_pool_csv(code: str, cache_dir: str, end_d: date) -> bool:
    """???????????/?????? CSV????????????? hist ?????????"""
    if _pool_csv_ready(code, cache_dir, end_d):
        return False
    csv_path = os.path.join(cache_dir, code + ".csv")
    if not os.path.isfile(csv_path):
        return False
    rows = _read_csv_rows(csv_path)
    check_d = _pool_daily_write_end_date(None, end_d)
    _remove_bars_after_date(rows, check_d)
    reason = _pool_storage_reason(
        _rows_to_bars(rows), check_d, list_date=_csv_list_date(code)
    )
    # ????????????????????????????????????/FORCE???????????
    if reason and str(reason).startswith("hist_"):
        last_d = None
        try:
            if rows:
                last_d = datetime.strptime(max(rows.keys())[:10], "%Y-%m-%d").date()
        except ValueError:
            last_d = None
        if (
            last_d is not None
            and last_d >= check_d - timedelta(days=POOL_MAX_DATE_LAG_DAYS)
            and _count_valid_rows(rows) >= POOL_INTRADAY_MIN_BARS
        ):
            return False
    try:
        os.remove(csv_path)
        print("[日线同步] 审计剔除 csv %s reason=%s" % (code, reason or ""))
        return True
    except Exception as e:
        print("[日线同步] 审计剔除 csv 异常 %s: %s" % (code, e))
        return False


def audit_pool_daily_cache(
    codes: List[str],
    cache_dir: str,
    *,
    through_date: Optional[date] = None,
) -> int:
    """??????? CSV ???????????????????????????? pending??"""
    try:
        from ant_data_sync_request import (
            load_requests,
            save_requests,
            _now_iso,
            _to_full_stock_code,
        )
    except ImportError:
        try:
            from qmt_builtin.ant_data_sync_request import (
                load_requests,
                save_requests,
                _now_iso,
                _to_full_stock_code,
            )
        except ImportError:
            return 0

    end_d = through_date or date.today()
    end_s = end_d.isoformat()
    data = load_requests()
    daily = data.setdefault("daily", {})
    fixed = 0
    for raw in codes or []:
        full = _to_full_stock_code(str(raw or ""))
        if not full:
            continue
        csv_path = os.path.join(cache_dir, full + ".csv")
        check_d = _pool_daily_write_end_date(None, end_d)
        _pool_strip_forming_csv(csv_path, check_d)
        if _pool_csv_ready(full, cache_dir, end_d):
            continue
        prev = daily.get(full) if isinstance(daily.get(full), dict) else {}
        prev_status = str(prev.get("status") or "")
        if prev_status == "short_history":
            try:
                from ant_data_sync_request import _short_history_stale
            except ImportError:
                from qmt_builtin.ant_data_sync_request import _short_history_stale
            if not _short_history_stale(prev):
                continue
        _purge_bad_pool_csv(full, cache_dir, end_d)
        if prev_status == "pending":
            continue
        daily[full] = {
            "through_date": end_s,
            "requested_at": _now_iso(),
            "status": "pending",
            "retries": int(prev.get("retries") or 0),
        }
        fixed += 1
    if fixed:
        save_requests(data)
        print(
            "[日线同步] audit pool daily: requeue %d codes (through=%s)"
            % (fixed, end_s)
        )
    return fixed


def requeue_priority_daily_requests(
    codes: List[str],
    cache_dir: str,
    *,
    through_date: Optional[date] = None,
) -> int:
    """?????? daily_cache ????? pending???? retries ?????????????????????"""
    try:
        from ant_data_sync_request import (
            MAX_RETRIES,
            load_requests,
            save_requests,
            _now_iso,
            _to_full_stock_code,
        )
    except ImportError:
        try:
            from qmt_builtin.ant_data_sync_request import (
                MAX_RETRIES,
                load_requests,
                save_requests,
                _now_iso,
                _to_full_stock_code,
            )
        except ImportError:
            return 0

    end_d = through_date or date.today()
    end_s = end_d.isoformat()
    data = load_requests()
    daily = data.setdefault("daily", {})
    requeued = 0
    marked_done = 0
    dirty = False
    for raw in codes or []:
        full = _to_full_stock_code(str(raw or ""))
        if not full:
            continue
        check_d = _pool_daily_write_end_date(None, end_d)
        _pool_strip_forming_csv(os.path.join(cache_dir, full + ".csv"), check_d)
        if _pool_csv_ready(full, cache_dir, end_d):
            meta = daily.get(full) if isinstance(daily.get(full), dict) else {}
            if meta and str(meta.get("status") or "") != "done":
                meta = dict(meta)
                meta["status"] = "done"
                meta["updated_at"] = _now_iso()
                daily[full] = meta
                marked_done += 1
                dirty = True
            continue
        prev = daily.get(full) if isinstance(daily.get(full), dict) else {}
        prev_retries = int(prev.get("retries") or 0)
        prev_status = str(prev.get("status") or "")
        if prev_status == "short_history":
            try:
                from ant_data_sync_request import _short_history_stale
            except ImportError:
                from qmt_builtin.ant_data_sync_request import _short_history_stale
            if not _short_history_stale(prev):
                continue
        elif prev_status == "pending" and prev_retries < MAX_RETRIES:
            # Already queued; do not reset retries every periodic tick.
            continue
        elif prev_status == "done":
            # done ??????????????????????????????? pending ?????
            if _pool_attempt_within(prev, POOL_REQUEUE_COOLDOWN_SEC):
                continue
        elif prev_status in ("failed", "pending") or prev_retries >= MAX_RETRIES:
            # Cool down before resetting failed/exhausted ?? pending retries=0.
            # Without this, pool_invalid loops reset every ~few seconds and
            # starve quotes (~8s ContextInfo each attempt).
            if _pool_attempt_within(prev, POOL_REQUEUE_COOLDOWN_SEC):
                continue
        daily[full] = {
            "through_date": end_s,
            "requested_at": _now_iso(),
            "status": "pending",
            "retries": 0,
            "requeued_from": prev_status or "none",
            "updated_at": _now_iso(),
        }
        requeued += 1
        dirty = True
    if dirty:
        save_requests(data)
    if requeued:
        print(
            "[日线同步] requeue pool daily: %d codes (through=%s)"
            % (requeued, end_s)
        )
    return requeued + marked_done


def _priority_sync_codes() -> set:
    """rules_armed ???????/????/??????? ?? ????????????????????????"""
    try:
        from ant_rules_io import RULES_ARMED_PATH, collect_subscribe_codes, load_rules_armed
    except ImportError:
        try:
            from qmt_builtin.ant_rules_io import (
                RULES_ARMED_PATH,
                collect_subscribe_codes,
                load_rules_armed,
            )
        except ImportError:
            return set()
    rules = load_rules_armed(RULES_ARMED_PATH)
    return set(
        collect_subscribe_codes(
            rules.get("tasks") or [],
            rules.get("watch_codes"),
            rules.get("strategy_pool_watch"),
        )
    )


def _ensure_repo_utils():
    root = PROJECT_ROOT.rstrip("\\/")
    if root not in sys.path:
        sys.path.insert(0, root)


def _sync_tick_one_day(xtdata, code_6: str, trade_date, ContextInfo=None) -> Tuple[str, Optional[str]]:
    """?? QMT ??????? tick ?????? data/ticks/??"""
    try:
        from ant_tick_cache_io import (
            coerce_tick_dataframe,
            fetch_tick_from_qmt,
            normalize_tick_dataframe,
            write_tick_cache,
        )
    except ImportError:
        try:
            from qmt_builtin.ant_tick_cache_io import (
                coerce_tick_dataframe,
                fetch_tick_from_qmt,
                normalize_tick_dataframe,
                write_tick_cache,
            )
        except Exception as e:
            return "fail", "import_tick_cache:%s" % e

    raw = fetch_tick_from_qmt(code_6, trade_date)
    data = normalize_tick_dataframe(raw)
    data = coerce_tick_dataframe(data) if data is not None else None
    if data is None or len(data) == 0:
        return "fail", "empty_tick"
    if not write_tick_cache(code_6, trade_date, data):
        return "fail", "write_tick_cache"
    return "ok", None


def _daily_full_dir() -> str:
    base, _, _, _ = _data_paths()
    path = os.path.join(base, "data", "daily_full")
    if not os.path.isdir(path):
        try:
            os.makedirs(path)
        except Exception:
            pass
    return path


def _full_history_request(meta: Optional[Dict[str, Any]]) -> bool:
    return str((meta or {}).get("mode") or "").strip().lower() == "full_history"


def _parse_meta_from_date(meta: Optional[Dict[str, Any]]) -> date:
    raw = str((meta or {}).get("from_date") or "").strip()
    if raw:
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
        try:
            return datetime.strptime(raw.replace("-", "")[:8], "%Y%m%d").date()
        except ValueError:
            pass
    return date(1991, 1, 2)


def _ipo_full_coverage_ok(
    earliest: Optional[date],
    start_d: date,
    list_date: Optional[date],
    bar_count: int,
) -> bool:
    """全量是否已挖到上市/请求起点附近（不能把近 2 年当全量）。"""
    if earliest is None or int(bar_count or 0) < 4:
        return False
    target = list_date if list_date is not None else start_d
    slack = timedelta(days=45)
    return earliest <= target + slack


def _sync_one_code_ipo_full(
    xtdata,
    code: str,
    end_d: date,
    ContextInfo=None,
    meta: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[str], int]:
    """岳教授新建票：经大 QMT 内置 download 拉上市以来全量 1d，写入 data/daily_full/。

    ContextInfo 一次取区间常只返回本地近 2 年；需按年片 download 后再 get 并合并。
    不裁剪到 BACKFILL_START(2025-01-01)；不走外部 MiniQMT xtdata/58610。
    """
    end_d = _pool_daily_write_end_date(xtdata, end_d)
    list_date = _instrument_list_date(ContextInfo, xtdata, code)
    start_d = _parse_meta_from_date(meta)
    if list_date is not None and list_date > start_d:
        start_d = list_date
    if start_d > end_d:
        return "fail", "bad_range", 0

    _ensure_builtin_download_bound()
    merged: Dict[str, Dict[str, Any]] = {}
    src_parts: List[str] = []
    dl_ok_total = 0

    def _merge_fetched(bars_in: List[Dict[str, Any]], tag: str) -> None:
        n0 = len(merged)
        for b in _sanitize_bars(bars_in or []):
            d = str(b.get("date") or "")[:10]
            if d:
                merged[d] = b
        if len(merged) > n0:
            src_parts.append("%s+%d" % (tag, len(merged) - n0))

    # 1) 先整段 download + 一次 range get（可能仍只有近端本地窗）
    n_ok, dl_detail = _download_1d_via_builtin([code], start_d, end_d)
    dl_ok_total += int(n_ok or 0)
    if n_ok > 0 and FULL_POST_DOWNLOAD_SLEEP_SEC > 0:
        time.sleep(float(FULL_POST_DOWNLOAD_SLEEP_SEC))
    batch_map, src0 = _batch_fetch_1d_bars(
        ContextInfo,
        xtdata,
        [code],
        start_d,
        end_d,
        prefer_count=-1,
        quality_min=1,
    )
    _merge_fetched(batch_map.get(code) or [], src0 or "ctx0")

    # 2) 若起点仍偏晚：自 start 起按年片 download+get，向前拼到覆盖上市日
    #    （QMT 本地窗常见只吐近 ~600 根，必须切片补更早）
    chunk_days = 370
    max_slices = 40
    for slice_i in range(max_slices):
        bars_now = _pool_bars_sorted(merged)
        earliest = _bars_earliest_date(bars_now)
        if _ipo_full_coverage_ok(earliest, start_d, list_date, len(bars_now)):
            break
        # 下一片：覆盖「当前最早日之前」一年，或从 start 顺序推进尚未覆盖的区间
        if earliest is None:
            dig_start = start_d
            dig_end = min(start_d + timedelta(days=chunk_days - 1), end_d)
        else:
            dig_end = earliest - timedelta(days=1)
            if dig_end < start_d:
                break
            dig_start = max(start_d, dig_end - timedelta(days=chunk_days - 1))
        if dig_start > dig_end:
            break
        n_ok2, dl2 = _download_1d_via_builtin([code], dig_start, dig_end)
        dl_ok_total += int(n_ok2 or 0)
        if n_ok2 > 0 and FULL_POST_DOWNLOAD_SLEEP_SEC > 0:
            time.sleep(float(FULL_POST_DOWNLOAD_SLEEP_SEC))
        batch2, src2 = _batch_fetch_1d_bars(
            ContextInfo,
            xtdata,
            [code],
            dig_start,
            dig_end,
            prefer_count=-1,
            quality_min=1,
        )
        before = len(merged)
        _merge_fetched(batch2.get(code) or [], "slice%d_%s" % (slice_i, src2 or "ctx"))
        if len(merged) <= before and not (batch2.get(code) or []):
            # 无进展：再试一次 dig_start..earliest 整段
            if earliest is not None and dig_start < earliest:
                n_ok3, _dl3 = _download_1d_via_builtin([code], dig_start, earliest)
                dl_ok_total += int(n_ok3 or 0)
                batch3, src3 = _batch_fetch_1d_bars(
                    ContextInfo,
                    xtdata,
                    [code],
                    dig_start,
                    earliest,
                    prefer_count=-1,
                    quality_min=1,
                )
                before2 = len(merged)
                _merge_fetched(
                    batch3.get(code) or [], "retry_%s" % (src3 or "ctx")
                )
                if len(merged) <= before2:
                    src_parts.append("stall_%s" % (dl2 or "empty"))
                    break

    bars = _sanitize_bars(
        _filter_bars_by_range(_pool_bars_sorted(merged), start_d, end_d)
    )
    if len(bars) < 4:
        return (
            "fail",
            "empty_full_%s_dl%d" % (("+".join(src_parts[:4]) or "none"), dl_ok_total),
            0,
        )

    rows: Dict[str, Dict[str, Any]] = {}
    _merge_bars(rows, bars)
    out_path = os.path.join(_daily_full_dir(), code + ".csv")
    try:
        _write_csv_atomic(out_path, rows)
    except Exception as e:
        return "fail", "write_full_%s" % type(e).__name__, 0
    try:
        _mirror_qfq_full_csv(code, start_d, end_d, ContextInfo=ContextInfo, xtdata=xtdata)
    except Exception as e:
        print("[按需同步] qfq full mirror %s: %s" % (code, e))

    valid_n = _count_valid_rows(rows)
    earliest = _rows_earliest_date(rows)
    last_d = None
    try:
        last_d = datetime.strptime(max(rows.keys())[:10], "%Y-%m-%d").date()
    except Exception:
        last_d = None
    covered = _ipo_full_coverage_ok(earliest, start_d, list_date, valid_n)
    print(
        "[按需同步] 全量日线 %s n=%d first=%s last=%s list=%s covered=%s src=%s dl_ok=%d"
        % (
            code,
            valid_n,
            earliest.isoformat() if earliest else "-",
            last_d.isoformat() if last_d else "-",
            list_date.isoformat() if list_date else "-",
            "Y" if covered else "N",
            "+".join(src_parts[:6]) or "-",
            dl_ok_total,
        )
    )
    if last_d is None or last_d < end_d - timedelta(days=POOL_MAX_DATE_LAG_DAYS):
        return "fail", "full_lag_%s" % (last_d.isoformat() if last_d else "none"), valid_n
    if not covered:
        # 仍写入部分结果，但标记失败以便重试（不把近 2 年当全量成功）
        return (
            "fail",
            "full_truncated_first_%s" % (earliest.isoformat() if earliest else "none"),
            valid_n,
        )
    return "ok", None, valid_n


def _daily_on_demand_satisfied(
    code: str,
    through_d: date,
    cache_dir: str,
    *,
    intraday: bool,
    priority: set,
) -> bool:
    """?????????? pending???????????????????????????????????? xtdata??"""
    if intraday and code in priority:
        return _pool_csv_ready(code, cache_dir, through_d)
    csv_path = os.path.join(cache_dir, code + ".csv")
    return _csv_ready_at_end(csv_path, through_d)


def _tick_on_demand_satisfied(code_6: str, trade_d: date) -> bool:
    try:
        from ant_tick_cache_io import tick_cache_file_ready
    except ImportError:
        try:
            from qmt_builtin.ant_tick_cache_io import tick_cache_file_ready
        except ImportError:
            return False
    try:
        return bool(tick_cache_file_ready(code_6, trade_d))
    except Exception:
        return False


def _run_on_demand_batch(
    ContextInfo,
    pending_daily: List[Tuple[str, date, Dict[str, Any]]],
    pending_tick: List[Tuple[str, date, Dict[str, Any]]],
    intraday: bool,
    priority: set,
) -> int:
    global _ON_DEMAND_BUSY, _ON_DEMAND_DAILY_LAST_TS
    handled = 0
    try:
        from ant_data_sync_request import (
            mark_daily_done,
            mark_daily_failed,
            mark_tick_done,
            mark_tick_failed,
        )
    except ImportError:
        try:
            from qmt_builtin.ant_data_sync_request import (
                mark_daily_done,
                mark_daily_failed,
                mark_tick_done,
                mark_tick_failed,
            )
        except ImportError as e:
            print("[日线同步] 按需处理失败: %s" % e)
            return 0

    _, cache_dir, _, _ = _data_paths()

    daily_need_sync: List[Tuple[str, date, Dict[str, Any]]] = []
    daily_cache_hit = 0
    for code, through_d, meta in pending_daily:
        # 全量日线：rolling daily_cache 命中不算完成
        if _full_history_request(meta):
            full_path = os.path.join(_daily_full_dir(), code + ".csv")
            rows_f = _read_csv_rows(full_path)
            n_f = _count_valid_rows(rows_f)
            first_f = _rows_earliest_date(rows_f)
            last_f = None
            try:
                last_f = datetime.strptime(max(rows_f.keys())[:10], "%Y-%m-%d").date()
            except Exception:
                last_f = None
            start_meta = _parse_meta_from_date(meta)
            list_d = _csv_list_date(code)
            if (
                n_f >= 4
                and last_f is not None
                and last_f >= through_d - timedelta(days=POOL_MAX_DATE_LAG_DAYS)
                and _ipo_full_coverage_ok(first_f, start_meta, list_d, n_f)
            ):
                mark_daily_done(code, through_d)
                handled += 1
                daily_cache_hit += 1
            else:
                daily_need_sync.append((code, through_d, meta))
            continue
        if _daily_on_demand_satisfied(
            code, through_d, cache_dir, intraday=intraday, priority=priority
        ):
            mark_daily_done(code, through_d)
            handled += 1
            daily_cache_hit += 1
        else:
            daily_need_sync.append((code, through_d, meta))

    tick_need_sync: List[Tuple[str, date, Dict[str, Any]]] = []
    tick_cache_hit = 0
    for code_6, trade_d, meta in pending_tick:
        if _tick_on_demand_satisfied(code_6, trade_d):
            mark_tick_done(code_6, trade_d)
            handled += 1
            tick_cache_hit += 1
        else:
            tick_need_sync.append((code_6, trade_d, meta))

    if daily_cache_hit or tick_cache_hit:
        print(
            "[按需同步] 缓存命中 daily=%d tick=%d"
            % (daily_cache_hit, tick_cache_hit)
        )

    if not daily_need_sync and not tick_need_sync:
        return handled

    try:
        xtdata = _load_xtdata()
    except Exception as e:
        print("[日线同步] xtdata 不可用: %s" % e)
        return handled

    # ???????????????????? download_history_data???????? bind??
    _ensure_builtin_download_bound()

    synced_ok = 0
    synced_fail = 0
    for code, through_d, _meta in daily_need_sync:
        if _full_history_request(_meta):
            status, reason, _rows = _sync_one_code_ipo_full(
                xtdata,
                code,
                through_d,
                ContextInfo=ContextInfo,
                meta=_meta,
            )
            if status == "ok":
                mark_daily_done(code, through_d)
                handled += 1
                synced_ok += 1
            else:
                mark_daily_failed(code, through_d, reason or status)
                synced_fail += 1
                print("[按需同步] 全量失败 %s: %s" % (code, reason or status))
            continue
        miss = _miss_cache_active(code, through_d, cache_dir)
        if miss is not None:
            miss_reason = str(miss.get("reason") or "skip")
            # tick ???? empty_history ???????????????????000566 ????????????
            if miss_reason == "delisted":
                csv_p = os.path.join(cache_dir, code + ".csv")
                last_d = _last_date_in_csv(csv_p)
                if last_d is not None and (through_d - last_d).days <= 15:
                    _miss_cache_clear(code, cache_dir)
                    print(
                        "[按需同步] 已清除误判退市 %s last_bar=%s"
                        % (code, last_d.isoformat())
                    )
                else:
                    reason = "miss_cache_%s" % miss_reason
                    mark_daily_failed(code, through_d, reason)
                    synced_fail += 1
                    print("[日线同步] miss 记入 %s: %s" % (code, reason))
                    continue
            if miss_reason in ("today_halt", "suspended"):
                csv_p = os.path.join(cache_dir, code + ".csv")
                if _count_valid_rows(_read_csv_rows(csv_p)) >= MIN_STORAGE_BARS:
                    reason = "miss_cache_%s" % miss_reason
                    mark_daily_failed(code, through_d, reason)
                    synced_fail += 1
                    print("[日线同步] miss 记入 %s: %s" % (code, reason))
                    continue
            # empty_history / local_miss / invalid_0 / no_ctx??????? miss ???????????
            if miss_reason in (
                "empty_history",
                "local_miss",
                "invalid_0",
                "no_ctx",
            ):
                _miss_cache_clear(code, cache_dir)
                print(
                    "[按需同步] 忽略软 miss %s reason=%s → 重试日线"
                    % (code, miss_reason)
                )
        sync_through = through_d
        if intraday:
            # ??????????????????????????????????????? K???????????????
            sync_through = _pool_daily_write_end_date(xtdata, through_d)
            status, reason, _rows = _sync_one_code(
                xtdata,
                cache_dir,
                code,
                sync_through,
                ContextInfo=ContextInfo,
                sync_source="on_demand_intraday",
            )
            _ON_DEMAND_DAILY_LAST_TS = time.time()
        else:
            sync_fn = (
                _sync_one_code_pool if code in priority else _sync_one_code
            )
            status, reason, _rows = sync_fn(
                xtdata, cache_dir, code, through_d, ContextInfo=ContextInfo
            )
        if status == "ok":
            mark_daily_done(code, through_d)
            _miss_cache_clear(code, cache_dir)
            handled += 1
            synced_ok += 1
        elif status == "skip":
            if reason == "short_history_today":
                pass
            elif reason in ("today_halt",):
                mark_daily_failed(code, through_d, reason or status)
                synced_fail += 1
            else:
                mark_daily_done(code, through_d)
                handled += 1
                synced_ok += 1
        else:
            miss_r = _miss_reason_from_fail(reason)
            if miss_r:
                _miss_cache_put(code, miss_r, through_d, cache_dir)
            mark_daily_failed(code, through_d, reason or status)
            synced_fail += 1
            print(
                "[日线同步] 失败 %s: %s" % (code, reason or status)
            )
    _miss_cache_save(cache_dir)

    for code_6, trade_d, _meta in tick_need_sync:
        status, reason = _sync_tick_one_day(
            xtdata, code_6, trade_d, ContextInfo=ContextInfo
        )
        if status == "ok":
            mark_tick_done(code_6, trade_d)
            handled += 1
            synced_ok += 1
            print(
                "[按需同步] 分笔成功 %s %s%Y%m%d"
                % (code_6, trade_d.strftime(""))
            )
        else:
            mark_tick_failed(code_6, trade_d, reason or status)
            synced_fail += 1
            print(
                "[按需同步] 分笔失败 %s %s: %s%Y%m%d"
                % (code_6, trade_d.strftime(""), reason or status)
            )

    if synced_ok or synced_fail:
        print(
            "[按需同步] 已同步 ok=%d fail=%d"
            % (synced_ok, synced_fail)
        )
    return handled


def _pool_attempt_within(meta: Dict[str, Any], cooldown_sec: float) -> bool:
    """True if last_attempt/updated_at is within cooldown_sec (skip requeue)."""
    la = str(meta.get("last_attempt_at") or meta.get("updated_at") or "")
    if not la:
        return False
    try:
        last_dt = datetime.strptime(la[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False
    return (datetime.now() - last_dt).total_seconds() < float(cooldown_sec)


def process_on_demand_sync_requests(
    ContextInfo=None,
    *,
    daily_limit: int = 8,
    tick_limit: int = 4,
) -> int:
    """?????????????????????????????????????????? download_history_data????????????????????"""
    try:
        return _process_on_demand_sync_requests_body(
            ContextInfo, daily_limit=daily_limit, tick_limit=tick_limit
        )
    except KeyboardInterrupt:
        return 0


def _process_on_demand_sync_requests_body(
    ContextInfo=None,
    *,
    daily_limit: int = 8,
    tick_limit: int = 4,
) -> int:
    global _ON_DEMAND_BUSY, _ON_DEMAND_DEFER_LOG_TS, _ON_DEMAND_STALL_LOG_TS, _ON_DEMAND_SKIP_LOG_TS, _AUDIT_POOL_LAST_TS, _FORCE_SLICE_IDLE_UNTIL, _ON_DEMAND_DAILY_LAST_TS
    if _ON_DEMAND_BUSY or _SYNC_RUNNING:
        now_skip = time.time()
        if now_skip - _ON_DEMAND_SKIP_LOG_TS >= 60.0:
            reason = "sync_running" if _SYNC_RUNNING else "busy"
            print(
                "[按需同步] 跳过 (%s); 空闲时重试"
                % reason
            )
            _ON_DEMAND_SKIP_LOG_TS = now_skip
        return 0

    # Never stack on_demand with FORCE: honor short slice idle only.
    # Long parks (legacy defer_after_hours set idle until 15:35) must not block.
    now_skip = time.time()
    idle_until = float(_FORCE_SLICE_IDLE_UNTIL)
    if now_skip < idle_until:
        remain_idle = idle_until - now_skip
        if remain_idle > float(FORCE_INTRADAY_IDLE_SEC) * 5.0:
            # Stale multi-minute park from old defer_after_hours ? clear.
            _FORCE_SLICE_IDLE_UNTIL = 0.0
        else:
            if now_skip - _ON_DEMAND_SKIP_LOG_TS >= 60.0:
                print(
                    "[日线同步] 强制补数 空闲间隙 remain=%.0fs（行情补跑）"
                    % max(0.0, remain_idle)
                )
                _ON_DEMAND_SKIP_LOG_TS = now_skip
            return 0

    _, cache_dir, _, manifest_path = _data_paths()
    # No ON_DEMAND_PAUSE full-stop; cooldown + intraday limits throttle storms.
    # 连续竞价窗仍允许按需日线（10s/只），不因 FORCE/manifest 整段暂停。
    if (
        _in_intraday_blocking_window()
        and not _in_continuous_quote_watch()
        and (
            _force_backfill_requested(cache_dir)
            or _manifest_partial_running(_load_manifest(manifest_path))
        )
    ):
        if now_skip - _ON_DEMAND_SKIP_LOG_TS >= 60.0:
            print(
                "[按需同步] 跳过（强制/部分进行中; 行情优先）"
            )
            _ON_DEMAND_SKIP_LOG_TS = now_skip
        return 0

    intraday = _in_intraday_blocking_window()
    in_continuous = _in_continuous_quote_watch()
    priority = _priority_sync_codes() if intraday else set()
    if intraday:
        eff_tick_limit = 0 if in_continuous else 2
    else:
        eff_daily_limit = daily_limit
        eff_tick_limit = tick_limit

    if intraday and priority:
        now_ts = time.time()
        if now_ts - _AUDIT_POOL_LAST_TS >= AUDIT_POOL_INTERVAL_SEC:
            audit_pool_daily_cache(sorted(priority), cache_dir)
            _AUDIT_POOL_LAST_TS = now_ts
        requeue_priority_daily_requests(sorted(priority), cache_dir)

    try:
        from ant_data_sync_request import list_pending_daily, list_pending_ticks
    except ImportError:
        try:
            from qmt_builtin.ant_data_sync_request import (
                list_pending_daily,
                list_pending_ticks,
            )
        except Exception as e:
            print("[日线同步] 按需处理失败: %s" % e)
            return 0

    # ??????????k?????????????????????????????????
    scan_n = 120 if intraday else 50
    all_pending_daily = list_pending_daily(limit=scan_n)
    if intraday:
        # ???????????? 1 ?????????????????????????????????/??????
        now_ts = time.time()
        interval = float(
            CONTINUOUS_QUOTE_ON_DEMAND_DAILY_INTERVAL_SEC
            if in_continuous
            else INTRADAY_ON_DEMAND_DAILY_INTERVAL_SEC
        )
        can_fetch = (now_ts - float(_ON_DEMAND_DAILY_LAST_TS)) >= interval
        pri_items = [it for it in all_pending_daily if it[0] in priority]
        non_items = [it for it in all_pending_daily if it[0] not in priority]
        pending_daily = []
        if can_fetch:
            pick = (pri_items + non_items)[: int(INTRADAY_ON_DEMAND_DAILY_BATCH)]
            pending_daily = pick
            if pending_daily and (now_ts - _ON_DEMAND_DEFER_LOG_TS) >= 60.0:
                print(
                    "[按需同步] 盘中日线限速 %d只/%.0fs (pool=%d queue≈%d; 拉至上一交易日)"
                    % (
                        int(INTRADAY_ON_DEMAND_DAILY_BATCH),
                        interval,
                        len(priority),
                        len(all_pending_daily),
                    )
                )
                _ON_DEMAND_DEFER_LOG_TS = now_ts
        if now_ts - _ON_DEMAND_STALL_LOG_TS >= 120.0 and priority:
            # ???????????????????? CSV ???????????????
            missing = [
                c
                for c in sorted(priority)
                if not _pool_csv_tail_ready(c, cache_dir, date.today())
            ]
            if missing:
                print(
                    "[日线同步] 池内日线仍缺 %d 只 （等待按需队列）: %s"
                    % (
                        len(missing),
                        ", ".join(missing[:6])
                        + (" ..." if len(missing) > 6 else ""),
                    )
                )
                _ON_DEMAND_STALL_LOG_TS = now_ts
    else:
        pending_daily = all_pending_daily[:eff_daily_limit]
        if len(all_pending_daily) > eff_daily_limit:
            eff_daily_limit = min(15, max(eff_daily_limit, len(all_pending_daily) // 3 + 2))
            pending_daily = all_pending_daily[:eff_daily_limit]

    pending_tick = list_pending_ticks(limit=max(eff_tick_limit, 20))
    if len(pending_tick) > eff_tick_limit:
        eff_tick_limit = min(8, max(eff_tick_limit, len(pending_tick) // 4 + 1))
        pending_tick = pending_tick[:eff_tick_limit]

    if not pending_daily and not pending_tick:
        return 0

    print(
        "[按需同步] 开始 daily=%d tick=%d (queue daily=%d)"
        % (
            len(pending_daily),
            len(pending_tick),
            len(all_pending_daily),
        )
    )

    # ?????????? xtdata/ContextInfo???? tick ???????? QMT ??????
    _ON_DEMAND_BUSY = True
    try:
        return _run_on_demand_batch(
            ContextInfo,
            pending_daily,
            pending_tick,
            intraday,
            priority,
        )
    finally:
        _ON_DEMAND_BUSY = False
