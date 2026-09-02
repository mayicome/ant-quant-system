# -*- coding: utf-8 -*-
"""将历史「龙虎榜解析_*.xlsx」导出为智能体用 JSONL。

输出（按交易日从早到晚追加行）：
  data/cos/lhb/lhb_base_main.jsonl   ← sheet「主力情报」
  data/cos/lhb/lhb_base_total.jsonl  ← sheet「原始数据_总榜」
  data/cos/lhb/lhb_base_seat.jsonl   ← sheet「原始数据_席位明细」

每行一个 JSON 对象，额外字段：
  trade_date      YYYY-MM-DD
  trade_date_ymd  YYYYMMDD

「主力情报」不导出主观列：主力强度(0-100)、次日溢价预测。

用法：
  python tools/export_lhb_to_jsonl.py
  python tools/export_lhb_to_jsonl.py --dry-run
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

HIST = ROOT / "history_data"
OUT_DIR = ROOT / "data" / "cos" / "lhb"

SHEET_MAIN = "主力情报"
SHEET_TOTAL = "原始数据_总榜"
SHEET_SEAT = "原始数据_席位明细"

OUT_MAP = {
    SHEET_MAIN: "lhb_base_main.jsonl",
    SHEET_TOTAL: "lhb_base_total.jsonl",
    SHEET_SEAT: "lhb_base_seat.jsonl",
}

# 单日分片：{YYYYMMDD}.lhb_{main|total|seat}.jsonl
DAILY_SHARD_MAP = {
    SHEET_MAIN: "main",
    SHEET_TOTAL: "total",
    SHEET_SEAT: "seat",
}

# 主力情报：主观/自研字段不给智能体
MAIN_DROP_COLS = {
    "主力强度(0-100)",
    "主力强度",
    "次日溢价预测",
}

_NAME_RE = re.compile(r"龙虎榜解析_(\d{8})\.xlsx$", re.IGNORECASE)


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
        return v.strftime("%Y-%m-%d %H:%M:%S") if (v.hour or v.minute or v.second) else v.strftime("%Y-%m-%d")
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
    # numpy scalars
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


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def discover_lhb_files() -> List[Tuple[str, Path]]:
    """返回 [(YYYYMMDD, path)]，同日优先 history_data 根目录（非存档）。"""
    found: Dict[str, Path] = {}
    roots: List[Path] = []
    if HIST.is_dir():
        roots.append(HIST)
        arch = HIST / "存档"
        if arch.is_dir():
            roots.append(arch)

    candidates: List[Tuple[str, Path, int]] = []
    for base in roots:
        for p in base.rglob("龙虎榜解析_*.xlsx"):
            if not p.is_file():
                continue
            m = _NAME_RE.search(p.name)
            if not m:
                continue
            ymd = m.group(1)
            # 优先分：根目录=0，存档=1，越深路径越大
            parts = p.relative_to(HIST).parts if HIST in p.parents or p.parent == HIST else p.parts
            in_arch = 1 if "存档" in parts else 0
            depth = len(parts)
            candidates.append((ymd, p, in_arch * 1000 + depth))

    # 同日取优先分最小者
    best: Dict[str, Tuple[int, Path]] = {}
    for ymd, p, score in candidates:
        cur = best.get(ymd)
        if cur is None or score < cur[0]:
            best[ymd] = (score, p)

    return sorted((ymd, p) for ymd, (_s, p) in best.items())


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
    # 浮点读成 890.0
    try:
        f = float(s)
        if f == int(f) and 0 <= int(f) < 1000000:
            return str(int(f)).zfill(6)
    except (TypeError, ValueError):
        pass
    return s


def _row_to_obj(
    row: pd.Series,
    *,
    trade_ymd: str,
    drop_cols: Optional[set] = None,
) -> Dict[str, Any]:
    obj: Dict[str, Any] = {
        "trade_date": f"{trade_ymd[0:4]}-{trade_ymd[4:6]}-{trade_ymd[6:8]}",
        "trade_date_ymd": trade_ymd,
    }
    skip = drop_cols or set()
    for col, val in row.items():
        key = _norm_header(col)
        if not key or key in skip:
            continue
        if key in ("代码", "股票代码"):
            obj[key] = _zfill_code(val)
        else:
            obj[key] = _json_safe(val)
    return obj


def export_sheet_rows(
    path: Path,
    sheet: str,
    trade_ymd: str,
) -> List[Dict[str, Any]]:
    try:
        df = pd.read_excel(path, sheet_name=sheet)
    except ValueError:
        return []
    except Exception as e:
        print("read fail", path.name, sheet, e)
        return []
    if df is None or df.empty:
        return []
    df = df.copy()
    df.columns = [_norm_header(c) for c in df.columns]
    drop = MAIN_DROP_COLS if sheet == SHEET_MAIN else None
    out: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        out.append(_row_to_obj(row, trade_ymd=trade_ymd, drop_cols=drop))
    return out


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for obj in rows:
            f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
            f.write("\n")
            n += 1
    return n


def write_daily_lhb_jsonl_shards(
    xlsx_path: Path | str,
    trade_ymd: str,
    *,
    out_dir: Path | str | None = None,
) -> Dict[str, Any]:
    """从单日「龙虎榜解析_YYYYMMDD.xlsx」写出三个 JSONL 分片。

    文件名：{YYYYMMDD}.lhb_main.jsonl / .lhb_total.jsonl / .lhb_seat.jsonl
    默认目录：data/cos/lhb
    """
    path = Path(xlsx_path)
    ymd = str(trade_ymd or "").strip()
    if len(ymd) != 8 or not ymd.isdigit():
        m = _NAME_RE.search(path.name)
        if not m:
            raise ValueError("trade_ymd 须为 YYYYMMDD，或文件名为 龙虎榜解析_YYYYMMDD.xlsx")
        ymd = m.group(1)
    dest = Path(out_dir) if out_dir else OUT_DIR
    dest.mkdir(parents=True, exist_ok=True)

    paths: Dict[str, str] = {}
    counts: Dict[str, int] = {}
    for sheet, suffix in DAILY_SHARD_MAP.items():
        out_path = dest / ("%s.lhb_%s.jsonl" % (ymd, suffix))
        rows = export_sheet_rows(path, sheet, ymd)
        n = write_jsonl(out_path, rows)
        paths[suffix] = str(out_path)
        counts[suffix] = n
    return {"trade_date_ymd": ymd, "out_dir": str(dest), "paths": paths, "counts": counts}


def main() -> None:
    ap = argparse.ArgumentParser(description="导出龙虎榜解析为 JSONL（智能体）")
    ap.add_argument("--dry-run", action="store_true", help="只扫描文件，不写盘")
    ap.add_argument(
        "--out-dir",
        default=str(OUT_DIR),
        help="输出目录（默认 data/cos/lhb）",
    )
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    files = discover_lhb_files()
    if not files:
        raise SystemExit("未找到 龙虎榜解析_*.xlsx")

    print("files=%d  range=%s .. %s" % (len(files), files[0][0], files[-1][0]))
    if args.dry_run:
        for ymd, p in files[:5]:
            print(" ", ymd, _rel(p))
        if len(files) > 5:
            print("  ...")
        for ymd, p in files[-3:]:
            print(" ", ymd, _rel(p))
        return

    buckets: Dict[str, List[Dict[str, Any]]] = {s: [] for s in OUT_MAP}
    missing: Dict[str, int] = {s: 0 for s in OUT_MAP}

    for i, (ymd, path) in enumerate(files, 1):
        if i == 1 or i % 20 == 0 or i == len(files):
            print("  [%d/%d] %s" % (i, len(files), ymd))
        for sheet in OUT_MAP:
            rows = export_sheet_rows(path, sheet, ymd)
            if not rows:
                missing[sheet] += 1
            buckets[sheet].extend(rows)

    counts: Dict[str, int] = {}
    for sheet, fname in OUT_MAP.items():
        out_path = out_dir / fname
        n = write_jsonl(out_path, buckets[sheet])
        counts[fname] = n
        print("wrote", _rel(out_path), "lines=%d empty_days=%d" % (n, missing[sheet]))

    meta = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "file_count": len(files),
        "date_from": files[0][0],
        "date_to": files[-1][0],
        "counts": counts,
        "missing_sheet_days": missing,
    }
    meta_path = out_dir / "lhb_base_export_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", _rel(meta_path))
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
