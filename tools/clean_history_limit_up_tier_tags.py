#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 history_data 下「每日涨停」JSON 中移除东财层级占位：一级 / 二级 / 三级。

处理内容：
- limit_up_stocks[]：concepts、plates 列表去项；industry 若为三字之一则置为 null
- concept_stats / sector_plate_stats / plate_stats / combined_stats（若存在）：去掉 name 为上述三字的整条统计

默认只处理文件名日期 >= 2026-03-31 的 *.json（与 CSV 涨停板数据无关，仅 JSON 快照）。

用法（项目根目录）:
  python tools/clean_history_limit_up_tier_tags.py --dry-run
  python tools/clean_history_limit_up_tier_tags.py
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

REMOVE = frozenset({"一级", "二级", "三级"})
DATE_JSON = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")

STAT_KEYS = ("concept_stats", "sector_plate_stats", "plate_stats", "combined_stats")


def _clean_str_list(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for x in raw:
        s = str(x).strip() if x is not None else ""
        if s and s not in REMOVE:
            out.append(s)
    return out


def _clean_stock_item(item: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(item)
    if "concepts" in out:
        out["concepts"] = _clean_str_list(out.get("concepts"))
    if "plates" in out:
        out["plates"] = _clean_str_list(out.get("plates"))
    ind = out.get("industry")
    if ind is not None and str(ind).strip() in REMOVE:
        out["industry"] = None
    return out


def _clean_stat_block(items: Any) -> Any:
    if not isinstance(items, list):
        return items
    return [
        x
        for x in items
        if isinstance(x, dict)
        and str(x.get("name", "")).strip() not in REMOVE
    ]


def clean_daily_json(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    stocks = out.get("limit_up_stocks")
    if isinstance(stocks, list):
        out["limit_up_stocks"] = [
            _clean_stock_item(x) if isinstance(x, dict) else x for x in stocks
        ]
    for k in STAT_KEYS:
        if k in out:
            out[k] = _clean_stat_block(out[k])
    return out


def _file_date_from_name(path: Path) -> Optional[str]:
    m = DATE_JSON.match(path.name)
    return m.group(1) if m else None


def main() -> int:
    ap = argparse.ArgumentParser(description="从 history_data 每日 JSON 移除一级/二级/三级")
    ap.add_argument(
        "--history-dir",
        type=Path,
        default=None,
        help="history_data 目录，默认项目根下 history_data",
    )
    ap.add_argument(
        "--from-date",
        default="2026-03-31",
        help="只处理日期 >= 该值的 YYYY-MM-DD（按文件名）",
    )
    ap.add_argument("--dry-run", action="store_true", help="只统计不写回")
    ap.add_argument(
        "--no-backup",
        action="store_true",
        help="不写 .bak（默认每个文件写回前复制一份）",
    )
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    hist = args.history_dir or (root / "history_data")
    if not hist.is_dir():
        print(f"目录不存在: {hist}")
        return 1

    from_d = str(args.from_date).strip()[:10]
    files_changed = 0
    json_files = sorted(hist.glob("*.json"))
    in_range: List[Path] = []
    for p in json_files:
        d = _file_date_from_name(p)
        if d and d >= from_d:
            in_range.append(p)

    for path in in_range:
        fd = _file_date_from_name(path)
        if not fd or fd < from_d:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"跳过（无法解析）: {path}  {e}")
            continue
        if not isinstance(data, dict):
            continue
        new_data = clean_daily_json(data)
        if new_data == data:
            continue
        files_changed += 1

        if args.dry_run:
            print(f"[dry-run] 将修改: {path.name}")
            continue

        if not args.no_backup:
            bak = path.with_name(path.name + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            shutil.copy2(path, bak)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(new_data, f, ensure_ascii=False, indent=2)

        print(f"已写回: {path}")

    print(
        f"完成。日期>={from_d} 的 JSON 共 {len(in_range)} 个；其中需修改 {files_changed} 个。"
        + (" (--dry-run 未写文件)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
