# -*- coding: utf-8 -*-
"""盘后步骤本地完成记录（按交易日）。

路径：data/post_market_step_status.json
结构：
{
  "2026-09-09": {
    "wechat_draft": {
      "ok": true,
      "finished_at": "2026-09-09T18:32:01",
      "detail": "..."
    }
  }
}
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

ROOT = Path(__file__).resolve().parents[1]
STATUS_PATH = ROOT / "data" / "post_market_step_status.json"


def _day_key(asof: Union[date, str]) -> str:
    if isinstance(asof, date):
        return asof.strftime("%Y-%m-%d")
    s = str(asof or "").strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


def load_status() -> Dict[str, Any]:
    try:
        if not STATUS_PATH.is_file():
            return {}
        with STATUS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_status(data: Dict[str, Any]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(
        prefix="post_market_step_status_",
        suffix=".json",
        dir=str(STATUS_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(raw)
            f.write("\n")
        os.replace(tmp, STATUS_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def mark_step_finished(
    asof: Union[date, str],
    step_id: str,
    *,
    ok: bool = True,
    detail: str = "",
    finished_at: Optional[datetime] = None,
) -> None:
    """写入某交易日某步骤完成记录。"""
    sid = str(step_id or "").strip()
    if not sid:
        return
    day = _day_key(asof)
    if not day:
        return
    ts = finished_at or datetime.now()
    data = load_status()
    day_map = data.get(day)
    if not isinstance(day_map, dict):
        day_map = {}
    day_map[sid] = {
        "ok": bool(ok),
        "finished_at": ts.strftime("%Y-%m-%d %H:%M:%S"),
        "detail": str(detail or "")[:500],
    }
    data[day] = day_map
    try:
        _save_status(data)
    except Exception as e:
        print(f"[post_market_step_status] 写入失败: {e}")


def get_step_record(asof: Union[date, str], step_id: str) -> Optional[Dict[str, Any]]:
    day = _day_key(asof)
    data = load_status()
    day_map = data.get(day)
    if not isinstance(day_map, dict):
        return None
    rec = day_map.get(str(step_id or "").strip())
    return rec if isinstance(rec, dict) else None


def get_step_finished_at(asof: Union[date, str], step_id: str) -> str:
    rec = get_step_record(asof, step_id)
    if not rec:
        return ""
    return str(rec.get("finished_at") or "").strip()


def is_step_marked_ok(asof: Union[date, str], step_id: str) -> bool:
    rec = get_step_record(asof, step_id)
    return bool(rec and rec.get("ok"))
