# -*- coding: utf-8 -*-
"""查看后复权批量进度。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "daily_cache_hfq"
FULL = ROOT / "data" / "daily_full_hfq"


def main() -> None:
    prog_p = CACHE / "_hfq_bulk_progress.json"
    done_p = CACHE / "HFQ_BULK_DONE.json"
    flag = CACHE / "FORCE_HFQ_BULK"
    print("armed", flag.is_file())
    print("done_file", done_p.is_file())
    if prog_p.is_file():
        prog = json.loads(prog_p.read_text(encoding="utf-8"))
        print(
            "progress cursor=%s/%s ok=%s fail=%s skip=%s"
            % (
                prog.get("cursor"),
                len(prog.get("codes") or []),
                prog.get("ok"),
                prog.get("fail"),
                prog.get("skip"),
            )
        )
    full_n = len(list(FULL.glob("*.csv"))) if FULL.is_dir() else 0
    cache_n = len([p for p in CACHE.glob("*.csv")]) if CACHE.is_dir() else 0
    print("full_hfq csv", full_n)
    print("cache_hfq csv", cache_n)
    if done_p.is_file():
        print("DONE", done_p.read_text(encoding="utf-8")[:500])


if __name__ == "__main__":
    main()
