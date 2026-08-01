#coding:gbk
"""Shadow ������tick �������� rules_armed.json��д results.json��"""
import os
import sys
import time
from typing import List, Optional

SHADOW_VERSION = "20260801.01"
SEED_INTERVAL_SEC = 3
AUCTION_SEED_INTERVAL_SEC = 1.0
NIGHT_RETRY_INTERVAL_SEC = 3.0
NIGHT_PENDING_TIMEOUT_SEC = 90.0
_ORDERS_ENABLED = True
_MIN_BUY_AMOUNT = 0.0
_BUY_BLOCK_ENABLED = False
_BUY_BLOCK_START = "09:30:00"
_BUY_BLOCK_END = "09:31:30"
_PASSORDER_MOD = None
_TICK_RUNNER_MTIME = 0.0
_TICK_RUNNER_MOD = None
ShadowTickRunner = None  # filled by _ensure_tick_runner_module
_light_row = None  # filled by _ensure_tick_runner_module
_NIGHT_LAST_ATTEMPT = {}  # task_id -> unix ts

try:
    from ant_qmt_paths import QMT_BUILTIN_DIR
except ImportError:
    from qmt_builtin.ant_qmt_paths import QMT_BUILTIN_DIR

if QMT_BUILTIN_DIR not in sys.path:
    sys.path.insert(0, QMT_BUILTIN_DIR)


def _load_py_module(module_key, filename):
    """�� QMT python Ŀ¼�� mtime �ȼ��أ�ͬ mtime ���� sys.modules�����ⷴ�� exec ��հ󶨡�"""
    import importlib.util

    path = os.path.join(QMT_BUILTIN_DIR, filename)
    if not os.path.isfile(path):
        return None
    mod_name = "%s_%d" % (module_key, int(os.path.getmtime(path)))
    existing = sys.modules.get(mod_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _import_rules_io_module():
    """QMT ͣ�����Բ�ж�� sys.modules������Ӵ����ȼ��� ant_rules_io��"""
    mod = _load_py_module("ant_rules_io", "ant_rules_io.py")
    if mod is not None:
        return mod
    import importlib

    for key in list(sys.modules.keys()):
        if key == "ant_rules_io" or key.startswith("ant_rules_io_"):
            sys.modules.pop(key, None)
    import ant_rules_io as legacy

    return importlib.reload(legacy)


_rio = _import_rules_io_module()
print(
    "[���׺���] rules_io loaded prune=%s file=%s"
    % (
        hasattr(_rio, "prune_results_stocks"),
        os.path.join(QMT_BUILTIN_DIR, "ant_rules_io.py"),
    )
)
PROJECT_ROOT = _rio.PROJECT_ROOT
RESULTS_FLUSH_INTERVAL_SEC = _rio.RESULTS_FLUSH_INTERVAL_SEC
RULES_RELOAD_INTERVAL_SEC = _rio.RULES_RELOAD_INTERVAL_SEC
append_stock_event = _rio.append_stock_event
collect_subscribe_codes = _rio.collect_subscribe_codes
default_paths = _rio.default_paths
empty_results = _rio.empty_results
extract_tick_price = _rio.extract_tick_price
load_json = _rio.load_json
load_rules_armed = _rio.load_rules_armed
rules_file_signature = _rio.rules_file_signature
save_json_atomic = _rio.save_json_atomic
update_price_snapshot = _rio.update_price_snapshot
prune_results_stocks = getattr(_rio, "prune_results_stocks", None)
if prune_results_stocks is None:
    def prune_results_stocks(results, keep_codes):  # type: ignore
        if not isinstance(results, dict):
            return 0
        stocks = results.get("stocks")
        if not isinstance(stocks, dict):
            return 0
        keep = {str(c).strip().upper() for c in (keep_codes or []) if c}
        removed = 0
        for code in list(stocks.keys()):
            if str(code).strip().upper() not in keep:
                del stocks[code]
                removed += 1
        results["stocks"] = stocks
        return removed
try:
    from ant_sector_sync_runner import (  # noqa: E402
        register_sector_sync_timer,
        register_startup_sector_timer,
    )
except ImportError:
    from qmt_builtin.ant_sector_sync_runner import (  # noqa: E402
        register_sector_sync_timer,
        register_startup_sector_timer,
    )


def _ensure_tick_runner_module() -> bool:
    """�� mtime �ȼ��� ant_tick_runner������ QMT ͬ���̻���ɰ棨�� single_buy����"""
    global _TICK_RUNNER_MTIME, _TICK_RUNNER_MOD, ShadowTickRunner, _light_row
    path = os.path.join(QMT_BUILTIN_DIR, "ant_tick_runner.py")
    mtime = os.path.getmtime(path) if os.path.isfile(path) else 0.0
    if (
        _TICK_RUNNER_MOD is not None
        and ShadowTickRunner is not None
        and _light_row is not None
        and mtime == _TICK_RUNNER_MTIME
    ):
        return False
    mod = _load_py_module("ant_tick_runner", "ant_tick_runner.py")
    if mod is None:
        try:
            import ant_tick_runner as legacy  # noqa: WPS433
        except ImportError:
            from qmt_builtin import ant_tick_runner as legacy  # noqa: WPS433
        import importlib

        for key in list(sys.modules.keys()):
            if key == "ant_tick_runner" or key.startswith("ant_tick_runner_"):
                sys.modules.pop(key, None)
        mod = importlib.reload(legacy)
    _TICK_RUNNER_MOD = mod
    _TICK_RUNNER_MTIME = mtime
    ShadowTickRunner = mod.ShadowTickRunner
    _light_row = mod._light_row
    src_ok = False
    try:
        import inspect

        src_ok = "single_buy" in inspect.getsource(mod.ShadowTickRunner._process_row)
        src_sell = "single_sell" in inspect.getsource(mod.ShadowTickRunner._process_row)
        src_bsell = "breakthrough_sell" in inspect.getsource(mod.ShadowTickRunner._process_row)
        src_elastic = "best_sell" in inspect.getsource(mod.ShadowTickRunner._process_row)
        src_bbuy = "best_buy" in inspect.getsource(mod.ShadowTickRunner._process_row)
        src_cage = "cage_buy" in inspect.getsource(mod.ShadowTickRunner._process_row)
        src_grid = "grid_buy" in inspect.getsource(mod.ShadowTickRunner._process_row)
        src_sc = "scheduled_clear" in inspect.getsource(mod.ShadowTickRunner._process_row)
        src_early = "early_place" in inspect.getsource(mod.ShadowTickRunner._process_row) or (
            "early_order_enabled" in inspect.getsource(mod.ShadowTickRunner.__init__)
        )
    except Exception:
        src_ok = False
        src_sell = False
        src_bsell = False
        src_elastic = False
        src_bbuy = False
        src_cage = False
        src_grid = False
        src_sc = False
        src_early = False
    print(
        "[���׺���] tick_runner hot-loaded mtime=%s single_buy=%s single_sell=%s breakthrough_sell=%s best_sell=%s best_buy=%s cage=%s grid=%s clear=%s early=%s file=%s"
        % (
            int(mtime),
            src_ok,
            src_sell,
            src_bsell,
            src_elastic,
            src_bbuy,
            src_cage,
            src_grid,
            src_sc,
            src_early,
            path,
        )
    )
    return True

#          tick      ContextInfo handlebar        
_RUNNER = None
_SUB_ID = None
_RESULTS = None
_RULES_PATH = ""
_RESULTS_PATH = ""
_RULES_SIG = ""
_TICK_COUNT = 0
_CONTEXT = None
_SUBSCRIBED_CODES: List[str] = []
_LAST_SEED_TS = 0.0
_LAST_PERIODIC_TS = 0.0
_LAST_FLUSH_TS = 0.0
_LAST_ACCOUNT_SYNC_TS = 0.0
_ACCOUNT_SNAPSHOT_IMPORT_ERR = ""
_ACCOUNT_SNAPSHOT_SKIP_REASON = ""
ACCOUNT_SYNC_INTERVAL_SEC = 5.0
_PENDING_RESUBSCRIBE: Optional[List[str]] = None
_DAILY_SYNC_MTIME = 0.0
_DAILY_SYNC_MOD = None
_AFTER_RANK_MTIME = 0.0
_AFTER_RANK_MOD = None
_TICK_FULL_SYNC_MTIME = 0.0
_TICK_FULL_SYNC_MOD = None
_SECTOR_SYNC_MTIME = 0.0
_SECTOR_SYNC_MOD = None


def _get_daily_sync_runner():
    """QMT ͬ������ͣ�����Բ���ж��ģ�飻���ļ� mtime ������ daily_sync��"""
    global _DAILY_SYNC_MTIME, _DAILY_SYNC_MOD
    import importlib

    path = os.path.join(QMT_BUILTIN_DIR, "ant_daily_sync_runner.py")
    mtime = os.path.getmtime(path) if os.path.isfile(path) else 0.0
    if _DAILY_SYNC_MOD is not None and mtime == _DAILY_SYNC_MTIME:
        return _DAILY_SYNC_MOD
    try:
        import ant_daily_sync_runner as mod
    except ImportError:
        import qmt_builtin.ant_daily_sync_runner as mod
    mod = importlib.reload(mod)
    try:
        import ant_data_sync_request as req_mod
    except ImportError:
        import qmt_builtin.ant_data_sync_request as req_mod
    importlib.reload(req_mod)
    _DAILY_SYNC_MOD = mod
    _DAILY_SYNC_MTIME = mtime
    print(
        "[����ͬ��] module loaded version=%s file=%s"
        % (getattr(mod, "DAILY_SYNC_VERSION", "?"), path)
    )
    return mod


def _get_after_hours_rank_runner():
    """�� mtime ������ after_hours_rank������ͣ���������û���ɰ档"""
    global _AFTER_RANK_MTIME, _AFTER_RANK_MOD
    import importlib

    path = os.path.join(QMT_BUILTIN_DIR, "ant_after_hours_rank_runner.py")
    mtime = os.path.getmtime(path) if os.path.isfile(path) else 0.0
    if _AFTER_RANK_MOD is not None and mtime == _AFTER_RANK_MTIME:
        return _AFTER_RANK_MOD
    try:
        import ant_after_hours_rank_runner as mod
    except ImportError:
        import qmt_builtin.ant_after_hours_rank_runner as mod
    mod = importlib.reload(mod)
    _AFTER_RANK_MOD = mod
    _AFTER_RANK_MTIME = mtime
    print(
        "[�̺�����] ģ���Ѽ��� �汾=%s �ļ�=%s"
        % (getattr(mod, "AFTER_HOURS_RANK_VERSION", "?"), path)
    )
    return mod


def _get_tick_full_sync_runner():
    """�� mtime ������ tick_full_sync��"""
    global _TICK_FULL_SYNC_MTIME, _TICK_FULL_SYNC_MOD
    import importlib

    path = os.path.join(QMT_BUILTIN_DIR, "ant_tick_full_sync_runner.py")
    mtime = os.path.getmtime(path) if os.path.isfile(path) else 0.0
    if _TICK_FULL_SYNC_MOD is not None and mtime == _TICK_FULL_SYNC_MTIME:
        return _TICK_FULL_SYNC_MOD
    try:
        import ant_tick_full_sync_runner as mod
    except ImportError:
        import qmt_builtin.ant_tick_full_sync_runner as mod
    mod = importlib.reload(mod)
    _TICK_FULL_SYNC_MOD = mod
    _TICK_FULL_SYNC_MTIME = mtime
    print(
        "[�ֱ�ͬ��] ģ���Ѽ��� �汾=%s �ļ�=%s"
        % (getattr(mod, "TICK_FULL_SYNC_VERSION", "?"), path)
    )
    return mod


def _get_sector_sync_runner():
    """�� mtime ������ sector_sync��������������� 20260728.2��"""
    global _SECTOR_SYNC_MTIME, _SECTOR_SYNC_MOD
    import importlib

    path = os.path.join(QMT_BUILTIN_DIR, "ant_sector_sync_runner.py")
    mtime = os.path.getmtime(path) if os.path.isfile(path) else 0.0
    if _SECTOR_SYNC_MOD is not None and mtime == _SECTOR_SYNC_MTIME:
        return _SECTOR_SYNC_MOD
    try:
        import ant_sector_sync_runner as mod
    except ImportError:
        import qmt_builtin.ant_sector_sync_runner as mod
    mod = importlib.reload(mod)
    _SECTOR_SYNC_MOD = mod
    _SECTOR_SYNC_MTIME = mtime
    print(
        "[���ͬ��] module loaded version=%s file=%s"
        % (getattr(mod, "SECTOR_SYNC_VERSION", "?"), path)
    )
    return mod


def peek_results():
    """������ļ��ڴ� QMT ����������ȡ�ʽ��д�� results��"""
    return _RESULTS


def _get_passorder_mod():
    global _PASSORDER_MOD
    mod = _load_py_module("ant_passorder", "ant_passorder.py")
    if mod is not None:
        _PASSORDER_MOD = mod
        # �ȼ�����ģ���� builtins �ع��
        if hasattr(mod, "bind_runtime_globals") and not getattr(mod, "is_bound", lambda: False)():
            try:
                mod.bind_runtime_globals(None)
            except Exception:
                pass
    return _PASSORDER_MOD


def _disarm_task_in_rules_armed(task_id: str) -> None:
    """passorder �ɹ������̰� rules_armed ��Ӧ������ enabled=False�����������д�ӳٵ�����������"""
    tid = str(task_id or "").strip()
    if not tid or not _RULES_PATH or not os.path.isfile(_RULES_PATH):
        return
    try:
        data = load_json(_RULES_PATH)
    except Exception:
        return
    if not isinstance(data, dict):
        return
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return
    changed = False
    for t in tasks:
        if not isinstance(t, dict):
            continue
        if str(t.get("task_id") or "").strip() != tid:
            continue
        if t.get("enabled", True):
            t["enabled"] = False
            changed = True
    if not changed:
        return
    try:
        from datetime import datetime

        data["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        save_json_atomic = None
        try:
            from ant_rules_io import save_json_atomic as _save
            save_json_atomic = _save
        except Exception:
            try:
                from qmt_builtin.ant_rules_io import save_json_atomic as _save
                save_json_atomic = _save
            except Exception:
                save_json_atomic = None
        if save_json_atomic is not None:
            save_json_atomic(_RULES_PATH, data)
        else:
            with open(_RULES_PATH, "w", encoding="utf-8") as f:
                import json as _json

                _json.dump(data, f, ensure_ascii=False, indent=2)
        print("[���׺���] disarmed task in rules_armed: %s" % tid)
    except Exception as e:
        print("[���׺���] disarm rules_armed error: %s" % e)



def _mark_grid_point_in_rules_armed(task_id: str, grid_index: int, all_done: bool = False) -> None:
    """�����λ��ɺ�д�� executed_grids��ȫ����ɲ� enabled=False��"""
    tid = str(task_id or "").strip()
    if not tid or not _RULES_PATH or not os.path.isfile(_RULES_PATH):
        return
    try:
        gi = int(grid_index)
    except (TypeError, ValueError):
        return
    try:
        data = load_json(_RULES_PATH)
    except Exception:
        return
    if not isinstance(data, dict):
        return
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return
    changed = False
    for t in tasks:
        if not isinstance(t, dict):
            continue
        if str(t.get("task_id") or "").strip() != tid:
            continue
        grids = []
        for x in t.get("executed_grids") or []:
            try:
                grids.append(int(x))
            except (TypeError, ValueError):
                pass
        if gi not in grids:
            grids.append(gi)
            grids = sorted(set(grids))
            t["executed_grids"] = grids
            changed = True
        else:
            grids = sorted(set(grids))
        n = int(t.get("num_grids") or 0)
        if all_done or (n > 0 and len(grids) >= n + 1):
            if t.get("enabled", True):
                t["enabled"] = False
                changed = True
        break
    if not changed:
        return
    try:
        from datetime import datetime

        data["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        try:
            from ant_rules_io import save_json_atomic
        except Exception:
            from qmt_builtin.ant_rules_io import save_json_atomic
        save_json_atomic(_RULES_PATH, data)
        print("[���׺���] grid mark rules_armed: %s g=%s all=%s" % (tid, gi, all_done))
    except Exception as e:
        print("[���׺���] grid mark rules_armed error: %s" % e)


def _seed_done_from_results_orders() -> None:
    """���ѳɹ��¹��ı��� orders ��� done_task_ids���������ܲ��Ժ󸴴�"""
    global _RESULTS, _RUNNER
    if _RUNNER is None or not isinstance(_RESULTS, dict):
        return
    ids = list(_RESULTS.get("done_task_ids") or [])
    for o in _RESULTS.get("orders") or []:
        if not isinstance(o, dict):
            continue
        tid = str(o.get("task_id") or "").strip()
        if not tid:
            continue
        msg = str(o.get("msg") or "")
        st = str(o.get("status") or "").lower()
        ev = str(o.get("event_type") or "")
        if msg == "passorder_called" or st in ("submitted", "filled", "error", "skipped"):
            # ��ǰ�ҵ����� done��ȷ��/�ɽ�������
            if bool(o.get("early_order")) and st != "filled" and ev != "early_confirm":
                continue
            # ҹ��/��ʱ��֣����̨�ѱ�����������ϵ���������
            if ev in (
                "night_buy_hit",
                "night_sell_hit",
                "scheduled_clear_hit",
            ) or "ҹ��" in str(o.get("strategy_name") or "") or "��ʱ���" in str(
                o.get("strategy_name") or ""
            ):
                try:
                    bst = int(o.get("broker_status")) if o.get("broker_status") is not None else -1
                except (TypeError, ValueError):
                    bst = -1
                if bst == 57 or st == "error":
                    continue
                if bst not in (50, 51, 52, 55, 56) and st != "filled":
                    continue
            gi = o.get("grid_index")
            key = tid
            if gi is not None and (
                ev.startswith("grid_")
                or ev == "early_confirm"
            ):
                try:
                    key = "%s@g%d" % (tid, int(gi))
                except (TypeError, ValueError):
                    key = tid
            if key not in ids:
                ids.append(key)
    try:
        _RUNNER.hydrate_done_task_ids(ids)
        _RESULTS["done_task_ids"] = _RUNNER.dump_done_task_ids()
    except Exception as e:
        print("[���׺���] seed done_task_ids error: %s" % e)


def _is_trading_day_local(d):
    try:
        from utils.trading_day import is_tradeday

        return bool(is_tradeday(d))
    except Exception:
        return d.weekday() < 5


def _next_trading_day_915(after_date=None):
    from datetime import datetime, time as dtime, timedelta

    base = after_date or (datetime.now().date() + timedelta(days=1))
    check = base
    for _ in range(12):
        if _is_trading_day_local(check):
            return datetime.combine(check, dtime(9, 15))
        check = check + timedelta(days=1)
    return datetime.combine(datetime.now().date() + timedelta(days=1), dtime(9, 15))


def _night_market_window_active():
    """��ͼ��ҹ�ж�ʱ��һ�£��ǽ����� / 9:15 ǰ / 19:29:59.9 �� �� �ɹң�ֱ����һ������ 9:15��"""
    from datetime import datetime, time as dtime, timedelta

    now = datetime.now()
    end_at = _next_trading_day_915(now.date() + timedelta(days=1))
    # �������ǽ���������δ�� 9:15��������Ϊ���� 9:15
    if _is_trading_day_local(now.date()) and now.time() < dtime(9, 15):
        end_at = datetime.combine(now.date(), dtime(9, 15))
    if now >= end_at:
        return False
    if not _is_trading_day_local(now.date()):
        return True
    if now.time() < dtime(9, 15):
        return True
    start = datetime.combine(now.date(), dtime(19, 29, 59, 900000))
    return now >= start


def _night_task_done(tid: str) -> bool:
    tid = str(tid or "").strip()
    if not tid or _RUNNER is None:
        return False
    try:
        for st in (_RUNNER._states or {}).values():
            if tid in getattr(st, "done_task_ids", set()):
                return True
    except Exception:
        pass
    try:
        if tid in list((_RESULTS or {}).get("done_task_ids") or []):
            return True
    except Exception:
        pass
    return False


def _mark_night_task_done(code: str, tid: str) -> None:
    global _RESULTS
    tid = str(tid or "").strip()
    code = str(code or "").strip().upper()
    if not tid:
        return
    try:
        if _RUNNER is not None:
            st = (_RUNNER._states or {}).get(code)
            if st is None and code:
                # ��д������ state �� done ���ϣ�ȡ��һ��
                for _c, st2 in (_RUNNER._states or {}).items():
                    st = st2
                    break
            if st is not None:
                st.done_task_ids.add(tid)
            if hasattr(_RUNNER, "dump_done_task_ids") and _RESULTS is not None:
                _RESULTS["done_task_ids"] = _RUNNER.dump_done_task_ids()
    except Exception:
        pass
    _disarm_task_in_rules_armed(tid)


def _parse_order_at_ts(o) -> float:
    import time as _time
    from datetime import datetime as _dt

    raw = str((o or {}).get("at") or (o or {}).get("order_time") or "").strip()
    if not raw:
        return 0.0
    try:
        return _dt.strptime(raw[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        try:
            return float(_time.time())
        except Exception:
            return 0.0


def _night_is_pending(tid: str) -> bool:
    """�� passorder ��δ�ϵ���δ�ѱ� �� pending����ʱ���������ԣ���"""
    tid = str(tid or "").strip()
    if not tid or not isinstance(_RESULTS, dict):
        return False
    import time as _time

    now = _time.time()
    for o in reversed(list(_RESULTS.get("orders") or [])):
        if not isinstance(o, dict):
            continue
        if str(o.get("task_id") or "") != tid:
            continue
        ev = str(o.get("event_type") or "")
        if ev not in ("night_buy_hit", "night_sell_hit"):
            continue
        try:
            bst = int(o.get("broker_status")) if o.get("broker_status") is not None else -1
        except (TypeError, ValueError):
            bst = -1
        st = str(o.get("status") or "").lower()
        if bst == 57 or st == "error":
            return False
        if bst in (50, 51, 52, 55, 56) or st == "filled":
            return False
        msg = str(o.get("msg") or "")
        if msg == "passorder_called" or st in ("submitted", "passorder_called"):
            age = now - (_parse_order_at_ts(o) or now)
            if age > float(NIGHT_PENDING_TIMEOUT_SEC):
                return False
            return True
        return False
    return False


def _finalize_night_market_orders() -> bool:
    """��̨�ѱ��� disarm + �� done����ͼ�����ѱ���д��"""
    if not isinstance(_RESULTS, dict):
        return False
    changed = False
    for o in list(_RESULTS.get("orders") or []):
        if not isinstance(o, dict):
            continue
        ev = str(o.get("event_type") or "")
        if ev not in ("night_buy_hit", "night_sell_hit"):
            continue
        tid = str(o.get("task_id") or "").strip()
        if not tid or bool(o.get("night_confirmed")):
            continue
        try:
            bst = int(o.get("broker_status")) if o.get("broker_status") is not None else -1
        except (TypeError, ValueError):
            bst = -1
        st = str(o.get("status") or "").lower()
        if bst not in (50, 51, 52, 55, 56) and st != "filled":
            continue
        code = str(o.get("stock_code") or "").strip().upper()
        _mark_night_task_done(code, tid)
        o["night_confirmed"] = True
        changed = True
        print(
            "[���׺���] night confirmed %s tid=%s bst=%s sysid=%s"
            % (code, tid, bst, o.get("order_sysid"))
        )
    return changed


def _poll_night_market_events():
    """ʱ�䴰�ڶ�����װҹ�й��򷢳��ҵ��¼��������� tick����"""
    global _NIGHT_LAST_ATTEMPT
    if not _ORDERS_ENABLED or _RUNNER is None:
        return []
    if not _night_market_window_active():
        return []
    import time as _time

    now = _time.time()
    events = []
    for t in list(getattr(_RUNNER, "tasks", None) or []):
        if not isinstance(t, dict) or not t.get("enabled", True):
            continue
        rtype = str(t.get("rule_type") or "").strip()
        if rtype not in ("night_buy", "night_sell"):
            continue
        tid = str(t.get("task_id") or "").strip()
        code = str(t.get("stock_code") or "").strip().upper()
        trig = float(t.get("trigger_price") or 0)
        vol = int(t.get("max_volume") or 0)
        if not tid or not code or trig <= 0 or vol <= 0:
            continue
        if _night_task_done(tid):
            continue
        if _night_is_pending(tid):
            continue
        last = float(_NIGHT_LAST_ATTEMPT.get(tid) or 0)
        if now - last < float(NIGHT_RETRY_INTERVAL_SEC):
            continue
        _NIGHT_LAST_ATTEMPT[tid] = now
        events.append(
            {
                "type": "night_buy_hit" if rtype == "night_buy" else "night_sell_hit",
                "stock_code": code,
                "task_id": tid,
                "trigger_price": trig,
                "last_price": trig,
                "max_volume": vol,
                "msg": "night_market",
            }
        )
    return events


def _clear_done_task(stock_code: str, task_id: str) -> None:
    """�µ�ʧ�ܣ���δ�󶨣�ʱ�����������޸���ͬ�����ԡ�"""
    if _RUNNER is None or not task_id:
        return
    try:
        st = _RUNNER._states.get(str(stock_code or "").strip().upper())
        if st is not None and hasattr(st, "done_task_ids"):
            st.done_task_ids.discard(str(task_id))
            print("[���׺���] unlock task for retry: %s" % task_id)
    except Exception as e:
        print("[���׺���] unlock task error: %s" % e)


def _refresh_orders_enabled(rules: Optional[dict] = None) -> None:
    global _ORDERS_ENABLED
    data = rules
    if data is None and _RULES_PATH and os.path.isfile(_RULES_PATH):
        try:
            data = load_rules_armed(_RULES_PATH)
        except Exception:
            data = None
    if isinstance(data, dict) and "orders_enabled" in data:
        _ORDERS_ENABLED = bool(data.get("orders_enabled"))
    else:
        _ORDERS_ENABLED = True
    _refresh_min_buy_amount(data if isinstance(data, dict) else rules)
    _refresh_buy_block_window(data if isinstance(data, dict) else rules)


def _refresh_min_buy_amount(rules: Optional[dict] = None) -> None:
    """�� rules_armed ˢ��ȫ����С�����"""
    global _MIN_BUY_AMOUNT
    data = rules
    if data is None and _RULES_PATH and os.path.isfile(_RULES_PATH):
        try:
            data = load_rules_armed(_RULES_PATH)
        except Exception:
            data = None
    try:
        _MIN_BUY_AMOUNT = max(0.0, float((data or {}).get("min_buy_amount") or 0))
    except (TypeError, ValueError):
        _MIN_BUY_AMOUNT = 0.0
    print("[���׺���] min_buy_amount=%.2f" % _MIN_BUY_AMOUNT)


def _parse_hms_to_seconds(raw: str) -> int:
    parts = str(raw or "").strip().split(":")
    try:
        h = int(parts[0]) if len(parts) > 0 else 0
        m = int(parts[1]) if len(parts) > 1 else 0
        s = int(parts[2]) if len(parts) > 2 else 0
        return h * 3600 + m * 60 + s
    except (TypeError, ValueError):
        return 0


def _refresh_buy_block_window(rules: Optional[dict] = None) -> None:
    """�� rules_armed ˢ�¿��̽���ʱ�䴰��"""
    global _BUY_BLOCK_ENABLED, _BUY_BLOCK_START, _BUY_BLOCK_END
    data = rules
    if data is None and _RULES_PATH and os.path.isfile(_RULES_PATH):
        try:
            data = load_rules_armed(_RULES_PATH)
        except Exception:
            data = None
    data = data or {}
    _BUY_BLOCK_ENABLED = bool(data.get("buy_block_window_enabled"))
    _BUY_BLOCK_START = str(data.get("buy_block_start") or "09:30:00").strip()
    _BUY_BLOCK_END = str(data.get("buy_block_end") or "09:31:30").strip()
    print(
        "[���׺���] buy_block enabled=%s %s-%s"
        % (_BUY_BLOCK_ENABLED, _BUY_BLOCK_START, _BUY_BLOCK_END)
    )


def _is_in_buy_block_window() -> bool:
    """��ͼ��һ�£������ҵ�ǰʱ������ [start, end]�����˵㣩��"""
    if not _BUY_BLOCK_ENABLED:
        return False
    from datetime import datetime as _dt

    now = _dt.now()
    now_s = now.hour * 3600 + now.minute * 60 + now.second
    start_s = _parse_hms_to_seconds(_BUY_BLOCK_START)
    end_s = _parse_hms_to_seconds(_BUY_BLOCK_END)
    if start_s <= end_s:
        return start_s <= now_s <= end_s
    return now_s >= start_s or now_s <= end_s


def _attach_event_context_to_order(record: dict, ev: dict) -> None:
    """���¼������ͻ����ϸ/ָ�����������¼����ִ�м�¼����չʾ��"""
    if not isinstance(record, dict) or not isinstance(ev, dict):
        return
    detail = str(ev.get("detail") or "").strip()
    if detail:
        record.setdefault("detail", detail)
        record.setdefault("true_breakthrough_detail", detail)
    if ev.get("true_breakthrough_passed") is not None:
        record.setdefault("true_breakthrough_passed", ev.get("true_breakthrough_passed"))
    elif str(ev.get("type") or "") == "tb_pass" or str(ev.get("event_type") or "") == "tb_pass":
        record.setdefault("true_breakthrough_passed", True)
    metrics = ev.get("metrics")
    if isinstance(metrics, dict) and metrics:
        record.setdefault("metrics", metrics)
    try:
        lp = float(ev.get("last_price") or 0)
    except (TypeError, ValueError):
        lp = 0.0
    if lp > 0:
        record.setdefault("last_price", lp)


def _finalize_buy_block_skip(
    *,
    po,
    code: str,
    tid: str,
    ev_type: str,
    gi_raw,
    uid: str,
    px: float,
    vol: int,
    strategy_name: str,
    ev: dict,
) -> None:
    """���н��򴰣���ͼ��һ�� �� �����µ����������񣨲����Ե����⣩��"""
    from datetime import datetime as _dt

    msg = "���п��̽���ʱ�䴰��δ�µ���%s-%s��" % (_BUY_BLOCK_START, _BUY_BLOCK_END)
    record = {
        "stock_code": code,
        "side": "buy",
        "price": float(px or 0),
        "volume": 0,
        "status": "skipped",
        "msg": msg,
        "strategy_name": strategy_name,
        "task_id": tid,
        "event_type": ev_type,
        "user_order_id": uid,
        "at": _dt.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "buy_block_window": True,
        "order_sysid": "SKIPPED_BUY_WINDOW",
    }
    _attach_event_context_to_order(record, ev)
    if gi_raw is not None and ev_type == "grid_buy_hit":
        try:
            record["grid_index"] = int(gi_raw)
        except (TypeError, ValueError):
            pass
    po.append_order_record(_RESULTS, record)
    ev["order"] = {
        "ok": False,
        "price": record.get("price"),
        "volume": 0,
        "status": "skipped",
        "msg": msg,
    }
    print(
        "[���׺���] order buy_block skip %s tid=%s px=%s vol=%s msg=%s"
        % (code, tid, px, vol, msg)
    )
    if tid:
        if ev_type == "grid_buy_hit" and gi_raw is not None:
            try:
                gi = int(gi_raw)
            except (TypeError, ValueError):
                gi = -1
            all_done = False
            try:
                st = _RUNNER._states.get(code) if _RUNNER is not None else None
                if st is not None and tid in getattr(st, "done_task_ids", set()):
                    all_done = True
            except Exception:
                all_done = False
            _mark_grid_point_in_rules_armed(tid, gi, all_done=all_done)
        else:
            _disarm_task_in_rules_armed(tid)


def _finalize_band_hard_pass_skip(
    *,
    po,
    code: str,
    tid: str,
    uid: str,
    px: float,
    vol: int,
    ev: dict,
) -> None:
    """�۸��Ӳpass����дʵ�̵���д�� skipped ������ͼ��/���׼�¼��д��"""
    from datetime import datetime as _dt

    detail = str(ev.get("detail") or "").strip()
    msg = str(ev.get("msg") or "band_hard_pass").strip() or "band_hard_pass"
    record = {
        "stock_code": code,
        "side": "buy",
        "price": float(px or 0),
        "volume": 0,
        "status": "skipped",
        "msg": msg,
        "detail": detail,
        "true_breakthrough_detail": detail,
        "true_breakthrough_passed": True,
        "strategy_name": str(ev.get("strategy_name") or "����-ͻ������"),
        "task_id": tid,
        "event_type": "tb_fail",
        "user_order_id": uid,
        "at": _dt.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "band_hard_pass": True,
        "order_sysid": "BAND_HARD_PASS",
        "last_price": float(ev.get("last_price") or px or 0),
        "max_volume": int(vol or 0),
    }
    po.append_order_record(_RESULTS, record)
    ev["order"] = {
        "ok": False,
        "price": record.get("price"),
        "volume": 0,
        "status": "skipped",
        "msg": msg,
    }
    print(
        "[���׺���] order band_hard_pass skip %s tid=%s px=%s detail=%s"
        % (code, tid, px, (detail[:80] + "...") if len(detail) > 80 else detail)
    )
    if tid:
        _disarm_task_in_rules_armed(tid)


def _mark_buy_task_done(code: str, tid: str, gi_raw=None) -> None:
    """�����ս᣺д�� done������ tick ����������"""
    tid = str(tid or "").strip()
    code = str(code or "").strip().upper()
    if not tid or _RUNNER is None:
        return
    try:
        st = (_RUNNER._states or {}).get(code)
        if st is None and code:
            for _c, st2 in (_RUNNER._states or {}).items():
                st = st2
                break
        if st is not None and hasattr(st, "done_task_ids"):
            if gi_raw is not None:
                try:
                    st.done_task_ids.add("%s@g%d" % (tid, int(gi_raw)))
                except (TypeError, ValueError):
                    st.done_task_ids.add(tid)
            else:
                st.done_task_ids.add(tid)
        if hasattr(_RUNNER, "dump_done_task_ids") and _RESULTS is not None:
            _RESULTS["done_task_ids"] = _RUNNER.dump_done_task_ids()
    except Exception as e:
        print("[���׺���] mark buy done error: %s" % e)


def _finalize_order_below_min_skip(
    *,
    po,
    code: str,
    tid: str,
    ev_type: str,
    gi_raw,
    uid: str,
    px: float,
    vol: int,
    strategy_name: str,
    ev: dict,
    cash_msg: str,
    early_order: bool = False,
) -> None:
    """���������������С���ޣ��������������񣨲�ÿ tick ���ԣ���"""
    from datetime import datetime as _dt

    record = {
        "stock_code": code,
        "side": "buy",
        "price": float(px or 0),
        "volume": 0,
        "status": "skipped",
        "msg": cash_msg,
        "strategy_name": strategy_name,
        "task_id": tid,
        "event_type": ev_type,
        "user_order_id": uid,
        "at": _dt.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "cash_block": "order_below_min",
        "order_sysid": "SKIPPED_MIN_BUY",
    }
    _attach_event_context_to_order(record, ev)
    if early_order:
        record["early_order"] = True
    if gi_raw is not None and ev_type in ("grid_buy_hit", "early_place"):
        try:
            record["grid_index"] = int(gi_raw)
        except (TypeError, ValueError):
            pass
    po.append_order_record(_RESULTS, record)
    ev["order"] = {
        "ok": False,
        "price": record.get("price"),
        "volume": 0,
        "status": "skipped",
        "msg": cash_msg,
    }
    print(
        "[���׺���] order below_min end %s tid=%s px=%s vol=%s msg=%s"
        % (code, tid, px, vol, cash_msg)
    )
    if early_order:
        ekey = _early_key_from_ev(tid, gi_raw)
        try:
            if _RUNNER is not None and hasattr(_RUNNER, "clear_early_state"):
                _RUNNER.clear_early_state(ekey)
        except Exception:
            pass
    _mark_buy_task_done(code, tid, gi_raw)
    if tid:
        if ev_type in ("grid_buy_hit", "early_place") and gi_raw is not None:
            try:
                gi = int(gi_raw)
            except (TypeError, ValueError):
                gi = -1
            _mark_grid_point_in_rules_armed(tid, gi, all_done=True)
        else:
            _disarm_task_in_rules_armed(tid)


def _available_cash() -> float:
    acc = (_RESULTS or {}).get("account") if isinstance(_RESULTS, dict) else None
    if not isinstance(acc, dict):
        return 0.0
    try:
        return float(acc.get("cash") or acc.get("available") or 0)
    except (TypeError, ValueError):
        return 0.0


def _assess_buy_volume(price: float, volume: int):
    """�����ſأ���ͼ�� _assess_buy_cash_requirements ���룩��

    ��С����ֻ�����ʼۡ�����������ֽ��޹أ���
    ���� (ok, adjusted_volume, reason_code, message)��
    """
    px = float(price or 0)
    vol = int(volume or 0)
    min_buy = float(_MIN_BUY_AMOUNT or 0)
    if px <= 0 or vol <= 0:
        return False, 0, "bad_params", "�۸��������Ч��δ�µ�"
    cash = _available_cash()
    required = px * vol
    # ��С���룺ֻ������ί�н�������ֽ��޹� �� ��������
    if min_buy > 0 and required < min_buy:
        return (
            False,
            0,
            "order_below_min",
            "�����������С�����δ�µ���Լ%.2fԪ < ��С%.2fԪ��"
            % (required, min_buy),
        )
    if cash <= 0:
        return (
            False,
            0,
            "no_cash",
            "�޿����ʽ�δ�µ�����ҪԼ%.2fԪ������%.2fԪ��" % (required, cash),
        )
    if required > cash:
        max_vol = int(cash / px / 100) * 100
        if max_vol <= 0:
            return (
                False,
                0,
                "no_cash",
                "�����ʽ���100�ɣ�δ�µ�����ҪԼ%.2fԪ������%.2fԪ��"
                % (required, cash),
            )
        adj_amt = px * max_vol
        # ��������С�����ݻ������ʽ𹻣���������С�������������
        if min_buy > 0 and adj_amt < min_buy:
            return (
                False,
                0,
                "no_cash",
                "�ֽ����������������С���룬�ݲ��µ���Լ%.2fԪ < ��С%.2fԪ������С����"
                % (adj_amt, min_buy),
            )
        return True, max_vol, "shrunk", "�ֽ��㣬������%d��" % max_vol
    return True, vol, "ok", ""


def _unlock_order_task(code: str, tid: str, gi_raw, ev_type: str) -> None:
    unlock_key = tid
    if gi_raw is not None and ev_type in ("grid_buy_hit", "grid_sell_hit"):
        try:
            unlock_key = "%s@g%d" % (tid, int(gi_raw))
        except (TypeError, ValueError):
            unlock_key = tid
    _clear_done_task(code, unlock_key)


def _early_key_from_ev(tid: str, gi_raw) -> str:
    if gi_raw is not None:
        try:
            return "%s@g%d" % (tid, int(gi_raw))
        except (TypeError, ValueError):
            pass
    return str(tid)


def _find_early_order_sysid(tid: str, gi_raw=None) -> str:
    """�� results.orders ����ǰ���Ĺ�̨��ͬ�š�"""
    if not isinstance(_RESULTS, dict):
        return ""
    ekey = _early_key_from_ev(tid, gi_raw)
    prefer_uid = ""
    try:
        if _RUNNER is not None:
            st = (_RUNNER._early or {}).get(ekey) or {}
            prefer_uid = str(st.get("user_order_id") or "")
    except Exception:
        prefer_uid = ""
    best = ""
    for o in reversed(list(_RESULTS.get("orders") or [])):
        if not isinstance(o, dict):
            continue
        if str(o.get("task_id") or "") != tid:
            continue
        if not bool(o.get("early_order")) and str(o.get("event_type") or "") != "early_place":
            continue
        if gi_raw is not None:
            try:
                if int(o.get("grid_index")) != int(gi_raw):
                    continue
            except (TypeError, ValueError):
                continue
        st = str(o.get("status") or "").lower()
        if st in ("cancelled", "skipped", "error", "cancel_sent"):
            continue
        sysid = str(o.get("order_sysid") or "").strip()
        uid = str(o.get("user_order_id") or o.get("pass_uid") or "")
        if prefer_uid and uid and prefer_uid not in uid and uid not in prefer_uid:
            if not sysid:
                continue
        if sysid:
            return sysid
        if not best:
            best = str(o.get("pass_uid") or o.get("user_order_id") or "")
    return best


def _handle_order_events(ContextInfo, events, datas) -> bool:
    """�� single_buy/sell ����ͻ�� tb_pass �� passorder�������Ƿ�д���˶�����¼��"""
    global _RESULTS
    if not events or _RESULTS is None:
        return False
    if not _ORDERS_ENABLED:
        return False
    po = _get_passorder_mod()
    if po is None:
        print("[���׺���] passorder module missing")
        return False
    changed = False
    for ev in events:
        ev_type = str(ev.get("type") or "")
        if ev_type not in (
            "single_buy_hit",
            "single_sell_hit",
            "tb_pass",
            "tb_fail",
            "breakthrough_sell_hit",
            "best_sell_hit",
            "best_buy_hit",
            "cage_buy_hit",
            "cage_sell_hit",
            "grid_buy_hit",
            "grid_sell_hit",
            "scheduled_clear_hit",
            "scheduled_clear_skip",
            "early_place",
            "early_cancel",
            "early_confirm",
            "night_buy_hit",
            "night_sell_hit",
        ):
            continue
        code = str(ev.get("stock_code") or "").strip().upper()
        if not code:
            continue
        # �۸��Ӳpass����ͻ���ѹ�����λ/�������� �� skipped �������� passorder
        if ev_type == "tb_fail":
            msg = str(ev.get("msg") or "").strip()
            detail = str(ev.get("detail") or "")
            is_band_hp = msg == "band_hard_pass" or (
                ("Ӳpass" in detail or "��ͻ�Ʒ���" in detail or "�״���ͻ�Ʒ���" in detail)
                and ("��Ч����" in detail or "Ӳ����" in detail or "����ο���" in detail)
            )
            if not is_band_hp:
                continue
            tid = str(ev.get("task_id") or "")
            uid = tid.replace(":", "_")
            last_px = float(ev.get("last_price") or 0)
            vol = int(ev.get("max_volume") or 0)
            _finalize_band_hard_pass_skip(
                po=po,
                code=code,
                tid=tid,
                uid=uid,
                px=last_px,
                vol=vol,
                ev=ev,
            )
            changed = True
            continue
        tick_row = None
        if isinstance(datas, dict):
            tick_row = datas.get(code) or datas.get(code.lower())
            if tick_row is not None and not isinstance(tick_row, dict):
                tick_row = _light_row(tick_row)
        last_px = float(ev.get("last_price") or 0)
        trig = float(ev.get("trigger_price") or 0)
        vol = int(ev.get("max_volume") or 0)
        tid = str(ev.get("task_id") or "")
        uid = tid.replace(":", "_")
        gi_raw = ev.get("grid_index")
        if gi_raw is not None and ev_type in (
            "grid_buy_hit",
            "grid_sell_hit",
            "early_place",
            "early_cancel",
            "early_confirm",
        ):
            try:
                uid = "%s_g%d" % (uid, int(gi_raw))
            except (TypeError, ValueError):
                pass
        tick_dict = tick_row if isinstance(tick_row, dict) else None

        # ---- ҹ��ί�У�������޼ۣ��ɹ������̨�ѱ��� disarm ----
        if ev_type in ("night_buy_hit", "night_sell_hit"):
            px = float(trig or 0)
            if px <= 0:
                px = float(last_px or 0)
            if ev_type == "night_buy_hit":
                if _is_in_buy_block_window():
                    _finalize_buy_block_skip(
                        po=po,
                        code=code,
                        tid=tid,
                        ev_type=ev_type,
                        gi_raw=gi_raw,
                        uid=uid,
                        px=px,
                        vol=vol,
                        strategy_name="����-ҹ������",
                        ev=ev,
                    )
                    changed = True
                    continue
                ok_cash, vol_adj, reason_code, cash_msg = _assess_buy_volume(px, vol)
                if not ok_cash:
                    if reason_code == "order_below_min":
                        _finalize_order_below_min_skip(
                            po=po,
                            code=code,
                            tid=tid,
                            ev_type=ev_type,
                            gi_raw=gi_raw,
                            uid=uid,
                            px=px,
                            vol=vol,
                            strategy_name="����-ҹ������",
                            ev=ev,
                            cash_msg=cash_msg,
                            early_order=False,
                        )
                        changed = True
                        continue
                    from datetime import datetime as _dt

                    record = {
                        "stock_code": code,
                        "side": "buy",
                        "price": px,
                        "volume": int(vol or 0),
                        "status": "skipped",
                        "msg": cash_msg,
                        "strategy_name": "����-ҹ������",
                        "task_id": tid,
                        "event_type": ev_type,
                        "user_order_id": uid,
                        "at": _dt.now().strftime("%Y-%m-%dT%H:%M:%S"),
                        "cash_block": reason_code,
                    }
                    po.append_order_record(_RESULTS, record)
                    ev["order"] = {
                        "ok": False,
                        "price": px,
                        "volume": vol,
                        "status": "skipped",
                        "msg": cash_msg,
                    }
                    print(
                        "[���׺���] order night_buy skip %s px=%s vol=%s msg=%s"
                        % (code, px, vol, cash_msg)
                    )
                    changed = True
                    continue
                vol = int(vol_adj)
                ok, reason, record = po.place_limit_buy(
                    ContextInfo,
                    code,
                    px,
                    vol,
                    strategy_name="����-ҹ������",
                    user_order_id=uid,
                )
                side_tag = "night_buy"
            else:
                ok, reason, record = po.place_limit_sell(
                    ContextInfo,
                    code,
                    px,
                    vol,
                    strategy_name="����-ҹ������",
                    user_order_id=uid,
                )
                side_tag = "night_sell"
            record["task_id"] = tid
            record["event_type"] = ev_type
            po.append_order_record(_RESULTS, record)
            ev["order"] = {
                "ok": bool(ok),
                "price": record.get("price"),
                "volume": record.get("volume"),
                "status": record.get("status"),
                "msg": record.get("msg") or reason,
            }
            print(
                "[���׺���] order %s %s ok=%s px=%s vol=%s msg=%s"
                % (side_tag, code, ok, record.get("price"), record.get("volume"), record.get("msg"))
            )
            # �� disarm / ���� done���ϵ���ʧ������ѯ����
            changed = True
            continue

        # ---- ��ǰ�µ����� / �� / ȷ�� ----
        if ev_type == "early_place":
            is_buy = True
            try:
                ekey = _early_key_from_ev(tid, gi_raw)
                kind = ""
                if _RUNNER is not None:
                    est = (_RUNNER._early or {}).get(ekey) or {}
                    kind = str(est.get("kind") or "")
                    if est.get("user_order_id"):
                        uid = str(est.get("user_order_id"))
                if kind in ("single_sell", "grid_sell"):
                    is_buy = False
                elif kind in ("single_buy", "grid_buy"):
                    is_buy = True
                else:
                    # ��״̬ʱ��������������Ŀ������ּ�
                    is_buy = float(trig or 0) < float(last_px or 0) or float(trig) <= 0
            except Exception:
                is_buy = True
            px = float(trig or 0)
            if px <= 0:
                px = float(last_px or 0)
            if is_buy:
                ok_cash, vol_adj, reason_code, cash_msg = _assess_buy_volume(px, vol)
                if not ok_cash:
                    if reason_code == "order_below_min":
                        _finalize_order_below_min_skip(
                            po=po,
                            code=code,
                            tid=tid,
                            ev_type=ev_type,
                            gi_raw=gi_raw,
                            uid=uid,
                            px=px,
                            vol=vol,
                            strategy_name="����-��ǰ����",
                            ev=ev,
                            cash_msg=cash_msg,
                            early_order=True,
                        )
                        changed = True
                        continue
                    from datetime import datetime as _dt

                    record = {
                        "stock_code": code,
                        "side": "buy",
                        "price": px,
                        "volume": int(vol or 0),
                        "status": "skipped",
                        "msg": cash_msg,
                        "strategy_name": "����-��ǰ����",
                        "task_id": tid,
                        "event_type": ev_type,
                        "early_order": True,
                        "user_order_id": uid,
                        "at": _dt.now().strftime("%Y-%m-%dT%H:%M:%S"),
                        "cash_block": reason_code,
                    }
                    if gi_raw is not None:
                        try:
                            record["grid_index"] = int(gi_raw)
                        except (TypeError, ValueError):
                            pass
                    po.append_order_record(_RESULTS, record)
                    ev["order"] = {
                        "ok": False,
                        "price": px,
                        "volume": vol,
                        "status": "skipped",
                        "msg": cash_msg,
                    }
                    ekey = _early_key_from_ev(tid, gi_raw)
                    try:
                        if _RUNNER is not None and hasattr(_RUNNER, "clear_early_state"):
                            _RUNNER.clear_early_state(ekey)
                    except Exception:
                        pass
                    print(
                        "[���׺���] order early_buy skip %s px=%s vol=%s msg=%s"
                        % (code, px, vol, cash_msg)
                    )
                    changed = True
                    continue
                vol = int(vol_adj)
                ok, reason, record = po.place_limit_buy(
                    ContextInfo,
                    code,
                    px,
                    vol,
                    strategy_name="����-��ǰ����",
                    user_order_id=uid,
                )
                side_tag = "early_buy"
            else:
                ok, reason, record = po.place_limit_sell(
                    ContextInfo,
                    code,
                    px,
                    vol,
                    strategy_name="����-��ǰ����",
                    user_order_id=uid,
                )
                side_tag = "early_sell"
            record["task_id"] = tid
            record["event_type"] = ev_type
            record["early_order"] = True
            if gi_raw is not None:
                try:
                    record["grid_index"] = int(gi_raw)
                except (TypeError, ValueError):
                    pass
            po.append_order_record(_RESULTS, record)
            ev["order"] = {
                "ok": bool(ok),
                "price": record.get("price"),
                "volume": record.get("volume"),
                "status": record.get("status"),
                "msg": record.get("msg") or reason,
            }
            print(
                "[���׺���] order %s %s ok=%s px=%s vol=%s msg=%s"
                % (side_tag, code, ok, record.get("price"), record.get("volume"), record.get("msg"))
            )
            if not ok:
                ekey = _early_key_from_ev(tid, gi_raw)
                try:
                    if _RUNNER is not None and hasattr(_RUNNER, "clear_early_state"):
                        _RUNNER.clear_early_state(ekey)
                except Exception:
                    pass
            changed = True
            continue

        if ev_type == "early_cancel":
            from datetime import datetime as _dt

            sysid = _find_early_order_sysid(tid, gi_raw)
            ok, reason, crec = False, "no_sysid", {}
            if sysid and hasattr(po, "cancel_order_sysid"):
                ok, reason, crec = po.cancel_order_sysid(ContextInfo, sysid)
            record = {
                "stock_code": code,
                "side": "cancel",
                "price": float(trig or last_px or 0),
                "volume": int(vol or 0),
                "status": "cancel_sent" if ok else "error",
                "msg": str(reason or crec.get("msg") or ""),
                "strategy_name": "����-��ǰ����",
                "task_id": tid,
                "event_type": ev_type,
                "early_order": True,
                "order_sysid": sysid,
                "user_order_id": uid,
                "at": _dt.now().strftime("%Y-%m-%dT%H:%M:%S"),
            }
            if gi_raw is not None:
                try:
                    record["grid_index"] = int(gi_raw)
                except (TypeError, ValueError):
                    pass
            po.append_order_record(_RESULTS, record)
            print(
                "[���׺���] early_cancel %s sysid=%s ok=%s msg=%s"
                % (code, sysid, ok, record.get("msg"))
            )
            changed = True
            continue

        if ev_type == "early_confirm":
            from datetime import datetime as _dt

            side = "buy"
            for o in reversed(list((_RESULTS or {}).get("orders") or [])):
                if not isinstance(o, dict):
                    continue
                if str(o.get("task_id") or "") != tid:
                    continue
                if str(o.get("event_type") or "") != "early_place":
                    continue
                if gi_raw is not None:
                    try:
                        if int(o.get("grid_index")) != int(gi_raw):
                            continue
                    except (TypeError, ValueError):
                        continue
                side = str(o.get("side") or "buy").lower()
                break
            else:
                # �޹ҵ���¼ʱ���ּ���Դ�����
                side = "buy" if float(last_px or 0) <= float(trig or 0) else "sell"
            is_buy = side != "sell"
            record = {
                "stock_code": code,
                "side": "buy" if is_buy else "sell",
                "price": float(trig or last_px or 0),
                "volume": int(vol or 0),
                "status": "filled",
                "msg": "early_confirm",
                "strategy_name": "����-��ǰȷ��",
                "task_id": tid,
                "event_type": ev_type,
                "early_order": True,
                "user_order_id": uid,
                "at": _dt.now().strftime("%Y-%m-%dT%H:%M:%S"),
            }
            if gi_raw is not None:
                try:
                    record["grid_index"] = int(gi_raw)
                except (TypeError, ValueError):
                    pass
            # �����ͬ�ű����б�����
            sysid = _find_early_order_sysid(tid, gi_raw)
            if sysid:
                record["order_sysid"] = sysid
            po.append_order_record(_RESULTS, record)
            if gi_raw is not None:
                try:
                    gi = int(gi_raw)
                except (TypeError, ValueError):
                    gi = -1
                all_done = False
                try:
                    st = _RUNNER._states.get(code) if _RUNNER is not None else None
                    if st is not None and tid in getattr(st, "done_task_ids", set()):
                        all_done = True
                except Exception:
                    all_done = False
                _mark_grid_point_in_rules_armed(tid, gi, all_done=all_done)
            elif tid:
                _disarm_task_in_rules_armed(tid)
            print(
                "[���׺���] early_confirm %s tid=%s px=%s vol=%s"
                % (code, tid, record.get("price"), record.get("volume"))
            )
            changed = True
            continue

        # ��ʱ���������ֻ��д״̬�����µ�
        if ev_type == "scheduled_clear_skip":
            from datetime import datetime as _dt

            record = {
                "stock_code": code,
                "side": "sell",
                "price": float(last_px or trig or 0),
                "volume": int(vol or 0),
                "status": "skipped",
                "msg": str(ev.get("detail") or ev.get("msg") or "skipped"),
                "strategy_name": "����-��ʱ���",
                "task_id": tid,
                "event_type": ev_type,
                "user_order_id": uid,
                "at": _dt.now().strftime("%Y-%m-%dT%H:%M:%S"),
            }
            po.append_order_record(_RESULTS, record)
            ev["order"] = {
                "ok": False,
                "price": record.get("price"),
                "volume": record.get("volume"),
                "status": "skipped",
                "msg": record.get("msg"),
            }
            if tid:
                _disarm_task_in_rules_armed(tid)
            print(
                "[���׺���] order scheduled_clear skip %s px=%s vol=%s msg=%s"
                % (code, record.get("price"), record.get("volume"), record.get("msg"))
            )
            changed = True
            continue
        if ev_type == "best_sell_hit" and vol <= 0:
            # volume=0 ��ʾ��֣��� results �ֲֿ�����
            pos_map = (_RESULTS or {}).get("positions") or {}
            prow = None
            if isinstance(pos_map, dict):
                prow = pos_map.get(code) or pos_map.get(code.split(".")[0])
            if isinstance(prow, dict):
                try:
                    vol = int(float(prow.get("can_use_volume") or prow.get("volume") or 0))
                except (TypeError, ValueError):
                    vol = 0
            vol = (vol // 100) * 100
            if vol <= 0:
                print("[���׺���] best_sell skip no position %s" % code)
                continue
        if ev_type == "scheduled_clear_hit":
            # ����ʱ max_volume ��Ϊȫ�֣����е���/ͻ���������밴�������ضϣ������̨�ϵ���
            # ��ǿ��ˢ�ֲ֣������տգ����á������� - ���������ɽ������ס�
            try:
                snap = _load_py_module(
                    "ant_account_snapshot", "ant_account_snapshot.py"
                )
                if (
                    snap is not None
                    and hasattr(snap, "sync_account_snapshot_to_results")
                    and _RESULTS is not None
                ):
                    snap.sync_account_snapshot_to_results(ContextInfo, _RESULTS)
            except Exception as e:
                print("[���׺���] scheduled_clear position sync fail: %s" % e)
            pos_map = (_RESULTS or {}).get("positions") or {}
            prow = None
            if isinstance(pos_map, dict):
                prow = pos_map.get(code) or pos_map.get(code.split(".")[0])
            avail = 0
            if isinstance(prow, dict):
                try:
                    avail = int(
                        float(prow.get("can_use_volume") or prow.get("volume") or 0)
                    )
                except (TypeError, ValueError):
                    avail = 0
            avail = (avail // 100) * 100
            if avail <= 0 and vol > 0:
                sold = 0
                for o in (_RESULTS or {}).get("orders") or []:
                    if not isinstance(o, dict):
                        continue
                    if str(o.get("stock_code") or "").strip().upper() != code:
                        continue
                    if str(o.get("side") or "").lower() != "sell":
                        continue
                    try:
                        tv = int(float(o.get("traded_volume") or 0))
                    except (TypeError, ValueError):
                        tv = 0
                    if tv <= 0 and str(o.get("broker_status_text") or "") in (
                        "�ѳ�",
                        "����",
                    ):
                        try:
                            tv = int(float(o.get("volume") or 0))
                        except (TypeError, ValueError):
                            tv = 0
                    if tv > 0:
                        sold += tv
                clipped = ((max(0, int(vol) - sold)) // 100) * 100
                if clipped > 0:
                    print(
                        "[���׺���] scheduled_clear clip by filled %s req=%s sold=%s -> %s"
                        % (code, vol, sold, clipped)
                    )
                    avail = clipped
            if avail <= 0:
                print("[���׺���] scheduled_clear skip no position %s" % code)
                if tid:
                    _disarm_task_in_rules_armed(tid)
                continue
            if vol <= 0 or vol > avail:
                print(
                    "[���׺���] scheduled_clear clip vol %s %s -> %s"
                    % (code, vol, avail)
                )
                vol = avail
        if ev_type in (
            "single_sell_hit",
            "breakthrough_sell_hit",
            "best_sell_hit",
            "cage_sell_hit",
            "grid_sell_hit",
            "scheduled_clear_hit",
        ):
            is_buy_order = False
            sell_px = po.resolve_sell_limit_price(
                code,
                last_price=last_px,
                trigger_price=trig,
                tick_row=tick_dict,
            )
            if ev_type == "best_sell_hit":
                strategy_name = "����-��������"
                side_tag = "best_sell"
            elif ev_type == "breakthrough_sell_hit":
                strategy_name = "����-ͻ������"
                side_tag = "breakthrough_sell"
            elif ev_type == "cage_sell_hit":
                strategy_name = "����-��������"
                side_tag = "cage_sell"
            elif ev_type == "grid_sell_hit":
                strategy_name = "����-��������"
                side_tag = "grid_sell"
            elif ev_type == "scheduled_clear_hit":
                strategy_name = "����-��ʱ���"
                side_tag = "scheduled_clear"
            else:
                strategy_name = "����-��������"
                side_tag = "single_sell"
            ok, reason, record = po.place_limit_sell(
                ContextInfo,
                code,
                sell_px,
                vol,
                strategy_name=strategy_name,
                user_order_id=uid,
            )
        elif ev_type == "tb_pass":
            buy_px = po.resolve_buy_limit_price(
                code,
                last_price=last_px,
                trigger_price=trig,
                tick_row=tick_dict,
            )
            strategy_name = "����-ͻ������"
            side_tag = "breakthrough_buy"
            is_buy_order = True
        elif ev_type == "best_buy_hit":
            buy_px = po.resolve_buy_limit_price(
                code,
                last_price=last_px,
                trigger_price=trig,
                tick_row=tick_dict,
            )
            strategy_name = "����-��������"
            side_tag = "best_buy"
            is_buy_order = True
        elif ev_type == "cage_buy_hit":
            buy_px = po.resolve_buy_limit_price(
                code,
                last_price=last_px,
                trigger_price=trig,
                tick_row=tick_dict,
            )
            strategy_name = "����-��������"
            side_tag = "cage_buy"
            is_buy_order = True
        elif ev_type == "grid_buy_hit":
            buy_px = po.resolve_buy_limit_price(
                code,
                last_price=last_px,
                trigger_price=trig,
                tick_row=tick_dict,
            )
            strategy_name = "����-��������"
            side_tag = "grid_buy"
            is_buy_order = True
        else:
            buy_px = po.resolve_buy_limit_price(
                code,
                last_price=last_px,
                trigger_price=trig,
                tick_row=tick_dict,
            )
            strategy_name = "����-��������"
            side_tag = "single_buy"
            is_buy_order = True

        if is_buy_order:
            if _is_in_buy_block_window():
                _finalize_buy_block_skip(
                    po=po,
                    code=code,
                    tid=tid,
                    ev_type=ev_type,
                    gi_raw=gi_raw,
                    uid=uid,
                    px=buy_px,
                    vol=vol,
                    strategy_name=strategy_name,
                    ev=ev,
                )
                changed = True
                continue
            ok_cash, vol_adj, reason_code, cash_msg = _assess_buy_volume(buy_px, vol)
            if not ok_cash:
                if reason_code == "order_below_min":
                    _finalize_order_below_min_skip(
                        po=po,
                        code=code,
                        tid=tid,
                        ev_type=ev_type,
                        gi_raw=gi_raw,
                        uid=uid,
                        px=buy_px,
                        vol=vol,
                        strategy_name=strategy_name,
                        ev=ev,
                        cash_msg=cash_msg,
                        early_order=False,
                    )
                    changed = True
                    continue
                from datetime import datetime as _dt

                record = {
                    "stock_code": code,
                    "side": "buy",
                    "price": float(buy_px or 0),
                    "volume": int(vol or 0),
                    "status": "skipped",
                    "msg": cash_msg,
                    "strategy_name": strategy_name,
                    "task_id": tid,
                    "event_type": ev_type,
                    "user_order_id": uid,
                    "at": _dt.now().strftime("%Y-%m-%dT%H:%M:%S"),
                    "cash_block": reason_code,
                }
                _attach_event_context_to_order(record, ev)
                if gi_raw is not None and ev_type == "grid_buy_hit":
                    try:
                        record["grid_index"] = int(gi_raw)
                    except (TypeError, ValueError):
                        pass
                po.append_order_record(_RESULTS, record)
                ev["order"] = {
                    "ok": False,
                    "price": record.get("price"),
                    "volume": record.get("volume"),
                    "status": "skipped",
                    "msg": cash_msg,
                }
                print(
                    "[���׺���] order %s skip %s px=%s vol=%s msg=%s"
                    % (side_tag, code, buy_px, vol, cash_msg)
                )
                _unlock_order_task(code, tid, gi_raw, ev_type)
                changed = True
                continue
            vol = int(vol_adj)
            ok, reason, record = po.place_limit_buy(
                ContextInfo,
                code,
                buy_px,
                vol,
                strategy_name=strategy_name,
                user_order_id=uid,
            )
        record["task_id"] = tid
        record["event_type"] = ev_type
        _attach_event_context_to_order(record, ev)
        ep = str(ev.get("executed_endpoint") or "").strip()
        if ep in ("low", "high"):
            record["executed_endpoint"] = ep
        if gi_raw is not None and ev_type in ("grid_buy_hit", "grid_sell_hit"):
            try:
                record["grid_index"] = int(gi_raw)
            except (TypeError, ValueError):
                pass
        po.append_order_record(_RESULTS, record)
        ev["order"] = {
            "ok": bool(ok),
            "price": record.get("price"),
            "volume": record.get("volume"),
            "status": record.get("status"),
            "msg": record.get("msg") or reason,
        }
        print(
            "[���׺���] order %s %s ok=%s px=%s vol=%s msg=%s"
            % (side_tag, code, ok, record.get("price"), record.get("volume"), record.get("msg"))
        )
        if not ok and str(reason or record.get("msg") or "") in (
            "passorder_unbound",
            "no_context",
            "no_account_id",
            "bad_params",
        ):
            _unlock_order_task(code, tid, gi_raw, ev_type)
            try:
                try:
                    import ant_server_chan as sct
                except ImportError:
                    import qmt_builtin.ant_server_chan as sct
                why = str(reason or record.get("msg") or "")
                sct.notify_alert(
                    "��QMT�µ�ͨ���쳣",
                    "ԭ��=%s\n����=%s side=%s\n���� passorder �����ʽ��˺š�"
                    % (why, code, side_tag),
                    alert_key="shadow_order_bind_%s" % datetime.now().strftime("%Y%m%d"),
                    cooldown_sec=3600,
                )
            except Exception:
                pass
        elif ok and tid:
            if ev_type in ("grid_buy_hit", "grid_sell_hit") and gi_raw is not None:
                try:
                    gi = int(gi_raw)
                except (TypeError, ValueError):
                    gi = -1
                all_done = False
                try:
                    # ���ڴ� done��ͬһ task �Ƿ��Ѱ�ȫ����λ������ tid ����
                    st = None
                    if _RUNNER is not None:
                        st = _RUNNER._states.get(code)
                    if st is not None and tid in getattr(st, "done_task_ids", set()):
                        all_done = True
                except Exception:
                    all_done = False
                _mark_grid_point_in_rules_armed(tid, gi, all_done=all_done)
            else:
                _disarm_task_in_rules_armed(tid)
        changed = True
    return changed


def _log_armed_tasks(rules: Optional[dict]) -> None:
    tasks = (rules or {}).get("tasks") or []
    print("[���׺���] armed tasks=%d" % len(tasks))
    for t in tasks:
        print(
            "[���׺���] task %s %s type=%s trig=%s vol=%s enabled=%s"
            % (
                t.get("task_id"),
                t.get("stock_code"),
                t.get("rule_type"),
                t.get("trigger_price"),
                t.get("max_volume"),
                t.get("enabled"),
            )
        )


def init(ContextInfo):
    print("[���׺���] init begin version=%s" % SHADOW_VERSION, flush=True)
    global _RUNNER, _SUB_ID, _RESULTS, _RULES_PATH, _RESULTS_PATH, _RULES_SIG, _TICK_COUNT, _CONTEXT, _SUBSCRIBED_CODES
    _CONTEXT = ContextInfo
    _ensure_tick_runner_module()
    _RULES_PATH, _RESULTS_PATH = default_paths(PROJECT_ROOT)
    print("[���׺���] rules_file_exists=%s" % os.path.isfile(_RULES_PATH), flush=True)
    rules = load_rules_armed(_RULES_PATH)
    _refresh_orders_enabled(rules)
    _RULES_SIG = rules_file_signature(_RULES_PATH)
    _RUNNER = ShadowTickRunner(rules, mode="shadow")
    _log_armed_tasks(rules)
    if os.path.isfile(_RESULTS_PATH):
        try:
            _RESULTS = load_json(_RESULTS_PATH)
        except Exception:
            _RESULTS = empty_results(mode="shadow", trade_date=str(rules.get("trade_date") or ""))
    else:
        _RESULTS = empty_results(mode="shadow", trade_date=str(rules.get("trade_date") or ""))
    if not isinstance(_RESULTS, dict):
        _RESULTS = empty_results(mode="shadow", trade_date=str(rules.get("trade_date") or ""))
    _RESULTS.setdefault("stocks", {})
    _RESULTS.setdefault("orders", [])
    try:
        _RUNNER.hydrate_elastic_states(_RESULTS.get("elastic_states") or {})
        if hasattr(_RUNNER, "hydrate_cage_states"):
            _RUNNER.hydrate_cage_states(_RESULTS.get("cage_states") or {})
        if hasattr(_RUNNER, "hydrate_early_states"):
            _RUNNER.hydrate_early_states(_RESULTS.get("early_states") or {})
    except Exception:
        pass
    try:
        _seed_done_from_results_orders()
    except Exception:
        pass
    _TICK_COUNT = 0

    pool_watch = rules.get("strategy_pool_watch") or []
    subscribe_codes = collect_subscribe_codes(
        rules.get("tasks") or [],
        rules.get("watch_codes"),
        pool_watch,
    )
    _SUBSCRIBED_CODES = list(subscribe_codes)
    _subscribe_codes(ContextInfo, subscribe_codes)
    removed = prune_results_stocks(_RESULTS, subscribe_codes)
    if removed:
        print("[���׺���] pruned results.stocks removed=%d keep=%d" % (removed, len(subscribe_codes)))
        _flush_results_to_disk(force=True)
    print(
        "[���׺���] init trade_date=%s tasks=%d watch=%d pool_watch=%d subscribe=%d orders_enabled=%s"
        % (
            rules.get("trade_date"),
            len(_RUNNER.stock_codes()),
            len(rules.get("watch_codes") or []),
            len(pool_watch),
            len(subscribe_codes),
            _ORDERS_ENABLED,
        )
    )
    print("[���׺���] rules reload=%ss results flush=%ss" % (RULES_RELOAD_INTERVAL_SEC, RESULTS_FLUSH_INTERVAL_SEC))

    try:
        snap = _load_py_module("ant_account_snapshot", "ant_account_snapshot.py")
        if snap is None or not hasattr(snap, "bind_trading_account"):
            print("[���׺���] set_account skip: ant_account_snapshot missing")
        else:
            ok_acc, acc_info = snap.bind_trading_account(ContextInfo)
            if ok_acc:
                print("[���׺���] set_account ok account=%s" % acc_info)
            else:
                print("[���׺���] set_account skip: %s" % acc_info)
    except Exception as e:
        print("[���׺���] set_account error: %s: %s" % (type(e).__name__, e))

    #   1      rules_armed     +   results
    try:
        ContextInfo.run_time(
            "periodic_sync",
            "%dnSecond" % int(RULES_RELOAD_INTERVAL_SEC),
            "2024-01-01 09:00:00",
            "SH",
        )
    except Exception as e:
        print(f"[���׺���] run_time not available: {e}")

    try:
        sector = _get_sector_sync_runner()
        sector.register_startup_sector_timer(ContextInfo)
        sector.register_sector_sync_timer(ContextInfo)
    except Exception as e:
        print("[���׺���] sector_sync timer register failed: %s" % e)
        try:
            register_startup_sector_timer(ContextInfo)
            register_sector_sync_timer(ContextInfo)
        except Exception as e2:
            print("[���׺���] sector_sync fallback failed: %s" % e2)
    daily_sync = _get_daily_sync_runner()
    daily_sync.register_daily_sync_timer(ContextInfo)
    daily_sync.schedule_failed_manifest_recovery_on_init()
    try:
        after_rank = _get_after_hours_rank_runner()
        after_rank.register_after_hours_rank_timer(ContextInfo)
    except Exception as e:
        print("[���׺���] after_hours_rank timer register failed: %s" % e)
    try:
        tick_full = _get_tick_full_sync_runner()
        tick_full.register_tick_full_sync_timer(ContextInfo)
    except Exception as e:
        print("[���׺���] tick_full_sync timer register failed: %s" % e)
    print("[���׺���] init done", flush=True)


def handlebar(ContextInfo):
    """���̣߳�run_time �Ĳ��䣨ʵ����Լÿ 3s ��������"""
    global _CONTEXT
    _CONTEXT = ContextInfo
    try:
        periodic_sync(ContextInfo)
    except Exception as e:
        print("[���׺���] handlebar periodic error: %s" % e)


def periodic_sync(ContextInfo):
    """���߳���ڣ���ֹ�� tick �ص����̨�̵߳��á�"""
    global _CONTEXT, _LAST_PERIODIC_TS
    _CONTEXT = ContextInfo
    now = time.time()
    if now - _LAST_PERIODIC_TS < float(RULES_RELOAD_INTERVAL_SEC) * 0.8:
        return
    _LAST_PERIODIC_TS = now
    _process_pending_resubscribe(ContextInfo)
    reload_rules_if_changed(ContextInfo, allow_resubscribe=True)
    _maybe_seed_snapshots(force=False)
    try:
        night_fin = _finalize_night_market_orders()
        night_ev = _poll_night_market_events()
        night_ord = False
        if night_ev:
            for nev in night_ev:
                print(
                    "[���׺���] %s %s %s trig=%s"
                    % (
                        nev.get("stock_code"),
                        nev.get("type"),
                        nev.get("msg"),
                        nev.get("trigger_price"),
                    )
                )
            night_ord = _handle_order_events(ContextInfo, night_ev, {})
        if night_fin or night_ord:
            _flush_results_to_disk(force=True)
    except Exception as e:
        print("[���׺���] night market poll error: %s" % e)
    _flush_results_to_disk(force=True)
    daily_sync = _get_daily_sync_runner()
    try:
        daily_sync.maybe_run_failed_manifest_recovery(ContextInfo)
    except Exception as e:
        print("[���׺���] failed recovery sync error: %s" % e)
    try:
        if hasattr(daily_sync, "maybe_run_force_year_backfill"):
            daily_sync.maybe_run_force_year_backfill(ContextInfo)
    except Exception as e:
        print("[���׺���] force year backfill error: %s" % e)
    try:
        daily_sync.process_on_demand_sync_requests(ContextInfo)
    except Exception as e:
        print("[���׺���] on_demand sync error: %s" % e)
    try:
        import ant_cancel_request as _cancel_req

        n = _cancel_req.process_pending_cancels(ContextInfo)
        if n:
            _flush_results_to_disk(force=True)
    except Exception as e:
        print("[���׺���] cancel_request error: %s" % e)
    # �ֶ�ָ���� tick ȫ�����ܣ�data/tick_full_sync/manual_request.json��
    try:
        tick_full = _get_tick_full_sync_runner()
        if hasattr(tick_full, "process_manual_request"):
            tick_full.process_manual_request(ContextInfo)
    except Exception as e:
        print("[���׺���] tick_full manual_request error: %s" % e)
    # �ֶ�ָ�����̺��������ܣ�data/after_hours_rank/manual_request.json��
    try:
        after_rank = _get_after_hours_rank_runner()
        if hasattr(after_rank, "process_manual_request"):
            after_rank.process_manual_request(ContextInfo)
    except Exception as e:
        print("[���׺���] after_hours_rank manual_request error: %s" % e)
    # �̺���ˮ�߲��ܣ����� �� tick ���� �� ���ܣ����മ�У��������أ�
    try:
        daily_sync.maybe_catch_up_after_hours_pipeline(ContextInfo)
    except Exception as e:
        print("[���׺���] after_hours pipeline catch-up error: %s" % e)


def _maybe_sync_account_snapshot(ContextInfo) -> None:
    """�����Խ�ģ�ͽ����˻��ʽ�/�ֲ�д�� results.json��"""
    global _RESULTS, _LAST_ACCOUNT_SYNC_TS, _ACCOUNT_SNAPSHOT_IMPORT_ERR, _ACCOUNT_SNAPSHOT_SKIP_REASON
    if _RESULTS is None:
        return
    now = time.time()
    if now - _LAST_ACCOUNT_SYNC_TS < float(ACCOUNT_SYNC_INTERVAL_SEC):
        return
    try:
        snap = _load_py_module("ant_account_snapshot", "ant_account_snapshot.py")
        if snap is None or not hasattr(snap, "sync_account_snapshot_to_results"):
            _LAST_ACCOUNT_SYNC_TS = now
            msg = "ant_account_snapshot missing"
            if msg != _ACCOUNT_SNAPSHOT_IMPORT_ERR:
                _ACCOUNT_SNAPSHOT_IMPORT_ERR = msg
                print("[���׺���] account snapshot import error: %s" % msg)
            return
        sync_fn = snap.sync_account_snapshot_to_results
        _ACCOUNT_SNAPSHOT_IMPORT_ERR = ""
    except Exception as e:
        _LAST_ACCOUNT_SYNC_TS = now
        msg = "%s: %s" % (type(e).__name__, e)
        if msg != _ACCOUNT_SNAPSHOT_IMPORT_ERR:
            _ACCOUNT_SNAPSHOT_IMPORT_ERR = msg
            print("[���׺���] account snapshot import error: %s" % msg)
        return
    try:
        ok, reason = sync_fn(ContextInfo, _RESULTS)
    except Exception as e:
        _LAST_ACCOUNT_SYNC_TS = now
        print("[���׺���] account snapshot error: %s: %s" % (type(e).__name__, e))
        return
    _LAST_ACCOUNT_SYNC_TS = now
    if ok:
        _ACCOUNT_SNAPSHOT_SKIP_REASON = ""
    elif reason != _ACCOUNT_SNAPSHOT_SKIP_REASON:
        _ACCOUNT_SNAPSHOT_SKIP_REASON = reason
        print("[���׺���] account snapshot skip: %s" % reason)


def _process_pending_resubscribe(ContextInfo) -> None:
    global _PENDING_RESUBSCRIBE, _SUBSCRIBED_CODES
    pending = _PENDING_RESUBSCRIBE
    if not pending:
        return
    _PENDING_RESUBSCRIBE = None
    _SUBSCRIBED_CODES = list(pending)
    _subscribe_codes(ContextInfo, pending)
    print("[���׺���] resubscribe done (deferred) codes=%d" % len(pending))


# QMT run_time may resolve the first timer name; keep alias on strategy module.
shadow_sync = periodic_sync


def flush_results(ContextInfo):
    _flush_results_to_disk()


def reload_rules_if_changed(ContextInfo, *, allow_resubscribe: bool = True):
    global _RUNNER, _RULES_SIG, _RESULTS, _SUBSCRIBED_CODES, _PENDING_RESUBSCRIBE
    if _RUNNER is None or not _RULES_PATH:
        return
    runner_reloaded = _ensure_tick_runner_module()
    sig = rules_file_signature(_RULES_PATH)
    if (not runner_reloaded) and sig == _RULES_SIG:
        return
    rules = load_rules_armed(_RULES_PATH)
    _refresh_orders_enabled(rules)
    if runner_reloaded:
        _RUNNER = ShadowTickRunner(rules, mode="shadow")
        try:
            _RUNNER.hydrate_elastic_states((_RESULTS or {}).get("elastic_states") or {})
            if hasattr(_RUNNER, "hydrate_cage_states"):
                _RUNNER.hydrate_cage_states((_RESULTS or {}).get("cage_states") or {})
            if hasattr(_RUNNER, "hydrate_early_states"):
                _RUNNER.hydrate_early_states((_RESULTS or {}).get("early_states") or {})
            _seed_done_from_results_orders()
        except Exception:
            pass
        tasks_changed, codes_changed = True, True
        print("[���׺���] runner recreated after tick_runner hot-load")
    else:
        tasks_changed, codes_changed = _RUNNER.reload_rules(rules)
    _RULES_SIG = sig
    if _RESULTS is not None:
        _RESULTS["trade_date"] = str(rules.get("trade_date") or _RESULTS.get("trade_date") or "")
    subscribe_codes = collect_subscribe_codes(
        rules.get("tasks") or [],
        rules.get("watch_codes"),
        rules.get("strategy_pool_watch"),
    )
    subscribe_changed = set(subscribe_codes) != set(_SUBSCRIBED_CODES)
    n_tasks = len(rules.get("tasks") or [])
    n_pool = len(rules.get("strategy_pool_watch") or [])
    print(
        "[���׺���] rules reload: tasks=%d pool_watch=%d subscribe=%d"
        % (n_tasks, n_pool, len(subscribe_codes))
    )
    _log_armed_tasks(rules)
    if codes_changed or subscribe_changed:
        if allow_resubscribe:
            _SUBSCRIBED_CODES = list(subscribe_codes)
            _subscribe_codes(ContextInfo, subscribe_codes)
            if _RESULTS is not None:
                removed = prune_results_stocks(_RESULTS, subscribe_codes)
                if removed:
                    print("[���׺���] rules reload: pruned stocks removed=%d" % removed)
            print("[���׺���] rules reload: resubscribe done")
        else:
            _PENDING_RESUBSCRIBE = list(subscribe_codes)
            print(
                "[���׺���] rules reload: resubscribe deferred codes=%d"
                % len(subscribe_codes)
            )
    elif tasks_changed:
        print("[���׺���] rules reload: tasks updated (trigger/params)")


def _subscribe_codes(ContextInfo, codes):
    global _SUB_ID, _CONTEXT
    if _SUB_ID:
        try:
            ContextInfo.unsubscribe_quote(_SUB_ID)
            print("[���׺���] unsubscribe ok sub_id=%s" % _SUB_ID)
        except Exception as e:
            print("[���׺���] unsubscribe failed: %s" % e)
        _SUB_ID = None
    if not codes:
        print("[���׺���] subscribe skipped: no codes (unsubscribed)")
        return
    try:
        _CONTEXT = ContextInfo
        _SUB_ID = ContextInfo.subscribe_whole_quote(list(codes), callback=_on_tick)
        print(f"[���׺���] subscribe_whole_quote codes={len(codes)} sub_id={_SUB_ID}")
        # ȫ�ƽ����ػ�����Ҫһ˲�����۽׶��� seed һ����� 9:25 �����ʣ��������������ȴ���
        _maybe_seed_snapshots(force=True)
        try:
            time.sleep(0.35)
        except Exception:
            pass
        _maybe_seed_snapshots(force=True)
    except Exception as e:
        print(f"[���׺���] subscribe failed: {e}")


def _codes_need_seed() -> bool:
    global _RESULTS, _SUBSCRIBED_CODES
    if _RESULTS is None or not _SUBSCRIBED_CODES:
        return False
    stocks = _RESULTS.get("stocks") or {}
    for code in _SUBSCRIBED_CODES:
        bucket = stocks.get(code) or {}
        if float(bucket.get("last_price") or 0) <= 0:
            return True
        if float(bucket.get("today_open") or 0) <= 0:
            return True
    return False


def _maybe_seed_snapshots(force: bool = False) -> None:
    """9:25�C9:30 ��ʱ�� tick �ص�����Ϊ�գ��� get_full_tick �� results.json��"""
    global _LAST_SEED_TS, _SUBSCRIBED_CODES
    import time
    from datetime import datetime

    if not _SUBSCRIBED_CODES:
        return
    now = time.time()
    if not force:
        # ���Ͼ��۴��ڸ��ڿ�� seed������ tick ϡ�٣�
        interval = float(SEED_INTERVAL_SEC)
        try:
            t = datetime.now().time()
            if t.hour == 9 and 15 <= t.minute < 30:
                interval = float(AUCTION_SEED_INTERVAL_SEC)
        except Exception:
            pass
        if now - _LAST_SEED_TS < interval:
            return
        if not _codes_need_seed():
            return
    changed = _seed_snapshots_from_full_tick(_SUBSCRIBED_CODES)
    _LAST_SEED_TS = now
    if changed:
        _flush_results_to_disk()


def _seed_snapshots_from_full_tick(codes: List[str]) -> bool:
    """��ȫ�ƻ���� results.stocks��

    ���� ContextInfo.get_full_tick���� subscribe_whole_quote ͬһ·���棩��
    xtdata.get_full_tick ֻ����·��9:25 ǰ��Ϊ�ա���������ǰ��9:30 ���мۡ�������֮һ��
    """
    global _RESULTS, _CONTEXT
    if _RESULTS is None or not codes:
        return False

    code_list = [str(c).strip().upper() for c in codes if str(c or "").strip()]
    if not code_list:
        return False

    tick_map = None
    source = ""
    ctx = _CONTEXT
    if ctx is not None and hasattr(ctx, "get_full_tick"):
        try:
            tick_map = ctx.get_full_tick(list(code_list))
            if isinstance(tick_map, dict) and tick_map:
                source = "ContextInfo"
        except Exception as e:
            print("[���׺���] seed ContextInfo.get_full_tick failed: %s" % e)
            tick_map = None

    if not isinstance(tick_map, dict) or not tick_map:
        try:
            import xtquant.xtdata as xtdata

            try:
                xtdata.enable_hello = False
            except Exception:
                pass
            tick_map = xtdata.get_full_tick(list(code_list))
            source = "xtdata"
        except Exception as e:
            print("[���׺���] seed xtdata.get_full_tick failed: %s" % e)
            return False

    if not isinstance(tick_map, dict) or not tick_map:
        return False

    changed = False
    seeded = 0
    for stock_code in code_list:
        row = _light_row(tick_map.get(stock_code))
        if not row:
            # �еİ汾 key ��Сд��һ��
            row = _light_row(tick_map.get(stock_code.lower()) or tick_map.get(stock_code.upper()))
        if not row:
            continue
        lp = extract_tick_price(row)
        if lp <= 0:
            continue
        tick_time = ShadowTickRunner._format_tick_time(row.get("time"))
        if update_price_snapshot(_RESULTS, stock_code, lp, tick_time, tick_row=row):
            changed = True
            seeded += 1
    if seeded:
        print(
            "[���׺���] seeded %d/%d codes from full_tick via %s"
            % (seeded, len(code_list), source or "?")
        )
    return changed


def _on_tick(datas):
    global _RUNNER, _RESULTS, _TICK_COUNT
    if _RUNNER is None or _RESULTS is None:
        return
    if not isinstance(datas, dict):
        return

    price_changed = False
    for stock_code, stock_data in datas.items():
        code = str(stock_code or "").strip().upper()
        if not code:
            continue
        row = _light_row(stock_data)
        if not row:
            continue
        lp = extract_tick_price(row)
        if lp <= 0:
            continue
        tick_time = ShadowTickRunner._format_tick_time(row.get("time"))
        if update_price_snapshot(_RESULTS, code, lp, tick_time, tick_row=row):
            price_changed = True

    try:
        events = _RUNNER.on_quote_dict(datas)
    except Exception as e:
        print(f"[���׺���] on_tick error: {e}")
        return

    _TICK_COUNT += 1
    if _TICK_COUNT == 1:
        print("[���׺���] first tick received")

    for ev in events:
        code = str(ev.get("stock_code") or "")
        print(
            f"[���׺���] {code} {ev.get('type')} {ev.get('tick_time')} "
            f"trig={ev.get('trigger_price')} {ev.get('msg')}"
        )
        if ev.get("detail"):
            print(f"[���׺���] detail: {ev.get('detail')}")
        price_changed = True

    order_changed = False
    try:
        order_changed = _handle_order_events(_CONTEXT, events, datas)
    except Exception as e:
        print("[���׺���] order handle error: %s: %s" % (type(e).__name__, e))

    elastic_changed = False
    try:
        dumped = _RUNNER.dump_elastic_states()
        prev = (_RESULTS or {}).get("elastic_states")
        if dumped != prev:
            _RESULTS["elastic_states"] = dumped
            elastic_changed = True
        if hasattr(_RUNNER, "dump_cage_states"):
            cage_dumped = _RUNNER.dump_cage_states()
            prev_cage = (_RESULTS or {}).get("cage_states")
            if cage_dumped != prev_cage:
                _RESULTS["cage_states"] = cage_dumped
                elastic_changed = True
        if hasattr(_RUNNER, "dump_early_states"):
            early_dumped = _RUNNER.dump_early_states()
            prev_early = (_RESULTS or {}).get("early_states")
            if early_dumped != prev_early:
                _RESULTS["early_states"] = early_dumped
                elastic_changed = True
        done_ids = _RUNNER.dump_done_task_ids()
        prev_done = (_RESULTS or {}).get("done_task_ids")
        if done_ids != prev_done:
            _RESULTS["done_task_ids"] = done_ids
            elastic_changed = True
    except Exception:
        pass

    for ev in events:
        code = str(ev.get("stock_code") or "")
        append_stock_event(
            _RESULTS,
            code,
            ev,
            last_tick_time=str(ev.get("tick_time") or ""),
        )

    if price_changed or order_changed or elastic_changed:
        _flush_results_to_disk(force=bool(order_changed or elastic_changed))


def _flush_results_to_disk(force: bool = False):
    global _RESULTS, _RESULTS_PATH, _LAST_FLUSH_TS
    if _RESULTS is None or not _RESULTS_PATH:
        return
    now = time.time()
    if not force and now - _LAST_FLUSH_TS < float(RESULTS_FLUSH_INTERVAL_SEC):
        return
    try:
        save_json_atomic(_RESULTS_PATH, _RESULTS)
        _LAST_FLUSH_TS = now
    except Exception as e:
        print(f"[���׺���] write results failed: {e}")
