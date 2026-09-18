# -*- coding: utf-8 -*-
import csv
import json
from pathlib import Path

CACHE = Path(r"D:\蚂蚁量化系统\data\daily_cache")
COLS = ["date", "open", "high", "low", "close", "volume", "amount"]

# Eastmoney field order in kline: date,open,close,high,low,volume,amount,...
PATCHES = {
    "600770.SH": [
        "2026-09-07,5.08,5.59,5.59,5.08,420103,225266753.00",
        "2026-09-08,5.73,5.90,6.15,5.64,1754746,1043311164.00",
    ],
    "600880.SH": [
        "2026-09-07,4.14,4.16,4.18,4.09,198105,82229231.00",
        "2026-09-08,4.58,4.58,4.58,4.58,147054,67350576.00",
    ],
    "603938.SH": [
        "2026-09-07,40.24,40.39,40.94,39.51,121979,491579094.00",
        "2026-09-08,44.43,44.43,44.43,44.43,41477,184284088.00",
    ],
    "600737.SH": [
        "2026-09-07,15.97,16.51,17.17,15.82,1472410,2420438942.00",
        "2026-09-08,18.08,18.16,18.16,17.75,974233,1766589039.00",
    ],
    "601949.SH": [
        "2026-09-07,6.11,6.41,6.41,5.88,1295658,794651255.00",
        "2026-09-08,6.80,7.05,7.05,6.66,544869,378918384.00",
    ],
}


def parse_line(line: str) -> dict:
    p = line.split(",")
    return {
        "date": p[0][:10],
        "open": p[1],
        "close": p[2],
        "high": p[3],
        "low": p[4],
        "volume": p[5],
        "amount": p[6],
    }


def apply_code(code: str, lines: list) -> int:
    path = CACHE / f"{code}.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    by = {str(r["date"])[:10]: r for r in rows}
    n = 0
    for line in lines:
        em = parse_line(line)
        loc = by.get(em["date"])
        if not loc:
            continue
        before = (loc.get("close"), loc.get("high"), loc.get("volume"))
        for k in ("open", "high", "low", "close", "volume", "amount"):
            loc[k] = em[k]
        after = (loc.get("close"), loc.get("high"), loc.get("volume"))
        if before != after:
            print(f"{code} {em['date']} {before} -> {after}")
            n += 1
    if n:
        tmp = path.with_suffix(".csv.tmp")
        with tmp.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS)
            w.writeheader()
            for r in rows:
                d = str(r["date"])[:10]
                rr = by.get(d, r)
                w.writerow({k: rr.get(k, "") for k in COLS})
        tmp.replace(path)
    return n


def main() -> None:
    total = 0
    for code, lines in PATCHES.items():
        total += apply_code(code, lines)
    print("total_patched_bars", total)
    # verify 600770
    rows = list(csv.DictReader((CACHE / "600770.SH.csv").open(encoding="utf-8")))
    for r in rows:
        if str(r["date"])[:10] == "2026-09-07":
            print("verify 600770 09-07", dict(r))


if __name__ == "__main__":
    main()
