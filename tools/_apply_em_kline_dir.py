# -*- coding: utf-8 -*-
"""Apply saved Eastmoney kline JSON files under data/_em_kline_repair/raw/ onto daily_cache."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "daily_cache"
RAW = ROOT / "data" / "\_em_kline_repair" / "raw"
# fix path - avoid escaped underscore weirdness
RAW = ROOT / "data" / "_em_kline_repair" / "raw"
COLS = ["date", "open", "high", "low", "close", "volume", "amount"]
CLOSE_EPS = 0.02
VOL_RATIO = 0.97


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse_klines(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    data = (payload or {}).get("data") or {}
    out: List[Dict[str, str]] = []
    for line in data.get("klines") or []:
        p = str(line).split(",")
        if len(p) < 7:
            continue
        out.append(
            {
                "date": p[0][:10],
                "open": p[1],
                "close": p[2],
                "high": p[3],
                "low": p[4],
                "volume": p[5],
                "amount": p[6],
            }
        )
    return out


def mismatch(local: Dict[str, Any], em: Dict[str, str]) -> bool:
    if abs(_f(local.get("close")) - _f(em.get("close"))) > CLOSE_EPS:
        return True
    v_e = _f(em.get("volume"))
    if v_e > 0 and _f(local.get("volume")) < v_e * VOL_RATIO:
        return True
    if _f(em.get("high")) > 0 and _f(local.get("high")) + CLOSE_EPS < _f(em.get("high")):
        return True
    if abs(_f(local.get("open")) - _f(em.get("open"))) > CLOSE_EPS:
        return True
    if abs(_f(local.get("low")) - _f(em.get("low"))) > CLOSE_EPS:
        return True
    return False


def apply_one(code: str, payload: Dict[str, Any]) -> Tuple[int, List[str]]:
    path = CACHE / f"{code}.csv"
    if not path.is_file():
        return 0, ["missing"]
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    by = {str(r.get("date") or "")[:10]: r for r in rows}
    notes: List[str] = []
    n = 0
    for em in parse_klines(payload):
        loc = by.get(em["date"])
        if not loc or not mismatch(loc, em):
            continue
        notes.append(
            "%s C %s->%s V %.0f->%.0f"
            % (
                em["date"],
                loc.get("close"),
                em["close"],
                _f(loc.get("volume")),
                _f(em.get("volume")),
            )
        )
        for k in ("open", "high", "low", "close", "volume", "amount"):
            loc[k] = em[k]
        n += 1
    if n:
        tmp = path.with_suffix(".csv.tmp")
        with tmp.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS)
            w.writeheader()
            for r in rows:
                d = str(r.get("date") or "")[:10]
                rr = by.get(d, r)
                w.writerow({k: rr.get(k, "") for k in COLS})
        tmp.replace(path)
    return n, notes


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    files = sorted(RAW.glob("*.json"))
    if not files:
        print("no raw json in", RAW)
        return 1
    codes_fixed = 0
    bars = 0
    for fp in files:
        code = fp.stem.upper()
        try:
            payload = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:
            print(code, "json_err", e)
            continue
        n, notes = apply_one(code, payload)
        if n:
            codes_fixed += 1
            bars += n
            print(code, "patched", n, "|", " ; ".join(notes[:4]))
        else:
            print(code, "ok")
    print("DONE codes_fixed=%d bars=%d files=%d" % (codes_fixed, bars, len(files)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
