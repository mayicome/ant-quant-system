# -*- coding: utf-8 -*-
"""马总 ma10 单点（6月选股窗 20260815_203110）：上MA10 全流程分析。"""
from __future__ import annotations

import json
import sys
import warnings
from collections import defaultdict
from datetime import date, datetime, timedelta
from itertools import combinations
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.daily_cache_reader import load_daily_from_cache  # noqa: E402
from utils.trading_day import get_trading_dates_in_range_sorted  # noqa: E402

DIR = ROOT / "history_data" / "马总选股逻辑"
TAG = "ma10"
STAMP = "20260815_203110"
PER_STOCK = DIR / f"各日选股收益汇总_日线-{TAG}-单点_按票_{STAMP}.xlsx"
DAILY_FUND = DIR / f"各日选股收益汇总_日线-{TAG}-单点_{STAMP}.xlsx"
BUY_CSV = DIR / f"回测成交明细_日线-{TAG}买入_{STAMP}.csv"
SELL_CSV = DIR / f"回测成交明细_日线-{TAG}卖出_{STAMP}.csv"
OUT_ABOVE = DIR / f"各日选股收益汇总_日线-{TAG}-单点_按票_{STAMP}_收盘上MA10.xlsx"
OUT_JSON = DIR / f"_{TAG}_june_flow_analysis.json"


def code6(v) -> str:
    try:
        return str(int(float(v))).zfill(6)
    except Exception:
        s = "".join(c for c in str(v or "") if c.isdigit())
        return s.zfill(6)[-6:] if s else ""


def stats(g: pd.DataFrame) -> dict:
    r = g["ret"]
    cs = g["ret_cs"] if "ret_cs" in g.columns else r * 0
    w = g["buy_amt"] if "buy_amt" in g.columns else None
    wret = float(np.average(r, weights=w)) if w is not None and w.fillna(0).sum() > 0 else None
    day = g.groupby("sel")["ret"].mean()
    return {
        "n": int(len(g)),
        "mean": round(float(r.mean()), 4),
        "med": round(float(r.median()), 4),
        "win": round(float((r > 0).mean() * 100), 2),
        "wret": None if wret is None else round(wret, 4),
        "cs": round(float(cs.mean()), 4),
        "day_mean": round(float(day.mean()), 4) if len(day) else None,
        "n_days": int(day.shape[0]),
        "pos_days": int((day > 0).sum()) if len(day) else 0,
    }


def qcut_table(df, col, q, scale=1.0):
    d = df[df[col].notna()].copy()
    d["qb"] = pd.qcut(d[col], q, duplicates="drop")
    rows = []
    for i, (_lab, g) in enumerate(d.groupby("qb", observed=True)):
        lo, hi = float(g[col].min()) * scale, float(g[col].max()) * scale
        st = stats(g)
        rows.append({"q": i + 1, "lo": round(lo, 4), "hi": round(hi, 4), **st})
    return rows


def limit_ratio(code: str, name: str, as_of: date) -> float:
    name_u = str(name or "").upper()
    c = code6(code)
    if c.startswith(("300", "301", "688", "689")):
        return 0.20
    if c.startswith(("8", "4", "920")):
        return 0.30
    if "ST" in name_u:
        return 0.10 if as_of and as_of >= date(2026, 7, 6) else 0.05
    return 0.10


def is_lu(prev_close, close, lr) -> bool:
    if prev_close is None or prev_close <= 0:
        return False
    lu = round(float(prev_close) * (1.0 + lr), 2)
    diff = abs(float(close) - lu)
    inc = (float(close) - float(prev_close)) / float(prev_close)
    return (diff < 0.02) or (inc >= lr * 0.99)


def recent_lu_stats(code, name, daily_df, as_of: date, lookback=10):
    if daily_df is None or daily_df.empty:
        return 0, ""
    dd = daily_df[daily_df["date"] <= as_of].sort_values("date")
    prev = dd[dd["date"] < as_of]
    if prev.empty:
        return 0, ""
    dates = list(prev["date"].tolist())
    window = dates[-lookback:] if len(dates) >= lookback else dates
    offsets = []
    for i, td in enumerate(window):
        sub = prev[prev["date"] == td]
        if sub.empty:
            continue
        before = dd[dd["date"] < td]
        if before.empty:
            continue
        pc = float(before.iloc[-1]["close"])
        cl = float(sub.iloc[-1]["close"])
        if is_lu(pc, cl, limit_ratio(code, name, td)):
            offsets.append(len(window) - i)
    return len(offsets), (min(offsets) if offsets else "")


def as_bool(s: pd.Series) -> pd.Series:
    if s.dtype == object:
        return s.map(lambda x: str(x).strip().lower() in ("1", "true", "yes", "是", "y", "t"))
    return s.fillna(False).astype(bool)


def prepare():
    df = pd.read_excel(PER_STOCK)
    df["sel"] = pd.to_datetime(df["选股日"]).dt.date
    df["code"] = df["代码"].map(code6)
    df["name"] = df["股票名称"].astype(str) if "股票名称" in df.columns else ""
    df["ret"] = pd.to_numeric(df["收益率pct"], errors="coerce")
    df["buy_amt"] = pd.to_numeric(df["买入金额合计"], errors="coerce")
    for c in [
        "MA5", "MA10", "MA20", "近5日RS", "近10日RS", "近20日RS", "买入成交价",
        "前十个交易日最高涨幅", "主力净流入_万元", "净流入占流通%", "流通市值_亿",
        "今日热门板块最高排名A", "今日热门概念最高排名B",
    ]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[df["ret"].notna()].copy()

    cache = {}
    above, closes = [], []
    for i, r in enumerate(df.itertuples(index=False)):
        if r.code not in cache:
            cache[r.code] = load_daily_from_cache(r.code, through_date=None)
        dd = cache[r.code]
        cl = None
        if dd is not None and not dd.empty:
            sub = dd[dd["date"] == r.sel]
            if not sub.empty:
                cl = float(sub.iloc[-1]["close"])
        closes.append(cl)
        ma10 = r.MA10 if pd.notna(r.MA10) else None
        above.append(bool(cl is not None and ma10 is not None and cl > float(ma10)))
        if (i + 1) % 300 == 0:
            print("close", i + 1, "/", len(df), flush=True)
    df["sel_close"] = closes
    df["above_ma10"] = above

    lu_n, lu_ago = [], []
    for i, r in enumerate(df.itertuples(index=False)):
        if (i + 1) % 300 == 0:
            print("lu", i + 1, "/", len(df), flush=True)
        n, ago = recent_lu_stats(r.code, r.name, cache.get(r.code), r.sel, 10)
        lu_n.append(n)
        lu_ago.append(ago)
    df["lu10"] = lu_n
    df["lu_ago"] = lu_ago

    df["ma_gap"] = (df["MA5"] - df["MA10"]) / df["MA10"]
    df["is20"] = df["code"].str.startswith(("300", "301", "688", "689"))
    df["mx_n"] = np.where(df["is20"], df["前十个交易日最高涨幅"] / 2.0, df["前十个交易日最高涨幅"])
    df["px"] = df["买入成交价"]
    df["inflow"] = df["主力净流入_万元"]
    df["mv"] = df["流通市值_亿"]
    if "条件_前10日无大涨" in df.columns:
        df["no_big"] = as_bool(df["条件_前10日无大涨"])
    if "条件_行业或概念排名达标" in df.columns:
        df["rank_ok"] = as_bool(df["条件_行业或概念排名达标"])

    above_df = df[df["above_ma10"]].copy()
    above_df["ret_cs"] = above_df["ret"] - above_df.groupby("sel")["ret"].transform("mean")
    df["ret_cs"] = df["ret"] - df.groupby("sel")["ret"].transform("mean")

    save = above_df.copy()
    save["最近10个交易日内的涨停板数量"] = save["lu10"]
    save["最近的涨停板是几日前"] = save["lu_ago"]
    save.to_excel(OUT_ABOVE, index=False)
    print("wrote", OUT_ABOVE, "n=", len(save), flush=True)
    return df, above_df


def analyze_factors(above: pd.DataFrame) -> dict:
    out = {"n": len(above), "base": stats(above)}
    out["close_gt_ma5"] = stats(above[above["sel_close"] > above["MA5"]])
    out["close_le_ma5"] = stats(above[above["sel_close"] <= above["MA5"]])
    out["close_gt_ma20"] = stats(above[above["sel_close"] > above["MA20"]])
    out["close_le_ma20"] = stats(above[above["sel_close"] <= above["MA20"]])
    out["ma5_gt_ma10"] = stats(above[above["MA5"] > above["MA10"]])
    out["ma5_le_ma10"] = stats(above[above["MA5"] <= above["MA10"]])
    out["ma10_lt_ma20"] = stats(above[above["MA10"] < above["MA20"]])
    out["ma10_ge_ma20"] = stats(above[above["MA10"] >= above["MA20"]])
    a = above["MA5"] > above["MA10"]
    b = above["MA10"] < above["MA20"]
    out["align_5gt10_10lt20"] = stats(above[a & b])
    out["align_5gt10_10ge20"] = stats(above[a & ~b])
    out["align_5le10_10lt20"] = stats(above[~a & b])
    out["align_5le10_10ge20"] = stats(above[~a & ~b])

    above = above.copy()
    above["dist_ma10"] = (above["sel_close"] - above["MA10"]) / above["MA10"] * 100
    out["corr_dist_ma10"] = round(float(above["dist_ma10"].corr(above["ret"])), 4)
    out["dist_ma10_q5"] = qcut_table(above, "dist_ma10", 5)
    out["corr_ma_gap"] = round(float(above["ma_gap"].corr(above["ret"])), 4)
    out["ma_gap_q5"] = qcut_table(above, "ma_gap", 5, scale=100)
    out["ma_gap_q10"] = qcut_table(above, "ma_gap", 10, scale=100)

    out["corr_px"] = round(float(above["px"].corr(above["ret"])), 4)
    out["px_q5"] = qcut_table(above, "px", 5)
    out["px_q10"] = qcut_table(above, "px", 10)

    for col, tag in [("近5日RS", "rs5"), ("近10日RS", "rs10"), ("近20日RS", "rs20")]:
        out[tag + "_corr"] = round(float(above[col].corr(above["ret"])), 4)
        out[tag + "_q5"] = qcut_table(above, col, 5, scale=100)
        out[tag + "_q10"] = qcut_table(above, col, 10, scale=100)

    out["corr_inflow"] = round(float(above["inflow"].corr(above["ret"])), 4)
    out["inflow_q5"] = qcut_table(above, "inflow", 5)
    bands = [
        ("<0", above["inflow"] < 0),
        ("0-3000", (above["inflow"] >= 0) & (above["inflow"] < 3000)),
        ("3000-10000", (above["inflow"] >= 3000) & (above["inflow"] < 10000)),
        ("1-3亿", (above["inflow"] >= 10000) & (above["inflow"] < 30000)),
        (">=3亿", above["inflow"] >= 30000),
    ]
    out["inflow_bands"] = []
    for lab, m in bands:
        g = above[m & above["inflow"].notna()]
        if len(g):
            st = stats(g)
            st["lab"] = lab
            out["inflow_bands"].append(st)

    out["corr_mxn"] = round(float(above["mx_n"].corr(above["ret"])), 4)
    out["mxn_q5"] = qcut_table(above, "mx_n", 5)
    if "no_big" in above.columns:
        out["no_big_move"] = stats(above[above["no_big"]])
        out["has_big_move"] = stats(above[~above["no_big"]])

    out["lu_no"] = stats(above[above["lu10"] <= 0])
    out["lu_yes"] = stats(above[above["lu10"] > 0])
    for k in [0, 1, 2, 3]:
        g = above[above["lu10"] == k] if k < 3 else above[above["lu10"] >= 3]
        out["lu_cnt_%s" % ("ge3" if k == 3 else k)] = stats(g)
    g0 = above[above["lu10"] > 0]
    out["lu_ago_bands"] = []
    for lab, m in [
        ("1日", g0["lu_ago"] == 1),
        ("2-3日", g0["lu_ago"].between(2, 3)),
        ("4-5日", g0["lu_ago"].between(4, 5)),
        ("6-10日", g0["lu_ago"].between(6, 10)),
    ]:
        gg = g0[m]
        if len(gg):
            st = stats(gg)
            st["lab"] = lab
            out["lu_ago_bands"].append(st)

    out["corr_mv"] = round(float(above["mv"].corr(above["ret"])), 4)
    out["mv_q5"] = qcut_table(above, "mv", 5)

    if "rank_ok" in above.columns:
        out["rank_ok"] = stats(above[above["rank_ok"]])
        out["rank_not"] = stats(above[~above["rank_ok"]])

    for col, tag in [
        ("今日热门板块最高排名A", "hot_ind_A"),
        ("今日热门概念最高排名B", "hot_con_B"),
    ]:
        if col not in above.columns:
            continue
        d = above[above[col].notna()]
        bands2 = [
            ("1-10", (d[col] >= 1) & (d[col] <= 10)),
            ("11-20", (d[col] >= 11) & (d[col] <= 20)),
            ("21-50", (d[col] >= 21) & (d[col] <= 50)),
            ("51+", d[col] > 50),
        ]
        out[tag + "_bands"] = []
        for lab, m in bands2:
            g = d[m]
            if len(g):
                st = stats(g)
                st["lab"] = lab
                out[tag + "_bands"].append(st)
    return out


def combo_screen(above: pd.DataFrame) -> list:
    F = {
        "MA5>MA10": above["MA5"] > above["MA10"],
        "gap>-1.3%": above["ma_gap"] > -0.013,
        "MA10<MA20": above["MA10"] < above["MA20"],
        "价≤11.6": above["px"] <= 11.6,
        "价≤18": above["px"] <= 18.14,
        "市值≤54亿": above["mv"] <= 54.34,
        "市值≤91亿": above["mv"] <= 90.86,
        "流入<3000万": above["inflow"] < 3000,
        "流入<1亿": above["inflow"] < 10000,
        "B": (above["近5日RS"] > 0.135) & (above["近10日RS"] > 0.06) & (above["近20日RS"] < 0.25),
        "无大涨": above["no_big"] if "no_big" in above.columns else pd.Series(False, index=above.index),
        "mxn≤5%": above["mx_n"] <= 5,
    }

    def ev(name, m):
        g = above[m.fillna(False)]
        if len(g) == 0:
            return None
        st = stats(g)
        st["name"] = name
        st["coverage"] = round(len(g) / len(above) * 100, 1)
        return st

    rows = [ev("基线上MA10", pd.Series(True, index=above.index))]
    for k, m in F.items():
        rows.append(ev(k, m))
    stacks = [
        ("B", ["B"]),
        ("B+MA5>MA10", ["B", "MA5>MA10"]),
        ("B+gap>-1.3%", ["B", "gap>-1.3%"]),
        ("B+价≤18", ["B", "价≤18"]),
        ("B+价≤11.6", ["B", "价≤11.6"]),
        ("B+市值≤91", ["B", "市值≤91亿"]),
        ("B+市值≤54", ["B", "市值≤54亿"]),
        ("B+流入<1亿", ["B", "流入<1亿"]),
        ("B+流入<3000", ["B", "流入<3000万"]),
        ("B+无大涨", ["B", "无大涨"]),
        ("B+价≤18+市值≤91", ["B", "价≤18", "市值≤91亿"]),
        ("B+价≤18+市值≤54", ["B", "价≤18", "市值≤54亿"]),
        ("B+市值≤54+流入<1亿", ["B", "市值≤54亿", "流入<1亿"]),
        ("B+价≤18+MA5>MA10", ["B", "价≤18", "MA5>MA10"]),
        ("B+5>10+10<20", ["B", "MA5>MA10", "MA10<MA20"]),
        ("无B:5>10+价≤11.6+市值≤54", ["MA5>MA10", "价≤11.6", "市值≤54亿"]),
        ("无B:价≤11.6+市值≤54+流入<3000", ["价≤11.6", "市值≤54亿", "流入<3000万"]),
    ]
    for name, keys in stacks:
        m = pd.Series(True, index=above.index)
        for k in keys:
            m = m & F[k].fillna(False)
        rows.append(ev(name, m))

    boosters = ["MA5>MA10", "价≤18", "价≤11.6", "市值≤91亿", "市值≤54亿", "流入<1亿", "gap>-1.3%"]
    seen = {r["name"] for r in rows if r}
    for r in range(1, 3):
        for combo in combinations(boosters, r):
            keys = ["B"] + list(combo)
            name = "+".join(keys)
            if name in seen:
                continue
            m = F["B"].fillna(False)
            for k in combo:
                m = m & F[k].fillna(False)
            row = ev(name, m)
            if row and row["n"] >= 40:
                rows.append(row)
                seen.add(name)
    return [r for r in rows if r]


def sell_hold(above_b_keys):
    """卖出持有 1..10：买入不变；【买入次日】=第1日；期内原卖出保留；到期收盘清。

    与 CLI --sell-hold 同口径（引擎含买入日计数由 CLI 侧 +1 对齐）。
    """
    buy = pd.read_csv(BUY_CSV, encoding="utf-8-sig")
    sell = pd.read_csv(SELL_CSV, encoding="utf-8-sig")
    for d in (buy, sell):
        d["sel"] = pd.to_datetime(d["选股日"], errors="coerce").dt.date
        d["code"] = d["代码"].map(code6)
        d["date"] = pd.to_datetime(d["日期"], errors="coerce").dt.date
        d["qty"] = pd.to_numeric(d["数量"], errors="coerce").fillna(0).astype(int)
        d["amt"] = pd.to_numeric(d["金额"], errors="coerce").fillna(0.0)
    key_df = pd.DataFrame(above_b_keys, columns=["sel", "code"])
    buy = buy.merge(key_df, on=["sel", "code"]).query("qty>0")
    sell = sell.merge(key_df, on=["sel", "code"]).query("qty>0")
    lo = min(k[0] for k in above_b_keys) - timedelta(days=5)
    hi = max(k[0] for k in above_b_keys) + timedelta(days=40)
    tdays = get_trading_dates_in_range_sorted(lo, hi)
    tindex = {d: i for i, d in enumerate(tdays)}

    def next_td(d: date):
        i = tindex.get(d)
        if i is None:
            for j, x in enumerate(tdays):
                if x > d:
                    return x, j
            return None, None
        if i + 1 >= len(tdays):
            return None, None
        return tdays[i + 1], i + 1

    def hold_end(buy_d, hold_n):
        # 与 CLI --sell-hold / engine code_sell_day_index 一致：买入日=第1天（含买入日）
        i = tindex.get(buy_d)
        if i is None:
            for j, x in enumerate(tdays):
                if x >= buy_d:
                    i = j
                    break
            else:
                return None
        k = i + int(hold_n) - 1
        return tdays[k] if k < len(tdays) else None

    buy_g, sell_g = defaultdict(list), defaultdict(list)
    for r in buy.itertuples(index=False):
        buy_g[(r.sel, r.code)].append({"date": r.date, "qty": int(r.qty), "amt": float(r.amt)})
    for r in sell.itertuples(index=False):
        sell_g[(r.sel, r.code)].append({"date": r.date, "qty": int(r.qty), "amt": float(r.amt)})

    codes = sorted({k[1] for k in above_b_keys})
    close_map = {}
    for c in codes:
        dd = load_daily_from_cache(c, through_date=None)
        close_map[c] = [] if dd is None or dd.empty else list(
            zip(dd["date"].tolist(), dd["close"].astype(float).tolist())
        )

    def mark_close(code, as_of):
        px = None
        for d, p in close_map.get(code) or []:
            if d <= as_of:
                px = p
            else:
                break
        return px

    def pnl(sel, code, hold_n):
        lots = [dict(x) for x in buy_g.get((sel, code), [])]
        if not lots:
            return None
        sells = [dict(x) for x in sell_g.get((sel, code), [])]
        for lot in lots:
            end = hold_end(lot["date"], hold_n)
            if end is None:
                return None
            lot["end"] = end
            lot["rem"] = lot["qty"]
            lot["sell_amt"] = 0.0
        si = 0
        for lot in lots:
            while si < len(sells) and lot["rem"] > 0:
                s = sells[si]
                if s["date"] <= lot["date"]:
                    si += 1
                    continue
                if s["date"] > lot["end"]:
                    break
                take = min(lot["rem"], s["qty"])
                if take <= 0:
                    si += 1
                    continue
                px = s["amt"] / s["qty"] if s["qty"] else 0.0
                lot["sell_amt"] += take * px
                lot["rem"] -= take
                s["qty"] -= take
                s["amt"] -= take * px
                if s["qty"] <= 0:
                    si += 1
        buy_amt = sum(l["amt"] for l in lots)
        if buy_amt <= 0:
            return None
        proceeds = sum(l["sell_amt"] for l in lots)
        for lot in lots:
            if lot["rem"] > 0:
                px = mark_close(code, lot["end"])
                if px is None:
                    return None
                proceeds += lot["rem"] * px
        return (proceeds - buy_amt) / buy_amt * 100.0, buy_amt

    results = {n: [] for n in range(1, 11)}
    for sel, code in above_b_keys:
        for n in range(1, 11):
            r = pnl(sel, code, n)
            if r is None:
                continue
            results[n].append((sel, r[0], r[1]))
    hold_rows = []
    for n in range(1, 11):
        rows = results[n]
        arr = np.array([x[1] for x in rows])
        w = np.array([x[2] for x in rows])
        by = defaultdict(list)
        for sel, ret, _a in rows:
            by[sel].append(ret)
        day_means = [float(np.mean(v)) for v in by.values()]
        mean = float(arr.mean()) if len(arr) else np.nan
        hold_rows.append(
            {
                "hold": n,
                "n": len(arr),
                "mean": round(mean, 4),
                "med": round(float(np.median(arr)), 4) if len(arr) else None,
                "win": round(float((arr > 0).mean() * 100), 2) if len(arr) else None,
                "wret": round(float(np.average(arr, weights=w)), 4) if w.sum() > 0 else None,
                "day_mean": round(float(np.mean(day_means)), 4) if day_means else None,
                "pos_days": int(sum(1 for x in day_means if x > 0)),
                "n_days": len(day_means),
            }
        )
    m10 = hold_rows[9]["mean"]
    for row in hold_rows:
        row["vs10"] = round(row["mean"] - m10, 4)
    return hold_rows


def main():
    print("prepare...", flush=True)
    df, above = prepare()
    print("n all", len(df), "above", len(above), flush=True)

    fund = pd.read_excel(DAILY_FUND)
    fund_sum = round(float(pd.to_numeric(fund["总收益率%"], errors="coerce").sum()), 4)

    b = (
        (above["近5日RS"] > 0.135)
        & (above["近10日RS"] > 0.06)
        & (above["近20日RS"] < 0.25)
    )
    out = {
        "daily_fund_sum": fund_sum,
        "all": stats(df.assign(ret_cs=df["ret"] - df.groupby("sel")["ret"].transform("mean"))),
        "above": stats(above),
        "below": stats(df[~df["above_ma10"]].assign(
            ret_cs=lambda x: x["ret"] - x.groupby("sel")["ret"].transform("mean")
        )),
        "B": stats(above[b]),
        "notB": stats(above[~b]),
    }
    print("factors...", flush=True)
    out["factors"] = analyze_factors(above)

    # B daily
    daily_rows = []
    for sel, sub_all in above.groupby("sel"):
        sub_b = sub_all[b.loc[sub_all.index]]
        daily_rows.append(
            {
                "sel": str(sel),
                "cand": int(len(df[df["sel"] == sel])),
                "above": int(len(sub_all)),
                "B": int(len(sub_b)),
                "B_mean": round(float(sub_b["ret"].mean()), 4) if len(sub_b) else None,
                "B_win": round(float((sub_b["ret"] > 0).mean() * 100), 2) if len(sub_b) else None,
                "above_mean": round(float(sub_all["ret"].mean()), 4),
            }
        )
    out["B_daily"] = daily_rows

    print("combos...", flush=True)
    out["combos"] = combo_screen(above)

    keys = list(zip(above.loc[b, "sel"], above.loc[b, "code"]))
    print("sell hold B n=", len(keys), flush=True)
    out["sell_hold"] = sell_hold(keys)

    # print overview
    for k in ("all", "above", "below", "B", "notB"):
        print(k, out[k])
    print("fund_sum", fund_sum)
    print("\nRS corr", out["factors"]["rs5_corr"], out["factors"]["rs10_corr"], out["factors"]["rs20_corr"])
    print("\n=== RS5 q5 ===")
    for r in out["factors"]["rs5_q5"]:
        print(r)
    print("=== RS5 q10 ===")
    for r in out["factors"]["rs5_q10"]:
        print(r)
    print("=== RS10 q5 ===")
    for r in out["factors"]["rs10_q5"]:
        print(r)
    print("=== RS10 q10 ===")
    for r in out["factors"]["rs10_q10"]:
        print(r)
    print("=== RS20 q5 ===")
    for r in out["factors"]["rs20_q5"]:
        print(r)
    print("=== RS20 q10 ===")
    for r in out["factors"]["rs20_q10"]:
        print(r)

    print("\n=== combos n>=80 mean>=1.2 ===")
    comb = pd.DataFrame(out["combos"])
    print(
        comb[(comb["n"] >= 80) & (comb["mean"] >= 1.2)]
        .sort_values(["mean", "n"], ascending=[False, False])
        .head(20)[["name", "n", "n_days", "mean", "med", "win", "day_mean", "coverage"]]
        .to_string(index=False)
    )
    print("\n=== sell hold ===")
    for r in out["sell_hold"]:
        print(r)
    print("\n=== B daily ===")
    for r in out["B_daily"]:
        print(r)

    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("wrote", OUT_JSON, flush=True)


if __name__ == "__main__":
    main()
