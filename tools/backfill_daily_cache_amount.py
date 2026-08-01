# -*- coding: utf-8 -*-
"""一次性：给 data/daily_cache/*.csv 补上 amount 列。

用法（项目根目录，Mini/QMT 已开）：
  python tools/backfill_daily_cache_amount.py
  python tools/backfill_daily_cache_amount.py --limit 50          # 先试 50 只
  python tools/backfill_daily_cache_amount.py --force             # 已有 amount 也重写
  python tools/backfill_daily_cache_amount.py --codes 000001.SZ,600000.SH

说明：
- 按每只股票 CSV 已有日期区间，从 xtdata 拉 open/high/low/close/volume/amount 后合并写回
- 只改本地缓存，不改大 QMT 定时同步逻辑；以后日常同步会继续带 amount
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from utils.daily_cache_reader import CACHE_DIR, to_full_stock_code

CSV_FIELDS = ("date", "open", "high", "low", "close", "volume", "amount")


def _has_amount(val: Any) -> bool:
    if val is None or str(val).strip() == "":
        return False
    try:
        return float(val) > 0.0
    except (TypeError, ValueError):
        return False


def _read_rows(path: str) -> Dict[str, Dict[str, str]]:
    rows: Dict[str, Dict[str, str]] = {}
    with open(path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            d = str(row.get("date") or "").strip()
            if d:
                rows[d] = dict(row)
    return rows


def _write_rows(path: str, rows: Dict[str, Dict[str, str]]) -> None:
    tmp = path + ".tmp"
    ordered = sorted(rows.keys())
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for d in ordered:
            row = rows[d]
            w.writerow(
                {
                    "date": d,
                    "open": row.get("open", ""),
                    "high": row.get("high", ""),
                    "low": row.get("low", ""),
                    "close": row.get("close", ""),
                    "volume": row.get("volume", ""),
                    "amount": row.get("amount", ""),
                }
            )
    os.replace(tmp, path)


def _needs_backfill(rows: Dict[str, Dict[str, str]], force: bool) -> bool:
    if force or not rows:
        return bool(rows)
    for row in rows.values():
        if not _has_amount(row.get("amount")):
            return True
    return False


def _parse_idx_date(idx: Any) -> Optional[str]:
    try:
        if isinstance(idx, (int, float)):
            return datetime.fromtimestamp(float(idx) / 1000.0).strftime("%Y-%m-%d")
        s = str(idx).replace("-", "")[:8]
        if len(s) == 8 and s.isdigit():
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    except Exception:
        return None
    return None


def _fetch_amount_map(
    xtdata: Any, code: str, start_s: str, end_s: str
) -> Dict[str, float]:
    """返回 {YYYY-MM-DD: amount}。"""
    try:
        xtdata.download_history_data(code, "1d", start_s, end_s)
    except Exception:
        pass
    try:
        raw = xtdata.get_market_data_ex(
            ["open", "high", "low", "close", "volume", "amount"],
            [code],
            period="1d",
            start_time=start_s,
            end_time=end_s,
            count=-1,
        )
    except Exception:
        return {}
    if not isinstance(raw, dict) or code not in raw:
        return {}
    df = raw[code]
    if df is None or getattr(df, "empty", True) or "amount" not in getattr(df, "columns", []):
        return {}
    out: Dict[str, float] = {}
    for idx, row in df.iterrows():
        d = _parse_idx_date(idx)
        if not d:
            continue
        try:
            amt = float(row["amount"])
        except Exception:
            continue
        if amt > 0:
            out[d] = amt
    return out


def _backfill_one(
    xtdata: Any, path: str, force: bool
) -> Tuple[str, int, str]:
    """返回 (status, filled_n, detail)。status: ok/skip/fail"""
    code = os.path.basename(path)[:-4]
    try:
        rows = _read_rows(path)
    except Exception as e:
        return "fail", 0, f"read:{e}"
    if not rows:
        return "skip", 0, "empty"
    if not _needs_backfill(rows, force):
        return "skip", 0, "already_has_amount"

    dates = sorted(rows.keys())
    start_s = dates[0].replace("-", "")
    end_s = dates[-1].replace("-", "")
    amt_map = _fetch_amount_map(xtdata, code, start_s, end_s)
    if not amt_map:
        return "fail", 0, "no_amount_from_xtdata"

    filled = 0
    for d, row in rows.items():
        if (not force) and _has_amount(row.get("amount")):
            continue
        if d in amt_map:
            row["amount"] = str(amt_map[d])
            filled += 1
    if filled <= 0:
        return "skip", 0, "no_date_overlap"
    try:
        _write_rows(path, rows)
    except Exception as e:
        return "fail", filled, f"write:{e}"
    return "ok", filled, f"{dates[0]}~{dates[-1]}"


def main() -> int:
    parser = argparse.ArgumentParser(description="一次性补齐 daily_cache 的 amount 列")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 只（0=全部）")
    parser.add_argument("--force", action="store_true", help="已有 amount 也重写")
    parser.add_argument("--codes", default="", help="逗号分隔代码，仅处理这些")
    parser.add_argument("--sleep", type=float, default=0.05, help="每只间隔秒")
    args = parser.parse_args()

    try:
        import xtquant.xtdata as xtdata

        try:
            xtdata.enable_hello = False
        except Exception:
            pass
    except Exception as e:
        print(f"[失败] 无法导入 xtdata（请先开 Mini/QMT）: {e}")
        return 1

    cache_dir = CACHE_DIR
    if not os.path.isdir(cache_dir):
        print(f"[失败] 目录不存在: {cache_dir}")
        return 1

    if args.codes.strip():
        want = {to_full_stock_code(c) for c in args.codes.split(",") if c.strip()}
        paths = [
            os.path.join(cache_dir, f"{c}.csv")
            for c in sorted(want)
            if os.path.isfile(os.path.join(cache_dir, f"{c}.csv"))
        ]
    else:
        paths = sorted(
            os.path.join(cache_dir, n)
            for n in os.listdir(cache_dir)
            if n.endswith(".csv") and "." in n[:-4]
        )

    if args.limit and args.limit > 0:
        paths = paths[: int(args.limit)]

    total = len(paths)
    print(f"[开始] 补 amount：共 {total} 只 | cache={cache_dir} | force={bool(args.force)}")
    ok = skip = fail = 0
    filled_total = 0
    t0 = time.time()
    for i, path in enumerate(paths, 1):
        code = os.path.basename(path)[:-4]
        status, filled, detail = _backfill_one(xtdata, path, bool(args.force))
        if status == "ok":
            ok += 1
            filled_total += filled
        elif status == "skip":
            skip += 1
        else:
            fail += 1
        if i % 50 == 0 or status == "fail" or i == total:
            elapsed = time.time() - t0
            print(
                f"[{i}/{total}] {code} {status} filled={filled} {detail} "
                f"| ok={ok} skip={skip} fail={fail} elapsed={elapsed:.0f}s"
            )
        if args.sleep > 0:
            time.sleep(float(args.sleep))

    print(
        f"[完成] ok={ok} skip={skip} fail={fail} "
        f"filled_rows={filled_total} elapsed={time.time() - t0:.0f}s"
    )
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
