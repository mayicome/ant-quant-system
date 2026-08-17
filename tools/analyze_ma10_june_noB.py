# -*- coding: utf-8 -*-
"""June MA10: factor hunt WITHOUT filter B."""
from __future__ import annotations

import json
import sys
import warnings
from itertools import combinations
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DIR = ROOT / "history_data" / "马总选股逻辑"
SRC = DIR / "各日选股收益汇总_日线-ma10-单点_按票_20260815_203110_收盘上MA10.xlsx"
OUT = DIR / "_ma10_june_noB_factors.json"


def stats(g: pd.DataFrame) -> dict:
    r = g["ret"]
    day = g.groupby("sel")["ret"].mean()
    return {
        "n": int(len(g)),
        "mean": round(float(r.mean()), 4),
        "med": round(float(r.median()), 4),
        "win": round(float((r > 0).mean() * 100), 2),
        "day_mean": round(float(day.mean()), 4) if len(day) else None,
        "n_days": int(day.shape[0]),
        "pos_days": int((day > 0).sum()) if len(day) else 0,
    }


def qcut_table(df, col, q, scale=1.0):
    d = df[df[col].notna()].copy()
    d["qb"] = pd.qcut(d[col], q, duplicates="drop")
    rows = []
    for i, (_lab, g) in enumerate(d.groupby("qb", observed=True)):
        st = stats(g)
        rows.append(
            {
                "q": i + 1,
                "lo": round(float(g[col].min()) * scale, 4),
                "hi": round(float(g[col].max()) * scale, 4),
                **st,
            }
        )
    return rows


def main():
    df = pd.read_excel(SRC)
    df["sel"] = pd.to_datetime(df["选股日"]).dt.date
    df["ret"] = pd.to_numeric(df["收益率pct"], errors="coerce")
    df = df[df["ret"].notna()].copy()
    for c in [
        "MA5", "MA10", "MA20", "近5日RS", "近10日RS", "近20日RS", "买入成交价",
        "前十个交易日最高涨幅", "主力净流入_万元", "流通市值_亿", "sel_close",
        "今日热门板块最高排名A", "今日热门概念最高排名B", "lu10", "均线差占比",
    ]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df["ma_gap"] = (df["MA5"] - df["MA10"]) / df["MA10"]
    df["dist_ma10"] = (df["sel_close"] - df["MA10"]) / df["MA10"] * 100
    df["dist_ma20"] = (df["sel_close"] - df["MA20"]) / df["MA20"] * 100
    code = df["代码"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    df["is20"] = code.str.startswith(("300", "301", "688", "689"))
    df["mx_n"] = np.where(df["is20"], df["前十个交易日最高涨幅"] / 2.0, df["前十个交易日最高涨幅"])
    df["px"] = df["买入成交价"]
    df["mv"] = df["流通市值_亿"]
    df["inflow"] = df["主力净流入_万元"]
    df["rs5"] = df["近5日RS"]
    df["rs10"] = df["近10日RS"]
    df["rs20"] = df["近20日RS"]

    if "条件_前10日无大涨" in df.columns:
        df["no_big"] = df["条件_前10日无大涨"].map(
            lambda x: str(x).strip().lower() in ("1", "true", "yes", "是", "y", "t")
        )
    if "条件_行业或概念排名达标" in df.columns:
        df["rank_ok"] = df["条件_行业或概念排名达标"].map(
            lambda x: str(x).strip().lower() in ("1", "true", "yes", "是", "y", "t")
        )

    base = stats(df)
    out = {"base": base, "n": len(df)}

    # --- continuous factor q5/q10 ---
    cont = {
        "px": (df["px"], 1),
        "mv": (df["mv"], 1),
        "inflow": (df["inflow"], 1),
        "ma_gap": (df["ma_gap"], 100),
        "dist_ma10": (df["dist_ma10"], 1),
        "dist_ma20": (df["dist_ma20"], 1),
        "mx_n": (df["mx_n"], 1),
        "rs5": (df["rs5"], 100),
        "rs10": (df["rs10"], 100),
        "rs20": (df["rs20"], 100),
        "hot_ind": (df["今日热门板块最高排名A"], 1),
        "hot_con": (df["今日热门概念最高排名B"], 1),
    }
    for name, (s, scale) in cont.items():
        tmp = df.copy()
        tmp["_v"] = s
        out[f"{name}_q5"] = qcut_table(tmp, "_v", 5, scale=scale)
        out[f"{name}_q10"] = qcut_table(tmp, "_v", 10, scale=scale)
        out[f"{name}_corr"] = round(float(tmp["_v"].corr(tmp["ret"])), 4)

    # --- binary / band factors ---
    bands = {}
    bands["MA5>MA10"] = df["MA5"] > df["MA10"]
    bands["MA5≤MA10"] = df["MA5"] <= df["MA10"]
    bands["MA10<MA20"] = df["MA10"] < df["MA20"]
    bands["MA10≥MA20"] = df["MA10"] >= df["MA20"]
    a = df["MA5"] > df["MA10"]
    b = df["MA10"] < df["MA20"]
    bands["5>10&10<20"] = a & b
    bands["5>10&10≥20"] = a & ~b
    bands["5≤10&10<20"] = ~a & b
    bands["5≤10&10≥20"] = ~a & ~b
    bands["close>MA20"] = df["sel_close"] > df["MA20"]
    bands["close≤MA20"] = df["sel_close"] <= df["MA20"]
    if "rank_ok" in df.columns:
        bands["rank_ok"] = df["rank_ok"]
        bands["rank_not"] = ~df["rank_ok"]
    if "no_big" in df.columns:
        bands["no_big"] = df["no_big"]
        bands["has_big"] = ~df["no_big"]
    bands["lu=0"] = df["lu10"] <= 0
    bands["lu=1"] = df["lu10"] == 1
    bands["lu≥2"] = df["lu10"] >= 2

    # price / mv / gap / rs / dist custom bands from qcuts that looked ok
    bands["价Q3-4(15.5~55)"] = df["px"].between(15.5, 55.5)
    bands["价26~55"] = df["px"].between(26.8, 55.5)
    bands["价>26"] = df["px"] > 26.8
    bands["价>15"] = df["px"] > 15.5
    bands["价≤15"] = df["px"] <= 15.5
    bands["市值100~353"] = df["mv"].between(100, 353)
    bands["市值170~353"] = df["mv"].between(170, 353)
    bands["市值≥100"] = df["mv"] >= 100
    bands["市值≥170"] = df["mv"] >= 170
    bands["市值≤100"] = df["mv"] <= 100
    bands["gap 0.5~6%"] = df["ma_gap"].between(0.005, 0.06)
    bands["gap 0.5~3%"] = df["ma_gap"].between(0.005, 0.03)
    bands["gap>0"] = df["ma_gap"] > 0
    bands["dist10 9~19%"] = df["dist_ma10"].between(9, 19.3)
    bands["dist10 8~20%"] = df["dist_ma10"].between(8, 20)
    bands["流入1~3亿"] = df["inflow"].between(10000, 30000)
    bands["流入0.5~3亿"] = df["inflow"].between(5000, 30000)
    bands["RS20 5~19%"] = df["rs20"].between(0.055, 0.19)
    bands["RS20 0~20%"] = df["rs20"].between(0, 0.20)
    bands["RS5 15~25%"] = df["rs5"].between(0.15, 0.25)
    bands["RS5 1~5%"] = df["rs5"].between(0.01, 0.05)
    bands["hot_ind≤20"] = df["今日热门板块最高排名A"] <= 20
    bands["hot_con≤20"] = df["今日热门概念最高排名B"] <= 20
    bands["hot_ind≤10"] = df["今日热门板块最高排名A"] <= 10
    bands["mxn≤5"] = df["mx_n"] <= 5
    bands["mxn 5~12"] = df["mx_n"].between(5, 12)

    band_stats = []
    for name, m in bands.items():
        g = df[m.fillna(False)]
        if len(g) < 30:
            continue
        st = stats(g)
        st["name"] = name
        st["coverage"] = round(len(g) / len(df) * 100, 1)
        st["lift"] = round(st["mean"] - base["mean"], 4)
        band_stats.append(st)
    band_stats.sort(key=lambda x: -x["mean"])
    out["bands"] = band_stats

    # --- combo screen without B ---
    F = {
        "5>10": bands["MA5>MA10"],
        "10<20": bands["MA10<MA20"],
        "align": bands["5>10&10<20"],
        "rank_ok": bands.get("rank_ok", pd.Series(False, index=df.index)),
        "价>15": bands["价>15"],
        "价26~55": bands["价26~55"],
        "市值≥100": bands["市值≥100"],
        "市值170~353": bands["市值170~353"],
        "gap0.5~6": bands["gap 0.5~6%"],
        "gap>0": bands["gap>0"],
        "dist9~19": bands["dist10 9~19%"],
        "流入1~3亿": bands["流入1~3亿"],
        "RS20_5~19": bands["RS20 5~19%"],
        "RS20_0~20": bands["RS20 0~20%"],
        "lu=0": bands["lu=0"],
        "lu=1": bands["lu=1"],
        "no_big": bands.get("no_big", pd.Series(False, index=df.index)),
        "close>MA20": bands["close>MA20"],
        "hot_ind≤20": bands["hot_ind≤20"],
    }

    combos = []
    keys = list(F.keys())
    # singles already in bands; do 2-3 way
    for r in (2, 3):
        for combo in combinations(keys, r):
            m = pd.Series(True, index=df.index)
            for k in combo:
                m = m & F[k].fillna(False)
            g = df[m]
            if len(g) < 60:
                continue
            st = stats(g)
            if st["n_days"] < 12:
                continue
            st["name"] = "+".join(combo)
            st["coverage"] = round(len(g) / len(df) * 100, 1)
            st["lift"] = round(st["mean"] - base["mean"], 4)
            combos.append(st)

    combos.sort(key=lambda x: (-x["mean"], -x["n"]))
    out["combos"] = combos[:80]

    # print highlights
    print("base", base)
    print("\n=== best bands n>=80 mean>=0 ===")
    for r in band_stats:
        if r["n"] >= 80 and r["mean"] >= 0:
            print(f"{r['mean']:+.2f}% n={r['n']:4d} d={r['n_days']:2d} win={r['win']:5.1f} day={r['day_mean']:+.2f} lift={r['lift']:+.2f} {r['name']}")
    print("\n=== best bands n>=80 (top15 by mean) ===")
    for r in band_stats[:15]:
        if r["n"] >= 80:
            print(f"{r['mean']:+.2f}% n={r['n']:4d} d={r['n_days']:2d} win={r['win']:5.1f} day={r['day_mean']:+.2f} {r['name']}")
    print("\n=== best combos n>=80 mean>=0.3 ===")
    for r in combos:
        if r["n"] >= 80 and r["mean"] >= 0.3:
            print(f"{r['mean']:+.2f}% n={r['n']:4d} d={r['n_days']:2d} win={r['win']:5.1f} day={r['day_mean']:+.2f} {r['name']}")
    print("\n=== top20 combos any ===")
    for r in combos[:20]:
        print(f"{r['mean']:+.2f}% n={r['n']:4d} d={r['n_days']:2d} win={r['win']:5.1f} day={r['day_mean']:+.2f} {r['name']}")

    # key q5 summaries
    for name in ("px", "mv", "ma_gap", "dist_ma10", "inflow", "rs20"):
        print(f"\n{name}_q5:")
        for r in out[f"{name}_q5"]:
            print(f"  Q{r['q']} {r['lo']}~{r['hi']} n={r['n']} mean={r['mean']:+.2f} win={r['win']}")

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
