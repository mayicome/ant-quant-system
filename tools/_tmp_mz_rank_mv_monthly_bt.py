# -*- coding: utf-8 -*-
"""马总新规则（排名5-20∧市值<50）按月回测汇总：日度篮子 → 月度。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "history_data" / "马总选股逻辑"
OUT_DIR = BASE / "过滤_排名5-20_市值lt50"


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
                "buy": buy.loc[mask].dt.normalize(),
                "ret": pd.to_numeric(df.loc[mask, "收益率pct"], errors="coerce"),
                "code": df.loc[mask, "代码" if "代码" in df.columns else "股票代码"].astype(str),
            }
        )
        frames.append(t)

    d = pd.concat(frames, ignore_index=True).dropna(subset=["buy", "ret"])
    d["ym"] = d["buy"].dt.to_period("M").astype(str)

    # 日度篮子：当日开买票的简单平均收益、只数
    day = (
        d.groupby("buy", as_index=False)
        .agg(n=("ret", "size"), day_mean=("ret", "mean"), day_win=("ret", lambda s: (s > 0).mean() * 100))
        .sort_values("buy")
    )
    day["ym"] = day["buy"].dt.to_period("M").astype(str)

    month_rows = []
    print(
        "口径: 新规则过滤后的已完成票 | 日度=当日开买票均 | 月度=该月各开买日的日均再平均"
    )
    print("区间", d["buy"].min().date(), "->", d["buy"].max().date(), "票", len(d), "开买日", len(day))
    print()
    hdr = "%-8s %5s %5s %5s %9s %9s %7s %9s %8s %8s" % (
        "月份",
        "开买日",
        "票数",
        "日均只",
        "月票均%",
        "月日均%",
        "日胜%",
        "月累加日均",
        "最好日%",
        "最差日%",
    )
    print(hdr)
    for ym, g in day.groupby("ym", sort=True):
        tickets = d[d["ym"] == ym]["ret"]
        n_days = len(g)
        n_tr = int(tickets.shape[0])
        avg_n = float(g["n"].mean())
        ticket_mean = float(tickets.mean())
        day_mean = float(g["day_mean"].mean())  # 各开买日票均 再平均
        day_win = float((g["day_mean"] > 0).mean() * 100)  # 开买日胜率（日均>0）
        cum = float(g["day_mean"].sum())  # 简单加总各日票均
        best = float(g["day_mean"].max())
        worst = float(g["day_mean"].min())
        month_rows.append(
            {
                "月份": ym,
                "开买日数": n_days,
                "完成票数": n_tr,
                "日均开买只数": round(avg_n, 2),
                "月_票均%": round(ticket_mean, 2),
                "月_开买日均%": round(day_mean, 2),
                "开买日胜率%": round(day_win, 1),
                "月_开买日均累加": round(cum, 2),
                "最好开买日%": round(best, 2),
                "最差开买日%": round(worst, 2),
            }
        )
        print(
            "%-8s %5d %5d %5.1f %+9.2f %+9.2f %6.1f %+9.2f %+8.2f %+8.2f"
            % (ym, n_days, n_tr, avg_n, ticket_mean, day_mean, day_win, cum, best, worst)
        )

    # 分年
    print()
    print("分年（开买日均再平均）:")
    day["y"] = day["buy"].dt.year
    year_rows = []
    for y, g in day.groupby("y"):
        tickets = d[d["buy"].dt.year == y]["ret"]
        year_rows.append(
            {
                "年": int(y),
                "开买日数": len(g),
                "完成票数": int(len(tickets)),
                "日均开买只数": round(float(g["n"].mean()), 2),
                "票均%": round(float(tickets.mean()), 2),
                "开买日均%": round(float(g["day_mean"].mean()), 2),
                "开买日胜率%": round(float((g["day_mean"] > 0).mean() * 100), 1),
                "开买日均累加": round(float(g["day_mean"].sum()), 2),
            }
        )
        print(
            "  %d 开买日=%d 票=%d 日均只=%.1f 票均=%+.2f%% 日均=%+.2f%% 日胜=%.1f%% 累加日均=%+.1f"
            % (
                int(y),
                len(g),
                len(tickets),
                float(g["n"].mean()),
                float(tickets.mean()),
                float(g["day_mean"].mean()),
                float((g["day_mean"] > 0).mean() * 100),
                float(g["day_mean"].sum()),
            )
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    xlsx = OUT_DIR / "按月回测_排名市值_三年.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        pd.DataFrame(month_rows).to_excel(w, sheet_name="按月回测", index=False)
        pd.DataFrame(year_rows).to_excel(w, sheet_name="按年", index=False)
        day_out = day.copy()
        day_out["buy"] = day_out["buy"].dt.strftime("%Y-%m-%d")
        day_out.to_excel(w, sheet_name="开买日明细", index=False)
    print("wrote", xlsx)


if __name__ == "__main__":
    main()
