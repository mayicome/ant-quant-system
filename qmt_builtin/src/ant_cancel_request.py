# -*- coding: utf-8 -*-
"""QMT 侧处理撤单。

待撤列表在同目录 ant_cancel_local.py，交易系统覆盖后按 mtime 热加载。
不读写 cancel_requests.json。处理结果打进策略日志，由交易系统落盘。
"""
import json
import os
import sys
from datetime import datetime

_CANCEL_MTIME = 0.0
_CANCEL_MOD = None
_DONE = {}


def _now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _local_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "ant_cancel_local.py")


def _load_local():
    """同一 mtime 复用模块。QMT 不卸载 sys.modules。"""
    global _CANCEL_MTIME, _CANCEL_MOD
    path = _local_path()
    if not os.path.isfile(path):
        return None
    try:
        mtime = float(os.path.getmtime(path))
    except OSError:
        return None
    if _CANCEL_MOD is not None and mtime == _CANCEL_MTIME:
        return _CANCEL_MOD
    import importlib.util

    mod_name = "ant_cancel_local_%d" % int(mtime)
    existing = sys.modules.get(mod_name)
    if existing is not None:
        _CANCEL_MOD = existing
        _CANCEL_MTIME = mtime
        return existing
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    _CANCEL_MOD = mod
    _CANCEL_MTIME = mtime
    return mod


def _pending_rows():
    mod = _load_local()
    if mod is None:
        return []
    raw = getattr(mod, "CANCELS", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if not isinstance(raw, list):
        return []
    out = []
    for it in raw:
        if isinstance(it, dict):
            out.append(it)
    return out


def _emit(req_id, sysid, code, status, msg, at):
    try:
        import ant_result_log as rl
    except ImportError:
        try:
            import qmt_builtin.ant_result_log as rl
        except ImportError:
            rl = None
    if rl is not None and hasattr(rl, "emit_cancel"):
        try:
            rl.emit_cancel(req_id, sysid, code=code, status=status, msg=msg, at=at)
            return
        except Exception:
            pass
    row = {"id": str(req_id or "")[:16], "sys": str(sysid or "")[:32], "st": status, "at": at}
    if code:
        row["c"] = str(code)[:12]
    if msg:
        row["msg"] = str(msg)[:80]
    try:
        print("ANTRES cx " + json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        pass


def process_pending_cancels(ContextInfo, po_module=None):
    """处理热加载列表里的待撤单。返回成功发出 cancel 的条数。"""
    rows = _pending_rows()
    if not rows:
        return 0
    if po_module is None:
        try:
            import ant_passorder as po_module
        except ImportError:
            try:
                from qmt_builtin import ant_passorder as po_module
            except ImportError:
                print("[撤单请求] ant_passorder 导入失败")
                return 0
    cancel_fn = getattr(po_module, "cancel_order_sysid", None)
    if not callable(cancel_fn):
        print("[撤单请求] 缺少 cancel_order_sysid")
        return 0

    sent = 0
    for it in rows:
        rid = str(it.get("id") or "").strip()
        sysid = str(it.get("order_sysid") or "").strip()
        code = str(it.get("stock_code") or "").strip()
        acct = str(it.get("account_type") or "STOCK").strip() or "STOCK"
        if not rid:
            rid = sysid
        if rid and _DONE.get(rid):
            continue
        at = _now_iso()
        if not sysid:
            _DONE[rid] = "error"
            _emit(rid, sysid, code, "error", "bad_sysid", at)
            continue
        try:
            ok, reason, crec = cancel_fn(ContextInfo, sysid, account_type=acct)
        except Exception as exc:
            ok = False
            reason = "%s: %s" % (type(exc).__name__, exc)
            crec = None
        msg = str(reason or (crec or {}).get("msg") or "")
        if ok:
            status = "sent"
            sent += 1
            print("[撤单请求] 撤单已发 sysid=%s code=%s" % (sysid, code or "-"))
        else:
            status = "error"
            print("[撤单请求] 撤单失败 sysid=%s code=%s msg=%s" % (sysid, code or "-", msg))
        _DONE[rid] = status
        _emit(rid, sysid, code, status, msg, at)
    return sent
