# -*- coding: utf-8 -*-
"""武装后复权大批量拉取：创建 FORCE_HFQ_BULK，可选重置进度。

用法：
  python tools/arm_hfq_bulk.py
  python tools/arm_hfq_bulk.py --reset
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "daily_cache_hfq"
FULL = ROOT / "data" / "daily_full_hfq"
FLAG = CACHE / "FORCE_HFQ_BULK"
PROGRESS = CACHE / "_hfq_bulk_progress.json"
DONE = CACHE / "HFQ_BULK_DONE.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="清进度/DONE 后重拉")
    args = ap.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    FULL.mkdir(parents=True, exist_ok=True)
    if args.reset:
        for p in (PROGRESS, DONE):
            if p.is_file():
                p.unlink()
                print("removed", p)
    FLAG.write_text("", encoding="utf-8")
    print("armed", FLAG)
    print("full_dir", FULL)
    print("cache_dir", CACHE)
    print("下一步: sync_qmt_gbk + deploy_to_qmt，并重启/保持大 QMT 模型交易运行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
