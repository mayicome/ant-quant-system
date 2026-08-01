#coding:gbk
"""大 QMT 内：盘后全 A 当日 tick 落盘到 data/ticks/{YYYYMMDD}/*.parquet。

由日线同步（15:35）完成后串行触发；落盘含至约 15:31 的盘后 tick。
主路径（迅投官方）：
  1) ContextInfo.get_local_data / get_market_data_ex(subscribe=False) 读本地
  2) 内置 download_history_data(code,"tick",YYYYMMDD,YYYYMMDD) 补本地（非 xtdata）
  3) 再 get_market_data_ex(subscribe=False)
  4) 可选 subscribe=True（订阅语义，不是历史 supply）
默认关闭 xtdata.download_history_*（同日线 ENABLE_XTDATA_DOWNLOAD），避免 miniQMT RPC 刷屏。
可断点续跑；已有完整 parquet/pkl 则跳过。
落盘成功（或已完成）后串行触发盘后量能（量能读本地落盘）。
结束后按磁盘剩余空间清理最旧日目录（空间够不删，可存超过三个月）。
兼容大 QMT 内置 Python 3.6（禁止 from __future__ import annotations）。
"""
import json
import os
import shutil
import time
from datetime import date, datetime, timedelta, time as dt_time
from typing import Any, Dict, List, Optional, Set, Tuple

TICK_FULL_SYNC_VERSION = "20260801.05"
# 与日线同点：仅作 catch-up 判断；正式启动由 daily_sync 串行触发
SYNC_HOUR = 15
SYNC_MINUTE = 35
BATCH_SIZE = 20
# 按盘符剩余空间清理：低于 MIN 才删最旧日目录，删到 TARGET 或只剩 MIN_KEEP_DAYS
TICK_MIN_FREE_GB = 40.0
TICK_TARGET_FREE_GB = 60.0
TICK_MIN_KEEP_DAYS = 20
# 券商 tick 留存约 20–30 个交易日；超出则本地/download 常永久为空（非 API 坏）
TICK_RETENTION_CALENDAR_DAYS = 28
# 大 QMT-only：默认关闭 xtdata.download_history_*（依赖 miniQMT 58610）。
# 仅调试可临时 True。生产补缺走内置 download_history_data（见 ENABLE_BUILTIN_TICK_DOWNLOAD）。
# download 参数须用 YYYYMMDD（带时分秒会导致 download2 空等超时）
ENABLE_XTDATA_TICK_DOWNLOAD = False
# 官方主路径：模型交易内置 download_history_data（非 xtdata）
ENABLE_BUILTIN_TICK_DOWNLOAD = True
# 本地+builtin 仍空时再试 subscribe=True（订阅语义，非历史 supply）
ENABLE_CTX_TICK_SUBSCRIBE = True
_DOWNLOAD_WAIT_SEC = 120
# 批内若多为已知空票，缩短 download 等待，避免每批空等 120s
_DOWNLOAD_WAIT_SEC_FAST = 8
# 连续多批「下载超时且本批几乎全失败」→ 疑似行情断连，中止并 Server酱告警
_CONN_FAIL_ABORT_BATCHES = 3
# ContextInfo 连续多批全空：本地无 tick 且 subscribe 也无数据，早停避免空转数小时
_CTX_EMPTY_ABORT_BATCHES = 5
# ABORT 后冷却：仅用于「今日」真故障；历史回填窗外/空批改为跳过该日并续队列
_ABORT_HOLD_SEC_CODEBUG = 6 * 3600
_ABORT_HOLD_SEC_CTX_EMPTY = 30 * 60
# manual_request / keep 日志节流（秒）
_MANUAL_LOG_INTERVAL_SEC = 120.0
# 盘中保护：全 A tick / manual_request 不抢主线程（与日线 intraday 窗口对齐并略宽）
# 否则 download_history_data 循环会饿死 periodic_sync → results.json/账户/策略取数全停
_MARKET_PROTECT_START = dt_time(9, 0)
_MARKET_PROTECT_END = dt_time(15, 30)
# 单次占用主线程上限：到点存 progress 并退回，让账户/on_demand/行情心跳先跑
_TIME_SLICE_SEC = 60.0
_PROGRESS_NAME = "_full_sync_progress.json"
_DONE_MARKER = "_full_sync_done.json"
_ABORT_HOLD_NAME = "_full_sync_abort_hold.json"
_MANUAL_REQUEST_NAME = "manual_request.json"
_PAUSE_FLAG_NAME = "PAUSE"
_MISS_CACHE_NAME = "sync_miss_codes.json"
# 仅跳过确认无行情/退市；不跳过 daily 的 local_miss / today_halt
_MISS_SKIP_REASONS = ("empty_history", "delisted")
# 这些失败原因不得写入 miss_cache（代码/API 问题，不是真无历史）
_MISS_BLOCK_REASON_SUBSTR = (
    "attributeerror",
    "has no attribute",
    "typeerror",
    "importerror",
    "modulenotfound",
    "nameerror",
)

_BUSY = False
_LAST_DONE_DAY = ""
_CATCHUP_LOG_TS = 0.0
_TICK_IO_MTIME = 0.0
_SECTOR_MTIME = 0.0
_ABORT_HOLD_DAY = ""
_ABORT_HOLD_UNTIL = 0.0
_ABORT_HOLD_REASON = ""
_ABORT_HOLD_LOG_TS = 0.0
_PROTECT_DEFER_LOG_TS = 0.0
_PAUSE_DEFER_LOG_TS = 0.0
_MANUAL_KEEP_LOG_TS = 0.0
_MANUAL_HOLD_LOG_TS = 0.0


def _log(msg):
    # type: (str) -> None
    print("[分笔同步] %s" % msg)


def _in_market_hours_protect(now=None):
    # type: (Optional[datetime]) -> bool
    """工作日盘中：禁止启动全 A tick 重活，避免阻塞策略/交易取数。"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return _MARKET_PROTECT_START <= t <= _MARKET_PROTECT_END


def _pause_flag_path():
    # type: () -> str
    return os.path.join(_data_dir(), "tick_full_sync", _PAUSE_FLAG_NAME)


def _pause_requested():
    # type: () -> bool
    try:
        return os.path.isfile(_pause_flag_path())
    except Exception:
        return False


def _daily_sync_running():
    # type: () -> bool
    """日线全量/ FORCE 回填占用同一 ContextInfo 主线程时勿并行开 tick。"""
    try:
        try:
            import ant_daily_sync_runner as daily
        except ImportError:
            import qmt_builtin.ant_daily_sync_runner as daily
        return bool(getattr(daily, "_SYNC_RUNNING", False))
    except Exception:
        return False


def _data_dir():
    # type: () -> str
    try:
        from ant_qmt_paths import DATA_DIR

        return str(DATA_DIR)
    except Exception:
        try:
            from qmt_builtin.ant_qmt_paths import DATA_DIR

            return str(DATA_DIR)
        except Exception:
            return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _project_root():
    # type: () -> str
    try:
        from ant_qmt_paths import PROJECT_ROOT

        return str(PROJECT_ROOT).rstrip("\\/")
    except Exception:
        try:
            from qmt_builtin.ant_qmt_paths import PROJECT_ROOT

            return str(PROJECT_ROOT).rstrip("\\/")
        except Exception:
            return os.path.dirname(_data_dir())


def _ticks_day_dir(day):
    # type: (str) -> str
    path = os.path.join(_project_root(), "data", "ticks", day)
    if not os.path.isdir(path):
        try:
            os.makedirs(path)
        except Exception:
            pass
    return path


def _progress_path(day):
    # type: (str) -> str
    return os.path.join(_ticks_day_dir(day), _PROGRESS_NAME)


def _done_path(day):
    # type: (str) -> str
    return os.path.join(_ticks_day_dir(day), _DONE_MARKER)


def _append_run_log(day, msg):
    # type: (str, str) -> None
    try:
        path = os.path.join(_data_dir(), "tick_full_sync", "%s_run.log" % day)
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, "a") as f:
            f.write("%s %s\n" % (datetime.now().strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def _load_xtdata():
    # type: () -> Any
    try:
        import xtquant.xtdata as xtdata

        return xtdata
    except Exception as e:
        _log("xtdata导入失败: %s" % e)
        return None


def _is_tradeday(xtdata, day):
    # type: (Any, date) -> bool
    try:
        ds = day.strftime("%Y%m%d")
        arr = xtdata.get_trading_dates("SH", ds, ds) or []
        return bool(arr)
    except Exception:
        return day.weekday() < 5


def _load_universe_from_file():
    # type: () -> List[str]
    """回退 data/a_share_universe.json（daily_sync 落盘），不依赖行情 RPC。"""
    path = os.path.join(_project_root(), "data", "a_share_universe.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f) or {}
        raw = list(payload.get("codes") or [])
        out = [str(c).strip() for c in raw if str(c).strip()]
        if out:
            _log("股票池回退文件 n=%d" % len(out))
        return out
    except Exception as e:
        _log("股票池文件失败: %s" % e)
        return []


def _load_universe(xtdata, limit=0, ContextInfo=None):
    # type: (Any, int, Any) -> List[str]
    # 板块名 unicode 转义，避免 GBK/编码损坏导致空池
    sectors = (
        "\u6caa\u6df1A\u80a1",  # 沪深A股
        "\u4e0a\u8bc1A\u80a1",  # 上证A股
        "\u6df1\u8bc1A\u80a1",  # 深证A股
    )
    owners = []  # type: List[Tuple[str, Any]]
    if ContextInfo is not None:
        owners.append(("ctx", ContextInfo))
    if xtdata is not None:
        owners.append(("xt", xtdata))

    codes = []  # type: List[str]
    src = "none"
    for label, owner in owners:
        fn = getattr(owner, "get_stock_list_in_sector", None)
        if not callable(fn):
            continue
        got = []  # type: List[str]
        for sector in sectors:
            try:
                part = fn(sector) or []
                got.extend([str(c).strip() for c in part if c])
            except Exception:
                continue
        if got:
            codes = got
            src = label
            break

    if not codes:
        codes = _load_universe_from_file()
        if codes:
            src = "file"

    seen = set()  # type: Set[str]
    out = []  # type: List[str]
    for c in codes:
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    if limit and limit > 0:
        out = out[:limit]
    if out:
        _log("股票池来源=%s n=%d" % (src, len(out)))
    return out


def _code6(full_code):
    # type: (str) -> str
    s = (full_code or "").strip().replace(".", "")
    if len(s) >= 6:
        return s[:6]
    return s.zfill(6) if s else ""


def _miss_cache_path():
    # type: () -> str
    return os.path.join(_project_root(), "data", "daily_cache", _MISS_CACHE_NAME)


def _load_miss_cache():
    # type: () -> Dict[str, Any]
    path = _miss_cache_path()
    out = {"version": 1, "codes": {}}  # type: Dict[str, Any]
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
            if isinstance(raw, dict) and isinstance(raw.get("codes"), dict):
                out = {
                    "version": int(raw.get("version") or 1),
                    "codes": dict(raw["codes"]),
                }
    except Exception:
        pass
    return out


def _save_miss_cache(payload):
    # type: (Dict[str, Any]) -> None
    path = _miss_cache_path()
    parent = os.path.dirname(path)
    try:
        if not os.path.isdir(parent):
            os.makedirs(parent)
        try:
            from ant_rules_io import save_json_atomic
        except ImportError:
            try:
                from qmt_builtin.ant_rules_io import save_json_atomic
            except ImportError:
                save_json_atomic = None  # type: ignore
        if save_json_atomic is not None:
            save_json_atomic(path, payload)
            return
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        _log("miss缓存保存失败: %s" % e)


def _full_from_c6(c6):
    # type: (str) -> str
    s = _code6(c6)
    if not s:
        return ""
    if s.startswith("6"):
        return s + ".SH"
    return s + ".SZ"


def _miss_active(meta, today):
    # type: (Dict[str, Any], date) -> bool
    if not isinstance(meta, dict):
        return False
    until_s = str(meta.get("until") or "").strip()[:10]
    if not until_s:
        return True
    try:
        until_d = datetime.strptime(until_s, "%Y-%m-%d").date()
    except ValueError:
        return True
    return until_d >= today


def _miss_reason_blocked(reason):
    # type: (Any) -> bool
    """AttributeError / 缺 API 等不得记入 empty_history miss_cache。"""
    s = str(reason or "").strip().lower()
    if not s:
        return False
    for sub in _MISS_BLOCK_REASON_SUBSTR:
        if sub in s:
            return True
    return False


def _miss_put(payload, full_code, reason, fail_day):
    # type: (Dict[str, Any], str, str, date) -> None
    from datetime import timedelta

    full = (full_code or "").strip().upper()
    if not full:
        return
    reason_s = str(reason or "empty_history").strip() or "empty_history"
    if _miss_reason_blocked(reason_s):
        return
    ttl_map = {
        "empty_history": 30,
        "delisted": 90,
        "empty_tick": 7,
        "today_halt": 0,
        "suspended": 0,
    }
    ttl = int(ttl_map.get(reason_s, 7))
    until_d = fail_day if ttl <= 0 else (fail_day + timedelta(days=ttl))
    store_reason = "empty_history" if reason_s == "empty_tick" else reason_s
    codes = payload.setdefault("codes", {})
    prev = codes.get(full) if isinstance(codes.get(full), dict) else {}
    fail_count = int(prev.get("fail_count") or 0) + 1
    codes[full] = {
        "reason": store_reason,
        "last_fail_date": fail_day.isoformat(),
        "fail_count": fail_count,
        "until": until_d.isoformat(),
        "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "tick_full_sync",
    }


def _load_progress(day):
    # type: (str) -> Dict[str, Any]
    path = _progress_path(day)
    if not os.path.isfile(path):
        return {"day": day, "done": [], "fail": {}, "updated_at": ""}
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"day": day, "done": [], "fail": {}, "updated_at": ""}
        data.setdefault("done", [])
        data.setdefault("fail", {})
        return data
    except Exception:
        return {"day": day, "done": [], "fail": {}, "updated_at": ""}


def _save_progress(day, data):
    # type: (str, Dict[str, Any]) -> None
    path = _progress_path(day)
    data["day"] = day
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        if os.path.exists(path):
            os.remove(path)
        os.rename(tmp, path)
    except Exception as e:
        _log("进度保存失败: %s" % e)


def _mark_done(
    day,
    ok_count,
    fail_count,
    total,
    elapsed_sec=None,
    started_at=None,
    extra=None,
):
    # type: (str, int, int, int, Optional[float], Optional[str], Optional[Dict[str, Any]]) -> None
    path = _done_path(day)
    payload = {
        "day": day,
        "ok": ok_count,
        "fail": fail_count,
        "total": total,
        "version": TICK_FULL_SYNC_VERSION,
        "started_at": started_at or "",
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_sec": None if elapsed_sec is None else round(float(elapsed_sec), 1),
    }
    if isinstance(extra, dict):
        payload.update(extra)
    try:
        parent = os.path.dirname(path)
        if parent and (not os.path.isdir(parent)):
            os.makedirs(parent)
        with open(path, "w") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _parse_day_date(day_s):
    # type: (str) -> Optional[date]
    s = str(day_s or "").replace("-", "").replace("/", "")[:8]
    if len(s) != 8 or (not s.isdigit()):
        return None
    try:
        return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _is_historical_day(day_s, today=None):
    # type: (str, Optional[date]) -> bool
    d = _parse_day_date(day_s)
    if d is None:
        return False
    return d < (today or date.today())


def _day_beyond_tick_retention(day_s, today=None, retention_days=None):
    # type: (str, Optional[date], Optional[int]) -> bool
    """超出券商 tick 留存窗口（按自然日）。"""
    d = _parse_day_date(day_s)
    if d is None:
        return False
    today = today or date.today()
    n = int(
        TICK_RETENTION_CALENDAR_DAYS if retention_days is None else retention_days
    )
    if n <= 0:
        return False
    return d < (today - timedelta(days=n))


def _mark_skipped_no_tick(day_s, reason="beyond_retention"):
    # type: (str, str) -> None
    """标记该日队列可推进：券商无 tick / 历史空批，非成功落盘。"""
    reason_s = str(reason or "beyond_retention")[:120]
    beyond = _day_beyond_tick_retention(day_s)
    extra = {
        "skipped_no_tick": True,
        "skipped_retention": bool(beyond) or reason_s.startswith("beyond_"),
        "skip_reason": reason_s,
    }
    _mark_done(day_s, 0, 0, 0, elapsed_sec=0.0, extra=extra)
    msg = (
        "SKIP 无tick日 day=%s reason=%s（超出券商tick留存或历史无数据，跳过续下一队列日）"
        % (day_s, reason_s)
    )
    if beyond or reason_s.startswith("beyond_"):
        msg = "SKIP 超出券商tick留存 day=%s reason=%s" % (day_s, reason_s)
    _log(msg)
    _append_run_log(day_s, msg)
    _clear_abort_hold(day_s)


def _day_already_done(day):
    # type: (str) -> bool
    path = _done_path(day)
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return False
        # 旧版落盘未含盘后时段，不能当成本版完成（否则量能读盘 after_vol 全 0）
        ver = str(data.get("version") or "")
        if ver < "20260728.04":
            return False
        # 留存窗外 / 历史空批跳过：队列与 catch-up 视为已处理
        if data.get("skipped_retention") or data.get("skipped_no_tick"):
            return True
        total = int(data.get("total") or 0)
        ok = int(data.get("ok") or 0)
        return total > 0 and ok >= max(1000, int(total * 0.8))
    except Exception:
        return False


def _abort_hold_path(day):
    # type: (str) -> str
    return os.path.join(_ticks_day_dir(day), _ABORT_HOLD_NAME)


def _clear_abort_hold(day_s):
    # type: (str) -> None
    global _ABORT_HOLD_DAY, _ABORT_HOLD_UNTIL, _ABORT_HOLD_REASON
    if _ABORT_HOLD_DAY == str(day_s):
        _ABORT_HOLD_DAY = ""
        _ABORT_HOLD_UNTIL = 0.0
        _ABORT_HOLD_REASON = ""
    path = _abort_hold_path(day_s)
    if os.path.isfile(path):
        try:
            os.remove(path)
        except Exception:
            pass


def _set_abort_hold(day_s, reason, cooldown_sec):
    # type: (str, str, float) -> None
    global _ABORT_HOLD_DAY, _ABORT_HOLD_UNTIL, _ABORT_HOLD_REASON
    until = time.time() + float(cooldown_sec)
    _ABORT_HOLD_DAY = str(day_s)
    _ABORT_HOLD_UNTIL = until
    _ABORT_HOLD_REASON = str(reason or "")[:200]
    payload = {
        "day": day_s,
        "reason": _ABORT_HOLD_REASON,
        "until_ts": until,
        "until": datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M:%S"),
        "version": TICK_FULL_SYNC_VERSION,
        "set_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = _abort_hold_path(day_s)
    try:
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, "w") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    _log(
        "中止冷却至 %s 原因=%s"
        % (payload["until"], _ABORT_HOLD_REASON[:80])
    )


def _abort_hold_active(day_s):
    # type: (str) -> Tuple[bool, str]
    """今日 ABORT 冷却中则跳过重跑，避免 miss_cache 被空批撑大。"""
    global _ABORT_HOLD_DAY, _ABORT_HOLD_UNTIL, _ABORT_HOLD_REASON, _ABORT_HOLD_LOG_TS
    now = time.time()
    if _ABORT_HOLD_DAY == str(day_s) and now < float(_ABORT_HOLD_UNTIL or 0):
        return True, _ABORT_HOLD_REASON
    path = _abort_hold_path(day_s)
    if os.path.isfile(path):
        try:
            with open(path, "r") as f:
                data = json.load(f)
            until = float((data or {}).get("until_ts") or 0)
            reason = str((data or {}).get("reason") or "")
            if until > now:
                _ABORT_HOLD_DAY = str(day_s)
                _ABORT_HOLD_UNTIL = until
                _ABORT_HOLD_REASON = reason
                return True, reason
        except Exception:
            pass
    return False, ""


def _import_tick_io():
    # type: () -> Any
    """按 mtime 热重载 ant_tick_cache_io，避免 deploy 后仍缺 fetch_ticks_batch。"""
    global _TICK_IO_MTIME
    import importlib

    try:
        from ant_qmt_paths import QMT_BUILTIN_DIR

        base = str(QMT_BUILTIN_DIR)
    except Exception:
        try:
            from qmt_builtin.ant_qmt_paths import QMT_BUILTIN_DIR

            base = str(QMT_BUILTIN_DIR)
        except Exception:
            base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, "ant_tick_cache_io.py")
    try:
        mtime = os.path.getmtime(path) if os.path.isfile(path) else 0.0
    except Exception:
        mtime = 0.0
    try:
        import ant_tick_cache_io as mod
    except ImportError:
        import qmt_builtin.ant_tick_cache_io as mod
    need_reload = (_TICK_IO_MTIME != mtime) or (
        not hasattr(mod, "fetch_ticks_batch")
    )
    if need_reload:
        mod = importlib.reload(mod)
        _TICK_IO_MTIME = mtime
        _log(
            "tick_io已重载 版本=%s 有批取=%s"
            % (
                getattr(mod, "TICK_CACHE_IO_VERSION", "?"),
                hasattr(mod, "fetch_ticks_batch"),
            )
        )
    if not hasattr(mod, "fetch_ticks_batch"):
        raise AttributeError(
            "module 'ant_tick_cache_io' has no attribute 'fetch_ticks_batch' "
            "(deploy/reload failed; file=%s)" % path
        )
    return mod


def _load_sector_sync_runner():
    # type: () -> Any
    """按 mtime 热重载板块同步，避免进程内仍是 20260728.2。"""
    global _SECTOR_MTIME
    import importlib

    try:
        from ant_qmt_paths import QMT_BUILTIN_DIR

        base = str(QMT_BUILTIN_DIR)
    except Exception:
        try:
            from qmt_builtin.ant_qmt_paths import QMT_BUILTIN_DIR

            base = str(QMT_BUILTIN_DIR)
        except Exception:
            base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, "ant_sector_sync_runner.py")
    try:
        mtime = os.path.getmtime(path) if os.path.isfile(path) else 0.0
    except Exception:
        mtime = 0.0
    try:
        import ant_sector_sync_runner as mod
    except ImportError:
        import qmt_builtin.ant_sector_sync_runner as mod
    if _SECTOR_MTIME != mtime:
        mod = importlib.reload(mod)
        _SECTOR_MTIME = mtime
        _log(
            "板块模块已重载 版本=%s"
            % getattr(mod, "SECTOR_SYNC_VERSION", "?")
        )
    return mod


def _disk_free_bytes(path):
    # type: (str) -> Optional[int]
    try:
        return int(shutil.disk_usage(path).free)
    except Exception:
        return None


def _dir_size_bytes(path):
    # type: (str) -> int
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


def _purge_old_tick_dirs(
    min_free_gb=TICK_MIN_FREE_GB,
    target_free_gb=TICK_TARGET_FREE_GB,
    min_keep_days=TICK_MIN_KEEP_DAYS,
):
    # type: (float, float, int) -> List[str]
    """空间够不删；不足时从最旧日目录删到目标剩余或只剩 min_keep_days。"""
    root = os.path.join(_project_root(), "data", "ticks")
    if not os.path.isdir(root):
        return []
    days = []  # type: List[str]
    for name in os.listdir(root):
        if len(name) == 8 and name.isdigit() and os.path.isdir(os.path.join(root, name)):
            days.append(name)
    days.sort()
    if not days:
        return []

    min_keep = max(1, int(min_keep_days or TICK_MIN_KEEP_DAYS))
    min_free = float(min_free_gb if min_free_gb is not None else TICK_MIN_FREE_GB)
    target_free = float(target_free_gb if target_free_gb is not None else TICK_TARGET_FREE_GB)
    if target_free < min_free:
        target_free = min_free

    free_b = _disk_free_bytes(root)
    if free_b is None:
        return []
    min_b = int(min_free * (1024.0 ** 3))
    target_b = int(target_free * (1024.0 ** 3))
    if free_b >= min_b:
        _log(
            "清理跳过: 剩余=%.1fGB >= 下限=%.1fGB（保留%d个日目录）"
            % (free_b / (1024.0 ** 3), min_free, len(days))
        )
        return []

    removed = []  # type: List[str]
    remaining = list(days)
    while remaining and len(remaining) > min_keep:
        free_now = _disk_free_bytes(root)
        if free_now is None or free_now >= target_b:
            break
        ymd = remaining.pop(0)
        path = os.path.join(root, ymd)
        try:
            shutil.rmtree(path)
            removed.append(ymd)
            _log("已清理日目录 %s（清理前剩余%.1fGB）" % (ymd, (free_now or 0) / (1024.0 ** 3)))
        except Exception as e:
            _log("清理 %s 失败: %s" % (ymd, e))
    return removed


def _download_batch_builtin(tick_io, full_codes, ymd):
    # type: (Any, List[str], str) -> Tuple[int, str]
    """大 QMT 内置 download_history_data 批量补本地 tick（非 58610）。"""
    if not ENABLE_BUILTIN_TICK_DOWNLOAD or tick_io is None:
        return 0, "builtin_dl_off"
    fn = getattr(tick_io, "download_ticks_via_builtin", None)
    if not callable(fn):
        return 0, "no_download_ticks_via_builtin"
    try:
        return fn(full_codes, ymd)
    except Exception as e:
        return 0, "%s: %s" % (type(e).__name__, e)


def _download_batch(xtdata, full_codes, ymd, wait_sec=None):
    # type: (Any, List[str], str, Optional[float]) -> bool
    """可选 xtdata 批量下载；默认关闭。返回是否等待回调超时（疑似通道不通）。"""
    if not ENABLE_XTDATA_TICK_DOWNLOAD:
        return False
    if xtdata is None:
        return True
    # download_history_* 必须用 YYYYMMDD；带 091500/153100 时 download2 常空等超时
    day_s = str(ymd or "").replace("-", "").replace("/", "")[:8]
    done = {"ok": False}
    wait = float(_DOWNLOAD_WAIT_SEC if wait_sec is None else wait_sec)

    def _cb(data):
        done["ok"] = True

    timed_out = False
    try:
        if hasattr(xtdata, "download_history_data2"):
            xtdata.download_history_data2(
                full_codes, "tick", day_s, day_s, callback=_cb
            )
            t0 = time.time()
            while not done["ok"] and (time.time() - t0) < wait:
                time.sleep(0.2)
            if not done["ok"]:
                timed_out = True
                _log(
                    "download2等待超时 %ss（本批=%d）"
                    % (int(wait), len(full_codes or []))
                )
        else:
            for fc in full_codes:
                try:
                    xtdata.download_history_data(fc, "tick", day_s, day_s)
                except Exception:
                    pass
    except Exception as e:
        _log("批量下载错误: %s" % e)
        timed_out = True
    return timed_out


def _notify_conn_dead(day_s, detail):
    # type: (str, str) -> None
    try:
        try:
            import ant_server_chan as sct
        except ImportError:
            import qmt_builtin.ant_server_chan as sct
        title = "大QMT疑似行情断连"
        body = (
            "全A tick落盘连续多批超时/失败，已中止，请检查大QMT行情连接后重载策略。\n\n"
            "日=%s\n版本=%s\n%s"
            % (day_s, TICK_FULL_SYNC_VERSION, detail)
        )
        r = sct.notify_alert(
            title, body, alert_key="tick_full_sync_conn_%s" % day_s, cooldown_sec=3600
        )
        if r.get("skipped"):
            _log("server酱已跳过: %s" % r.get("message"))
        elif r.get("success"):
            _log("server酱告警已发 (%s)" % r.get("source"))
        else:
            _log("server酱告警失败: %s" % r.get("message"))
    except Exception as e:
        _log("server酱告警异常: %s" % e)


def _notify_ctx_empty(day_s, detail):
    # type: (str, str) -> None
    try:
        try:
            import ant_server_chan as sct
        except ImportError:
            import qmt_builtin.ant_server_chan as sct
        title = "全A tick本地/服务器无数据"
        body = (
            "本地读 + 内置 download_history_data + get_market_data_ex(tick) "
            "连续多批为空，已中止。\n"
            "说明：官方正确路径是先 download_history_data(period=tick,YYYYMMDD)，"
            "再 get_market_data_ex(subscribe=False)；subscribe≠补历史。\n"
            "下一步：在模型交易跑策略函数 tick_probe 看哪组变体有数据；"
            "或 UI 数据管理补分笔。禁止默认走 miniQMT。\n\n"
            "日=%s\n版本=%s\n%s"
            % (day_s, TICK_FULL_SYNC_VERSION, detail)
        )
        r = sct.notify_alert(
            title, body, alert_key="tick_full_sync_empty_%s" % day_s, cooldown_sec=3600
        )
        if r.get("skipped"):
            _log("server酱已跳过: %s" % r.get("message"))
        elif r.get("success"):
            _log("server酱告警已发 (%s)" % r.get("source"))
        else:
            _log("server酱告警失败: %s" % r.get("message"))
    except Exception as e:
        _log("server酱告警异常: %s" % e)


def _write_one_from_raw(tick_io, full_code, trade_d, raw, overwrite=False):
    # type: (Any, str, date, Any, bool) -> Tuple[str, Optional[str]]
    c6 = _code6(full_code)
    if not c6:
        return "fail", "bad_code"
    if (not overwrite) and tick_io.tick_cache_file_ready(c6, trade_d):
        return "skip", "exists"
    try:
        data = tick_io.normalize_tick_dataframe(raw)
        data = tick_io.coerce_tick_dataframe(data) if data is not None else None
        if data is None or len(data) == 0:
            return "fail", "empty_tick"
        if not tick_io.write_tick_cache(c6, trade_d, data):
            return "fail", "write_tick_cache"
        return "ok", None
    except Exception as e:
        return "fail", "%s" % e


def _sync_one(
    tick_io,
    xtdata,
    full_code,
    trade_d,
    overwrite=False,
    ContextInfo=None,
    prefetched=None,
    allow_refetch=True,
):
    # type: (Any, Any, str, date, bool, Any, Any, bool) -> Tuple[str, Optional[str]]
    c6 = _code6(full_code)
    if not c6:
        return "fail", "bad_code"
    if (not overwrite) and tick_io.tick_cache_file_ready(c6, trade_d):
        return "skip", "exists"
    try:
        raw = prefetched
        if raw is None and allow_refetch:
            raw = tick_io.fetch_tick_from_qmt(
                c6,
                trade_d,
                ContextInfo=ContextInfo,
                xtdata=xtdata,
                allow_xtdata_download=ENABLE_XTDATA_TICK_DOWNLOAD,
            )
        if raw is None:
            return "fail", "empty_tick"
        return _write_one_from_raw(
            tick_io, full_code, trade_d, raw, overwrite=overwrite
        )
    except Exception as e:
        return "fail", "%s" % e


def run_tick_full_sync(
    ContextInfo=None,
    day=None,
    force=False,
    limit=0,
    allow_intraday=False,
    skip_retention_gate=False,
):
    # type: (Any, Optional[str], bool, int, bool, bool) -> bool
    """同步指定日（默认今天）全 A tick 到项目 data/ticks。

    allow_intraday=True 仅应急；默认盘中拒绝启动，避免饿死交易/策略取数。
    skip_retention_gate=True：manual_request 显式排队日不按留存窗自动跳过。
    单次最多占用主线程约 _TIME_SLICE_SEC，到期存 progress 退回（可断点续跑）。
    """
    global _BUSY, _LAST_DONE_DAY, _ABORT_HOLD_LOG_TS, _PROTECT_DEFER_LOG_TS
    global _PAUSE_DEFER_LOG_TS

    if _BUSY:
        _log("忙碌中，跳过")
        return False

    if _pause_requested():
        ts = time.time()
        if ts - _PAUSE_DEFER_LOG_TS >= 60.0:
            _PAUSE_DEFER_LOG_TS = ts
            _log("已暂停：删除 data/tick_full_sync/PAUSE 后继续")
        return False

    if (not allow_intraday) and _in_market_hours_protect():
        ts = time.time()
        if ts - _PROTECT_DEFER_LOG_TS >= 60.0:
            _PROTECT_DEFER_LOG_TS = ts
            _log(
                "暂缓：盘中保护 %s-%s（优先交易/策略取数）"
                % (
                    _MARKET_PROTECT_START.strftime("%H:%M"),
                    _MARKET_PROTECT_END.strftime("%H:%M"),
                )
            )
        return False

    if _daily_sync_running():
        _log("暂缓：日线同步进行中（共用ContextInfo）")
        return False

    xtdata = _load_xtdata()
    if xtdata is None and ContextInfo is None:
        _log("无ContextInfo且无xtdata")
        return False

    now = datetime.now()
    if day:
        day_s = str(day).replace("-", "").replace("/", "")[:8]
        trade_d = date(int(day_s[0:4]), int(day_s[4:6]), int(day_s[6:8]))
    else:
        trade_d = now.date()
        day_s = trade_d.strftime("%Y%m%d")

    if xtdata is not None and not _is_tradeday(xtdata, trade_d):
        _log("非交易日: %s" % day_s)
        return False
    if xtdata is None and trade_d.weekday() >= 5:
        _log("非交易日（周末）: %s" % day_s)
        return False

    if (not force) and (_LAST_DONE_DAY == day_s or _day_already_done(day_s)):
        _log("已完成: %s" % day_s)
        return True

    # 超出券商 tick 留存：直接跳过并让队列前进（勿 abort-hold）。
    # manual_request 显式排队日可 skip_retention_gate，避免用户点名回补被 28 日窗误杀。
    if (not force) and (not skip_retention_gate) and _day_beyond_tick_retention(day_s):
        _mark_skipped_no_tick(day_s, "beyond_retention_%dd" % int(TICK_RETENTION_CALENDAR_DAYS))
        _LAST_DONE_DAY = day_s
        return True

    # ABORT 冷却：仅约束「今日」真故障；历史日 ctx-empty 改为跳过续跑
    if not force:
        held, hold_reason = _abort_hold_active(day_s)
        if held:
            hr = str(hold_reason or "")
            if _is_historical_day(day_s) and ("ctx-empty" in hr or "conn-dead" in hr):
                _mark_skipped_no_tick(day_s, "historical_%s" % (hr or "empty")[:80])
                _LAST_DONE_DAY = day_s
                return True
            ts = time.time()
            if ts - _ABORT_HOLD_LOG_TS >= 120.0:
                _ABORT_HOLD_LOG_TS = ts
                _log(
                    "跳过：中止冷却中 day=%s 原因=%s"
                    % (day_s, hr[:80])
                )
            return False

    try:
        tick_io = _import_tick_io()
    except AttributeError as e:
        _log("tick_io导入中止: %s" % e)
        _append_run_log(day_s, "ABORT code-bug %s" % e)
        _set_abort_hold(day_s, "code-bug:%s" % e, _ABORT_HOLD_SEC_CODEBUG)
        return False
    # 镜像开关，避免 GBK 副本与 src 不一致时仍去 download / 关掉 subscribe
    try:
        tick_io.ENABLE_XTDATA_TICK_DOWNLOAD = ENABLE_XTDATA_TICK_DOWNLOAD
    except Exception:
        pass
    try:
        tick_io.ENABLE_BUILTIN_TICK_DOWNLOAD = ENABLE_BUILTIN_TICK_DOWNLOAD
    except Exception:
        pass
    try:
        tick_io.ENABLE_CTX_TICK_SUBSCRIBE = ENABLE_CTX_TICK_SUBSCRIBE
    except Exception:
        pass
    try:
        if hasattr(tick_io, "bind_download_history_data"):
            tick_io.bind_download_history_data(None)
    except Exception:
        pass
    universe = _load_universe(xtdata, limit=limit, ContextInfo=ContextInfo)
    if not universe:
        _log("股票池为空")
        # 写入 run.log，便于盘后排查（此前仅 print，目录空且无 START）
        _append_run_log(
            day_s,
            "ABORT 股票池为空 version=%s（需ContextInfo或文件回退）"
            % TICK_FULL_SYNC_VERSION,
        )
        return False

    # 旧版落盘截断在 15:00，升级后需重拉含盘后时段，否则量能读盘 after_vol 全 0
    legacy_resync = False
    done_marker = _done_path(day_s)
    if os.path.isfile(done_marker):
        try:
            with open(done_marker, "r") as f:
                old_done = json.load(f)
            old_ver = str((old_done or {}).get("version") or "")
            if old_ver < "20260728.04":
                legacy_resync = True
                _log(
                    "旧版同步 version=%s → 含盘后重拉 %s"
                    % (old_ver or "?", day_s)
                )
                try:
                    os.remove(done_marker)
                except Exception:
                    pass
        except Exception:
            pass

    _BUSY = True
    t_start = time.time()
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _log(
        "开始 day=%s 股票池=%d 版本=%s xtdata_dl=%s builtin_dl=%s "
        "ctx_sub=%s 主路径=download_history_data+get_market_data_ex at=%s"
        % (
            day_s,
            len(universe),
            TICK_FULL_SYNC_VERSION,
            "on" if ENABLE_XTDATA_TICK_DOWNLOAD else "off",
            "on" if ENABLE_BUILTIN_TICK_DOWNLOAD else "off",
            "on" if ENABLE_CTX_TICK_SUBSCRIBE else "off",
            started_at,
        )
    )
    _append_run_log(
        day_s,
        "START n=%d force=%s 旧版重拉=%s xtdata_dl=%s builtin_dl=%s "
        "ctx_sub=%s at=%s"
        % (
            len(universe),
            force,
            legacy_resync,
            "on" if ENABLE_XTDATA_TICK_DOWNLOAD else "off",
            "on" if ENABLE_BUILTIN_TICK_DOWNLOAD else "off",
            "on" if ENABLE_CTX_TICK_SUBSCRIBE else "off",
            started_at,
        ),
    )

    if legacy_resync:
        progress = {"day": day_s, "done": [], "fail": {}, "updated_at": ""}
        done_set = set()  # type: Set[str]
        fail_map = {}  # type: Dict[str, str]
    else:
        progress = _load_progress(day_s)
        done_set = set([str(x) for x in (progress.get("done") or [])])  # type: Set[str]
        fail_map = dict(progress.get("fail") or {})  # type: Dict[str, str]

    pending = []  # type: List[str]
    miss_payload = _load_miss_cache()
    miss_codes = miss_payload.get("codes") or {}
    miss_skip = 0
    for fc in universe:
        c6 = _code6(fc)
        if not c6:
            continue
        if c6 in done_set:
            continue
        full = str(fc).strip().upper()
        if "." not in full:
            full = _full_from_c6(c6)
        meta = miss_codes.get(full) if isinstance(miss_codes.get(full), dict) else None
        if meta is None:
            meta = miss_codes.get(c6) if isinstance(miss_codes.get(c6), dict) else None
        if (not force) and meta is not None and _miss_active(meta, trade_d):
            reason = str(meta.get("reason") or "")
            # 仅退市/确认无历史；不跳过 local_miss / today_halt / invalid_0
            if reason in _MISS_SKIP_REASONS:
                miss_skip += 1
                fail_map[c6] = "miss_cache_%s" % reason
                continue
        if (
            (not force)
            and (not legacy_resync)
            and tick_io.tick_cache_file_ready(c6, trade_d)
        ):
            done_set.add(c6)
            continue
        pending.append(fc)
    if miss_skip:
        _log(
            "miss缓存跳过=%d 待跑=%d path=%s"
            % (miss_skip, len(pending), _miss_cache_path())
        )

    ok_new = 0
    fail_new = 0
    skip_exist = 0
    conn_fail_streak = 0
    ctx_empty_streak = 0
    aborted_conn = False
    aborted_ctx_empty = False
    aborted_code_bug = False
    aborted_yield = False
    miss_dirty = False
    next_wait = float(_DOWNLOAD_WAIT_SEC)
    try:
        for i in range(0, len(pending), BATCH_SIZE):
            if _pause_requested():
                aborted_yield = True
                _log("运行中暂停：保存进度后让出")
                _append_run_log(day_s, "YIELD 暂停 剩余=%d" % (len(pending) - i))
                break
            if (not allow_intraday) and _in_market_hours_protect():
                aborted_yield = True
                _log("运行中盘中保护：让出")
                _append_run_log(
                    day_s, "YIELD 盘中保护 剩余=%d" % (len(pending) - i)
                )
                break
            if (time.time() - t_start) >= float(_TIME_SLICE_SEC):
                aborted_yield = True
                _log(
                    "时间片让出 已用%.0fs（剩余约%d）；周期任务续跑"
                    % (time.time() - t_start, max(0, len(pending) - i))
                )
                _append_run_log(
                    day_s,
                    "YIELD 时间片 sec=%.0f 剩余=%d"
                    % (time.time() - t_start, len(pending) - i),
                )
                break
            batch = pending[i : i + BATCH_SIZE]
            # 主路径预下载：内置 download_history_data（YYYYMMDD）；xtdata 默认关
            n_builtin, builtin_err = _download_batch_builtin(tick_io, batch, day_s)
            if n_builtin > 0:
                _log(
                    "内置download_history_data 成功=%d/%d day=%s"
                    % (n_builtin, len(batch), day_s)
                )
            elif builtin_err and builtin_err not in ("builtin_dl_off",):
                _log("内置下载备注: %s" % builtin_err)
            timed_out = _download_batch(
                xtdata, batch, day_s, wait_sec=next_wait
            )
            # 主路径：本地 →（已 builtin download）→ get_market_data_ex
            batch_map = {}  # type: Dict[str, Any]
            batch_fetch_ok = True
            batch_fetch_err = ""
            try:
                # 批前已全成功则不再 download；部分失败留给 fetch 对缺票补下
                redo_dl = bool(
                    ENABLE_BUILTIN_TICK_DOWNLOAD and (n_builtin < len(batch))
                )
                batch_map = tick_io.fetch_ticks_batch(
                    batch,
                    trade_d,
                    ContextInfo=ContextInfo,
                    xtdata=xtdata,
                    allow_subscribe=ENABLE_CTX_TICK_SUBSCRIBE,
                    allow_builtin_download=redo_dl,
                ) or {}
            except TypeError:
                # 旧 GBK 副本尚无 allow_builtin_download 参数
                try:
                    batch_map = tick_io.fetch_ticks_batch(
                        batch,
                        trade_d,
                        ContextInfo=ContextInfo,
                        xtdata=xtdata,
                        allow_subscribe=ENABLE_CTX_TICK_SUBSCRIBE,
                    ) or {}
                except AttributeError as e:
                    batch_fetch_ok = False
                    batch_fetch_err = "%s" % e
                    _log("批取tick错误(code-bug): %s" % e)
                    aborted_code_bug = True
                    _append_run_log(
                        day_s, "ABORT code-bug %s" % str(e).replace("\n", " ")
                    )
                    _set_abort_hold(
                        day_s, "code-bug:%s" % e, _ABORT_HOLD_SEC_CODEBUG
                    )
                    break
                except Exception as e:
                    batch_fetch_ok = False
                    batch_fetch_err = "%s" % e
                    _log("批取tick错误: %s" % e)
                    batch_map = {}
            except AttributeError as e:
                batch_fetch_ok = False
                batch_fetch_err = "%s" % e
                _log("批取tick错误(code-bug): %s" % e)
                aborted_code_bug = True
                _append_run_log(
                    day_s, "ABORT code-bug %s" % str(e).replace("\n", " ")
                )
                _set_abort_hold(
                    day_s, "code-bug:%s" % e, _ABORT_HOLD_SEC_CODEBUG
                )
                break
            except Exception as e:
                batch_fetch_ok = False
                batch_fetch_err = "%s" % e
                _log("批取tick错误: %s" % e)
                if _miss_reason_blocked(e):
                    aborted_code_bug = True
                    _append_run_log(
                        day_s, "ABORT code-bug %s" % str(e).replace("\n", " ")
                    )
                    _set_abort_hold(
                        day_s, "code-bug:%s" % e, _ABORT_HOLD_SEC_CODEBUG
                    )
                    break
                batch_map = {}
            batch_ok = 0
            batch_fail = 0
            for fc in batch:
                c6 = _code6(fc)
                full = str(fc).strip().upper()
                if "." not in full:
                    full = _full_from_c6(c6)
                pref = batch_map.get(full)
                if pref is None:
                    pref = batch_map.get(fc)
                status, reason = _sync_one(
                    tick_io,
                    xtdata,
                    fc,
                    trade_d,
                    overwrite=(force or legacy_resync),
                    ContextInfo=ContextInfo,
                    prefetched=pref,
                    # 批已试过 ContextInfo/local；缺票不再单票重打（防空挂）
                    allow_refetch=False,
                )
                if status == "ok":
                    ok_new += 1
                    batch_ok += 1
                    done_set.add(c6)
                    if c6 in fail_map:
                        fail_map.pop(c6, None)
                elif status == "skip":
                    skip_exist += 1
                    done_set.add(c6)
                else:
                    fail_new += 1
                    batch_fail += 1
                    fail_map[c6] = reason or status
                    # 仅 API 正常返回空才记 miss；AttributeError/批失败不写 miss_cache
                    if (
                        batch_fetch_ok
                        and str(reason or "") in ("empty_tick", "empty")
                        and not _miss_reason_blocked(reason)
                    ):
                        _miss_put(miss_payload, full, "empty_tick", trade_d)
                        miss_dirty = True
            # 整批几乎全空：后续批用短 timeout（仅 xtdata_dl=on 时有意义）
            if batch_ok == 0 and batch_fail >= max(1, int(len(batch) * 0.8)):
                next_wait = float(_DOWNLOAD_WAIT_SEC_FAST)
            else:
                next_wait = float(_DOWNLOAD_WAIT_SEC)
            progress["done"] = sorted(list(done_set))
            progress["fail"] = fail_map
            _save_progress(day_s, progress)
            _log(
                "进度 %d/%d 新增成功=%d 新增失败=%d 已有跳过=%d miss跳过=%d "
                "本批命中=%d 超时=%s 批取ok=%s"
                % (
                    min(i + BATCH_SIZE, len(pending)),
                    len(pending),
                    ok_new,
                    fail_new,
                    skip_exist,
                    miss_skip,
                    len(batch_map),
                    int(bool(timed_out)),
                    int(bool(batch_fetch_ok)),
                )
            )
            if batch_fetch_err and not batch_fetch_ok:
                # 非 AttributeError 的批失败：本批已记 progress，不撑 miss；计空批 streak
                pass

            # 下载超时且本批几乎无成功 → 计为通道异常（仅 xtdata_dl=on）
            if (
                ENABLE_XTDATA_TICK_DOWNLOAD
                and timed_out
                and batch_ok == 0
                and batch_fail >= max(1, int(len(batch) * 0.5))
            ):
                conn_fail_streak += 1
                _log(
                    "疑似断连连续 %d/%d（超时+本批空）"
                    % (conn_fail_streak, _CONN_FAIL_ABORT_BATCHES)
                )
            else:
                conn_fail_streak = 0

            if conn_fail_streak >= int(_CONN_FAIL_ABORT_BATCHES):
                aborted_conn = True
                detail = (
                    "已处理 %d/%d\nok_new=%d fail_new=%d\n连续异常批=%d"
                    % (
                        min(i + BATCH_SIZE, len(pending)),
                        len(pending),
                        ok_new,
                        fail_new,
                        conn_fail_streak,
                    )
                )
                _log("ABORT 疑似行情断连")
                _append_run_log(day_s, "ABORT conn-dead " + detail.replace("\n", " | "))
                _notify_conn_dead(day_s, detail)
                _set_abort_hold(
                    day_s, "conn-dead", _ABORT_HOLD_SEC_CTX_EMPTY
                )
                break

            # 本地+builtin download+subscribe 连续多批全空 → 早停
            # 批 fetch 失败（API 异常）不计入「真·空行情」，也不写 miss
            if (
                (not ENABLE_XTDATA_TICK_DOWNLOAD)
                and batch_fetch_ok
                and batch_ok == 0
                and batch_fail >= max(1, int(len(batch) * 0.8))
                and len(batch_map) == 0
            ):
                ctx_empty_streak += 1
                _log(
                    "本地/内置/订阅连续空批 %d/%d"
                    % (ctx_empty_streak, _CTX_EMPTY_ABORT_BATCHES)
                )
            else:
                if batch_fetch_ok:
                    ctx_empty_streak = 0

            if ctx_empty_streak >= int(_CTX_EMPTY_ABORT_BATCHES):
                aborted_ctx_empty = True
                detail = (
                    "已处理 %d/%d\nok_new=%d fail_new=%d\n连续空批=%d\n"
                    "builtin_dl=%s ctx_sub=%s；请跑 tick_probe；勿开 miniQMT"
                    % (
                        min(i + BATCH_SIZE, len(pending)),
                        len(pending),
                        ok_new,
                        fail_new,
                        ctx_empty_streak,
                        "on" if ENABLE_BUILTIN_TICK_DOWNLOAD else "off",
                        "on" if ENABLE_CTX_TICK_SUBSCRIBE else "off",
                    )
                )
                # 历史回填连续空：多半是留存外/当日无 tick → 跳过该日续队列，不卡 30min
                if _is_historical_day(day_s) and ok_new == 0:
                    _log(
                        "历史日连续空批 → 跳过该日（不进冷却）: %s"
                        % day_s
                    )
                    _append_run_log(
                        day_s,
                        "SKIP 历史空批 " + detail.replace("\n", " | "),
                    )
                    # 空批写入的 miss 不可靠，丢弃本趟 miss 变更
                    miss_dirty = False
                    _mark_skipped_no_tick(day_s, "ctx-empty-historical")
                    _LAST_DONE_DAY = day_s
                    return True
                _log("ABORT ContextInfo tick为空（本地+内置下载+订阅）")
                _append_run_log(
                    day_s, "ABORT ctx-empty " + detail.replace("\n", " | ")
                )
                _notify_ctx_empty(day_s, detail)
                _set_abort_hold(
                    day_s, "ctx-empty", _ABORT_HOLD_SEC_CTX_EMPTY
                )
                break

            time.sleep(0.05)

        if miss_dirty:
            _save_miss_cache(miss_payload)

        total = len(universe)
        ok_total = len(done_set)
        fail_total = len(fail_map)
        elapsed = time.time() - t_start
        elapsed_min = elapsed / 60.0
        if aborted_yield:
            # 进度已落盘；不标 done，下次续跑。退回主循环让账户/行情先更新。
            msg = (
                "本片结束 成功=%d 失败=%d 待跑剩余=%d 总数=%d "
                "耗时=%.1fmin (%.0fs) %s -> %s"
                % (
                    ok_total,
                    fail_total,
                    max(0, len(pending) - ok_new - fail_new - skip_exist),
                    total,
                    elapsed_min,
                    elapsed,
                    started_at,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
            )
            _log(msg)
            _append_run_log(day_s, msg)
            return False
        if aborted_conn or aborted_ctx_empty or aborted_code_bug:
            tag = (
                "code-bug"
                if aborted_code_bug
                else ("conn-dead" if aborted_conn else "ctx-empty")
            )
            msg = (
                "已中止(%s) 成功=%d 失败=%d 总数=%d 耗时=%.1fmin (%.0fs) %s -> %s"
                % (
                    tag,
                    ok_total,
                    fail_total,
                    total,
                    elapsed_min,
                    elapsed,
                    started_at,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
            )
            _log(msg)
            _append_run_log(day_s, msg)
            return False

        _mark_done(
            day_s,
            ok_total,
            fail_total,
            total,
            elapsed_sec=elapsed,
            started_at=started_at,
        )
        _LAST_DONE_DAY = day_s
        removed = _purge_old_tick_dirs()
        msg = (
            "完成 成功=%d 失败=%d 总数=%d 已清理=%d 耗时=%.1fmin (%.0fs) %s -> %s"
            % (
                ok_total,
                fail_total,
                total,
                len(removed),
                elapsed_min,
                elapsed,
                started_at,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        )
        _log(msg)
        _append_run_log(day_s, msg)
        if removed:
            _log("已清理日期: %s" % ",".join(removed[:10]))
        ok_enough = ok_total >= max(1000, int(total * 0.5))
        if not ok_enough:
            try:
                try:
                    import ant_server_chan as sct
                except ImportError:
                    import qmt_builtin.ant_server_chan as sct
                sct.notify_alert(
                    "全A tick落盘成功过少",
                    "日=%s\nok=%d fail=%d total=%d\n请检查后补跑 tick_full_sync。"
                    % (day_s, ok_total, fail_total, total),
                    alert_key="tick_full_low_%s" % day_s,
                    cooldown_sec=3600,
                )
            except Exception:
                pass
        return ok_enough
    except Exception as e:
        elapsed = time.time() - t_start
        _log(
            "失败 已跑%.1fmin: %s: %s"
            % (elapsed / 60.0, type(e).__name__, e)
        )
        _append_run_log(
            day_s, "FAILED 已跑%.1fs: %s" % (elapsed, e)
        )
        try:
            try:
                import ant_server_chan as sct
            except ImportError:
                import qmt_builtin.ant_server_chan as sct
            sct.notify_alert(
                "全A tick落盘异常退出",
                "日=%s\n%s: %s" % (day_s, type(e).__name__, e),
                alert_key="tick_full_fail_%s" % day_s,
                cooldown_sec=3600,
            )
        except Exception:
            pass
        return False
    finally:
        _BUSY = False


def _manual_request_path():
    # type: () -> str
    return os.path.join(_data_dir(), "tick_full_sync", _MANUAL_REQUEST_NAME)


def _normalize_manual_days(req):
    # type: (Dict[str, Any]) -> List[str]
    """从 day / days 字段抽出 YYYYMMDD 队列（去重、保序）。"""
    out = []  # type: List[str]
    seen = set()  # type: Set[str]

    def _push(raw):
        # type: (Any) -> None
        ds = str(raw or "").replace("-", "").replace("/", "")[:8]
        if len(ds) != 8 or (not ds.isdigit()):
            return
        if ds in seen:
            return
        seen.add(ds)
        out.append(ds)

    days_raw = req.get("days")
    if isinstance(days_raw, list):
        for item in days_raw:
            _push(item)
    elif days_raw not in (None, ""):
        _push(days_raw)
    # 兼容旧单日字段；若 days 已有则 day 仅在未出现时追加到队首意图由调用方保证
    if not out:
        _push(req.get("day"))
    elif req.get("day") not in (None, ""):
        # days 优先；忽略重复的 day
        pass
    return out


def _write_manual_request_remaining(path, req, remaining):
    # type: (str, Dict[str, Any], List[str]) -> None
    """把尚未执行的日期写回 manual_request.json（原子替换）。"""
    payload = dict(req) if isinstance(req, dict) else {}
    payload["days"] = list(remaining)
    if "day" in payload:
        payload.pop("day", None)
    payload["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    payload["version"] = TICK_FULL_SYNC_VERSION
    folder = os.path.dirname(path) or "."
    if not os.path.isdir(folder):
        os.makedirs(folder)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _try_after_rank_manual_request(ContextInfo=None):
    # type: (Any) -> None
    """顺带消化 data/after_hours_rank/manual_request.json（热重载路径可触达）。"""
    try:
        try:
            import ant_after_hours_rank_runner as ar
        except ImportError:
            import qmt_builtin.ant_after_hours_rank_runner as ar
        # 按文件 mtime 热重载，确保吃到 force/manual_request 新版
        import importlib

        ar = importlib.reload(ar)
        fn = getattr(ar, "process_manual_request", None)
        if callable(fn):
            fn(ContextInfo)
    except Exception as e:
        _log("串联量能手动请求失败: %s" % e)


def process_manual_request(ContextInfo=None):
    # type: (Any) -> Optional[bool]
    """处理 data/tick_full_sync/manual_request.json；无请求返回 None。

    外挂 / 工具写入，大 QMT 策略 periodic_sync 用真实 ContextInfo 执行。

    单日::
        {"day":"20260730","force":false}

    多日队列（每次只跑队首一日，剩余写回文件，下次 periodic 继续）::
        {"days":["20260728","20260729"],"force":false,"limit":0}

    盘中（09:00–15:10）默认 defer，不删队列，避免阻塞交易/策略取数。
    可选 `"allow_intraday": true` 应急强跑（不推荐）。
    勿默认开 enable_xtdata_download（miniQMT）。成功拉起后写入
    manual_request.done.json；未完成（time-slice）则把当日留在队首。
    超出券商 tick 留存 / 历史日连续空批：跳过该日并续下一队列日（不 abort-hold）。
    无论 tick 队列有无，结束时都会尝试 after_hours_rank/manual_request.json。
    """
    try:
        return _process_manual_request_body(ContextInfo)
    finally:
        _try_after_rank_manual_request(ContextInfo)


def _process_manual_request_body(ContextInfo=None):
    # type: (Any) -> Optional[bool]
    global ENABLE_XTDATA_TICK_DOWNLOAD, _PROTECT_DEFER_LOG_TS, _PAUSE_DEFER_LOG_TS
    global _MANUAL_KEEP_LOG_TS, _MANUAL_HOLD_LOG_TS, _ABORT_HOLD_LOG_TS

    path = _manual_request_path()
    if not os.path.isfile(path):
        return None
    if _BUSY:
        return False
    if _pause_requested():
        ts = time.time()
        if ts - _PAUSE_DEFER_LOG_TS >= 60.0:
            _PAUSE_DEFER_LOG_TS = ts
            _log("手动请求暂缓：PAUSE标志")
        return False
    if _daily_sync_running():
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            req = json.load(f) or {}
    except Exception as e:
        _log("手动请求读取失败: %s" % e)
        return False
    if not isinstance(req, dict):
        return False
    allow_intraday = bool(req.get("allow_intraday"))
    if (not allow_intraday) and _in_market_hours_protect():
        ts = time.time()
        if ts - _PROTECT_DEFER_LOG_TS >= 60.0:
            _PROTECT_DEFER_LOG_TS = ts
            _log(
                "手动请求暂缓：盘中保护 "
                "（队列保留；%s后继续）"
                % _MARKET_PROTECT_END.strftime("%H:%M")
            )
        return False
    days = _normalize_manual_days(req)
    if not days:
        _log("手动请求日期无效: %s" % (req.get("days") or req.get("day")))
        try:
            os.remove(path)
        except Exception:
            pass
        return False
    force = bool(req.get("force"))
    try:
        limit = int(req.get("limit") or 0)
    except (TypeError, ValueError):
        limit = 0
    if "enable_xtdata_download" in req:
        ENABLE_XTDATA_TICK_DOWNLOAD = bool(req.get("enable_xtdata_download"))

    # 队首连消：已完成 / 历史空批跳过 — 一次推进。
    # 留存窗外不再在此自动跳过：manual_request 里的日期是用户显式点名。
    skipped_head = []  # type: List[str]
    while days:
        head = days[0]
        if _day_already_done(head):
            skipped_head.append(head)
            days = days[1:]
            continue
        if not force:
            held, hold_reason = _abort_hold_active(head)
            if held:
                hr = str(hold_reason or "")
                if _is_historical_day(head) and (
                    "ctx-empty" in hr or "conn-dead" in hr
                ):
                    _mark_skipped_no_tick(head, "historical_%s" % hr[:60])
                    skipped_head.append(head)
                    days = days[1:]
                    continue
                # 今日 abort-hold：节流日志，不清 hold、不刷 MANUAL_REQUEST
                ts = time.time()
                if ts - _MANUAL_HOLD_LOG_TS >= float(_MANUAL_LOG_INTERVAL_SEC):
                    _MANUAL_HOLD_LOG_TS = ts
                    _ABORT_HOLD_LOG_TS = ts
                    _log(
                        "手动请求暂缓：中止冷却 day=%s 原因=%s 剩余=%d"
                        % (head, hr[:60], max(0, len(days) - 1))
                    )
                    _append_run_log(
                        head,
                        "MANUAL_REQUEST 暂缓 中止冷却 reason=%s remain=%d"
                        % (hr[:60], max(0, len(days) - 1)),
                    )
                return False
        break

    if skipped_head:
        _log(
            "手动请求跳过已完成/空批 %d 日: %s"
            % (len(skipped_head), ",".join(skipped_head[:8]))
        )
    if not days:
        try:
            os.remove(path)
        except Exception:
            pass
        _log("手动请求队列已空（跳过/完成后）")
        return True

    day_s = days[0]
    remaining = days[1:]
    # 仅 force 时清 abort-hold；默认尊重冷却，避免每秒重跑刷日志
    if force:
        _clear_abort_hold(day_s)

    _log(
        "手动请求 day=%s 剩余=%d force=%s limit=%s xtdata_dl=%s src=%s"
        % (
            day_s,
            len(remaining),
            force,
            limit,
            "on" if ENABLE_XTDATA_TICK_DOWNLOAD else "off",
            str(req.get("source") or "")[:40],
        )
    )
    _append_run_log(
        day_s,
        "MANUAL_REQUEST force=%s limit=%s remain=%d xtdata_dl=%s version=%s src=%s"
        % (
            force,
            limit,
            len(remaining),
            "on" if ENABLE_XTDATA_TICK_DOWNLOAD else "off",
            TICK_FULL_SYNC_VERSION,
            str(req.get("source") or "")[:40],
        ),
    )
    try:
        done_path = path + ".done.json"
        with open(done_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "day": day_s,
                    "remaining": remaining,
                    "force": force,
                    "limit": limit,
                    "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "version": TICK_FULL_SYNC_VERSION,
                    "source": str(req.get("source") or ""),
                    "skipped_ahead": skipped_head,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception as e:
        _log("手动请求归档失败: %s" % e)
        return False

    ok = run_tick_full_sync(
        ContextInfo,
        day=day_s,
        force=force,
        limit=limit,
        allow_intraday=allow_intraday,
        skip_retention_gate=True,
    )
    finished = bool(ok) or _day_already_done(day_s)
    try:
        if finished:
            if remaining:
                _write_manual_request_remaining(path, req, remaining)
            else:
                if os.path.isfile(path):
                    os.remove(path)
        else:
            # time-slice / 今日 ABORT：当日留队首，下次 periodic 续跑
            _write_manual_request_remaining(path, req, [day_s] + list(remaining))
            ts = time.time()
            if ts - _MANUAL_KEEP_LOG_TS >= float(_MANUAL_LOG_INTERVAL_SEC):
                _MANUAL_KEEP_LOG_TS = ts
                _log(
                    "手动请求保留 day=%s 于队首（未完成）剩余=%d"
                    % (day_s, len(remaining))
                )
    except Exception as e:
        _log("手动请求队列更新失败: %s" % e)
        # 归档失败时勿丢队：尽量保留原文件
        return False
    return ok


def tick_full_sync(ContextInfo):
    # type: (Any) -> bool
    """兼容旧 timer 入口；若有 manual_request 则优先按指定日续跑。

    多日队列：每次只消化队首一日，剩余留在 manual_request.json，
    由 periodic_sync / 下次 timer 继续（避免单次入口卡死数日）。
    """
    manual = process_manual_request(ContextInfo)
    if manual is not None:
        return bool(manual)
    return run_post_daily_pipeline(ContextInfo)


def run_post_daily_pipeline(ContextInfo=None):
    # type: (Any) -> bool
    """日线之后：tick 落盘 → 盘后量能 → 板块同步。"""
    now = datetime.now()
    day = now.strftime("%Y%m%d")
    # 日线未完成则不抢下载（兜底；正常已由 daily 闸门挡住）
    if not _daily_gate_open():
        _log("等待日线同步完成后再跑分笔")
        return False
    ok = run_tick_full_sync(ContextInfo, force=False)
    if ok or _day_already_done(day):
        _run_after_rank_after_sync(ContextInfo, day)
    _chain_sector_after_pipeline(ContextInfo)
    return ok


def _daily_gate_open():
    # type: () -> bool
    try:
        try:
            import ant_daily_sync_runner as daily
        except ImportError:
            import qmt_builtin.ant_daily_sync_runner as daily
        if hasattr(daily, "_daily_gate_open_for_tick"):
            return bool(daily._daily_gate_open_for_tick())
    except Exception:
        pass
    return True


def _run_after_rank_after_sync(ContextInfo, day):
    # type: (Any, str) -> None
    """落盘就绪后触发盘后量能（读本地 ticks）。"""
    try:
        try:
            import ant_after_hours_rank_runner as ar
        except ImportError:
            import qmt_builtin.ant_after_hours_rank_runner as ar
        if hasattr(ar, "is_rank_done") and ar.is_rank_done(day):
            return
        if getattr(ar, "_BUSY", False):
            return
        _log("串联盘后量能 day=%s" % day)
        ar.run_after_hours_rank(ContextInfo, day=day, force=False)
    except Exception as e:
        _log("串联盘后量能失败: %s" % e)


def _chain_sector_after_pipeline(ContextInfo):
    # type: (Any) -> None
    """量能之后：板块成分同步（后台，不挡主流程）。"""
    try:
        sector = _load_sector_sync_runner()
        if hasattr(sector, "run_after_hours_pipeline"):
            sector.run_after_hours_pipeline(ContextInfo)
        elif hasattr(sector, "_start_sector_sync_bg"):
            _log("串联板块同步（后台）")
            sector._start_sector_sync_bg(ContextInfo, "after_hours_pipeline", force=True)
    except Exception as e:
        _log("串联板块同步失败: %s" % e)


def maybe_catch_up_tick_full_sync(ContextInfo=None):
    # type: (Any) -> bool
    """盘后补跑（优先走 daily 的整条 pipeline；此处仅作 tick 闸门兜底）。"""
    global _CATCHUP_LOG_TS, _ABORT_HOLD_LOG_TS

    if _BUSY:
        return False

    now = datetime.now()
    day = now.strftime("%Y%m%d")
    if now.time() < dt_time(SYNC_HOUR, SYNC_MINUTE):
        return False
    if not _daily_gate_open():
        return False

    if day == _LAST_DONE_DAY or _day_already_done(day):
        _run_after_rank_after_sync(ContextInfo, day)
        _chain_sector_after_pipeline(ContextInfo)
        return True

    held, hold_reason = _abort_hold_active(day)
    if held:
        ts = time.time()
        if ts - _ABORT_HOLD_LOG_TS >= 120.0:
            _ABORT_HOLD_LOG_TS = ts
            _log(
                "补跑跳过：中止冷却 day=%s 原因=%s"
                % (day, (hold_reason or "")[:80])
            )
        return False

    ts = time.time()
    if ts - _CATCHUP_LOG_TS >= 120.0:
        _CATCHUP_LOG_TS = ts
        _log(
            "需补跑 day=%s（日线后 %02d:%02d）"
            % (day, SYNC_HOUR, SYNC_MINUTE)
        )

    return run_post_daily_pipeline(ContextInfo)


def register_tick_full_sync_timer(ContextInfo):
    # type: (Any) -> None
    """不再单独注册定时：由 daily_bar_sync(15:35) 完成后串行触发。"""
    _log(
        "定时器跳过（由日线同步串行触发）版本=%s"
        % TICK_FULL_SYNC_VERSION
    )


def tick_probe(ContextInfo, day="20260730"):
    # type: (Any, str) -> Any
    """策略函数：探测内置 download + ContextInfo tick 变体（2–3 只流动性票）。

    模型交易里添加/调用一次本函数即可；结果写 data/tick_full_sync/tick_probe_*.log
    并打印 [tick_probe] 行。
    """
    try:
        tick_io = _import_tick_io()
    except Exception as e:
        _log("tick_probe导入失败: %s" % e)
        return None
    try:
        if hasattr(tick_io, "bind_download_history_data"):
            tick_io.bind_download_history_data(None)
    except Exception:
        pass
    fn = getattr(tick_io, "run_tick_api_probe", None)
    if not callable(fn):
        _log("tick_probe: 缺少run_tick_api_probe（请重部署ant_tick_cache_io）")
        return None
    day_s = str(day or "20260730").replace("-", "").replace("/", "")[:8]
    _log("tick_probe开始 day=%s 版本=%s" % (day_s, TICK_FULL_SYNC_VERSION))
    return fn(ContextInfo, day=day_s)
