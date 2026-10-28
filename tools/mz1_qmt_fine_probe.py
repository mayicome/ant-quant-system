#coding:utf-8
"""MZ1 + QMT-daily hot-reload probe (watch userdata current.py files).

Primary jobs (ONE probe instance -- do not run two):
  1) userdata/mz1_qmt_fine_watch/current.py  -> MZ1 fine backtest (ANT_MZ1_BT|)
  2) userdata/qmt_daily_watch/current.py     -> full-A daily bars (ANT_DAILY|)

Pattern: importlib + mtime, poll in handlebar + background thread (after-hours).
Heavy run_backtest runs on a dedicated worker so the poll loop never blocks
(heartbeat stays alive even while download_history_data runs).
Deploy: overwrite QMT python/probe.py then restart model-trading strategy.

Tick-depth one-shot probe is NOT run here.
"""
from __future__ import print_function

import importlib.util
import os
import sys
import time
import traceback
import threading

PROBE_VERSION = "mz1_daily_watch_1.6"

# Fixed watch paths under Guojin QMT userdata (project dir is Forbidden FileIO).
WATCH_SPECS = [
    {
        "name": "mz1",
        "watch_dir": r"D:\国金证券QMT交易端2\userdata\mz1_qmt_fine_watch",
        "mod_prefix": "mz1_watch_",
        "log_tag": "mz1-probe",
        "error_prefix": "ANT_MZ1_BT|ERROR|",
        "clear_prefixes": ("mz1_watch_strategy", "mz1_watch_"),
    },
    {
        "name": "daily",
        "watch_dir": r"D:\国金证券QMT交易端2\userdata\qmt_daily_watch",
        "mod_prefix": "qmt_daily_watch_",
        "log_tag": "daily-probe",
        "error_prefix": "ANT_DAILY|ERROR|",
        "clear_prefixes": ("qmt_daily_watch_",),
        "run_on_init": True,  # after-hours bg threads freeze; init main-thread run
    },
]


POLL_INTERVAL_SEC = 1.0
STABLE_NEED = 2

_last_check_ts = 0.0
_bg_stop = False
_bg_thread = None
_ctx = None
_run_lock = threading.Lock()

# Job worker: keeps _bg_poll_loop / handlebar free while heavy jobs run.
_worker_stop = False
_worker_thread = None
_job_cv = threading.Condition()
_job_queue = []  # list of (ContextInfo, spec, mtime)

# per-watch state
_states = {}


def _state(name):
    st = _states.get(name)
    if st is None:
        st = {
            "last_mtime": 0,
            "stable_mtime": 0,
            "stable_hits": 0,
            "loaded_mtime": 0,
            "pending_mtime": 0,
            "mod": None,
        }
        _states[name] = st
    return st


def _plog(tag, msg):
    line = "[%s] %s" % (tag, msg)
    try:
        print(line, flush=True)
    except TypeError:
        try:
            print(line)
        except Exception:
            pass
    except Exception:
        try:
            print(line)
        except Exception:
            pass


def _emit_error(spec, msg):
    try:
        print("%s%s" % (spec["error_prefix"], msg))
    except Exception:
        pass
    _plog(spec["log_tag"], "error %s" % msg)


def _ensure_watch_dir(spec):
    d = spec["watch_dir"]
    try:
        if not os.path.isdir(d):
            os.makedirs(d)
            _plog(spec["log_tag"], "created watch dir %s" % d)
        return True
    except Exception as e:
        _plog(spec["log_tag"], "watch dir not writable: %s (%s)" % (d, e))
        return False


def _watch_file(spec):
    return os.path.join(spec["watch_dir"], "current.py")


def _file_mtime_size(path):
    try:
        st = os.stat(path)
        return int(st.st_mtime), int(st.st_size)
    except OSError:
        return 0, 0


def _load_watch_module(spec, path, mtime):
    st = _state(spec["name"])
    for key in list(sys.modules.keys()):
        for pref in spec.get("clear_prefixes") or ():
            if key == pref.rstrip("_") or str(key).startswith(pref):
                sys.modules.pop(key, None)
                break
    mod_name = "%s%d" % (spec["mod_prefix"], int(mtime))
    spec_obj = importlib.util.spec_from_file_location(mod_name, path)
    if spec_obj is None or spec_obj.loader is None:
        raise RuntimeError("spec_from_file_location failed: %s" % path)
    mod = importlib.util.module_from_spec(spec_obj)
    sys.modules[mod_name] = mod
    spec_obj.loader.exec_module(mod)
    mod._WATCH_MTIME = int(mtime)
    st["mod"] = mod
    return mod


def _run_loaded(ContextInfo, mod, mtime, log_tag):
    fn = getattr(mod, "run_backtest", None)
    if callable(fn):
        _plog(log_tag, "calling run_backtest mtime=%d" % int(mtime))
        return fn(ContextInfo)
    fn = getattr(mod, "init", None)
    if callable(fn):
        _plog(log_tag, "calling init mtime=%d" % int(mtime))
        return fn(ContextInfo)
    raise RuntimeError("watch module has neither run_backtest nor init")


def _try_reload_and_run(ContextInfo, spec, mtime):
    st = _state(spec["name"])
    # Serialize all watches: only one job at a time (QMT freezes if dual heavy).
    if not _run_lock.acquire(False):
        st["pending_mtime"] = int(mtime)
        _plog(spec["log_tag"], "lock busy; queued pending mtime=%d" % int(mtime))
        return
    try:
        if int(mtime) == int(st["loaded_mtime"]):
            return
        path = _watch_file(spec)
        if not os.path.isfile(path):
            _plog(spec["log_tag"], "no watch file yet: %s" % path)
            return
        try:
            mod = _load_watch_module(spec, path, mtime)
            _run_loaded(ContextInfo, mod, mtime, spec["log_tag"])
            st["loaded_mtime"] = int(mtime)
            _plog(spec["log_tag"], "reloaded mtime=%d ran" % int(mtime))
        except Exception:
            tb = traceback.format_exc()
            _emit_error(spec, tb[-1500:].replace("\n", " | "))
            _plog(spec["log_tag"], "traceback:\n%s" % tb[-2000:])
    finally:
        try:
            _run_lock.release()
        except Exception:
            pass
    # Drain pending for THIS watch, then other watches with pending.
    if st["pending_mtime"] and int(st["pending_mtime"]) != int(st["loaded_mtime"]):
        nxt = int(st["pending_mtime"])
        st["pending_mtime"] = 0
        if nxt != int(st["loaded_mtime"]):
            _plog(spec["log_tag"], "draining pending mtime=%d" % nxt)
            _try_reload_and_run(ContextInfo, spec, nxt)
    for other in WATCH_SPECS:
        if other["name"] == spec["name"]:
            continue
        ost = _state(other["name"])
        pm = int(ost.get("pending_mtime") or 0)
        if pm and pm != int(ost.get("loaded_mtime") or 0):
            ost["pending_mtime"] = 0
            _plog(other["log_tag"], "draining cross pending mtime=%d" % pm)
            _try_reload_and_run(ContextInfo, other, pm)


def _schedule_run(ContextInfo, spec, mtime):
    """Enqueue heavy job onto worker thread (poll loop must not block)."""
    item = (ContextInfo, spec, int(mtime))
    with _job_cv:
        # Drop older jobs for the same watch name; keep newest mtime.
        name = spec["name"]
        kept = []
        for ci, sp, mt in _job_queue:
            if sp["name"] != name:
                kept.append((ci, sp, mt))
        kept.append(item)
        _job_queue[:] = kept
        _job_cv.notify()
    _plog(spec["log_tag"], "scheduled job mtime=%d queue=%d" % (int(mtime), len(_job_queue)))


def _worker_loop():
    global _worker_stop
    _plog("probe", "job worker enter")
    while not _worker_stop:
        item = None
        with _job_cv:
            while not _job_queue and not _worker_stop:
                try:
                    _job_cv.wait(timeout=1.0)
                except Exception:
                    break
            if _worker_stop:
                break
            if _job_queue:
                item = _job_queue.pop(0)
        if item is None:
            continue
        ContextInfo, spec, mtime = item
        try:
            _try_reload_and_run(ContextInfo, spec, mtime)
        except Exception:
            try:
                _plog(spec["log_tag"], "worker error: %s" % traceback.format_exc()[-400:])
            except Exception:
                pass
    try:
        _plog("probe", "job worker exit")
    except Exception:
        pass


def _poll_one(ContextInfo, spec):
    st = _state(spec["name"])
    path = _watch_file(spec)
    mtime, size = _file_mtime_size(path)
    if mtime <= 0:
        return
    if mtime == st["loaded_mtime"]:
        st["stable_mtime"] = mtime
        st["stable_hits"] = 0
        return
    if mtime == st["stable_mtime"] and size > 0:
        st["stable_hits"] += 1
    else:
        st["stable_mtime"] = mtime
        st["stable_hits"] = 1
        # Log detection immediately (even before stable / run).
        _plog(
            spec["log_tag"],
            "mtime bump seen mtime=%d size=%d loaded=%d"
            % (int(mtime), int(size), int(st["loaded_mtime"]))
        )
    st["last_mtime"] = mtime
    if st["stable_hits"] < int(STABLE_NEED):
        return
    if _run_lock.locked():
        st["pending_mtime"] = mtime
        return
    _schedule_run(ContextInfo, spec, mtime)


def _poll(ContextInfo):
    global _last_check_ts
    now = time.time()
    if (now - _last_check_ts) < float(POLL_INTERVAL_SEC):
        return
    _last_check_ts = now
    for spec in WATCH_SPECS:
        try:
            _poll_one(ContextInfo, spec)
        except Exception:
            try:
                _plog(spec["log_tag"], "poll error: %s" % traceback.format_exc()[-400:])
            except Exception:
                pass


def _emit_alive():
    try:
        parts = []
        for spec in WATCH_SPECS:
            st = _state(spec["name"])
            mtime, size = _file_mtime_size(_watch_file(spec))
            parts.append(
                "%s mtime=%d size=%d loaded=%d"
                % (spec["name"], int(mtime), int(size), int(st["loaded_mtime"]))
            )
        busy = "busy" if _run_lock.locked() else "idle"
        qlen = 0
        try:
            qlen = len(_job_queue)
        except Exception:
            pass
        _plog("probe", "alive watching %s | lock=%s queue=%d" % (" | ".join(parts), busy, qlen))
    except Exception:
        try:
            _plog("probe", "alive")
        except Exception:
            pass


def _bg_poll_loop():
    global _bg_stop, _ctx
    alive_every = 30.0
    last_alive = 0.0
    _plog("probe", "bg poll loop enter")
    while not _bg_stop:
        try:
            time.sleep(float(POLL_INTERVAL_SEC))
        except Exception:
            break
        if _bg_stop:
            break
        # Heartbeat MUST NOT depend on poll / job finishing.
        now = time.time()
        if (now - last_alive) >= alive_every:
            last_alive = now
            _emit_alive()
        ctx = _ctx
        if ctx is None:
            continue
        try:
            # _poll only schedules; heavy work is on worker thread.
            _poll(ctx)
        except Exception:
            try:
                _plog("probe", "bg poll error: %s" % traceback.format_exc()[-400:])
            except Exception:
                pass
    try:
        _plog("probe", "bg poll loop exit")
    except Exception:
        pass


def _start_threads():
    global _bg_thread, _worker_thread, _bg_stop, _worker_stop
    _worker_stop = False
    try:
        if _worker_thread is not None and getattr(_worker_thread, "is_alive", lambda: False)():
            _plog("probe", "job worker already alive")
        else:
            _worker_thread = threading.Thread(
                target=_worker_loop, name="probe-job", args=()
            )
            try:
                _worker_thread.daemon = True
            except Exception:
                pass
            _worker_thread.start()
            _plog("probe", "job worker started")
    except Exception as e:
        _plog("probe", "job worker failed: %s" % e)

    _bg_stop = False
    try:
        if _bg_thread is not None and getattr(_bg_thread, "is_alive", lambda: False)():
            _plog("probe", "bg poll thread already alive")
        else:
            _bg_thread = threading.Thread(
                target=_bg_poll_loop, name="probe-bg", args=()
            )
            try:
                _bg_thread.daemon = True
            except Exception:
                pass
            _bg_thread.start()
            _plog("probe", "bg poll thread started (after-hours safe)")
    except Exception as e:
        _plog("probe", "bg poll thread failed: %s" % e)


def _seed_existing():
    """Mark current.py files as already-loaded BEFORE starting bg threads.

    Specs with run_on_init=True keep loaded_mtime=0 so init can run them on the
    main thread (QMT after-hours often freezes bg/worker threads after init).
    """
    for spec in WATCH_SPECS:
        st = _state(spec["name"])
        mtime, size = _file_mtime_size(_watch_file(spec))
        if mtime > 0 and size > 0:
            st["last_mtime"] = mtime
            st["stable_mtime"] = mtime
            st["stable_hits"] = 0
            st["pending_mtime"] = 0
            if spec.get("run_on_init"):
                st["loaded_mtime"] = 0
                _plog(
                    spec["log_tag"],
                    "existing current.py queued for init-run mtime=%d size=%d"
                    % (mtime, size),
                )
            else:
                st["loaded_mtime"] = mtime
                _plog(
                    spec["log_tag"],
                    "existing current.py ignored until next change mtime=%d size=%d"
                    % (mtime, size),
                )
        else:
            _plog(spec["log_tag"], "waiting for current.py")


def _run_on_init_jobs(ContextInfo):
    """Synchronously run watches marked run_on_init on the QMT main thread."""
    for spec in WATCH_SPECS:
        if not spec.get("run_on_init"):
            continue
        path = _watch_file(spec)
        mtime, size = _file_mtime_size(path)
        if mtime <= 0 or size <= 0:
            _plog(spec["log_tag"], "init-run skip: no current.py")
            continue
        st = _state(spec["name"])
        if int(mtime) == int(st.get("loaded_mtime") or 0):
            continue
        _plog(
            spec["log_tag"],
            "init-run on main thread mtime=%d size=%d" % (int(mtime), int(size)),
        )
        try:
            _try_reload_and_run(ContextInfo, spec, mtime)
        except Exception:
            try:
                _plog(
                    spec["log_tag"],
                    "init-run error: %s" % traceback.format_exc()[-500:],
                )
            except Exception:
                pass


def init(ContextInfo):
    global _last_check_ts, _ctx, _bg_stop, _worker_stop
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    _last_check_ts = 0.0
    _states.clear()
    with _job_cv:
        _job_queue[:] = []
    _plog("probe", "version=%s dual-watch" % PROBE_VERSION)
    for spec in WATCH_SPECS:
        _ensure_watch_dir(spec)
        _plog(spec["log_tag"], "watching %s" % _watch_file(spec))

    try:
        if hasattr(ContextInfo, "set_universe"):
            ContextInfo.set_universe(["000001.SZ"])
    except Exception:
        pass

    _ctx = ContextInfo

    # CRITICAL: seed BEFORE starting bg/worker threads to avoid race where
    # poll runs with loaded_mtime=0 and starts a blocking job during init.
    _seed_existing()
    _start_threads()
    # After-hours: bg/worker threads often freeze after init (no further alive).
    # Daily watch runs here on the main thread so CLI+restart-probe still works.
    _run_on_init_jobs(ContextInfo)


def handlebar(ContextInfo):
    try:
        _poll(ContextInfo)
    except KeyboardInterrupt:
        return
    except Exception:
        try:
            _plog("probe", "handlebar poll error: %s" % traceback.format_exc()[-500:])
        except Exception:
            pass
