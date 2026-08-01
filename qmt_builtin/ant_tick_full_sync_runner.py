#coding:gbk
"""�� QMT �ڣ��̺�ȫ A ���� tick ���̵� data/ticks/{YYYYMMDD}/*.parquet��

������ͬ����15:35����ɺ��д��������̺���Լ 15:31 ���̺� tick��
��·����ѸͶ�ٷ�����
  1) ContextInfo.get_local_data / get_market_data_ex(subscribe=False) ������
  2) ���� download_history_data(code,"tick",YYYYMMDD,YYYYMMDD) �����أ��� xtdata��
  3) �� get_market_data_ex(subscribe=False)
  4) ��ѡ subscribe=True���������壬������ʷ supply��
Ĭ�Ϲر� xtdata.download_history_*��ͬ���� ENABLE_XTDATA_DOWNLOAD�������� miniQMT RPC ˢ����
�ɶϵ����ܣ��������п��� tick��parquet ��� pkl����������
ע�⣺�� pkl Ҳ�㡸������/�����ݡ�����������ȫ���� parquet��
��ɾ pkl������ͬ��������У��ͨ���� parquet������Ǩ�Ƽ� tools/convert_tick_pkl_to_parquet.py��
���̳ɹ���������ɣ����д����̺����ܣ����ܶ��������̣���
�����󰴴���ʣ��ռ����������Ŀ¼���ռ乻��ɾ���ɴ泬�������£���
���ݴ� QMT ���� Python 3.6����ֹ from __future__ import annotations����
"""
import json
import os
import shutil
import time
from datetime import date, datetime, timedelta, time as dt_time
from typing import Any, Dict, List, Optional, Set, Tuple

TICK_FULL_SYNC_VERSION = "20260801.06"
# ������ͬ�㣺���� catch-up �жϣ���ʽ������ daily_sync ���д���
SYNC_HOUR = 15
SYNC_MINUTE = 35
BATCH_SIZE = 20
# ���̷�ʣ��ռ����������� MIN ��ɾ�����Ŀ¼��ɾ�� TARGET ��ֻʣ MIN_KEEP_DAYS
TICK_MIN_FREE_GB = 40.0
TICK_TARGET_FREE_GB = 60.0
TICK_MIN_KEEP_DAYS = 20
# ȯ�� tick ����Լ 20�C30 �������գ������򱾵�/download ������Ϊ�գ��� API ����
TICK_RETENTION_CALENDAR_DAYS = 28
# �� QMT-only��Ĭ�Ϲر� xtdata.download_history_*������ miniQMT 58610����
# �����Կ���ʱ True��������ȱ������ download_history_data���� ENABLE_BUILTIN_TICK_DOWNLOAD����
# download �������� YYYYMMDD����ʱ����ᵼ�� download2 �յȳ�ʱ��
ENABLE_XTDATA_TICK_DOWNLOAD = False
# �ٷ���·����ģ�ͽ������� download_history_data���� xtdata��
ENABLE_BUILTIN_TICK_DOWNLOAD = True
# ����+builtin �Կ�ʱ���� subscribe=True���������壬����ʷ supply��
ENABLE_CTX_TICK_SUBSCRIBE = True
_DOWNLOAD_WAIT_SEC = 120
# ��������Ϊ��֪��Ʊ������ download �ȴ�������ÿ���յ� 120s
_DOWNLOAD_WAIT_SEC_FAST = 8
# �������������س�ʱ�ұ�������ȫʧ�ܡ��� ���������������ֹ�� Server���澯
_CONN_FAIL_ABORT_BATCHES = 3
# ContextInfo ��������ȫ�գ������� tick �� subscribe Ҳ�����ݣ���ͣ�����ת��Сʱ
_CTX_EMPTY_ABORT_BATCHES = 5
# ABORT ����ȴ�������ڡ����ա�����ϣ���ʷ�����/������Ϊ�������ղ�������
_ABORT_HOLD_SEC_CODEBUG = 6 * 3600
_ABORT_HOLD_SEC_CTX_EMPTY = 30 * 60
# manual_request / keep ��־�������룩
_MANUAL_LOG_INTERVAL_SEC = 120.0
# ���б�����ȫ A tick / manual_request �������̣߳������� intraday ���ڶ��벢�Կ���
# ���� download_history_data ѭ������� periodic_sync �� results.json/�˻�/����ȡ��ȫͣ
_MARKET_PROTECT_START = dt_time(9, 0)
_MARKET_PROTECT_END = dt_time(15, 30)
# ����ռ�����߳����ޣ������ progress ���˻أ����˻�/on_demand/������������
_TIME_SLICE_SEC = 60.0
_PROGRESS_NAME = "_full_sync_progress.json"
_DONE_MARKER = "_full_sync_done.json"
_ABORT_HOLD_NAME = "_full_sync_abort_hold.json"
_MANUAL_REQUEST_NAME = "manual_request.json"
_PAUSE_FLAG_NAME = "PAUSE"
_MISS_CACHE_NAME = "sync_miss_codes.json"
# ������ȷ��������/���У������� daily �� local_miss / today_halt
_MISS_SKIP_REASONS = ("empty_history", "delisted")
# ��Щʧ��ԭ�򲻵�д�� miss_cache������/API ���⣬����������ʷ��
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
    print("[�ֱ�ͬ��] %s" % msg)


def _in_market_hours_protect(now=None):
    # type: (Optional[datetime]) -> bool
    """���������У���ֹ����ȫ A tick �ػ������������/����ȡ����"""
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
    """����ȫ��/ FORCE ����ռ��ͬһ ContextInfo ���߳�ʱ���п� tick��"""
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


def _ticks_day_dir(day, ensure=False):
    # type: (str, bool) -> str
    """ticks ��Ŀ¼·����ensure=True ʱ�Ŵ�����д progress/done �ã���

    ̽���Ƿ���� / abort-hold ʱ�� mkdir������ǽ����� catch-up �����¿�Ŀ¼��
    �̺����ܰѡ���Ŀ¼�����гɽ����գ�һֱ���ȴ��ֱ�ͬ������
    """
    path = os.path.join(_project_root(), "data", "ticks", day)
    if ensure and (not os.path.isdir(path)):
        try:
            os.makedirs(path)
        except Exception:
            pass
    return path


def _progress_path(day):
    # type: (str) -> str
    return os.path.join(_ticks_day_dir(day, ensure=False), _PROGRESS_NAME)


def _done_path(day):
    # type: (str) -> str
    return os.path.join(_ticks_day_dir(day, ensure=False), _DONE_MARKER)


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
        _log("xtdata����ʧ��: %s" % e)
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
    """���� data/a_share_universe.json��daily_sync ���̣������������� RPC��"""
    path = os.path.join(_project_root(), "data", "a_share_universe.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f) or {}
        raw = list(payload.get("codes") or [])
        out = [str(c).strip() for c in raw if str(c).strip()]
        if out:
            _log("��Ʊ�ػ����ļ� n=%d" % len(out))
        return out
    except Exception as e:
        _log("��Ʊ���ļ�ʧ��: %s" % e)
        return []


def _load_universe(xtdata, limit=0, ContextInfo=None):
    # type: (Any, int, Any) -> List[str]
    # ����� unicode ת�壬���� GBK/�����𻵵��¿ճ�
    sectors = (
        "\u6caa\u6df1A\u80a1",  # ����A��
        "\u4e0a\u8bc1A\u80a1",  # ��֤A��
        "\u6df1\u8bc1A\u80a1",  # ��֤A��
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
        _log("��Ʊ����Դ=%s n=%d" % (src, len(out)))
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
        _log("miss���汣��ʧ��: %s" % e)


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
    """AttributeError / ȱ API �Ȳ��ü��� empty_history miss_cache��"""
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
        parent = os.path.dirname(path)
        if parent and (not os.path.isdir(parent)):
            os.makedirs(parent)
        with open(tmp, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        if os.path.exists(path):
            os.remove(path)
        os.rename(tmp, path)
    except Exception as e:
        _log("���ȱ���ʧ��: %s" % e)


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
    """����ȯ�� tick ���洰�ڣ�����Ȼ�գ���"""
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
    """��Ǹ��ն��п��ƽ���ȯ���� tick / ��ʷ�������ǳɹ����̡�"""
    reason_s = str(reason or "beyond_retention")[:120]
    beyond = _day_beyond_tick_retention(day_s)
    extra = {
        "skipped_no_tick": True,
        "skipped_retention": bool(beyond) or reason_s.startswith("beyond_"),
        "skip_reason": reason_s,
    }
    _mark_done(day_s, 0, 0, 0, elapsed_sec=0.0, extra=extra)
    msg = (
        "SKIP ��tick�� day=%s reason=%s������ȯ��tick�������ʷ�����ݣ���������һ�����գ�"
        % (day_s, reason_s)
    )
    if beyond or reason_s.startswith("beyond_"):
        msg = "SKIP ����ȯ��tick���� day=%s reason=%s" % (day_s, reason_s)
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
        # �ɰ�����δ���̺�ʱ�Σ����ܵ��ɱ�����ɣ��������ܶ��� after_vol ȫ 0��
        ver = str(data.get("version") or "")
        if ver < "20260728.04":
            return False
        # ���洰�� / ��ʷ���������������� catch-up ��Ϊ�Ѵ���
        if data.get("skipped_retention") or data.get("skipped_no_tick"):
            return True
        total = int(data.get("total") or 0)
        ok = int(data.get("ok") or 0)
        return total > 0 and ok >= max(1000, int(total * 0.8))
    except Exception:
        return False


def _abort_hold_path(day):
    # type: (str) -> str
    return os.path.join(_ticks_day_dir(day, ensure=False), _ABORT_HOLD_NAME)


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
        "��ֹ��ȴ�� %s ԭ��=%s"
        % (payload["until"], _ABORT_HOLD_REASON[:80])
    )


def _abort_hold_active(day_s):
    # type: (str) -> Tuple[bool, str]
    """���� ABORT ��ȴ�����������ܣ����� miss_cache �������Ŵ�"""
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
    """�� mtime ������ ant_tick_cache_io������ deploy ����ȱ fetch_ticks_batch��"""
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
            "tick_io������ �汾=%s ����ȡ=%s"
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
    """�� mtime �����ذ��ͬ����������������� 20260728.2��"""
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
            "���ģ�������� �汾=%s"
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
    """�ռ乻��ɾ������ʱ�������Ŀ¼ɾ��Ŀ��ʣ���ֻʣ min_keep_days��"""
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
            "��������: ʣ��=%.1fGB >= ����=%.1fGB������%d����Ŀ¼��"
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
            _log("��������Ŀ¼ %s������ǰʣ��%.1fGB��" % (ymd, (free_now or 0) / (1024.0 ** 3)))
        except Exception as e:
            _log("���� %s ʧ��: %s" % (ymd, e))
    return removed


def _download_batch_builtin(tick_io, full_codes, ymd):
    # type: (Any, List[str], str) -> Tuple[int, str]
    """�� QMT ���� download_history_data ���������� tick���� 58610����"""
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
    """��ѡ xtdata �������أ�Ĭ�Ϲرա������Ƿ�ȴ��ص���ʱ������ͨ����ͨ����"""
    if not ENABLE_XTDATA_TICK_DOWNLOAD:
        return False
    if xtdata is None:
        return True
    # download_history_* ������ YYYYMMDD���� 091500/153100 ʱ download2 ���յȳ�ʱ
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
                    "download2�ȴ���ʱ %ss������=%d��"
                    % (int(wait), len(full_codes or []))
                )
        else:
            for fc in full_codes:
                try:
                    xtdata.download_history_data(fc, "tick", day_s, day_s)
                except Exception:
                    pass
    except Exception as e:
        _log("�������ش���: %s" % e)
        timed_out = True
    return timed_out


def _notify_conn_dead(day_s, detail):
    # type: (str, str) -> None
    try:
        try:
            import ant_server_chan as sct
        except ImportError:
            import qmt_builtin.ant_server_chan as sct
        title = "��QMT�����������"
        body = (
            "ȫA tick��������������ʱ/ʧ�ܣ�����ֹ�������QMT�������Ӻ����ز��ԡ�\n\n"
            "��=%s\n�汾=%s\n%s"
            % (day_s, TICK_FULL_SYNC_VERSION, detail)
        )
        r = sct.notify_alert(
            title, body, alert_key="tick_full_sync_conn_%s" % day_s, cooldown_sec=3600
        )
        if r.get("skipped"):
            _log("server��������: %s" % r.get("message"))
        elif r.get("success"):
            _log("server���澯�ѷ� (%s)" % r.get("source"))
        else:
            _log("server���澯ʧ��: %s" % r.get("message"))
    except Exception as e:
        _log("server���澯�쳣: %s" % e)


def _notify_ctx_empty(day_s, detail):
    # type: (str, str) -> None
    try:
        try:
            import ant_server_chan as sct
        except ImportError:
            import qmt_builtin.ant_server_chan as sct
        title = "ȫA tick����/������������"
        body = (
            "���ض� + ���� download_history_data + get_market_data_ex(tick) "
            "��������Ϊ�գ�����ֹ��\n"
            "˵�����ٷ���ȷ·������ download_history_data(period=tick,YYYYMMDD)��"
            "�� get_market_data_ex(subscribe=False)��subscribe�ٲ���ʷ��\n"
            "��һ������ģ�ͽ����ܲ��Ժ��� tick_probe ��������������ݣ�"
            "�� UI ���ݹ������ֱʡ���ֹĬ���� miniQMT��\n\n"
            "��=%s\n�汾=%s\n%s"
            % (day_s, TICK_FULL_SYNC_VERSION, detail)
        )
        r = sct.notify_alert(
            title, body, alert_key="tick_full_sync_empty_%s" % day_s, cooldown_sec=3600
        )
        if r.get("skipped"):
            _log("server��������: %s" % r.get("message"))
        elif r.get("success"):
            _log("server���澯�ѷ� (%s)" % r.get("source"))
        else:
            _log("server���澯ʧ��: %s" % r.get("message"))
    except Exception as e:
        _log("server���澯�쳣: %s" % e)


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
    """ͬ��ָ���գ�Ĭ�Ͻ��죩ȫ A tick ����Ŀ data/ticks��

    allow_intraday=True ��Ӧ����Ĭ�����оܾ������������������/����ȡ����
    skip_retention_gate=True��manual_request ��ʽ�Ŷ��ղ������洰�Զ�������
    �������ռ�����߳�Լ _TIME_SLICE_SEC�����ڴ� progress �˻أ��ɶϵ����ܣ���
    """
    global _BUSY, _LAST_DONE_DAY, _ABORT_HOLD_LOG_TS, _PROTECT_DEFER_LOG_TS
    global _PAUSE_DEFER_LOG_TS

    if _BUSY:
        _log("æµ�У�����")
        return False

    if _pause_requested():
        ts = time.time()
        if ts - _PAUSE_DEFER_LOG_TS >= 60.0:
            _PAUSE_DEFER_LOG_TS = ts
            _log("����ͣ��ɾ�� data/tick_full_sync/PAUSE �����")
        return False

    if (not allow_intraday) and _in_market_hours_protect():
        ts = time.time()
        if ts - _PROTECT_DEFER_LOG_TS >= 60.0:
            _PROTECT_DEFER_LOG_TS = ts
            _log(
                "�ݻ������б��� %s-%s�����Ƚ���/����ȡ����"
                % (
                    _MARKET_PROTECT_START.strftime("%H:%M"),
                    _MARKET_PROTECT_END.strftime("%H:%M"),
                )
            )
        return False

    if _daily_sync_running():
        _log("�ݻ�������ͬ�������У�����ContextInfo��")
        return False

    xtdata = _load_xtdata()
    if xtdata is None and ContextInfo is None:
        _log("��ContextInfo����xtdata")
        return False

    now = datetime.now()
    if day:
        day_s = str(day).replace("-", "").replace("/", "")[:8]
        trade_d = date(int(day_s[0:4]), int(day_s[4:6]), int(day_s[6:8]))
    else:
        trade_d = now.date()
        day_s = trade_d.strftime("%Y%m%d")

    if xtdata is not None and not _is_tradeday(xtdata, trade_d):
        _log("�ǽ�����: %s" % day_s)
        return False
    if xtdata is None and trade_d.weekday() >= 5:
        _log("�ǽ����գ���ĩ��: %s" % day_s)
        return False

    if (not force) and (_LAST_DONE_DAY == day_s or _day_already_done(day_s)):
        _log("�����: %s" % day_s)
        return True

    # ����ȯ�� tick ���棺ֱ���������ö���ǰ������ abort-hold����
    # manual_request ��ʽ�Ŷ��տ� skip_retention_gate�������û������ز��� 28 �մ���ɱ��
    if (not force) and (not skip_retention_gate) and _day_beyond_tick_retention(day_s):
        _mark_skipped_no_tick(day_s, "beyond_retention_%dd" % int(TICK_RETENTION_CALENDAR_DAYS))
        _LAST_DONE_DAY = day_s
        return True

    # ABORT ��ȴ����Լ�������ա�����ϣ���ʷ�� ctx-empty ��Ϊ��������
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
                    "��������ֹ��ȴ�� day=%s ԭ��=%s"
                    % (day_s, hr[:80])
                )
            return False

    try:
        tick_io = _import_tick_io()
    except AttributeError as e:
        _log("tick_io������ֹ: %s" % e)
        _append_run_log(day_s, "ABORT code-bug %s" % e)
        _set_abort_hold(day_s, "code-bug:%s" % e, _ABORT_HOLD_SEC_CODEBUG)
        return False
    # ���񿪹أ����� GBK ������ src ��һ��ʱ��ȥ download / �ص� subscribe
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
        _log("��Ʊ��Ϊ��")
        # д�� run.log�������̺��Ų飨��ǰ�� print��Ŀ¼������ START��
        _append_run_log(
            day_s,
            "ABORT ��Ʊ��Ϊ�� version=%s����ContextInfo���ļ����ˣ�"
            % TICK_FULL_SYNC_VERSION,
        )
        return False

    # �ɰ����̽ض��� 15:00�����������������̺�ʱ�Σ��������ܶ��� after_vol ȫ 0
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
                    "�ɰ�ͬ�� version=%s �� ���̺����� %s"
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
        "��ʼ day=%s ��Ʊ��=%d �汾=%s xtdata_dl=%s builtin_dl=%s "
        "ctx_sub=%s ��·��=download_history_data+get_market_data_ex at=%s"
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
        "START n=%d force=%s �ɰ�����=%s xtdata_dl=%s builtin_dl=%s "
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
            # ������/ȷ������ʷ�������� local_miss / today_halt / invalid_0
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
            "miss��������=%d ����=%d path=%s"
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
                _log("��������ͣ��������Ⱥ��ó�")
                _append_run_log(day_s, "YIELD ��ͣ ʣ��=%d" % (len(pending) - i))
                break
            if (not allow_intraday) and _in_market_hours_protect():
                aborted_yield = True
                _log("���������б������ó�")
                _append_run_log(
                    day_s, "YIELD ���б��� ʣ��=%d" % (len(pending) - i)
                )
                break
            if (time.time() - t_start) >= float(_TIME_SLICE_SEC):
                aborted_yield = True
                _log(
                    "ʱ��Ƭ�ó� ����%.0fs��ʣ��Լ%d����������������"
                    % (time.time() - t_start, max(0, len(pending) - i))
                )
                _append_run_log(
                    day_s,
                    "YIELD ʱ��Ƭ sec=%.0f ʣ��=%d"
                    % (time.time() - t_start, len(pending) - i),
                )
                break
            batch = pending[i : i + BATCH_SIZE]
            # ��·��Ԥ���أ����� download_history_data��YYYYMMDD����xtdata Ĭ�Ϲ�
            n_builtin, builtin_err = _download_batch_builtin(tick_io, batch, day_s)
            if n_builtin > 0:
                _log(
                    "����download_history_data �ɹ�=%d/%d day=%s"
                    % (n_builtin, len(batch), day_s)
                )
            elif builtin_err and builtin_err not in ("builtin_dl_off",):
                _log("�������ر�ע: %s" % builtin_err)
            timed_out = _download_batch(
                xtdata, batch, day_s, wait_sec=next_wait
            )
            # ��·�������� ������ builtin download���� get_market_data_ex
            batch_map = {}  # type: Dict[str, Any]
            batch_fetch_ok = True
            batch_fetch_err = ""
            try:
                # ��ǰ��ȫ�ɹ����� download������ʧ������ fetch ��ȱƱ����
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
                # �� GBK �������� allow_builtin_download ����
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
                    _log("��ȡtick����(code-bug): %s" % e)
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
                    _log("��ȡtick����: %s" % e)
                    batch_map = {}
            except AttributeError as e:
                batch_fetch_ok = False
                batch_fetch_err = "%s" % e
                _log("��ȡtick����(code-bug): %s" % e)
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
                _log("��ȡtick����: %s" % e)
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
                    # �����Թ� ContextInfo/local��ȱƱ���ٵ�Ʊ�ش򣨷��չң�
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
                    # �� API �������ؿղż� miss��AttributeError/��ʧ�ܲ�д miss_cache
                    if (
                        batch_fetch_ok
                        and str(reason or "") in ("empty_tick", "empty")
                        and not _miss_reason_blocked(reason)
                    ):
                        _miss_put(miss_payload, full, "empty_tick", trade_d)
                        miss_dirty = True
            # ��������ȫ�գ��������ö� timeout���� xtdata_dl=on ʱ�����壩
            if batch_ok == 0 and batch_fail >= max(1, int(len(batch) * 0.8)):
                next_wait = float(_DOWNLOAD_WAIT_SEC_FAST)
            else:
                next_wait = float(_DOWNLOAD_WAIT_SEC)
            progress["done"] = sorted(list(done_set))
            progress["fail"] = fail_map
            _save_progress(day_s, progress)
            _log(
                "���� %d/%d �����ɹ�=%d ����ʧ��=%d ��������=%d miss����=%d "
                "��������=%d ��ʱ=%s ��ȡok=%s"
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
                # �� AttributeError ����ʧ�ܣ������Ѽ� progress������ miss���ƿ��� streak
                pass

            # ���س�ʱ�ұ��������޳ɹ� �� ��Ϊͨ���쳣���� xtdata_dl=on��
            if (
                ENABLE_XTDATA_TICK_DOWNLOAD
                and timed_out
                and batch_ok == 0
                and batch_fail >= max(1, int(len(batch) * 0.5))
            ):
                conn_fail_streak += 1
                _log(
                    "���ƶ������� %d/%d����ʱ+�����գ�"
                    % (conn_fail_streak, _CONN_FAIL_ABORT_BATCHES)
                )
            else:
                conn_fail_streak = 0

            if conn_fail_streak >= int(_CONN_FAIL_ABORT_BATCHES):
                aborted_conn = True
                detail = (
                    "�Ѵ��� %d/%d\nok_new=%d fail_new=%d\n�����쳣��=%d"
                    % (
                        min(i + BATCH_SIZE, len(pending)),
                        len(pending),
                        ok_new,
                        fail_new,
                        conn_fail_streak,
                    )
                )
                _log("ABORT �����������")
                _append_run_log(day_s, "ABORT conn-dead " + detail.replace("\n", " | "))
                _notify_conn_dead(day_s, detail)
                _set_abort_hold(
                    day_s, "conn-dead", _ABORT_HOLD_SEC_CTX_EMPTY
                )
                break

            # ����+builtin download+subscribe ��������ȫ�� �� ��ͣ
            # �� fetch ʧ�ܣ�API �쳣�������롸�桤�����项��Ҳ��д miss
            if (
                (not ENABLE_XTDATA_TICK_DOWNLOAD)
                and batch_fetch_ok
                and batch_ok == 0
                and batch_fail >= max(1, int(len(batch) * 0.8))
                and len(batch_map) == 0
            ):
                ctx_empty_streak += 1
                _log(
                    "����/����/������������ %d/%d"
                    % (ctx_empty_streak, _CTX_EMPTY_ABORT_BATCHES)
                )
            else:
                if batch_fetch_ok:
                    ctx_empty_streak = 0

            if ctx_empty_streak >= int(_CTX_EMPTY_ABORT_BATCHES):
                aborted_ctx_empty = True
                detail = (
                    "�Ѵ��� %d/%d\nok_new=%d fail_new=%d\n��������=%d\n"
                    "builtin_dl=%s ctx_sub=%s������ tick_probe���� miniQMT"
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
                # ��ʷ���������գ������������/������ tick �� �������������У����� 30min
                if _is_historical_day(day_s) and ok_new == 0:
                    _log(
                        "��ʷ���������� �� �������գ�������ȴ��: %s"
                        % day_s
                    )
                    _append_run_log(
                        day_s,
                        "SKIP ��ʷ���� " + detail.replace("\n", " | "),
                    )
                    # ����д��� miss ���ɿ����������� miss ���
                    miss_dirty = False
                    _mark_skipped_no_tick(day_s, "ctx-empty-historical")
                    _LAST_DONE_DAY = day_s
                    return True
                _log("ABORT ContextInfo tickΪ�գ�����+��������+���ģ�")
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
            # ���������̣����� done���´����ܡ��˻���ѭ�����˻�/�����ȸ��¡�
            msg = (
                "��Ƭ���� �ɹ�=%d ʧ��=%d ����ʣ��=%d ����=%d "
                "��ʱ=%.1fmin (%.0fs) %s -> %s"
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
                "����ֹ(%s) �ɹ�=%d ʧ��=%d ����=%d ��ʱ=%.1fmin (%.0fs) %s -> %s"
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
            "��� �ɹ�=%d ʧ��=%d ����=%d ������=%d ��ʱ=%.1fmin (%.0fs) %s -> %s"
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
            _log("����������: %s" % ",".join(removed[:10]))
        ok_enough = ok_total >= max(1000, int(total * 0.5))
        if not ok_enough:
            try:
                try:
                    import ant_server_chan as sct
                except ImportError:
                    import qmt_builtin.ant_server_chan as sct
                sct.notify_alert(
                    "ȫA tick���̳ɹ�����",
                    "��=%s\nok=%d fail=%d total=%d\n������� tick_full_sync��"
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
            "ʧ�� ����%.1fmin: %s: %s"
            % (elapsed / 60.0, type(e).__name__, e)
        )
        _append_run_log(
            day_s, "FAILED ����%.1fs: %s" % (elapsed, e)
        )
        try:
            try:
                import ant_server_chan as sct
            except ImportError:
                import qmt_builtin.ant_server_chan as sct
            sct.notify_alert(
                "ȫA tick�����쳣�˳�",
                "��=%s\n%s: %s" % (day_s, type(e).__name__, e),
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
    """�� day / days �ֶγ�� YYYYMMDD ���У�ȥ�ء����򣩡�"""
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
    # ���ݾɵ����ֶΣ��� days ������ day ����δ����ʱ׷�ӵ�������ͼ�ɵ��÷���֤
    if not out:
        _push(req.get("day"))
    elif req.get("day") not in (None, ""):
        # days ���ȣ������ظ��� day
        pass
    return out


def _write_manual_request_remaining(path, req, remaining):
    # type: (str, Dict[str, Any], List[str]) -> None
    """����δִ�е�����д�� manual_request.json��ԭ���滻����"""
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
    """˳������ data/after_hours_rank/manual_request.json��������·���ɴ����"""
    try:
        try:
            import ant_after_hours_rank_runner as ar
        except ImportError:
            import qmt_builtin.ant_after_hours_rank_runner as ar
        # ���ļ� mtime �����أ�ȷ���Ե� force/manual_request �°�
        import importlib

        ar = importlib.reload(ar)
        fn = getattr(ar, "process_manual_request", None)
        if callable(fn):
            fn(ContextInfo)
    except Exception as e:
        _log("���������ֶ�����ʧ��: %s" % e)


def process_manual_request(ContextInfo=None):
    # type: (Any) -> Optional[bool]
    """���� data/tick_full_sync/manual_request.json�������󷵻� None��

    ��� / ����д�룬�� QMT ���� periodic_sync ����ʵ ContextInfo ִ�С�

    ����::
        {"day":"20260730","force":false}

    ���ն��У�ÿ��ֻ�ܶ���һ�գ�ʣ��д���ļ����´� periodic ������::
        {"days":["20260728","20260729"],"force":false,"limit":0}

    ���У�09:00�C15:10��Ĭ�� defer����ɾ���У�������������/����ȡ����
    ��ѡ `"allow_intraday": true` Ӧ��ǿ�ܣ����Ƽ�����
    ��Ĭ�Ͽ� enable_xtdata_download��miniQMT�����ɹ������д��
    manual_request.done.json��δ��ɣ�time-slice����ѵ������ڶ��ס�
    ����ȯ�� tick ���� / ��ʷ�������������������ղ�����һ�����գ��� abort-hold����
    ���� tick �������ޣ�����ʱ���᳢�� after_hours_rank/manual_request.json��
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
            _log("�ֶ������ݻ���PAUSE��־")
        return False
    if _daily_sync_running():
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            req = json.load(f) or {}
    except Exception as e:
        _log("�ֶ������ȡʧ��: %s" % e)
        return False
    if not isinstance(req, dict):
        return False
    allow_intraday = bool(req.get("allow_intraday"))
    if (not allow_intraday) and _in_market_hours_protect():
        ts = time.time()
        if ts - _PROTECT_DEFER_LOG_TS >= 60.0:
            _PROTECT_DEFER_LOG_TS = ts
            _log(
                "�ֶ������ݻ������б��� "
                "�����б�����%s�������"
                % _MARKET_PROTECT_END.strftime("%H:%M")
            )
        return False
    days = _normalize_manual_days(req)
    if not days:
        _log("�ֶ�����������Ч: %s" % (req.get("days") or req.get("day")))
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

    # ��������������� / ��ʷ�������� �� һ���ƽ���
    # ���洰�ⲻ���ڴ��Զ�������manual_request ����������û���ʽ������
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
                # ���� abort-hold��������־������ hold����ˢ MANUAL_REQUEST
                ts = time.time()
                if ts - _MANUAL_HOLD_LOG_TS >= float(_MANUAL_LOG_INTERVAL_SEC):
                    _MANUAL_HOLD_LOG_TS = ts
                    _ABORT_HOLD_LOG_TS = ts
                    _log(
                        "�ֶ������ݻ�����ֹ��ȴ day=%s ԭ��=%s ʣ��=%d"
                        % (head, hr[:60], max(0, len(days) - 1))
                    )
                    _append_run_log(
                        head,
                        "MANUAL_REQUEST �ݻ� ��ֹ��ȴ reason=%s remain=%d"
                        % (hr[:60], max(0, len(days) - 1)),
                    )
                return False
        break

    if skipped_head:
        _log(
            "�ֶ��������������/���� %d ��: %s"
            % (len(skipped_head), ",".join(skipped_head[:8]))
        )
    if not days:
        try:
            os.remove(path)
        except Exception:
            pass
        _log("�ֶ���������ѿգ�����/��ɺ�")
        return True

    day_s = days[0]
    remaining = days[1:]
    # �� force ʱ�� abort-hold��Ĭ��������ȴ������ÿ������ˢ��־
    if force:
        _clear_abort_hold(day_s)

    _log(
        "�ֶ����� day=%s ʣ��=%d force=%s limit=%s xtdata_dl=%s src=%s"
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
        _log("�ֶ�����鵵ʧ��: %s" % e)
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
            # time-slice / ���� ABORT�����������ף��´� periodic ����
            _write_manual_request_remaining(path, req, [day_s] + list(remaining))
            ts = time.time()
            if ts - _MANUAL_KEEP_LOG_TS >= float(_MANUAL_LOG_INTERVAL_SEC):
                _MANUAL_KEEP_LOG_TS = ts
                _log(
                    "�ֶ������� day=%s �ڶ��ף�δ��ɣ�ʣ��=%d"
                    % (day_s, len(remaining))
                )
    except Exception as e:
        _log("�ֶ�������и���ʧ��: %s" % e)
        # �鵵ʧ��ʱ�𶪶ӣ���������ԭ�ļ�
        return False
    return ok


def tick_full_sync(ContextInfo):
    # type: (Any) -> bool
    """���ݾ� timer ��ڣ����� manual_request �����Ȱ�ָ�������ܡ�

    ���ն��У�ÿ��ֻ��������һ�գ�ʣ������ manual_request.json��
    �� periodic_sync / �´� timer ���������ⵥ����ڿ������գ���
    """
    manual = process_manual_request(ContextInfo)
    if manual is not None:
        return bool(manual)
    return run_post_daily_pipeline(ContextInfo)


def run_post_daily_pipeline(ContextInfo=None):
    # type: (Any) -> bool
    """����֮��tick ���� �� �̺����� �� ���ͬ����"""
    now = datetime.now()
    day = now.strftime("%Y%m%d")
    # �ǽ����գ���ĩ/�ڼ��գ�Ĭ����ˮ�߲��ܡ����졹����ʷ������ manual_request
    if not _today_is_tradeday(ContextInfo):
        _log("�ǽ���������Ĭ�Ϸֱ���ˮ��: %s" % day)
        _chain_sector_after_pipeline(ContextInfo)
        return False
    # ����δ����������أ����ף��������� daily բ�ŵ�ס��
    if not _daily_gate_open():
        _log("�ȴ�����ͬ����ɺ����ֱܷ�")
        return False
    ok = run_tick_full_sync(ContextInfo, force=False)
    if ok or _day_already_done(day):
        _run_after_rank_after_sync(ContextInfo, day)
    _chain_sector_after_pipeline(ContextInfo)
    return ok


def _today_is_tradeday(ContextInfo=None):
    # type: (Any) -> bool
    """�����Ƿ����գ���Ĭ���̺���ˮ�����ˣ������� ticks Ŀ¼����"""
    today = date.today()
    xtdata = _load_xtdata()
    if xtdata is not None:
        return bool(_is_tradeday(xtdata, today))
    if ContextInfo is not None:
        fn = getattr(ContextInfo, "get_trading_dates", None)
        if callable(fn):
            try:
                ds = today.strftime("%Y%m%d")
                arr = fn("SH", ds, ds) or []
                return bool(arr)
            except Exception:
                pass
    return today.weekday() < 5


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
    """���̾����󴥷��̺����ܣ������� ticks����"""
    try:
        try:
            import ant_after_hours_rank_runner as ar
        except ImportError:
            import qmt_builtin.ant_after_hours_rank_runner as ar
        if hasattr(ar, "is_rank_done") and ar.is_rank_done(day):
            return
        if getattr(ar, "_BUSY", False):
            return
        _log("�����̺����� day=%s" % day)
        ar.run_after_hours_rank(ContextInfo, day=day, force=False)
    except Exception as e:
        _log("�����̺�����ʧ��: %s" % e)


def _chain_sector_after_pipeline(ContextInfo):
    # type: (Any) -> None
    """����֮�󣺰��ɷ�ͬ������̨�����������̣���"""
    try:
        sector = _load_sector_sync_runner()
        if hasattr(sector, "run_after_hours_pipeline"):
            sector.run_after_hours_pipeline(ContextInfo)
        elif hasattr(sector, "_start_sector_sync_bg"):
            _log("�������ͬ������̨��")
            sector._start_sector_sync_bg(ContextInfo, "after_hours_pipeline", force=True)
    except Exception as e:
        _log("�������ͬ��ʧ��: %s" % e)


def maybe_catch_up_tick_full_sync(ContextInfo=None):
    # type: (Any) -> bool
    """�̺��ܣ������� daily ������ pipeline���˴����� tick բ�Ŷ��ף���"""
    global _CATCHUP_LOG_TS, _ABORT_HOLD_LOG_TS

    if _BUSY:
        return False

    now = datetime.now()
    day = now.strftime("%Y%m%d")
    if now.time() < dt_time(SYNC_HOUR, SYNC_MINUTE):
        return False
    if not _today_is_tradeday(ContextInfo):
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
                "������������ֹ��ȴ day=%s ԭ��=%s"
                % (day, (hold_reason or "")[:80])
            )
        return False

    ts = time.time()
    if ts - _CATCHUP_LOG_TS >= 120.0:
        _CATCHUP_LOG_TS = ts
        _log(
            "�貹�� day=%s�����ߺ� %02d:%02d��"
            % (day, SYNC_HOUR, SYNC_MINUTE)
        )

    return run_post_daily_pipeline(ContextInfo)


def register_tick_full_sync_timer(ContextInfo):
    # type: (Any) -> None
    """���ٵ���ע�ᶨʱ���� daily_bar_sync(15:35) ��ɺ��д�����"""
    _log(
        "��ʱ��������������ͬ�����д������汾=%s"
        % TICK_FULL_SYNC_VERSION
    )


def tick_probe(ContextInfo, day="20260730"):
    # type: (Any, str) -> Any
    """���Ժ�����̽������ download + ContextInfo tick ���壨2�C3 ֻ������Ʊ����

    ģ�ͽ���������/����һ�α��������ɣ����д data/tick_full_sync/tick_probe_*.log
    ����ӡ [tick_probe] �С�
    """
    try:
        tick_io = _import_tick_io()
    except Exception as e:
        _log("tick_probe����ʧ��: %s" % e)
        return None
    try:
        if hasattr(tick_io, "bind_download_history_data"):
            tick_io.bind_download_history_data(None)
    except Exception:
        pass
    fn = getattr(tick_io, "run_tick_api_probe", None)
    if not callable(fn):
        _log("tick_probe: ȱ��run_tick_api_probe�����ز���ant_tick_cache_io��")
        return None
    day_s = str(day or "20260730").replace("-", "").replace("/", "")[:8]
    _log("tick_probe��ʼ day=%s �汾=%s" % (day_s, TICK_FULL_SYNC_VERSION))
    return fn(ContextInfo, day=day_s)
