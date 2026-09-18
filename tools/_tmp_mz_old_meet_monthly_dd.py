# -*- coding: utf-8 -*-
"""旧规则（满足条件）· 最终目录：按月收益 + 月内回撤"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "history_data" / "马总选股逻辑" / "最终"
OUT_DIR = ROOT / "history_data" / "马总选股逻辑" / "过滤_旧满足_最终"


def as_bool(s: pd.Series) -> pd.Series:
    return s.map(
        lambda x: True
        if x is True or str(x).strip().lower() in ("true", "1", "是", "真")
        else False
    )


def max_drawdown(equity: np.ndarray) -> float:
    if equity is None or len(equity) == 0:
        return 0.0
    eq = np.asarray(equity, dtype=float)
    peak = np.maximum.accumulate(eq)
    return float((eq - peak).min())


def main() -> None:
    hits = list(BASE.glob("*按票_已完成_收盘上MA10_latest.xlsx"))
    if not hits:
        hits = list(BASE.glob("*按票_已完成_latest.xlsx"))
    path = hits[0]
    df = pd.read_excel(path)
    buy = pd.to_datetime(df.get("买入日"), errors="coerce")
    meet = as_bool(df["满足条件"])
    d = pd.DataFrame(
        {
            "buy": buy[meet].dt.normalize(),
            "ret": pd.to_numeric(df.loc[meet, "收益率pct"], errors="coerce"),
        }
    ).dropna(subset=["buy", "ret"])

    day = (
        d.groupby("buy", as_index=False)
        .agg(n=("ret", "size"), day_mean=("ret", "mean"))
        .sort_values("buy")
        .reset_index(drop=True)
    )
    day["cum"] = day["day_mean"].cumsum()
    day["dd_from_peak"] = day["cum"] - day["cum"].cummax()
    day["ym"] = day["buy"].dt.to_period("M").astype(str)

    print("文件", path.name)
    print(
        "旧规则=满足条件 | 最终目录 | n票=%d 开买日=%d %s -> %s"
        % (len(d), len(day), d["buy"].min().date(), d["buy"].max().date())
    )
    print("权益=开买日票均累加(pp) | 月内DD=月内相对峰值回落")
    print()
    print(
        "%-8s %5s %5s %9s %9s %9s %10s"
        % ("月份", "开买日", "票", "月收益pp", "月内最大DD", "月末全局DD", "月末累计")
    )

    rows = []
    for ym, g in day.groupby("ym", sort=True):
        tickets = d[d["buy"].dt.to_period("M").astype(str) == ym]["ret"]
        local = g["day_mean"].cumsum().to_numpy()
        mdd = max_drawdown(local)
        month_ret = float(g["day_mean"].sum())
        end_dd = float(g["dd_from_peak"].iloc[-1])
        end_cum = float(g["cum"].iloc[-1])
        rows.append(
            {
                "月份": ym,
                "开买日数": len(g),
                "完成票数": int(len(tickets)),
                "日均只数": round(float(g["n"].mean()), 2),
                "票均%": round(float(tickets.mean()), 2),
                "月收益_开买日均累加_pp": round(month_ret, 2),
                "月内最大回撤_pp": round(mdd, 2),
                "月末相对全局峰值回撤_pp": round(end_dd, 2),
                "月末全局累计_pp": round(end_cum, 2),
            }
        )
        print(
            "%-8s %5d %5d %+9.2f %+9.2f %+9.2f %+10.2f"
            % (ym, len(g), len(tickets), month_ret, mdd, end_dd, end_cum)
        )

    print()
    print("分年:")
    day["y"] = day["buy"].dt.year
    year_rows = []
    for y, g in day.groupby("y"):
        tickets = d[d["buy"].dt.year == y]["ret"]
        local = g["day_mean"].cumsum().to_numpy()
        mdd = max_drawdown(local)
        year_rows.append(
            {
                "年": int(y),
                "开买日数": len(g),
                "完成票数": int(len(tickets)),
                "票均%": round(float(tickets.mean()), 2),
                "年收益_pp": round(float(g["day_mean"].sum()), 2),
                "年内最大回撤_pp": round(mdd, 2),
                "年末全局累计_pp": round(float(g["cum"].iloc[-1]), 2),
                "年末全局回撤_pp": round(float(g["dd_from_peak"].iloc[-1]), 2),
            }
        )
        print(
            "  %d 票均=%+.2f%% 年收益=%+.1fpp 年内最大DD=%+.1fpp 年末累计=%+.1f 年末全局DD=%+.1f"
            % (
                int(y),
                float(tickets.mean()),
                float(g["day_mean"].sum()),
                mdd,
                float(g["cum"].iloc[-1]),
                float(g["dd_from_peak"].iloc[-1]),
            )
        )

    mdd_all = max_drawdown(day["cum"].to_numpy())
    print()
    print(
        "全样本(最终段): 累计=%+.1fpp 最大回撤=%+.1fpp"
        % (float(day["cum"].iloc[-1]), mdd_all)
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    xlsx = OUT_DIR / "按月回撤_旧满足_最终.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        pd.DataFrame(rows).to_excel(w, sheet_name="每月回撤", index=False)
        pd.DataFrame(year_rows).to_excel(w, sheet_name="每年回撤", index=False)
        out_day = day.copy()
        out_day["buy"] = out_day["buy"].dt.strftime("%Y-%m-%d")
        out_day.to_excel(w, sheet_name="开买日权益曲线", index=False)
    print("wrote", xlsx)


if __name__ == "__main__":
    main()
