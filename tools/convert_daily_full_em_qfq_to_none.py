# -*- coding: utf-8 -*-
"""将 daily_full 中东财前复权(qfq) 日线重拉为不复权，与 QMT daily_cache 口径一致。

默认识别：data/daily_full/fetch_em_progress.json 的 ok 列表（曾由 fetch_daily_full_from_em 拉取）。

用法：
  # 查看待转换数量
  python tools/convert_daily_full_em_qfq_to_none.py --scan-only

  # 试跑 5 只
  python tools/convert_daily_full_em_qfq_to_none.py --limit 5

  # 全部转换（会先备份到 data/daily_full/_backup_qfq/）
  python tools/convert_daily_full_em_qfq_to_none.py

  # 指定代码
  python tools/convert_daily_full_em_qfq_to_none.py --codes 000001.SZ,600000.SH

  # 与 daily_cache 比对，找出疑似前复权（不限于 progress ok）
  python tools/convert_daily_full_em_qfq_to_none.py --detect --scan-only
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fetch_daily_full_from_em import (  # noqa: E402
    DAILY_CACHE,
    DAILY_FULL,
    OUT_COLS,
    PROGRESS_PATH,
    fetch_hist_em,
)
from utils.daily_cache_reader import to_full_stock_code  # noqa: E402

BACKUP_DIR = DAILY_FULL / "_backup_qfq"
CONVERTED_FLAG = DAILY_FULL / "em_converted_to_none.json"


def _load_progress() -> Dict[str, Any]:
    if not PROGRESS_PATH.is_file():
        return {"ok": [], "fail": {}, "converted_none": []}
    try:
        data = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": [], "fail": {}, "converted_none": []}
    data.setdefault("converted_none", [])
    return data


def _save_progress(prog: Dict[str, Any]) -> None:
    DAILY_FULL.mkdir(parents=True, exist_ok=True)
    prog["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    PROGRESS_PATH.write_text(json.dumps(prog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_converted() -> Set[str]:
    if not CONVERTED_FLAG.is_file():
        return set()
    try:
        data = json.loads(CONVERTED_FLAG.read_text(encoding="utf-8"))
        return set(data.get("codes") or [])
    except Exception:
        return set()


def _save_converted(codes: Set[str]) -> None:
    DAILY_FULL.mkdir(parents=True, exist_ok=True)
    payload = {
        "adjust": "none",
        "converted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "codes": sorted(codes),
    }
    CONVERTED_FLAG.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _codes_from_progress(prog: Dict[str, Any]) -> List[str]:
    converted = set(prog.get("converted_none") or []) | _load_converted()
    ok = [str(c).upper() for c in (prog.get("ok") or []) if str(c).strip()]
    return sorted({c for c in ok if c not in converted})


def _close_diff_vs_cache(full_code: str, *, min_overlap: int = 20) -> Optional[float]:
    """重叠段 close 平均绝对差；None 表示无法比对。"""
    full_path = DAILY_FULL / f"{full_code}.csv"
    cache_path = DAILY_CACHE / f"{full_code}.csv"
    if not full_path.is_file() or not cache_path.is_file():
        return None
    try:
        full = pd.read_csv(full_path, usecols=["date", "close"])
        cache = pd.read_csv(cache_path, usecols=["date", "close"])
    except Exception:
        return None
    full["date"] = pd.to_datetime(full["date"], errors="coerce")
    cache["date"] = pd.to_datetime(cache["date"], errors="coerce")
    m = full.merge(cache, on="date", suffixes=("_full", "_cache")).dropna()
    if len(m) < min_overlap:
        return None
    diff = (m["close_full"] - m["close_cache"]).abs()
    return float(diff.mean())


def detect_qfq_codes(*, diff_threshold: float = 0.05) -> List[Tuple[str, float]]:
    """与 daily_cache 比对，找出疑似前复权的 daily_full 文件。"""
    out: List[Tuple[str, float]] = []
    for path in sorted(DAILY_FULL.glob("*.csv")):
        code = path.stem.upper()
        if code.count(".") != 1:
            continue
        diff = _close_diff_vs_cache(code)
        if diff is not None and diff >= diff_threshold:
            out.append((code, diff))
    return sorted(out, key=lambda x: -x[1])


def save_replace(path: Path, df: pd.DataFrame) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df[OUT_COLS].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date"]).sort_values("date")
    out = out.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    out.to_csv(path, index=False, encoding="utf-8-sig")
    return int(len(out))


def backup_file(path: Path, backup_dir: Path) -> None:
    if not path.is_file():
        return
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / path.name
    if not dest.is_file():
        shutil.copy2(path, dest)


def process_one(
    full_code: str,
    *,
    start: str,
    end: str,
    sleep_s: float,
    backup_dir: Optional[Path],
) -> Tuple[str, bool, str]:
    out_path = DAILY_FULL / f"{full_code}.csv"
    try:
        if backup_dir is not None:
            backup_file(out_path, backup_dir)
        df = fetch_hist_em(full_code, start=start, end=end, adjust="", session=None)
        time.sleep(max(0.0, sleep_s))
        if df.empty:
            return full_code, False, "empty_response"
        n = save_replace(out_path, df)
        diff_after = _close_diff_vs_cache(full_code)
        if diff_after is not None and diff_after >= 0.05:
            return full_code, False, f"verify_fail mean_diff={diff_after:.4f} rows={n}"
        return full_code, True, f"rows={n}"
    except Exception as e:
        return full_code, False, f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser(description="daily_full 东财前复权 → 不复权重拉")
    ap.add_argument("--start", default="19900101", help="开始 YYYYMMDD")
    ap.add_argument("--end", default="", help="结束 YYYYMMDD，默认今天")
    ap.add_argument("--codes", default="", help="逗号分隔代码；默认 progress ok 列表")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 只")
    ap.add_argument("--workers", type=int, default=1, help="并发（建议 1～3）")
    ap.add_argument("--sleep", type=float, default=0.25, help="每只请求后休眠秒")
    ap.add_argument("--retries", type=int, default=3, help="失败重试")
    ap.add_argument("--scan-only", action="store_true", help="只统计，不写入")
    ap.add_argument("--detect", action="store_true", help="用 daily_cache 检测疑似 qfq（可单独 --scan-only）")
    ap.add_argument("--diff-threshold", type=float, default=0.05, help="检测：与 cache 平均 close 差阈值")
    ap.add_argument("--no-backup", action="store_true", help="不备份原 qfq 文件")
    args = ap.parse_args()

    end_s = args.end or datetime.now().strftime("%Y%m%d")
    backup_dir = None if args.no_backup else BACKUP_DIR

    if args.detect:
        detected = detect_qfq_codes(diff_threshold=float(args.diff_threshold))
        print("[detect] suspected_qfq=%d threshold=%.4f" % (len(detected), args.diff_threshold))
        for code, diff in detected[:20]:
            print("  %s mean_close_diff=%.4f" % (code, diff))
        if len(detected) > 20:
            print("  ... +%d more" % (len(detected) - 20))
        if args.scan_only and not args.codes.strip():
            return 0
        if not args.codes.strip():
            codes = [c for c, _ in detected]
        else:
            codes = [to_full_stock_code(x.strip()) for x in args.codes.split(",") if x.strip()]
    elif args.codes.strip():
        codes = [to_full_stock_code(x.strip()) for x in args.codes.split(",") if x.strip()]
    else:
        prog = _load_progress()
        codes = _codes_from_progress(prog)
        converted = set(prog.get("converted_none") or []) | _load_converted()
        print(
            "[scan] progress_ok=%d already_converted=%d todo=%d"
            % (len(prog.get("ok") or []), len(converted), len(codes))
        )

    codes = [c for c in codes if c]
    if args.limit and args.limit > 0:
        codes = codes[: int(args.limit)]

    if args.scan_only:
        print("[scan-only] would_convert=%d backup=%s" % (len(codes), backup_dir or "disabled"))
        for c in codes[:30]:
            diff = _close_diff_vs_cache(c)
            tag = ("diff=%.4f" % diff) if diff is not None else "no_cache_overlap"
            print("  %s %s" % (c, tag))
        if len(codes) > 30:
            print("  ... +%d more" % (len(codes) - 30))
        return 0

    if not codes:
        print("[done] nothing to convert")
        return 0

    prog = _load_progress()
    converted_set = set(prog.get("converted_none") or []) | _load_converted()
    ok_n = 0
    fail_n = 0
    fail_map: Dict[str, str] = {}

    def _run_with_retry(code: str) -> Tuple[str, bool, str]:
        last = ""
        for i in range(max(1, int(args.retries))):
            c, ok, msg = process_one(
                code,
                start=args.start,
                end=end_s,
                sleep_s=float(args.sleep),
                backup_dir=backup_dir,
            )
            if ok:
                return c, True, msg
            last = msg
            time.sleep(0.5 * (i + 1))
        return code, False, last

    workers = max(1, int(args.workers))
    done = 0
    if workers == 1:
        for code in codes:
            c, ok, msg = _run_with_retry(code)
            done += 1
            if ok:
                ok_n += 1
                converted_set.add(c)
                prog.setdefault("converted_none", [])
                if c not in prog["converted_none"]:
                    prog["converted_none"].append(c)
            else:
                fail_n += 1
                fail_map[c] = msg
            if done % 10 == 0 or done == len(codes):
                print("[%d/%d] ok=%d fail=%d last=%s %s" % (done, len(codes), ok_n, fail_n, c, msg))
                _save_progress(prog)
                _save_converted(converted_set)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_run_with_retry, code): code for code in codes}
            for fut in as_completed(futs):
                c, ok, msg = fut.result()
                done += 1
                if ok:
                    ok_n += 1
                    converted_set.add(c)
                    prog.setdefault("converted_none", [])
                    if c not in prog["converted_none"]:
                        prog["converted_none"].append(c)
                else:
                    fail_n += 1
                    fail_map[c] = msg
                if done % 10 == 0 or done == len(codes):
                    print("[%d/%d] ok=%d fail=%d last=%s %s" % (done, len(codes), ok_n, fail_n, c, msg))
                    _save_progress(prog)
                    _save_converted(converted_set)

    prog["converted_none"] = sorted(set(prog.get("converted_none") or []) | converted_set)
    if fail_map:
        prog.setdefault("convert_fail", {}).update(fail_map)
    _save_progress(prog)
    _save_converted(converted_set)
    print(
        "[done] ok=%d fail=%d backup=%s flag=%s"
        % (ok_n, fail_n, backup_dir or "none", CONVERTED_FLAG)
    )
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
