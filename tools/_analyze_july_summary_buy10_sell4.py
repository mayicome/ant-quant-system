# -*- coding: utf-8 -*-
"""July Ma Zong after-hours pool: buy window 10d / sell window 4d summary analysis."""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(r"d:/蚂蚁量化系统")
SRC = ROOT / "history_data" / "马总选股逻辑" / "各日选股收益汇总-7月.xlsx"
OUT = ROOT / "history_data" / "马总选股逻辑" / "_july_summary_buy10_sell4.json"


def _to_bool(x):
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    s = str(x).strip().lower()
    if s in ("true", "1", "yes", "是"):
        return True
    if s in ("false", "0", "no", "否", "nan", ""):
        return False
    return bool(x)


def _stats(s: pd.Series) -> dict:
    s = pd.to_numeric(s, errors="coerce").dropna()
    n = int(len(s))
    if n == 0:
        return {"n": 0}
    mean = float(s.mean())
    std = float(s.std(ddof=1)) if n > 1 else 0.0
    se = std / math.sqrt(n) if n > 0 else None
    ci = 1.96 * se if se is not None else None
    return {
        "n": n,
        "mean": round(mean, 4),
        "median": round(float(s.median()), 4),
        "std": round(std, 4),
        "win_rate": round(float((s > 0).mean()), 4),
        "p25": round(float(s.quantile(0.25)), 4),
        "p75": round(float(s.quantile(0.75)), 4),
        "ci95_lo": round(mean - ci, 4) if ci is not None else None,
        "ci95_hi": round(mean + ci, 4) if ci is not None else None,
        "sum_pnl_proxy": round(float(s.sum()), 2),  # equal-weight pct points
    }


def _trading_days_between(cal: list, a, b) -> int | None:
    if pd.isna(a) or pd.isna(b):
        return None
    a = pd.Timestamp(a).date()
    b = pd.Timestamp(b).date()
    if a not in cal or b not in cal:
        # fallback calendar distance by sorted unique dates present
        return None
    ia, ib = cal.index(a), cal.index(b)
    return int(ib - ia)


def main():
    df = pd.read_excel(SRC)
    df["满足条件"] = df["满足条件"].map(_to_bool)
    df["收益率pct"] = pd.to_numeric(df["收益率pct"], errors="coerce")
    df["选股日"] = pd.to_datetime(df["选股日"], errors="coerce")
    df["买入日"] = pd.to_datetime(df["买入日"], errors="coerce")
    df["end_date"] = pd.to_datetime(df["end_date"], errors="coerce")
    df["代码6"] = (
        df["代码"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    )

    # trading calendar from data
    cal = sorted(
        {
            d.date()
            for d in pd.concat([df["选股日"], df["买入日"], df["end_date"]]).dropna()
        }
    )
    cal_list = list(cal)

    def td(a, b):
        if pd.isna(a) or pd.isna(b):
            return np.nan
        a, b = pd.Timestamp(a).date(), pd.Timestamp(b).date()
        if a not in cal or b not in cal:
            return np.nan
        return cal_list.index(b) - cal_list.index(a)

    df["买入滞后交易日"] = [td(a, b) for a, b in zip(df["选股日"], df["买入日"])]
    df["持有交易日"] = [td(a, b) for a, b in zip(df["买入日"], df["end_date"])]
    df["选股至结束交易日"] = [td(a, b) for a, b in zip(df["选股日"], df["end_date"])]

    # buy/sell coverage
    has_buy = df["买入日"].notna() & (pd.to_numeric(df["买入金额合计"], errors="coerce") > 0)
    cleared = df["备注"].astype(str).str.contains("已清仓", na=False)

    out = {
        "source": str(SRC),
        "note": "买入窗口约10交易日、卖出/持有观察约4交易日（以表内 end_date 与买入日差为准）",
        "n_rows": int(len(df)),
        "n_selection_days": int(df["选股日"].nunique()),
        "selection_day_range": [
            str(df["选股日"].min().date()) if df["选股日"].notna().any() else None,
            str(df["选股日"].max().date()) if df["选股日"].notna().any() else None,
        ],
        "buy_lag_hist": df["买入滞后交易日"].value_counts(dropna=False).sort_index().to_dict(),
        "hold_days_hist": df["持有交易日"].value_counts(dropna=False).sort_index().to_dict(),
        "sel_to_end_hist": df["选股至结束交易日"].value_counts(dropna=False).sort_index().to_dict(),
        "has_buy_n": int(has_buy.sum()),
        "cleared_n": int(cleared.sum()),
        "meet_true_n": int(df["满足条件"].sum()),
        "meet_false_n": int((~df["满足条件"]).sum()),
    }
    # stringify hist keys
    for k in ("buy_lag_hist", "hold_days_hist", "sel_to_end_hist"):
        out[k] = {str(a): int(b) for a, b in out[k].items()}

    # overall returns
    out["all"] = _stats(df["收益率pct"])
    out["meet_true"] = _stats(df.loc[df["满足条件"], "收益率pct"])
    out["meet_false"] = _stats(df.loc[~df["满足条件"], "收益率pct"])
    out["has_buy"] = _stats(df.loc[has_buy, "收益率pct"])
    out["no_buy"] = _stats(df.loc[~has_buy, "收益率pct"])

    # by buy lag
    lag_rows = []
    for lag, g in df.groupby(df["买入滞后交易日"], dropna=False):
        st = _stats(g["收益率pct"])
        st["buy_lag"] = None if pd.isna(lag) else int(lag)
        st["meet_true_n"] = int(g["满足条件"].sum())
        lag_rows.append(st)
    out["by_buy_lag"] = sorted(lag_rows, key=lambda x: (x["buy_lag"] is None, x["buy_lag"] or 0))

    # by hold days (buy->end)
    hold_rows = []
    for h, g in df.groupby(df["持有交易日"], dropna=False):
        st = _stats(g["收益率pct"])
        st["hold_days"] = None if pd.isna(h) else int(h)
        hold_rows.append(st)
    out["by_hold_days"] = sorted(hold_rows, key=lambda x: (x["hold_days"] is None, x["hold_days"] or 0))

    # daily equal-weight mean return
    daily = []
    for d, g in df.groupby(df["选股日"].dt.date):
        st = _stats(g["收益率pct"])
        st["date"] = str(d)
        st["meet_true_n"] = int(g["满足条件"].sum())
        st["n_all"] = int(len(g))
        daily.append(st)
    out["by_selection_day"] = daily

    # condition flags among bought
    cond_cols = [
        "条件_当日涨停",
        "条件_行业或概念前10",
        "条件_主力净流入>=3000万",
        "条件_前10日无大涨",
        "条件_收盘站上MA5且MA20",
    ]
    cond_stats = []
    for c in cond_cols:
        if c not in df.columns:
            continue
        flag = df[c].map(_to_bool)
        for val, label in ((True, "True"), (False, "False")):
            st = _stats(df.loc[flag == val, "收益率pct"])
            st["condition"] = c
            st["value"] = label
            cond_stats.append(st)
    out["by_soft_condition"] = cond_stats

    # board rank buckets (best rank)
    rk = pd.to_numeric(df["最佳板块排名"], errors="coerce")
    buckets = [
        ("1-3", (rk >= 1) & (rk <= 3)),
        ("4-10", (rk >= 4) & (rk <= 10)),
        ("11-30", (rk >= 11) & (rk <= 30)),
        ("无排名", rk.isna()),
    ]
    br = []
    for name, m in buckets:
        st = _stats(df.loc[m, "收益率pct"])
        st["bucket"] = name
        br.append(st)
    out["by_best_board_rank"] = br

    # inflow ok
    inf = df["条件_主力净流入>=3000万"].map(_to_bool)
    out["inflow_ok"] = _stats(df.loc[inf, "收益率pct"])
    out["inflow_fail"] = _stats(df.loc[~inf, "收益率pct"])

    # meet_true detail: still check if any edge
    mt = df[df["满足条件"]]
    out["meet_true_by_day"] = []
    for d, g in mt.groupby(mt["选股日"].dt.date):
        st = _stats(g["收益率pct"])
        st["date"] = str(d)
        out["meet_true_by_day"].append(st)

    # top/bottom selection days
    day_means = pd.DataFrame(daily)
    if not day_means.empty and "mean" in day_means.columns:
        day_means = day_means.dropna(subset=["mean"]).sort_values("mean")
        out["worst_days"] = day_means.head(5)[["date", "n", "mean", "win_rate", "meet_true_n"]].to_dict("records")
        out["best_days"] = day_means.tail(5).iloc[::-1][["date", "n", "mean", "win_rate", "meet_true_n"]].to_dict("records")

    # equal-weight portfolio path: average daily mean
    if daily:
        means = [d["mean"] for d in daily if d.get("n", 0) > 0 and d.get("mean") is not None]
        out["daily_mean_of_means"] = round(float(np.mean(means)), 4) if means else None
        out["daily_mean_win_days"] = round(float(np.mean([1 if m > 0 else 0 for m in means])), 4) if means else None

    # money-weighted approx using 净现金流 and buy amount
    buy_amt = pd.to_numeric(df["买入金额合计"], errors="coerce")
    ret = df["收益率pct"] / 100.0
    w = buy_amt.fillna(0)
    mask = (w > 0) & ret.notna()
    if mask.any():
        wret = float((w[mask] * ret[mask]).sum() / w[mask].sum())
        out["value_weighted_return_pct"] = round(wret * 100, 4)
    else:
        out["value_weighted_return_pct"] = None

    # cleared vs not
    out["cleared"] = _stats(df.loc[cleared, "收益率pct"])
    out["not_cleared"] = _stats(df.loc[~cleared, "收益率pct"])

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in (
        "n_rows", "n_selection_days", "selection_day_range",
        "all", "meet_true", "meet_false", "has_buy",
        "buy_lag_hist", "hold_days_hist", "daily_mean_of_means",
        "value_weighted_return_pct", "worst_days", "best_days",
    )}, ensure_ascii=False, indent=2))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
