#coding:gbk
"""QMT ???д data/data_sync_requests.json?????????? Python 3.6+???? future annotations????"""
import json
import os
import re
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    from ant_qmt_paths import PROJECT_ROOT
except ImportError:
    from qmt_builtin.ant_qmt_paths import PROJECT_ROOT

MAX_RETRIES = 3
SHORT_HISTORY_MIN_VOL = 120
# Bars below quality floor (??MA120+buffer / FORCE min): defer same-day retries.
# Avoids pool_invalid_321_hist_321 soft-retry storms that block the main thread.
SHORT_HISTORY_QUALITY_FLOOR = 380
# pool_invalid_13_vol_13 ?? pool_invalid_13_hist_13????????????? K??
_SHORT_HISTORY_RE = re.compile(r"pool_invalid_(\d+)_(?:vol|hist)_(\d+)")
REQUESTS_PATH = os.path.join(PROJECT_ROOT.rstrip("\\/"), "data", "data_sync_requests.json")
_PENDING_ROTATE_OFFSET = 0


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    folder = os.path.dirname(path) or "."
    if not os.path.isdir(folder):
        os.makedirs(folder)
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


def _parse_date(raw: Any) -> Optional[date]:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _to_full_stock_code(stock_code: str) -> str:
    code = str(stock_code or "").strip().upper()
    if "." in code:
        return code
    if code.startswith(("5", "6")):
        return code + ".SH"
    if code.startswith(("8", "4", "920")):
        return code + ".BJ"
    return code + ".SZ"


def _parse_short_history_vol(reason: str) -> Optional[int]:
    m = _SHORT_HISTORY_RE.match(str(reason or "").strip())
    if not m:
        return None
    try:
        return int(m.group(2))
    except (TypeError, ValueError):
        return None


def _short_history_stale(meta: Dict[str, Any]) -> bool:
    """short_history ???????????????Σ??????????? K ?????"""
    la = str(meta.get("last_attempt_at") or meta.get("updated_at") or "")
    if not la:
        return True
    try:
        last_d = datetime.strptime(la[:19], "%Y-%m-%dT%H:%M:%S").date()
    except ValueError:
        return True
    return last_d < date.today()


def list_pending_daily(limit: int = 20) -> List[Tuple[str, date, Dict[str, Any]]]:
    global _PENDING_ROTATE_OFFSET
    data = load_requests()
    pending = []  # type: List[Tuple[str, date, Dict[str, Any], str]]
    daily = data.get("daily") or {}
    if not isinstance(daily, dict):
        return []
    for code, meta in daily.items():
        if not isinstance(meta, dict):
            continue
        status = str(meta.get("status") or "")
        if status == "short_history":
            if not _short_history_stale(meta):
                continue
        elif status != "pending":
            continue
        if int(meta.get("retries") or 0) >= MAX_RETRIES:
            continue
        td = _parse_date(meta.get("through_date")) or date.today()
        pending.append(
            (
                str(code),
                td,
                meta,
                str(meta.get("last_attempt_at") or ""),
                str(meta.get("requested_at") or ""),
            )
        )
    pending.sort(key=lambda x: (x[3], x[4], x[0]))
    if len(pending) > 1:
        off = _PENDING_ROTATE_OFFSET % len(pending)
        pending = pending[off:] + pending[:off]
        _PENDING_ROTATE_OFFSET += 1
    out = [(c, td, m) for c, td, m, _, _ in pending[: max(1, int(limit))]]
    return out


def list_pending_ticks(limit: int = 10) -> List[Tuple[str, date, Dict[str, Any]]]:
    data = load_requests()
    out = []  # type: List[Tuple[str, date, Dict[str, Any]]]
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


def _set_daily_status(code: str, through_date: date, status: str, error: str = "") -> None:
    data = load_requests()
    daily = data.setdefault("daily", {})
    full = _to_full_stock_code(code)
    meta = daily.get(full) if isinstance(daily.get(full), dict) else {}
    meta = dict(meta)
    meta["through_date"] = through_date.isoformat()
    meta["status"] = status
    meta["updated_at"] = _now_iso()
    if error:
        meta["last_error"] = error[:200]
    daily[full] = meta
    save_requests(data)


def _set_tick_status(code_6: str, trade_date: date, status: str, error: str = "") -> None:
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
    full = _to_full_stock_code(code)
    meta = daily.get(full) if isinstance(daily.get(full), dict) else {}
    meta = dict(meta)
    meta["through_date"] = through_date.isoformat()
    reason_s = str(reason or "")
    soft_pool = reason_s.startswith("pool_invalid") or reason_s.startswith(
        "pool_write_check"
    )
    short_vol = _parse_short_history_vol(reason_s) if soft_pool else None
    reason_l = reason_s.strip().lower()
    # miss_cache_empty_history ???? tick ?????????????????????????????
    miss_hard = reason_l in (
        "miss_cache_delisted",
        "miss_cache_today_halt",
        "miss_cache_suspended",
    )
    hard_empty = (
        miss_hard
        or reason_l.startswith("empty_history")
        or reason_l in ("empty", "delisted", "today_halt", "suspended", "no_ctx_no_rpc")
        or reason_l.startswith("invalid_0_valid")
    )
    # vol=0 / hist=0 ???????????????????????в?????? short_history???????????????
    if short_vol is not None and 0 < short_vol < SHORT_HISTORY_QUALITY_FLOOR:
        # ???/???????????? K ??????? MA120???????????????????????
        meta["status"] = "short_history"
        meta["short_vol_bars"] = short_vol
    elif soft_pool and (short_vol is None or short_vol <= 0):
        # pool_invalid_0_vol_0??QMT ??????????/δ???????????
        meta["retries"] = int(meta.get("retries") or 0) + 1
        meta["status"] = "pending"
    elif hard_empty:
        # ????/???????/? miss?????? failed
        meta["status"] = "failed"
        meta["retries"] = MAX_RETRIES
    elif soft_pool:
        # ?????? pool ??????????Σ????????? pending ???????
        meta["retries"] = int(meta.get("retries") or 0) + 1
        meta["status"] = "pending"
    else:
        meta["retries"] = int(meta.get("retries") or 0) + 1
        meta["status"] = "pending"
    meta["last_error"] = reason_s[:200]
    meta["last_attempt_at"] = _now_iso()
    meta["updated_at"] = _now_iso()
    if int(meta.get("retries") or 0) >= MAX_RETRIES:
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
