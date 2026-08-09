# -*- coding: utf-8 -*-
"""Daily trade counts for Cond1+2 + MA5<MA10<MA20 (July open-clip)."""
from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path

BASE = Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门")
OUT = BASE / "Cond12_MA空头_每日笔数.xlsx"

FILES = {
    "anytag": BASE
    / "开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_各日选股收益汇总.xlsx",
    "besttest": BASE
    / "besttest_开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_各日选股收益汇总.xlsx",
}
CAL_FILES = {
    "anytag": BASE / "开盘夹档_各日选股收益汇总.xlsx",
    "besttest": BASE / "besttest_开盘夹档_各日选股收益汇总.xlsx",
}


def load_ma_lt(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=0)
    for c in ["MA5", "MA10", "MA20", "收益率pct"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[df["收益率pct"].notna()].copy()
    has = df["MA5"].notna() & df["MA10"].notna() & df["MA20"].notna()
    ma_lt = has & (df["MA5"] < df["MA10"]) & (df["MA10"] < df["MA20"])
    sub = df.loc[ma_lt].copy()
    sub["选股日"] = pd.to_datetime(sub["选股日"]).dt.strftime("%Y-%m-%d")
    if "买入日" in sub.columns:
        sub["买入日"] = pd.to_datetime(sub["买入日"]).dt.strftime("%Y-%m-%d")
    return sub


def calendar_dates() -> list[str]:
    dates: set[str] = set()
    for p in CAL_FILES.values():
        if not p.exists():
            continue
        d = pd.read_excel(p, sheet_name=0, usecols=["选股日"])
        dates.update(pd.to_datetime(d["选股日"]).dt.strftime("%Y-%m-%d").tolist())
    return sorted(dates)


def summarize(series: pd.Series, name: str) -> dict:
    n_days = len(series)
    n_pos = int((series > 0).sum())
    n_zero = int((series == 0).sum())
    pos = series[series > 0]
    return {
        "src": name,
        "total_trades": int(series.sum()),
        "calendar_days": n_days,
        "days_with_trades": n_pos,
        "days_with_0": n_zero,
        "min": int(series.min()),
        "median_all": float(series.median()),
        "max": int(series.max()),
        "mean_all": float(series.mean()),
        "mean_gt0_only": float(pos.mean()) if len(pos) else np.nan,
        "median_gt0_only": float(pos.median()) if len(pos) else np.nan,
        "min_gt0": int(pos.min()) if len(pos) else np.nan,
    }


def main() -> None:
    subs = {k: load_ma_lt(p) for k, p in FILES.items()}
    for k, s in subs.items():
        print(f"{k}: Cond1+2+MA5<MA10<MA20 n={len(s)}")

    all_dates = calendar_dates()
    print(f"calendar selection days n={len(all_dates)}")
    print("dates:", all_dates)

    rows = []
    for d in all_dates:
        a = int((subs["anytag"]["选股日"] == d).sum())
        b = int((subs["besttest"]["选股日"] == d).sum())
        rows.append({"选股日": d, "anytag_n": a, "besttest_n": b})
    daily = pd.DataFrame(rows)

    summary = pd.DataFrame(
        [
            summarize(daily["anytag_n"], "anytag"),
            summarize(daily["besttest_n"], "besttest"),
        ]
    )

    print("\n===== DAY BY DAY (by 选股日) =====")
    print(daily.to_string(index=False))
    print("\n===== SUMMARY =====")
    print(summary.to_string(index=False))

    ba = subs["anytag"].groupby("买入日").size()
    bb = subs["besttest"].groupby("买入日").size()
    buy_dates = sorted(set(ba.index) | set(bb.index))
    buy_daily = pd.DataFrame(
        {
            "买入日": buy_dates,
            "anytag_n": [int(ba.get(d, 0)) for d in buy_dates],
            "besttest_n": [int(bb.get(d, 0)) for d in buy_dates],
        }
    )
    print("\n===== by 买入日 =====")
    print(buy_daily.to_string(index=False))
    print(
        "buy-day sums:",
        int(buy_daily["anytag_n"].sum()),
        int(buy_daily["besttest_n"].sum()),
        "unique buy days",
        len(buy_daily),
    )

    with pd.ExcelWriter(OUT, engine="openpyxl") as w:
        daily.to_excel(w, sheet_name="by_选股日", index=False)
        buy_daily.to_excel(w, sheet_name="by_买入日", index=False)
        summary.to_excel(w, sheet_name="summary", index=False)
        for k, s in subs.items():
            cols = [
                c
                for c in [
                    "选股日",
                    "买入日",
                    "代码",
                    "股票名称",
                    "收益率pct",
                    "MA5",
                    "MA10",
                    "MA20",
                    "开盘相对买入日MA5_pct",
                    "均线差占比",
                ]
                if c in s.columns
            ]
            s[cols].sort_values(["选股日", "代码"]).to_excel(
                w, sheet_name=f"detail_{k}", index=False
            )
    print("\nWrote", OUT)


if __name__ == "__main__":
    main()
