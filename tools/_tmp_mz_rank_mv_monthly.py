# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "history_data" / "马总选股逻辑"
OUT_DIR = BASE / "过滤_排名5-20_市值lt50"


def met(r: pd.Series) -> dict:
    r = pd.to_numeric(r, errors="coerce").dropna()
    n = len(r)
    if not n:
        return {"n": 0}
    return {
        "n": n,
        "mean": float(r.mean()),
        "median": float(r.median()),
        "win": float((r > 0).mean() * 100),
        "sum": float(r.sum()),
    }


def main() -> None:
    frames = []
    for folder in (BASE / "2024", BASE / "最终"):
        hits = list(folder.glob("*按票_已完成_收盘上MA10_latest.xlsx"))
        if not hits:
            continue
        df = pd.read_excel(hits[0])
        buy = pd.to_datetime(df.get("买入日"), errors="coerce")
        rk = pd.to_numeric(df["最佳板块排名"], errors="coerce")
        mvcol = "流通市值_亿_选股日" if "流通市值_亿_选股日" in df.columns else "流通市值_亿"
        mv = pd.to_numeric(df[mvcol], errors="coerce")
        mask = (rk >= 5) & (rk <= 20) & (mv < 50)
        t = pd.DataFrame(
            {
                "_buy": buy.loc[mask],
                "_ret": pd.to_numeric(df.loc[mask, "收益率pct"], errors="coerce"),
            }
        )
        frames.append(t)

    d = pd.concat(frames, ignore_index=True).dropna(subset=["_buy", "_ret"])
    d["_ym"] = d["_buy"].dt.to_period("M").astype(str)
    d["_y"] = d["_buy"].dt.year

    print(
        "条件: 最佳排名[5,20] ∧ 市值<50亿 | n=%d %s -> %s"
        % (len(d), d["_buy"].min().date(), d["_buy"].max().date())
    )
    print()
    print("%-10s %5s %9s %9s %7s %9s" % ("月份", "n", "票均%", "中位%", "胜率%", "累加%"))
    rows = []
    for ym, g in d.groupby("_ym", sort=True):
        m = met(g["_ret"])
        rows.append(
            {
                "月份": ym,
                "n": m["n"],
                "票均%": round(m["mean"], 2),
                "中位%": round(m["median"], 2),
                "胜率%": round(m["win"], 1),
                "累加%": round(m["sum"], 1),
            }
        )
        print(
            "%-10s %5d %+9.2f %+9.2f %6.1f %+9.1f"
            % (ym, m["n"], m["mean"], m["median"], m["win"], m["sum"])
        )

    print()
    print("分年:")
    year_rows = []
    for y, g in d.groupby("_y"):
        m = met(g["_ret"])
        year_rows.append(
            {
                "年": int(y),
                "n": m["n"],
                "票均%": round(m["mean"], 2),
                "中位%": round(m["median"], 2),
                "胜率%": round(m["win"], 1),
                "累加%": round(m["sum"], 1),
            }
        )
        print(
            "  %d n=%d 票均=%+.2f%% 中位=%+.2f%% 胜率=%.1f%% 累加=%+.1f%%"
            % (int(y), m["n"], m["mean"], m["median"], m["win"], m["sum"])
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    xlsx = OUT_DIR / "分月_排名市值_三年.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        pd.DataFrame(rows).to_excel(w, sheet_name="分月", index=False)
        pd.DataFrame(year_rows).to_excel(w, sheet_name="分年", index=False)
    print("wrote", xlsx)


if __name__ == "__main__":
    main()
