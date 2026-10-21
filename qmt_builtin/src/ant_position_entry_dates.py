#coding:utf-8
"""QMT 内置侧：建仓日 / 末笔买入日只打策略日志，不读、不写 json。

交易系统读 ANTRES ent / lbu / posx 后再写入那两个文件。

- 买入成交：打建仓日和这笔成交日
- 本进程见过不少于 100 股、后来没了：打清除
- 启动时账上已有的仓，不补打今天
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional, Set

_SEEN_HELD = set()  # type: Set[str]


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


def _result_log():
    try:
        import ant_result_log as rl
    except ImportError:
        try:
            import qmt_builtin.ant_result_log as rl
        except ImportError:
            return None
    return rl


def _emit_entry(code: str, day: str) -> None:
    rl = _result_log()
    if rl is None or not hasattr(rl, "emit_entry_date"):
        return
    try:
        rl.emit_entry_date(code, day)
    except Exception:
        pass


def _emit_last(code: str, day: str) -> None:
    rl = _result_log()
    if rl is None or not hasattr(rl, "emit_last_buy"):
        return
    try:
        rl.emit_last_buy(code, day)
    except Exception:
        pass


def _emit_clear(code: str) -> None:
    rl = _result_log()
    if rl is None or not hasattr(rl, "emit_pos_clear"):
        return
    try:
        rl.emit_pos_clear(code)
    except Exception:
        pass


def _mark_seen(c6: str) -> None:
    if c6:
        _SEEN_HELD.add(c6)


def ensure_on_buy(code: Any, fill_day: Any = None) -> bool:
    """买入成交：打建仓日和这笔成交日。同一进程里重复打由日志模块按日并掉。"""
    c6 = _norm_code(code)
    ds = _parse_day(fill_day) or date.today().isoformat()
    if not c6:
        return False
    _mark_seen(c6)
    _emit_entry(c6, ds)
    _emit_last(c6, ds)
    return True


def clear_code(code: Any) -> bool:
    c6 = _norm_code(code)
    if not c6 or c6 not in _SEEN_HELD:
        return False
    _SEEN_HELD.discard(c6)
    _emit_clear(c6)
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
    """只根据本进程见过的持仓打清除。不打开建仓日文件，也不给旧仓补今天。"""
    pos = positions if isinstance(positions, dict) else {}
    held = set()
    for code, info in pos.items():
        c6 = _norm_code(code)
        if not c6:
            continue
        if _pos_volume(info) >= int(min_volume):
            held.add(c6)
            _mark_seen(c6)
    for c6 in list(_SEEN_HELD):
        if c6 not in held:
            _SEEN_HELD.discard(c6)
            _emit_clear(c6)


def note_from_order_record(record: Any) -> None:
    """订单记录：买入成交则打建仓日和成交日。跳过单不记。"""
    if not isinstance(record, dict):
        return
    status = str(record.get("status") or "").strip().lower()
    if status in ("skipped", "error", ""):
        return
    side = str(record.get("side") or "").strip().lower()
    ev = str(record.get("event_type") or "").strip().lower()
    filled = (
        status == "filled"
        or ev == "early_confirm"
        or str(record.get("msg") or "") == "early_confirm"
    )
    if not filled:
        return
    if side not in ("buy", "b", "48"):
        if "buy" not in ev and "buy" not in str(record.get("strategy_name") or "").lower():
            return
    if bool(record.get("buy_block_window")):
        return
    ensure_on_buy(record.get("stock_code"), record.get("at"))
