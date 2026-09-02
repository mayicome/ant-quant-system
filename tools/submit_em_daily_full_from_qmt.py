# -*- coding: utf-8 -*-
"""将 fetch_em_progress 中的 127 只（东财前复权）改由大 QMT 拉全量不复权日线 → data/daily_full/。

大 QMT 若发现 daily_full 已有「看起来完整」的文件会直接 mark done，因此默认会先备份并删除旧 CSV，
再写入 data/data_sync_requests.json（mode=full_history），由大 QMT 内置策略消费。

用法：
  python tools/submit_em_daily_full_from_qmt.py --dry-run
  python tools/submit_em_daily_full_from_qmt.py
  python tools/submit_em_daily_full_from_qmt.py --codes 000001.SZ,600000.SH
"""
from __future__ import annotations

import argparse
import json
import shutil
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
    count_pending_sync,
    full_daily_csv_path,
    submit_full_daily_requests,
)

DAILY_FULL = ROOT / "data" / "daily_full"
PROGRESS_PATH = DAILY_FULL / "fetch_em_progress.json"
BACKUP_DIR = DAILY_FULL / "_backup_em_qfq"
MARKER = DAILY_FULL / "EM_QFQ_QMT_REFETCH.json"


def load_em_codes() -> List[str]:
    if not PROGRESS_PATH.is_file():
        raise SystemExit("缺少 %s" % PROGRESS_PATH)
    data = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    ok = [str(c).upper() for c in (data.get("ok") or []) if str(c).strip()]
    return sorted(set(ok))


def backup_and_remove_csv(codes: List[str], *, dry_run: bool) -> int:
    removed = 0
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for code in codes:
        path = Path(full_daily_csv_path(code))
        if not path.is_file():
            continue
        dest = BACKUP_DIR / path.name
        if dry_run:
            print("[dry-run] remove %s -> backup %s" % (path, dest))
            removed += 1
            continue
        if not dest.is_file():
            shutil.copy2(path, dest)
        path.unlink()
        removed += 1
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(description="127 只东财 daily_full → 大 QMT 全量不复权重拉")
    ap.add_argument("--codes", default="", help="逗号分隔；默认 fetch_em_progress ok 列表")
    ap.add_argument("--through", default="", help="截止 YYYY-MM-DD，默认今天")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写请求、不删文件")
    ap.add_argument("--keep-files", action="store_true", help="不删旧 CSV（大 QMT 可能直接 mark done）")
    ap.add_argument("--limit", type=int, default=0, help="最多提交 N 只（调试）")
    args = ap.parse_args()

    through = date.fromisoformat(args.through) if args.through else date.today()
    if args.codes.strip():
        codes = [to_full_stock_code(x.strip()) for x in args.codes.split(",") if x.strip()]
        codes = sorted({c for c in codes if c})
    else:
        codes = load_em_codes()

    if args.limit and args.limit > 0:
        codes = codes[: int(args.limit)]

    print(
        "[em→qmt] codes=%d through=%s from=%s"
        % (len(codes), through.isoformat(), FULL_HISTORY_FROM_DATE.isoformat())
    )

    if not args.keep_files:
        n_rm = backup_and_remove_csv(codes, dry_run=bool(args.dry_run))
        print("[em→qmt] removed_old_csv=%d backup=%s" % (n_rm, BACKUP_DIR))
    else:
        print("[em→qmt] keep-files：未删除旧 CSV，大 QMT 可能跳过已有文件")

    if args.dry_run:
        print("[em→qmt] dry-run，未写入 sync 请求")
        return 0

    submitted = submit_full_daily_requests(codes, through_date=through, force=True)
    pend_d, pend_t = count_pending_sync()
    marker = {
        "submitted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "through_date": through.isoformat(),
        "from_date": FULL_HISTORY_FROM_DATE.isoformat(),
        "source": "fetch_em_progress.ok",
        "codes": codes,
        "submitted": submitted,
        "removed_csv": not args.keep_files,
        "backup_dir": str(BACKUP_DIR),
        "pending_daily_after": pend_d,
        "note": "东财 qfq daily_full 改大 QMT 全量不复权重拉",
    }
    MARKER.write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("[em→qmt] submitted=%d pending_daily=%d marker=%s" % (len(submitted), pend_d, MARKER))
    print("[em→qmt] 大 QMT 在线时会按心跳消费；可用 python tools/qmt_status_monitor.py --text 查看队列")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
