# -*- coding: utf-8 -*-
"""
QMT ????????????????????.py?????? ant_*.py ?????
get_trade_detail_data ????????????????????????/????????????
"""
import importlib.util
import os
import sys
import time

ENTRY_VERSION = "20261016.08"
_shadow = None
_ACCOUNT_SNAPSHOT_MOD = None
_RESULT_LOG_MOD = None
_ENTRY_ACCOUNT_SKIP = ""
_LAST_ENTRY_ACCOUNT_SYNC = 0.0
_ENTRY_ACCOUNT_INTERVAL_SEC = 3.0
# 资金查询：日志模式不跟市值抖动。持仓仍走上面的 3 秒，卖出要可卖量。
_LAST_ENTRY_CASH_FETCH = 0.0
_ENTRY_CASH_FETCH_INTERVAL_SEC = 60.0
# 委托/成交查询较重，过勤会堵同一线程上的 tick 回调与 full_tick 补种
_LAST_ENTRY_ORDER_DEAL_SYNC = 0.0
_ENTRY_ORDER_DEAL_INTERVAL_SEC = 12.0
_ENTRY_ORDER_DEAL_SESSION_INTERVAL_SEC = 60.0
# kind -> get_trade_detail_data 已成功的参数元组；盘中禁止笛卡尔重试
_GTD_OK_ARGS = {}
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


def _load_result_log_mod():
    """QMT 停启策略不卸载 sys.modules。资金日志必须按文件时间载入，否则还是旧的 emit_asset。"""
    global _RESULT_LOG_MOD
    _ensure_qmt_sys_path()
    root = _qmt_python_dir()
    if not root:
        return None
    path = os.path.join(root, "ant_result_log.py")
    if not os.path.isfile(path):
        return None
    try:
        mtime = int(os.path.getmtime(path))
    except OSError:
        mtime = 0
    cached = _RESULT_LOG_MOD
    if cached is not None and getattr(cached, "_ANT_RESULT_LOG_MTIME", 0) == mtime:
        if hasattr(cached, "emit_asset"):
            return cached
    for key in list(sys.modules.keys()):
        if key == "ant_result_log" or key.startswith("ant_result_log_"):
            sys.modules.pop(key, None)
    mod_name = "ant_result_log_%d" % mtime
    try:
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        mod._ANT_RESULT_LOG_MTIME = mtime
    except Exception:
        return None
    if not hasattr(mod, "emit_asset"):
        return None
    _RESULT_LOG_MOD = mod
    return mod


def _in_continuous_auction(now=None):
    """连续竞价：把线程留给 tick 回调。"""
    from datetime import datetime
    from datetime import time as dt_time

    now = now or datetime.now()
    t = now.time()
    return (dt_time(9, 30) <= t <= dt_time(11, 30)) or (
        dt_time(13, 0) <= t <= dt_time(15, 0)
    )


def _gtd_kind(data_type):
    s = str(data_type or "").strip().lower()
    if s in ("position", "positions", "pos"):
        return "position"
    if s in ("order", "orders"):
        return "order"
    if s in ("deal", "deals", "trade"):
        return "deal"
    return "account"


def _gtd_len(raw):
    if raw is None:
        return -1
    try:
        return len(raw)
    except Exception:
        try:
            return sum(1 for _ in raw)
        except Exception:
            return 1


def _gtd_try(gtd, args):
    try:
        return gtd(*args)
    except TypeError:
        return None
    except Exception:
        return None


def _gtd_uniq(items):
    out = []
    for x in items:
        if x is None or x == "":
            continue
        if x not in out:
            out.append(x)
    return out


def _entry_fetch_trade_detail(account_id, data_type, strategy_names=None, account_type_hint=""):
    """Query get_trade_detail_data. 命中后缓存参数，盘中不再笛卡尔穷举。"""
    global _GTD_OK_ARGS
    try:
        gtd = get_trade_detail_data
    except NameError:
        return None
    if not callable(gtd):
        return None
    kind = _gtd_kind(data_type)
    strategies = _gtd_uniq(
        [str(s or "").strip() for s in (strategy_names or [])]
    )

    cached = _GTD_OK_ARGS.get(kind)
    if isinstance(cached, tuple) and cached:
        if cached[0] == "__merge__" and len(cached) == 4:
            _aid, _atype, _dtype = cached[1], cached[2], cached[3]
            merged = []
            for sn in strategies:
                raw = _gtd_try(gtd, (_aid, _atype, _dtype, sn))
                n = _gtd_len(raw)
                if n <= 0:
                    continue
                if isinstance(raw, list):
                    merged.extend(raw)
                else:
                    try:
                        merged.extend(list(raw))
                    except Exception:
                        merged.append(raw)
            if merged:
                return merged
            _GTD_OK_ARGS.pop(kind, None)
        else:
            raw = _gtd_try(gtd, cached)
            if raw is not None:
                return raw
            _GTD_OK_ARGS.pop(kind, None)

    dtypes = _gtd_uniq(
        [data_type, str(data_type).upper(), str(data_type).lower()]
    )
    if kind == "position":
        dtypes = _gtd_uniq(dtypes + ["POSITION", "position", "Position"])

    aid_s = str(account_id or "").strip()
    account_ids = []
    if aid_s:
        account_ids.append(aid_s)
        try:
            if aid_s.isdigit():
                account_ids.append(int(aid_s))
        except Exception:
            pass

    hint = str(account_type_hint or "").strip()
    account_types = _gtd_uniq([hint, "STOCK", "CREDIT"] if hint else ["STOCK", "CREDIT"])

    def _hit(args, raw):
        _GTD_OK_ARGS[kind] = args
        try:
            setattr(gtd, "_ant_last_pos_try", ["ok %s %s n=%s" % (kind, args[:3], _gtd_len(raw))])
        except Exception:
            pass
        return raw

    # 3 参：一次取该账户全部策略；有数据立刻缓存返回
    for aid in account_ids:
        for account_type in account_types:
            for dtype in dtypes:
                args = (aid, account_type, dtype)
                raw = _gtd_try(gtd, args)
                if _gtd_len(raw) > 0:
                    return _hit(args, raw)

    # ORDER/DEAL 3 参常为空：按策略名各查一次后合并（不再穷举大小写/两融）
    if strategies:
        for aid in account_ids[:1]:
            for account_type in account_types[:1]:
                for dtype in dtypes[:1]:
                    merged = []
                    for sn in strategies:
                        raw = _gtd_try(gtd, (aid, account_type, dtype, sn))
                        if _gtd_len(raw) <= 0:
                            continue
                        if isinstance(raw, list):
                            merged.extend(raw)
                        else:
                            try:
                                merged.extend(list(raw))
                            except Exception:
                                merged.append(raw)
                    if merged:
                        _GTD_OK_ARGS[kind] = ("__merge__", aid, account_type, dtype)
                        try:
                            setattr(
                                gtd,
                                "_ant_last_pos_try",
                                ["merge %s n=%d" % (kind, len(merged))],
                            )
                        except Exception:
                            pass
                        return merged
    return None


def _probe_bj_sectors_once(ContextInfo):
    """一次性探测本机 QMT 北交所板块名是否可用，结果写入 data/bj_sector_probe.json。"""
    global _BJ_SECTOR_PROBE_DONE
    if _BJ_SECTOR_PROBE_DONE:
        return
    _BJ_SECTOR_PROBE_DONE = True
    return


def _entry_sync_account_snapshot(ContextInfo, force=False):
    """大 QMT 账户/持仓/委托写入 results（调用 get_trade_detail_data）。"""
    global _ENTRY_ACCOUNT_SKIP, _LAST_ENTRY_ACCOUNT_SYNC, _LAST_ENTRY_ORDER_DEAL_SYNC, _LAST_ENTRY_CASH_FETCH
    try:
        _probe_bj_sectors_once(ContextInfo)
        now = time.time()
        if not force and now - _LAST_ENTRY_ACCOUNT_SYNC < float(_ENTRY_ACCOUNT_INTERVAL_SEC):
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

        acc_raw = None
        if force or now - _LAST_ENTRY_CASH_FETCH >= float(_ENTRY_CASH_FETCH_INTERVAL_SEC):
            acc_raw = _entry_fetch_trade_detail(
                aid, "account", account_type_hint=acct_type_hint
            )
            _LAST_ENTRY_CASH_FETCH = now
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
        od_interval = float(_ENTRY_ORDER_DEAL_INTERVAL_SEC)
        if _in_continuous_auction():
            od_interval = float(_ENTRY_ORDER_DEAL_SESSION_INTERVAL_SEC)
        if force or now - _LAST_ENTRY_ORDER_DEAL_SYNC >= od_interval:
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
                "\u8682\u8681\u002d\u63d0\u524d\u4e70\u5165",
                "\u8682\u8681\u002d\u63d0\u524d\u5356\u51fa",
                "\u8682\u8681\u002d\u591c\u5e02\u4e70\u5165",
                "\u8682\u8681\u002d\u591c\u5e02\u5356\u51fa",
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
            if force:
                _log_trade_snapshot(snap, results)
                print("[交易核心] 载入后已把持仓、委托和资金写入策略日志", flush=True)
        elif reason != _ENTRY_ACCOUNT_SKIP:
            _ENTRY_ACCOUNT_SKIP = reason
            print("[交易核心] 账户快照跳过: %s" % reason)
    except Exception as e:
        _LAST_ENTRY_ACCOUNT_SYNC = time.time()
        msg = "%s: %s" % (type(e).__name__, e)
        if msg != _ENTRY_ACCOUNT_SKIP:
            _ENTRY_ACCOUNT_SKIP = msg
            print("[交易核心] 账户快照错误: %s" % msg)



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





def _ui_host_value(name):
    """模型交易界面传入的 account() / accountype()。不读 data 文件。"""
    val = globals().get(name)
    if val is None:
        try:
            import builtins

            val = getattr(builtins, name, None)
        except Exception:
            val = None
    if callable(val):
        try:
            val = val()
        except Exception:
            return ""
    text = str(val or "").strip()
    if not text or text.lower() in ("none", "null") or text.startswith("<"):
        return ""
    if len(text) > 32 or any(ch.isspace() for ch in text):
        return ""
    return text


def _stamp_ui_account(ContextInfo):
    aid = _ui_host_value("account") or _ui_host_value("accid")
    acct_type = (
        _ui_host_value("accountype")
        or _ui_host_value("accounttype")
        or _ui_host_value("accountType")
    )
    if aid:
        try:
            ContextInfo.accountID = aid
            ContextInfo.account_id = aid
            ContextInfo.accid = aid
        except Exception:
            pass
    if acct_type:
        try:
            ContextInfo.account_type = acct_type
            ContextInfo.accountType = acct_type
        except Exception:
            pass
    if aid:
        _plog(
            "[入口] 模型交易账号已读到 尾号=%s 类型=%s"
            % (aid[-4:], acct_type or "STOCK")
        )
    else:
        _plog("[入口] 模型交易账号未读到")
    return aid


def init(ContextInfo):
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    _ensure_qmt_sys_path()
    _ensure_passorder_bound()
    _stamp_ui_account(ContextInfo)
    shadow = _load_shadow()
    if shadow is None:
        _plog("[入口] 初始化中止: 交易核心为 None")
        return
    out = shadow.init(ContextInfo)
    return out


def handlebar(ContextInfo):
    try:
        _ensure_passorder_bound()
        if _shadow is None:
            return
        # 先跑交易核心（含 full_tick 补种），再查账户，避免 GTD 堵行情墙钟
        out = _shadow.handlebar(ContextInfo)
        _publish_snapshot_if_requested(ContextInfo)
        _entry_sync_account_snapshot(ContextInfo)
        return out
    except KeyboardInterrupt:
        # 模型交易手动停止；吞掉以免深栈刷屏
        return


def periodic_sync(ContextInfo):
    try:
        _ensure_passorder_bound()
        if _shadow is None:
            return
        out = _shadow.periodic_sync(ContextInfo)
        _publish_snapshot_if_requested(ContextInfo)
        _entry_sync_account_snapshot(ContextInfo)
        return out
    except KeyboardInterrupt:
        return


def shadow_sync(ContextInfo):
    return periodic_sync(ContextInfo)


def _publish_snapshot_if_requested(ContextInfo):
    """会话刚载入时查一次柜台，把持仓、委托和资金打进策略日志。"""
    fn = getattr(_shadow, "take_account_snapshot_request", None)
    if not callable(fn):
        return
    try:
        need = bool(fn())
    except Exception:
        return
    if not need:
        return
    _entry_sync_account_snapshot(ContextInfo, force=True)


def flush_results(ContextInfo):
    if _shadow is None:
        return
    return _shadow.flush_results(ContextInfo)










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


def _log_trade_snapshot(snap, results=None):
    """成交或持仓变化时，把全部持仓、当日委托和资产打成一批短日志。"""
    rl = _load_result_log_mod()
    if rl is None:
        return
    pos = getattr(snap, "_CACHED_POSITIONS", None) or {}
    orders = list((getattr(snap, "_CACHED_ORDERS", None) or {}).values())
    acc = getattr(snap, "_CACHED_ACCOUNT", None)
    if acc is None and isinstance(results, dict):
        acc = results.get("account")
    try:
        rl.emit_asset(acc)
        rl.request_snapshot(pos, orders, acc)
    except Exception as e:
        print("[交易核心] 快照日志失败: %s" % e)


def account_callback(ContextInfo, accountInfo):
    _dispatch_account_snapshot_callback("on_account_callback", ContextInfo, accountInfo)
    try:
        snap = _load_account_snapshot_mod()
        rl = _load_result_log_mod()
        if snap is not None and rl is not None:
            rl.emit_asset(getattr(snap, "_CACHED_ACCOUNT", None))
    except Exception:
        pass


def position_callback(ContextInfo, positionInfo):
    snap = _load_account_snapshot_mod()
    before = {}
    if snap is not None:
        before = dict(getattr(snap, "_CACHED_POSITIONS", None) or {})
    _dispatch_account_snapshot_callback("on_position_callback", ContextInfo, positionInfo)
    try:
        if snap is None:
            return
        after = getattr(snap, "_CACHED_POSITIONS", None) or {}
        changed = False
        for code in set(before) | set(after):
            old = before.get(code) or {}
            new = after.get(code) or {}
            if int(old.get("volume") or 0) != int(new.get("volume") or 0):
                changed = True
                break
            if int(old.get("can_use_volume") or 0) != int(new.get("can_use_volume") or 0):
                changed = True
                break
        if changed:
            _log_trade_snapshot(snap)
    except Exception:
        pass


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
        _log_trade_snapshot(snap, results)
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
        _log_trade_snapshot(snap, results)
    except Exception as e:
        print("[入口] deal_callback 错误: %s: %s" % (type(e).__name__, e))


