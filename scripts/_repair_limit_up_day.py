# -*- coding: utf-8 -*-
"""Rebuild history_data/涨停日数据/YYYY-MM-DD.json from CSV (+ fallbacks) + all_a_stock_info.

Usage:
  python scripts/_repair_limit_up_day.py 2026-06-30
  python scripts/_repair_limit_up_day.py 2026-07-31

Stock universe priority when CSV missing/empty:
  1) history_data/涨停板数据_DATE.csv (non-empty)
  2) history_data/存档/涨停板数据_DATE.csv (non-empty)
  3) 存档/要分析的股票列表_YYYYMMDD.txt
  4) 存档/首板统计_YYYYMMDD.xlsx + 连板统计_YYYYMMDD.xlsx

Price/change_pct filled from daily_change(_clean)_DATE.xlsx when available.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
HD = ROOT / "history_data"
ARCHIVE = HD / "存档"
INFO_PATH = ROOT / "data" / "all_a_stock_info.json"


def calculate_plate_stats(stocks):
    industry_count = {}
    for stock in stocks:
        industry = stock.get("industry")
        if industry and str(industry).strip() and str(industry) != "nan":
            industry_name = str(industry).strip()
            if industry_name not in industry_count:
                industry_count[industry_name] = {
                    "name": industry_name,
                    "count": 0,
                    "stocks": [],
                }
            industry_count[industry_name]["count"] += 1
            code = str(stock.get("code", "")).zfill(6)
            industry_count[industry_name]["stocks"].append(f"{code} {stock['name']}")
    plate_stats = list(industry_count.values())
    plate_stats.sort(key=lambda x: x["count"], reverse=True)
    return plate_stats


def calculate_concept_stats(stocks):
    excluded_concepts = {"央国企改革", "融资融券"}
    concept_count = {}
    for stock in stocks:
        concepts = stock.get("concepts", [])
        if concepts and isinstance(concepts, list):
            for concept in concepts:
                if concept and str(concept).strip():
                    concept_name = str(concept).strip()
                    if concept_name in excluded_concepts:
                        continue
                    if concept_name not in concept_count:
                        concept_count[concept_name] = {
                            "name": concept_name,
                            "count": 0,
                            "stocks": [],
                        }
                    concept_count[concept_name]["count"] += 1
                    code = str(stock.get("code", "")).zfill(6)
                    stock_str = f"{code} {stock['name']}"
                    if stock_str not in concept_count[concept_name]["stocks"]:
                        concept_count[concept_name]["stocks"].append(stock_str)
    concept_stats = [item for item in concept_count.values() if item["count"] >= 2]
    concept_stats.sort(key=lambda x: x["count"], reverse=True)
    return concept_stats


def calculate_sector_plate_stats(stocks):
    excluded_sectors = {
        "央国企改革",
        "融资融券",
        "深股通",
        "沪股通",
        "机构重仓",
        "QFII重仓",
        "专精特新",
        "标准普尔",
        "富时罗素",
    }
    sector_plate_count = {}
    for stock in stocks:
        plates = stock.get("plates", [])
        if plates and isinstance(plates, list):
            for plate in plates:
                if plate and str(plate).strip():
                    plate_name = str(plate).strip()
                    if plate_name in excluded_sectors:
                        continue
                    if plate_name not in sector_plate_count:
                        sector_plate_count[plate_name] = {
                            "name": plate_name,
                            "count": 0,
                            "stocks": [],
                        }
                    sector_plate_count[plate_name]["count"] += 1
                    code = str(stock.get("code", "")).zfill(6)
                    stock_str = f"{code} {stock['name']}"
                    if stock_str not in sector_plate_count[plate_name]["stocks"]:
                        sector_plate_count[plate_name]["stocks"].append(stock_str)
    sector_plate_stats = [
        item for item in sector_plate_count.values() if item["count"] >= 2
    ]
    sector_plate_stats.sort(key=lambda x: x["count"], reverse=True)
    return sector_plate_stats


def _to_float(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    if s in ("", "nan", "None"):
        return None
    try:
        return float(s.replace("%", ""))
    except Exception:
        return None


def _norm_code(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip().replace("\u200b", "")
    digits = "".join(c for c in s if c.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)[-6:]


def _csv_nonempty(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size < 20:
            return False
        df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
        if df is None or df.empty:
            return False
        return any(_norm_code(v) for v in df.iloc[:, 0].tolist())
    except Exception:
        return False


def _load_rows_from_csv(path: Path) -> Tuple[List[dict], str]:
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    cols = list(df.columns)
    code_col, name_col = cols[0], cols[1] if len(cols) > 1 else cols[0]
    price_col = cols[2] if len(cols) > 2 else None
    pct_col = cols[3] if len(cols) > 3 else None
    ind_col = cols[4] if len(cols) > 4 else None
    rows = []
    for _, row in df.iterrows():
        code = _norm_code(row[code_col])
        if len(code) != 6:
            continue
        name = str(row[name_col]).strip() if name_col and pd.notna(row[name_col]) else ""
        if name.lower() == "nan":
            name = ""
        industry = None
        if (
            ind_col
            and pd.notna(row[ind_col])
            and str(row[ind_col]).strip() not in ("", "nan")
        ):
            industry = str(row[ind_col]).strip()
        rows.append(
            {
                "code": code,
                "name": name,
                "price": _to_float(row[price_col]) if price_col else None,
                "change_pct": _to_float(row[pct_col]) if pct_col else None,
                "industry": industry,
            }
        )
    return rows, f"CSV:{path.name}"


def _load_rows_from_analyze_list(path: Path) -> Tuple[List[dict], str]:
    rows = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip().replace("\u200b", "")
        if not ln:
            continue
        parts = re.split(r"[\t,，\s]+", ln, maxsplit=1)
        code = _norm_code(parts[0] if parts else "")
        if len(code) != 6:
            continue
        name = parts[1].strip() if len(parts) > 1 else ""
        rows.append(
            {"code": code, "name": name, "price": None, "change_pct": None, "industry": None}
        )
    return rows, f"要分析列表:{path.name}"


def _load_rows_from_board_xlsx(first_path: Path, streak_path: Path) -> Tuple[List[dict], str]:
    frames = []
    labels = []
    for p, label in ((first_path, "首板"), (streak_path, "连板")):
        if not p.is_file():
            continue
        df = pd.read_excel(p, dtype=str)
        frames.append(df)
        labels.append(label)
    if not frames:
        return [], ""
    seen = set()
    rows = []
    for df in frames:
        code_col = df.columns[0]
        name_col = df.columns[1] if len(df.columns) > 1 else None
        for _, row in df.iterrows():
            code = _norm_code(row[code_col])
            if len(code) != 6 or code in seen:
                continue
            seen.add(code)
            name = ""
            if name_col is not None and pd.notna(row[name_col]):
                name = str(row[name_col]).strip().replace("\u200b", "")
                if name.lower() == "nan":
                    name = ""
            rows.append(
                {
                    "code": code,
                    "name": name,
                    "price": None,
                    "change_pct": None,
                    "industry": None,
                }
            )
    return rows, "+".join(labels) + "统计xlsx"


def _load_daily_change_map(date: str) -> Dict[str, dict]:
    ymd = date.replace("-", "")
    candidates = [
        HD / f"daily_change_clean_{date}.xlsx",
        HD / f"daily_change_{date}.xlsx",
        ARCHIVE / f"daily_change_clean_{date}.xlsx",
        ARCHIVE / f"daily_change_{date}.xlsx",
        ARCHIVE / f"daily_change_clean_{ymd}.xlsx",
        ARCHIVE / f"daily_change_{ymd}.xlsx",
    ]
    path = next((p for p in candidates if p.is_file()), None)
    if not path:
        return {}
    df = pd.read_excel(path)
    cols = {str(c).strip().lower(): c for c in df.columns}
    code_col = cols.get("stock_code") or cols.get("代码") or cols.get("证券代码")
    name_col = cols.get("stock_name") or cols.get("名称") or cols.get("证券简称")
    pct_col = cols.get("change_pct") or cols.get("涨跌幅(%)") or cols.get("涨跌幅")
    close_col = cols.get("close") or cols.get("最新价") or cols.get("收盘价")
    if code_col is None:
        return {}
    out: Dict[str, dict] = {}
    for _, row in df.iterrows():
        code = _norm_code(row.get(code_col))
        if len(code) != 6:
            continue
        name = ""
        if name_col is not None and pd.notna(row.get(name_col)):
            name = str(row.get(name_col)).strip()
            if name.lower() == "nan":
                name = ""
        out[code] = {
            "name": name,
            "price": _to_float(row.get(close_col)) if close_col else None,
            "change_pct": _to_float(row.get(pct_col)) if pct_col else None,
            "_source": path.name,
        }
    return out


def resolve_stock_rows(date: str) -> Tuple[List[dict], str, Optional[Path]]:
    """Return (rows, source_label, csv_path_used_or_None)."""
    ymd = date.replace("-", "")
    csv_candidates = [
        HD / f"涨停板数据_{date}.csv",
        ARCHIVE / f"涨停板数据_{date}.csv",
    ]
    for csv_path in csv_candidates:
        if _csv_nonempty(csv_path):
            rows, label = _load_rows_from_csv(csv_path)
            if rows:
                return rows, label, csv_path

    list_path = ARCHIVE / f"要分析的股票列表_{ymd}.txt"
    if list_path.is_file():
        rows, label = _load_rows_from_analyze_list(list_path)
        if rows:
            return rows, label, None

    first_path = ARCHIVE / f"首板统计_{ymd}.xlsx"
    streak_path = ARCHIVE / f"连板统计_{ymd}.xlsx"
    rows, label = _load_rows_from_board_xlsx(first_path, streak_path)
    if rows:
        return rows, label, None

    empty_csvs = [str(p) for p in csv_candidates if p.is_file()]
    raise SystemExit(
        f"no usable stock universe for {date}; empty/missing CSV candidates={empty_csvs}"
    )


def repair(date: str) -> None:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise SystemExit(f"date must be YYYY-MM-DD, got {date!r}")

    json_path = HD / "涨停日数据" / f"{date}.json"
    if not json_path.exists():
        raise SystemExit(f"missing json: {json_path}")
    if not INFO_PATH.exists():
        raise SystemExit(f"missing stock info: {INFO_PATH}")

    raw_rows, source_label, csv_used = resolve_stock_rows(date)
    dc_map = _load_daily_change_map(date)
    info = json.loads(INFO_PATH.read_text(encoding="utf-8"))

    bak = json_path.with_suffix(
        ".json.bak_empty_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    shutil.copy2(json_path, bak)
    print(f"backup -> {bak}")
    print(f"source -> {source_label}" + (f" ({csv_used})" if csv_used else ""))
    if dc_map:
        sample = next(iter(dc_map.values()))
        print(f"prices  -> daily_change ({sample.get('_source')})")

    stocks = []
    for row in raw_rows:
        code = row["code"]
        si = info.get(code) or {}
        dc = dc_map.get(code) or {}
        name = (row.get("name") or "").strip() or str(dc.get("name") or "").strip()
        if not name:
            name = str(si.get("name") or "").strip()
        price = row.get("price")
        if price is None:
            price = dc.get("price")
        pct = row.get("change_pct")
        if pct is None:
            pct = dc.get("change_pct")
        industry = row.get("industry")
        if not industry:
            industry = si.get("industry") or None
        stocks.append(
            {
                "code": code,
                "name": name or code,
                "price": price if price is not None else 0,
                "change_pct": pct if pct is not None else 0,
                "industry": industry,
                "concepts": list(si.get("concepts") or [])
                if isinstance(si.get("concepts"), list)
                else [],
                "plates": list(si.get("plates") or [])
                if isinstance(si.get("plates"), list)
                else [],
            }
        )

    plate_stats = calculate_plate_stats(stocks)
    concept_stats = calculate_concept_stats(stocks)
    sector_plate_stats = calculate_sector_plate_stats(stocks)

    note_parts = [f"Backfilled from {source_label}"]
    if dc_map:
        note_parts.append("+ daily_change for price/change_pct")
    note_parts.append("+ data/all_a_stock_info.json")
    out = {
        "date": date,
        "timestamp": int(time.time()),
        "limit_up_stocks": stocks,
        "plate_stats": plate_stats,
        "concept_stats": concept_stats,
        "sector_plate_stats": sector_plate_stats,
        "total_stocks": len(stocks),
        "total_industries": len(plate_stats),
        "_repaired": True,
        "_repair_note": "; ".join(note_parts),
    }
    json_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {json_path}")
    print(
        f"stocks={len(stocks)} plate={len(plate_stats)} "
        f"concept={len(concept_stats)} sector={len(sector_plate_stats)}"
    )
    print("top concepts", [(c["name"], c["count"]) for c in concept_stats[:5]])
    print("top sectors", [(c["name"], c["count"]) for c in sector_plate_stats[:5]])
    print("top industries", [(c["name"], c["count"]) for c in plate_stats[:5]])


def main():
    ap = argparse.ArgumentParser(description="Repair empty/broken limit-up day JSON")
    ap.add_argument("date", help="YYYY-MM-DD")
    args = ap.parse_args()
    repair(args.date)


if __name__ == "__main__":
    main()
