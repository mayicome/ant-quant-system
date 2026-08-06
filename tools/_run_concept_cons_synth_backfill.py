# -*- coding: utf-8 -*-
"""概念优先：clist 拉成份 → daily_cache 等权合成日 K → 写 concept_rank CSV。

push2his 不可用时的回退路径。行业缺失板块也可一并补齐。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.snapshot_eastmoney_board_rank import (  # noqa: E402
    _clear_proxy_env,
    _get_json,
    _session,
)
from tools._synth_em_board_hist_from_cons import (  # noqa: E402
    CONS_DIR,
    export_pending_cons,
    fill_missing_with_synth,
)
from tools.backfill_em_board_rank_from_hist import backfill_kind  # noqa: E402

CACHE = os.path.join(ROOT, "data", "eastmoney_board_rank", "_hist_cache")
START = date(2026, 6, 9)
END = date(2026, 7, 31)


def fetch_cons(session, code: str) -> list:
    params = {
        "pn": "1",
        "pz": "100",
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fs": f"b:{code}",
        "fields": "f12",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
    }
    first = _get_json(session, params, retries=6, timeout=30)
    data = first.get("data") or {}
    total = int(data.get("total") or 0)
    diff = data.get("diff") or []
    rows = list(diff.values()) if isinstance(diff, dict) else list(diff)
    members = []
    for r in rows:
        c = str(r.get("f12") or "").strip()
        if c:
            members.append(c.zfill(6)[-6:])
    pages = max(1, math.ceil(total / 100)) if total else 1
    for pn in range(2, pages + 1):
        p = dict(params)
        p["pn"] = str(pn)
        payload = _get_json(session, p, retries=6, timeout=30)
        diff = (payload.get("data") or {}).get("diff") or []
        rows = list(diff.values()) if isinstance(diff, dict) else list(diff)
        for r in rows:
            c = str(r.get("f12") or "").strip()
            if c:
                members.append(c.zfill(6)[-6:])
        time.sleep(0.25 + random.random() * 0.15)
    seen = set()
    out = []
    for m in members:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def fill_cons(kind: str, pause: float = 0.6) -> tuple[int, int]:
    path = os.path.join(CACHE, f"_pending_cons_{kind}.json")
    pending = json.load(open(path, encoding="utf-8"))["pending"]
    os.makedirs(CONS_DIR, exist_ok=True)
    session = _session()
    ok = skip = fail = 0
    streak = 0
    t0 = time.time()
    for i, item in enumerate(pending, 1):
        code = str(item["板块代码"]).strip().upper()
        outp = os.path.join(CONS_DIR, f"{kind}_{code}.json")
        if os.path.isfile(outp) and os.path.getsize(outp) > 20:
            skip += 1
            ok += 1
            continue
        try:
            members = fetch_cons(session, code)
            with open(outp, "w", encoding="utf-8") as f:
                json.dump({"code": code, "members": members}, f, ensure_ascii=False)
            ok += 1
            streak = 0
        except Exception as e:
            fail += 1
            streak += 1
            print(f"[warn] {kind} {code}: {type(e).__name__}: {e}")
            # recreate session after failures
            time.sleep(min(120, 3 * streak))
            session = _session()
            if streak >= 6:
                print("[info] hard backoff 180s")
                time.sleep(180)
                streak = 0
        if i % 20 == 0 or i == len(pending):
            print(
                f"[progress] cons {kind} {i}/{len(pending)} "
                f"ok={ok} skip={skip} fail={fail} elapsed={time.time()-t0:.0f}s"
            )
        time.sleep(pause + random.random() * 0.3)
    print(f"[done] cons {kind} ok={ok} fail={fail}")
    return ok, fail


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="concept", choices=("concept", "industry", "both"))
    ap.add_argument("--pause", type=float, default=0.55)
    ap.add_argument("--cons-only", action="store_true")
    ap.add_argument("--synth-only", action="store_true")
    ap.add_argument("--build-only", action="store_true")
    args = ap.parse_args()
    kinds = ("concept", "industry") if args.kind == "both" else (args.kind,)

    _clear_proxy_env()
    print(f"[info] start {datetime.now()} kinds={kinds}")

    for kind in kinds:
        if not args.synth_only and not args.build_only:
            export_pending_cons(kind, START, END)
            fill_cons(kind, pause=float(args.pause))
        if args.cons_only:
            continue
        if not args.build_only:
            fill_missing_with_synth(kind, START, END)
        n, dates = backfill_kind(kind, START, END, allow_network=False, write_csv=True)
        print(f"[built] {kind} days={n}")
        if dates:
            print(f"        range {dates[0]} .. {dates[-1]}")

    print(f"[done] {datetime.now()}")


if __name__ == "__main__":
    main()
