# -*- coding: utf-8 -*-
"""给封单验证临时补次日日线（新浪），只补指定封板日名单。"""
from __future__ import annotations

import csv
import json
import re
import time
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
HIST = ROOT / "history_data"
DAILY = ROOT / "data" / "daily_cache"


def code6(v) -> str:
    m = re.search(r"(\d{6})", str(v or ""))
    return m.group(1) if m else ""


def suffix(c6: str) -> str:
    if c6.startswith("6"):
        return ".SH"
    if c6.startswith(("8", "4", "92")):
        return ".BJ"
    return ".SZ"


def sina_symbol(c6: str) -> str:
    if c6.startswith("6"):
        return f"sh{c6}"
    if c6.startswith(("8", "4", "92")):
        return f"bj{c6}"
    return f"sz{c6}"


def sina_klines(c6: str, datalen: int = 12) -> dict:
    sym = sina_symbol(c6)
    url = (
        "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
        f"?symbol={sym}&scale=240&ma=no&datalen={datalen}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = json.loads(r.read().decode())
    out = {}
    for row in raw or []:
        d = str(row.get("day") or "")[:10]
        if not d:
            continue
        out[d] = {
            "date": d,
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": row.get("close"),
            # 新浪 volume 多为股；daily_cache 多为手，除以100对齐常见口径
            "volume": str(float(row.get("volume") or 0) / 100.0),
        }
    return out


def upsert(c6: str, bars: dict) -> int:
    path = DAILY / f"{c6}{suffix(c6)}.csv"
    rows = {}
    if path.is_file():
        with path.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[r["date"]] = r
    n = 0
    for d, bar in bars.items():
        rows[d] = {
            "date": d,
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "volume": bar["volume"],
        }
        n += 1
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
        w.writeheader()
        for d in sorted(rows):
            w.writerow({k: rows[d][k] for k in w.fieldnames})
    return n


def main():
    seal = HIST / "封单结构_20260716.xlsx"
    df = pd.read_excel(seal)
    col = None
    for c in df.columns:
        if "代码" in str(c) or str(c).lower() == "code":
            col = c
            break
    codes = sorted({code6(v) for v in df[col].tolist() if code6(v)})
    print(f"codes={len(codes)}")
    ok = 0
    fail = []
    for i, c6 in enumerate(codes):
        try:
            bars = sina_klines(c6)
            if "2026-07-17" not in bars:
                fail.append((c6, "no 0717"))
                continue
            # 只补最近几天，避免整表被新浪口径整体覆盖
            keep = {d: bars[d] for d in bars if d >= "2026-07-15"}
            upsert(c6, keep)
            ok += 1
            print(f"  {c6} close0717={bars['2026-07-17']['close']}")
        except Exception as e:
            fail.append((c6, str(e)))
        time.sleep(0.15)
    print(f"ok={ok} fail={len(fail)}")
    for x in fail[:15]:
        print(" fail", x)


if __name__ == "__main__":
    main()
