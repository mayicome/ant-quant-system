"""对指定汇总表做 7 参数组合收益分析。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_param_combo_returns import (  # noqa: E402
    PARAM_COLS,
    _exclude_st_and_688,
    combo_label,
    norm_tri,
)

import pandas as pd


def analyze(path: Path) -> None:
    df = pd.read_excel(path, sheet_name=0)
    raw_n = len(df)
    df = _exclude_st_and_688(df)
    ret_col = next(c for c in df.columns if "pct" in str(c).lower() or "收益率" in str(c))
    sub = df[df[ret_col].notna()].copy()
    for c in PARAM_COLS:
        sub[c] = sub[c].map(norm_tri)
    sub["combo"] = list(zip(*(sub[c] for c in PARAM_COLS)))

    g = (
        sub.groupby("combo", as_index=False)
        .agg(
            笔数=(ret_col, "count"),
            平均收益率pct=(ret_col, "mean"),
            中位收益率pct=(ret_col, "median"),
            胜率pct=(ret_col, lambda s: float((s > 0).mean() * 100)),
            累计收益率pct=(ret_col, "sum"),
        )
        .sort_values("平均收益率pct", ascending=False)
    )
    g["组合"] = g["combo"].map(combo_label)

    print(f"文件: {path.name}")
    print(
        f"原始 {raw_n} 笔，剔 ST/688 后 {len(df)} 笔，"
        f"有效 {len(sub)} 笔，组合 {len(g)} 种，全样本平均 {sub[ret_col].mean():.2f}%"
    )
    print()
    print("=== 全部组合排名 ===")
    for _, row in g.iterrows():
        line = (
            f"笔数={int(row['笔数']):2d}  平均={row['平均收益率pct']:7.2f}%  "
            f"中位={row['中位收益率pct']:7.2f}%  胜率={row['胜率pct']:5.1f}%  | {row['组合']}"
        )
        print(line)

    print()
    print("=== 样本 >=3 笔 ===")
    g3 = g[g["笔数"] >= 3]
    if g3.empty:
        print("(无)")
    else:
        for _, row in g3.iterrows():
            line = (
                f"笔数={int(row['笔数']):2d}  平均={row['平均收益率pct']:7.2f}%  "
                f"胜率={row['胜率pct']:5.1f}%  | {row['组合']}"
            )
            print(line)

    print()
    print("=== 单参数边际 ===")
    for c in PARAM_COLS:
        sg = (
            sub.groupby(c, as_index=False)
            .agg(笔数=(ret_col, "count"), 平均收益率pct=(ret_col, "mean"))
            .sort_values("平均收益率pct", ascending=False)
        )
        print(c + ":")
        for _, row in sg.iterrows():
            print(f"  {str(row[c]):5s}  n={int(row['笔数']):2d}  avg={row['平均收益率pct']:7.2f}%")

    out = path.with_name(path.stem + "_参数组合分析.csv")
    g.drop(columns=["combo"]).to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n已导出: {out}")


if __name__ == "__main__":
    p = (
        ROOT
        / "history_data"
        / "回测七月"
        / "各日选股收益汇总_6月_全部涨停后1-2日判断5日线上方不能有20日和30日线.xlsx"
    )
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
    analyze(p)
