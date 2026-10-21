# -*- coding: utf-8 -*-
"""从 QMT 的 FormulaOutput 日志还原原来 results.json 里的内容。

策略只 print，不写文件。本模块在交易界面进程里跟着当天的
XtClient_FormulaOutput_YYYYMMDD.log 往下读，只认 ANTRES 开头的短行。
leg / legx / ent / lbu / posx 会合并进 filled_legs 和两个建仓日文件。
cx 会把撤单结果写回 cancel_requests.json。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any, Dict, Optional

_OFFSET = 0
_BUF = ""
_PATH = ""
_DAY = ""
_LIVE = False
_DIRTY = False
_HB_AT = 0.0
_HB_SEEN = False
_HB_SUB = None
_SNAP_SEQ = 0
_OPEN_SNAP: Dict[str, Any] = {}
_STATUS = {
    "elastic_changed": False,
    "early_changed": False,
    "orders_changed": False,
    "snapshot_changed": False,
}
_RESULTS: Dict[str, Any] = {
    "source": "formula_log",
    "stocks": {},
    "orders": [],
    "broker_orders": [],
    "positions": {},
    "account": {},
    "elastic_states": {},
    "cage_states": {},
    "early_states": {},
    "done_task_ids": [],
}


def current() -> Dict[str, Any]:
    if not _LIVE:
        return {}
    return _RESULTS


def heartbeat_at() -> float:
    return float(_HB_AT or 0)


def heartbeat_seen() -> bool:
    return bool(_HB_SEEN)


def heartbeat_subscribe():
    """最近一拍心跳里的订阅数。没有 s 字段时返回 None（旧日志）。"""
    if _HB_SUB is None:
        return None
    try:
        return int(_HB_SUB)
    except (TypeError, ValueError):
        return None


def _install_root() -> str:
    try:
        import configparser

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cp = configparser.ConfigParser()
        cp.read(os.path.join(root, "data", "config.ini"), encoding="utf-8")
        py = ""
        if cp.has_section("qmt_builtin"):
            py = str(cp.get("qmt_builtin", "qmt_python_dir", fallback="") or "").strip()
        if py:
            return os.path.dirname(py.rstrip("\\/"))
    except Exception:
        pass
    return r"D:\国金证券QMT交易端"


def log_path(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    name = "XtClient_FormulaOutput_%s.log" % now.strftime("%Y%m%d")
    return os.path.join(_install_root(), "userdata", "log", name)


def _reset_if_needed(path: str) -> None:
    global _OFFSET, _BUF, _PATH, _DAY, _HB_SUB
    day = os.path.basename(path)
    if path != _PATH or day != _DAY:
        _PATH = path
        _DAY = day
        _OFFSET = 0
        _BUF = ""
        _HB_SUB = None


def _accept_line(line: str) -> None:
    idx = line.find("ANTRES ")
    if idx < 0:
        return
    rest = line[idx + len("ANTRES ") :].strip()
    if not rest:
        return
    parts = rest.split(" ", 1)
    kind = parts[0].strip()
    raw = parts[1].strip() if len(parts) > 1 else ""
    try:
        payload = json.loads(raw) if raw else {}
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    global _LIVE, _DIRTY, _HB_AT, _HB_SEEN, _HB_SUB
    _LIVE = True
    if kind == "hb":
        _HB_SEEN = True
        _HB_AT = time.time()
        row = {"n": payload.get("n"), "t": payload.get("t"), "at": _HB_AT}
        if "s" in payload:
            try:
                _HB_SUB = int(payload.get("s"))
            except (TypeError, ValueError):
                _HB_SUB = 0
            row["s"] = _HB_SUB
        _RESULTS["heartbeat"] = row
        return
    if kind == "asset":
        _set_asset(payload)
        return
    if kind == "el":
        _set_elastic(payload)
        return
    if kind == "cage":
        _set_cage(payload)
        return
    if kind == "early":
        _set_early(payload)
        return
    if kind == "done":
        tid = str(payload.get("id") or "").strip()
        if tid and tid not in _RESULTS["done_task_ids"]:
            _RESULTS["done_task_ids"].append(tid)
            _DIRTY = True
        return
    if kind == "ev":
        _add_event(payload)
        return
    if kind == "ord":
        _add_order(payload)
        return
    if kind == "px":
        # 策略已不再打 px；保留解析以兼容旧日志
        return
    if kind == "snap":
        _take_snap(payload)
        return
    if kind == "leg":
        _apply_filled_leg(payload)
        return
    if kind == "legx":
        _apply_leg_clear(payload)
        return
    if kind == "ent":
        _apply_entry(payload)
        return
    if kind == "lbu":
        _apply_last_buy(payload)
        return
    if kind == "posx":
        _apply_pos_clear(payload)
        return
    if kind == "cx":
        _apply_cancel(payload)
        return


def _set_asset(payload: dict) -> None:
    global _DIRTY
    account = dict(_RESULTS.get("account") or {})
    account["cash"] = payload.get("cash")
    account["total_asset"] = payload.get("total")
    account["market_value"] = payload.get("mkt")
    account["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    account.pop("account_id", None)
    _RESULTS["account"] = account
    _DIRTY = True


def _set_elastic(payload: dict) -> None:
    global _DIRTY
    tid = str(payload.get("id") or "").strip()
    if not tid:
        return
    states = _RESULTS.setdefault("elastic_states", {})
    if payload.get("gone"):
        states.pop(tid, None)
    else:
        kind = str(payload.get("k") or "")
        row = {
            "kind": kind,
            "triggered": bool(payload.get("tr")),
            "tick_idx": int(payload.get("ti") or 0),
        }
        if kind == "best_buy":
            row["lowest_price"] = payload.get("px")
            row["lowest_tick_idx"] = int(payload.get("xi") or 0)
            row["rebound_hit_count"] = int(payload.get("hc") or 0)
        else:
            row["highest_price"] = payload.get("px")
            row["highest_tick_idx"] = int(payload.get("xi") or 0)
            row["pullback_hit_count"] = int(payload.get("hc") or 0)
        states[tid] = row
    _STATUS["elastic_changed"] = True
    _DIRTY = True


def _set_cage(payload: dict) -> None:
    global _DIRTY
    tid = str(payload.get("id") or "").strip()
    if not tid:
        return
    states = _RESULTS.setdefault("cage_states", {})
    if payload.get("gone"):
        states.pop(tid, None)
    else:
        states[tid] = {"kind": str(payload.get("k") or ""), "entered": True}
    _DIRTY = True


def _set_early(payload: dict) -> None:
    global _DIRTY
    key = str(payload.get("id") or "").strip()
    if not key:
        return
    states = _RESULTS.setdefault("early_states", {})
    if payload.get("gone"):
        states.pop(key, None)
    else:
        states[key] = {
            "active": True,
            "task_id": str(payload.get("task") or ""),
            "kind": str(payload.get("k") or ""),
            "price": payload.get("px") or 0,
            "volume": int(payload.get("vol") or 0),
            "grid_index": payload.get("gi"),
            "user_order_id": str(payload.get("uid") or ""),
        }
    _STATUS["early_changed"] = True
    _DIRTY = True


def _add_event(payload: dict) -> None:
    global _DIRTY
    code = str(payload.get("stock_code") or "").strip().upper()
    if not code:
        return
    stocks = _RESULTS.setdefault("stocks", {})
    bucket = stocks.setdefault(code, {"events": []})
    events = bucket.setdefault("events", [])
    ev = dict(payload)
    if "passed" in ev and "metrics" not in ev:
        ev["metrics"] = {"passed": bool(ev.get("passed"))}
    events.append(ev)
    if len(events) > 200:
        bucket["events"] = events[-200:]
    _DIRTY = True


def _set_price(payload: dict) -> None:
    global _DIRTY
    code = str(payload.get("c") or "").strip().upper()
    try:
        price = float(payload.get("p") or 0)
    except (TypeError, ValueError):
        return
    if not code or price <= 0:
        return
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    stocks = _RESULTS.setdefault("stocks", {})
    bucket = stocks.setdefault(code, {"events": []})
    bucket["last_price"] = price
    if payload.get("t"):
        bucket["last_tick_time"] = str(payload.get("t"))
    bucket["quote_recv_at"] = now
    for src, dest in (
        ("o", "today_open"),
        ("h", "today_high"),
        ("l", "today_low"),
        ("lc", "last_close"),
    ):
        if not payload.get(src):
            continue
        try:
            bucket[dest] = float(payload.get(src))
        except (TypeError, ValueError):
            pass
    _RESULTS["quotes_recv_at"] = now
    _DIRTY = True


def _add_order(payload: dict) -> None:
    global _DIRTY
    orders = _RESULTS.setdefault("orders", [])
    orders.append(dict(payload))
    if len(orders) > 200:
        _RESULTS["orders"] = orders[-200:]
    _STATUS["orders_changed"] = True
    _DIRTY = True


def _take_snap(payload: dict) -> None:
    global _OPEN_SNAP, _SNAP_SEQ
    op = str(payload.get("op") or "")
    seq = payload.get("seq")
    if op == "begin":
        _OPEN_SNAP = {
            "seq": seq,
            "npos": int(payload.get("npos") or 0),
            "nord": int(payload.get("nord") or 0),
            "pos": [],
            "ord": [],
            "asset": None,
        }
        return
    if not _OPEN_SNAP or _OPEN_SNAP.get("seq") != seq:
        return
    if op == "asset":
        _OPEN_SNAP["asset"] = payload
        return
    if op == "pos":
        _OPEN_SNAP["pos"].append(payload)
        return
    if op == "ord":
        _OPEN_SNAP["ord"].append(payload)
        return
    if op != "end":
        return
    if len(_OPEN_SNAP["pos"]) != _OPEN_SNAP["npos"]:
        _OPEN_SNAP = {}
        return
    if len(_OPEN_SNAP["ord"]) != _OPEN_SNAP["nord"]:
        _OPEN_SNAP = {}
        return
    positions = {}
    for row in _OPEN_SNAP["pos"]:
        code = str(row.get("c") or "").strip().upper()
        if not code:
            continue
        positions[code] = {
            "stock_code": code,
            "stock_name": str(row.get("n") or ""),
            "volume": int(row.get("v") or 0),
            "can_use_volume": int(row.get("u") or 0),
            "open_price": float(row.get("p") or 0),
            "market_value": float(row.get("m") or 0),
        }
    broker = []
    for row in _OPEN_SNAP["ord"]:
        broker.append(
            {
                "stock_code": str(row.get("c") or ""),
                "stock_name": str(row.get("n") or ""),
                "side": str(row.get("sd") or ""),
                "broker_status_text": str(row.get("st") or ""),
                "price": float(row.get("px") or 0),
                "volume": int(row.get("v") or 0),
                "traded_volume": int(row.get("tv") or 0),
                "traded_price": float(row.get("tp") or 0),
                "order_sysid": str(row.get("id") or ""),
                "order_time": str(row.get("tm") or ""),
                "order_at": str(row.get("at") or ""),
            }
        )
    _RESULTS["positions"] = positions
    _RESULTS["broker_orders"] = broker
    if isinstance(_OPEN_SNAP.get("asset"), dict):
        _set_asset(_OPEN_SNAP["asset"])
    _SNAP_SEQ = int(seq or 0)
    _STATUS["snapshot_changed"] = True
    _OPEN_SNAP = {}
    global _DIRTY
    _DIRTY = True


def _apply_filled_leg(payload: dict) -> None:
    code = str(payload.get("c") or "").strip()
    lid = str(payload.get("id") or "").strip()
    if not code or not lid:
        return
    try:
        from utils.filled_legs import note_leg

        note_leg(
            code,
            name=payload.get("n") or "",
            leg_key="%s:%s" % (code, lid),
            side=payload.get("sd") or "",
            volume=payload.get("v") or 0,
            task_id=payload.get("task") or "",
            filled_at=payload.get("at") or "",
        )
    except Exception:
        pass


def _apply_leg_clear(payload: dict) -> None:
    code = str(payload.get("c") or "").strip()
    if not code:
        return
    try:
        from utils.filled_legs import clear_code

        clear_code(code)
    except Exception:
        pass


def _apply_entry(payload: dict) -> None:
    code = str(payload.get("c") or "").strip()
    day = str(payload.get("d") or "")[:10]
    if not code or len(day) < 10:
        return
    try:
        from utils.position_entry_dates import set_entry_date

        set_entry_date(code, day, overwrite=False)
    except Exception:
        pass


def _apply_last_buy(payload: dict) -> None:
    code = str(payload.get("c") or "").strip()
    day = str(payload.get("d") or "")[:10]
    if not code or len(day) < 10:
        return
    try:
        from utils.position_entry_dates import set_last_buy_date

        set_last_buy_date(code, day)
    except Exception:
        pass


def _apply_pos_clear(payload: dict) -> None:
    code = str(payload.get("c") or "").strip()
    if not code:
        return
    try:
        from utils.position_entry_dates import clear_entry_date

        clear_entry_date(code)
    except Exception:
        pass


def _apply_cancel(payload: dict) -> None:
    rid = str(payload.get("id") or "").strip()
    status = str(payload.get("st") or "").strip()
    if not rid or not status:
        return
    try:
        from utils.cancel_request import mark_processed

        mark_processed(
            rid,
            status,
            msg=str(payload.get("msg") or ""),
            processed_at=str(payload.get("at") or ""),
        )
    except Exception:
        pass


def fills_for_ui() -> list:
    side_name = {"buy": "买入", "sell": "卖出", "subscribe": "申购"}
    rows = []
    for rec in _RESULTS.get("broker_orders") or []:
        if not isinstance(rec, dict):
            continue
        side = str(rec.get("side") or "")
        rows.append(
            {
                "stock_code": rec.get("stock_code") or "",
                "stock_name": rec.get("stock_name") or "",
                "time": rec.get("order_time") or "",
                "side": side_name.get(side.lower(), side),
                "status": rec.get("broker_status_text") or "",
                "price": rec.get("price") or 0,
                "trade_price": rec.get("traded_price") or 0,
                "volume": rec.get("volume") or 0,
                "trade_volume": rec.get("traded_volume") or 0,
                "order_id": rec.get("order_sysid") or "",
                "note": "QMT策略日志",
            }
        )
    return rows


def _consume(text: str) -> None:
    global _BUF
    _BUF += text
    lines = _BUF.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        _BUF = lines.pop()
    else:
        _BUF = ""
    for line in lines:
        _accept_line(line)


def poll(results_path: str = "") -> Dict[str, Any]:
    """读新增日志。有变化时把还原结果写入 results.json，供图表直接打开。"""
    global _OFFSET, _DIRTY
    for key in _STATUS:
        _STATUS[key] = False
    path = log_path()
    _reset_if_needed(path)
    try:
        size = os.path.getsize(path) if os.path.isfile(path) else 0
    except OSError:
        size = 0
    if size < _OFFSET:
        _OFFSET = 0
        _BUF = ""
    if size > _OFFSET and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                handle.seek(_OFFSET)
                chunk = handle.read()
                _OFFSET = handle.tell()
            if chunk:
                _consume(chunk)
        except OSError:
            pass
    if _DIRTY and results_path:
        _write(results_path)
        _DIRTY = False
    out = dict(_STATUS)
    out["live"] = _LIVE
    out["heartbeat_seen"] = _HB_SEEN
    out["heartbeat_at"] = _HB_AT
    out["snapshot_seq"] = _SNAP_SEQ
    out["positions"] = dict(_RESULTS.get("positions") or {})
    out["asset"] = {
        key: (_RESULTS.get("account") or {}).get(key)
        for key in ("cash", "market_value", "total_asset")
        if key in (_RESULTS.get("account") or {})
    }
    out["fills"] = fills_for_ui() if _STATUS.get("snapshot_changed") else []
    return out


def _write(results_path: str) -> None:
    _RESULTS["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    _RESULTS["source"] = "formula_log"
    account = _RESULTS.get("account")
    if isinstance(account, dict):
        account.pop("account_id", None)
    try:
        from utils.ant_rules_io_ext import save_json_atomic

        save_json_atomic(results_path, _RESULTS)
    except Exception:
        folder = os.path.dirname(results_path) or "."
        os.makedirs(folder, exist_ok=True)
        with open(results_path, "w", encoding="utf-8") as handle:
            json.dump(_RESULTS, handle, ensure_ascii=False, indent=2)
