#coding:utf-8
"""MZ1 + QMT-daily hot-reload probe (watch userdata current.py files).

Primary jobs (ONE probe instance -- do not run two):
  1) userdata/mz1_qmt_fine_watch/current.py  -> MZ1 fine backtest (ANT_MZ1_BT|)
  2) userdata/qmt_daily_watch/current.py     -> full-A daily bars (ANT_DAILY|)

Pattern: importlib + mtime. Heartbeat stays on a background thread.
ContextInfo.download_history_data / get_market_data_ex only return on the
model-trading main thread. init only queues. A ContextInfo.run_time timer
(same API as 蚂蚁量化规则, "1nSecond") drives one short slice of
iter_backtest() per callback, then returns. SLICE_GAP_SEC keeps the thread
free for 蚂蚁量化规则. The strategy period stays 日线; handlebar is only an
extra nudge. The background thread must not call ContextInfo.

Deploy: overwrite QMT python/探针.py then restart the 「探针」 model strategy.
A running process keeps the old module until that restart.

Tick-depth one-shot probe is NOT run here.
"""
from __future__ import print_function

import importlib.util
import os
import sys
import time
import traceback
import threading

PROBE_VERSION = "mz1_daily_watch_1.9"

# One timer callback may use the main thread for this long, then must return.
SLICE_BUDGET_SEC = 0.30
# After a slice, ignore further callbacks until this gap elapses.
SLICE_GAP_SEC = 0.50
# Proven on this install: 蚂蚁量化规则 calls run_time(..., "1nSecond", ...)
# and the callback keeps firing while the model period stays 日线 (86400).
SLICE_TIMER_NAME = "probe_slice_timer"
SLICE_TIMER_PERIOD = "1nSecond"
SLICE_TIMER_START = "2024-01-01 09:00:00"

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
    },
]


POLL_INTERVAL_SEC = 1.0
STABLE_NEED = 2

_last_check_ts = 0.0
_bg_stop = False
_bg_thread = None
_ctx = None
_run_lock = threading.Lock()
_active = None  # {"spec", "mtime", "gen", "steps"}
_next_slice_ts = 0.0
_strategy_ident = None
_slice_busy = False
_offthread_logged = False

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


def _open_iter(ContextInfo, spec, mtime):
    """Load current.py and return its iter_backtest generator. Main thread only."""
    path = _watch_file(spec)
    mod = _load_watch_module(spec, path, mtime)
    fn = getattr(mod, "iter_backtest", None)
    if not callable(fn):
        raise RuntimeError("current.py has no iter_backtest(); refusing monolithic run_backtest")
    gen = fn(ContextInfo)
    if gen is None or not hasattr(gen, "__next__"):
        raise RuntimeError("iter_backtest() did not return a generator")
    return gen


def _close_active(mark_loaded):
    global _active
    job = _active
    _active = None
    if not job:
        return
    spec = job["spec"]
    st = _state(spec["name"])
    if mark_loaded:
        st["loaded_mtime"] = int(job["mtime"])
        _plog(spec["log_tag"], "reloaded mtime=%d ran steps=%d" % (int(job["mtime"]), int(job["steps"])))
    else:
        st["loaded_mtime"] = int(job["mtime"])
        _plog(spec["log_tag"], "stopped mtime=%d steps=%d" % (int(job["mtime"]), int(job["steps"])))


def _pick_pending_spec():
    for spec in WATCH_SPECS:
        st = _state(spec["name"])
        mtime, size = _file_mtime_size(_watch_file(spec))
        if mtime <= 0 or size <= 0:
            continue
        if int(mtime) == int(st.get("loaded_mtime") or 0):
            continue
        if int(st.get("stable_hits") or 0) < int(STABLE_NEED):
            continue
        return spec, int(mtime)
    return None, 0


def _drive_slice(ContextInfo):
    """Advance the active generator for at most SLICE_BUDGET_SEC, then return."""
    global _active, _next_slice_ts
    now = time.time()
    if now < float(_next_slice_ts or 0):
        return
    if _active is None:
        spec, mtime = _pick_pending_spec()
        if spec is None:
            return
        try:
            gen = _open_iter(ContextInfo, spec, mtime)
        except Exception:
            tb = traceback.format_exc()
            _emit_error(spec, tb[-1500:].replace("\n", " | "))
            _state(spec["name"])["loaded_mtime"] = int(mtime)
            _next_slice_ts = time.time() + float(SLICE_GAP_SEC)
            return
        _active = {"spec": spec, "mtime": int(mtime), "gen": gen, "steps": 0}
        _plog(spec["log_tag"], "slice-run start mtime=%d budget_ms=%d gap_ms=%d" % (
            int(mtime), int(SLICE_BUDGET_SEC * 1000), int(SLICE_GAP_SEC * 1000),
        ))
    job = _active
    gen = job["gen"]
    spec = job["spec"]
    t0 = time.time()
    steps = 0
    finished = False
    failed = False
    try:
        while (time.time() - t0) < float(SLICE_BUDGET_SEC):
            try:
                next(gen)
            except StopIteration:
                finished = True
                break
            steps += 1
    except Exception:
        failed = True
        tb = traceback.format_exc()
        _emit_error(spec, tb[-1500:].replace("\n", " | "))
        _plog(spec["log_tag"], "slice error:\n%s" % tb[-2000:])
    elapsed_ms = int((time.time() - t0) * 1000)
    job["steps"] = int(job["steps"]) + steps
    if finished or failed:
        _close_active(True)
    else:
        _plog(
            "probe",
            "yielded %s slice_ms=%d steps=%d gap_ms=%d total_steps=%d"
            % (spec["name"], elapsed_ms, steps, int(SLICE_GAP_SEC * 1000), int(job["steps"])),
        )
    _next_slice_ts = time.time() + float(SLICE_GAP_SEC)


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
        _plog(
            spec["log_tag"],
            "mtime bump seen mtime=%d size=%d loaded=%d"
            % (int(mtime), int(size), int(st["loaded_mtime"]))
        )
    st["last_mtime"] = mtime
    if st["stable_hits"] < int(STABLE_NEED):
        return
    st["pending_mtime"] = mtime


def _poll(ContextInfo):
    """Main thread only. Do not call this from the heartbeat thread."""
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
        busy = "busy" if _active is not None else "idle"
        _plog(
            "probe",
            "alive watching %s | lock=%s queue=0 timer-sliced"
            % (" | ".join(parts), busy),
        )
    except Exception:
        try:
            _plog("probe", "alive")
        except Exception:
            pass


def _bg_poll_loop():
    """Heartbeat only. Never calls ContextInfo or _poll."""
    global _bg_stop
    alive_every = 30.0
    last_alive = 0.0
    _plog("probe", "bg heartbeat enter (no ContextInfo)")
    while not _bg_stop:
        try:
            time.sleep(float(POLL_INTERVAL_SEC))
        except Exception:
            break
        if _bg_stop:
            break
        now = time.time()
        if (now - last_alive) >= alive_every:
            last_alive = now
            _emit_alive()
    try:
        _plog("probe", "bg heartbeat exit")
    except Exception:
        pass


def _start_threads():
    global _bg_thread, _bg_stop
    _bg_stop = False
    try:
        if _bg_thread is not None and getattr(_bg_thread, "is_alive", lambda: False)():
            _plog("probe", "bg heartbeat already alive")
        else:
            _bg_thread = threading.Thread(
                target=_bg_poll_loop, name="probe-bg", args=()
            )
            try:
                _bg_thread.daemon = True
            except Exception:
                pass
            _bg_thread.start()
            _plog("probe", "bg heartbeat started")
    except Exception as e:
        _plog("probe", "bg heartbeat failed: %s" % e)


def _seed_existing():
    """Remember files but do not mark them loaded.

    loaded_mtime stays 0 so the run_time callback slices whatever current.py
    is already on disk. init itself does not run the job.
    """
    for spec in WATCH_SPECS:
        st = _state(spec["name"])
        mtime, size = _file_mtime_size(_watch_file(spec))
        if mtime > 0 and size > 0:
            st["last_mtime"] = mtime
            st["stable_mtime"] = mtime
            st["stable_hits"] = int(STABLE_NEED)
            st["pending_mtime"] = mtime
            st["loaded_mtime"] = 0
            _plog(
                spec["log_tag"],
                "existing current.py queued for timer slices mtime=%d size=%d"
                % (mtime, size),
            )
        else:
            _plog(spec["log_tag"], "waiting for current.py")


def _on_strategy_thread():
    ident = _strategy_ident
    if ident is None:
        return False
    try:
        return threading.current_thread().ident == ident
    except Exception:
        return False


def _note_offthread():
    global _offthread_logged
    if _offthread_logged:
        return
    _offthread_logged = True
    _plog("probe", "timer not on strategy thread; skip download")


def _on_strategy_tick(ContextInfo, where):
    """One poll plus at most one slice. Caller must be the strategy thread."""
    global _ctx, _slice_busy
    if ContextInfo is None:
        ContextInfo = _ctx
    if not _on_strategy_thread():
        _note_offthread()
        return
    if _slice_busy:
        return
    _ctx = ContextInfo
    _slice_busy = True
    try:
        _poll(ContextInfo)
        _drive_slice(ContextInfo)
    except KeyboardInterrupt:
        return
    except Exception:
        try:
            _plog("probe", "%s error: %s" % (where, traceback.format_exc()[-500:]))
        except Exception:
            pass
    finally:
        _slice_busy = False


def probe_slice_timer(ContextInfo):
    """run_time callback. QMT invokes this on the strategy thread."""
    _on_strategy_tick(ContextInfo, "timer")


def _arm_slice_timer(ContextInfo):
    try:
        ContextInfo.run_time(
            SLICE_TIMER_NAME,
            SLICE_TIMER_PERIOD,
            SLICE_TIMER_START,
            "SH",
        )
        _plog(
            "probe",
            "run_time armed name=%s interval=%s" % (SLICE_TIMER_NAME, SLICE_TIMER_PERIOD),
        )
    except Exception as e:
        _plog("probe", "run_time failed: %s" % e)


def init(ContextInfo):
    global _last_check_ts, _ctx, _bg_stop, _strategy_ident, _slice_busy, _offthread_logged
    global _active, _next_slice_ts
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    _last_check_ts = 0.0
    _active = None
    _next_slice_ts = 0.0
    _states.clear()
    _slice_busy = False
    _offthread_logged = False
    try:
        _strategy_ident = threading.current_thread().ident
    except Exception:
        _strategy_ident = None
    _plog(
        "probe",
        "version=%s timer-main interval=%s budget_ms=%d gap_ms=%d"
        % (
            PROBE_VERSION,
            SLICE_TIMER_PERIOD,
            int(SLICE_BUDGET_SEC * 1000),
            int(SLICE_GAP_SEC * 1000),
        ),
    )
    for spec in WATCH_SPECS:
        _ensure_watch_dir(spec)
        _plog(spec["log_tag"], "watching %s" % _watch_file(spec))

    try:
        if hasattr(ContextInfo, "set_universe"):
            ContextInfo.set_universe(["000001.SZ"])
    except Exception:
        pass

    _ctx = ContextInfo
    _seed_existing()
    _start_threads()
    _arm_slice_timer(ContextInfo)
    _plog("probe", "init returned; slices run from run_time")


def handlebar(ContextInfo):
    """Optional nudge. Daily bars rarely call this; the timer does the work."""
    global _strategy_ident
    if _strategy_ident is None:
        try:
            _strategy_ident = threading.current_thread().ident
        except Exception:
            pass
    _on_strategy_tick(ContextInfo, "handlebar")
