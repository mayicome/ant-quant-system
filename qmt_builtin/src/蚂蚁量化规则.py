#coding:gbk
"""
QMT ????????????????????.py?????? ant_*.py ?????
get_trade_detail_data ????????????????????????/????????????
"""
import importlib.util
import os
import sys
import time

ENTRY_VERSION = "20260811.04"
_shadow = None
_ACCOUNT_SNAPSHOT_MOD = None
_ENTRY_ACCOUNT_SKIP = ""
_LAST_ENTRY_ACCOUNT_SYNC = 0.0
_ENTRY_ACCOUNT_INTERVAL_SEC = 3.0
# 委托/成交查询较重，过勤会堵同一线程上的 tick 回调与 full_tick 补种
_LAST_ENTRY_ORDER_DEAL_SYNC = 0.0
_ENTRY_ORDER_DEAL_INTERVAL_SEC = 12.0
# 内置 download_history_data：每进程只 bind/log 一次（勿在 handlebar 热路径刷屏）
_DOWNLOAD_HISTORY_BOUND = False
_DOWNLOAD_HISTORY_MISS_LOGGED = False
_BJ_SECTOR_PROBE_DONE = False


def _plog(msg):
    """QMT 内置 Python 常非 TTY，print 全缓冲；启动日志必须 flush 才能立刻看见。"""
    try:
        print(msg, flush=True)
    except Exception:
        try:
            print(msg)
        except Exception:
            pass


_plog("[入口] 模块已加载 版本=%s" % ENTRY_VERSION)


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
        _plog("[入口] 致命: 未找到 ant_shadow_strategy.py")
        _shadow = None
        return None
    try:
        mtime = int(os.path.getmtime(path))
    except OSError:
        mtime = 0
    # 同文件未改则复用，避免 init 再整包 reload 刷双份启动日志
    if _shadow is not None and getattr(_shadow, "_ANT_SHADOW_MTIME", None) == mtime:
        return _shadow
    for key in list(sys.modules.keys()):
        if key == "ant_shadow_strategy" or key.startswith("ant_shadow_"):
            sys.modules.pop(key, None)
    mod_name = "ant_shadow_%d" % mtime
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        _plog("[入口] 致命: 无法加载 " + path)
        _shadow = None
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    mod._ANT_SHADOW_MTIME = mtime
    _shadow = mod
    ver = getattr(mod, "SHADOW_VERSION", "?")
    _plog("[入口] 交易核心已加载 版本=%s" % ver)
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
    # 部分版本持仓 dtype 别名
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


def _probe_bj_sectors_once(ContextInfo):
    """一次性探测本机 QMT 北交所板块名是否可用，结果写入 data/bj_sector_probe.json。"""
    global _BJ_SECTOR_PROBE_DONE
    if _BJ_SECTOR_PROBE_DONE:
        return
    _BJ_SECTOR_PROBE_DONE = True
    try:
        import json
        from datetime import datetime

        try:
            from ant_qmt_paths import DATA_DIR
        except Exception:
            try:
                from qmt_builtin.ant_qmt_paths import DATA_DIR
            except Exception:
                DATA_DIR = os.path.join(_qmt_python_dir(), "data")

        owners = []
        if ContextInfo is not None:
            owners.append(("ctx", ContextInfo))
        try:
            import builtins

            owners.append(("builtins", builtins))
        except Exception:
            pass

        sector_candidates = (
            "\u4eac\u5e02A\u80a1",  # 京市A股
            "\u6caa\u6df1\u4eacA\u80a1",  # 沪深京A股
            "\u5317\u4ea4\u6240",  # 北交所
            "\u5317\u4ea4\u6240A\u80a1",  # 北交所A股
            "BJ",
            "\u4eacA\u80a1",  # 京A股
            "\u6caa\u6df1A\u80a1",  # 沪深A股（对照）
        )
        sector_counts = {}
        samples = {}
        for sec in sector_candidates:
            best_n = -1
            best_sample = []
            src = ""
            for label, owner in owners:
                fn = getattr(owner, "get_stock_list_in_sector", None)
                if not callable(fn):
                    continue
                try:
                    raw = fn(sec) or []
                except Exception:
                    continue
                try:
                    n = len(raw)
                except Exception:
                    n = 0
                if n > best_n:
                    best_n = n
                    src = label
                    try:
                        best_sample = [str(x) for x in list(raw)[:8]]
                    except Exception:
                        best_sample = []
            sector_counts[sec] = {"n": max(0, best_n), "source": src}
            samples[sec] = best_sample

        matched_names = []
        for label, owner in owners:
            fn = getattr(owner, "get_sector_list", None)
            if not callable(fn):
                continue
            try:
                sl = fn() or []
            except Exception:
                continue
            for s in sl:
                t = str(s)
                if any(k in t for k in ("\u4eac", "\u5317\u4ea4", "BJ", "bj")):
                    if t not in matched_names:
                        matched_names.append(t)
            if matched_names:
                break

        payload = {
            "probed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "entry_version": ENTRY_VERSION,
            "matched_sector_names": matched_names,
            "sector_counts": sector_counts,
            "samples": samples,
        }
        out_path = os.path.join(str(DATA_DIR), "bj_sector_probe.json")
        tmp = out_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, out_path)
        jing = sector_counts.get("\u4eac\u5e02A\u80a1", {}).get("n", 0)
        hsj = sector_counts.get("\u6caa\u6df1\u4eacA\u80a1", {}).get("n", 0)
        print(
            "[交易核心] 北交所板块探测 京市A股=%s 沪深京A股=%s matched=%s -> %s"
            % (jing, hsj, len(matched_names), out_path),
            flush=True,
        )
    except Exception as e:
        print("[交易核心] 北交所板块探测失败: %s" % e, flush=True)


def _entry_sync_account_snapshot(ContextInfo):
    """大 QMT 账户/持仓/委托写入 results（调用 get_trade_detail_data）。"""
    global _ENTRY_ACCOUNT_SKIP, _LAST_ENTRY_ACCOUNT_SYNC, _LAST_ENTRY_ORDER_DEAL_SYNC
    try:
        _probe_bj_sectors_once(ContextInfo)
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
                print("[交易核心] 账户快照跳过: 缺少 ant_account_snapshot")
            _LAST_ENTRY_ACCOUNT_SYNC = now
            return
        aid = snap.resolve_account_id(ContextInfo)
        if not aid:
            if _ENTRY_ACCOUNT_SKIP != "no_account_id":
                _ENTRY_ACCOUNT_SKIP = "no_account_id"
                print("[交易核心] 账户快照跳过: no_account_id")
            _LAST_ENTRY_ACCOUNT_SYNC = now
            return
        try:
            get_trade_detail_data
        except NameError:
            if _ENTRY_ACCOUNT_SKIP != "no_gtd":
                _ENTRY_ACCOUNT_SKIP = "no_gtd"
                print("[交易核心] 账户快照跳过: 入口作用域无 get_trade_detail_data")
            _LAST_ENTRY_ACCOUNT_SYNC = now
            return

        # 每次查询前重新绑定账号 + account_type，避免持仓查询空列表
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
        # 降频：None 时 apply 侧沿用缓存，避免每轮多路 GTD 堵行情
        order_raw = None
        deal_raw = None
        if now - _LAST_ENTRY_ORDER_DEAL_SYNC >= float(_ENTRY_ORDER_DEAL_INTERVAL_SEC):
            _strat_names = (
                "\u8682\u8681\u002d\u5355\u70b9\u4e70\u5165",
                "\u8682\u8681\u002d\u5355\u70b9\u5356\u51fa",
                "\u8682\u8681\u002d\u7a81\u7834\u4e70\u5165",
                "\u8682\u8681\u002d\u7a81\u7834\u5356\u51fa",
                "\u8682\u8681\u002d\u5f39\u6027\u5356\u51fa",
                "\u8682\u8681\u002d\u5f39\u6027\u4e70\u5165",
                "\u8682\u8681\u002d\u7b3c\u5b50\u4e70\u5165",
                "\u8682\u8681\u002d\u7b3c\u5b50\u5356\u51fa",
                "\u8682\u8681\u002d\u7f51\u683c\u4e70\u5165",
                "\u8682\u8681\u002d\u7f51\u683c\u5356\u51fa",
                "\u8682\u8681\u002d\u5b9a\u65f6\u6e05\u4ed3",
                "\u8682\u8681\u002d\u5185\u7f6e\u4e0b\u5355",
            )
            order_raw = _entry_fetch_trade_detail(
                aid,
                "order",
                strategy_names=_strat_names,
                account_type_hint=acct_type_hint,
            )
            deal_raw = _entry_fetch_trade_detail(
                aid,
                "deal",
                strategy_names=_strat_names,
                account_type_hint=acct_type_hint,
            )
            _LAST_ENTRY_ORDER_DEAL_SYNC = now
        ok, reason = snap.apply_trade_detail_raw(
            ContextInfo,
            results,
            acc_raw,
            pos_raw,
            aid,
            order_raw=order_raw,
            deal_raw=deal_raw,
        )
        # 附加持仓查询试探日志，便于对照「账户有市值但持仓空」
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
            print("[交易核心] 账户快照跳过: %s" % reason)
    except Exception as e:
        _LAST_ENTRY_ACCOUNT_SYNC = time.time()
        msg = "%s: %s" % (type(e).__name__, e)
        if msg != _ENTRY_ACCOUNT_SKIP:
            _ENTRY_ACCOUNT_SKIP = msg
            print("[交易核心] 账户快照错误: %s" % msg)


def _reload_daily_sync_runner():
    import importlib

    try:
        import ant_daily_sync_runner as runner
    except ImportError:
        import qmt_builtin.ant_daily_sync_runner as runner
    runner = importlib.reload(runner)
    print(
        "[日线同步] 定时入口 版本=%s"
        % getattr(runner, "DAILY_SYNC_VERSION", "?")
    )
    return runner


def _ensure_passorder_bound():
    """Bind passorder from entry globals; reuse module by mtime; mirror to builtins."""
    try:
        root = _qmt_python_dir()
        path = os.path.join(root, "ant_passorder.py") if root else ""
        if not (path and os.path.isfile(path)):
            print("[入口] 缺少 ant_passorder.py")
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
        print("[入口] 绑定 passorder 错误: %s: %s" % (type(e).__name__, e))
        return False


def _ensure_download_history_bound():
    """Bind 内置 download_history_data（大 QMT 模型交易全局函数，非 xtdata）。

    成功后设 _DOWNLOAD_HISTORY_BOUND，后续 handlebar/periodic 直接跳过，避免刷屏。
    miss 只打一次日志，仍允许后续静默重试（globals 可能晚于首帧就绪）。
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
            elif not _DOWNLOAD_HISTORY_MISS_LOGGED:
                _DOWNLOAD_HISTORY_MISS_LOGGED = True
                _plog("[入口] 绑定 download_history_data 未命中（策略 globals 中无此函数）")
            return ok
        return False
    except Exception as e:
        if not _DOWNLOAD_HISTORY_MISS_LOGGED:
            _DOWNLOAD_HISTORY_MISS_LOGGED = True
            _plog(
                "[入口] 绑定 download_history_data 错误: %s: %s"
                % (type(e).__name__, e)
            )
        return False


def init(ContextInfo):
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    _ensure_qmt_sys_path()
    _ensure_passorder_bound()
    _ensure_download_history_bound()
    shadow = _load_shadow()
    if shadow is None:
        _plog("[入口] 初始化中止: 交易核心为 None")
        return
    return shadow.init(ContextInfo)


def handlebar(ContextInfo):
    try:
        _ensure_passorder_bound()
        _ensure_download_history_bound()
        if _shadow is None:
            return
        # 先跑交易核心（含 full_tick 补种），再查账户，避免 GTD 堵行情墙钟
        out = _shadow.handlebar(ContextInfo)
        _entry_sync_account_snapshot(ContextInfo)
        return out
    except KeyboardInterrupt:
        # 模型交易手动停止；吞掉以免深栈刷屏
        return


def periodic_sync(ContextInfo):
    try:
        _ensure_passorder_bound()
        _ensure_download_history_bound()
        if _shadow is None:
            return
        out = _shadow.periodic_sync(ContextInfo)
        _entry_sync_account_snapshot(ContextInfo)
        return out
    except KeyboardInterrupt:
        return


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
        "[盘后排名] 定时入口 版本=%s"
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
        "[分笔同步] 定时入口 版本=%s"
        % getattr(runner, "TICK_FULL_SYNC_VERSION", "?")
    )
    return runner


def tick_full_sync(ContextInfo):
    runner = _reload_tick_full_sync_runner()
    return runner.tick_full_sync(ContextInfo)


def tick_probe(ContextInfo):
    """一次性探测：内置 download_history_data + ContextInfo tick 变体（默认 20260730）。"""
    _ensure_download_history_bound()
    runner = _reload_tick_full_sync_runner()
    fn = getattr(runner, "tick_probe", None)
    if not callable(fn):
        _plog("[入口] runner 上缺少 tick_probe")
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
            print("[入口] sector_data_sync: 未找到 ant_sector_sync_runner")
            return
    runner = importlib.reload(runner)
    print(
        "[板块同步] 定时入口 版本=%s"
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
        print("[入口] %s 错误: %s" % (callback_name, e))


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
            print("[交易核心] 委托回调 status=%s" % st)
    except Exception as e:
        print("[入口] order_callback 错误: %s: %s" % (type(e).__name__, e))



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
            print("[交易核心] 成交回调")
    except Exception as e:
        print("[入口] deal_callback 错误: %s: %s" % (type(e).__name__, e))


def startup_sector_sync(ContextInfo):
    import importlib

    try:
        import ant_sector_sync_runner as runner
    except ImportError:
        try:
            import qmt_builtin.ant_sector_sync_runner as runner
        except ImportError:
            print("[入口] startup_sector_sync: 未找到 ant_sector_sync_runner")
            return
    runner = importlib.reload(runner)
    print(
        "[板块同步] 启动入口 版本=%s"
        % getattr(runner, "SECTOR_SYNC_VERSION", "?")
    )
    return runner.startup_sector_sync(ContextInfo)
