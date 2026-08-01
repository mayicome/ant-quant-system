#coding:gbk
"""QMT 侧处理 data/cancel_requests.json（主程序 UI 撤单）。"""
import json
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from ant_qmt_paths import PROJECT_ROOT
except ImportError:
    from qmt_builtin.ant_qmt_paths import PROJECT_ROOT

REQUESTS_PATH = os.path.join(PROJECT_ROOT.rstrip("\\/"), "data", "cancel_requests.json")


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    folder = os.path.dirname(path) or "."
    if not os.path.isdir(folder):
        os.makedirs(folder)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    for attempt in range(8):
        try:
            os.replace(tmp, path)
            return
        except OSError:
            if attempt >= 7:
                raise
            time.sleep(0.05 * (attempt + 1))


def _empty() -> Dict[str, Any]:
    return {"version": 1, "items": []}


def load_requests() -> Dict[str, Any]:
    if not os.path.isfile(REQUESTS_PATH):
        return _empty()
    try:
        with open(REQUESTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _empty()
        if not isinstance(data.get("items"), list):
            data["items"] = []
        data.setdefault("version", 1)
        return data
    except Exception:
        return _empty()


def save_requests(data: Dict[str, Any]) -> None:
    _atomic_write_json(REQUESTS_PATH, data)


def process_pending_cancels(ContextInfo, po_module=None) -> int:
    """
    处理 pending 撤单。返回成功发出 cancel 的条数。
    po_module: ant_passorder 模块（可选，缺省动态 import）。
    """
    data = load_requests()
    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        return 0

    pending = [
        it
        for it in items
        if isinstance(it, dict) and str(it.get("status") or "") == "pending"
    ]
    if not pending:
        return 0

    if po_module is None:
        try:
            import ant_passorder as po_module
        except ImportError:
            try:
                from qmt_builtin import ant_passorder as po_module
            except ImportError:
                print("[撤单请求] ant_passorder import failed")
                return 0

    cancel_fn = getattr(po_module, "cancel_order_sysid", None)
    if not callable(cancel_fn):
        print("[撤单请求] cancel_order_sysid missing")
        return 0

    sent = 0
    changed = False
    for it in pending:
        sysid = str(it.get("order_sysid") or "").strip()
        code = str(it.get("stock_code") or "").strip()
        acct = str(it.get("account_type") or "STOCK").strip() or "STOCK"
        if not sysid:
            it["status"] = "error"
            it["msg"] = "bad_sysid"
            it["processed_at"] = _now_iso()
            changed = True
            continue
        ok, reason, crec = cancel_fn(ContextInfo, sysid, account_type=acct)
        it["processed_at"] = _now_iso()
        it["msg"] = str(reason or (crec or {}).get("msg") or "")
        if ok:
            it["status"] = "sent"
            sent += 1
            print(
                "[撤单请求] CANCEL sent sysid=%s code=%s"
                % (sysid, code or "-")
            )
        else:
            it["status"] = "error"
            print(
                "[撤单请求] CANCEL fail sysid=%s code=%s msg=%s"
                % (sysid, code or "-", it["msg"])
            )
        changed = True

    if changed:
        # 保留最近 100 条
        if len(items) > 100:
            data["items"] = items[-100:]
        save_requests(data)
    return sent
