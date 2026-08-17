# -*- coding: utf-8 -*-
"""QMT 内置侧：实盘建仓日（不依赖外部主程序）。

写入与主程序相同的文件：data/position_entry_dates.json
  { "000001": "2026-08-12", ... }

- 持仓可用>=100 且尚无建仓日 → 记今天（覆盖系统成交与手动买入）
- 可用<100 或已无持仓 → 清除
- 订单 status=filled 且 side=buy → 记成交日（不加仓覆盖）
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime
from typing import Any, Dict, Optional

try:
    from ant_qmt_paths import DATA_DIR, PROJECT_ROOT
except ImportError:
    try:
        from qmt_builtin.ant_qmt_paths import DATA_DIR, PROJECT_ROOT
    except ImportError:
        DATA_DIR = ""
        PROJECT_ROOT = ""


def _entry_path() -> str:
    base = DATA_DIR or os.path.join(PROJECT_ROOT or ".", "data")
    return os.path.join(base, "position_entry_dates.json")


def _norm_code(code: Any) -> str:
    s = str(code or "").strip().upper()
    if "." in s:
        s = s.split(".", 1)[0]
    digits = "".join(c for c in s if c.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)[-6:]


def _parse_day(val: Any) -> Optional[str]:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val.date().isoformat()
    if isinstance(val, date):
        return val.isoformat()
    s = str(val).strip()[:10]
    if len(s) >= 10:
        return s
    return None


def _load() -> Dict[str, str]:
    path = _entry_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        c6 = _norm_code(k)
        ds = _parse_day(v)
        if c6 and ds:
            out[c6] = ds
    return out


def _save(data: Dict[str, str]) -> None:
    path = _entry_path()
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent)
        except Exception:
            return
    clean = {}
    for k, v in (data or {}).items():
        c6 = _norm_code(k)
        ds = _parse_day(v)
        if c6 and ds:
            clean[c6] = ds
    try:
        fd, tmp = tempfile.mkstemp(prefix="ped_", suffix=".json", dir=parent or None)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(clean, f, ensure_ascii=False, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp, path)
        except Exception:
            try:
                os.remove(tmp)
            except Exception:
                pass
            raise
    except Exception:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(clean, f, ensure_ascii=False, indent=2, sort_keys=True)
                f.write("\n")
        except Exception:
            pass


def ensure_on_buy(code: Any, fill_day: Any = None) -> bool:
    """首次买入：无建仓日则写入。"""
    c6 = _norm_code(code)
    ds = _parse_day(fill_day) or date.today().isoformat()
    if not c6:
        return False
    data = _load()
    if c6 in data:
        return False
    data[c6] = ds
    _save(data)
    return True


def clear_code(code: Any) -> bool:
    c6 = _norm_code(code)
    if not c6:
        return False
    data = _load()
    if c6 not in data:
        return False
    del data[c6]
    _save(data)
    return True


def _pos_volume(info: Any) -> int:
    if isinstance(info, dict):
        for k in ("can_use_volume", "volume", "m_nCanUseVolume", "可用数量", "持仓量"):
            if info.get(k) is not None:
                try:
                    return int(info.get(k) or 0)
                except (TypeError, ValueError):
                    pass
        return 0
    try:
        return int(info or 0)
    except (TypeError, ValueError):
        return 0


def sync_from_positions(positions: Any, min_volume: int = 100) -> None:
    """按 QMT 持仓快照维护建仓日（交易时即使未开主程序也会更新）。"""
    pos = positions if isinstance(positions, dict) else {}
    today = date.today().isoformat()
    data = _load()
    held = set()
    changed = False
    for code, info in pos.items():
        c6 = _norm_code(code)
        if not c6:
            continue
        vol = _pos_volume(info)
        if vol >= int(min_volume):
            held.add(c6)
            if c6 not in data:
                data[c6] = today
                changed = True
        else:
            if c6 in data:
                del data[c6]
                changed = True
    for c6 in list(data.keys()):
        if c6 not in held:
            del data[c6]
            changed = True
    if changed:
        _save(data)


def note_from_order_record(record: Any) -> None:
    """订单记录：买入成交则记建仓日；跳过单不记。"""
    if not isinstance(record, dict):
        return
    status = str(record.get("status") or "").strip().lower()
    if status in ("skipped", "error", ""):
        return
    side = str(record.get("side") or "").strip().lower()
    ev = str(record.get("event_type") or "").strip().lower()
    # 明确成交，或 early_confirm
    filled = status == "filled" or ev == "early_confirm" or str(record.get("msg") or "") == "early_confirm"
    if not filled:
        return
    if side not in ("buy", "b", "48"):
        # 无 side 时看事件名
        if "buy" not in ev and "buy" not in str(record.get("strategy_name") or "").lower():
            return
    if bool(record.get("buy_block_window")):
        return
    ensure_on_buy(record.get("stock_code"), record.get("at"))
