# -*- coding: utf-8 -*-
import csv
from pathlib import Path

CACHE = Path(__file__).resolve().parents[1] / "data" / "daily_cache"
FIXES = {
    "301520.SZ": {
        "date": "2026-07-16",
        "open": "54.88",
        "high": "69.54",
        "low": "54.8",
        "close": "66.65",
        "volume": "183496.0",
    },
    "603669.SH": {
        "date": "2026-07-16",
        "open": "7.75",
        "high": "8.4",
        "low": "7.3",
        "close": "7.3",
        "volume": "936891.0",
    },
}

for stem, bar in FIXES.items():
    p = CACHE / f"{stem}.csv"
    rows = list(csv.DictReader(p.open(encoding="utf-8")))
    for r in rows:
        if r["date"] == bar["date"]:
            print(stem, "before", dict(r))
            for k in ("open", "high", "low", "close", "volume"):
                r[k] = bar[k]
            print(stem, "after", dict(r))
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["date", "open", "high", "low", "close", "volume"]
        )
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in w.fieldnames})
    print("wrote", p)
