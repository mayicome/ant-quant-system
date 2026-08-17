#coding:gbk
"""QMT 内置侧：实盘已执行分支（买/卖腿）写入 data/filled_legs.json。

不依赖主程序；大 QMT 查到成交后由 passorder / 柜台回填调用。
"""
from __future__ import annotations

import json
import os
import tempfile
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


def _load() -> Dict[str, Any]:
    path = _path()
    if not os.path.isfile(path):
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
    out_legs = []
    if isinstance(legs, list):
        out_legs = [str(x) for x in legs if x]
    elif isinstance(legs, dict):
        out_legs = [str(k) for k, v in legs.items() if v]
    return {"legs": out_legs, "meta": dict(meta)}


def _save(legs: Iterable[str], meta: Dict[str, Any]) -> None:
    path = _path()
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent)
        except Exception:
            return
    clean_legs = sorted({str(x) for x in legs if x})
    clean_meta = {k: v for k, v in (meta or {}).items() if k in set(clean_legs)}
    payload = {
        "legs": clean_legs,
        "meta": clean_meta,
        "updated_at": _now_iso(),
    }
    try:
        fd, tmp = tempfile.mkstemp(prefix="fl_", suffix=".json", dir=parent or None)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp, path)
        except Exception:
            try:
                os.remove(tmp)
            except Exception:
                pass
    except Exception:
        pass


def _save_compat_sell(legs: Iterable[str]) -> None:
    """同步卖腿到旧文件，供马总卖策略 _load_state_legs 读取。"""
    sell_legs = []
    for k in legs:
        s = str(k or "")
        if not s or ":" not in s:
            continue
        lid = s.split(":", 1)[1].upper()
        if any(x in lid for x in ("OPEN50", "LU10", "破MA20", "末日")):
            sell_legs.append(s)
    path = _compat_sell_path()
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent)
        except Exception:
            return
    # 与旧文件合并，避免冲掉策略侧其它写入
    existing = set()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            old = raw.get("legs") if isinstance(raw, dict) else raw
            if isinstance(old, list):
                existing |= set(str(x) for x in old if x)
            elif isinstance(old, dict):
                existing |= set(str(k) for k, v in old.items() if v)
        except Exception:
            pass
    existing |= set(sell_legs)
    payload = {
        "legs": sorted(existing),
        "updated_at": _now_iso(),
    }
    try:
        fd, tmp = tempfile.mkstemp(prefix="mz_", suffix=".json", dir=parent or None)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp, path)
        except Exception:
            try:
                os.remove(tmp)
            except Exception:
                pass
    except Exception:
        pass


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
    """从 rules_armed 按 task_id 取 leg_key / rule_name。"""
    tid = str(task_id or "").strip()
    out = {"leg_key": "", "rule_name": ""}
    if not tid:
        return out
    path = rules_path
    if not path:
        base = DATA_DIR or os.path.join(PROJECT_ROOT or ".", "data")
        path = os.path.join(base, "rules_armed.json")
    if not os.path.isfile(path):
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return out
    tasks = []
    if isinstance(raw, dict):
        tasks = raw.get("tasks") or []
    if not isinstance(tasks, list):
        return out
    for t in tasks:
        if not isinstance(t, dict):
            continue
        if str(t.get("task_id") or "").strip() != tid:
            continue
        meta = t.get("metadata") if isinstance(t.get("metadata"), dict) else {}
        out["leg_key"] = str(t.get("leg_key") or meta.get("leg_key") or "").strip()
        out["rule_name"] = str(
            meta.get("rule_name") or t.get("strategy_name") or ""
        ).strip()
        break
    return out
