# -*- coding: utf-8 -*-
"""八月前3日 besttest 全池次日开→收收益（无 Cond123）。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SEL = (
    ROOT
    / "history_data"
    / "存档"
    / "选股结果_东财热门-besttest-无涨停均线差0.5to2-MA空头排列_2026-08-03_2026-08-05.xls"
)
CACHE = ROOT / "data" / "daily_cache"
OUT = ROOT / "history_data" / "八月回测-热门" / "全选股_次日开收收益_8月3to5.xlsx"


def code6(c) -> str:
    s = str(c).strip()
    if "." in s:
        s = s.split(".", 1)[0]
    return s.zfill(6) if s.isdigit() else s


def load_daily(c6: str) -> pd.DataFrame | None:
    cands = list(CACHE.glob("%s.*" % c6))
    if not cands:
        return None
    p = cands[0]
    try:
        d = pd.read_csv(p)
    except Exception:
        return None
    for col in ("date", "trade_date", "日期"):
        if col in d.columns:
            d = d.copy()
            d["_d"] = pd.to_datetime(d[col]).dt.date
            break
    else:
        return None
    return d.sort_values("_d")


def main() -> None:
    sel = pd.read_excel(SEL, engine="xlrd")
    cal = load_daily("000001")
    if cal is None:
        raise SystemExit("no calendar")
    dates = sorted(cal["_d"].unique())

    def next_td(d):
        d = pd.to_datetime(d).date()
        for x in dates:
            if x > d:
                return x
        return None

    rows = []
    for _, r in sel.iterrows():
        c6 = code6(r["股票代码"])
        asof = pd.to_datetime(r["选股日"]).date()
        buy_d = next_td(asof)
        dd = load_daily(c6)
        if dd is None or buy_d is None:
            continue
        sub = dd[dd["_d"] <= asof]
        if len(sub) < 5:
            continue
        closes = sub["close"].astype(float).tolist()
        ma5 = float(np.mean(closes[-5:]))
        ma10 = float(np.mean(closes[-10:])) if len(closes) >= 10 else float("nan")
        buy_row = dd[dd["_d"] == buy_d]
        if buy_row.empty:
            continue
        br = buy_row.iloc[0]
        o = float(br["open"])
        c = float(br["close"])
        oc = (c / o - 1) * 100 if o > 0 else float("nan")
        prev = float(sub.iloc[-1]["close"])
        pc = (c / prev - 1) * 100 if prev > 0 else float("nan")
        early_ma5 = float(np.mean(closes[-4:])) if len(closes) >= 4 else float("nan")
        rel = (o / early_ma5 - 1) * 100 if early_ma5 > 0 else float("nan")
        lo, hi = min(ma5, ma10), max(ma5, ma10)
        in_band = bool(lo <= o <= hi) if ma10 == ma10 else False
        rows.append(
            {
                "选股日": str(asof),
                "买入日": str(buy_d),
                "代码": c6,
                "名称": r["股票名称"],
                "合格榜内序位": r.get("合格榜内序位"),
                "选出标签": r.get("选出标签"),
                "开盘": round(o, 3),
                "收盘": round(c, 3),
                "开收收益pct": round(oc, 4),
                "昨收收收益pct": round(pc, 4),
                "开盘相对MA5_pct": round(rel, 3) if rel == rel else None,
                "开盘夹档": in_band,
                "MA5": round(ma5, 3),
                "MA10": round(ma10, 3) if ma10 == ma10 else None,
            }
        )

    df = pd.DataFrame(rows)
    sum_rows = []
    for d, g in df.groupby("选股日"):
        hi = g["开盘相对MA5_pct"] > 2
        sum_rows.append(
            {
                "选股日": d,
                "n": len(g),
                "开收均值": round(float(g["开收收益pct"].mean()), 3),
                "开收中位": round(float(g["开收收益pct"].median()), 3),
                "胜率pct": round(float((g["开收收益pct"] > 0).mean() * 100), 1),
                "夹档内n": int(g["开盘夹档"].sum()),
                "夹档内开收均值": (
                    round(float(g.loc[g["开盘夹档"], "开收收益pct"].mean()), 3)
                    if g["开盘夹档"].any()
                    else None
                ),
                "高开gt2pct_n": int(hi.sum()),
                "高开gt2pct开收均值": (
                    round(float(g.loc[hi, "开收收益pct"].mean()), 3) if hi.any() else None
                ),
            }
        )
    summary = pd.DataFrame(sum_rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUT, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="按日汇总", index=False)
        df.sort_values(["选股日", "开收收益pct"], ascending=[True, False]).to_excel(
            w, sheet_name="明细", index=False
        )
    print("wrote", OUT)
    print(summary.to_string(index=False))
    print(
        "ALL n=%d mean=%.3f win=%.1f%%"
        % (
            len(df),
            float(df["开收收益pct"].mean()),
            float((df["开收收益pct"] > 0).mean() * 100),
        )
    )


if __name__ == "__main__":
    main()
