# -*- coding: utf-8 -*-
"""从并集选股/收益汇总中拆出 MA5<MA10<MA20（只用表内 MA 列，不重算日线）。

用法:
  python tools/split_ma5_lt_ma10_lt_ma20.py 路径.xlsx
  python tools/split_ma5_lt_ma10_lt_ma20.py 路径.xlsx -o 输出.xlsx

必须含 MA5、MA10、MA20（或 5日线/10日线/20日线）。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def _read(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".xls":
        return pd.read_excel(path, engine="xlrd")
    return pd.read_excel(path)


def _pick(df: pd.DataFrame, *names: str):
    cols = {str(c).upper(): c for c in df.columns}
    for n in names:
        if n in df.columns:
            return n
        if n.upper() in cols:
            return cols[n.upper()]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="并集选股结果或各日选股收益汇总（须含MA列）")
    ap.add_argument("-o", "--out", default="", help="默认：原名_仅MA5lt10lt20.xlsx")
    args = ap.parse_args()
    src = Path(args.src)
    if not src.is_file():
        raise SystemExit("文件不存在: %s" % src)
    df = _read(src)
    c5 = _pick(df, "MA5", "5日线")
    c10 = _pick(df, "MA10", "10日线")
    c20 = _pick(df, "MA20", "20日线")
    if not (c5 and c10 and c20):
        raise SystemExit(
            "表内缺少 MA5/MA10/MA20（或5日线/10日线/20日线）。"
            "请用选股导出或带均线列的收益汇总，本工具不重算日线。"
        )
    m5 = pd.to_numeric(df[c5], errors="coerce")
    m10 = pd.to_numeric(df[c10], errors="coerce")
    m20 = pd.to_numeric(df[c20], errors="coerce")
    mask = m5.notna() & m10.notna() & m20.notna() & (m5 < m10) & (m10 < m20)
    out_df = df.loc[mask].copy()
    out = Path(args.out) if args.out else src.with_name(src.stem + "_仅MA5lt10lt20.xlsx")
    out_df.to_excel(out, index=False)
    print("source", src)
    print("filter 表内列:", c5, c10, c20)
    print("rows", len(df), "->", len(out_df))
    print("wrote", out)
    if "收益率pct" in out_df.columns:
        y = pd.to_numeric(out_df["收益率pct"], errors="coerce")
        y0 = pd.to_numeric(df["收益率pct"], errors="coerce")
        print(
            "收益 全样本 mean=%.4f n=%d | 仅空头排列 mean=%.4f n=%d"
            % (
                float(y0.mean()),
                int(y0.notna().sum()),
                float(y.mean()),
                int(y.notna().sum()),
            )
        )


if __name__ == "__main__":
    main()
