# -*- coding: utf-8 -*-
"""一次性：向大 QMT 提交全 A「上市以来全量日线」请求 → data/daily_full/。

不直连 xtdata；只写 data/data_sync_requests.json，由大 QMT 内置策略消费。
与 rolling daily_cache（约 2025 起）分离。

用法：
  python tools/submit_full_daily_once.py
  python tools/submit_full_daily_once.py --dry-run
  python tools/submit_full_daily_once.py --limit 50
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.daily_cache_reader import to_full_stock_code  # noqa: E402
from utils.data_sync_request import (  # noqa: E402
    FULL_HISTORY_FROM_DATE,
    _full_daily_ready,
    count_pending_sync,
    submit_full_daily_requests,
)

ALL_A_CSV = ROOT / "data" / "all_a_stocks.csv"
MARKER = ROOT / "data" / "daily_full" / "FULL_HISTORY_ONCE.json"


def list_universe_codes() -> List[str]:
    out: List[str] = []
    seen = set()
    if not ALL_A_CSV.is_file():
        raise SystemExit("缺少 %s" % ALL_A_CSV)
    with ALL_A_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        code_k = fields[0] if fields else "code"
        for row in reader:
            raw = str(row.get(code_k) or "").strip()
            full = to_full_stock_code(raw)
            if not full or full in seen:
                continue
            # 只要沪深 A；北交所也可拉，但宽度脉冲主用沪深
            code6, mkt = full.split(".", 1)
            if mkt == "BJ":
                continue
            if mkt == "SH" and code6.startswith(("000", "399")):
                continue
            if mkt == "SZ" and code6.startswith("399"):
                continue
            seen.add(full)
            out.append(full)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="一次性提交全量日线 → 大 QMT → daily_full")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写请求")
    ap.add_argument("--limit", type=int, default=0, help="最多提交 N 只（调试）")
    ap.add_argument("--include-bj", action="store_true", help="包含北交所")
    ap.add_argument("--through", default="", help="截止日期 YYYY-MM-DD，默认今天")
    args = ap.parse_args()

    through = date.fromisoformat(args.through) if args.through else date.today()
    codes = list_universe_codes()
    if args.include_bj:
        # 重新读含 BJ
        codes = []
        seen = set()
        with ALL_A_CSV.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fields = list(reader.fieldnames or [])
            code_k = fields[0] if fields else "code"
            for row in reader:
                full = to_full_stock_code(str(row.get(code_k) or "").strip())
                if full and full not in seen:
                    seen.add(full)
                    codes.append(full)

    print(
        "[once] universe=%d through=%s from=%s"
        % (len(codes), through.isoformat(), FULL_HISTORY_FROM_DATE.isoformat())
    )

    ready = 0
    need: List[str] = []
    for i, c in enumerate(codes, 1):
        if i % 1000 == 0:
            print("[once] scanned %d/%d ready=%d need=%d" % (i, len(codes), ready, len(need)))
        if _full_daily_ready(c, through):
            ready += 1
        else:
            need.append(c)

    if args.limit and args.limit > 0:
        need = need[: int(args.limit)]
        print("[once] limit -> need=%d" % len(need))

    print("[once] already_ready=%d need_submit=%d" % (ready, len(need)))
    if args.dry_run:
        print("[once] dry-run，未写入请求")
        return 0

    submitted = submit_full_daily_requests(need, through_date=through)
    pend_d, pend_t = count_pending_sync()
    marker = {
        "submitted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "through_date": through.isoformat(),
        "from_date": FULL_HISTORY_FROM_DATE.isoformat(),
        "universe": len(codes),
        "already_ready": ready,
        "need": len(need),
        "submitted": len(submitted),
        "pending_daily_after": pend_d,
        "pending_tick_after": pend_t,
        "note": "one-shot full_history → data/daily_full via big QMT",
    }
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("[once] submitted=%d pending_daily=%d" % (len(submitted), pend_d))
    print("[once] marker", MARKER)
    print("[once] 大 QMT 在线时会按心跳消费；可用 python tools/qmt_status_monitor.py --text 查看队列")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
