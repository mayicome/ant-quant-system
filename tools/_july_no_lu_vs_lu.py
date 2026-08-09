# -*- coding: utf-8 -*-
"""七月：近10日无涨停 vs 有涨停（在均线差+MA空头已满足的前提下）。"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.eastmoney_board_rank_ctx import load_em_board_hot_map

CACHE = ROOT / "data" / "daily_cache"
OUT = ROOT / "history_data" / "八月回测-热门" / "近10日涨停过滤对照_七月.xlsx"
DAYS = [
    date(2026, 7, d)
    for d in (
        1,
        2,
        3,
        6,
        7,
        8,
        9,
        10,
        13,
        14,
        15,
        16,
        17,
        20,
        21,
        22,
        23,
        24,
        27,
        28,
        29,
        30,
        31,
    )
]


def load_daily(c6):
    cands = list(CACHE.glob(c6 + ".*"))
    if not cands:
        return None
    d = pd.read_csv(cands[0])
    for col in ("date", "trade_date", "日期"):
        if col in d.columns:
            d = d.copy()
            d["_d"] = pd.to_datetime(d[col]).dt.date
            break
    else:
        return None
    return d.sort_values("_d")


def next_td(d, dates):
    for x in dates:
        if x > d:
            return x
    return None


def ma(closes, n):
    if len(closes) < n:
        return None
    return float(np.mean(closes[-n:]))


def rs_ok(rs, n):
    try:
        rs, n = int(rs), int(n)
    except (TypeError, ValueError):
        return False
    if rs <= 0 or n <= 0:
        return False
    cut = min(50, max(1, (n + 1) // 2))
    return 1 <= rs <= cut


def lu_count(dd, asof, code):
    sub = dd[dd["_d"] <= asof]
    if len(sub) < 2:
        return 0
    c = str(code).zfill(6)
    if c.startswith(("300", "301", "688", "689")):
        lim = 0.20
    elif c.startswith(("8", "4")) or c.startswith("920"):
        lim = 0.30
    else:
        lim = 0.10
    closes = sub.tail(11)["close"].astype(float).tolist()
    cnt = 0
    for i in range(1, len(closes)):
        if i < len(closes) - 10:
            continue
        prev, cur = closes[i - 1], closes[i]
        if prev > 0 and (cur / prev - 1) >= lim * 0.99:
            cnt += 1
    return cnt


def ma_gap_ok(closes):
    ma5, ma10 = ma(closes, 5), ma(closes, 10)
    if ma5 is None or ma10 is None:
        return False
    lo = min(ma5, ma10)
    if lo <= 0:
        return False
    g = abs(ma5 - ma10) / lo
    return 0.005 <= g <= 0.02


def ma_lt_ok(closes):
    ma5, ma10, ma20 = ma(closes, 5), ma(closes, 10), ma(closes, 20)
    if None in (ma5, ma10, ma20):
        return False
    return ma5 < ma10 < ma20


def cond12(dd, asof, buy_d):
    sub = dd[dd["_d"] <= asof]
    closes = sub["close"].astype(float).tolist()
    ma5, ma10 = ma(closes, 5), ma(closes, 10)
    if ma5 is None or ma10 is None:
        return False
    buy = dd[dd["_d"] == buy_d]
    if buy.empty:
        return False
    o = float(buy.iloc[0]["open"])
    if not (min(ma5, ma10) <= o <= max(ma5, ma10)):
        return False
    early = ma(closes, 4)
    if early is None or early <= 0:
        return False
    return 0 <= (o / early - 1) <= 0.02


def summ(sub, name):
    y = sub["开收"]
    c12 = sub[sub["Cond12"]]
    return {
        "组": name,
        "选股n": int(len(sub)),
        "选股开收均值": round(float(y.mean()), 4) if len(sub) else None,
        "选股胜率": round(float((y > 0).mean()), 4) if len(sub) else None,
        "Cond12_n": int(len(c12)),
        "Cond12开收均值": round(float(c12["开收"].mean()), 4) if len(c12) else None,
        "Cond12胜率": round(float((c12["开收"] > 0).mean()), 4) if len(c12) else None,
    }


def main():
    cal = sorted(load_daily("000001")["_d"].unique())
    cache = {}
    rows = []
    for asof in DAYS:
        ctx = load_em_board_hot_map(
            asof,
            top_n=50,
            rs_top_k=50,
            min_members=10,
            arms=["today"],
            elig_bands=[(1, 30)],
        )
        if ctx.get("error"):
            continue
        buy = next_td(asof, cal)
        if not buy:
            continue
        for c6, hit in (ctx.get("today_code_hits") or {}).items():
            if not isinstance(hit, dict):
                continue
            try:
                elig = int(hit.get("合格榜内序位") or 0)
            except (TypeError, ValueError):
                continue
            if elig < 1 or elig > 30:
                continue
            if not rs_ok(hit.get("合格榜标签内RS排名"), hit.get("合格榜标签RS样本数")):
                continue
            if c6 not in cache:
                cache[c6] = load_daily(c6)
            dd = cache[c6]
            if dd is None:
                continue
            sub = dd[dd["_d"] <= asof]
            if len(sub) < 20:
                continue
            closes = sub["close"].astype(float).tolist()
            if not ma_gap_ok(closes) or not ma_lt_ok(closes):
                continue
            br = dd[dd["_d"] == buy]
            if br.empty:
                continue
            o, c = float(br.iloc[0]["open"]), float(br.iloc[0]["close"])
            if o <= 0:
                continue
            luc = lu_count(dd, asof, c6)
            rows.append(
                {
                    "选股日": asof.isoformat(),
                    "代码": c6,
                    "lu": luc,
                    "有涨停": luc > 0,
                    "开收": (c / o - 1) * 100,
                    "Cond12": cond12(dd, asof, buy),
                }
            )
        print(asof, "n_day", sum(1 for r in rows if r["选股日"] == asof.isoformat()))

    df = pd.DataFrame(rows)
    summary = pd.DataFrame(
        [
            summ(df[~df["有涨停"]], "近10日无涨停"),
            summ(df[df["有涨停"]], "近10日有涨停"),
        ]
    )
    by_lu = (
        df.groupby("lu")["开收"]
        .agg(选股n="count", 选股开收均值="mean")
        .reset_index()
    )
    by_lu["选股胜率"] = df.groupby("lu")["开收"].apply(lambda x: (x > 0).mean()).values
    c12 = df[df["Cond12"]]
    by_lu_c12 = (
        c12.groupby("lu")["开收"]
        .agg(Cond12_n="count", Cond12开收均值="mean")
        .reset_index()
    )

    with pd.ExcelWriter(OUT, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="对照", index=False)
        by_lu.to_excel(w, sheet_name="按涨停次数_选股", index=False)
        by_lu_c12.to_excel(w, sheet_name="按涨停次数_Cond12", index=False)

    print("wrote", OUT)
    print(summary.to_string(index=False))
    print("\nby lu count (选股)")
    print(by_lu.to_string(index=False))
    print("\nby lu count (Cond12)")
    print(by_lu_c12.to_string(index=False))
    d_mean = summary.loc[0, "Cond12开收均值"] - summary.loc[1, "Cond12开收均值"]
    print("\nΔCond12均值 无-有 =", round(float(d_mean), 4) if d_mean == d_mean else None)


if __name__ == "__main__":
    main()
