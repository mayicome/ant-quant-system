# -*- coding: utf-8 -*-
"""7/3、7/6、7/9：各日选股收益汇总.xlsx（无 Cond123）全量收益 vs 新规则215。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门")
DAYS = ["2026-07-03", "2026-07-06", "2026-07-09"]
OUT = ROOT / "7月3_6_9_全量选股收益.xlsx"


def main() -> None:
    full = pd.read_excel(ROOT / "各日选股收益汇总.xlsx")
    full["_d"] = full["选股日"].astype(str).str[:10]
    full["收益率pct"] = pd.to_numeric(full["收益率pct"], errors="coerce")

    nr = pd.read_excel(ROOT / "各日选股收益汇总_新规则.xlsx")
    nr["_d"] = nr["选股日"].astype(str).str[:10]
    nr["收益率pct"] = pd.to_numeric(nr["收益率pct"], errors="coerce")

    rows = []
    detail_frames = []
    for d in DAYS:
        f = full[full["_d"] == d].copy()
        n = nr[nr["_d"] == d].copy()
        rows.append(
            {
                "选股日": d,
                "全量n": len(f),
                "全量均收益%": float(f["收益率pct"].mean()),
                "全量中位%": float(f["收益率pct"].median()),
                "全量胜率%": float((f["收益率pct"] > 0).mean() * 100),
                "全量负收益占比%": float((f["收益率pct"] < 0).mean() * 100),
                "全量min%": float(f["收益率pct"].min()),
                "全量max%": float(f["收益率pct"].max()),
                "Cond123_n": len(n),
                "Cond123均收益%": float(n["收益率pct"].mean()) if len(n) else None,
                "Cond123胜率%": float((n["收益率pct"] > 0).mean() * 100) if len(n) else None,
            }
        )
        f = f.sort_values("收益率pct")
        f.insert(0, "池", "全量无Cond123")
        detail_frames.append(f)

        print(
            "%s 全量 n=%d mean=%+.2f%% med=%+.2f%% win=%.1f%%  range=[%+.2f, %+.2f]"
            % (
                d,
                len(f),
                f["收益率pct"].mean(),
                f["收益率pct"].median(),
                (f["收益率pct"] > 0).mean() * 100,
                f["收益率pct"].min(),
                f["收益率pct"].max(),
            )
        )
        print(
            "       Cond123 n=%d mean=%+.2f%% win=%.1f%%"
            % (
                len(n),
                n["收益率pct"].mean(),
                (n["收益率pct"] > 0).mean() * 100,
            )
        )
        cols = [
            c
            for c in ["代码", "股票名称", "收益率pct", "买入日", "end_date"]
            if c in f.columns
        ]
        print("  最差5:\n", f.head(5)[cols].to_string(index=False))
        print("  最好5:\n", f.tail(5)[cols].to_string(index=False))
        r = f["收益率pct"]
        for lo, hi, lab in [
            (None, -5, "≤-5%"),
            (-5, -2, "-5~-2%"),
            (-2, 0, "-2~0%"),
            (0, 2, "0~2%"),
            (2, 5, "2~5%"),
            (5, None, ">5%"),
        ]:
            if lo is None:
                m = r <= hi
            elif hi is None:
                m = r > lo
            else:
                m = (r > lo) & (r <= hi)
            print("  %s: %d (%.1f%%)" % (lab, int(m.sum()), m.mean() * 100))

    summary = pd.DataFrame(rows)
    with pd.ExcelWriter(OUT, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="对照摘要", index=False)
        for d, f in zip(DAYS, detail_frames):
            f.to_excel(w, sheet_name=d.replace("-", ""), index=False)
        nr[nr["_d"].isin(DAYS)].to_excel(w, sheet_name="Cond123那10笔", index=False)

    print("wrote", OUT)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
