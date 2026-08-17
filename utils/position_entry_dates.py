# -*- coding: utf-8 -*-
"""实盘建仓日（首次买入成交日）持久化。

文件：data/position_entry_dates.json
  { "000001": "2026-08-12", ... }

规则：
- 某票从无仓到首次买入成交：若尚无建仓日则写入成交日
- 加仓不改建仓日
- 可用持仓 < 100：清除建仓日（可再次买入后重记）
- 手动/外部买入：同步持仓时补记（界面）
"""
from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_LOCK = threading.RLock()

_BUY_RULE_TYPES = {
    "single_buy",
    "best_buy",
    "grid_buy",
    "night_buy",
    "breakthrough_buy",
    "cage_buy",
    "buy",
}
_SELL_RULE_TYPES = {
    "single_sell",
    "best_sell",
    "grid_sell",
    "night_sell",
    "breakthrough_sell",
    "cage_sell",
    "scheduled_clear",
    "sell",
}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_path(project_root: Optional[str] = None) -> Path:
    root = Path(project_root) if project_root else _project_root()
    return root / "data" / "position_entry_dates.json"


def _norm_code(code: Any) -> str:
    s = str(code or "").strip().upper()
    if "." in s:
        s = s.split(".", 1)[0]
    digits = "".join(c for c in s if c.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)[-6:]


def _parse_date(val: Any) -> Optional[date]:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()[:10]
    if len(s) < 10:
        return None
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


def load_all(project_root: Optional[str] = None) -> Dict[str, str]:
    """返回 {code6: 'YYYY-MM-DD'}。"""
    path = default_path(project_root)
    with _LOCK:
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, str] = {}
        for k, v in raw.items():
            c6 = _norm_code(k)
            d = _parse_date(v)
            if c6 and d:
                out[c6] = d.isoformat()
        return out


def save_all(data: Dict[str, str], project_root: Optional[str] = None) -> None:
    path = default_path(project_root)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        clean: Dict[str, str] = {}
        for k, v in (data or {}).items():
            c6 = _norm_code(k)
            d = _parse_date(v)
            if c6 and d:
                clean[c6] = d.isoformat()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(clean, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(str(tmp), str(path))


def load_as_dates(project_root: Optional[str] = None) -> Dict[str, date]:
    return {
        c: date.fromisoformat(s)
        for c, s in load_all(project_root).items()
        if len(s) >= 10
    }


def get_entry_date(code: str, project_root: Optional[str] = None) -> Optional[date]:
    c6 = _norm_code(code)
    if not c6:
        return None
    s = load_all(project_root).get(c6)
    return _parse_date(s)


def set_entry_date(
    code: str,
    entry: Any,
    *,
    overwrite: bool = False,
    project_root: Optional[str] = None,
) -> bool:
    """写入建仓日。overwrite=False 时已有则保留。成功写入返回 True。"""
    c6 = _norm_code(code)
    d = _parse_date(entry)
    if not c6 or d is None:
        return False
    with _LOCK:
        data = load_all(project_root)
        if not overwrite and c6 in data:
            return False
        data[c6] = d.isoformat()
        save_all(data, project_root)
        return True


def ensure_entry_date_on_buy(
    code: str,
    fill_day: Optional[Any] = None,
    project_root: Optional[str] = None,
) -> bool:
    """首次买入成交：无建仓日则记 fill_day（默认今天）。"""
    return set_entry_date(
        code,
        fill_day or date.today(),
        overwrite=False,
        project_root=project_root,
    )


def clear_entry_date(code: str, project_root: Optional[str] = None) -> bool:
    c6 = _norm_code(code)
    if not c6:
        return False
    with _LOCK:
        data = load_all(project_root)
        if c6 not in data:
            return False
        del data[c6]
        save_all(data, project_root)
        return True


def reconcile_with_positions(
    positions: Dict[str, Any],
    *,
    min_volume: int = 100,
    project_root: Optional[str] = None,
) -> List[str]:
    """持仓可用 < min_volume 的代码清除建仓日，返回被清除代码列表。"""
    cleared: List[str] = []
    with _LOCK:
        data = load_all(project_root)
        if not data:
            return cleared
        for c6 in list(data.keys()):
            vol = 0
            raw = positions.get(c6)
            if raw is None:
                # 也试带后缀
                for k, v in (positions or {}).items():
                    if _norm_code(k) == c6:
                        raw = v
                        break
            try:
                if isinstance(raw, dict):
                    vol = int(raw.get("volume") or raw.get("can_use_volume") or 0)
                else:
                    vol = int(raw or 0)
            except (TypeError, ValueError):
                vol = 0
            if vol < int(min_volume):
                del data[c6]
                cleared.append(c6)
        if cleared:
            save_all(data, project_root)
    return cleared


def missing_entry_codes(
    codes: Iterable[str],
    project_root: Optional[str] = None,
) -> List[str]:
    have = load_all(project_root)
    out: List[str] = []
    for c in codes or []:
        c6 = _norm_code(c)
        if c6 and c6 not in have:
            out.append(c6)
    return out


def is_buy_rule_type(rule_type: str) -> bool:
    t = str(rule_type or "").strip().lower()
    if t in _BUY_RULE_TYPES:
        return True
    return "buy" in t and "sell" not in t


def is_sell_rule_type(rule_type: str) -> bool:
    t = str(rule_type or "").strip().lower()
    if t in _SELL_RULE_TYPES:
        return True
    return "sell" in t or t == "scheduled_clear"


def note_fill_from_order(
    *,
    stock_code: str,
    rule: Optional[Dict[str, Any]] = None,
    order_rec: Optional[Dict[str, Any]] = None,
    skip_reason: str = "",
    project_root: Optional[str] = None,
) -> Tuple[str, bool]:
    """根据订单回写更新建仓日。

    返回 (action, changed)：action in ('buy_set','sell_clear','skip','noop')。
    """
    if skip_reason:
        return ("skip", False)
    order_rec = order_rec or {}
    rule = rule or {}
    c6 = _norm_code(stock_code or order_rec.get("stock_code") or "")
    if not c6:
        return ("noop", False)

    side = str(order_rec.get("side") or order_rec.get("order_side") or "").strip().lower()
    rtype = str(rule.get("type") or order_rec.get("rule_type") or "")
    status = str(order_rec.get("status") or "").strip().lower()
    if status in ("skipped",):
        return ("skip", False)

    fill_day = _parse_date(order_rec.get("at")) or _parse_date(
        rule.get("executed_time")
    ) or date.today()

    is_buy = side in ("buy", "b", "48") or (not side and is_buy_rule_type(rtype))
    is_sell = side in ("sell", "s", "49") or (not side and is_sell_rule_type(rtype))

    if is_buy and not is_sell:
        ok = ensure_entry_date_on_buy(c6, fill_day, project_root=project_root)
        return ("buy_set", ok)

    if is_sell:
        # 卖出后若仍有仓，保留建仓日；无仓则清。调用方也可再 reconcile。
        vol = order_rec.get("can_use_volume")
        if vol is None:
            vol = order_rec.get("position_volume_after")
        try:
            if vol is not None and int(vol) < 100:
                return ("sell_clear", clear_entry_date(c6, project_root=project_root))
        except (TypeError, ValueError):
            pass
        return ("noop", False)

    return ("noop", False)
