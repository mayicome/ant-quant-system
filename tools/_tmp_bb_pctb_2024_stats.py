# -*- coding: utf-8 -*-
"""布林%b 2024 表现快检（对比 最终）"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "history_data" / "布林%b回落选股"


def met(r: pd.Series) -> dict:
    r = pd.to_numeric(r, errors="coerce").dropna()
    n = len(r)
    if not n:
        return {"n": 0}
    return {
        "n": n,
        "mean": round(float(r.mean()), 4),
        "median": round(float(r.median()), 4),
        "win": round(float((r > 0).mean() * 100), 2),
        "sum": round(float(r.sum()), 2),
        "p05": round(float(r.quantile(0.05)), 4),
        "p95": round(float(r.quantile(0.95)), 4),
    }


def load_ticket(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path)
    buy = pd.to_datetime(df.get("买入日"), errors="coerce")
    if buy.isna().all():
        buy = pd.to_datetime(df.get("选股日"), errors="coerce")
    df = df.copy()
    df["_buy"] = buy
    df["_ret"] = pd.to_numeric(df["收益率pct"], errors="coerce")
    df["_y"] = buy.dt.year
    return df


def load_daily(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path)
    df["选股日"] = pd.to_datetime(df["选股日"], errors="coerce")
    df["总收益率%"] = pd.to_numeric(df["总收益率%"], errors="coerce")
    df["股票数"] = pd.to_numeric(df.get("股票数"), errors="coerce")
    return df


def show(label: str, df: pd.DataFrame) -> None:
    print(f"\n=== {label} ===")
    print(
        "buy",
        df["_buy"].min(),
        "->",
        df["_buy"].max(),
        "rows",
        len(df),
    )
    print("全样本", met(df["_ret"]))
    for y, g in df.groupby("_y"):
        if pd.isna(y):
            continue
        print(f"  {int(y)}", met(g["_ret"]))
    # 月
    g = df.dropna(subset=["_buy"]).copy()
    g["_ym"] = g["_buy"].dt.to_period("M").astype(str)
    print("  分月票均:")
    for ym, sub in g.groupby("_ym"):
        m = met(sub["_ret"])
        if m.get("n", 0) >= 5:
            print(f"    {ym} n={m['n']} 票均={m['mean']:+.3f}% 胜率={m['win']:.1f}%")


def main() -> None:
    ticket = BASE / "各日选股收益汇总_日线-bb_pctb-bb_pctb_sell-单点_按票_已完成_latest.xlsx"
    if not ticket.exists():
        ticket = BASE / "各日选股收益汇总_日线-bb_pctb-bb_pctb_sell-单点_按票_latest.xlsx"
    daily = BASE / "各日选股收益汇总_日线-bb_pctb-bb_pctb_sell-单点_latest.xlsx"
    final_t = (
        BASE
        / "最终"
        / "各日选股收益汇总_日线-bb_pctb-bb_pctb_sell-单点_按票_已完成_latest.xlsx"
    )
    if not final_t.exists():
        final_t = (
            BASE / "最终" / "各日选股收益汇总_日线-bb_pctb-bb_pctb_sell-单点_按票_latest.xlsx"
        )

    print("ticket", ticket.name, ticket.exists())
    df = load_ticket(ticket)
    show("当前 latest（应为2024回测）", df)

    if daily.exists():
        d = load_daily(daily)
        m = (d["选股日"] >= "2024-01-01") & (d["选股日"] <= "2024-12-31")
        dd = d.loc[m]
        print("\n=== 日度篮子 2024 ===")
        print(met(dd["总收益率%"]))
        print("日均选股只数", round(float(dd["股票数"].mean()), 1) if len(dd) else None)

    if final_t.exists():
        show("最终目录（拟合段）", load_ticket(final_t))


if __name__ == "__main__":
    main()
