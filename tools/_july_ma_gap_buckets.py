# -*- coding: utf-8 -*-
"""七月：|MA5-MA10|/min 三档数量（<0.5% / [0.5%,2%] / >2%）。

主池：Elig1-30 + RS截断 + 无近涨停 + MA5<MA10<MA20。
对照池：去掉 Cond3，仅 Elig+RS+无涨停。
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
OUT = ROOT / "history_data" / "八月回测-热门" / "均线差三档对照_七月.xlsx"
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
    return 1 <= rs <= min(50, max(1, (n + 1) // 2))


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


def gap_bucket(gap):
    if gap < 0.005:
        return "<0.5%"
    if gap <= 0.02:
        return "[0.5%,2%]"
    return ">2%"


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
            if ma5 is None or ma10 is None or ma20 is None:
                continue
            lo = min(ma5, ma10)
            if lo <= 0:
                continue
            gap = abs(ma5 - ma10) / lo
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
                    "gap": gap,
                    "档": gap_bucket(gap),
                    "Cond3": bool(ma5 < ma10 < ma20),
                    "开收": (c / o - 1) * 100,
                    "Cond1": min(ma5, ma10) <= o <= max(ma5, ma10),
                }
            )
        print(asof, "n", sum(1 for r in rows if r["选股日"] == asof.isoformat()))

    df = pd.DataFrame(rows)
    order = ["<0.5%", "[0.5%,2%]", ">2%"]

    def table(sub, title):
        g = (
            sub.groupby("档", dropna=False)
            .agg(
                n=("代码", "count"),
                开收均值=("开收", "mean"),
                胜率=("开收", lambda s: (s > 0).mean()),
                Cond1_n=("Cond1", "sum"),
                Cond1均值=("开收", lambda s: s[sub.loc[s.index, "Cond1"]].mean() if False else np.nan),
            )
            .reindex(order)
        )
        # fix Cond1 mean properly
        rows2 = []
        for b in order:
            s = sub[sub["档"] == b]
            c1 = s[s["Cond1"]]
            rows2.append(
                {
                    "池": title,
                    "均线差档": b,
                    "n": int(len(s)),
                    "占比": round(len(s) / len(sub), 4) if len(sub) else None,
                    "开收均值": round(float(s["开收"].mean()), 4) if len(s) else None,
                    "胜率": round(float((s["开收"] > 0).mean()), 4) if len(s) else None,
                    "Cond1_n": int(len(c1)),
                    "Cond1开收均值": round(float(c1["开收"].mean()), 4) if len(c1) else None,
                }
            )
        return pd.DataFrame(rows2)

    t1 = table(df[df["Cond3"]], "Elig+RS+无涨停+Cond3")
    t2 = table(df, "Elig+RS+无涨停（含非Cond3）")
    out = pd.concat([t1, t2], ignore_index=True)

    # how many cut by current rule among Cond3 pool
    cond3 = df[df["Cond3"]]
    keep = cond3[cond3["档"] == "[0.5%,2%]"]
    cut_lo = cond3[cond3["档"] == "<0.5%"]
    cut_hi = cond3[cond3["档"] == ">2%"]

    with pd.ExcelWriter(OUT, engine="openpyxl") as w:
        out.to_excel(w, sheet_name="三档", index=False)
        pd.DataFrame(
            [
                {
                    "说明": "主看 Cond3 池；现行规则保留[0.5%,2%]",
                    "Cond3池n": len(cond3),
                    "保留n": len(keep),
                    "砍掉_小于0.5%": len(cut_lo),
                    "砍掉_大于2%": len(cut_hi),
                    "砍掉合计": len(cut_lo) + len(cut_hi),
                    "砍掉占比": round((len(cut_lo) + len(cut_hi)) / len(cond3), 4)
                    if len(cond3)
                    else None,
                }
            ]
        ).to_excel(w, sheet_name="现行规则卡掉", index=False)

    print("wrote", OUT)
    print(t1.to_string(index=False))
    print(
        "\nCond3池: 总",
        len(cond3),
        "保留[0.5,2]",
        len(keep),
        "砍<0.5%",
        len(cut_lo),
        "砍>2%",
        len(cut_hi),
        "砍掉占比",
        round((len(cut_lo) + len(cut_hi)) / len(cond3), 4) if len(cond3) else None,
    )
    print("\n对照（含非Cond3）")
    print(t2.to_string(index=False))


if __name__ == "__main__":
    main()
