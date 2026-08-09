# -*- coding: utf-8 -*-
"""七月重跑：besttest 板块Elig1-30 / 概念Elig1-40 vs 旧双边1-30。

口径：最热标签 + 组内RS + 个股过滤；Cond12=次日开盘夹档+开盘相对MA5∈[0,2%]；
收益=次日开→收%。并导出新规则选股结果 xls，便于 GUI 再跑真回测。
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
OUT_DIR = ROOT / "history_data" / "八月回测-热门"
OLD_SUM = None  # resolved below
BASE_215_CANDS = [
    OUT_DIR / "备份2" / "各日选股收益汇总_新规则.xlsx",
    OUT_DIR / "各日选股收益汇总_新规则.xlsx",
    OUT_DIR
    / "besttest_开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_条件三MA5lt10lt20_各日选股收益汇总.xlsx",
]

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
ELIG_LO = 1
ELIG_HI_SECTOR = 30
ELIG_HI_CONCEPT = 40


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
        rs, n = int(rs_rank), int(tag_rs_n)
    except (TypeError, ValueError):
        return False
    if rs <= 0 or n <= 0:
        return False
    frac = max(1, (n * RS_NUM + RS_DEN - 1) // RS_DEN)
    return RS_LO <= rs <= min(RS_HI, frac)


def _kind(hit) -> str:
    k = str(hit.get("合格榜标签类型") or "").strip().lower()
    if k in ("concept", "概念"):
        return "概念"
    if k in ("sector", "板块", "industry", "行业"):
        return "板块"
    return ""


def _elig_hi(kind: str) -> int:
    return ELIG_HI_CONCEPT if kind == "概念" else ELIG_HI_SECTOR


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
    return (c / o - 1.0) * 100.0, o, c


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


def _name_map():
    # optional names from all_a — skip if heavy; leave blank
    return {}


def collect(dates):
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
        for c6, hit in hits.items():
            if not isinstance(hit, dict):
                continue
            try:
                elig = int(hit.get("合格榜内序位") or 0)
            except (TypeError, ValueError):
                continue
            kind = _kind(hit)
            if not kind:
                continue
            if elig < ELIG_LO or elig > 50:
                continue
            if not _rs_ok(hit.get("合格榜标签内RS排名"), hit.get("合格榜标签RS样本数")):
                continue
            if c6 not in cache:
                cache[c6] = _load_daily(c6)
            dd = cache[c6]
            if dd is None:
                continue
            filt = _stock_filters_ok(dd, asof, c6)
            if not filt:
                continue
            oc_pack = _oc(dd, buy_d)
            if oc_pack is None:
                continue
            oc, o, c = oc_pack
            c12 = _cond12(dd, asof, buy_d)
            old_ok = elig <= 30  # 旧规则双边 1-30
            new_ok = elig <= _elig_hi(kind)
            if not (old_ok or new_ok):
                continue
            try:
                rs = int(hit.get("合格榜标签内RS排名") or 0)
            except (TypeError, ValueError):
                rs = 0
            rows.append(
                {
                    "选股日": asof.isoformat(),
                    "买入日": buy_d.isoformat(),
                    "股票代码": c6,
                    "股票名称": "",
                    "类型": kind,
                    "选出标签": hit.get("合格榜对应标签") or "",
                    "合格榜内序位": elig,
                    "合格榜标签内RS排名": rs,
                    "合格榜标签RS样本数": hit.get("合格榜标签RS样本数"),
                    "开盘": o,
                    "收盘": c,
                    "开收收益pct": oc,
                    "Cond12": bool(c12),
                    "旧规则入选": bool(old_ok),
                    "新规则入选": bool(new_ok),
                    "仅新规则新增": bool(new_ok and not old_ok),
                }
            )
        print(
            asof,
            "filt",
            sum(1 for r in rows if r["选股日"] == asof.isoformat()),
            "new",
            sum(1 for r in rows if r["选股日"] == asof.isoformat() and r["新规则入选"]),
            "added",
            sum(1 for r in rows if r["选股日"] == asof.isoformat() and r["仅新规则新增"]),
        )
    return pd.DataFrame(rows)


def _stats(df, label):
    y = df["开收收益pct"]
    return {
        "方案": label,
        "选股n": int(len(df)),
        "Cond12_n": int(df["Cond12"].sum()),
        "选股开收均值": round(float(y.mean()), 4) if len(df) else None,
        "选股胜率": round(float((y > 0).mean()), 4) if len(df) else None,
        "Cond12开收均值": round(float(y[df["Cond12"]].mean()), 4)
        if df["Cond12"].any()
        else None,
        "Cond12胜率": round(float((y[df["Cond12"]] > 0).mean()), 4)
        if df["Cond12"].any()
        else None,
        "概念占比_选股": round(float((df["类型"] == "概念").mean()), 4) if len(df) else None,
        "概念占比_Cond12": round(
            float((df.loc[df["Cond12"], "类型"] == "概念").mean()), 4
        )
        if df["Cond12"].any()
        else None,
    }


def main():
    dates = _calendar()
    df = collect(dates)
    if df.empty:
        raise SystemExit("empty")

    old_pool = df[df["旧规则入选"]].copy()
    new_pool = df[df["新规则入选"]].copy()
    added = df[df["仅新规则新增"]].copy()

    summary = pd.DataFrame(
        [
            _stats(old_pool, "旧_板块1-30+概念1-30"),
            _stats(new_pool, "新_板块1-30+概念1-40"),
            _stats(added, "仅新增_概念31-40"),
            _stats(old_pool[old_pool["Cond12"]], "旧_仅Cond12行"),
            _stats(new_pool[new_pool["Cond12"]], "新_仅Cond12行"),
        ]
    )

    # by day Cond12
    def day_tbl(pool, name):
        g = (
            pool[pool["Cond12"]]
            .groupby("选股日")["开收收益pct"]
            .agg(n="count", mean="mean")
            .reset_index()
        )
        g.insert(0, "方案", name)
        return g

    day = pd.concat(
        [
            day_tbl(old_pool, "旧"),
            day_tbl(new_pool, "新"),
        ],
        ignore_index=True,
    )
    day_pivot = (
        new_pool[new_pool["Cond12"]]
        .groupby("选股日")
        .agg(新_n=("开收收益pct", "count"), 新_mean=("开收收益pct", "mean"))
        .join(
            old_pool[old_pool["Cond12"]]
            .groupby("选股日")
            .agg(旧_n=("开收收益pct", "count"), 旧_mean=("开收收益pct", "mean")),
            how="outer",
        )
        .reset_index()
    )
    day_pivot["Δn"] = day_pivot["新_n"].fillna(0) - day_pivot["旧_n"].fillna(0)
    day_pivot["Δmean"] = day_pivot["新_mean"] - day_pivot["旧_mean"]

    # vs archived 215 (different pnl口径 — report separately)
    base215 = next((p for p in BASE_215_CANDS if p.exists()), None)
    note_215 = ""
    if base215 is not None:
        b = pd.read_excel(base215)
        y = pd.to_numeric(b["收益率pct"], errors="coerce")
        note_215 = "归档Cond123盯市 n=%d mean=%.4f win=%.4f file=%s" % (
            int(y.notna().sum()),
            float(y.mean()),
            float((y > 0).mean()),
            base215.name,
        )

    # export new selection (filtered pool) for GUI
    sel_cols = [
        "股票代码",
        "股票名称",
        "选股日",
        "类型",
        "选出标签",
        "合格榜内序位",
        "合格榜标签内RS排名",
        "合格榜标签RS样本数",
        "ELIG_HI_SECTOR",
        "ELIG_HI_CONCEPT",
        "HOT_MODE",
    ]
    sel = new_pool.copy()
    sel["ELIG_HI_SECTOR"] = ELIG_HI_SECTOR
    sel["ELIG_HI_CONCEPT"] = ELIG_HI_CONCEPT
    sel["HOT_MODE"] = "today_elig_sec1_30_con1_40_rs_1_50_frac1of2"
    sel_out = sel[sel_cols].sort_values(["选股日", "合格榜内序位", "股票代码"])

    sel_path = OUT_DIR / (
        "选股结果_东财热门-besttest-无涨停均线差0.5to2-MA空头-板块1to30概念1to40_"
        "2026-07-01_2026-07-31.xls"
    )
    cmp_path = OUT_DIR / "besttest_板块1-30概念1-40_七月重跑对比.xlsx"

    # xls via openpyxl as xlsx if xls engine missing — use xlsx for selection too
    sel_path = sel_path.with_suffix(".xlsx")

    with pd.ExcelWriter(cmp_path, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="汇总", index=False)
        day_pivot.to_excel(w, sheet_name="Cond12按日", index=False)
        new_pool[new_pool["Cond12"]].to_excel(w, sheet_name="新_Cond12明细", index=False)
        added[added["Cond12"]].to_excel(w, sheet_name="新增_Cond12明细", index=False)
        added.to_excel(w, sheet_name="新增_选股明细", index=False)
        pd.DataFrame(
            [
                {
                    "说明": "收益均为次日开→收%；Cond12=夹档+相对MA5∈[0,2%]；与归档215盯市口径不同",
                    "归档215": note_215,
                }
            ]
        ).to_excel(w, sheet_name="口径说明", index=False)

    sel_out.to_excel(sel_path, index=False)

    print("wrote", cmp_path)
    print("wrote", sel_path)
    print(summary.to_string(index=False))
    print("\n新增概念31-40: 选股", len(added), "Cond12", int(added["Cond12"].sum()))
    if len(added):
        print(
            "新增 Cond12 mean",
            round(float(added.loc[added["Cond12"], "开收收益pct"].mean()), 4)
            if added["Cond12"].any()
            else None,
        )
    print(note_215)


if __name__ == "__main__":
    main()
