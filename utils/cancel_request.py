# -*- coding: utf-8 -*-
"""
builtin/standalone：主程序无法用 xt_trader 撤单。

本进程写 data/cancel_requests.json，并把仍待处理的条目写成
qmt_builtin/src/ant_cancel_local.py，覆盖到 QMT 后由策略按 mtime 热加载。
策略不改 json。处理结果打进策略日志，再由本模块 mark_processed 落盘。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUESTS_PATH = os.path.join(_PROJECT_ROOT, "data", "cancel_requests.json")
_LOCAL_PY = os.path.join(_PROJECT_ROOT, "qmt_builtin", "src", "ant_cancel_local.py")
_LOCK = threading.RLock()


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _atomic_write(path: str, payload: Dict[str, Any]) -> None:
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
        items = data.get("items")
        if not isinstance(items, list):
            data["items"] = []
        data.setdefault("version", 1)
        return data
    except Exception:
        return _empty()


def save_requests(data: Dict[str, Any]) -> None:
    _atomic_write(REQUESTS_PATH, data)


def _pending_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for it in data.get("items") or []:
        if not isinstance(it, dict):
            continue
        if str(it.get("status") or "") != "pending":
            continue
        out.append(
            {
                "id": str(it.get("id") or ""),
                "order_sysid": str(it.get("order_sysid") or ""),
                "stock_code": str(it.get("stock_code") or ""),
                "account_type": str(it.get("account_type") or "STOCK"),
            }
        )
    return out


def _render_local(items: List[Dict[str, Any]]) -> str:
    payload = json.dumps(items, ensure_ascii=True, separators=(",", ":"))
    literal = json.dumps(payload, ensure_ascii=True)
    return (
        "# -*- coding: utf-8 -*-\n"
        "# 待撤单列表。由交易系统生成，不进 Git。QMT 按文件 mtime 热加载。\n"
        "import json\n"
        "CANCELS = json.loads(%s)\n"
    ) % literal


def publish_cancel_local(data: Optional[Dict[str, Any]] = None) -> None:
    """把仍为 pending 的撤单写成 Python，并覆盖到 QMT python 目录。"""
    if data is None:
        data = load_requests()
    text = _render_local(_pending_items(data))
    folder = os.path.dirname(_LOCAL_PY)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    with open(_LOCAL_PY, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    try:
        from tools.sync_qmt_gbk import _to_gbk_bytes
        import configparser
        from pathlib import Path

        encoded = _to_gbk_bytes(text, "ant_cancel_local.py")
        root = Path(_PROJECT_ROOT)
        (root / "qmt_builtin" / "ant_cancel_local.py").write_bytes(encoded)
        cp = configparser.ConfigParser()
        cp.read(os.path.join(_PROJECT_ROOT, "data", "config.ini"), encoding="utf-8")
        raw = ""
        if cp.has_section("qmt_builtin"):
            raw = cp.get("qmt_builtin", "qmt_python_dir", fallback="").strip()
        dirs = []
        if raw:
            dirs.append(Path(raw.replace("/", "\\")))
        sim = Path(r"D:\国金QMT交易端模拟\python")
        if sim.is_dir():
            dirs.append(sim)
        seen = set()
        for folder_path in dirs:
            key = str(folder_path.resolve()) if folder_path.is_dir() else ""
            if not key or key in seen:
                continue
            seen.add(key)
            (folder_path / "ant_cancel_local.py").write_bytes(encoded)
    except Exception:
        pass


def mark_processed(
    req_id: str,
    status: str,
    msg: str = "",
    processed_at: str = "",
) -> bool:
    """按日志回写撤单结果，并刷新给 QMT 的待处理列表。"""
    rid = str(req_id or "").strip()
    st = str(status or "").strip()
    if not rid or not st:
        return False
    with _LOCK:
        data = load_requests()
        found = False
        for it in data.get("items") or []:
            if not isinstance(it, dict):
                continue
            if str(it.get("id") or "") != rid:
                continue
            if str(it.get("status") or "") == st and str(it.get("msg") or "") == str(msg or ""):
                return False
            it["status"] = st
            it["msg"] = str(msg or "")
            it["processed_at"] = str(processed_at or _now_iso())[:19]
            found = True
            break
        if not found:
            return False
        save_requests(data)
        publish_cancel_local(data)
        return True


def cancel_inflight(order_sysid: str) -> bool:
    """这笔委托已提交撤单、柜台尚未给终结状态。"""
    sysid = str(order_sysid or "").strip()
    if not sysid:
        return False
    latest = ""
    for it in load_requests().get("items") or []:
        if not isinstance(it, dict):
            continue
        if str(it.get("order_sysid") or "").strip() != sysid:
            continue
        latest = str(it.get("status") or "")
    return latest in ("pending", "sent")


def enqueue_cancel(
    order_sysid: str,
    *,
    stock_code: str = "",
    account_type: str = "STOCK",
) -> Optional[str]:
    """提交撤单请求，返回 request id；失败返回 None。"""
    sysid = str(order_sysid or "").strip()
    if not sysid or sysid in ("0", "-1"):
        return None
    with _LOCK:
        return _enqueue_cancel_locked(sysid, stock_code=stock_code, account_type=account_type)


def _enqueue_cancel_locked(
    sysid: str,
    *,
    stock_code: str = "",
    account_type: str = "STOCK",
) -> Optional[str]:
    data = load_requests()
    items: List[Dict[str, Any]] = data.setdefault("items", [])
    for it in items:
        if not isinstance(it, dict):
            continue
        if str(it.get("status") or "") != "pending":
            continue
        if str(it.get("order_sysid") or "").strip() == sysid:
            publish_cancel_local(data)
            return str(it.get("id") or "")
    req_id = uuid.uuid4().hex[:16]
    items.append(
        {
            "id": req_id,
            "order_sysid": sysid,
            "stock_code": str(stock_code or "").strip().upper(),
            "account_type": str(account_type or "STOCK"),
            "status": "pending",
            "msg": "",
            "requested_at": _now_iso(),
            "processed_at": "",
        }
    )
    # 只保留最近 100 条，防止文件膨胀
    if len(items) > 100:
        data["items"] = items[-100:]
    save_requests(data)
    publish_cancel_local(data)
    return req_id
