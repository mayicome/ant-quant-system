#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 data/all_a_stock_info.json 中移除东财行业层级占位「一级」「二级」「三级」：
- concepts、plates：从列表中删除这些字符串
- industry：若整段仅为上述三字之一，则置为空字符串 ""

用法（在项目根目录执行）:
  python tools/clean_a_stock_info_tier_tags.py
  python tools/clean_a_stock_info_tier_tags.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

REMOVE = frozenset({"一级", "二级", "三级"})


def _clean_str_list(raw: Any) -> Tuple[List[str], int]:
    """返回 (新列表, 剔除条数)。"""
    if not isinstance(raw, list):
        return [], 0
    out: List[str] = []
    removed_here = 0
    for c in raw:
        s = str(c).strip() if c is not None else ""
        if not s:
            continue
        if s in REMOVE:
            removed_here += 1
            continue
        out.append(s)
    return out, removed_here


def _clean_industry(raw: Any) -> Tuple[str, int]:
    """若为一级/二级/三级占位，返回空串并计 1 次移除。"""
    if raw is None:
        return "", 0
    s = str(raw).strip()
    if s in REMOVE:
        return "", 1
    return s, 0


def main() -> int:
    ap = argparse.ArgumentParser(description="从 all_a_stock_info.json 移除一级/二级/三级（concepts/plates/industry）")
    ap.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="data 目录，默认项目根下 data",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计不写回",
    )
    ap.add_argument(
        "--no-backup",
        action="store_true",
        help="不写 .bak 备份（默认会先备份）",
    )
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    data_dir = args.data_dir or (root / "data")
    path = data_dir / "all_a_stock_info.json"
    if not path.is_file():
        print(f"找不到文件: {path}")
        return 1

    with open(path, "r", encoding="utf-8") as f:
        data: Dict[str, Any] = json.load(f)

    stocks_touched = 0
    tags_removed = 0
    out_data: Dict[str, Any] = {}

    for code, info in data.items():
        if not isinstance(info, dict):
            out_data[code] = info
            continue
        rec = dict(info)
        changed = False
        new_c, n = _clean_str_list(rec.get("concepts"))
        if n:
            tags_removed += n
            rec["concepts"] = new_c
            changed = True
        new_p, np = _clean_str_list(rec.get("plates"))
        if np:
            tags_removed += np
            rec["plates"] = new_p
            changed = True
        new_i, ni = _clean_industry(rec.get("industry"))
        if ni:
            tags_removed += ni
            rec["industry"] = new_i
            changed = True
        if changed:
            stocks_touched += 1
        out_data[code] = rec

    print(f"文件: {path}")
    print(
        f"共 {len(data)} 条股票记录；移除占位总次数: {tags_removed}；"
        f"至少有一处被清理的股票数: {stocks_touched}"
    )

    if args.dry_run:
        print("(--dry-run，未写回)")
        return 0

    if not args.no_backup:
        bak = path.with_suffix(path.suffix + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(path, bak)
        print(f"已备份: {bak}")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)

    print("已写回。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
