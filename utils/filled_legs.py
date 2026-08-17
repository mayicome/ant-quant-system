# -*- coding: utf-8 -*-
"""实盘已执行分支（买/卖腿）持久化。

文件：data/filled_legs.json
  {
    "legs": ["000001:OPEN50", "000001:MA5", ...],
    "meta": {
      "000001:OPEN50": {
        "side": "sell",
        "name": "...",
        "filled_at": "2026-08-12T10:30:00",
        "volume": 500,
        "task_id": "..."
      }
    },
    "updated_at": "..."
  }

规则：
- QMT/主程序确认成交后写入 leg_key（有则用；否则由规则名推断）
- 可用持仓 < 100：清除该代码全部腿（便于下次再买再卖）
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

_LOCK = threading.RLock()

_KNOWN_LEGS = (
    "OPEN50_REST",
    "OPEN50",
    "LU10",
    "MA5",
    "MA10",
    "MA20",
)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_path(project_root: Optional[str] = None) -> Path:
    root = Path(project_root) if project_root else _project_root()
    return root / "data" / "filled_legs.json"


def _norm_code(code: Any) -> str:
    s = str(code or "").strip().upper()
    if "." in s:
        s = s.split(".", 1)[0]
    digits = "".join(c for c in s if c.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)[-6:]


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def infer_leg_id(name: Any, leg_key: Any = None) -> str:
    """从 leg_key 或规则名抽出腿短码（OPEN50 / MA5 …）。"""
    lk = str(leg_key or "").strip()
    if ":" in lk:
        lk = lk.split(":", 1)[1].strip()
    if lk:
        su = lk.upper().replace(" ", "")
        for leg in _KNOWN_LEGS:
            if leg == su or leg in su:
                return leg
        if lk:
            return lk
    s = str(name or "")
    su = s.upper().replace(" ", "")
    for leg in _KNOWN_LEGS:
        if leg in su or leg in s:
            return leg
    if "破MA20" in s or "破 MA20" in s:
        return "破MA20"
    if "无条件清仓" in s or "末日" in s or "强制清仓" in s:
        return "末日清仓"
    return ""


def make_leg_key(code: Any, name: Any = None, leg_key: Any = None) -> str:
    c6 = _norm_code(code)
    if not c6:
        return ""
    raw = str(leg_key or "").strip()
    if raw and ":" in raw:
        left, right = raw.split(":", 1)
        if _norm_code(left) == c6 and right.strip():
            lid = infer_leg_id(name, right.strip()) or right.strip()
            return "%s:%s" % (c6, lid)
    lid = infer_leg_id(name, raw)
    if not lid:
        return ""
    return "%s:%s" % (c6, lid)


def _load_raw(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {"legs": [], "meta": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {"legs": [], "meta": {}}
    if not isinstance(raw, dict):
        if isinstance(raw, list):
            return {"legs": [str(x) for x in raw if x], "meta": {}}
        return {"legs": [], "meta": {}}
    legs = raw.get("legs")
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    out_legs: List[str] = []
    if isinstance(legs, list):
        out_legs = [str(x) for x in legs if x]
    elif isinstance(legs, dict):
        out_legs = [str(k) for k, v in legs.items() if v]
        if not meta:
            meta = {str(k): {"flag": True} for k, v in legs.items() if v}
    return {"legs": out_legs, "meta": dict(meta), "updated_at": raw.get("updated_at")}


def _save_raw(path: Path, legs: Iterable[str], meta: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clean_legs = sorted({str(x) for x in legs if x})
    clean_meta = {k: v for k, v in (meta or {}).items() if k in set(clean_legs)}
    payload = {
        "legs": clean_legs,
        "meta": clean_meta,
        "updated_at": _now_iso(),
    }
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def load_all(project_root: Optional[str] = None) -> Dict[str, Any]:
    with _LOCK:
        return _load_raw(default_path(project_root))


def load_leg_keys(project_root: Optional[str] = None) -> List[str]:
    data = load_all(project_root)
    return list(data.get("legs") or [])


def load_legs_by_code(project_root: Optional[str] = None) -> Dict[str, Set[str]]:
    out: Dict[str, Set[str]] = {}
    for raw in load_leg_keys(project_root):
        s = str(raw or "")
        if ":" not in s:
            continue
        left, right = s.split(":", 1)
        c6 = _norm_code(left)
        lid = infer_leg_id("", right) or right.strip()
        if not c6 or not lid:
            continue
        out.setdefault(c6, set()).add(lid)
    return out


def note_leg(
    code: Any,
    *,
    name: Any = None,
    leg_key: Any = None,
    side: Any = None,
    volume: Any = None,
    task_id: Any = None,
    filled_at: Any = None,
    project_root: Optional[str] = None,
) -> Optional[str]:
    """记一笔已执行腿；返回写入的 leg_key，无法识别则 None。"""
    key = make_leg_key(code, name=name, leg_key=leg_key)
    if not key:
        return None
    path = default_path(project_root)
    with _LOCK:
        data = _load_raw(path)
        legs = set(data.get("legs") or [])
        meta = dict(data.get("meta") or {})
        legs.add(key)
        row = dict(meta.get(key) or {})
        if side:
            row["side"] = str(side).lower()
        if name:
            row["name"] = str(name)
        if task_id:
            row["task_id"] = str(task_id)
        try:
            vol = int(volume or 0)
        except (TypeError, ValueError):
            vol = 0
        if vol > 0:
            row["volume"] = vol
        row["filled_at"] = str(filled_at or _now_iso())
        meta[key] = row
        _save_raw(path, legs, meta)
    return key


def note_from_order_record(
    record: Dict[str, Any], project_root: Optional[str] = None
) -> Optional[str]:
    if not isinstance(record, dict):
        return None
    status = str(record.get("status") or "").strip().lower()
    ev = str(record.get("event_type") or record.get("msg") or "").strip().lower()
    filled = status == "filled" or ev == "early_confirm" or str(record.get("msg") or "") == "early_confirm"
    if not filled:
        return None
    if str(record.get("status") or "").lower() == "skipped":
        return None
    return note_leg(
        record.get("stock_code") or record.get("code"),
        name=record.get("rule_name") or record.get("name") or record.get("strategy_name"),
        leg_key=record.get("leg_key"),
        side=record.get("side"),
        volume=record.get("traded_volume") or record.get("volume"),
        task_id=record.get("task_id"),
        filled_at=record.get("at") or record.get("order_time"),
        project_root=project_root,
    )


def note_from_rule_fill(
    *,
    stock_code: Any,
    rule: Optional[Dict[str, Any]] = None,
    order_rec: Optional[Dict[str, Any]] = None,
    project_root: Optional[str] = None,
) -> Optional[str]:
    """主程序图表确认成交时写入腿（与 QMT 侧互补）。"""
    rule = rule if isinstance(rule, dict) else {}
    order_rec = order_rec if isinstance(order_rec, dict) else {}
    side = str(order_rec.get("side") or "").lower()
    if not side:
        rt = str(rule.get("type") or rule.get("rule_type") or "").lower()
        side = "sell" if "sell" in rt or "clear" in rt else "buy"
    return note_leg(
        stock_code or order_rec.get("stock_code"),
        name=rule.get("name") or order_rec.get("rule_name") or order_rec.get("strategy_name"),
        leg_key=rule.get("leg_key") or order_rec.get("leg_key"),
        side=side,
        volume=order_rec.get("traded_volume")
        or order_rec.get("volume")
        or rule.get("executed_volume")
        or rule.get("volume"),
        task_id=order_rec.get("task_id"),
        filled_at=order_rec.get("at")
        or order_rec.get("order_time")
        or rule.get("executed_time"),
        project_root=project_root,
    )


def clear_code(code: Any, project_root: Optional[str] = None) -> List[str]:
    """清除某代码全部腿；返回被删 key 列表。"""
    c6 = _norm_code(code)
    if not c6:
        return []
    path = default_path(project_root)
    removed: List[str] = []
    with _LOCK:
        data = _load_raw(path)
        legs = set(data.get("legs") or [])
        meta = dict(data.get("meta") or {})
        for k in list(legs):
            if str(k).split(":", 1)[0] == c6:
                legs.discard(k)
                meta.pop(k, None)
                removed.append(k)
        if removed:
            _save_raw(path, legs, meta)
    return removed


def reconcile_with_positions(
    positions: Dict[str, Any],
    *,
    min_volume: int = 100,
    project_root: Optional[str] = None,
) -> List[str]:
    """可用 < min_volume 的代码清除腿记录。返回被清代码列表。"""
    cleared_codes: List[str] = []
    path = default_path(project_root)
    with _LOCK:
        data = _load_raw(path)
        legs = set(data.get("legs") or [])
        if not legs:
            return []
        meta = dict(data.get("meta") or {})
        pos_map: Dict[str, int] = {}
        for k, v in (positions or {}).items():
            c6 = _norm_code(k)
            if not c6:
                continue
            try:
                if isinstance(v, dict):
                    vol = int(v.get("can_use_volume") or v.get("volume") or 0)
                else:
                    vol = int(v or 0)
            except (TypeError, ValueError):
                vol = 0
            pos_map[c6] = max(pos_map.get(c6, 0), vol)
        changed = False
        for k in list(legs):
            c6 = str(k).split(":", 1)[0]
            # 仅当该代码出现在持仓快照里且可用不足时清；快照缺票不误清
            if c6 in pos_map and pos_map.get(c6, 0) < int(min_volume):
                legs.discard(k)
                meta.pop(k, None)
                if c6 not in cleared_codes:
                    cleared_codes.append(c6)
                changed = True
        if changed:
            _save_raw(path, legs, meta)
    return cleared_codes
