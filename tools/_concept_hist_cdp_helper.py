# -*- coding: utf-8 -*-
"""概念板块日 K：浏览器 JSONP 批次写入缓存后 build CSV。

配合 Cursor browser CDP：
1. 在 quote.eastmoney.com 页面注入 window.__emRun
2. 每批 __emRun(N) 后 dump ACC → 本脚本 ingest
3. 全部完成后 --build
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.backfill_em_board_rank_from_hist import (  # noqa: E402
    CACHE_DIR,
    _klines_to_df,
    _save_hist_df,
    backfill_kind,
)

LOG_DIR = r"C:\Users\Administrator\.cursor\browser-logs"


def _latest_cdp() -> str:
    files = glob.glob(os.path.join(LOG_DIR, "cdp-response-Runtime.evaluate-*.json"))
    if not files:
        raise SystemExit("no cdp dump")
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]


def _extract(obj):
    val = obj
    if isinstance(val, dict) and "result" in val:
        val = val["result"]
    if isinstance(val, dict) and "value" in val:
        val = val["value"]
    if isinstance(val, str):
        return json.loads(val)
    return val


def ingest_path(path: str, kind: str = "concept") -> int:
    obj = json.load(open(path, encoding="utf-8"))
    payload = _extract(obj)
    if isinstance(payload, dict) and "acc" in payload:
        kind = str(payload.get("kind") or kind)
        acc = payload.get("acc") or {}
    elif isinstance(payload, dict) and "items" in payload:
        kind = str(payload.get("kind") or kind)
        acc = {it["code"]: it.get("klines") for it in payload.get("items") or []}
    else:
        raise SystemExit(f"unknown payload keys: {list(payload)[:10] if isinstance(payload, dict) else type(payload)}")
    n = 0
    for code, klines in acc.items():
        code = str(code).strip().upper()
        df = _klines_to_df(klines or [])
        if df.empty:
            continue
        _save_hist_df(df, kind, code)
        n += 1
    print(f"ingested={n} kind={kind} file={os.path.basename(path)}")
    return n


def count_cached(kind: str) -> int:
    n = 0
    for name in os.listdir(CACHE_DIR):
        if name.startswith(f"{kind}_BK") and name.endswith((".parquet", ".csv", ".json")):
            # skip synth markers
            if name.endswith(".synth.json"):
                continue
            n += 1
    # unique by code
    codes = set()
    for name in os.listdir(CACHE_DIR):
        if not name.startswith(f"{kind}_BK"):
            continue
        if name.endswith(".synth.json"):
            continue
        code = name[len(kind) + 1 :].split(".")[0]
        if code.startswith("BK"):
            codes.add(code)
    return len(codes)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingest-latest", action="store_true")
    ap.add_argument("--ingest", default="")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--kind", default="concept")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    os.makedirs(CACHE_DIR, exist_ok=True)
    if args.status:
        codes_path = os.path.join(CACHE_DIR, f"_codes_{args.kind}.json")
        total = len(json.load(open(codes_path, encoding="utf-8"))) if os.path.isfile(codes_path) else "?"
        print(f"cached={count_cached(args.kind)} total={total}")
        return
    if args.ingest_latest:
        ingest_path(_latest_cdp(), args.kind)
        return
    if args.ingest:
        ingest_path(args.ingest, args.kind)
        return
    if args.build:
        n, dates = backfill_kind(
            args.kind,
            date(2026, 6, 9),
            date(2026, 7, 31),
            allow_network=False,
            write_csv=True,
        )
        print(f"built days={n} first={dates[:3] if dates else []} last={dates[-3:] if dates else []}")
        return
    ap.print_help()


if __name__ == "__main__":
    main()
