# -*- coding: utf-8 -*-
"""builtin/standalone：涨停封单结构一次一只。

本进程写 data/seal_structure_requests.json，并把仍待处理的条目写成
qmt_builtin/src/ant_seal_local.py，覆盖到 QMT 后由策略按 mtime 热加载。
策略不改 json。处理结果打 ANTRES seal，由本模块 note_seal_result / wait 匹配。
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
REQUESTS_PATH = os.path.join(_PROJECT_ROOT, "data", "seal_structure_requests.json")
_LOCAL_PY = os.path.join(_PROJECT_ROOT, "qmt_builtin", "src", "ant_seal_local.py")
_LOCK = threading.RLock()
_RESULTS: Dict[str, Dict[str, Any]] = {}
_RESULT_CV = threading.Condition(_LOCK)


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
                "d": str(it.get("d") or it.get("trade_date") or "")[:8],
                "c": str(it.get("c") or it.get("stock_code") or "").strip().upper()[:6],
            }
        )
    return out


def _render_local(items: List[Dict[str, Any]]) -> str:
    payload = json.dumps(items, ensure_ascii=True, separators=(",", ":"))
    literal = json.dumps(payload, ensure_ascii=True)
    return (
        "# -*- coding: utf-8 -*-\n"
        "# 待算封单结构列表。由分析程序生成，不进 Git。QMT 按文件 mtime 热加载。\n"
        "import json\n"
        "SEALS = json.loads(%s)\n"
    ) % literal


def publish_seal_local(data: Optional[Dict[str, Any]] = None) -> None:
    """把仍为 pending 的封单请求写成 Python，并覆盖到 QMT python 目录。"""
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

        encoded = _to_gbk_bytes(text, "ant_seal_local.py")
        root = Path(_PROJECT_ROOT)
        (root / "qmt_builtin" / "ant_seal_local.py").write_bytes(encoded)
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
            (folder_path / "ant_seal_local.py").write_bytes(encoded)
    except Exception:
        pass


def note_seal_result(payload: Dict[str, Any]) -> bool:
    """日志解析到 ANTRES seal / sealx 时回写。"""
    if not isinstance(payload, dict):
        return False
    rid = str(payload.get("id") or "").strip()
    st = str(payload.get("st") or "").strip()
    if not rid or not st:
        return False
    with _RESULT_CV:
        _RESULTS[rid] = dict(payload)
        _RESULT_CV.notify_all()
        data = load_requests()
        found = False
        for it in data.get("items") or []:
            if not isinstance(it, dict):
                continue
            if str(it.get("id") or "") != rid:
                continue
            it["status"] = st
            it["msg"] = str(payload.get("msg") or "")
            it["result"] = {
                k: payload.get(k)
                for k in (
                    "ca",
                    "ty",
                    "sr",
                    "rr",
                    "vc",
                    "ts",
                    "tl",
                    "sh",
                    "ri",
                    "sa",
                    "ss",
                    "n",
                    "c",
                    "d",
                )
                if k in payload
            }
            it["processed_at"] = str(payload.get("at") or _now_iso())[:19]
            found = True
            break
        if found:
            save_requests(data)
            publish_seal_local(data)
        return True


def get_cached_result(req_id: str) -> Optional[Dict[str, Any]]:
    rid = str(req_id or "").strip()
    if not rid:
        return None
    with _LOCK:
        row = _RESULTS.get(rid)
        return dict(row) if isinstance(row, dict) else None


def wait_seal_result(
    req_id: str,
    timeout_sec: float = 120.0,
    poll_fn=None,
    poll_interval_sec: float = 0.25,
) -> Optional[Dict[str, Any]]:
    """等待匹配 req_id 的 seal 回执。可选 poll_fn（通常是 formula_log.poll）。"""
    rid = str(req_id or "").strip()
    if not rid:
        return None
    deadline = time.time() + float(timeout_sec)
    while time.time() < deadline:
        if callable(poll_fn):
            try:
                poll_fn()
            except Exception:
                pass
        with _RESULT_CV:
            row = _RESULTS.get(rid)
            if isinstance(row, dict) and str(row.get("st") or ""):
                return dict(row)
            remain = deadline - time.time()
            if remain <= 0:
                break
            _RESULT_CV.wait(timeout=min(float(poll_interval_sec), max(0.05, remain)))
    with _LOCK:
        row = _RESULTS.get(rid)
        return dict(row) if isinstance(row, dict) else None


def enqueue_seal(trade_date: str, stock_code: str) -> Optional[str]:
    """提交一只封单结构请求，返回 request id。"""
    day = str(trade_date or "").replace("-", "").replace("/", "")[:8]
    code = str(stock_code or "").strip().upper()
    if code.endswith(".SH") or code.endswith(".SZ") or code.endswith(".BJ"):
        code = code.split(".", 1)[0]
    code = "".join(ch for ch in code if ch.isdigit())[:6].zfill(6) if code else ""
    if len(day) != 8 or len(code) != 6:
        return None
    with _LOCK:
        data = load_requests()
        items: List[Dict[str, Any]] = data.setdefault("items", [])
        for it in items:
            if not isinstance(it, dict):
                continue
            if str(it.get("status") or "") != "pending":
                continue
            if str(it.get("d") or "")[:8] == day and str(it.get("c") or "").zfill(6) == code:
                publish_seal_local(data)
                return str(it.get("id") or "")
        req_id = uuid.uuid4().hex[:16]
        items.append(
            {
                "id": req_id,
                "d": day,
                "c": code,
                "status": "pending",
                "msg": "",
                "requested_at": _now_iso(),
                "processed_at": "",
                "result": {},
            }
        )
        if len(items) > 200:
            data["items"] = items[-200:]
        save_requests(data)
        publish_seal_local(data)
        return req_id


def request_seal(
    trade_date: str,
    stock_code: str,
    timeout_sec: float = 120.0,
    poll_fn=None,
) -> Optional[Dict[str, Any]]:
    """一次一只：发布 → 等 ANTRES seal。"""
    rid = enqueue_seal(trade_date, stock_code)
    if not rid:
        return None
    return wait_seal_result(rid, timeout_sec=timeout_sec, poll_fn=poll_fn)


def clear_pending() -> None:
    """清空待处理（发布空列表）。"""
    with _LOCK:
        data = load_requests()
        for it in data.get("items") or []:
            if isinstance(it, dict) and str(it.get("status") or "") == "pending":
                it["status"] = "cancelled"
                it["msg"] = "cleared"
                it["processed_at"] = _now_iso()
        save_requests(data)
        publish_seal_local(data)
