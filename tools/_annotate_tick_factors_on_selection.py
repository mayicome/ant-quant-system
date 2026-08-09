# -*- coding: utf-8 -*-
"""给已有选股/成交汇总表挂上选股日分时因子列（不淘汰）。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from utils.tick_session_factors import compute_session_factors

FACTOR_COLS = [
    "分时_午后收益",
    "分时_尾盘收益",
    "分时_收盘相对VWAP",
    "分时_早盘量占比",
    "分时_早盘收益",
    "分时_日收益",
    "分时_10点后收益",
    "分时_VWAP",
    "分时_开盘价",
    "分时_收盘价",
    "分时_样本数",
]


def _code6(v) -> str:
    s = str(v or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def annotate(df: pd.DataFrame) -> pd.DataFrame:
    date_col = "选股日" if "选股日" in df.columns else None
    if date_col is None:
        raise ValueError("缺少选股日列")
    code_col = None
    for c in ("股票代码", "代码", "code"):
        if c in df.columns:
            code_col = c
            break
    if code_col is None:
        raise ValueError("缺少股票代码列")

    out = df.copy()
    for c in FACTOR_COLS:
        if c not in out.columns:
            out[c] = None

    cache = {}
    miss = 0
    for i, row in out.iterrows():
        c6 = _code6(row.get(code_col))
        d = row.get(date_col)
        key = (c6, str(d)[:10])
        if key not in cache:
            try:
                cache[key] = compute_session_factors(c6, d)
            except Exception:
                cache[key] = None
        fac = cache[key]
        if not fac:
            miss += 1
            continue
        for c in FACTOR_COLS:
            if c in fac:
                out.at[i, c] = fac[c]
    out.attrs["tick_miss"] = miss
    out.attrs["tick_cache_n"] = len(cache)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "src",
        nargs="?",
        default=str(
            Path(__file__).resolve().parents[1]
            / "history_data"
            / "八月回测-热门"
            / "各日选股收益汇总_新规则.xlsx"
        ),
    )
    ap.add_argument(
        "-o",
        "--out",
        default="",
        help="默认：同目录 *_带分时因子.xlsx",
    )
    args = ap.parse_args()
    src = Path(args.src)
    out = Path(args.out) if args.out else src.with_name(src.stem + "_带分时因子.xlsx")
    df = pd.read_excel(src, sheet_name=0)
    ann = annotate(df)
    # put factor cols near MA columns if present
    cols = list(ann.columns)
    for c in FACTOR_COLS:
        if c in cols:
            cols.remove(c)
    insert_at = cols.index("均线差占比") + 1 if "均线差占比" in cols else len(cols)
    cols = cols[:insert_at] + FACTOR_COLS + cols[insert_at:]
    ann = ann[cols]
    ann.to_excel(out, index=False, sheet_name="汇总")
    print("wrote", out)
    print("rows", len(ann), "unique_keys", ann.attrs.get("tick_cache_n"), "miss", ann.attrs.get("tick_miss"))
    for c in ("分时_午后收益", "分时_尾盘收益", "分时_收盘相对VWAP", "分时_早盘量占比"):
        s = pd.to_numeric(ann[c], errors="coerce")
        print(
            c,
            "n=",
            int(s.notna().sum()),
            "mean=",
            round(float(s.mean()), 4) if s.notna().any() else None,
            "p10=",
            round(float(s.quantile(0.1)), 4) if s.notna().any() else None,
        )


if __name__ == "__main__":
    main()
