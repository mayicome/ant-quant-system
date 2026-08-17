# -*- coding: utf-8 -*-
"""清理 daily_cache 中的未来假K线，并纠正 data_sync_requests 的未来 through_date。

用法:
  python tools/cleanup_future_daily_bars.py
  python tools/cleanup_future_daily_bars.py --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "daily_cache"
REQ = ROOT / "data" / "data_sync_requests.json"
FIELDS = ["date", "open", "high", "low", "close", "volume", "amount"]


def _parse_d(s: str):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _body(row: dict) -> tuple:
    return tuple(str(row.get(k) or "") for k in FIELDS[1:])


def clean_csv(path: Path, today: date, dry: bool) -> tuple[int, int]:
    """返回 (删除未来行数, 删除末尾复制行数)。"""
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        return 0, 0
    if not rows:
        return 0, 0

    kept = []
    dropped_future = 0
    for row in rows:
        d = _parse_d(row.get("date") or "")
        if d is None:
            continue
        if d > today:
            dropped_future += 1
            continue
        kept.append(row)

    # 若曾写入未来假K，常伴随「末日 OHLCV 原样复制」到次日；去掉尾部完全相同的复制行
    dropped_dup = 0
    if dropped_future > 0:
        while len(kept) >= 2 and _body(kept[-1]) == _body(kept[-2]):
            kept.pop()
            dropped_dup += 1

    if dropped_future == 0 and dropped_dup == 0:
        return 0, 0
    if dry:
        return dropped_future, dropped_dup

    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in kept:
            w.writerow({k: row.get(k, "") for k in FIELDS})
    tmp.replace(path)
    return dropped_future, dropped_dup


def clean_requests(today: date, dry: bool) -> int:
    if not REQ.exists():
        return 0
    data = json.loads(REQ.read_text(encoding="utf-8"))
    daily = data.get("daily") or {}
    n = 0
    today_s = today.isoformat()
    for code, meta in list(daily.items()):
        if not isinstance(meta, dict):
            continue
        th = _parse_d(meta.get("through_date") or "")
        if th is None or th <= today:
            continue
        n += 1
        if dry:
            continue
        meta["through_date"] = today_s
        # 曾按未来 through 标 done 的，改回 pending 以便按今日重拉真K
        if str(meta.get("status") or "") == "done":
            meta["status"] = "pending"
            meta["retries"] = 0
        meta["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        meta["note"] = "clamped_future_through"
    if n and not dry:
        REQ.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    today = date.today()
    print("today=", today.isoformat(), "dry=", args.dry_run)

    files = sorted(CACHE.glob("*.csv"))
    touched = 0
    fut_rows = 0
    dup_rows = 0
    for p in files:
        if p.name in ("sync_miss_codes.json",):
            continue
        a, b = clean_csv(p, today, args.dry_run)
        if a or b:
            touched += 1
            fut_rows += a
            dup_rows += b
    req_n = clean_requests(today, args.dry_run)
    print(
        "csv_touched=%d future_rows_removed=%d trailing_dup_rows_removed=%d "
        "requests_clamped=%d"
        % (touched, fut_rows, dup_rows, req_n)
    )


if __name__ == "__main__":
    main()
