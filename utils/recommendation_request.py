# -*- coding: utf-8 -*-
"""builtin/standalone：弹性推荐值一次一股（买+卖一包）。

本进程写 data/recommendation_requests.json，并把仍待处理的条目写成
qmt_builtin/src/ant_rec_local.py，覆盖到 QMT 后由策略按 mtime 热加载。
策略不改 json。处理结果打 ANTRES rec，由本模块 note_rec_result / wait 匹配。
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
REQUESTS_PATH = os.path.join(_PROJECT_ROOT, "data", "recommendation_requests.json")
_LOCAL_PY = os.path.join(_PROJECT_ROOT, "qmt_builtin", "src", "ant_rec_local.py")
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
        days = it.get("days") or []
        if not isinstance(days, list):
            days = []
        days_norm = []
        for d in days:
            dd = str(d or "").replace("-", "").replace("/", "")[:8]
            if len(dd) == 8:
                days_norm.append(dd)
        row = {
            "id": str(it.get("id") or ""),
            "c": str(it.get("c") or it.get("stock_code") or "").strip().upper()[:6],
            "days": days_norm,
            "si": int(it.get("si") or it.get("sample_interval") or 30),
        }
        if it.get("fake") or it.get("fk"):
            row["fake"] = 1
        out.append(row)
    return out


def _render_local(items: List[Dict[str, Any]]) -> str:
    payload = json.dumps(items, ensure_ascii=True, separators=(",", ":"))
    literal = json.dumps(payload, ensure_ascii=True)
    return (
        "# -*- coding: utf-8 -*-\n"
        "# 待算推荐值列表。由交易界面生成，不进 Git。QMT 按文件 mtime 热加载。\n"
        "import json\n"
        "RECS = json.loads(%s)\n"
    ) % literal


def publish_rec_local(data: Optional[Dict[str, Any]] = None) -> None:
    """把仍为 pending 的推荐值请求写成 Python，并覆盖到 QMT python 目录。"""
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

        encoded = _to_gbk_bytes(text, "ant_rec_local.py")
        root = Path(_PROJECT_ROOT)
        (root / "qmt_builtin" / "ant_rec_local.py").write_bytes(encoded)
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
            (folder_path / "ant_rec_local.py").write_bytes(encoded)
    except Exception:
        pass


def note_rec_result(payload: Dict[str, Any]) -> bool:
    """日志解析到 ANTRES rec / recx 时回写。"""
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
                    "s1", "s1r", "s1p", "s1d",
                    "s2", "s2r", "s2p", "s2d",
                    "b1", "b1r", "b1s", "b1d",
                    "b2", "b2r", "b2s", "b2d",
                    "n", "nd", "c",
                )
                if k in payload
            }
            it["processed_at"] = str(payload.get("at") or _now_iso())[:19]
            found = True
            break
        if found:
            save_requests(data)
            publish_rec_local(data)
        return True


def get_cached_result(req_id: str) -> Optional[Dict[str, Any]]:
    rid = str(req_id or "").strip()
    if not rid:
        return None
    with _LOCK:
        row = _RESULTS.get(rid)
        return dict(row) if isinstance(row, dict) else None


def wait_rec_result(
    req_id: str,
    timeout_sec: float = 180.0,
    poll_fn=None,
    poll_interval_sec: float = 0.25,
) -> Optional[Dict[str, Any]]:
    """等待匹配 req_id 的 rec 回执。可选 poll_fn（通常是 formula_log.poll）。"""
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


def enqueue_rec(
    stock_code: str,
    days: List[str],
    sample_interval: int = 30,
    fake: bool = False,
) -> Optional[str]:
    """提交一股推荐值请求（买+卖一包），返回 request id。"""
    code = str(stock_code or "").strip().upper()
    if code.endswith(".SH") or code.endswith(".SZ") or code.endswith(".BJ"):
        code = code.split(".", 1)[0]
    code = "".join(ch for ch in code if ch.isdigit())[:6].zfill(6) if code else ""
    days_norm: List[str] = []
    for d in days or []:
        dd = str(d or "").replace("-", "").replace("/", "")[:8]
        if len(dd) == 8:
            days_norm.append(dd)
    if len(code) != 6:
        return None
    if not days_norm and not fake:
        return None
    try:
        si = int(sample_interval or 30)
    except Exception:
        si = 30
    with _LOCK:
        data = load_requests()
        items: List[Dict[str, Any]] = data.setdefault("items", [])
        for it in items:
            if not isinstance(it, dict):
                continue
            if str(it.get("status") or "") != "pending":
                continue
            if str(it.get("c") or "").zfill(6) == code and list(it.get("days") or []) == days_norm and int(it.get("si") or 30) == si:
                publish_rec_local(data)
                return str(it.get("id") or "")
        req_id = uuid.uuid4().hex[:16]
        row = {
            "id": req_id,
            "c": code,
            "days": days_norm,
            "si": si,
            "status": "pending",
            "msg": "",
            "requested_at": _now_iso(),
            "processed_at": "",
            "result": {},
        }
        if fake:
            row["fake"] = 1
        items.append(row)
        if len(items) > 200:
            data["items"] = items[-200:]
        save_requests(data)
        publish_rec_local(data)
        return req_id


def request_rec(
    stock_code: str,
    days: List[str],
    sample_interval: int = 30,
    timeout_sec: float = 180.0,
    poll_fn=None,
    fake: bool = False,
) -> Optional[Dict[str, Any]]:
    """一次一股：发布 → 等 ANTRES rec。"""
    rid = enqueue_rec(stock_code, days, sample_interval=sample_interval, fake=fake)
    if not rid:
        return None
    return wait_rec_result(rid, timeout_sec=timeout_sec, poll_fn=poll_fn)


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
        publish_rec_local(data)
