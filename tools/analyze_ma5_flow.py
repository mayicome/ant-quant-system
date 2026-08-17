# -*- coding: utf-8 -*-
"""马总 ma5 单点：按 MA10 流程做总览 / 上MA5 / 因子 / B / 按日 / 卖出持有期。"""
from __future__ import annotations

import json
import sys
import warnings
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.daily_cache_reader import load_daily_from_cache  # noqa: E402
from utils.trading_day import get_trading_dates_in_range_sorted  # noqa: E402

DIR = ROOT / "history_data" / "马总选股逻辑"
PER_STOCK = DIR / "各日选股收益汇总_日线-ma5-单点_按票_20260815_193552.xlsx"
DAILY_FUND = DIR / "各日选股收益汇总_日线-ma5-单点_20260815_193552.xlsx"
BUY_CSV = DIR / "回测成交明细_日线-ma5买入_20260815_193552.csv"
SELL_CSV = DIR / "回测成交明细_日线-ma5卖出_20260815_193552.csv"
OUT_ABOVE = DIR / "各日选股收益汇总_日线-ma5-单点_按票_20260815_193552_收盘上MA5.xlsx"
OUT_JSON = DIR / "_ma5_flow_analysis.json"


def code6(v) -> str:
    try:
        return str(int(float(v))).zfill(6)
    except Exception:
        s = "".join(c for c in str(v or "") if c.isdigit())
        return s.zfill(6)[-6:] if s else ""


def parse_d(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return pd.to_datetime(v).date()
    except Exception:
        return None


def stats(g: pd.DataFrame, ret_col="ret") -> dict:
    r = g[ret_col]
    cs = g["ret_cs"] if "ret_cs" in g.columns else r * 0
    w = g["buy_amt"] if "buy_amt" in g.columns else None
    wret = (
        float(np.average(r, weights=w))
        if w is not None and w.fillna(0).sum() > 0
        else None
    )
    day = g.groupby("sel")[ret_col].mean()
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


def qcut_table(df, col, q, as_pct=False):
    d = df[df[col].notna()].copy()
    d["q"] = pd.qcut(d[col], q, duplicates="drop")
    rows = []
    for i, (_lab, g) in enumerate(d.groupby("q", observed=True)):
        lo, hi = float(g[col].min()), float(g[col].max())
        if as_pct:
            lo, hi = lo * 100, hi * 100
        st = stats(g)
        rows.append(
            {
                "q": i + 1,
                "lo": round(lo, 4),
                "hi": round(hi, 4),
                **{k: st[k] for k in ("n", "mean", "med", "win", "cs")},
            }
        )
    return rows


def limit_ratio(code: str, name: str, as_of: date) -> float:
    name_u = str(name or "").upper()
    c = code6(code)
    if c.startswith(("300", "301", "688", "689")):
        return 0.20
    if c.startswith(("8", "4", "920")):
        return 0.30
    if "ST" in name_u:
        if as_of and as_of >= date(2026, 7, 6):
            return 0.10
        return 0.05
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
            offsets.append(len(window) - i)  # 1=昨
    return len(offsets), (min(offsets) if offsets else "")


def prepare():
    df = pd.read_excel(PER_STOCK)
    df["sel"] = pd.to_datetime(df["选股日"]).dt.date
    df["code"] = df["代码"].map(code6)
    df["name"] = df["股票名称"].astype(str) if "股票名称" in df.columns else ""
    df["ret"] = pd.to_numeric(df["收益率pct"], errors="coerce")
    df["buy_amt"] = pd.to_numeric(df["买入金额合计"], errors="coerce")
    for c in [
        "MA5",
        "MA10",
        "MA20",
        "近5日RS",
        "近10日RS",
        "近20日RS",
        "买入成交价",
        "前十个交易日最高涨幅",
        "主力净流入_万元",
        "净流入占流通%",
    ]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[df["ret"].notna()].copy()

    # 上MA5：选股日收盘 > MA5
    cache = {}
    above = []
    closes = []
    for r in df.itertuples(index=False):
        c = r.code
        if c not in cache:
            cache[c] = load_daily_from_cache(c, through_date=None)
        dd = cache[c]
        cl = None
        if dd is not None and not dd.empty:
            sub = dd[dd["date"] == r.sel]
            if not sub.empty:
                cl = float(sub.iloc[-1]["close"])
        closes.append(cl)
        ma5 = r.MA5 if pd.notna(r.MA5) else None
        above.append(bool(cl is not None and ma5 is not None and cl > float(ma5)))
    df["sel_close"] = closes
    df["above_ma5"] = above

    # 重算近10日涨停（不含选股日）
    lu_n, lu_ago = [], []
    for i, r in enumerate(df.itertuples(index=False)):
        if (i + 1) % 200 == 0:
            print("lu", i + 1, "/", len(df), flush=True)
        n, ago = recent_lu_stats(r.code, r.name, cache.get(r.code), r.sel, 10)
        lu_n.append(n)
        lu_ago.append(ago)
    df["lu10"] = lu_n
    df["lu_ago"] = lu_ago

    # derived
    df["ma_gap"] = (df["MA5"] - df["MA10"]) / df["MA10"]
    df["mx"] = df["前十个交易日最高涨幅"]
    df["is20"] = df["code"].str.startswith(("300", "301", "688", "689"))
    df["mx_n"] = np.where(df["is20"], df["mx"] / 2.0, df["mx"])
    df["px"] = df["买入成交价"]

    above_df = df[df["above_ma5"]].copy()
    above_df["ret_cs"] = above_df["ret"] - above_df.groupby("sel")["ret"].transform("mean")
    df["ret_cs"] = df["ret"] - df.groupby("sel")["ret"].transform("mean")

    # save above file (full columns + flags)
    save = above_df.copy()
    save["最近10个交易日内的涨停板数量"] = save["lu10"]
    save["最近的涨停板是几日前"] = save["lu_ago"]
    save.to_excel(OUT_ABOVE, index=False)
    print("wrote", OUT_ABOVE, "n=", len(save), flush=True)
    return df, above_df, cache


def analyze(df, above):
    out = {}
    fund = pd.read_excel(DAILY_FUND)
    out["daily_fund_sum"] = round(float(pd.to_numeric(fund["总收益率%"], errors="coerce").sum()), 4)
    out["all"] = stats(df)
    out["above"] = stats(above)
    out["below"] = stats(df[~df["above_ma5"]])

    # MA gap (MA5-MA10)/MA10
    out["ma_gap_q5"] = qcut_table(above, "ma_gap", 5, as_pct=True)
    out["ma_gap_q10"] = qcut_table(above, "ma_gap", 10, as_pct=True)
    out["ma5_gt_ma10"] = stats(above[above["ma_gap"] > 0])
    out["ma5_le_ma10"] = stats(above[above["ma_gap"] <= 0])
    out["ma10_lt_ma20"] = stats(above[above["MA10"] < above["MA20"]])
    out["ma10_ge_ma20"] = stats(above[above["MA10"] >= above["MA20"]])

    # price
    out["px_q5"] = qcut_table(above, "px", 5)
    out["corr_px"] = round(float(above["px"].corr(above["ret"])), 4)

    # RS
    for col, tag in [("近5日RS", "rs5"), ("近10日RS", "rs10"), ("近20日RS", "rs20")]:
        out[tag + "_corr"] = round(float(above[col].corr(above["ret"])), 4)
        out[tag + "_q5"] = qcut_table(above, col, 5, as_pct=True)
        out[tag + "_q10"] = qcut_table(above, col, 10, as_pct=True)

    # prior max norm
    out["mxn_corr"] = round(float(above["mx_n"].corr(above["ret"])), 4)
    out["mxn_q5"] = qcut_table(above.dropna(subset=["mx_n"]), "mx_n", 5)

    # limit up prior
    above["has_lu"] = above["lu10"] > 0
    out["lu_no"] = stats(above[~above["has_lu"]])
    out["lu_yes"] = stats(above[above["has_lu"]])
    for k in [0, 1, 2, 3]:
        g = above[above["lu10"] == k] if k < 3 else above[above["lu10"] >= 3]
        out["lu_cnt_%s" % ("ge3" if k == 3 else k)] = stats(g)

    # B filter
    b = (
        (above["近5日RS"] > 0.135)
        & (above["近10日RS"] > 0.06)
        & (above["近20日RS"] < 0.25)
    )
    out["B"] = stats(above[b])
    out["notB"] = stats(above[~b])

    # daily B
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
                "B_med": round(float(sub_b["ret"].median()), 4) if len(sub_b) else None,
                "B_win": round(float((sub_b["ret"] > 0).mean() * 100), 2) if len(sub_b) else None,
                "above_mean": round(float(sub_all["ret"].mean()), 4),
            }
        )
    out["B_daily"] = daily_rows

    # inflow
    if "主力净流入_万元" in above.columns:
        out["inflow_q5"] = qcut_table(above.dropna(subset=["主力净流入_万元"]), "主力净流入_万元", 5)
        out["corr_inflow"] = round(float(above["主力净流入_万元"].corr(above["ret"])), 4)

    return out, b


def sell_hold_analysis(above_b_keys, out: dict):
    """卖出持有 1..10：买入不变；【买入次日】=第1日；到期收盘清。

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

    def hold_end(buy_d: date, hold_n: int):
        start, j = next_td(buy_d)
        if start is None:
            return None
        k = j + hold_n - 1
        return tdays[k] if k < len(tdays) else None

    buy_g = defaultdict(list)
    sell_g = defaultdict(list)
    for r in buy.itertuples(index=False):
        buy_g[(r.sel, r.code)].append({"date": r.date, "qty": int(r.qty), "amt": float(r.amt)})
    for r in sell.itertuples(index=False):
        sell_g[(r.sel, r.code)].append({"date": r.date, "qty": int(r.qty), "amt": float(r.amt)})

    codes = sorted({k[1] for k in above_b_keys})
    close_map = {}
    for c in codes:
        dd = load_daily_from_cache(c, through_date=None)
        close_map[c] = (
            []
            if dd is None or dd.empty
            else list(zip(dd["date"].tolist(), dd["close"].astype(float).tolist()))
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
        proceeds = 0.0
        for lot in lots:
            proceeds += lot["sell_amt"]
            if lot["rem"] > 0:
                px = mark_close(code, lot["end"])
                if px is None:
                    return None
                proceeds += lot["rem"] * px
        return (proceeds - buy_amt) / buy_amt * 100.0, buy_amt

    hold_rows = []
    results = {n: [] for n in range(1, 11)}
    for sel, code in above_b_keys:
        for n in range(1, 11):
            r = pnl(sel, code, n)
            if r is None:
                continue
            results[n].append((sel, r[0], r[1]))
    m10 = None
    for n in range(1, 11):
        rows = results[n]
        arr = np.array([x[1] for x in rows])
        w = np.array([x[2] for x in rows])
        by = defaultdict(list)
        for sel, ret, _a in rows:
            by[sel].append(ret)
        day_means = [float(np.mean(v)) for v in by.values()]
        mean = float(arr.mean()) if len(arr) else np.nan
        if n == 10:
            m10 = mean
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
                "vs10": None if m10 is None else round(mean - m10, 4),
            }
        )
    # fill vs10
    m10 = hold_rows[9]["mean"]
    for row in hold_rows:
        row["vs10"] = round(row["mean"] - m10, 4)
    out["sell_hold"] = hold_rows
    return out


def main():
    print("prepare...", flush=True)
    df, above, _cache = prepare()
    print("n all", len(df), "above", int(above["above_ma5"].sum() if "above_ma5" in above.columns else len(above)), flush=True)
    print("analyze...", flush=True)
    out, bmask = analyze(df, above)
    above_b = above[bmask]
    keys = list(zip(above_b["sel"], above_b["code"]))
    print("B n", len(keys), flush=True)
    print("sell hold...", flush=True)
    out = sell_hold_analysis(keys, out)

    # print key
    print("\n=== OVERVIEW ===")
    for k in ("all", "above", "below", "B", "notB"):
        print(k, out[k])
    print("daily_fund_sum", out["daily_fund_sum"])
    print("\n=== SELL HOLD ===")
    for r in out["sell_hold"]:
        print(r)
    print("\n=== B DAILY (head) ===")
    for r in out["B_daily"][:5]:
        print(r)

    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("wrote", OUT_JSON, flush=True)


if __name__ == "__main__":
    main()
