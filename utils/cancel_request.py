# -*- coding: utf-8 -*-
"""
builtin/standalone：主程序无法用 xt_trader 撤单，写入 data/cancel_requests.json，
由大 QMT 内置策略 passorder.cancel 处理。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUESTS_PATH = os.path.join(_PROJECT_ROOT, "data", "cancel_requests.json")


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
    data = load_requests()
    items: List[Dict[str, Any]] = data.setdefault("items", [])
    # 避免重复 pending 同一合同号
    for it in items:
        if not isinstance(it, dict):
            continue
        if str(it.get("status") or "") != "pending":
            continue
        if str(it.get("order_sysid") or "").strip() == sysid:
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
    return req_id
