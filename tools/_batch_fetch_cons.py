# -*- coding: utf-8 -*-
"""分批拉概念/行业成份股（每批默认 30 个后退出，便于断点续跑）。"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.snapshot_eastmoney_board_rank import (  # noqa: E402
    _clear_proxy_env,
    _get_json,
    _session,
)
from tools._synth_em_board_hist_from_cons import CONS_DIR  # noqa: E402

CACHE = os.path.join(ROOT, "data", "eastmoney_board_rank", "_hist_cache")


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
    first = _get_json(session, params, retries=8, timeout=35)
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
        payload = _get_json(session, p, retries=8, timeout=35)
        diff = (payload.get("data") or {}).get("diff") or []
        rows = list(diff.values()) if isinstance(diff, dict) else list(diff)
        for r in rows:
            c = str(r.get("f12") or "").strip()
            if c:
                members.append(c.zfill(6)[-6:])
        time.sleep(0.35 + random.random() * 0.2)
    seen = set()
    out = []
    for m in members:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="concept")
    ap.add_argument("--batch", type=int, default=25)
    ap.add_argument("--pause", type=float, default=0.8)
    args = ap.parse_args()
    kind = args.kind
    pending_path = os.path.join(CACHE, f"_pending_cons_{kind}.json")
    if not os.path.isfile(pending_path):
        # build from codes file + existing
        codes = json.load(open(os.path.join(CACHE, f"_codes_{kind}.json"), encoding="utf-8"))
        pending = [{"板块代码": c, "板块名称": ""} for c in codes]
    else:
        pending = json.load(open(pending_path, encoding="utf-8"))["pending"]

    os.makedirs(CONS_DIR, exist_ok=True)
    todo = []
    for item in pending:
        code = str(item["板块代码"]).strip().upper()
        outp = os.path.join(CONS_DIR, f"{kind}_{code}.json")
        if os.path.isfile(outp) and os.path.getsize(outp) > 20:
            continue
        todo.append(code)

    print(f"[info] kind={kind} remaining={len(todo)} batch={args.batch}")
    if not todo:
        print("[done] nothing to fetch")
        return

    _clear_proxy_env()
    session = _session()
    batch = todo[: max(1, int(args.batch))]
    ok = fail = 0
    for i, code in enumerate(batch, 1):
        outp = os.path.join(CONS_DIR, f"{kind}_{code}.json")
        try:
            members = fetch_cons(session, code)
            with open(outp, "w", encoding="utf-8") as f:
                json.dump({"code": code, "members": members}, f, ensure_ascii=False)
            ok += 1
            print(f"[ok] {i}/{len(batch)} {code} members={len(members)}")
        except Exception as e:
            fail += 1
            print(f"[fail] {i}/{len(batch)} {code}: {type(e).__name__}: {e}")
            time.sleep(5)
            session = _session()
        time.sleep(float(args.pause) + random.random() * 0.4)

    left = len(todo) - ok  # approximate
    # recount
    left2 = 0
    for code in todo:
        outp = os.path.join(CONS_DIR, f"{kind}_{code}.json")
        if not (os.path.isfile(outp) and os.path.getsize(outp) > 20):
            left2 += 1
    print(f"[batch-done] ok={ok} fail={fail} remaining≈{left2}")


if __name__ == "__main__":
    main()
