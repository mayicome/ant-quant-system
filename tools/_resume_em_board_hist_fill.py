# -*- coding: utf-8 -*-
"""轮询 push2his；恢复后慢速补齐缺失板块日 K 并 build CSV。"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.backfill_em_board_rank_from_hist import (  # noqa: E402
    CACHE_DIR,
    _klines_to_df,
    _save_hist_df,
    backfill_kind,
)
from datetime import date

PROBE = "BK0433"


def probe_once() -> bool:
    params = {
        "secid": f"90.{PROBE}",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "0",
        "beg": "20260701",
        "end": "20260705",
        "smplmt": "10000",
        "lmt": "1000000",
    }
    qs = urllib.parse.urlencode(params)
    url = "https://91.push2his.eastmoney.com/api/qt/stock/kline/get?" + qs
    # try system default first (may use 127.0.0.1:7078), then direct
    openers = [
        urllib.request.build_opener(),
        urllib.request.build_opener(urllib.request.ProxyHandler({})),
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"https://quote.eastmoney.com/bk/90.{PROBE}.html",
    }
    for opener in openers:
        try:
            req = urllib.request.Request(url, headers=headers)
            with opener.open(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            kl = ((data.get("data") or {}).get("klines") or [])
            if kl:
                print(f"[probe] OK klines={len(kl)} at {datetime.now()}")
                return True
        except Exception as e:
            print(f"[probe] fail {type(e).__name__}: {e}")
    return False


def fetch_one(code: str, beg: str = "20260601", end: str = "20260805"):
    params = {
        "secid": f"90.{code}",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "0",
        "beg": beg,
        "end": end,
        "smplmt": "10000",
        "lmt": "1000000",
    }
    qs = urllib.parse.urlencode(params)
    url = "https://91.push2his.eastmoney.com/api/qt/stock/kline/get?" + qs
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"https://quote.eastmoney.com/bk/90.{code}.html",
    }
    last_err = None
    for opener in (
        urllib.request.build_opener(),
        urllib.request.build_opener(urllib.request.ProxyHandler({})),
    ):
        try:
            req = urllib.request.Request(url, headers=headers)
            with opener.open(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            return _klines_to_df(((data.get("data") or {}).get("klines") or []))
        except Exception as e:
            last_err = e
            time.sleep(0.5)
    raise RuntimeError(str(last_err))


def pending_codes(kind: str):
    path = os.path.join(CACHE_DIR, f"_codes_{kind}.json")
    codes = json.load(open(path, encoding="utf-8"))
    out = []
    for code in codes:
        hit = False
        for ext in ("parquet", "csv", "json"):
            p = os.path.join(CACHE_DIR, f"{kind}_{code}.{ext}")
            if os.path.isfile(p) and os.path.getsize(p) > 50:
                hit = True
                break
        if not hit:
            out.append(code)
    return out


def fill_kind(kind: str, pause: float = 0.35) -> int:
    codes = pending_codes(kind)
    print(f"[fill] {kind} pending={len(codes)}")
    ok = 0
    fail = 0
    for i, code in enumerate(codes, 1):
        try:
            df = fetch_one(code)
            if df is None or df.empty:
                fail += 1
            else:
                _save_hist_df(df, kind, code)
                ok += 1
        except Exception as e:
            fail += 1
            print(f"[warn] {kind} {code}: {e}")
            # 连续失败则暂停更久
            if fail >= 5 and ok == 0:
                print("[warn] consecutive fails, sleep 120s")
                time.sleep(120)
                if not probe_once():
                    print("[warn] still blocked, abort fill")
                    break
                fail = 0
        if i % 20 == 0:
            print(f"[progress] {kind} {i}/{len(codes)} ok={ok} fail={fail}")
        time.sleep(pause)
    print(f"[done-fill] {kind} ok={ok} fail={fail}")
    return ok


def main() -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    print(f"[info] waiting for push2his recovery… {datetime.now()}")
    while True:
        if probe_once():
            break
        print(f"[info] blocked, sleep 90s … {datetime.now()}")
        time.sleep(90)

    fill_kind("industry", pause=0.4)
    fill_kind("concept", pause=0.4)

    print("[info] building CSVs from cache…")
    start = date(2026, 6, 9)
    end = date(2026, 7, 31)
    for kind in ("industry", "concept"):
        backfill_kind(kind, start, end, allow_network=False, write_csv=True)
    print("[done] all")


if __name__ == "__main__":
    main()
