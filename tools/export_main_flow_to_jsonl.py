# -*- coding: utf-8 -*-
"""将「个股主力净流入」CSV 导出为智能体用日分片 JSONL。

输出目录：data/cos/main_flow/
文件名：{YYYYMMDD}.main_flow.jsonl

仅导出「基本全 A」样本日（默认行数 ≥ 4000）。
早期仅「净流入≥约3000万」截断层不导出，避免智能体误判为全市场。

用法：
  python tools/export_main_flow_to_jsonl.py
  python tools/export_main_flow_to_jsonl.py --dry-run
  python tools/export_main_flow_to_jsonl.py --min-rows 4000
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.main_force_inflow_path import (  # noqa: E402
    list_flow_file_dates,
    resolve_flow_csv_path,
)

OUT_DIR = ROOT / "data" / "cos" / "main_flow"
# 全市场约 5200+；截断层通常 <800。4000 作稳妥分界。
DEFAULT_MIN_FULL_ROWS = 4000
_NAME_RE = re.compile(r"个股主力净流入_(\d{8})\.csv$", re.IGNORECASE)


def _norm_header(name: Any) -> str:
    s = str(name or "").replace("\r", "").replace("\n", "").strip()
    s = re.sub(r"\s+", "", s)
    return s


def _json_safe(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, str)):
        return v
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    if isinstance(v, datetime):
        if v.hour or v.minute or v.second:
            return v.strftime("%Y-%m-%d %H:%M:%S")
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, pd.Timestamp):
        if pd.isna(v):
            return None
        if v.hour or v.minute or v.second or v.microsecond:
            return v.strftime("%Y-%m-%d %H:%M:%S")
        return v.strftime("%Y-%m-%d")
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    try:
        import numpy as np

        if isinstance(v, np.generic):
            if isinstance(v, np.bool_):
                return bool(v)
            if isinstance(v, np.integer):
                return int(v)
            if isinstance(v, np.floating):
                fv = float(v)
                if math.isnan(fv) or math.isinf(fv):
                    return None
                return fv
    except Exception:
        pass
    return str(v)


def _zfill_code(v: Any) -> Any:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none"):
        return None
    if s.isdigit() and len(s) <= 6:
        return s.zfill(6)
    try:
        f = float(s)
        if f == int(f) and 0 <= int(f) < 1000000:
            return str(int(f)).zfill(6)
    except (TypeError, ValueError):
        pass
    return s


def _count_data_rows(path: Path) -> int:
    try:
        with path.open("rb") as f:
            return max(0, sum(1 for _ in f) - 1)
    except Exception:
        return 0


def discover_full_market_flow_files(
    *,
    history_dir: str = "history_data",
    min_rows: int = DEFAULT_MIN_FULL_ROWS,
) -> List[Tuple[str, Path, int]]:
    """返回 [(YYYYMMDD, csv_path, nrows)]，仅行数达标的全日。"""
    out: List[Tuple[str, Path, int]] = []
    for ymd in list_flow_file_dates(history_dir):
        p = resolve_flow_csv_path(ymd, history_dir)
        if not p:
            continue
        path = Path(p)
        n = _count_data_rows(path)
        if n < int(min_rows):
            continue
        out.append((ymd, path, n))
    return out


def _row_to_obj(row: pd.Series, *, trade_ymd: str) -> Dict[str, Any]:
    obj: Dict[str, Any] = {
        "trade_date": f"{trade_ymd[0:4]}-{trade_ymd[4:6]}-{trade_ymd[6:8]}",
        "trade_date_ymd": trade_ymd,
    }
    for col, val in row.items():
        key = _norm_header(col)
        if not key or key.startswith("Unnamed"):
            continue
        if key in ("代码", "股票代码"):
            obj[key] = _zfill_code(val)
        else:
            obj[key] = _json_safe(val)
    return obj


def csv_to_rows(path: Path, trade_ymd: str) -> List[Dict[str, Any]]:
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as e:
        print("read fail", path, e)
        return []
    if df is None or df.empty:
        return []
    df = df.copy()
    df.columns = [_norm_header(c) for c in df.columns]
    return [_row_to_obj(row, trade_ymd=trade_ymd) for _, row in df.iterrows()]


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for obj in rows:
            f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
            f.write("\n")
            n += 1
    return n


def write_daily_main_flow_jsonl_shard(
    csv_path: Path | str,
    trade_ymd: str = "",
    *,
    out_dir: Path | str | None = None,
    min_rows: int = DEFAULT_MIN_FULL_ROWS,
    force: bool = False,
) -> Optional[Dict[str, Any]]:
    """单日 CSV → `{ymd}.main_flow.jsonl`。行数不足则跳过（除非 force）。"""
    path = Path(csv_path)
    ymd = str(trade_ymd or "").strip()
    if len(ymd) != 8 or not ymd.isdigit():
        m = _NAME_RE.search(path.name)
        if not m:
            raise ValueError("trade_ymd 须为 YYYYMMDD，或文件名为 个股主力净流入_YYYYMMDD.csv")
        ymd = m.group(1)
    n_file = _count_data_rows(path)
    if (not force) and n_file < int(min_rows):
        print(
            "[main_flow jsonl] skip %s rows=%d < min_rows=%d（疑似截断层，非全市场）"
            % (ymd, n_file, int(min_rows))
        )
        return None
    rows = csv_to_rows(path, ymd)
    dest = Path(out_dir) if out_dir else OUT_DIR
    out_path = dest / ("%s.main_flow.jsonl" % ymd)
    n = write_jsonl(out_path, rows)
    info = {
        "trade_date_ymd": ymd,
        "path": str(out_path),
        "lines": n,
        "csv_rows": n_file,
    }
    print("[main_flow jsonl] wrote", out_path.name, "lines=%d" % n)
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description="导出主力净流入全市场日为 JSONL 分片")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-rows", type=int, default=DEFAULT_MIN_FULL_ROWS)
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--history-dir", default="history_data")
    args = ap.parse_args()

    files = discover_full_market_flow_files(
        history_dir=args.history_dir, min_rows=int(args.min_rows)
    )
    all_dates = list_flow_file_dates(args.history_dir)
    skipped = len(all_dates) - len(files)
    print(
        "full_market_days=%d skipped_truncated=%d min_rows=%d"
        % (len(files), skipped, int(args.min_rows))
    )
    if not files:
        raise SystemExit("没有达到全市场行数门槛的 CSV")
    print("range=%s .. %s" % (files[0][0], files[-1][0]))
    if args.dry_run:
        for ymd, p, n in files[:5]:
            print(" ", ymd, n, p)
        if len(files) > 5:
            print("  ...")
        for ymd, p, n in files[-3:]:
            print(" ", ymd, n, p)
        return

    out_dir = Path(args.out_dir)
    counts: Dict[str, int] = {}
    for i, (ymd, path, n_csv) in enumerate(files, 1):
        if i == 1 or i % 10 == 0 or i == len(files):
            print("  [%d/%d] %s csv_rows=%d" % (i, len(files), ymd, n_csv))
        info = write_daily_main_flow_jsonl_shard(
            path, ymd, out_dir=out_dir, min_rows=int(args.min_rows), force=True
        )
        if info:
            counts[ymd] = int(info["lines"])

    meta = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "min_rows": int(args.min_rows),
        "full_market_days": len(files),
        "skipped_truncated_days": skipped,
        "date_from": files[0][0],
        "date_to": files[-1][0],
        "day_line_counts": counts,
    }
    meta_path = out_dir / "main_flow_export_meta.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", meta_path)
    print(
        json.dumps(
            {k: meta[k] for k in ("full_market_days", "skipped_truncated_days", "date_from", "date_to")},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
