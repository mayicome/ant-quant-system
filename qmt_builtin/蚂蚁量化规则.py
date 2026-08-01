#coding:gbk
"""
QMT ????????????????????.py?????? ant_*.py ?????
get_trade_detail_data ????????????????????????/????????????
"""
import importlib.util
import os
import sys
import time

ENTRY_VERSION = "20260801.01"
_shadow = None
_ACCOUNT_SNAPSHOT_MOD = None
_ENTRY_ACCOUNT_SKIP = ""
_LAST_ENTRY_ACCOUNT_SYNC = 0.0
_ENTRY_ACCOUNT_INTERVAL_SEC = 2.0
# ���� download_history_data��ÿ����ֻ bind/log һ�Σ����� handlebar ��·��ˢ����
_DOWNLOAD_HISTORY_BOUND = False
_DOWNLOAD_HISTORY_MISS_LOGGED = False


def _plog(msg):
    """QMT ���� Python ���� TTY��print ȫ���壻������־���� flush �������̿�����"""
    try:
        print(msg, flush=True)
    except Exception:
        try:
            print(msg)
        except Exception:
            pass


_plog("[�������] module load ENTRY_VERSION=%s" % ENTRY_VERSION)


def _qmt_python_dir():
    """QMT ?? <string> ?????????? __file__???? ant_qmt_paths ?? sys.path ???? python ????"""
    try:
        from ant_qmt_paths import QMT_BUILTIN_DIR

        d = str(QMT_BUILTIN_DIR or "").strip()
        if d and os.path.isdir(d):
            return d
    except Exception:
        pass
    for base in sys.path:
        base = str(base)
        if os.path.isfile(os.path.join(base, "ant_qmt_paths.py")):
            return base
        if os.path.isfile(os.path.join(base, "ant_shadow_strategy.py")):
            return base
    return ""


def _shadow_py_path():
    root = _qmt_python_dir()
    if root:
        cand = os.path.join(root, "ant_shadow_strategy.py")
        if os.path.isfile(cand):
            return cand
    for base in sys.path:
        cand = os.path.join(str(base), "ant_shadow_strategy.py")
        if os.path.isfile(cand):
            return cand
    return ""


def _load_shadow():
    global _shadow
    path = _shadow_py_path()
    if not path:
        _plog("[�������] FATAL: ant_shadow_strategy.py not found")
        _shadow = None
        return None
    for key in list(sys.modules.keys()):
        if key == "ant_shadow_strategy" or key.startswith("ant_shadow_"):
            sys.modules.pop(key, None)
    mod_name = "ant_shadow_%d" % int(os.path.getmtime(path))
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        _plog("[�������] FATAL: cannot load " + path)
        _shadow = None
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    _shadow = mod
    ver = getattr(mod, "SHADOW_VERSION", "?")
    _plog("[�������] shadow loaded version=%s file=%s" % (ver, path))
    return mod


_load_shadow()


def _ensure_qmt_sys_path():
    root = _qmt_python_dir()
    if root and root not in sys.path:
        sys.path.insert(0, root)


def _load_account_snapshot_mod():
    global _ACCOUNT_SNAPSHOT_MOD
    _ensure_qmt_sys_path()
    root = _qmt_python_dir()
    if not root:
        return None
    path = os.path.join(root, "ant_account_snapshot.py")
    if not os.path.isfile(path):
        return None
    mtime = int(os.path.getmtime(path))
    cached = _ACCOUNT_SNAPSHOT_MOD
    if cached is not None:
        cached_mtime = getattr(cached, "_ANT_SNAPSHOT_MTIME", 0)
        if cached_mtime == mtime and hasattr(cached, "resolve_account_id"):
            return cached
    for key in list(sys.modules.keys()):
        if key == "ant_account_snapshot" or key.startswith("ant_account_snapshot_"):
            sys.modules.pop(key, None)
    mod_name = "ant_account_snapshot_%d" % mtime
    try:
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        mod._ANT_SNAPSHOT_MTIME = mtime
    except Exception:
        return None
    if not hasattr(mod, "resolve_account_id"):
        return None
    _ACCOUNT_SNAPSHOT_MOD = mod
    return mod


def _entry_fetch_trade_detail(account_id, data_type, strategy_names=None, account_type_hint=""):
    """Query get_trade_detail_data. Never pass empty strategyName for ORDER/DEAL."""
    try:
        gtd = get_trade_detail_data
    except NameError:
        return None
    if not callable(gtd):
        return None
    dtypes = []
    for val in (data_type, str(data_type).upper(), str(data_type).lower()):
        if val and val not in dtypes:
            dtypes.append(val)
    # ���ְ汾�ֲ� dtype ����
    if str(data_type).lower() in ("position", "positions", "pos"):
        for extra in ("POSITION", "position", "Position"):
            if extra not in dtypes:
                dtypes.append(extra)
    strategies = []
    if strategy_names:
        for s in strategy_names:
            s = str(s or "").strip()
            if s and s not in strategies:
                strategies.append(s)
    account_ids = []
    for val in (account_id, str(account_id).strip()):
        if val is None:
            continue
        s = str(val).strip()
        if s and s not in account_ids:
            account_ids.append(s)
        try:
            if s.isdigit():
                iv = int(s)
                if iv not in account_ids:
                    account_ids.append(iv)
        except Exception:
            pass
    account_types = []
    hint = str(account_type_hint or "").strip()
    if hint:
        account_types.append(hint)
        account_types.append(hint.upper())
        account_types.append(hint.lower())
    for t in ("STOCK", "stock", "Stock", "CREDIT", "credit", "Credit"):
        if t not in account_types:
            account_types.append(t)
    # 3-arg = all strategies; never use strategyName=""
    arg_lists = []
    for aid in account_ids:
        for account_type in account_types:
            for dtype in dtypes:
                arg_lists.append((aid, account_type, dtype))
                for sn in strategies:
                    arg_lists.append((aid, account_type, dtype, sn))
    best = None
    best_n = -1
    try_log = []
    for args in arg_lists:
        try:
            raw = gtd(*args)
        except TypeError:
            continue
        except Exception as e:
            if len(try_log) < 8:
                try_log.append("%s->err:%s" % (args[:3], e))
            continue
        if raw is None:
            if len(try_log) < 8:
                try_log.append("%s->None" % (args[:3],))
            continue
        try:
            n = len(raw)
        except Exception:
            try:
                n = sum(1 for _ in raw)
            except Exception:
                n = 1 if raw else 0
        if len(try_log) < 12:
            try_log.append("%s->len=%s type=%s" % (args[:3], n, type(raw).__name__))
        if n > best_n:
            best = raw
            best_n = n
        if n > 0 and len(args) == 3:
            try:
                setattr(gtd, "_ant_last_pos_try", try_log)
            except Exception:
                pass
            return raw
    try:
        setattr(gtd, "_ant_last_pos_try", try_log)
    except Exception:
        pass
    return best


def _entry_sync_account_snapshot(ContextInfo):
    """?? QMT ???/????????????????? get_trade_detail_data??"""
    global _ENTRY_ACCOUNT_SKIP, _LAST_ENTRY_ACCOUNT_SYNC
    try:
        now = time.time()
        if now - _LAST_ENTRY_ACCOUNT_SYNC < float(_ENTRY_ACCOUNT_INTERVAL_SEC):
            return
        if _shadow is None:
            return
        results = _shadow.peek_results()
        if not isinstance(results, dict):
            return
        snap = _load_account_snapshot_mod()
        if snap is None:
            if _ENTRY_ACCOUNT_SKIP != "snapshot_mod_missing":
                _ENTRY_ACCOUNT_SKIP = "snapshot_mod_missing"
                print("[���׺���] account snapshot skip: ant_account_snapshot missing")
            _LAST_ENTRY_ACCOUNT_SYNC = now
            return
        aid = snap.resolve_account_id(ContextInfo)
        if not aid:
            if _ENTRY_ACCOUNT_SKIP != "no_account_id":
                _ENTRY_ACCOUNT_SKIP = "no_account_id"
                print("[���׺���] account snapshot skip: no_account_id")
            _LAST_ENTRY_ACCOUNT_SYNC = now
            return
        try:
            get_trade_detail_data
        except NameError:
            if _ENTRY_ACCOUNT_SKIP != "no_gtd":
                _ENTRY_ACCOUNT_SKIP = "no_gtd"
                print("[���׺���] account snapshot skip: get_trade_detail_data not in entry scope")
            _LAST_ENTRY_ACCOUNT_SYNC = now
            return

        # ÿ�β�ѯǰ���°��˺� + account_type������ֲֲ�ѯ���б�
        try:
            snap.bind_trading_account(ContextInfo, aid)
        except Exception:
            pass
        acct_type_hint = str(getattr(ContextInfo, "account_type", "") or "").strip()

        acc_raw = _entry_fetch_trade_detail(aid, "account", account_type_hint=acct_type_hint)
        pos_raw = _entry_fetch_trade_detail(aid, "position", account_type_hint=acct_type_hint)
        pos_try_log = []
        try:
            pos_try_log = list(getattr(get_trade_detail_data, "_ant_last_pos_try", []) or [])[:12]
        except Exception:
            pos_try_log = []
        # ORDER/DEAL: do NOT query with strategyName="" (filters everything out)
        order_raw = _entry_fetch_trade_detail(
            aid,
            "order",
            strategy_names=("\u8682\u8681\u002d\u5355\u70b9\u4e70\u5165", "\u8682\u8681\u002d\u5355\u70b9\u5356\u51fa", "\u8682\u8681\u002d\u7a81\u7834\u4e70\u5165", "\u8682\u8681\u002d\u7a81\u7834\u5356\u51fa", "\u8682\u8681\u002d\u5f39\u6027\u5356\u51fa", "\u8682\u8681\u002d\u5f39\u6027\u4e70\u5165", "\u8682\u8681\u002d\u7b3c\u5b50\u4e70\u5165", "\u8682\u8681\u002d\u7b3c\u5b50\u5356\u51fa", "\u8682\u8681\u002d\u7f51\u683c\u4e70\u5165", "\u8682\u8681\u002d\u7f51\u683c\u5356\u51fa", "\u8682\u8681\u002d\u5b9a\u65f6\u6e05\u4ed3", "\u8682\u8681\u002d\u5185\u7f6e\u4e0b\u5355"),
            account_type_hint=acct_type_hint,
        )
        deal_raw = _entry_fetch_trade_detail(
            aid,
            "deal",
            strategy_names=("\u8682\u8681\u002d\u5355\u70b9\u4e70\u5165", "\u8682\u8681\u002d\u5355\u70b9\u5356\u51fa", "\u8682\u8681\u002d\u7a81\u7834\u4e70\u5165", "\u8682\u8681\u002d\u7a81\u7834\u5356\u51fa", "\u8682\u8681\u002d\u5f39\u6027\u5356\u51fa", "\u8682\u8681\u002d\u5f39\u6027\u4e70\u5165", "\u8682\u8681\u002d\u7b3c\u5b50\u4e70\u5165", "\u8682\u8681\u002d\u7b3c\u5b50\u5356\u51fa", "\u8682\u8681\u002d\u7f51\u683c\u4e70\u5165", "\u8682\u8681\u002d\u7f51\u683c\u5356\u51fa", "\u8682\u8681\u002d\u5b9a\u65f6\u6e05\u4ed3", "\u8682\u8681\u002d\u5185\u7f6e\u4e0b\u5355"),
            account_type_hint=acct_type_hint,
        )
        ok, reason = snap.apply_trade_detail_raw(
            ContextInfo,
            results,
            acc_raw,
            pos_raw,
            aid,
            order_raw=order_raw,
            deal_raw=deal_raw,
        )
        # ���ӳֲֲ�ѯ��̽��־�����ڶ��ա��˻�����ֵ���ֲֿա�
        try:
            pq = results.get("position_query")
            if isinstance(pq, dict):
                pq["account_type"] = acct_type_hint or getattr(ContextInfo, "account_type", "")
                pq["do_back_test"] = getattr(ContextInfo, "do_back_test", None)
                pq["try_log"] = pos_try_log
        except Exception:
            pass
        _LAST_ENTRY_ACCOUNT_SYNC = now
        if ok:
            _ENTRY_ACCOUNT_SKIP = ""
            try:
                if _shadow is not None and hasattr(_shadow, "flush_results"):
                    _shadow.flush_results(ContextInfo)
            except Exception:
                pass
        elif reason != _ENTRY_ACCOUNT_SKIP:
            _ENTRY_ACCOUNT_SKIP = reason
            print("[���׺���] account snapshot skip: %s" % reason)
    except Exception as e:
        _LAST_ENTRY_ACCOUNT_SYNC = time.time()
        msg = "%s: %s" % (type(e).__name__, e)
        if msg != _ENTRY_ACCOUNT_SKIP:
            _ENTRY_ACCOUNT_SKIP = msg
            print("[���׺���] account snapshot error: %s" % msg)


def _reload_daily_sync_runner():
    import importlib

    try:
        import ant_daily_sync_runner as runner
    except ImportError:
        import qmt_builtin.ant_daily_sync_runner as runner
    runner = importlib.reload(runner)
    print(
        "[����ͬ��] timer entry version=%s"
        % getattr(runner, "DAILY_SYNC_VERSION", "?")
    )
    return runner


def _ensure_passorder_bound():
    """Bind passorder from entry globals; reuse module by mtime; mirror to builtins."""
    try:
        root = _qmt_python_dir()
        path = os.path.join(root, "ant_passorder.py") if root else ""
        if not (path and os.path.isfile(path)):
            print("[�������] ant_passorder.py missing")
            return False
        mod_name = "ant_passorder_%d" % int(os.path.getmtime(path))
        po = sys.modules.get(mod_name)
        if po is None:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                return False
            po = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = po
            spec.loader.exec_module(po)
        if hasattr(po, "bind_runtime_globals"):
            return bool(po.bind_runtime_globals(globals()))
        return False
    except Exception as e:
        print("[�������] bind passorder error: %s: %s" % (type(e).__name__, e))
        return False


def _ensure_download_history_bound():
    """Bind ���� download_history_data���� QMT ģ�ͽ���ȫ�ֺ������� xtdata����

    �ɹ����� _DOWNLOAD_HISTORY_BOUND������ handlebar/periodic ֱ������������ˢ����
    miss ֻ��һ����־��������������Ĭ���ԣ�globals ����������֡��������
    """
    global _DOWNLOAD_HISTORY_BOUND, _DOWNLOAD_HISTORY_MISS_LOGGED
    if _DOWNLOAD_HISTORY_BOUND:
        return True
    try:
        root = _qmt_python_dir()
        path = os.path.join(root, "ant_tick_cache_io.py") if root else ""
        if not (path and os.path.isfile(path)):
            return False
        mod_name = "ant_tick_cache_io_%d" % int(os.path.getmtime(path))
        mod = sys.modules.get(mod_name)
        if mod is None:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                return False
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
        fn = getattr(mod, "bind_download_history_data", None)
        if callable(fn):
            ok = bool(fn(globals()))
            if ok:
                _DOWNLOAD_HISTORY_BOUND = True
                _plog("[�������] bind download_history_data ok")
            elif not _DOWNLOAD_HISTORY_MISS_LOGGED:
                _DOWNLOAD_HISTORY_MISS_LOGGED = True
                _plog("[�������] bind download_history_data miss (not in strategy globals)")
            return ok
        return False
    except Exception as e:
        if not _DOWNLOAD_HISTORY_MISS_LOGGED:
            _DOWNLOAD_HISTORY_MISS_LOGGED = True
            _plog(
                "[�������] bind download_history_data error: %s: %s"
                % (type(e).__name__, e)
            )
        return False


def init(ContextInfo):
    _plog("[�������] init begin entry version=%s" % ENTRY_VERSION)
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    _ensure_qmt_sys_path()
    _ensure_passorder_bound()
    _ensure_download_history_bound()
    shadow = _load_shadow()
    if shadow is None:
        _plog("[�������] init aborted: shadow is None")
        return
    ret = shadow.init(ContextInfo)
    _plog("[�������] init returned")
    return ret


def handlebar(ContextInfo):
    _ensure_passorder_bound()
    _ensure_download_history_bound()
    if _shadow is None:
        return
    _entry_sync_account_snapshot(ContextInfo)
    return _shadow.handlebar(ContextInfo)


def periodic_sync(ContextInfo):
    _ensure_passorder_bound()
    _ensure_download_history_bound()
    _entry_sync_account_snapshot(ContextInfo)
    if _shadow is None:
        return
    return _shadow.periodic_sync(ContextInfo)


def shadow_sync(ContextInfo):
    return periodic_sync(ContextInfo)


def flush_results(ContextInfo):
    if _shadow is None:
        return
    return _shadow.flush_results(ContextInfo)


def daily_bar_sync(ContextInfo):
    runner = _reload_daily_sync_runner()
    return runner.daily_bar_sync(ContextInfo)


def startup_catch_up(ContextInfo):
    runner = _reload_daily_sync_runner()
    return runner.startup_catch_up(ContextInfo)


def _reload_after_hours_rank_runner():
    import importlib

    try:
        import ant_after_hours_rank_runner as runner
    except ImportError:
        import qmt_builtin.ant_after_hours_rank_runner as runner
    runner = importlib.reload(runner)
    print(
        "[�̺�����] ��ʱ��� �汾=%s"
        % getattr(runner, "AFTER_HOURS_RANK_VERSION", "?")
    )
    return runner


def after_hours_volume_rank(ContextInfo):
    runner = _reload_after_hours_rank_runner()
    return runner.after_hours_volume_rank(ContextInfo)


def _reload_tick_full_sync_runner():
    import importlib

    try:
        import ant_tick_full_sync_runner as runner
    except ImportError:
        import qmt_builtin.ant_tick_full_sync_runner as runner
    runner = importlib.reload(runner)
    print(
        "[�ֱ�ͬ��] ��ʱ��� �汾=%s"
        % getattr(runner, "TICK_FULL_SYNC_VERSION", "?")
    )
    return runner


def tick_full_sync(ContextInfo):
    runner = _reload_tick_full_sync_runner()
    return runner.tick_full_sync(ContextInfo)


def tick_probe(ContextInfo):
    """һ����̽�⣺���� download_history_data + ContextInfo tick ���壨Ĭ�� 20260730����"""
    _ensure_download_history_bound()
    runner = _reload_tick_full_sync_runner()
    fn = getattr(runner, "tick_probe", None)
    if not callable(fn):
        _plog("[�������] tick_probe missing on runner")
        return None
    return fn(ContextInfo, day="20260730")


def sector_data_sync(ContextInfo):
    import importlib

    try:
        import ant_sector_sync_runner as runner
    except ImportError:
        try:
            import qmt_builtin.ant_sector_sync_runner as runner
        except ImportError:
            print("[�������] sector_data_sync: ant_sector_sync_runner not found")
            return
    runner = importlib.reload(runner)
    print(
        "[���ͬ��] timer entry version=%s"
        % getattr(runner, "SECTOR_SYNC_VERSION", "?")
    )
    return runner.sector_data_sync(ContextInfo)


def _dispatch_account_snapshot_callback(callback_name, ContextInfo, payload):
    try:
        snap = _load_account_snapshot_mod()
        if snap is None:
            return
        fn = getattr(snap, callback_name, None)
        if callable(fn):
            fn(ContextInfo, payload)
    except Exception as e:
        print("[�������] %s error: %s" % (callback_name, e))


def account_callback(ContextInfo, accountInfo):
    _dispatch_account_snapshot_callback("on_account_callback", ContextInfo, accountInfo)


def position_callback(ContextInfo, positionInfo):
    _dispatch_account_snapshot_callback("on_position_callback", ContextInfo, positionInfo)


def order_callback(ContextInfo, orderInfo):
    """?? QMT ?????????????????????? results??"""
    try:
        snap = _load_account_snapshot_mod()
        if snap is None:
            return
        if _shadow is None:
            return
        results = _shadow.peek_results()
        if not isinstance(results, dict):
            return
        aid = ""
        try:
            aid = snap.resolve_account_id(ContextInfo)
        except Exception:
            pass
        if hasattr(snap, "apply_order_callback_to_results"):
            changed = snap.apply_order_callback_to_results(results, orderInfo, aid)
        else:
            snap.on_order_callback(ContextInfo, orderInfo)
            changed = False
        if changed:
            try:
                _shadow.flush_results(ContextInfo)
            except Exception:
                pass
            st = ""
            try:
                st = str(getattr(orderInfo, "m_nOrderStatus", "") or "")
            except Exception:
                pass
            print("[���׺���] order_callback status=%s" % st)
    except Exception as e:
        print("[�������] order_callback error: %s: %s" % (type(e).__name__, e))



def deal_callback(ContextInfo, dealInfo):
    """?? QMT ??????????????????"""
    try:
        snap = _load_account_snapshot_mod()
        if snap is None or _shadow is None:
            return
        results = _shadow.peek_results()
        if not isinstance(results, dict):
            return
        aid = ""
        try:
            aid = snap.resolve_account_id(ContextInfo)
        except Exception:
            pass
        changed = False
        if hasattr(snap, "apply_deal_callback_to_results"):
            changed = snap.apply_deal_callback_to_results(results, dealInfo, aid)
        if changed:
            try:
                _shadow.flush_results(ContextInfo)
            except Exception:
                pass
            print("[���׺���] deal_callback")
    except Exception as e:
        print("[�������] deal_callback error: %s: %s" % (type(e).__name__, e))


def startup_sector_sync(ContextInfo):
    import importlib

    try:
        import ant_sector_sync_runner as runner
    except ImportError:
        try:
            import qmt_builtin.ant_sector_sync_runner as runner
        except ImportError:
            print("[�������] startup_sector_sync: ant_sector_sync_runner not found")
            return
    runner = importlib.reload(runner)
    print(
        "[���ͬ��] startup entry version=%s"
        % getattr(runner, "SECTOR_SYNC_VERSION", "?")
    )
    return runner.startup_sector_sync(ContextInfo)
