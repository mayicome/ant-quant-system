# -*- coding: utf-8 -*-
"""本地把 data/ticks/{日}/{代码}.pkl 转成 .parquet（不下载、不连 QMT）。

安全约定：
- 只处理「有 pkl、无可用 parquet」的代码
- 先写 parquet，读回校验行数后，才删 pkl
- 校验失败 / 不完整 tick：保留 pkl，不删

用法（项目根目录）：
  python tools/convert_tick_pkl_to_parquet.py --days 20260701
  python tools/convert_tick_pkl_to_parquet.py --from 20260701 --to 20260727
  python tools/convert_tick_pkl_to_parquet.py --from 20260701 --to 20260727 --dry-run
  python tools/convert_tick_pkl_to_parquet.py --scan-july   # 仅统计 7 月 pkl-only
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Iterable, List, Optional, Sequence, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from utils.tick_data_cache import (  # noqa: E402
    _read_parquet_file,
    _write_parquet_file,
    is_full_day_ticks,
    prepare_tick_for_disk,
    prepare_tick_for_use,
    project_root,
    tick_cache_path,
    tick_day_dir,
)


def _list_day_dirs(from_day: Optional[str], to_day: Optional[str], days: Sequence[str]) -> List[str]:
    ticks = os.path.join(project_root(), "data", "ticks")
    if days:
        out = []
        for d in days:
            ds = str(d).strip().replace("-", "")[:8]
            if len(ds) == 8 and ds.isdigit() and os.path.isdir(os.path.join(ticks, ds)):
                out.append(ds)
        return sorted(set(out))
    if not os.path.isdir(ticks):
        return []
    all_days = sorted(
        d
        for d in os.listdir(ticks)
        if len(d) == 8 and d.isdigit() and os.path.isdir(os.path.join(ticks, d))
    )
    if from_day:
        fs = str(from_day).strip().replace("-", "")[:8]
        all_days = [d for d in all_days if d >= fs]
    if to_day:
        ts = str(to_day).strip().replace("-", "")[:8]
        all_days = [d for d in all_days if d <= ts]
    return all_days


def _pkl_only_codes(day_s: str) -> List[str]:
    dpath = tick_day_dir(day_s)
    pq = set()
    pkl = set()
    for name in os.listdir(dpath):
        if name.startswith("_"):
            continue
        base, ext = os.path.splitext(name)
        if len(base) != 6 or not base.isdigit():
            continue
        path = os.path.join(dpath, name)
        try:
            if os.path.getsize(path) <= 32:
                continue
        except OSError:
            continue
        if ext.lower() == ".parquet":
            pq.add(base)
        elif ext.lower() == ".pkl":
            pkl.add(base)
    return sorted(pkl - pq)


def _verify_parquet(path: str, expect_rows: int) -> bool:
    if not os.path.isfile(path) or os.path.getsize(path) <= 32:
        return False
    df = _read_parquet_file(path)
    if df is None or len(df) == 0:
        return False
    if expect_rows > 0 and len(df) < max(1, int(expect_rows * 0.95)):
        return False
    return True


def convert_one(code6: str, day_s: str, dry_run: bool = False) -> Tuple[str, str]:
    """返回 (status, detail)。status: ok|skip|fail|dry。"""
    import pandas as pd

    pkl_path = os.path.join(tick_day_dir(day_s), code6 + ".pkl")
    pq_path = tick_cache_path(code6, day_s)
    if not os.path.isfile(pkl_path) or os.path.getsize(pkl_path) <= 32:
        return "skip", "no_pkl"
    if os.path.isfile(pq_path) and os.path.getsize(pq_path) > 32:
        return "skip", "parquet_exists"

    if dry_run:
        return "dry", "would_convert"

    try:
        raw = pd.read_pickle(pkl_path)
    except Exception as e:
        return "fail", "read_pkl:%s" % e

    data = prepare_tick_for_use(raw)
    if data is None or len(data) == 0:
        return "fail", "prepare_empty"
    if not is_full_day_ticks(data):
        return "fail", "not_full_day"

    disk_df = prepare_tick_for_disk(data)
    if disk_df is None or len(disk_df) == 0:
        return "fail", "disk_empty"

    expect_rows = len(disk_df)
    os.makedirs(os.path.dirname(pq_path), exist_ok=True)
    if not _write_parquet_file(disk_df, pq_path):
        return "fail", "write_parquet"

    if not _verify_parquet(pq_path, expect_rows):
        try:
            if os.path.isfile(pq_path):
                os.remove(pq_path)
        except Exception:
            pass
        return "fail", "verify_failed"

    try:
        os.remove(pkl_path)
    except Exception as e:
        return "ok", "parquet_ok_pkl_remain:%s" % e
    return "ok", "converted_rows=%d" % expect_rows


def scan_summary(days: Iterable[str]) -> None:
    print("day | total_codes | parquet | pkl_only | note")
    for day in days:
        dpath = tick_day_dir(day)
        if not os.path.isdir(dpath):
            continue
        pq = set()
        pkl = set()
        for name in os.listdir(dpath):
            if name.startswith("_"):
                continue
            base, ext = os.path.splitext(name)
            if len(base) != 6 or not base.isdigit():
                continue
            path = os.path.join(dpath, name)
            try:
                if os.path.getsize(path) <= 32:
                    continue
            except OSError:
                continue
            if ext.lower() == ".parquet":
                pq.add(base)
            elif ext.lower() == ".pkl":
                pkl.add(base)
        only = pkl - pq
        note = "pkl-heavy" if len(only) >= 50 else ("clean" if not only else "few-pkl")
        print(
            "%s | %5d | %7d | %8d | %s"
            % (day, len(pq | pkl), len(pq), len(only), note)
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="本地 pkl→parquet（无下载）")
    ap.add_argument("--days", nargs="*", default=[], help="指定交易日 YYYYMMDD")
    ap.add_argument("--from", dest="from_day", default="", help="起始日")
    ap.add_argument("--to", dest="to_day", default="", help="结束日")
    ap.add_argument("--scan-july", action="store_true", help="只统计 202607 各日 pkl-only")
    ap.add_argument("--dry-run", action="store_true", help="只列将转换数量，不写盘")
    ap.add_argument("--limit", type=int, default=0, help="最多转换 N 个（调试）")
    ap.add_argument("--keep-fail-list", action="store_true", help="写出失败列表到 tick_full_sync")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.scan_july:
        days = _list_day_dirs("20260701", "20260731", [])
        scan_summary(days)
        return 0

    days = _list_day_dirs(args.from_day or None, args.to_day or None, args.days)
    if not days:
        print("无匹配交易日目录")
        return 1

    t0 = time.time()
    tot_ok = tot_fail = tot_skip = tot_dry = 0
    fails: List[str] = []
    for day in days:
        codes = _pkl_only_codes(day)
        print("[%s] pkl-only=%d dry_run=%s" % (day, len(codes), args.dry_run))
        n = 0
        for c6 in codes:
            if args.limit and (tot_ok + tot_fail + tot_dry) >= args.limit:
                break
            st, detail = convert_one(c6, day, dry_run=args.dry_run)
            n += 1
            if st == "ok":
                tot_ok += 1
            elif st == "dry":
                tot_dry += 1
            elif st == "skip":
                tot_skip += 1
            else:
                tot_fail += 1
                fails.append("%s %s %s" % (day, c6, detail))
            if n % 100 == 0:
                print(
                    "  ... %s progress %d/%d ok=%d fail=%d"
                    % (day, n, len(codes), tot_ok, tot_fail)
                )
        if args.limit and (tot_ok + tot_fail + tot_dry) >= args.limit:
            print("达到 --limit=%d，停止" % args.limit)
            break

    elapsed = time.time() - t0
    print(
        "DONE ok=%d fail=%d skip=%d dry=%d elapsed=%.1fs"
        % (tot_ok, tot_fail, tot_skip, tot_dry, elapsed)
    )
    if fails[:20]:
        print("fail samples:")
        for line in fails[:20]:
            print("  ", line)
    if args.keep_fail_list and fails:
        out_dir = os.path.join(project_root(), "data", "tick_full_sync")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, "pkl_convert_fail.txt")
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(fails) + "\n")
        print("fail list ->", out)
    return 0 if tot_fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
