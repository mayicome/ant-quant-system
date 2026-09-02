# -*- coding: utf-8 -*-
"""将 data/after_hours_rank/*/top10.csv 合并为智能体用 JSONL。

输出：data/cos/after_hours/after_hours_top.jsonl
按交易日从早到晚；每天生成 top10 后可调用 rebuild 覆盖重写（体量很小）。

用法：
  python tools/export_after_hours_top_to_jsonl.py
  python tools/export_after_hours_top_to_jsonl.py --dry-run
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

SRC_DIR = ROOT / "data" / "after_hours_rank"
OUT_DIR = ROOT / "data" / "cos" / "after_hours"
OUT_FILE = OUT_DIR / "after_hours_top.jsonl"
_DAY_RE = re.compile(r"^\d{8}$")


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


def _code_fields(v: Any) -> Dict[str, Any]:
    """保留原代码；另给纯 6 位 code6。"""
    if v is None:
        return {"代码": None, "code6": None}
    try:
        if pd.isna(v):
            return {"代码": None, "code6": None}
    except Exception:
        pass
    raw = str(v).strip()
    if not raw or raw.lower() in ("nan", "none"):
        return {"代码": None, "code6": None}
    code6 = raw.split(".", 1)[0]
    digits = re.sub(r"\D", "", code6)
    if digits.isdigit() and len(digits) <= 6:
        code6 = digits.zfill(6)
    else:
        code6 = code6 or None
    return {"代码": raw, "code6": code6}


def discover_top10_files(src_dir: Path = SRC_DIR) -> List[Tuple[str, Path]]:
    out: List[Tuple[str, Path]] = []
    if not src_dir.is_dir():
        return out
    for day_dir in sorted(src_dir.iterdir()):
        if not day_dir.is_dir():
            continue
        ymd = day_dir.name
        if not _DAY_RE.match(ymd):
            continue
        p = day_dir / "top10.csv"
        if p.is_file():
            out.append((ymd, p))
    return out


def _top10_to_rows(path: Path, trade_ymd: str) -> List[Dict[str, Any]]:
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as e:
        print("read fail", path, e)
        return []
    if df is None or df.empty:
        return []
    df = df.copy()
    df.columns = [_norm_header(c) for c in df.columns]
    rows: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        obj: Dict[str, Any] = {
            "trade_date": f"{trade_ymd[0:4]}-{trade_ymd[4:6]}-{trade_ymd[6:8]}",
            "trade_date_ymd": trade_ymd,
        }
        for col, val in row.items():
            key = _norm_header(col)
            if not key or key.startswith("Unnamed"):
                continue
            if key == "代码":
                obj.update(_code_fields(val))
            else:
                obj[key] = _json_safe(val)
        rows.append(obj)
    return rows


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for obj in rows:
            f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
            f.write("\n")
            n += 1
    return n


def rebuild_after_hours_top_jsonl(
    *,
    src_dir: Path | str | None = None,
    out_path: Path | str | None = None,
) -> Dict[str, Any]:
    """扫描全部 top10.csv，重写合并 JSONL。"""
    src = Path(src_dir) if src_dir else SRC_DIR
    dest = Path(out_path) if out_path else OUT_FILE
    files = discover_top10_files(src)
    all_rows: List[Dict[str, Any]] = []
    day_counts: Dict[str, int] = {}
    for ymd, path in files:
        rows = _top10_to_rows(path, ymd)
        day_counts[ymd] = len(rows)
        all_rows.extend(rows)
    n = write_jsonl(dest, all_rows)
    meta = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "days": len(files),
        "lines": n,
        "date_from": files[0][0] if files else "",
        "date_to": files[-1][0] if files else "",
        "path": str(dest),
        "day_counts": day_counts,
    }
    meta_path = dest.parent / "after_hours_top_export_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "[after_hours jsonl] wrote %s lines=%d days=%d (%s..%s)"
        % (dest.name, n, len(files), meta["date_from"], meta["date_to"])
    )
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description="导出盘后量 top10 为合并 JSONL")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--src-dir", default=str(SRC_DIR))
    ap.add_argument("--out", default=str(OUT_FILE))
    args = ap.parse_args()
    files = discover_top10_files(Path(args.src_dir))
    print("top10_days=%d" % len(files))
    if not files:
        raise SystemExit("未找到 data/after_hours_rank/*/top10.csv")
    print("range=%s .. %s" % (files[0][0], files[-1][0]))
    if args.dry_run:
        for ymd, p in files[:5]:
            print(" ", ymd, p)
        if len(files) > 5:
            print("  ...")
        for ymd, p in files[-3:]:
            print(" ", ymd, p)
        return
    rebuild_after_hours_top_jsonl(src_dir=args.src_dir, out_path=args.out)


if __name__ == "__main__":
    main()
