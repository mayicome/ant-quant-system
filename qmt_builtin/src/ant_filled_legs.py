#coding:utf-8
"""QMT 内置侧：已执行分支只打策略日志，不读、不写 filled_legs.json。

腿名来自会话任务上的 leg_key / 规则名。交易系统读 ANTRES leg / legx 后再落盘。
本进程没见过的旧腿，仓位卖掉时不打清除。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

_KNOWN_LEGS = (
    "OPEN50_REST",
    "OPEN50",
    "LU10",
    "MA5",
    "MA10",
    "MA20",
)

# code6 -> 本进程打过的 leg_key
_SEEN = {}  # type: Dict[str, set]


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


def _result_log():
    try:
        import ant_result_log as rl
    except ImportError:
        try:
            import qmt_builtin.ant_result_log as rl
        except ImportError:
            return None
    return rl


def _emit_one(key: str, row: Dict[str, Any]) -> None:
    rl = _result_log()
    if rl is None or not hasattr(rl, "emit_filled_leg"):
        return
    text = str(key or "")
    if ":" not in text:
        return
    code, lid = text.split(":", 1)
    row = row if isinstance(row, dict) else {}
    try:
        rl.emit_filled_leg(
            code,
            lid,
            side=row.get("side") or "",
            volume=row.get("volume") or 0,
            filled_at=row.get("filled_at") or "",
            task_id=row.get("task_id") or "",
            name=row.get("name") or "",
        )
    except Exception:
        pass


def _emit_clear(code: Any) -> None:
    rl = _result_log()
    if rl is None or not hasattr(rl, "emit_leg_clear"):
        return
    try:
        rl.emit_leg_clear(code)
    except Exception:
        pass


def _remember(key: str) -> None:
    c6 = str(key or "").split(":", 1)[0]
    if not c6 or ":" not in str(key):
        return
    bucket = _SEEN.get(c6)
    if bucket is None:
        bucket = set()
        _SEEN[c6] = bucket
    bucket.add(str(key))


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
    row = {}
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
    _remember(key)
    _emit_one(key, row)
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
    if not filled or status == "skipped":
        return None
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
    if not c6 or c6 not in _SEEN:
        return []
    removed = list(_SEEN.pop(c6, []) or [])
    if removed:
        _emit_clear(c6)
    return removed


def _pos_volume(v: Any) -> int:
    try:
        if isinstance(v, dict):
            return int(
                v.get("can_use_volume")
                or v.get("volume")
                or v.get("m_nCanUseVolume")
                or 0
            )
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def sync_clear_from_positions(positions: Any, min_volume: int = 100) -> List[str]:
    """本进程打过腿的代码，持仓不足时打清除。"""
    pos_map = {}  # type: Dict[str, int]
    if isinstance(positions, dict):
        items = positions.items()
    elif isinstance(positions, list):
        items = []
        for row in positions:
            if isinstance(row, dict):
                items.append(
                    (row.get("stock_code") or row.get("code"), row)
                )
    else:
        items = []
    for k, v in items:
        c6 = _norm_code(k)
        if not c6:
            continue
        pos_map[c6] = max(pos_map.get(c6, 0), _pos_volume(v))
    cleared = []
    for c6 in list(_SEEN.keys()):
        if pos_map.get(c6, 0) < int(min_volume):
            if clear_code(c6):
                cleared.append(c6)
    return cleared
