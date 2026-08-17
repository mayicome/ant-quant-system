# -*- coding: utf-8 -*-
"""持仓周期「总仓位」基准（半仓用）。

定义（与产品约定一致）：
- 总仓位 = 本轮持仓周期内累计买入股数
- 只随买入增加；半仓/部分卖出不减少
- 当前持仓 volume < 100 时清零，下一轮重新累计

实盘：按持仓数量相对上次快照的增量近似买入（无成交回报时的兜底）；
回测：由引擎在买入成交时精确累加。

文件：data/position_baseline.json
  {
    "codes": {
      "600665": {"baseline": 3000, "last_volume": 2000}
    },
    "updated_at": "..."
  }
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from typing import Any, Dict, Optional

_LOCK = threading.RLock()


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _path() -> str:
    return os.path.join(_repo_root(), "data", "position_baseline.json")


def _norm_code6(code: Any) -> str:
    s = str(code or "").strip()
    if "." in s:
        s = s.split(".", 1)[0].strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    if s.isdigit():
        s = s.zfill(6)
    return s[:6] if len(s) >= 6 else (s.zfill(6) if s else "")


def load_state() -> Dict[str, Dict[str, int]]:
    """返回 code -> {baseline, last_volume}。"""
    p = _path()
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}
    codes = raw.get("codes") if isinstance(raw, dict) else None
    if not isinstance(codes, dict):
        return {}
    out: Dict[str, Dict[str, int]] = {}
    for k, v in codes.items():
        c6 = _norm_code6(k)
        if not c6 or not isinstance(v, dict):
            continue
        try:
            base = max(0, int(v.get("baseline") or 0))
        except (TypeError, ValueError):
            base = 0
        try:
            last = max(0, int(v.get("last_volume") or 0))
        except (TypeError, ValueError):
            last = 0
        out[c6] = {"baseline": base, "last_volume": last}
    return out


def save_state(state: Dict[str, Dict[str, int]]) -> None:
    p = _path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    payload = {
        "codes": {
            c: {
                "baseline": int((row or {}).get("baseline") or 0),
                "last_volume": int((row or {}).get("last_volume") or 0),
            }
            for c, row in (state or {}).items()
            if _norm_code6(c)
        },
        "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def sync_baselines_from_volumes(volume_map: Dict[str, int]) -> Dict[str, int]:
    """
    用当前总持仓 volume 刷新基准，返回 code -> baseline。

    - volume < 100：基准清零
    - volume 较上次增加：增量计入基准（视为买入）
    - volume 减少（卖出）：基准不变
    - 基准若低于当前持仓：抬到当前持仓（冷启动/丢状态兜底）
    """
    with _LOCK:
        state = load_state()
        vol_norm: Dict[str, int] = {}
        for k, v in (volume_map or {}).items():
            c6 = _norm_code6(k)
            if not c6:
                continue
            try:
                vol = max(0, int(v or 0))
            except (TypeError, ValueError):
                vol = 0
            vol_norm[c6] = max(vol_norm.get(c6, 0), vol)

        all_codes = set(state.keys()) | set(vol_norm.keys())
        out: Dict[str, int] = {}
        new_state: Dict[str, Dict[str, int]] = {}
        for c6 in all_codes:
            cur = int(vol_norm.get(c6) or 0)
            prev = state.get(c6) or {}
            try:
                base = max(0, int(prev.get("baseline") or 0))
            except (TypeError, ValueError):
                base = 0
            try:
                last = max(0, int(prev.get("last_volume") or 0))
            except (TypeError, ValueError):
                last = 0
            if cur < 100:
                base = 0
                last = 0
            else:
                if cur > last:
                    base += cur - last
                if base < cur:
                    base = cur
                last = cur
                out[c6] = int(base)
            if base > 0 or last > 0:
                new_state[c6] = {"baseline": int(base), "last_volume": int(last)}
        try:
            save_state(new_state)
        except Exception:
            pass
        return out


def bump_baseline(baseline: Dict[str, int], code_6: str, qty: int) -> None:
    """回测用：买入成交累加基准。"""
    c6 = _norm_code6(code_6)
    q = max(0, int(qty or 0))
    if not c6 or q <= 0:
        return
    baseline[c6] = int(baseline.get(c6) or 0) + q


def prune_baseline_if_flat(
    baseline: Dict[str, int],
    positions: Dict[str, Dict[str, Any]],
) -> None:
    """持仓 volume < 100 时清除基准。"""
    for c6 in list(baseline.keys()):
        pos = positions.get(c6) or {}
        try:
            vol = int((pos or {}).get("volume") or 0)
        except (TypeError, ValueError):
            vol = 0
        if vol < 100:
            baseline.pop(c6, None)


def baselines_for_strategy_params(baseline: Dict[str, int]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for k, v in (baseline or {}).items():
        c6 = _norm_code6(k)
        try:
            n = max(0, int(v or 0))
        except (TypeError, ValueError):
            n = 0
        if c6 and n >= 100:
            out[c6] = n
    return out
