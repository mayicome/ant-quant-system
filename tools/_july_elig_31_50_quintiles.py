# -*- coding: utf-8 -*-
"""七月：Elig 31–50 vs 1–30 次日开→收收益五分位（besttest 口径：最热标签 + 组内RS）。"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.eastmoney_board_rank_ctx import load_em_board_hot_map

CACHE = ROOT / "data" / "daily_cache"
OUT = ROOT / "history_data" / "八月回测-热门" / "Elig31to50_五分位_七月.xlsx"

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


def _load_daily(c6: str) -> pd.DataFrame | None:
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


def _calendar() -> list:
    cal = _load_daily("000001")
    if cal is None:
        raise SystemExit("no calendar")
    return sorted(cal["_d"].unique())


def _next_td(d: date, dates: list) -> date | None:
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
    cut = min(RS_HI, frac)
    return RS_LO <= rs <= cut


def _stock_filters_ok(dd: pd.DataFrame, asof: date, code: str, name: str) -> bool:
    """与 besttest 个股过滤一致：均线差、MA空头、近10日无涨停（简化用涨跌幅阈值近似涨停）。"""
    sub = dd[dd["_d"] <= asof]
    if len(sub) < 20:
        return False
    closes = sub["close"].astype(float).tolist()
    ma5, ma10, ma20 = _ma(closes, 5), _ma(closes, 10), _ma(closes, 20)
    if ma5 is None or ma10 is None or ma20 is None:
        return False
    lo = min(ma5, ma10)
    if lo <= 0:
        return False
    gap = abs(ma5 - ma10) / lo
    if gap < 0.005 or gap > 0.02:
        return False
    if not (ma5 < ma10 < ma20):
        return False
    # recent LU: use limit ratio by code prefix
    c = str(code).zfill(6)
    if c.startswith(("300", "301", "688", "689")):
        lim = 0.20
    elif c.startswith(("8", "4")) or c.startswith("920"):
        lim = 0.30
    else:
        lim = 0.10
    window = sub.tail(11)  # need prev close
    if len(window) < 2:
        return True
    dates = list(window["_d"])
    for i in range(1, len(dates)):
        # only last 10 trading days
        pass
    recent = sub.tail(11)
    closes_r = recent["close"].astype(float).tolist()
    for i in range(1, len(closes_r)):
        prev, cur = closes_r[i - 1], closes_r[i]
        if prev <= 0:
            continue
        if (cur / prev - 1.0) >= lim * 0.99:
            # only count if within last 10 bars of asof window
            if i >= len(closes_r) - 10:
                return False
    return True


def _open_close_ret(dd: pd.DataFrame, buy_d: date) -> float | None:
    row = dd[dd["_d"] == buy_d]
    if row.empty:
        return None
    o = float(row.iloc[0]["open"])
    c = float(row.iloc[0]["close"])
    if o <= 0:
        return None
    return (c / o - 1.0) * 100.0


def _cond12(dd: pd.DataFrame, asof: date, buy_d: date) -> bool:
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
    lo, hi = min(ma5, ma10), max(ma5, ma10)
    if not (lo <= o <= hi):
        return False
    early = _ma(closes, 4)  # approx early MA5 = mean last 4 closes
    if early is None or early <= 0:
        return False
    rel = o / early - 1.0
    return 0.0 <= rel <= 0.02


def collect_rows(dates: list) -> pd.DataFrame:
    rows = []
    daily_cache: dict = {}
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
        for c6, hit in hits.items():
            if not isinstance(hit, dict):
                continue
            try:
                elig = int(hit.get("合格榜内序位") or 0)
            except (TypeError, ValueError):
                continue
            if elig < 1 or elig > 50:
                continue
            rs = hit.get("合格榜标签内RS排名")
            n = hit.get("合格榜标签RS样本数")
            if not _rs_ok(rs, n):
                continue
            if c6 not in daily_cache:
                daily_cache[c6] = _load_daily(c6)
            dd = daily_cache[c6]
            if dd is None:
                continue
            oc = _open_close_ret(dd, buy_d)
            if oc is None:
                continue
            filt = _stock_filters_ok(dd, asof, c6, str(hit.get("股票名称") or ""))
            c12 = _cond12(dd, asof, buy_d)
            rows.append(
                {
                    "选股日": asof.isoformat(),
                    "买入日": buy_d.isoformat(),
                    "代码": c6,
                    "elig": elig,
                    "标签": hit.get("合格榜对应标签") or hit.get("选出标签") or "",
                    "开收收益pct": oc,
                    "过滤后": bool(filt),
                    "Cond12": bool(c12 and filt),  # 近似 Cond123：过滤后+夹档+Cond2
                }
            )
        print(
            asof,
            "hits",
            len(hits),
            "kept_rows_day",
            sum(1 for r in rows if r["选股日"] == asof.isoformat()),
        )
    return pd.DataFrame(rows)


def quintile_table(s: pd.DataFrame, value_col: str = "开收收益pct") -> pd.DataFrame:
    s = s.dropna(subset=["elig", value_col]).copy()
    if len(s) < 5:
        return pd.DataFrame()
    try:
        s["q"] = pd.qcut(s["elig"], 5, duplicates="drop")
    except ValueError:
        return pd.DataFrame()
    g = s.groupby("q", observed=True)[value_col].agg(["count", "mean", "median"])
    g["win"] = s.groupby("q", observed=True)[value_col].apply(lambda x: (x > 0).mean())
    g = g.reset_index()
    g["q"] = g["q"].astype(str)
    return g


def band_table(s: pd.DataFrame, value_col: str = "开收收益pct") -> pd.DataFrame:
    s = s.dropna(subset=["elig", value_col]).copy()
    s["band"] = pd.cut(
        s["elig"],
        bins=[0, 10, 20, 30, 40, 50],
        labels=["1-10", "11-20", "21-30", "31-40", "41-50"],
    )
    g = s.groupby("band", observed=True)[value_col].agg(["count", "mean", "median"])
    g["win"] = s.groupby("band", observed=True)[value_col].apply(lambda x: (x > 0).mean())
    return g.reset_index()


def main() -> None:
    dates = _calendar()
    df = collect_rows(dates)
    if df.empty:
        raise SystemExit("no rows")

    # 入列口径（仅 Elig+RS，无个股过滤）
    base = df.copy()
    # 过滤后
    filt = df[df["过滤后"]].copy()
    # Cond12 on filtered
    c12 = df[df["Cond12"]].copy()

    summaries = []
    for label, part in [
        ("入列_开收", base),
        ("过滤后_开收", filt),
        ("Cond12_开收", c12),
    ]:
        for band_name, lo, hi in [("1-30", 1, 30), ("31-50", 31, 50)]:
            sub = part[(part.elig >= lo) & (part.elig <= hi)]
            summaries.append(
                {
                    "口径": label,
                    "Elig带": band_name,
                    "n": len(sub),
                    "mean": round(float(sub["开收收益pct"].mean()), 4) if len(sub) else None,
                    "median": round(float(sub["开收收益pct"].median()), 4)
                    if len(sub)
                    else None,
                    "win": round(float((sub["开收收益pct"] > 0).mean()), 4)
                    if len(sub)
                    else None,
                }
            )

    # quintiles within 31-50 and 1-30 for 入列 / 过滤后 / Cond12
    q_frames = []
    for label, part in [
        ("入列_开收", base),
        ("过滤后_开收", filt),
        ("Cond12_开收", c12),
    ]:
        for band_name, lo, hi in [("1-30", 1, 30), ("31-50", 31, 50)]:
            sub = part[(part.elig >= lo) & (part.elig <= hi)]
            qt = quintile_table(sub)
            if qt.empty:
                continue
            qt.insert(0, "Elig带", band_name)
            qt.insert(0, "口径", label)
            q_frames.append(qt)

    bands = band_table(base)
    bands_f = band_table(filt)
    bands_c = band_table(c12)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUT, engine="openpyxl") as w:
        pd.DataFrame(summaries).to_excel(w, sheet_name="带间对比", index=False)
        bands.to_excel(w, sheet_name="入列_十分档", index=False)
        bands_f.to_excel(w, sheet_name="过滤后_十分档", index=False)
        bands_c.to_excel(w, sheet_name="Cond12_十分档", index=False)
        if q_frames:
            pd.concat(q_frames, ignore_index=True).to_excel(
                w, sheet_name="五分位", index=False
            )
        # detail 31-50 filtered for inspection
        filt[(filt.elig >= 31) & (filt.elig <= 50)].to_excel(
            w, sheet_name="明细_过滤后_31to50", index=False
        )
        c12[(c12.elig >= 31) & (c12.elig <= 50)].to_excel(
            w, sheet_name="明细_Cond12_31to50", index=False
        )

    print("wrote", OUT)
    print(pd.DataFrame(summaries).to_string(index=False))
    print("\n=== 五分位 入列 31-50 ===")
    print(quintile_table(base[(base.elig >= 31) & (base.elig <= 50)]).to_string(index=False))
    print("\n=== 五分位 过滤后 31-50 ===")
    print(quintile_table(filt[(filt.elig >= 31) & (filt.elig <= 50)]).to_string(index=False))
    print("\n=== 五分位 Cond12 31-50 ===")
    print(quintile_table(c12[(c12.elig >= 31) & (c12.elig <= 50)]).to_string(index=False))
    print("\n=== 五分位 过滤后 1-30（对照）===")
    print(quintile_table(filt[(filt.elig >= 1) & (filt.elig <= 30)]).to_string(index=False))
    print("\n=== 十分档 过滤后 ===")
    print(bands_f.to_string(index=False))
    print("\n=== 十分档 Cond12 ===")
    print(bands_c.to_string(index=False))


if __name__ == "__main__":
    main()
