# -*- coding: utf-8 -*-
"""补弱日/空仓：漏斗诊断 + 仅在空/弱日叠加补票方案。

基线：besttest Cond123 开盘夹档（各日选股收益汇总_新规则.xlsx，215笔）
补票候选：
  A) 空仓日叠加高开回踩（besttest 回踩成交）
  B) 空仓+弱日(n<=5)叠加高开回踩
  C) 空仓日用 anytag 开盘夹档替代/并入
  D) 空仓+弱日并入 anytag 开盘夹档增量
  E) A+C 组合
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门")
OUT_XLSX = ROOT / "补弱日空仓_方案对比.xlsx"
OUT_JSON = ROOT / "_fill_weak_empty_days.json"

RET = "收益率pct"
DAY = "选股日"
CODE = "代码"
THIN_N = 5


def _day_s(s: pd.Series) -> pd.Series:
    return s.astype(str).str[:10]


def _code6(s: pd.Series) -> pd.Series:
    out = s.astype(str).str.strip()
    out = out.str.replace(r"\.SZ|\.SH|\.BJ", "", regex=True)
    out = out.str.extract(r"(\d+)", expand=False).fillna(out)
    return out.str.zfill(6)


def metrics(sub: pd.DataFrame, ret_col: str = RET) -> dict:
    if sub is None or len(sub) == 0:
        return dict(n=0, mean=None, med=None, win=None, days=0, empty_days=None)
    r = pd.to_numeric(sub[ret_col], errors="coerce").dropna()
    days = sub[DAY].nunique() if DAY in sub.columns else None
    return dict(
        n=int(len(r)),
        mean=float(r.mean()) if len(r) else None,
        med=float(r.median()) if len(r) else None,
        win=float((r > 0).mean() * 100) if len(r) else None,
        days=int(days) if days is not None else None,
    )


def load_openclip_best() -> pd.DataFrame:
    df = pd.read_excel(ROOT / "各日选股收益汇总_新规则.xlsx")
    df = df.copy()
    df[DAY] = _day_s(df[DAY])
    df[CODE] = _code6(df[CODE])
    df[RET] = pd.to_numeric(df[RET], errors="coerce")
    df["来源"] = "开盘夹档_besttest"
    return df


def load_openclip_any() -> pd.DataFrame:
    # detail_anytag in Cond12 file
    df = pd.read_excel(ROOT / "Cond12_MA空头_每日笔数.xlsx", sheet_name="detail_anytag")
    df = df.copy()
    df[DAY] = _day_s(df[DAY])
    df[CODE] = _code6(df[CODE])
    df[RET] = pd.to_numeric(df[RET], errors="coerce")
    df["来源"] = "开盘夹档_anytag"
    return df


def load_pullback_best_returns() -> pd.DataFrame:
    """Join besttest 回踩 buy/sell → per-trade return（卖出分笔按日+代码汇总金额）。"""
    buy = pd.read_csv(
        ROOT
        / "besttest_回踩_条件一_无涨停_均线差0.5to2_条件二成交相对MA5满足0to2_条件三MA5lt10lt20_回测成交明细_买入.csv",
        encoding="utf-8-sig",
    )
    sell = pd.read_csv(
        ROOT
        / "besttest_回踩_条件一_无涨停_均线差0.5to2_条件二成交相对MA5满足0to2_条件三MA5lt10lt20_回测成交明细_卖出.csv",
        encoding="utf-8-sig",
    )
    buy = buy.copy()
    sell = sell.copy()
    buy["_day"] = _day_s(buy["选股日"])
    buy["_code"] = _code6(buy["代码"])
    buy["_buy_amt"] = pd.to_numeric(buy["金额"], errors="coerce")
    buy["_buy_px"] = pd.to_numeric(buy["价格"], errors="coerce")
    sell["_code"] = _code6(sell["代码"])
    sell["_day"] = _day_s(sell["选股日"])
    sell["_sell_amt"] = pd.to_numeric(sell["金额"], errors="coerce")
    sell["_sell_px"] = pd.to_numeric(sell["价格"], errors="coerce")
    sell["_sell_qty"] = pd.to_numeric(sell["数量"], errors="coerce")
    sell["_notional"] = sell["_sell_px"] * sell["_sell_qty"]
    agg = sell.groupby(["_day", "_code"], as_index=False).agg(
        _sell_amt=("_sell_amt", "sum"),
        _sell_qty=("_sell_qty", "sum"),
        _notional=("_notional", "sum"),
    )
    agg["_sell_px"] = agg["_notional"] / agg["_sell_qty"].replace(0, np.nan)

    m = buy.merge(
        agg[["_day", "_code", "_sell_amt", "_sell_px"]], on=["_day", "_code"], how="left"
    )
    m[RET] = (m["_sell_amt"] - m["_buy_amt"]) / m["_buy_amt"] * 100.0
    m[DAY] = m["_day"]
    m[CODE] = m["_code"]
    m["来源"] = "高开回踩_besttest"
    m["分支"] = "高开回踩"
    return m[[DAY, CODE, "股票名称", RET, "来源", "分支", "_buy_amt"]].dropna(subset=[RET])


def load_sel_counts() -> pd.DataFrame:
    rows = []
    files = {
        "best无过滤": "选股结果_东财热门-besttest全量-无个股过滤_2026-07-01_2026-07-31.xls",
        "best有过滤": "选股结果_东财热门-besttest-无涨停均线差0.5to2-MA空头排列_2026-07-01_2026-07-31.xls",
        "any无过滤": "选股结果_东财热门-anytag全量-无个股过滤_2026-07-01_2026-07-31.xls",
    }
    series = {}
    for label, name in files.items():
        df = pd.read_excel(ROOT / name)
        dcol = "选股日" if "选股日" in df.columns else df.columns[0]
        g = _day_s(df[dcol]).value_counts().sort_index()
        series[label] = g
    all_days = sorted(set().union(*[set(s.index) for s in series.values()]))
    for d in all_days:
        rows.append(
            {
                "选股日": d,
                **{k: int(series[k].get(d, 0)) for k in series},
            }
        )
    return pd.DataFrame(rows)


def daily_table(base: pd.DataFrame, cal_days: list[str]) -> pd.DataFrame:
    rows = []
    for d in cal_days:
        sub = base[base[DAY] == d]
        rows.append(
            {
                "选股日": d,
                "n": int(len(sub)),
                "mean": float(sub[RET].mean()) if len(sub) else None,
            }
        )
    return pd.DataFrame(rows)


def merge_unique(base: pd.DataFrame, extra: pd.DataFrame) -> pd.DataFrame:
    """Append extras not already in base same day+code."""
    if extra is None or len(extra) == 0:
        return base.copy()
    b = base.copy()
    e = extra.copy()
    key_b = set(zip(b[DAY], b[CODE]))
    e = e[~e.apply(lambda r: (r[DAY], r[CODE]) in key_b, axis=1)]
    cols = [DAY, CODE, RET, "来源"]
    for c in ["股票名称", "分支"]:
        if c in b.columns and c in e.columns:
            cols.append(c)
    out = pd.concat([b[cols], e[[c for c in cols if c in e.columns]]], ignore_index=True)
    return out


def eval_plan(
    name: str,
    trades: pd.DataFrame,
    cal_days: list[str],
    empty0: set[str],
    thin0: set[str],
) -> dict:
    m = metrics(trades)
    daily = daily_table(trades, cal_days)
    empty_after = set(daily.loc[daily["n"] == 0, "选股日"])
    thin_after = set(daily.loc[daily["n"].between(1, THIN_N), "选股日"])
    filled_empty = sorted(empty0 - empty_after)
    # quality of trades on originally empty/thin days
    weakish = empty0 | thin0
    sub_w = trades[trades[DAY].isin(weakish)]
    sub_e = trades[trades[DAY].isin(empty0)]
    mw = metrics(sub_w)
    me = metrics(sub_e)
    return {
        "name": name,
        "n": m["n"],
        "mean": m["mean"],
        "med": m["med"],
        "win": m["win"],
        "days_with": int((daily["n"] > 0).sum()),
        "empty_days": int((daily["n"] == 0).sum()),
        "empty_filled": len(filled_empty),
        "filled_empty_days": filled_empty,
        "thin_days": int((daily["n"].between(1, THIN_N)).sum()),
        "weakish_n": mw["n"],
        "weakish_mean": mw["mean"],
        "empty_day_trades_n": me["n"],
        "empty_day_trades_mean": me["mean"],
        "daily": daily.replace({np.nan: None}).to_dict("records"),
    }


def main() -> None:
    base = load_openclip_best()
    any_oc = load_openclip_any()
    pb = load_pullback_best_returns()
    sel = load_sel_counts()

    dist = pd.read_excel(ROOT / "Cond12_MA空头_每日笔数.xlsx", sheet_name="by_选股日")
    cal_days = list(_day_s(dist["选股日"]))

    base_daily = daily_table(base, cal_days)
    empty0 = set(base_daily.loc[base_daily["n"] == 0, "选股日"])
    thin0 = set(base_daily.loc[base_daily["n"].between(1, THIN_N), "选股日"])

    print("empty0", sorted(empty0))
    print("thin0", sorted(thin0))
    print("pullback n", len(pb), "mean", pb[RET].mean())
    print("pullback on empty:")
    print(pb[pb[DAY].isin(empty0)].groupby(DAY)[RET].agg(["count", "mean"]))

    # anytag-only extras vs besttest openclip
    best_keys = set(zip(base[DAY], base[CODE]))
    any_extra = any_oc[~any_oc.apply(lambda r: (r[DAY], r[CODE]) in best_keys, axis=1)].copy()
    any_extra["来源"] = "开盘夹档_anytag增量"

    plans = []
    plans.append(eval_plan("基线:开盘夹档_besttest", base, cal_days, empty0, thin0))

    # A: empty days + pullback only those days
    pb_empty = pb[pb[DAY].isin(empty0)]
    plans.append(
        eval_plan(
            "A:空仓日+回踩",
            merge_unique(base, pb_empty),
            cal_days,
            empty0,
            thin0,
        )
    )

    # B: empty+thin + pullback
    pb_weak = pb[pb[DAY].isin(empty0 | thin0)]
    plans.append(
        eval_plan(
            "B:空仓+弱日+回踩",
            merge_unique(base, pb_weak),
            cal_days,
            empty0,
            thin0,
        )
    )

    # C: empty days + anytag openclip (replace emptiness)
    any_empty = any_oc[any_oc[DAY].isin(empty0)]
    plans.append(
        eval_plan(
            "C:空仓日+anytag开盘夹档",
            merge_unique(base, any_empty),
            cal_days,
            empty0,
            thin0,
        )
    )

    # D: empty+thin + anytag extras
    any_weak = any_extra[any_extra[DAY].isin(empty0 | thin0)]
    plans.append(
        eval_plan(
            "D:空仓+弱日+anytag增量",
            merge_unique(base, any_weak),
            cal_days,
            empty0,
            thin0,
        )
    )

    # E: empty + pullback + anytag
    plans.append(
        eval_plan(
            "E:空仓日+回踩+anytag",
            merge_unique(merge_unique(base, pb_empty), any_empty),
            cal_days,
            empty0,
            thin0,
        )
    )

    # F: always-on pullback everywhere (contrast — not asymmetric)
    plans.append(
        eval_plan(
            "对照:全日叠加回踩",
            merge_unique(base, pb),
            cal_days,
            empty0,
            thin0,
        )
    )

    # G: empty+thin pullback, but only if pullback ret available; also show pb-only metrics
    plans.append(
        eval_plan(
            "对照:仅回踩全集",
            pb.rename(columns={})[[DAY, CODE, RET]].assign(来源="回踩"),
            cal_days,
            empty0,
            thin0,
        )
    )

    # funnel join
    funnel = sel.merge(
        base_daily.rename(columns={"n": "开盘夹档_best_n", "mean": "开盘夹档_best_mean"}),
        on="选股日",
        how="outer",
    )
    any_daily = daily_table(any_oc, cal_days).rename(
        columns={"n": "开盘夹档_any_n", "mean": "开盘夹档_any_mean"}
    )
    pb_daily = daily_table(pb, cal_days).rename(
        columns={"n": "回踩_best_n", "mean": "回踩_best_mean"}
    )
    funnel = funnel.merge(any_daily, on="选股日", how="left").merge(
        pb_daily, on="选股日", how="left"
    )
    funnel["标记"] = funnel["选股日"].map(
        lambda d: "空仓" if d in empty0 else ("弱日" if d in thin0 else ("忙日" if int(base_daily.loc[base_daily["选股日"]==d, "n"].iloc[0] or 0) >= 15 else ""))
    )

    # detail supplements for A/B
    detail_a = pb_empty.copy()
    detail_b = pb_weak.copy()

    plan_df = pd.DataFrame([{k: v for k, v in p.items() if k != "daily"} for p in plans])

    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        plan_df.to_excel(w, sheet_name="方案对比", index=False)
        funnel.sort_values("选股日").to_excel(w, sheet_name="漏斗按日", index=False)
        detail_a.sort_values([DAY, RET], ascending=[True, False]).to_excel(
            w, sheet_name="A补票明细_空仓回踩", index=False
        )
        detail_b.sort_values([DAY, RET], ascending=[True, False]).to_excel(
            w, sheet_name="B补票明细_弱日回踩", index=False
        )
        any_empty.to_excel(w, sheet_name="C补票_空仓anytag", index=False)
        # per-plan daily for A/B/E
        sheet_i = 0
        for p in plans:
            if p["name"].startswith(("A:", "B:", "E:", "基线")):
                sheet_i += 1
                safe = "日分布_%d" % sheet_i
                pd.DataFrame(p["daily"]).to_excel(w, sheet_name=safe, index=False)

    out = {
        "empty0": sorted(empty0),
        "thin0": sorted(thin0),
        "plans": [{k: v for k, v in p.items() if k != "daily"} for p in plans],
        "funnel": funnel.replace({np.nan: None}).to_dict("records"),
        "pb_empty_metrics": metrics(pb_empty),
        "pb_weak_metrics": metrics(pb_weak),
    }
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT_XLSX)
    print(plan_df.to_string(index=False))
    print("\n--- funnel empty/thin ---")
    print(
        funnel[funnel["选股日"].isin(empty0 | thin0)]
        .sort_values("选股日")
        .to_string(index=False)
    )


if __name__ == "__main__":
    main()
