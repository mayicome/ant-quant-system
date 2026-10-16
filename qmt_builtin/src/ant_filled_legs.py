# -*- coding: utf-8 -*-
"""QMT 内置侧：实盘已执行分支（买/卖腿）写入 data/filled_legs.json。

不依赖主程序；大 QMT 查到成交后由 passorder / 柜台回填调用。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set

try:
    from ant_qmt_paths import DATA_DIR, PROJECT_ROOT
except ImportError:
    try:
        from qmt_builtin.ant_qmt_paths import DATA_DIR, PROJECT_ROOT
    except ImportError:
        DATA_DIR = ""
        PROJECT_ROOT = ""

_KNOWN_LEGS = (
    "OPEN50_REST",
    "OPEN50",
    "LU10",
    "MA5",
    "MA10",
    "MA20",
)


def _path() -> str:
    base = DATA_DIR or os.path.join(PROJECT_ROOT or ".", "data")
    return os.path.join(base, "filled_legs.json")


def _compat_sell_path() -> str:
    """旧马总卖策略落盘路径；卖腿同时写入，便于未改策略码时仍可读。"""
    base = DATA_DIR or os.path.join(PROJECT_ROOT or ".", "data")
    return os.path.join(base, "ma_zong1_sell_filled_legs.json")


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


_MEM = {"legs": [], "meta": {}}


def _load() -> Dict[str, Any]:
    return _MEM


def _save(legs: Iterable[str], meta: Dict[str, Any]) -> None:
    clean_legs = sorted({str(x) for x in legs if x})
    clean_meta = {k: v for k, v in (meta or {}).items() if k in set(clean_legs)}
    _MEM["legs"] = clean_legs
    _MEM["meta"] = clean_meta
    _MEM["updated_at"] = _now_iso()
    return


def _save_compat_sell(legs: Iterable[str]) -> None:
    return


def note_leg(
    code: Any,
    *,
    name: Any = None,
    leg_key: Any = None,
    side: Any = None,
    volume: Any = None,
    task_id: Any = None,
    filled_at: Any = None,
) -> Optional[str]:
    key = make_leg_key(code, name=name, leg_key=leg_key)
    if not key:
        return None
    data = _load()
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
    _save(legs, meta)
    try:
        _save_compat_sell(legs)
    except Exception:
        pass
    return key


def note_from_order_record(record: Dict[str, Any]) -> Optional[str]:
    if not isinstance(record, dict):
        return None
    status = str(record.get("status") or "").strip().lower()
    ev = str(record.get("event_type") or record.get("msg") or "").strip().lower()
    filled = (
        status == "filled"
        or ev == "early_confirm"
        or str(record.get("msg") or "") == "early_confirm"
    )
    if not filled:
        return None
    if status == "skipped":
        return None
    # 下单瞬间 status=submitted 不算；须已成
    return note_leg(
        record.get("stock_code") or record.get("code"),
        name=record.get("rule_name")
        or record.get("name")
        or record.get("strategy_name"),
        leg_key=record.get("leg_key"),
        side=record.get("side"),
        volume=record.get("traded_volume") or record.get("volume"),
        task_id=record.get("task_id"),
        filled_at=record.get("at") or record.get("order_time"),
    )


def clear_code(code: Any) -> List[str]:
    c6 = _norm_code(code)
    if not c6:
        return []
    data = _load()
    legs = set(data.get("legs") or [])
    meta = dict(data.get("meta") or {})
    removed = []
    for k in list(legs):
        if str(k).split(":", 1)[0] == c6:
            legs.discard(k)
            meta.pop(k, None)
            removed.append(k)
    if removed:
        _save(legs, meta)
        try:
            _save_compat_sell(legs)
        except Exception:
            pass
    return removed


def sync_clear_from_positions(positions: Any, min_volume: int = 100) -> List[str]:
    """持仓可用不足时清腿。positions 可为 {code: vol} 或 list[dict]。"""
    pos_map: Dict[str, int] = {}
    if isinstance(positions, dict):
        for k, v in positions.items():
            c6 = _norm_code(k)
            if not c6:
                continue
            try:
                if isinstance(v, dict):
                    vol = int(
                        v.get("can_use_volume")
                        or v.get("volume")
                        or v.get("m_nCanUseVolume")
                        or 0
                    )
                else:
                    vol = int(v or 0)
            except (TypeError, ValueError):
                vol = 0
            pos_map[c6] = max(pos_map.get(c6, 0), vol)
    elif isinstance(positions, list):
        for row in positions:
            if not isinstance(row, dict):
                continue
            c6 = _norm_code(row.get("stock_code") or row.get("code"))
            if not c6:
                continue
            try:
                vol = int(
                    row.get("can_use_volume")
                    or row.get("volume")
                    or row.get("m_nCanUseVolume")
                    or 0
                )
            except (TypeError, ValueError):
                vol = 0
            pos_map[c6] = max(pos_map.get(c6, 0), vol)

    data = _load()
    legs = set(data.get("legs") or [])
    if not legs:
        return []
    meta = dict(data.get("meta") or {})
    cleared = []
    changed = False
    for k in list(legs):
        c6 = str(k).split(":", 1)[0]
        if c6 in pos_map and pos_map.get(c6, 0) < int(min_volume):
            legs.discard(k)
            meta.pop(k, None)
            if c6 not in cleared:
                cleared.append(c6)
            changed = True
    if changed:
        _save(legs, meta)
        try:
            _save_compat_sell(legs)
        except Exception:
            pass
    return cleared


def lookup_leg_from_armed(task_id: Any, rules_path: str = "") -> Dict[str, str]:
    return {"leg_key": "", "rule_name": ""}
