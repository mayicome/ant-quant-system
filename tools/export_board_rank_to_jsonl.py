# -*- coding: utf-8 -*-
"""东财板块排名 → 智能体日分片 JSONL。

以涨跌幅全榜为基础，左连涨停家数榜、资金流榜。
行业 / 概念分开：

  data/cos/board_rank/{YYYYMMDD}.board_industry.jsonl
  data/cos/board_rank/{YYYYMMDD}.board_concept.jsonl

默认自 2026-06-09 起（concept_rank 齐全日起）。

用法：
  python tools/export_board_rank_to_jsonl.py
  python tools/export_board_rank_to_jsonl.py --dry-run
  python tools/export_board_rank_to_jsonl.py --from-date 2026-06-09
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

SRC_DIR = ROOT / "data" / "eastmoney_board_rank"
OUT_DIR = ROOT / "data" / "cos" / "board_rank"
DEFAULT_FROM = "2026-06-09"

KIND_INDUSTRY = "industry"
KIND_CONCEPT = "concept"

# 涨跌幅全榜字段 → 导出名（基础）
_RANK_KEEP = (
    "板块名称",
    "板块代码",
    "最新价",
    "涨跌额",
    "涨跌幅",
    "总市值",
    "换手率",
    "上涨家数",
    "下跌家数",
    "领涨股票",
    "领涨股票-涨跌幅",
)

# 资金流：名称对齐后带入，去掉与基础重复的涨跌幅
_FLOW_MAP = {
    "序号": "资金流排名",
    "今日主力净流入-净额": "主力净流入-净额",
    "今日主力净流入-净占比": "主力净流入-净占比",
    "今日超大单净流入-净额": "超大单净流入-净额",
    "今日超大单净流入-净占比": "超大单净流入-净占比",
    "今日大单净流入-净额": "大单净流入-净额",
    "今日大单净流入-净占比": "大单净流入-净占比",
    "今日中单净流入-净额": "中单净流入-净额",
    "今日中单净流入-净占比": "中单净流入-净占比",
    "今日小单净流入-净额": "小单净流入-净额",
    "今日小单净流入-净占比": "小单净流入-净占比",
    "今日主力净流入最大股": "主力净流入最大股",
}


def _norm_header(name: Any) -> str:
    s = str(name or "").replace("\r", "").replace("\n", "").strip()
    return re.sub(r"\s+", "", s)


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


def _ymd_dash(s: str) -> str:
    s = str(s or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


def _ymd_compact(s: str) -> str:
    d = _ymd_dash(s).replace("-", "")
    return d if len(d) == 8 and d.isdigit() else ""


def _read_csv(path: Path) -> Optional[pd.DataFrame]:
    if not path.is_file():
        return None
    df = None
    last_err: Optional[Exception] = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            df = pd.read_csv(path, encoding=enc)
            last_err = None
            break
        except Exception as e:
            last_err = e
            df = None
    if df is None:
        print("read fail", path, last_err)
        return None
    if df.empty:
        return None
    df = df.copy()
    df.columns = [_norm_header(c) for c in df.columns]
    return df


def _board_paths(kind: str, day_dash: str, src: Path) -> Dict[str, Path]:
    if kind == KIND_INDUSTRY:
        return {
            "rank": src / ("industry_rank_%s.csv" % day_dash),
            "lu": src / ("industry_lu_rank_%s.csv" % day_dash),
            "flow": src / ("industry_fund_flow_%s.csv" % day_dash),
        }
    return {
        "rank": src / ("concept_rank_%s.csv" % day_dash),
        "lu": src / ("concept_lu_rank_%s.csv" % day_dash),
        "flow": src / ("concept_fund_flow_%s.csv" % day_dash),
    }


def _name_key(v: Any) -> str:
    return str(v or "").strip()


def build_joined_rows(
    kind: str,
    day_dash: str,
    *,
    src_dir: Path = SRC_DIR,
) -> List[Dict[str, Any]]:
    """涨跌幅榜为底，左连涨停家数榜、资金流榜。无涨跌幅榜则返回空。"""
    paths = _board_paths(kind, day_dash, src_dir)
    rank = _read_csv(paths["rank"])
    if rank is None or "板块名称" not in rank.columns:
        return []

    lu = _read_csv(paths["lu"])
    flow = _read_csv(paths["flow"])

    lu_by_name: Dict[str, pd.Series] = {}
    if lu is not None and "板块名称" in lu.columns:
        for _, r in lu.iterrows():
            k = _name_key(r.get("板块名称"))
            if k:
                lu_by_name[k] = r

    flow_by_name: Dict[str, pd.Series] = {}
    if flow is not None:
        name_col = "名称" if "名称" in flow.columns else ("板块名称" if "板块名称" in flow.columns else "")
        if name_col:
            for _, r in flow.iterrows():
                k = _name_key(r.get(name_col))
                if k:
                    flow_by_name[k] = r

    ymd = _ymd_compact(day_dash)
    board_type = "行业" if kind == KIND_INDUSTRY else "概念"
    out: List[Dict[str, Any]] = []
    for _, r in rank.iterrows():
        name = _name_key(r.get("板块名称"))
        if not name:
            continue
        obj: Dict[str, Any] = {
            "trade_date": day_dash,
            "trade_date_ymd": ymd,
            "board_type": board_type,
            "涨跌幅排名": _json_safe(r.get("排名")),
            "板块名称": name,
        }
        for col in _RANK_KEEP:
            if col == "板块名称":
                continue
            if col in rank.columns:
                obj[col] = _json_safe(r.get(col))

        lr = lu_by_name.get(name)
        if lr is not None:
            obj["涨停家数排名"] = _json_safe(lr.get("排名"))
            # lu 表里「上涨家数」即涨停家数；涨跌幅列偶发也是家数，优先上涨家数
            lu_cnt = lr.get("上涨家数")
            if lu_cnt is None or (isinstance(lu_cnt, float) and pd.isna(lu_cnt)):
                lu_cnt = lr.get("涨跌幅")
            obj["涨停家数"] = _json_safe(lu_cnt)
        else:
            obj["涨停家数排名"] = None
            obj["涨停家数"] = None

        fr = flow_by_name.get(name)
        if fr is not None:
            for src_col, dst_col in _FLOW_MAP.items():
                if src_col in fr.index:
                    obj[dst_col] = _json_safe(fr.get(src_col))
        else:
            for dst_col in _FLOW_MAP.values():
                obj.setdefault(dst_col, None)

        out.append(obj)
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


def write_daily_board_rank_jsonl(
    day: str,
    *,
    src_dir: Path | str | None = None,
    out_dir: Path | str | None = None,
) -> Dict[str, Any]:
    """写出单日行业+概念两个分片。day 支持 YYYY-MM-DD 或 YYYYMMDD。"""
    day_dash = _ymd_dash(day)
    ymd = _ymd_compact(day_dash)
    if not ymd:
        raise ValueError("invalid day: %s" % day)
    src = Path(src_dir) if src_dir else SRC_DIR
    dest = Path(out_dir) if out_dir else OUT_DIR
    result: Dict[str, Any] = {"trade_date": day_dash, "paths": {}, "counts": {}}
    for kind, suffix in ((KIND_INDUSTRY, "industry"), (KIND_CONCEPT, "concept")):
        rows = build_joined_rows(kind, day_dash, src_dir=src)
        out_path = dest / ("%s.board_%s.jsonl" % (ymd, suffix))
        if not rows:
            # 无涨跌幅底表则不写空文件（避免误导）
            if out_path.is_file():
                try:
                    out_path.unlink()
                except Exception:
                    pass
            result["counts"][suffix] = 0
            continue
        n = write_jsonl(out_path, rows)
        result["paths"][suffix] = str(out_path)
        result["counts"][suffix] = n
    print(
        "[board_rank jsonl] %s industry=%s concept=%s"
        % (day_dash, result["counts"].get("industry", 0), result["counts"].get("concept", 0))
    )
    return result


def list_rank_days(
    *,
    src_dir: Path = SRC_DIR,
    from_date: str = DEFAULT_FROM,
) -> List[str]:
    """有 industry_rank 或 concept_rank 的日期（YYYY-MM-DD），≥ from_date。"""
    start = _ymd_dash(from_date)
    days = set()
    for pref in ("industry_rank_", "concept_rank_"):
        for p in src_dir.glob(pref + "*.csv"):
            m = re.search(r"(\d{4}-\d{2}-\d{2})", p.name)
            if not m:
                continue
            d = m.group(1)
            if d >= start:
                days.add(d)
    return sorted(days)


def main() -> None:
    ap = argparse.ArgumentParser(description="导出东财板块排名日分片 JSONL")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-date", default=DEFAULT_FROM)
    ap.add_argument("--src-dir", default=str(SRC_DIR))
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    args = ap.parse_args()

    src = Path(args.src_dir)
    days = list_rank_days(src_dir=src, from_date=args.from_date)
    print("days=%d from=%s" % (len(days), _ymd_dash(args.from_date)))
    if not days:
        raise SystemExit("无可用涨跌幅排名日")
    print("range=%s .. %s" % (days[0], days[-1]))
    if args.dry_run:
        for d in days[:5]:
            print(" ", d)
        if len(days) > 5:
            print("  ...")
        for d in days[-3:]:
            print(" ", d)
        return

    out_dir = Path(args.out_dir)
    day_counts: Dict[str, Any] = {}
    for i, d in enumerate(days, 1):
        if i == 1 or i % 10 == 0 or i == len(days):
            print("  [%d/%d] %s" % (i, len(days), d))
        info = write_daily_board_rank_jsonl(d, src_dir=src, out_dir=out_dir)
        day_counts[d] = info.get("counts") or {}

    meta = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "from_date": _ymd_dash(args.from_date),
        "days": len(days),
        "date_from": days[0],
        "date_to": days[-1],
        "day_counts": day_counts,
        "out_dir": str(out_dir),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "board_rank_export_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", meta_path)


if __name__ == "__main__":
    main()
