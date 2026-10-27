#coding:gbk
"""MZ1 QMT fine-backtest hot-reload probe (watch userdata current.py).

Primary job: model-trading strategy that stays running and reloads
  userdata/mz1_qmt_fine_watch/current.py when its mtime changes, then runs
  the embedded MZ1 fine backtest. Results go to FormulaOutput via print
  (ANT_MZ1_BT|...), same as the generated strategy itself.

Pattern copied from 蚂蚁量化规则._load_shadow (importlib + mtime mod name),
but polling happens in handlebar (ant rules only reload on init).

One-time setup:
  模型交易 -> 新建策略 -> Python -> paste this file (or load 探针.py) -> 启动.
Thereafter the GUI 「生成QMT细回测脚本」 only needs to overwrite current.py.

Tick-depth one-shot probe (old 探针) is NOT run here; see backup under
  data/probe_tick_history_depth/ or tools/probe_tick_history_depth_qmt.py.
"""
from __future__ import print_function

import importlib.util
import os
import sys
import time
import traceback
import threading

PROBE_VERSION = "mz1_watch_1.3"

# Fixed watch path under Guojin QMT userdata (project dir is Forbidden FileIO).
WATCH_DIR = r"D:\国金证券QMT交易端2\userdata\mz1_qmt_fine_watch"
WATCH_FILE = os.path.join(WATCH_DIR, "current.py")

# Throttle mtime checks (~1s).
POLL_INTERVAL_SEC = 1.0
# Require same mtime seen twice (or size stable) before load, avoid half-write.
STABLE_NEED = 2

_last_check_ts = 0.0
_last_mtime = 0
_stable_mtime = 0
_stable_hits = 0
_loaded_mtime = 0
_running = False
_pending_mtime = 0
_mod = None
_bg_stop = False
_bg_thread = None
_ctx = None
_run_lock = threading.Lock()


def _plog(msg):
    line = "[mz1-probe] %s" % msg
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


def _emit_error(msg):
    # Keep ANT_MZ1_BT prefix so GUI ingest can see failures.
    try:
        print("ANT_MZ1_BT|ERROR|%s" % msg)
    except Exception:
        pass
    _plog("error %s" % msg)


def _ensure_watch_dir():
    try:
        if not os.path.isdir(WATCH_DIR):
            os.makedirs(WATCH_DIR)
            _plog("created watch dir %s" % WATCH_DIR)
        return True
    except Exception as e:
        _plog("watch dir not writable: %s (%s)" % (WATCH_DIR, e))
        return False


def _file_mtime_size(path):
    try:
        st = os.stat(path)
        return int(st.st_mtime), int(st.st_size)
    except OSError:
        return 0, 0


def _load_watch_module(path, mtime):
    """Like 蚂蚁量化规则._load_shadow: fresh module name per mtime."""
    global _mod
    for key in list(sys.modules.keys()):
        if key == "mz1_watch_strategy" or str(key).startswith("mz1_watch_"):
            sys.modules.pop(key, None)
    mod_name = "mz1_watch_%d" % int(mtime)
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("spec_from_file_location failed: %s" % path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    mod._MZ1_WATCH_MTIME = int(mtime)
    _mod = mod
    return mod


def _run_loaded(ContextInfo, mod, mtime):
    """Prefer run_backtest(ContextInfo); else init(ContextInfo)."""
    fn = getattr(mod, "run_backtest", None)
    if callable(fn):
        _plog("calling run_backtest mtime=%d" % int(mtime))
        return fn(ContextInfo)
    fn = getattr(mod, "init", None)
    if callable(fn):
        _plog("calling init mtime=%d" % int(mtime))
        return fn(ContextInfo)
    raise RuntimeError("watch module has neither run_backtest nor init")


def _try_reload_and_run(ContextInfo, mtime):
    global _running, _pending_mtime, _loaded_mtime
    # Serialize bg-thread + handlebar: without a lock both can pass the
    # "_running" gate and execute the same current.py multiple times.
    if not _run_lock.acquire(False):
        _pending_mtime = int(mtime)
        _plog("lock busy; queued pending mtime=%d" % int(mtime))
        return
    try:
        if int(mtime) == int(_loaded_mtime):
            return
        if _running:
            _pending_mtime = int(mtime)
            _plog("busy; queued pending mtime=%d" % int(mtime))
            return
        path = WATCH_FILE
        if not os.path.isfile(path):
            _plog("no watch file yet: %s" % path)
            return
        _running = True
        try:
            mod = _load_watch_module(path, mtime)
            _run_loaded(ContextInfo, mod, mtime)
            _loaded_mtime = int(mtime)
            _plog("reloaded mtime=%d ran" % int(mtime))
        except Exception:
            tb = traceback.format_exc()
            # keep short for FormulaOutput
            _emit_error(tb[-1500:].replace("\n", " | "))
            _plog("traceback:\n%s" % tb[-2000:])
        finally:
            _running = False
    finally:
        try:
            _run_lock.release()
        except Exception:
            pass
    # Drain one pending mtime after finish (outside lock so nested call can acquire).
    if _pending_mtime and int(_pending_mtime) != int(_loaded_mtime):
        nxt = int(_pending_mtime)
        _pending_mtime = 0
        if nxt != int(_loaded_mtime):
            _plog("draining pending mtime=%d" % nxt)
            _try_reload_and_run(ContextInfo, nxt)


def _poll(ContextInfo):
    global _last_check_ts, _last_mtime, _stable_mtime, _stable_hits
    now = time.time()
    if (now - _last_check_ts) < float(POLL_INTERVAL_SEC):
        return
    _last_check_ts = now
    path = WATCH_FILE
    mtime, size = _file_mtime_size(path)
    if mtime <= 0:
        return
    if mtime == _loaded_mtime:
        _stable_mtime = mtime
        _stable_hits = 0
        return
    # Stability: same mtime for STABLE_NEED consecutive polls.
    if mtime == _stable_mtime and size > 0:
        _stable_hits += 1
    else:
        _stable_mtime = mtime
        _stable_hits = 1
    _last_mtime = mtime
    if _stable_hits < int(STABLE_NEED):
        return
    if _running:
        global _pending_mtime
        _pending_mtime = mtime
        return
    _try_reload_and_run(ContextInfo, mtime)



def _bg_poll_loop():
    """Poll watch file even when market is closed / handlebar idle."""
    global _bg_stop, _ctx
    alive_every = 30.0
    last_alive = 0.0
    _plog("bg poll loop enter")
    while not _bg_stop:
        try:
            time.sleep(float(POLL_INTERVAL_SEC))
        except Exception:
            break
        if _bg_stop:
            break
        ctx = _ctx
        if ctx is None:
            continue
        try:
            _poll(ctx)
        except Exception:
            try:
                _plog("bg poll error: %s" % traceback.format_exc()[-400:])
            except Exception:
                pass
        now = time.time()
        if (now - last_alive) >= alive_every:
            last_alive = now
            try:
                mtime, size = _file_mtime_size(WATCH_FILE)
                _plog(
                    "alive watching mtime=%d size=%d loaded=%d"
                    % (int(mtime), int(size), int(_loaded_mtime))
                )
            except Exception:
                try:
                    _plog("alive")
                except Exception:
                    pass
    try:
        _plog("bg poll loop exit")
    except Exception:
        pass


def init(ContextInfo):
    global _last_mtime, _loaded_mtime, _running, _pending_mtime
    global _stable_mtime, _stable_hits, _last_check_ts
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    _running = False
    _pending_mtime = 0
    _loaded_mtime = 0
    _last_mtime = 0
    _stable_mtime = 0
    _stable_hits = 0
    _last_check_ts = 0.0
    _plog("version=%s watching %s" % (PROBE_VERSION, WATCH_FILE))
    _ensure_watch_dir()
    # Keep one liquid code so daytime handlebar still fires; after-hours
    # relies on the background poll thread below (empty universe never ticks).
    try:
        if hasattr(ContextInfo, "set_universe"):
            ContextInfo.set_universe(["000001.SZ"])
    except Exception:
        pass
    global _ctx, _bg_stop, _bg_thread
    _ctx = ContextInfo
    _bg_stop = False
    try:
        if _bg_thread is not None and getattr(_bg_thread, "is_alive", lambda: False)():
            _plog("bg poll thread already alive")
        else:
            _bg_thread = threading.Thread(
                target=_bg_poll_loop, name="mz1-probe-bg", args=()
            )
            try:
                _bg_thread.daemon = True
            except Exception:
                pass
            _bg_thread.start()
            _plog("bg poll thread started (after-hours safe)")
    except Exception as e:
        _plog("bg poll thread failed: %s" % e)
    # Do NOT auto-run existing current.py on init (avoids leftover/bad scripts
    # connecting MiniQMT 58610 before GUI regenerates). Seed mtime so first
    # polls do not treat the current file as a change; only later mtime bumps run.
    mtime, size = _file_mtime_size(WATCH_FILE)
    if mtime > 0 and size > 0:
        _last_mtime = mtime
        _stable_mtime = mtime
        _stable_hits = 0
        _loaded_mtime = mtime  # pretend already "loaded" so poll skips until change
        _plog(
            "existing current.py ignored until next change mtime=%d size=%d"
            % (mtime, size)
        )
    else:
        _plog("waiting for current.py (GUI generate will drop it here)")


def handlebar(ContextInfo):
    try:
        _poll(ContextInfo)
    except KeyboardInterrupt:
        return
    except Exception:
        try:
            _plog("handlebar poll error: %s" % traceback.format_exc()[-500:])
        except Exception:
            pass
