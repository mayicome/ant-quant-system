# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

spec = importlib.util.spec_from_file_location(
    "base", Path(__file__).with_name("_analyze_selday_cols_returns.py")
)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

path = base._pick_summary()
df = pd.read_excel(path)
ret = pd.to_numeric(df["收益率pct"], errors="coerce")

pairs = [
    ("近10日RS", "近10日RS_选股日"),
    ("近5日RS", "近5日RS_选股日"),
    ("均线差占比", "均线差占比_选股日"),
    ("MA5", "MA5_选股日"),
]
print("=== 原列 vs _选股日 列：谁更能预测收益？ ===")
for a, b in pairs:
    for c in (a, b):
        s = pd.to_numeric(df[c], errors="coerce")
        sub = pd.concat([s, ret], axis=1).dropna()
        if len(sub) < 100:
            continue
        sp = sub.iloc[:, 0].corr(sub.iloc[:, 1], method="spearman")
        try:
            sub = sub.copy()
            sub["q"] = pd.qcut(sub.iloc[:, 0], 5, duplicates="drop")
            means = [g.iloc[:, 1].mean() for _, g in sub.groupby("q", observed=True)]
            spread = means[-1] - means[0]
        except Exception:
            spread = np.nan
        print(
            f"  {c:22s} spearman={sp:+.4f}  Q5-Q1 spread={spread:+.3f}pp  n={len(sub)}"
        )

print("\n=== 概念/行业排名分层（名次越小=越热）===")
for c in ["所属概念最高排名名次_选股日", "所属行业最高排名名次_选股日"]:
    s = pd.to_numeric(df[c], errors="coerce")
    sub = pd.DataFrame({"rank": s, "ret": ret}).dropna()
    for lo, hi, lab in [
        (1, 3, "Top1-3"),
        (4, 10, "4-10"),
        (11, 30, "11-30"),
        (31, 100, "31-100"),
        (101, 9999, "100+"),
    ]:
        m = (sub["rank"] >= lo) & (sub["rank"] <= hi)
        g = sub.loc[m, "ret"]
        if len(g) < 10:
            continue
        tag = "概念" if "概念" in c else "行业"
        print(
            f"  {tag} {lab:6s} n={len(g):4d} mean={g.mean():+.3f}% win={(g>0).mean()*100:.1f}%"
        )


def quintile_detail(col: str, title: str) -> None:
    s = pd.to_numeric(df[col], errors="coerce")
    sub = pd.DataFrame({"x": s, "ret": ret}).dropna()
    if len(sub) < 50:
        print(f"\n=== {title}: 数据不足 ===")
        return
    sub["q"] = pd.qcut(sub["x"], 5, duplicates="drop")
    print(f"\n=== {title} ===")
    for q, g in sub.groupby("q", observed=True):
        print(
            f"  {str(q):30s} n={len(g):4d} mean={g.ret.mean():+.3f}% "
            f"med={g.ret.median():+.3f}% win={(g.ret>0).mean()*100:.1f}%"
        )


quintile_detail("主力净流入_万元_选股日", "主力净流入_万元_选股日")
quintile_detail("流通市值_亿_选股日", "流通市值_亿_选股日")
quintile_detail("近10日RS_选股日", "近10日RS_选股日")

close = pd.to_numeric(df["收盘价_选股日"], errors="coerce")
ma10 = pd.to_numeric(df["MA10_选股日"], errors="coerce")
df["_tmp_pct"] = (close - ma10) / ma10 * 100
quintile_detail("_tmp_pct", "收盘相对MA10_选股日(%)")
