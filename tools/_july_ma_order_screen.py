# -*- coding: utf-8 -*-
"""七月：近10日无涨停前提下，MA5/MA10/MA20 六种排列的数量与次日开→收。

池：Elig1-30 + RS≤min(50,ceil(n/2)) + 无近涨停；不做均线差、不做 Cond2。
另报 Cond1（开盘落在 MA5/MA10 夹档）子集。
"""
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
OUT = ROOT / "history_data" / "八月回测-热门" / "MA排列对照_无涨停_七月.xlsx"
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

ORDER_LABELS = {
    ("MA5", "MA10", "MA20"): "MA5<MA10<MA20（现Cond3/空头）",
    ("MA5", "MA20", "MA10"): "MA5<MA20<MA10",
    ("MA10", "MA5", "MA20"): "MA10<MA5<MA20",
    ("MA10", "MA20", "MA5"): "MA10<MA20<MA5",
    ("MA20", "MA5", "MA10"): "MA20<MA5<MA10",
    ("MA20", "MA10", "MA5"): "MA20<MA10<MA5（多头）",
}


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


def order_key(ma5, ma10, ma20):
    items = [("MA5", ma5), ("MA10", ma10), ("MA20", ma20)]
    items.sort(key=lambda x: x[1])
    # reject ties (equal mas)
    vals = [x[1] for x in items]
    if vals[0] == vals[1] or vals[1] == vals[2]:
        return None
    return tuple(x[0] for x in items)


def cond1(dd, asof, buy_d, ma5, ma10):
    buy = dd[dd["_d"] == buy_d]
    if buy.empty:
        return False
    o = float(buy.iloc[0]["open"])
    return min(ma5, ma10) <= o <= max(ma5, ma10)


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
            if lu_count(dd, asof, c6) > 0:
                continue
            sub = dd[dd["_d"] <= asof]
            if len(sub) < 20:
                continue
            closes = sub["close"].astype(float).tolist()
            ma5, ma10, ma20 = ma(closes, 5), ma(closes, 10), ma(closes, 20)
            if None in (ma5, ma10, ma20):
                continue
            key = order_key(ma5, ma10, ma20)
            if key is None:
                label = "均线有相等"
            else:
                label = ORDER_LABELS.get(key, str(key))
            br = dd[dd["_d"] == buy]
            if br.empty:
                continue
            o, c = float(br.iloc[0]["open"]), float(br.iloc[0]["close"])
            if o <= 0:
                continue
            rows.append(
                {
                    "选股日": asof.isoformat(),
                    "代码": c6,
                    "排列": label,
                    "MA5": round(ma5, 4),
                    "MA10": round(ma10, 4),
                    "MA20": round(ma20, 4),
                    "开收": (c / o - 1) * 100,
                    "Cond1夹档": cond1(dd, asof, buy, ma5, ma10),
                    "MA5_lt_MA10": ma5 < ma10,
                    "MA10_lt_MA20": ma10 < ma20,
                    "MA5_lt_MA20": ma5 < ma20,
                }
            )
        print(asof, "n", sum(1 for r in rows if r["选股日"] == asof.isoformat()))

    df = pd.DataFrame(rows)

    def agg(sub):
        y = sub["开收"]
        c1 = sub[sub["Cond1夹档"]]
        return pd.Series(
            {
                "选股n": len(sub),
                "选股占比": len(sub) / len(df) if len(df) else 0,
                "选股开收均值": y.mean(),
                "选股胜率": (y > 0).mean(),
                "Cond1_n": len(c1),
                "Cond1开收均值": c1["开收"].mean() if len(c1) else np.nan,
                "Cond1胜率": (c1["开收"] > 0).mean() if len(c1) else np.nan,
            }
        )

    # preferred order for display
    pref = list(ORDER_LABELS.values()) + ["均线有相等"]
    g = df.groupby("排列", dropna=False).apply(agg, include_groups=False).reset_index()
    g["_ord"] = g["排列"].apply(lambda x: pref.index(x) if x in pref else 99)
    g = g.sort_values("_ord").drop(columns="_ord")
    for c in ("选股占比", "选股开收均值", "选股胜率", "Cond1开收均值", "Cond1胜率"):
        g[c] = g[c].map(lambda v: round(float(v), 4) if pd.notna(v) else None)
    g["选股n"] = g["选股n"].astype(int)
    g["Cond1_n"] = g["Cond1_n"].astype(int)

    # pairwise buckets
    pair_rows = []
    for name, mask in [
        ("MA5<MA10", df["MA5_lt_MA10"]),
        ("MA5≥MA10", ~df["MA5_lt_MA10"]),
        ("MA10<MA20", df["MA10_lt_MA20"]),
        ("MA10≥MA20", ~df["MA10_lt_MA20"]),
        ("MA5<MA20", df["MA5_lt_MA20"]),
        ("MA5≥MA20", ~df["MA5_lt_MA20"]),
        ("MA5<MA10<MA20", (df["排列"] == ORDER_LABELS[("MA5", "MA10", "MA20")])),
        ("非MA5<MA10<MA20", (df["排列"] != ORDER_LABELS[("MA5", "MA10", "MA20")])),
    ]:
        sub = df[mask]
        y = sub["开收"]
        c1 = sub[sub["Cond1夹档"]]
        pair_rows.append(
            {
                "关系": name,
                "选股n": int(len(sub)),
                "选股开收均值": round(float(y.mean()), 4) if len(sub) else None,
                "选股胜率": round(float((y > 0).mean()), 4) if len(sub) else None,
                "Cond1_n": int(len(c1)),
                "Cond1开收均值": round(float(c1["开收"].mean()), 4) if len(c1) else None,
                "Cond1胜率": round(float((c1["开收"] > 0).mean()), 4) if len(c1) else None,
            }
        )
    pairs = pd.DataFrame(pair_rows)

    with pd.ExcelWriter(OUT, engine="openpyxl") as w:
        g.to_excel(w, sheet_name="六种排列", index=False)
        pairs.to_excel(w, sheet_name="两两关系", index=False)
        df.to_excel(w, sheet_name="明细", index=False)

    print("wrote", OUT)
    print("total", len(df))
    print(g.to_string(index=False))
    print("\n=== 两两/是否Cond3 ===")
    print(pairs.to_string(index=False))


if __name__ == "__main__":
    main()
