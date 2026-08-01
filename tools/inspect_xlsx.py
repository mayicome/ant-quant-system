#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打印 xlsx 的 sheet 名、列名与前几行，便于对齐「封单结构」与「昨日涨停」字段。"""

import argparse
import os
import sys
from typing import Optional


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _default_xlsx_path() -> Optional[str]:
    """未传路径时：在 history_data 下找 *昨日涨停*.xlsx，取最近修改的一个。"""
    hd = os.path.join(_repo_root(), "history_data")
    if not os.path.isdir(hd):
        return None
    cands = []
    for name in os.listdir(hd):
        if not name.lower().endswith(".xlsx"):
            continue
        if "昨日涨停" in name:
            fp = os.path.join(hd, name)
            try:
                m = os.path.getmtime(fp)
            except OSError:
                m = 0
            cands.append((m, fp))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0], reverse=True)
    return cands[0][1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect Excel columns and sample rows",
        epilog=(
            "示例:\n"
            '  python tools/inspect_xlsx.py "history_data/4月16日昨日涨停.xlsx"\n'
            "  python tools/inspect_xlsx.py   # 不传路径时自动在 history_data 下找 *昨日涨停*.xlsx"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="xlsx 路径；省略则在 history_data 下自动匹配文件名含「昨日涨停」的 xlsx",
    )
    parser.add_argument("--rows", type=int, default=5, help="sample rows (default 5)")
    args = parser.parse_args()

    path = args.path
    if not path:
        path = _default_xlsx_path()
        if path:
            print(f"[默认文件] {path}\n", file=sys.stderr)
    if not path:
        print(
            "请指定 xlsx 路径，例如:\n"
            '  python tools/inspect_xlsx.py "history_data/4月16日昨日涨停.xlsx"\n'
            "或在 history_data 下放一个文件名含「昨日涨停」的 xlsx 后再运行本脚本（无参数）。",
            file=sys.stderr,
        )
        return 1

    path = os.path.abspath(path)
    if not os.path.isfile(path):
        print(f"文件不存在: {path}", file=sys.stderr)
        return 1

    try:
        import pandas as pd
    except ImportError:
        print("需要: pip install pandas openpyxl", file=sys.stderr)
        return 1

    xl = pd.ExcelFile(path)
    print(f"文件: {path}")
    print(f"Sheets: {xl.sheet_names}")
    for sheet in xl.sheet_names:
        df = pd.read_excel(path, sheet_name=sheet)
        print()
        print(f"--- Sheet: {sheet} ---")
        print(f"shape: {df.shape}")
        print("columns:", list(df.columns))
        print(df.head(args.rows).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
