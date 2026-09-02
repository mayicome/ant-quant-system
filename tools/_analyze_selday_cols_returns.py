# -*- coding: utf-8 -*-
"""分析收益汇总中「_选股日」对照列与收益率pct的关系。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"d:\蚂蚁量化系统\history_data\马总选股逻辑")
RET_COL = "收益率pct"


def _pick_summary() -> Path:
    for pat in (
        "各日选股收益汇总_日线-ma10-sell_half-按票_带回填_带次日MA10_latest.xlsx",
        "各日选股收益汇总_日线-ma10-sell_half-按票_带回填_latest.xlsx",
        "各日选股收益汇总_日线-ma10-sell_half-按票_latest.xlsx",
    ):
        p = BASE / pat
        if p.is_file():
            return p
    files = sorted(BASE.glob("各日选股收益汇总*.xlsx"), key=lambda x: x.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError("未找到各日选股收益汇总")
    return files[0]


def bucket_stats(df: pd.DataFrame, col: str, label: str, bins: int = 5) -> list[dict]:
    sub = df[[col, RET_COL]].dropna()
    if len(sub) < 50:
        return []
    try:
        sub = sub.copy()
        sub["q"] = pd.qcut(sub[col], bins, duplicates="drop")
    except ValueError:
        sub["q"] = pd.cut(sub[col], bins, duplicates="drop")
    out = []
    for q, g in sub.groupby("q", observed=True):
        r = g[RET_COL]
        out.append(
            dict(
                factor=label,
                bucket=str(q),
                n=len(g),
                mean=float(r.mean()),
                med=float(r.median()),
                win=float((r > 0).mean() * 100),
            )
        )
    return out


def flag_stats(df: pd.DataFrame, mask: pd.Series, label: str) -> dict | None:
    sub = df.loc[mask & df[RET_COL].notna(), RET_COL]
    if len(sub) < 10:
        return None
    return dict(
        label=label,
        n=len(sub),
        mean=float(sub.mean()),
        med=float(sub.median()),
        win=float((sub > 0).mean() * 100),
    )


def main() -> None:
    path = _pick_summary()
    print(f"FILE: {path.name}")
    df = pd.read_excel(path, sheet_name=0)
    selday_cols = [c for c in df.columns if str(c).endswith("_选股日")]
    print(f"rows={len(df)}  _选股日列={len(selday_cols)}")

    num_cols: list[str] = []
    for c in selday_cols:
        s = pd.to_numeric(df[c], errors="coerce")
        if s.notna().sum() >= 50:
            num_cols.append(c)
            df[c] = s

    df[RET_COL] = pd.to_numeric(df[RET_COL], errors="coerce")
    valid = df[RET_COL].notna()
    print(f"valid ret rows: {valid.sum()}")

    # 1) correlation
    rows = []
    for c in num_cols:
        sub = df[[c, RET_COL]].dropna()
        if len(sub) < 30:
            continue
        rows.append(
            dict(
                col=c,
                n=len(sub),
                pearson=sub[c].corr(sub[RET_COL]),
                spearman=sub[c].corr(sub[RET_COL], method="spearman"),
            )
        )
    corr_df = pd.DataFrame(rows).sort_values("spearman", key=abs, ascending=False)
    print("\n=== Correlation with 收益率pct ===")
    for _, r in corr_df.iterrows():
        print(
            f"  {r['col']:32s} n={int(r['n']):4d}  "
            f"pearson={r['pearson']:+.4f}  spearman={r['spearman']:+.4f}"
        )

    all_bucket: list[dict] = []
    for c in [
        "近5日RS_选股日",
        "近10日RS_选股日",
        "近20日RS_选股日",
        "主力净流入_万元_选股日",
        "主力净流入-净占比_选股日",
        "流通市值_亿_选股日",
        "均线差占比_选股日",
    ]:
        if c in df.columns:
            all_bucket.extend(bucket_stats(df, c, c))

    for c in ["所属行业最高排名名次_选股日", "所属概念最高排名名次_选股日"]:
        if c not in df.columns:
            continue
        df[c] = pd.to_numeric(df[c], errors="coerce")
        sub = df[[c, RET_COL]].dropna().copy()
        sub["rank_bin"] = pd.cut(
            sub[c],
            bins=[0, 3, 10, 30, 100, 9999],
            labels=["1-3", "4-10", "11-30", "31-100", "100+"],
        )
        for b, g in sub.groupby("rank_bin", observed=True):
            r = g[RET_COL]
            all_bucket.append(
                dict(
                    factor=c,
                    bucket=str(b),
                    n=len(g),
                    mean=float(r.mean()),
                    med=float(r.median()),
                    win=float((r > 0).mean() * 100),
                )
            )

    ma5 = pd.to_numeric(df.get("MA5_选股日"), errors="coerce")
    ma10 = pd.to_numeric(df.get("MA10_选股日"), errors="coerce")
    ma20 = pd.to_numeric(df.get("MA20_选股日"), errors="coerce")
    close = pd.to_numeric(df.get("收盘价_选股日"), errors="coerce")
    has_ma = ma5.notna() & ma10.notna() & ma20.notna() & df[RET_COL].notna()

    flags = {
        "MA多头_MA5>MA10>MA20": has_ma & (ma5 > ma10) & (ma10 > ma20),
        "MA空头_MA5<MA10<MA20": has_ma & (ma5 < ma10) & (ma10 < ma20),
        "收盘>MA10": has_ma & close.notna() & (close > ma10),
        "收盘<MA10": has_ma & close.notna() & (close < ma10),
        "收盘>MA20": has_ma & close.notna() & (close > ma20),
        "收盘<MA20": has_ma & close.notna() & (close < ma20),
        "收盘在MA5上方": has_ma & close.notna() & (close > ma5),
        "收盘在MA5下方": has_ma & close.notna() & (close < ma5),
    }
    print("\n=== MA / 收盘位置 vs 收益 ===")
    flag_rows: list[dict] = []
    for k, m in flags.items():
        d = flag_stats(df, m, k)
        if d:
            flag_rows.append(d)
            print(
                f"  {d['label']:25s} n={d['n']:4d}  "
                f"mean={d['mean']:+.3f}%  med={d['med']:+.3f}%  win={d['win']:.1f}%"
            )

    # compare with non-_选股日 columns
    print("\n=== 对照列 vs 原列差异 ===")
    for base_c, sel_c in [
        ("MA5", "MA5_选股日"),
        ("MA10", "MA10_选股日"),
        ("MA20", "MA20_选股日"),
        ("近5日RS", "近5日RS_选股日"),
        ("均线差占比", "均线差占比_选股日"),
        ("收盘价", "收盘价_选股日"),
    ]:
        if base_c not in df.columns or sel_c not in df.columns:
            continue
        a = pd.to_numeric(df[base_c], errors="coerce")
        b = pd.to_numeric(df[sel_c], errors="coerce")
        both = a.notna() & b.notna()
        if both.sum() == 0:
            continue
        diff = (a[both] - b[both]).abs()
        print(
            f"  {base_c:12s} vs {sel_c:20s}: "
            f"both非空={both.sum():4d}  median|diff|={diff.median():.6f}  max|diff|={diff.max():.6f}"
        )

    print("\n=== Quintile highlights (mean ret) ===")
    bdf = pd.DataFrame(all_bucket)
    for fac in bdf["factor"].unique():
        sub = bdf[bdf["factor"] == fac]
        if sub.empty:
            continue
        lo, hi = sub.iloc[0], sub.iloc[-1]
        spread = hi["mean"] - lo["mean"]
        print(
            f"  {fac}: Q1 mean={lo['mean']:+.3f}% → Q{len(sub)} mean={hi['mean']:+.3f}%  "
            f"spread={spread:+.3f}pp  (total n={int(sub['n'].sum())})"
        )

    # close vs MA distance buckets
    if close.notna().any() and ma10.notna().any():
        df["close_ma10_pct"] = (close - ma10) / ma10 * 100
        all_bucket.extend(bucket_stats(df, "close_ma10_pct", "收盘相对MA10_pct"))

    # multivariate: top/bottom RS + above MA10
    rs10 = pd.to_numeric(df.get("近10日RS_选股日"), errors="coerce")
    if rs10.notna().sum() > 100:
        q80 = rs10.quantile(0.8)
        q20 = rs10.quantile(0.2)
        combos = [
            ("RS10高+收盘>MA10", (rs10 >= q80) & (close > ma10)),
            ("RS10低+收盘>MA10", (rs10 <= q20) & (close > ma10)),
            ("RS10高+收盘<MA10", (rs10 >= q80) & (close < ma10)),
            ("RS10低+收盘<MA10", (rs10 <= q20) & (close < ma10)),
        ]
        print("\n=== RS10 × 收盘/MA10 组合 ===")
        combo_rows = []
        for label, m in combos:
            d = flag_stats(df, m & df[RET_COL].notna(), label)
            if d:
                combo_rows.append(d)
                print(
                    f"  {d['label']:22s} n={d['n']:4d}  "
                    f"mean={d['mean']:+.3f}%  win={d['win']:.1f}%"
                )
    else:
        combo_rows = []

    out = BASE / "_analysis_selday_cols.xlsx"
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        corr_df.to_excel(w, sheet_name="correlation", index=False)
        pd.DataFrame(flag_rows).to_excel(w, sheet_name="ma_flags", index=False)
        bdf.to_excel(w, sheet_name="quintiles", index=False)
        if combo_rows:
            pd.DataFrame(combo_rows).to_excel(w, sheet_name="combos", index=False)
    print(f"\nWROTE {out}")


if __name__ == "__main__":
    main()
