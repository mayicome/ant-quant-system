# -*- coding: utf-8 -*-
"""七月：RS截断网格 — RS_HI∈{40,50,60} × 比例∈{1/3,1/2,2/3}。

池：besttest 最热标签 + Elig1-30 + 个股过滤；收益=次日开→收%；
另报 Cond12（夹档+相对MA5∈[0,2%]）子集。
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
OUT = ROOT / "history_data" / "八月回测-热门" / "RS截断网格_七月.xlsx"

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

ELIG_LO, ELIG_HI = 1, 30
RS_HI_LIST = [40, 50, 60]
FRACS = [(1, 3), (1, 2), (2, 3)]  # num/den


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
    return sorted(_load_daily("000001")["_d"].unique())


def _next_td(d, dates):
    for x in dates:
        if x > d:
            return x
    return None


def _ma(closes, n):
    if len(closes) < n:
        return None
    return float(np.mean(closes[-n:]))


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
    return 0.0 <= (o / early - 1.0) <= 0.02


def _rs_pass(rs, n, rs_hi, num, den) -> bool:
    if rs <= 0 or n <= 0:
        return False
    frac = max(1, (n * num + den - 1) // den)
    cut = min(int(rs_hi), frac)
    return 1 <= rs <= cut


def collect(dates) -> pd.DataFrame:
    """Elig+个股过滤后的候选（不做 RS 截断），带 rs/n。"""
    rows = []
    cache = {}
    for asof in DAYS:
        ctx = load_em_board_hot_map(
            asof,
            top_n=50,
            rs_top_k=50,
            min_members=10,
            arms=["today"],
            elig_bands=[(ELIG_LO, ELIG_HI)],
        )
        if ctx.get("error"):
            print("skip", asof, ctx.get("error"))
            continue
        buy_d = _next_td(asof, dates)
        if buy_d is None:
            continue
        hits = ctx.get("today_code_hits") or {}
        n0 = 0
        for c6, hit in hits.items():
            if not isinstance(hit, dict):
                continue
            try:
                elig = int(hit.get("合格榜内序位") or 0)
            except (TypeError, ValueError):
                continue
            if elig < ELIG_LO or elig > ELIG_HI:
                continue
            if "合格榜标签内RS排名" not in hit or hit.get("合格榜标签内RS排名") in (
                None,
                "",
            ):
                continue
            try:
                rs = int(hit.get("合格榜标签内RS排名") or 0)
                n = int(hit.get("合格榜标签RS样本数") or 0)
            except (TypeError, ValueError):
                continue
            if rs <= 0 or n <= 0:
                continue
            if c6 not in cache:
                cache[c6] = _load_daily(c6)
            dd = cache[c6]
            if dd is None or not _stock_filters_ok(dd, asof, c6):
                continue
            oc = _oc(dd, buy_d)
            if oc is None:
                continue
            rows.append(
                {
                    "选股日": asof.isoformat(),
                    "代码": c6,
                    "elig": elig,
                    "rs": rs,
                    "tag_rs_n": n,
                    "近10日RS": hit.get("近10日RS"),
                    "开收收益pct": oc,
                    "Cond12": _cond12(dd, asof, buy_d),
                }
            )
            n0 += 1
        print(asof, "pool", n0)
    return pd.DataFrame(rows)


def eval_cut(df: pd.DataFrame, rs_hi: int, num: int, den: int) -> dict:
    m = df.apply(
        lambda r: _rs_pass(int(r["rs"]), int(r["tag_rs_n"]), rs_hi, num, den), axis=1
    )
    sub = df[m]
    c12 = sub[sub["Cond12"]]
    y, y12 = sub["开收收益pct"], c12["开收收益pct"]
    return {
        "RS_HI": rs_hi,
        "比例": "%d/%d" % (num, den),
        "选股n": int(len(sub)),
        "选股开收均值": round(float(y.mean()), 4) if len(sub) else None,
        "选股胜率": round(float((y > 0).mean()), 4) if len(sub) else None,
        "Cond12_n": int(len(c12)),
        "Cond12开收均值": round(float(y12.mean()), 4) if len(c12) else None,
        "Cond12胜率": round(float((y12 > 0).mean()), 4) if len(c12) else None,
        "相对基线Δ选股均值": None,
        "相对基线ΔCond12均值": None,
        "相对基线ΔCond12_n": None,
    }


def main():
    dates = _calendar()
    df = collect(dates)
    if df.empty:
        raise SystemExit("empty pool")

    # no RS cut at all
    base_nocut = {
        "RS_HI": None,
        "比例": "无截断",
        "选股n": int(len(df)),
        "选股开收均值": round(float(df["开收收益pct"].mean()), 4),
        "选股胜率": round(float((df["开收收益pct"] > 0).mean()), 4),
        "Cond12_n": int(df["Cond12"].sum()),
        "Cond12开收均值": round(float(df.loc[df["Cond12"], "开收收益pct"].mean()), 4),
        "Cond12胜率": round(
            float((df.loc[df["Cond12"], "开收收益pct"] > 0).mean()), 4
        ),
    }

    rows = []
    for hi in RS_HI_LIST:
        for num, den in FRACS:
            rows.append(eval_cut(df, hi, num, den))
    grid = pd.DataFrame(rows)

    # baseline current: 50 & 1/2
    b = grid[(grid["RS_HI"] == 50) & (grid["比例"] == "1/2")].iloc[0]
    grid["相对基线Δ选股均值"] = grid["选股开收均值"] - b["选股开收均值"]
    grid["相对基线ΔCond12均值"] = grid["Cond12开收均值"] - b["Cond12开收均值"]
    grid["相对基线ΔCond12_n"] = grid["Cond12_n"] - b["Cond12_n"]

    # also one-way sweeps
    hi_sweep = grid[grid["比例"] == "1/2"].copy()
    frac_sweep = grid[grid["RS_HI"] == 50].copy()

    with pd.ExcelWriter(OUT, engine="openpyxl") as w:
        pd.DataFrame([base_nocut]).to_excel(w, sheet_name="无RS截断", index=False)
        grid.sort_values(["比例", "RS_HI"]).to_excel(w, sheet_name="全网格", index=False)
        hi_sweep.to_excel(w, sheet_name="固定1_2扫HI", index=False)
        frac_sweep.to_excel(w, sheet_name="固定HI50扫比例", index=False)
        # pivot Cond12 mean
        piv = grid.pivot(index="比例", columns="RS_HI", values="Cond12开收均值")
        piv.to_excel(w, sheet_name="透视_Cond12均值")
        piv_n = grid.pivot(index="比例", columns="RS_HI", values="Cond12_n")
        piv_n.to_excel(w, sheet_name="透视_Cond12_n")

    print("wrote", OUT)
    print("pool(no RS cut) n", len(df), "Cond12", int(df["Cond12"].sum()))
    print("\n=== 基线 RS_HI=50, 1/2 ===")
    print(b.to_string())
    print("\n=== 全网格（按 Cond12均值 降序）===")
    print(
        grid.sort_values("Cond12开收均值", ascending=False).to_string(index=False)
    )
    print("\n=== 固定1/2 扫 HI ===")
    print(hi_sweep.to_string(index=False))
    print("\n=== 固定HI=50 扫比例 ===")
    print(frac_sweep.to_string(index=False))


if __name__ == "__main__":
    main()
