# -*- coding: utf-8 -*-
"""代码→选股日持久化（跨买/卖策略股票池）。

文件：data/code_selection_dates.json — { "600778": "2026-11-03", ... }

用途：
- 买池成交/删除导致 prune selection_date_by_code 时，把被裁掉的选股日落到本库，避免卖池同步持仓后 LU10 等腿缺选股日
- ensure / 同步持仓 / 手动编辑时读写本库；**不要**用建仓日（position_entry_dates）冒充选股日
- 用户手改选股日以手改为准，并回写本库（overwrite=True）
"""
from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

_LOCK = threading.RLock()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_path(project_root: Optional[str] = None) -> Path:
    root = Path(project_root) if project_root else _project_root()
    return root / "data" / "code_selection_dates.json"


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


def get_selection_date(code: str, project_root: Optional[str] = None) -> Optional[str]:
    c6 = _norm_code(code)
    if not c6:
        return None
    return load_all(project_root).get(c6)


def upsert_many(
    dates: Dict[str, Any],
    *,
    overwrite: bool = True,
    project_root: Optional[str] = None,
) -> int:
    """批量写入选股日。overwrite=False 时已有则保留。返回新写入/覆盖条数。"""
    if not dates:
        return 0
    n = 0
    with _LOCK:
        data = load_all(project_root)
        for k, v in (dates or {}).items():
            c6 = _norm_code(k)
            d = _parse_date(v)
            if not c6 or d is None:
                continue
            iso = d.isoformat()
            if not overwrite and c6 in data and data[c6]:
                continue
            if data.get(c6) == iso:
                continue
            data[c6] = iso
            n += 1
        if n:
            save_all(data, project_root)
    return n


def set_selection_date(
    code: str,
    sel_day: Any,
    *,
    overwrite: bool = True,
    project_root: Optional[str] = None,
) -> bool:
    """写入单只选股日。sel_day 为空则删除。成功变更返回 True。"""
    c6 = _norm_code(code)
    if not c6:
        return False
    raw = str(sel_day or "").strip()
    if raw in ("", "—", "-", "无", "None", "nan"):
        return clear_selection_date(c6, project_root=project_root)
    d = _parse_date(sel_day)
    if d is None:
        return False
    with _LOCK:
        data = load_all(project_root)
        if not overwrite and c6 in data and data[c6]:
            return False
        iso = d.isoformat()
        if data.get(c6) == iso:
            return False
        data[c6] = iso
        save_all(data, project_root)
        return True


def clear_selection_date(code: str, project_root: Optional[str] = None) -> bool:
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


def archive_before_prune(
    current_map: Dict[str, Any],
    keep_codes: Iterable[str],
    *,
    project_root: Optional[str] = None,
) -> int:
    """prune 前：把即将被裁掉（及仍保留）的选股日写入持久库。返回写入条数。"""
    keep = {_norm_code(c) for c in (keep_codes or []) if _norm_code(c)}
    to_save: Dict[str, str] = {}
    for k, v in (current_map or {}).items():
        c6 = _norm_code(k)
        d = _parse_date(v)
        if c6 and d:
            # 全部归档：保留在池的也刷新，被裁掉的尤其需要
            to_save[c6] = d.isoformat()
    if not to_save:
        return 0
    # 被裁掉的必须 overwrite；池内已有也刷新
    return upsert_many(to_save, overwrite=True, project_root=project_root)
