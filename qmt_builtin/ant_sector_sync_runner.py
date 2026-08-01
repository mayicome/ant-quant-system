#coding:gbk
"""��ʱ + ������©���ô� QMT ContextInfo ����鲢д�� data/qmt_sector_index.json��

��·����ContextInfo.get_sector_list / get_stock_list_in_sector��
������ xtdata.download_sector_data���� miniQMT/���� RPC���� QMT ��ˢ���޷�����������񡹣���
"""
import json
import os
import sys
import threading
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set, Tuple

SECTOR_SYNC_VERSION = "20260730.13"
SYNC_HOUR = 15
SYNC_MINUTE = 35
STARTUP_DELAY_SEC = 10
PROGRESS_EVERY = 50
# ѡ�ɶ˶�ȡ��������ɽ������䣻��ʽͬ�������̺���ˮ�ߴ���
CACHE_MAX_AGE_DAYS = 7
# �� QMT �� download_sector �ȼ� API����ֹ�� xtdata ��ʷ RPC
ENABLE_XTDATA_SECTOR_DOWNLOAD = False

UI_SECTOR_PREFIXES = ("SW1", "GN", "SW2", "SW3")
# ������� unicode ת�壬����Դ�ļ������𻵵��¿չ�Ʊ��
UNIVERSE_SECTOR = "\u6caa\u6df1A\u80a1"  # ����A��
UNIVERSE_SECTORS = (
    "\u6caa\u6df1A\u80a1",  # ����A��
    "\u4e0a\u8bc1A\u80a1",  # ��֤A��
    "\u6df1\u8bc1A\u80a1",  # ��֤A��
)

EXCLUDE_SUBSTR = (
    "��Ȩ",
    "����",
    "ָ��",
    "ETF",
    "ծȯ",
    "����",
    "��Ȩ",
    "תծ",
    "�ڻ�",
    "�ƴ���CDR",
    "������Լ",
)

MARKET_SECTOR_EXACT = frozenset(
    {
        "����A��",
        "����B��",
        "��֤A��",
        "��֤B��",
        "��֤A��",
        "��֤B��",
        "��ҵ��",
        "�ƴ���",
        "������",
        "�н���",
        "������",
        "֣����",
        "��Դ����",
        "���������ָ��",
        "�����������Ʊ",
    }
)

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

_SYNC_RUNNING = False
# ��������ʧ��/�ѳ��Թ�����ֹ catch-up ��������ˢ��
_ATTEMPTED_DAY = ""
_SOFT_FAIL_LOGGED = False
_API_FAIL_LOGGED = set()  # type: Set[str]


def _paths() -> Tuple[str, str, str]:
    base = PROJECT_ROOT.rstrip("\\/")
    index_path = os.path.join(base, "data", "qmt_sector_index.json")
    manifest_path = os.path.join(base, "data", "qmt_sector_manifest.json")
    return base, index_path, manifest_path


def _load_xtdata():
    if not ENABLE_XTDATA_SECTOR_DOWNLOAD:
        return None
    try:
        import xtquant.xtdata as xtdata

        try:
            xtdata.enable_hello = False
        except Exception:
            pass
        return xtdata
    except Exception:
        return None


def _api_owners(ContextInfo=None):
    """���� ContextInfo����ѡ xtdata��Ĭ�Ϲرգ���"""
    owners = []
    if ContextInfo is not None:
        owners.append(("ctx", ContextInfo))
    xt = _load_xtdata()
    if xt is not None:
        owners.append(("xt", xt))
    return owners


def _call_first(owners, method: str, *args, log_fail: bool = True):
    """�� owners ���ε��� method������ (label, result)��ȫʧ���� (None, None)��"""
    global _API_FAIL_LOGGED
    last_err = None
    for label, owner in owners:
        fn = getattr(owner, method, None)
        if not callable(fn):
            continue
        try:
            return label, fn(*args)
        except Exception as e:
            last_err = e
            continue
    if log_fail and last_err is not None and method not in _API_FAIL_LOGGED:
        _API_FAIL_LOGGED.add(method)
        print("[���ͬ��] %s failed: %s" % (method, last_err))
    return None, None


def _code_to_6(full: Any) -> str:
    s = str(full or "").strip().upper()
    if "." in s:
        s = s.split(".")[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def _is_ui_sector(name: str) -> bool:
    if not name or name in MARKET_SECTOR_EXACT:
        return False
    if any(x in name for x in EXCLUDE_SUBSTR):
        return False
    return any(name.startswith(p) for p in UI_SECTOR_PREFIXES)


def _read_json(path: str) -> Optional[dict]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _cache_fresh(index_path: str) -> bool:
    payload = _read_json(index_path)
    if not payload:
        return False
    built_at = str(payload.get("built_at") or "").strip()
    if not built_at:
        return False
    try:
        age = (date.today() - date.fromisoformat(built_at)).days
    except Exception:
        return False
    return 0 <= age <= CACHE_MAX_AGE_DAYS


def _cache_age_days(index_path: str) -> Optional[int]:
    payload = _read_json(index_path)
    if not payload:
        return None
    built_at = str(payload.get("built_at") or "").strip()
    if not built_at:
        return None
    try:
        return (date.today() - date.fromisoformat(built_at)).days
    except Exception:
        return None


def _is_trading_day_today() -> bool:
    try:
        root = PROJECT_ROOT.rstrip("\\/")
        if root not in sys.path:
            sys.path.insert(0, root)
        from utils.trading_day import is_tradeday

        return bool(is_tradeday())
    except Exception:
        return datetime.now().weekday() < 5


def _start_sector_sync_bg(ContextInfo, source: str, *, force: bool = False) -> None:
    def _worker():
        try:
            run_sector_sync(ContextInfo, source=source, force=force)
        except Exception as e:
            print("[���ͬ��] bg error (%s): %s" % (source, e))

    th = threading.Thread(
        target=_worker, daemon=True, name="sector_sync_%s" % source
    )
    th.start()


def _list_ui_sectors(ContextInfo=None) -> Tuple[List[str], str]:
    owners = _api_owners(ContextInfo)
    label, raw = _call_first(owners, "get_sector_list")
    if raw is None:
        raw = []
    if not isinstance(raw, (list, tuple)):
        raw = list(raw) if raw else []
    sectors = sorted({s for s in raw if _is_ui_sector(str(s))})
    gn = sum(1 for s in sectors if s.startswith("GN"))
    src = label or "none"
    print(
        "[���ͬ��] ui sectors=%d sw1=%d gn=%d source=%s"
        % (
            len(sectors),
            sum(1 for s in sectors if s.startswith("SW1")),
            gn,
            src,
        )
    )
    if gn == 0 and sectors:
        print("[���ͬ��] WARN: no GN concept sectors in local/ContextInfo data")
    return sectors, src


def _universe_from_file() -> Set[str]:
    base = PROJECT_ROOT.rstrip("\\/")
    path = os.path.join(base, "data", "a_share_universe.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f) or {}
        raw = list(payload.get("codes") or [])
        codes = {_code_to_6(c) for c in raw if _code_to_6(c)}
        if codes:
            print("[���ͬ��] universe fallback file=%d" % len(codes))
        return codes
    except Exception as e:
        print("[���ͬ��] universe file fail: %s" % e)
        return set()


def _universe_codes(ContextInfo=None) -> Tuple[Set[str], str]:
    owners = _api_owners(ContextInfo)
    for label, owner in owners:
        fn = getattr(owner, "get_stock_list_in_sector", None)
        if not callable(fn):
            continue
        raw = []
        try:
            for sec in UNIVERSE_SECTORS:
                try:
                    raw.extend(fn(sec) or [])
                except Exception:
                    continue
        except Exception:
            continue
        codes = {_code_to_6(c) for c in raw if _code_to_6(c)}
        if codes:
            print(
                "[���ͬ��] universe %s=%d source=%s"
                % (UNIVERSE_SECTOR, len(codes), label)
            )
            return codes, label
    codes = _universe_from_file()
    return codes, "file" if codes else "none"


def _members_of(ContextInfo, sector: str, universe: Set[str]) -> Set[str]:
    owners = _api_owners(ContextInfo)
    _, raw = _call_first(
        owners, "get_stock_list_in_sector", sector, log_fail=False
    )
    if raw is None:
        raw = []
    codes = {_code_to_6(c) for c in raw if _code_to_6(c)}
    if universe:
        codes &= universe
    return codes


def _save_index(index_path: str, ui_sectors: List[str], code_sectors: Dict[str, List[str]]) -> None:
    os.makedirs(os.path.dirname(index_path), exist_ok=True)
    payload = {
        "built_at": date.today().isoformat(),
        "sector_count": len(ui_sectors),
        "ui_sectors": ui_sectors,
        "code_sectors": code_sectors,
        "source": "ant_sector_sync_runner",
        "runner_version": SECTOR_SYNC_VERSION,
    }
    tmp = index_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, index_path)


def _save_manifest(manifest_path: str, body: Dict[str, Any]) -> None:
    try:
        save_json_atomic(manifest_path, body)
    except Exception as e:
        print("[���ͬ��] manifest save failed: %s" % e)


def is_sync_running() -> bool:
    return bool(_SYNC_RUNNING)


def _mark_attempted() -> None:
    global _ATTEMPTED_DAY
    _ATTEMPTED_DAY = date.today().isoformat()


def _already_attempted_today() -> bool:
    return _ATTEMPTED_DAY == date.today().isoformat()


def run_sector_sync(ContextInfo=None, source: str = "manual", force: bool = False) -> bool:
    global _SYNC_RUNNING, _SOFT_FAIL_LOGGED
    if _SYNC_RUNNING:
        print("[���ͬ��] skip: already running")
        return False

    _, index_path, manifest_path = _paths()
    if not force and _cache_fresh(index_path):
        age = _cache_age_days(index_path)
        print(
            "[���ͬ��] skip: cache fresh (age=%sd, max=%sd)"
            % (age if age is not None else "?", CACHE_MAX_AGE_DAYS)
        )
        return True

    # �����ѳ��Թ���ʧ�ܣ��������ԣ����� catch-up ˢ��
    if force and _already_attempted_today():
        manifest = _read_json(manifest_path) or {}
        st = str(manifest.get("status") or "")
        if st in ("failed", "ok_cached"):
            if not _SOFT_FAIL_LOGGED:
                print(
                    "[���ͬ��] skip: already attempted today status=%s version=%s"
                    % (st, SECTOR_SYNC_VERSION)
                )
                _SOFT_FAIL_LOGGED = True
            return st == "ok_cached" or _cache_fresh(index_path)

    _SYNC_RUNNING = True
    _mark_attempted()
    started = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    print(
        "[���ͬ��] begin source=%s version=%s primary=ContextInfo "
        "xtdata_dl=%s"
        % (
            source,
            SECTOR_SYNC_VERSION,
            "on" if ENABLE_XTDATA_SECTOR_DOWNLOAD else "off",
        )
    )
    ok = False
    status = "failed"
    ui_sectors: List[str] = []
    code_sectors: Dict[str, List[str]] = {}
    universe: Set[str] = set()
    sector_src = "none"
    universe_src = "none"
    downloaded = False  # �� QMT �� download����Ϊ False

    try:
        ui_sectors, sector_src = _list_ui_sectors(ContextInfo)
        if not ui_sectors:
            # ContextInfo �� get_sector_list ʱ���ñ���������İ����ˢ�³ɷ�
            cached = _read_json(index_path) or {}
            cached_secs = [
                str(s)
                for s in (cached.get("ui_sectors") or [])
                if _is_ui_sector(str(s))
            ]
            if cached_secs:
                ui_sectors = sorted(set(cached_secs))
                sector_src = "cache_names"
                print(
                    "[���ͬ��] no live get_sector_list; "
                    "refresh members from cached names n=%d"
                    % len(ui_sectors)
                )
            else:
                age = _cache_age_days(index_path)
                if age is not None and 0 <= age <= CACHE_MAX_AGE_DAYS:
                    status = "ok_cached"
                    ok = True
                    print(
                        "[���ͬ��] soft-ok: no live sectors; keep cache "
                        "age=%sd (no xtdata download)"
                        % age
                    )
                    return True
                raise RuntimeError(
                    "no ui sectors from ContextInfo (xtdata download disabled)"
                )

        universe, universe_src = _universe_codes(ContextInfo)
        if not universe:
            print("[���ͬ��] WARN: empty universe; index members unfiltered")

        code_to_set: Dict[str, Set[str]] = {}
        total = len(ui_sectors)
        member_fail = 0
        for i, sector in enumerate(ui_sectors, 1):
            try:
                for code in _members_of(ContextInfo, sector, universe):
                    code_to_set.setdefault(code, set()).add(sector)
            except Exception:
                member_fail += 1
            if i % PROGRESS_EVERY == 0 or i == total:
                print("[���ͬ��] index progress %d/%d" % (i, total))
            time.sleep(0.01)

        if not code_to_set:
            age = _cache_age_days(index_path)
            if age is not None and 0 <= age <= CACHE_MAX_AGE_DAYS:
                status = "ok_cached"
                ok = True
                print(
                    "[���ͬ��] soft-ok: empty members; keep cache age=%sd"
                    % age
                )
                return True
            raise RuntimeError("no sector members resolved")

        code_sectors = {c: sorted(ss) for c, ss in code_to_set.items()}
        _save_index(index_path, ui_sectors, code_sectors)
        ok = True
        status = "ok"
        print(
            "[���ͬ��] done sectors=%d stocks=%d universe=%d "
            "sector_src=%s universe_src=%s downloaded=%s member_fail=%d"
            % (
                len(ui_sectors),
                len(code_sectors),
                len(universe),
                sector_src,
                universe_src,
                downloaded,
                member_fail,
            )
        )
        return True
    except Exception as e:
        status = "failed"
        if not _SOFT_FAIL_LOGGED:
            print("[���ͬ��] ERROR: %s" % e)
            _SOFT_FAIL_LOGGED = True
        else:
            print("[���ͬ��] ERROR (suppressed detail): %s" % type(e).__name__)
        return False
    finally:
        finished = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        _save_manifest(
            manifest_path,
            {
                "version": 1,
                "built_at": date.today().isoformat(),
                "sector_count": len(ui_sectors),
                "gn_count": sum(1 for s in ui_sectors if s.startswith("GN")),
                "stock_count": len(code_sectors),
                "universe_count": len(universe),
                "downloaded": downloaded,
                "status": status if ok else "failed",
                "started_at": started,
                "finished_at": finished,
                "runner_version": SECTOR_SYNC_VERSION,
                "trigger": source,
                "index_path": index_path,
                "sector_source": sector_src,
                "universe_source": universe_src,
                "xtdata_download": "on" if ENABLE_XTDATA_SECTOR_DOWNLOAD else "off",
                "primary_path": "ContextInfo.get_sector_list",
            },
        )
        _SYNC_RUNNING = False


def startup_sector_sync(ContextInfo):
    """������ͬ����飨ȫ��ɨ�����أ������̺���ˮ�߸��¡�"""
    _, index_path, manifest_path = _paths()
    age = _cache_age_days(index_path)
    if age is None:
        manifest = _read_json(manifest_path)
        if manifest and str(manifest.get("status") or "") in ("ok", "ok_cached"):
            try:
                built = str(manifest.get("built_at") or "")[:10]
                if built:
                    age = (date.today() - date.fromisoformat(built)).days
            except Exception:
                pass
    age_s = "missing" if age is None else ("%dd" % age)
    print(
        "[���ͬ��] startup disabled; cache_age=%s; "
        "chained after daily->tick->after_rank version=%s"
        % (age_s, SECTOR_SYNC_VERSION)
    )
    return True


def is_synced_today() -> bool:
    """�����Ƿ��ѳɹ�д����������������ɹ��������棩��"""
    _, index_path, manifest_path = _paths()
    age = _cache_age_days(index_path)
    if age == 0:
        return True
    manifest = _read_json(manifest_path)
    if not manifest:
        return False
    st = str(manifest.get("status") or "")
    built = str(manifest.get("built_at") or "")[:10]
    if built != date.today().isoformat():
        return False
    # ok�������ؽ���ok_cached�������ѳ��Բ��������û��棻failed ����
    if st == "ok":
        return True
    if st == "ok_cached" and _cache_fresh(index_path):
        return True
    # �����ѳ��Թ�����ʧ�ܣ�Ҳ��Ϊ��������ˮ�ߴ˲������������� catch-up ��ѭ��
    if _already_attempted_today() and st == "failed":
        return True
    return False


def run_after_hours_pipeline(ContextInfo=None) -> bool:
    """�̺���ˮ��ĩβ������δͬ�����̨ȫ�����°�顣"""
    if is_sync_running():
        print("[���ͬ��] pipeline skip: already running")
        return False
    if is_synced_today():
        print("[���ͬ��] pipeline skip: already synced today")
        return True
    if _already_attempted_today():
        print("[���ͬ��] pipeline skip: already attempted today")
        return False
    print("[���ͬ��] pipeline: start background after after_rank")
    _start_sector_sync_bg(ContextInfo, "after_hours_pipeline", force=True)
    return True


def sector_data_sync(ContextInfo):
    """���ݾ� timer ��ڣ��������̺���ˮ�ߴ�����"""
    return run_after_hours_pipeline(ContextInfo)


def register_startup_sector_timer(ContextInfo) -> None:
    """����ע��������ʱ��������ÿ�ο� QMT ɨ��ǧ���顣"""
    startup_sector_sync(ContextInfo)


def register_sector_sync_timer(ContextInfo) -> None:
    """���ٵ���ע�� 00:00 ��ʱ�����̺����ߡ�tick��������ɺ��д�����"""
    print(
        "[���ͬ��] timer skipped (chained after after_rank) version=%s"
        % SECTOR_SYNC_VERSION
    )
