#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""快速窥探 ant-quant JSONL：打印行数、键名、首行或按日过滤条数。

用法：
  python scripts/peek_jsonl.py path/to/file.jsonl
  python scripts/peek_jsonl.py path/to/file.jsonl --ymd 20260828 --limit 3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Peek ant-quant JSONL")
    ap.add_argument("path", type=Path)
    ap.add_argument("--ymd", default="", help="只统计/打印 trade_date_ymd 匹配的行")
    ap.add_argument("--limit", type=int, default=1, help="打印前 N 条匹配行")
    args = ap.parse_args()
    path: Path = args.path
    if not path.is_file():
        print("missing file:", path, file=sys.stderr)
        return 1

    total = 0
    matched = 0
    keys = None
    printed = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total += 1
            obj = json.loads(line)
            if keys is None:
                keys = list(obj.keys())
            if args.ymd and str(obj.get("trade_date_ymd") or "") != args.ymd:
                continue
            matched += 1
            if printed < args.limit:
                print(json.dumps(obj, ensure_ascii=False))
                printed += 1

    print("---", file=sys.stderr)
    print("file:", path, file=sys.stderr)
    print("total_lines:", total, file=sys.stderr)
    if args.ymd:
        print("matched_ymd:", args.ymd, matched, file=sys.stderr)
    print("keys:", keys, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
