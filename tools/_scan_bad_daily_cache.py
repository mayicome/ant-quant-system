# -*- coding: utf-8 -*-
"""扫描 daily_cache 中疑似残缺 K 线（O=H=L=C 且量远小于历史中位数）。"""
from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "daily_cache"


def main() -> None:
    files = sorted(CACHE.glob("*.csv"))
    print(f"csv files: {len(files)}")

    bad_tiny = []
    flat_all = 0
    read_err = 0

    for fp in files:
        try:
            rows = list(csv.DictReader(fp.open(encoding="utf-8")))
        except Exception:
            read_err += 1
            continue
        if len(rows) < 5:
            continue

        vols = []
        for r in rows:
            try:
                vols.append(float(r.get("volume") or 0))
            except Exception:
                vols.append(0.0)
        hist = [v for v in vols[:-1] if v > 0]
        med = statistics.median(hist[-20:]) if hist else 0.0

        for i, r in enumerate(rows):
            try:
                o = float(r["open"])
                h = float(r["high"])
                l = float(r["low"])
                c = float(r["close"])
                v = float(r.get("volume") or 0)
            except Exception:
                continue
            if o <= 0 or c <= 0:
                continue
            flat = abs(o - h) < 1e-9 and abs(h - l) < 1e-9 and abs(l - c) < 1e-9
            if not flat:
                continue
            flat_all += 1
            # 量远小于近期中位数：像「只写了开盘一笔」
            if med > 0 and v > 0 and v < med * 0.05:
                bad_tiny.append(
                    {
                        "code": fp.stem,
                        "date": r.get("date") or "",
                        "ohlc": o,
                        "vol": v,
                        "med20": med,
                        "is_last": i == len(rows) - 1,
                    }
                )

    print(f"flat O=H=L=C bars (any): {flat_all}")
    print(f"flat + vol < 5% of med20: {len(bad_tiny)}")
    print(f"read errors: {read_err}")

    recent = [x for x in bad_tiny if str(x["date"]) >= "2026-07-01"]
    print(f"suspicious since 2026-07-01: {len(recent)}")
    by_date: dict = defaultdict(list)
    for x in recent:
        by_date[x["date"]].append(x["code"])
    for d in sorted(by_date):
        print(f"  {d}: {len(by_date[d])} codes")

    last_bad = [
        x
        for x in bad_tiny
        if x["is_last"] and str(x["date"]) >= "2026-07-10"
    ]
    print(f"\nLAST-bar suspicious since 07-10 (最影响昨收): {len(last_bad)}")
    for x in sorted(last_bad, key=lambda z: (z["date"], z["code"]))[:50]:
        print(
            f"  {x['code']:12s} {x['date']}  px={x['ohlc']:.4g}  "
            f"vol={x['vol']:.0f}  med20={x['med20']:.0f}"
        )
    if len(last_bad) > 50:
        print(f"  ... +{len(last_bad) - 50} more")

    # 按股票统计近期可疑条数
    by_code = defaultdict(int)
    for x in recent:
        by_code[x["code"]] += 1
    top = sorted(by_code.items(), key=lambda kv: -kv[1])[:20]
    if top:
        print("\nTop codes by suspicious bars since 07-01:")
        for code, n in top:
            print(f"  {code}: {n}")


if __name__ == "__main__":
    main()
