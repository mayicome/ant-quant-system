# -*- coding: utf-8 -*-
"""七月 Elig1–40：板块 vs 概念（besttest 最热标签 + 组内RS；次日开→收）。"""
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
OUT = ROOT / "history_data" / "八月回测-热门" / "Elig1to40_板块vs概念_七月.xlsx"

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

RS_LO, RS_HI = 1, 50
RS_NUM, RS_DEN = 1, 2


def _load_daily(c6: str):
    cands = list(CACHE.glob("%s.*" % c6))
    if not cands:
        return None
    try:
        d = pd.read_csv(cands[0])
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


def _calendar():
    cal = _load_daily("000001")
    return sorted(cal["_d"].unique())


def _next_td(d, dates):
    for x in dates:
        if x > d:
            return x
    return None


def _ma(closes, n):
    if len(closes) < n:
        return None
    return float(np.mean(closes[-n:]))


def _rs_ok(rs_rank, tag_rs_n) -> bool:
    try:
        rs = int(rs_rank)
        n = int(tag_rs_n)
    except (TypeError, ValueError):
        return False
    if rs <= 0 or n <= 0:
        return False
    frac = max(1, (n * RS_NUM + RS_DEN - 1) // RS_DEN)
    return RS_LO <= rs <= min(RS_HI, frac)


def _stock_filters_ok(dd, asof, code) -> bool:
    sub = dd[dd["_d"] <= asof]
    if len(sub) < 20:
        return False
    closes = sub["close"].astype(float).tolist()
    ma5, ma10, ma20 = _ma(closes, 5), _ma(closes, 10), _ma(closes, 20)
    if None in (ma5, ma10, ma20):
        return False
    lo = min(ma5, ma10)
    if lo <= 0:
        return False
    gap = abs(ma5 - ma10) / lo
    if gap < 0.005 or gap > 0.02:
        return False
    if not (ma5 < ma10 < ma20):
        return False
    c = str(code).zfill(6)
    if c.startswith(("300", "301", "688", "689")):
        lim = 0.20
    elif c.startswith(("8", "4")) or c.startswith("920"):
        lim = 0.30
    else:
        lim = 0.10
    closes_r = sub.tail(11)["close"].astype(float).tolist()
    for i in range(1, len(closes_r)):
        prev, cur = closes_r[i - 1], closes_r[i]
        if prev > 0 and (cur / prev - 1.0) >= lim * 0.99 and i >= len(closes_r) - 10:
            return False
    return True


def _oc(dd, buy_d):
    row = dd[dd["_d"] == buy_d]
    if row.empty:
        return None
    o, c = float(row.iloc[0]["open"]), float(row.iloc[0]["close"])
    if o <= 0:
        return None
    return (c / o - 1.0) * 100.0


def _cond12(dd, asof, buy_d) -> bool:
    sub = dd[dd["_d"] <= asof]
    if len(sub) < 10:
        return False
    closes = sub["close"].astype(float).tolist()
    ma5, ma10 = _ma(closes, 5), _ma(closes, 10)
    if ma5 is None or ma10 is None:
        return False
    buy = dd[dd["_d"] == buy_d]
    if buy.empty:
        return False
    o = float(buy.iloc[0]["open"])
    if not (min(ma5, ma10) <= o <= max(ma5, ma10)):
        return False
    early = _ma(closes, 4)
    if early is None or early <= 0:
        return False
    rel = o / early - 1.0
    return 0.0 <= rel <= 0.02


def _norm_kind(k) -> str:
    s = str(k or "").strip().lower()
    if s in ("sector", "板块", "industry", "行业"):
        return "板块"
    if s in ("concept", "概念"):
        return "概念"
    return ""


def collect(dates) -> pd.DataFrame:
    rows = []
    cache = {}
    for asof in DAYS:
        ctx = load_em_board_hot_map(
            asof,
            top_n=50,
            rs_top_k=50,
            min_members=10,
            arms=["today"],
            elig_bands=None,
        )
        if ctx.get("error"):
            print("skip", asof, ctx.get("error"))
            continue
        buy_d = _next_td(asof, dates)
        if buy_d is None:
            continue
        hits = ctx.get("today_code_hits") or {}
        n_keep = 0
        for c6, hit in hits.items():
            if not isinstance(hit, dict):
                continue
            try:
                elig = int(hit.get("合格榜内序位") or 0)
            except (TypeError, ValueError):
                continue
            if elig < 1 or elig > 40:
                continue
            if not _rs_ok(hit.get("合格榜标签内RS排名"), hit.get("合格榜标签RS样本数")):
                continue
            kind = _norm_kind(hit.get("合格榜标签类型") or hit.get("选出标签类型"))
            if not kind:
                continue
            if c6 not in cache:
                cache[c6] = _load_daily(c6)
            dd = cache[c6]
            if dd is None:
                continue
            oc = _oc(dd, buy_d)
            if oc is None:
                continue
            filt = _stock_filters_ok(dd, asof, c6)
            c12 = bool(filt and _cond12(dd, asof, buy_d))
            rows.append(
                {
                    "选股日": asof.isoformat(),
                    "代码": c6,
                    "elig": elig,
                    "类型": kind,
                    "标签": hit.get("合格榜对应标签") or "",
                    "开收收益pct": oc,
                    "过滤后": filt,
                    "Cond12": c12,
                }
            )
            n_keep += 1
        print(asof, "kept", n_keep)
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame, mask, label: str) -> dict:
    s = df[mask]
    y = s["开收收益pct"]
    return {
        "口径": label,
        "n": int(len(s)),
        "mean": round(float(y.mean()), 4) if len(s) else None,
        "median": round(float(y.median()), 4) if len(s) else None,
        "win": round(float((y > 0).mean()), 4) if len(s) else None,
    }


def main():
    dates = _calendar()
    df = collect(dates)
    if df.empty:
        raise SystemExit("empty")

    rows = []
    for kind in ("板块", "概念"):
        k = df["类型"] == kind
        rows.append({**summarize(df, k, "入列"), "类型": kind, "Elig": "1-40"})
        rows.append({**summarize(df, k & df["过滤后"], "过滤后"), "类型": kind, "Elig": "1-40"})
        rows.append({**summarize(df, k & df["Cond12"], "Cond12"), "类型": kind, "Elig": "1-40"})

    # elig bands within 1-40
    band_rows = []
    df = df.copy()
    df["band"] = pd.cut(
        df["elig"],
        bins=[0, 10, 20, 30, 40],
        labels=["1-10", "11-20", "21-30", "31-40"],
    )
    for kind in ("板块", "概念"):
        for band in ["1-10", "11-20", "21-30", "31-40"]:
            for label, extra in [
                ("入列", True),
                ("过滤后", df["过滤后"]),
                ("Cond12", df["Cond12"]),
            ]:
                m = (df["类型"] == kind) & (df["band"] == band)
                if label != "入列":
                    m = m & extra
                band_rows.append({**summarize(df, m, label), "类型": kind, "Elig带": band})

    # daily mean by kind (filtered)
    day = (
        df[df["过滤后"]]
        .groupby(["选股日", "类型"])["开收收益pct"]
        .agg(["count", "mean"])
        .reset_index()
    )

    # tag-level: among filtered, mean by tag type
    tag = (
        df[df["过滤后"]]
        .groupby(["类型", "标签"])
        .agg(n=("开收收益pct", "count"), mean=("开收收益pct", "mean"))
        .reset_index()
        .sort_values(["类型", "mean"], ascending=[True, False])
    )

    out_sum = pd.DataFrame(rows)[["类型", "Elig", "口径", "n", "mean", "median", "win"]]
    out_band = pd.DataFrame(band_rows)[["类型", "Elig带", "口径", "n", "mean", "median", "win"]]

    # delta table
    piv = out_sum.pivot(index="口径", columns="类型", values=["n", "mean", "win"])
    piv.columns = [f"{a}_{b}" for a, b in piv.columns]
    piv = piv.reset_index()
    if "mean_板块" in piv.columns and "mean_概念" in piv.columns:
        piv["Δmean_板块-概念"] = piv["mean_板块"] - piv["mean_概念"]

    with pd.ExcelWriter(OUT, engine="openpyxl") as w:
        out_sum.to_excel(w, sheet_name="总对比", index=False)
        piv.to_excel(w, sheet_name="板块减概念", index=False)
        out_band.to_excel(w, sheet_name="Elig带x类型", index=False)
        day.to_excel(w, sheet_name="过滤后_按日", index=False)
        tag.to_excel(w, sheet_name="过滤后_按标签", index=False)
        df.to_excel(w, sheet_name="明细", index=False)

    print("wrote", OUT)
    print(out_sum.to_string(index=False))
    print("\n=== Elig带 x 类型（过滤后）===")
    print(
        out_band[(out_band["口径"] == "过滤后")]
        .sort_values(["Elig带", "类型"])
        .to_string(index=False)
    )
    print("\n=== Elig带 x 类型（Cond12）===")
    print(
        out_band[(out_band["口径"] == "Cond12")]
        .sort_values(["Elig带", "类型"])
        .to_string(index=False)
    )


if __name__ == "__main__":
    main()
