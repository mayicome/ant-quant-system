#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在单个 Excel 中查找「代码」列，在其后插入（或更新）「股票名称」列。

用法:
  python tools/add_stock_name_to_excel.py --in history_data/各日选股收益汇总.xlsx
  python tools/add_stock_name_to_excel.py --in a.xlsx --out b.xlsx
  python tools/add_stock_name_to_excel.py --in a.xlsx --sheet 汇总
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _get_stock_name_fn():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from utils.stock_info_manager import get_stock_name as gsn

        def _fn(code: str) -> str:
            try:
                n = gsn(code) or ""
                return "" if n == "未知名称" else n
            except Exception:
                return ""

        return _fn
    except Exception:
        return lambda _c: ""


def _cell_to_code(v) -> str:
    if v is None:
        return ""
    import pandas as pd

    if pd.isna(v):
        return ""
    s = str(v).strip()
    if not s:
        return ""
    try:
        n = int(float(s))
        return str(n).zfill(6)
    except (ValueError, TypeError, OverflowError):
        return s.zfill(6) if s.isdigit() else s


def add_stock_name_column(
    path_in: Path,
    path_out: Path,
    sheet: str | int = 0,
) -> None:
    import pandas as pd

    xl = pd.ExcelFile(path_in)
    if isinstance(sheet, int):
        if sheet < 0 or sheet >= len(xl.sheet_names):
            raise SystemExit(
                f"工作表序号 {sheet} 无效，共 {len(xl.sheet_names)} 个表: {xl.sheet_names}"
            )
        sheet_name = xl.sheet_names[sheet]
    else:
        sheet_name = sheet
        if sheet_name not in xl.sheet_names:
            raise SystemExit(f"工作表不存在: {sheet_name!r}，可选: {xl.sheet_names}")

    all_sheets = {name: pd.read_excel(path_in, sheet_name=name) for name in xl.sheet_names}
    df = all_sheets[sheet_name].copy()

    if "代码" not in df.columns:
        raise SystemExit(f"未找到列「代码」。当前列: {list(df.columns)}")

    if "股票名称" in df.columns:
        df = df.drop(columns=["股票名称"])

    idx = int(df.columns.get_loc("代码"))
    get_name = _get_stock_name_fn()
    codes = df["代码"].map(_cell_to_code)
    names = codes.map(lambda c: get_name(c) if c else "")

    df.insert(idx + 1, "股票名称", names)
    all_sheets[sheet_name] = df

    path_out.parent.mkdir(parents=True, exist_ok=True)
    same_file = path_out.resolve() == path_in.resolve()

    def _write(to: Path) -> None:
        with pd.ExcelWriter(to, engine="openpyxl") as writer:
            for name in xl.sheet_names:
                all_sheets[name].to_excel(writer, sheet_name=name, index=False)

    if same_file:
        fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=str(path_out.parent))
        os.close(fd)
        tmp_path = Path(tmp)
        try:
            _write(tmp_path)
            os.replace(tmp_path, path_out)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
    else:
        _write(path_out)


def main() -> int:
    ap = argparse.ArgumentParser(description="在 Excel 的「代码」列后增加「股票名称」")
    ap.add_argument("--in", dest="path_in", required=True, help="输入 .xlsx")
    ap.add_argument(
        "--out",
        default="",
        help="输出路径；省略则覆盖输入文件",
    )
    ap.add_argument(
        "--sheet",
        default="0",
        help="工作表名或从 0 开始的序号（默认 0）",
    )
    args = ap.parse_args()

    pin = Path(args.path_in)
    if not pin.is_file():
        print(f"找不到文件: {pin}", file=sys.stderr)
        return 1
    if pin.suffix.lower() != ".xlsx":
        print("仅支持 .xlsx", file=sys.stderr)
        return 1

    pout = Path(args.out) if args.out.strip() else pin

    sh = args.sheet.strip()
    try:
        sheet_arg: str | int = int(sh)
    except ValueError:
        sheet_arg = sh

    try:
        add_stock_name_column(pin, pout, sheet=sheet_arg)
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"处理失败: {e}", file=sys.stderr)
        return 1

    print(f"已写入: {pout.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
