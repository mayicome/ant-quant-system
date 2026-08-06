# -*- coding: utf-8 -*-
"""拉取全部缺失板块成份股，并用 daily_cache 等权合成日 K，再重建榜单。"""
from __future__ import annotations

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


def _sessions():
    """直连优先；失败时可换系统代理 session。"""
    import requests

    direct = _session()
    proxied = requests.Session()  # trust_env=True → 127.0.0.1:7078
    return [direct, proxied]


def fetch_cons(sessions, code: str, pause: float = 0.25) -> list:
    members = []
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
    last_err = None
    first = None
    for sess in sessions:
        try:
            first = _get_json(sess, params, retries=3, timeout=25)
            break
        except Exception as e:
            last_err = e
            time.sleep(1.5)
    if first is None:
        raise RuntimeError(last_err)
    data = first.get("data") or {}
    total = int(data.get("total") or 0)
    diff = data.get("diff") or []
    rows = list(diff.values()) if isinstance(diff, dict) else list(diff)
    for r in rows:
        c = str(r.get("f12") or "").strip()
        if c:
            members.append(c.zfill(6)[-6:])
    pages = max(1, math.ceil(total / 100)) if total else 1
    sess = sessions[0]
    for pn in range(2, pages + 1):
        params = dict(params)
        params["pn"] = str(pn)
        payload = _get_json(sess, params, retries=4, timeout=25)
        diff = (payload.get("data") or {}).get("diff") or []
        rows = list(diff.values()) if isinstance(diff, dict) else list(diff)
        for r in rows:
            c = str(r.get("f12") or "").strip()
            if c:
                members.append(c.zfill(6)[-6:])
        time.sleep(pause + random.random() * 0.1)
    seen = set()
    out = []
    for m in members:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def fill_cons(kind: str) -> int:
    path = os.path.join(CACHE, f"_pending_cons_{kind}.json")
    pending = json.load(open(path, encoding="utf-8"))["pending"]
    os.makedirs(CONS_DIR, exist_ok=True)
    sessions = _sessions()
    ok = 0
    fail = 0
    streak_fail = 0
    for i, item in enumerate(pending, 1):
        code = str(item["板块代码"]).strip().upper()
        outp = os.path.join(CONS_DIR, f"{kind}_{code}.json")
        if os.path.isfile(outp) and os.path.getsize(outp) > 20:
            ok += 1
            continue
        try:
            members = fetch_cons(sessions, code, pause=0.3)
            with open(outp, "w", encoding="utf-8") as f:
                json.dump({"code": code, "members": members}, f, ensure_ascii=False)
            ok += 1
            streak_fail = 0
        except Exception as e:
            fail += 1
            streak_fail += 1
            print(f"[warn] cons {kind} {code}: {e}")
            sleep_s = min(180, 5 * streak_fail)
            print(f"[info] backoff {sleep_s}s (streak_fail={streak_fail})")
            time.sleep(sleep_s)
            if streak_fail >= 8:
                print("[warn] too many consecutive fails, pause 300s")
                time.sleep(300)
                streak_fail = 0
                sessions = _sessions()
        if i % 20 == 0 or i == len(pending):
            print(f"[progress] cons {kind} {i}/{len(pending)} ok={ok} fail={fail}")
        time.sleep(0.35 + random.random() * 0.2)
    print(f"[done] cons {kind} ok={ok} fail={fail}")
    return ok


def main() -> None:
    _clear_proxy_env()
    start = date(2026, 6, 9)
    end = date(2026, 7, 31)
    print(f"[info] start {datetime.now()}")
    for kind in ("industry", "concept"):
        export_pending_cons(kind, start, end)
        fill_cons(kind)
        fill_missing_with_synth(kind, start, end)
        backfill_kind(kind, start, end, allow_network=False, write_csv=True)
    print(f"[done] {datetime.now()}")


if __name__ == "__main__":
    main()
