# -*- coding: utf-8 -*-
"""Apply Eastmoney kline JSON (from WebFetch) onto daily_cache rows."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "daily_cache"
OUT_COLS = ["date", "open", "high", "low", "close", "volume", "amount"]

CLOSE_EPS = 0.02
VOL_RATIO = 0.97


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_klines(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = (payload or {}).get("data") or {}
    out = []
    for line in data.get("klines") or []:
        parts = str(line).split(",")
        if len(parts) < 7:
            continue
        out.append(
            {
                "date": parts[0][:10],
                "open": float(parts[1]),
                "close": float(parts[2]),
                "high": float(parts[3]),
                "low": float(parts[4]),
                "volume": float(parts[5]),
                "amount": float(parts[6]),
            }
        )
    return out


def _mismatch(local: Dict[str, Any], em: Dict[str, Any]) -> bool:
    if abs(_f(local.get("close")) - _f(em.get("close"))) > CLOSE_EPS:
        return True
    v_e = _f(em.get("volume"))
    if v_e > 0 and _f(local.get("volume")) < v_e * VOL_RATIO:
        return True
    if _f(local.get("high")) + CLOSE_EPS < _f(em.get("high")):
        return True
    return False


def apply_payload(code: str, payload: Dict[str, Any], dry_run: bool = False) -> Tuple[int, List[str]]:
    path = CACHE / f"{code}.csv"
    if not path.is_file():
        return 0, ["missing_csv"]
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    by_d = {str(r.get("date") or "")[:10]: r for r in rows}
    notes = []
    n = 0
    for em in _parse_klines(payload):
        ds = em["date"]
        local = by_d.get(ds)
        if not local or not _mismatch(local, em):
            continue
        notes.append(
            f"{ds} C {_f(local.get('close'))}->{em['close']} V {_f(local.get('volume')):.0f}->{em['volume']:.0f}"
        )
        if not dry_run:
            for k in ("open", "high", "low", "close", "volume", "amount"):
                local[k] = em[k]
        n += 1
    if n and not dry_run:
        tmp = path.with_suffix(".csv.tmp")
        with tmp.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=OUT_COLS)
            w.writeheader()
            for r in rows:
                w.writerow({k: (by_d.get(str(r.get("date") or "")[:10]) or r).get(k, "") for k in OUT_COLS})
        tmp.replace(path)
    return n, notes


def main() -> int:
    # stdin: JSONL lines {"code":"600770.SH","payload":{...}}
    fixed = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        code = str(obj["code"]).upper()
        n, notes = apply_payload(code, obj["payload"], dry_run=bool(obj.get("dry_run")))
        print(code, "patched", n, "|", " ; ".join(notes) if notes else "ok")
        if n:
            fixed += 1
    print("codes_fixed", fixed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
