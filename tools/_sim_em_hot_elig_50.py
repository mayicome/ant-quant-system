# -*- coding: utf-8 -*-
"""对比 Elig 30/40 vs 50/50 对东财热门夹档选股只数的影响（基于已有选股母集重放）。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

BASE = Path(r"d:\蚂蚁量化系统\history_data\东财热门夹档")
SEL = BASE / "选股结果_东财热门-无涨停-MA排列并集-含均线差对照_2026-06-01_2026-08-21.xls"


def _kind_ok(kind: str, elig: int, sec_hi: int, con_hi: int) -> bool:
    if elig < 1:
        return False
    k = str(kind or "").strip().lower()
    if k in ("concept", "概念"):
        return elig <= con_hi
    return elig <= sec_hi


def daily_counts(df: pd.DataFrame, sec_hi: int, con_hi: int) -> pd.Series:
    """按合格榜内序位 + 标签类型重放 Elig（ANY_TAG=False 已在选股结果中体现）。"""
    elig = pd.to_numeric(df.get("合格榜内序位"), errors="coerce")
    kind = df.get("合格榜标签类型", df.get("合格榜对应标签类型", ""))
    ok = [
        _kind_ok(str(kind.iloc[i]) if kind is not None else "", int(elig.iloc[i]) if pd.notna(elig.iloc[i]) else 0, sec_hi, con_hi)
        for i in range(len(df))
    ]
    sub = df.loc[ok].copy()
    sub["sel"] = pd.to_datetime(sub["选股日"], errors="coerce").dt.date
    return sub.groupby("sel").size()


def main() -> None:
    df = pd.read_excel(SEL, engine="xlrd")
    old = daily_counts(df, 30, 40)
    new = daily_counts(df, 50, 50)
    days = sorted(set(old.index) | set(new.index))
    rows = []
    for d in days:
        o = int(old.get(d, 0))
        n = int(new.get(d, 0))
        rows.append({"sel": d, "old": o, "new": n, "delta": n - o})
    tab = pd.DataFrame(rows)
    print(f"选股日 {len(tab)}  旧Elig30/40有票日 {(tab.old>0).sum()}  新Elig50/50有票日 {(tab.new>0).sum()}")
    print(f"日均只数: 旧 {tab.old.mean():.1f}  新 {tab.new.mean():.1f}  增量 {tab.delta.mean():.1f}")
    print(f"新增有票日(0→>0): {((tab.old==0)&(tab.new>0)).sum()}")
    print(f"旧0新0: {((tab.old==0)&(tab.new==0)).sum()}")
    extra = tab[tab.delta > 0].sort_values("delta", ascending=False).head(8)
    if not extra.empty:
        print("\n增量最多的几天:")
        for _, r in extra.iterrows():
            print(f"  {r['sel']}  {int(r['old'])} -> {int(r['new'])}  (+{int(r['delta'])})")


if __name__ == "__main__":
    main()
