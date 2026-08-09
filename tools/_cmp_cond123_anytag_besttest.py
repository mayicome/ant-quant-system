# -*- coding: utf-8 -*-
"""Compare Cond1+2+3 anytag vs besttest exports. Does not modify source trade CSVs."""
from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path

P = Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门")

BUY_A = P / "开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_条件三MA5lt10lt20_回测成交明细_买入.csv"
BUY_B = P / "besttest_开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_条件三MA5lt10lt20_回测成交明细_买入.csv"
SELL_A = P / "开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_条件三MA5lt10lt20_回测成交明细_卖出.csv"
SELL_B = P / "besttest_开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_条件三MA5lt10lt20_回测成交明细_卖出.csv"
SUM_A = P / "开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_条件三MA5lt10lt20_各日选股收益汇总.xlsx"
SUM_B = P / "besttest_开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_条件三MA5lt10lt20_各日选股收益汇总.xlsx"
OUT = P / "条件一二三_anytag_vs_besttest对比.xlsx"


def norm_code(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip()
    out = []
    for x in s:
        if x.endswith(".0"):
            x = x[:-2]
        if x.isdigit():
            out.append(x.zfill(6))
        else:
            out.append(x)
    return pd.Series(out, index=s.index)


def prep(df: pd.DataFrame, tag: str) -> pd.DataFrame:
    d = df.copy()
    d["代码_n"] = norm_code(d["代码"])
    d["选股日"] = pd.to_datetime(d["选股日"]).dt.strftime("%Y-%m-%d")
    if "买入日" in d.columns:
        d["买入日"] = pd.to_datetime(d["买入日"]).dt.strftime("%Y-%m-%d")
    d["key"] = d["选股日"] + "|" + d["代码_n"]
    d["src"] = tag
    d["收益率pct"] = pd.to_numeric(d["收益率pct"], errors="coerce")
    return d


def stats(ret: pd.Series, name: str) -> dict:
    ret = pd.to_numeric(ret, errors="coerce").dropna()
    n = len(ret)
    if n == 0:
        return {"set": name, "n": 0}
    return {
        "set": name,
        "n": n,
        "mean_ret": float(ret.mean()),
        "median_ret": float(ret.median()),
        "winrate": float((ret > 0).mean() * 100),
        "sum_ret": float(ret.sum()),
        "pos": int((ret > 0).sum()),
        "neg": int((ret <= 0).sum()),
        "p25": float(ret.quantile(0.25)),
        "p75": float(ret.quantile(0.75)),
    }


def max_dd_by_day(df: pd.DataFrame) -> tuple[float, pd.DataFrame]:
    g = df.groupby("选股日", as_index=False)["收益率pct"].mean().sort_values("选股日")
    if g.empty:
        return float("nan"), g
    cum = g["收益率pct"].cumsum()
    peak = cum.cummax()
    dd = cum - peak
    return float(dd.min()), g.assign(cum_mean_ret=cum, dd=dd)


def main() -> None:
    buy_a = pd.read_csv(BUY_A, encoding="utf-8-sig")
    buy_b = pd.read_csv(BUY_B, encoding="utf-8-sig")
    sell_a = pd.read_csv(SELL_A, encoding="utf-8-sig")
    sell_b = pd.read_csv(SELL_B, encoding="utf-8-sig")
    da = prep(pd.read_excel(SUM_A), "anytag")
    db = prep(pd.read_excel(SUM_B), "besttest")

    sa = stats(da["收益率pct"], "anytag")
    sb = stats(db["收益率pct"], "besttest")
    dda, _ = max_dd_by_day(da)
    ddb, _ = max_dd_by_day(db)
    sa["maxDD_day_mean_eq"] = dda
    sb["maxDD_day_mean_eq"] = ddb
    sa["n_buys_csv"] = len(buy_a)
    sb["n_buys_csv"] = len(buy_b)
    sa["n_sells_csv"] = len(sell_a)
    sb["n_sells_csv"] = len(sell_b)
    sa["n_sel_days"] = int(da["选股日"].nunique())
    sb["n_sel_days"] = int(db["选股日"].nunique())

    keys_a = set(da["key"])
    keys_b = set(db["key"])
    both = keys_a & keys_b
    only_a = keys_a - keys_b
    only_b = keys_b - keys_a

    da_i = da.set_index("key")
    db_i = db.set_index("key")

    overlap_a = da_i.loc[
        sorted(both), ["股票名称", "选股日", "代码_n", "收益率pct", "买入日", "买入成交价"]
    ].rename(
        columns={
            "收益率pct": "ret_anytag",
            "买入成交价": "px_anytag",
            "买入日": "buyday_anytag",
        }
    )
    overlap_b = db_i.loc[sorted(both), ["收益率pct", "买入日", "买入成交价"]].rename(
        columns={
            "收益率pct": "ret_besttest",
            "买入成交价": "px_besttest",
            "买入日": "buyday_besttest",
        }
    )
    overlap = overlap_a.join(overlap_b)
    overlap["ret_diff"] = overlap["ret_anytag"] - overlap["ret_besttest"]
    overlap["ret_same"] = np.isclose(
        overlap["ret_anytag"].to_numpy(dtype=float),
        overlap["ret_besttest"].to_numpy(dtype=float),
        rtol=0,
        atol=1e-6,
        equal_nan=True,
    )
    overlap["px_same"] = np.isclose(
        pd.to_numeric(overlap["px_anytag"], errors="coerce").to_numpy(dtype=float),
        pd.to_numeric(overlap["px_besttest"], errors="coerce").to_numpy(dtype=float),
        rtol=0,
        atol=1e-4,
        equal_nan=True,
    )

    n_same_ret = int(overlap["ret_same"].sum())
    n_diff_ret = int((~overlap["ret_same"]).sum())
    max_abs_diff = (
        float(overlap.loc[~overlap["ret_same"], "ret_diff"].abs().max())
        if n_diff_ret
        else 0.0
    )

    s_both = stats(overlap["ret_anytag"], "overlap(anytag ret)")
    s_both_b = stats(overlap["ret_besttest"], "overlap(besttest ret)")
    s_only_a = stats(
        da_i.loc[sorted(only_a), "收益率pct"] if only_a else pd.Series(dtype=float),
        "only_anytag",
    )
    s_only_b = stats(
        db_i.loc[sorted(only_b), "收益率pct"] if only_b else pd.Series(dtype=float),
        "only_besttest",
    )

    cnt_a = da.groupby("选股日").size().rename("n_anytag")
    cnt_b = db.groupby("选股日").size().rename("n_besttest")
    mean_a = da.groupby("选股日")["收益率pct"].mean().rename("mean_ret_anytag")
    mean_b = db.groupby("选股日")["收益率pct"].mean().rename("mean_ret_besttest")
    sum_a = da.groupby("选股日")["收益率pct"].sum().rename("sum_ret_anytag")
    sum_b = db.groupby("选股日")["收益率pct"].sum().rename("sum_ret_besttest")
    daily = pd.concat([cnt_a, cnt_b, mean_a, mean_b, sum_a, sum_b], axis=1).fillna(0)
    daily["n_anytag"] = daily["n_anytag"].astype(int)
    daily["n_besttest"] = daily["n_besttest"].astype(int)
    daily["n_diff"] = daily["n_anytag"] - daily["n_besttest"]
    daily["mean_ret_diff"] = daily["mean_ret_anytag"] - daily["mean_ret_besttest"]
    daily = daily.reset_index().sort_values("选股日")

    cond12_note = ""
    try:
        c12 = pd.read_excel(P / "Cond12_MA空头_每日笔数.xlsx")
        cond12_note = f"Cond12_MA空头_每日笔数 cols={list(c12.columns)[:12]}, n={len(c12)}"
    except Exception as e:  # noqa: BLE001
        cond12_note = f"Cond12 read fail: {e}"

    only_a_df = (
        da_i.loc[sorted(only_a)]
        .reset_index()[["选股日", "代码_n", "股票名称", "买入日", "收益率pct", "买入成交价"]]
        .sort_values(["选股日", "代码_n"])
    )
    only_b_df = (
        db_i.loc[sorted(only_b)]
        .reset_index()[["选股日", "代码_n", "股票名称", "买入日", "收益率pct", "买入成交价"]]
        .sort_values(["选股日", "代码_n"])
        if only_b
        else pd.DataFrame()
    )

    overall = pd.DataFrame([sa, sb, s_both, s_both_b, s_only_a, s_only_b])

    with pd.ExcelWriter(OUT, engine="openpyxl") as w:
        overall.to_excel(w, sheet_name="总体与分层", index=False)
        daily.to_excel(w, sheet_name="每日对比", index=False)
        overlap.reset_index().to_excel(w, sheet_name="重叠交易", index=False)
        only_a_df.to_excel(w, sheet_name="仅anytag", index=False)
        if len(only_b_df):
            only_b_df.to_excel(w, sheet_name="仅besttest", index=False)
        pd.DataFrame(
            [
                {
                    "both_n": len(both),
                    "only_anytag_n": len(only_a),
                    "only_besttest_n": len(only_b),
                    "overlap_ret_identical_n": n_same_ret,
                    "overlap_ret_diff_n": n_diff_ret,
                    "overlap_max_abs_ret_diff": max_abs_diff,
                    "overlap_px_identical_n": int(overlap["px_same"].sum()),
                    "anytag_n_summary": len(da),
                    "besttest_n_summary": len(db),
                    "anytag_buys_csv": len(buy_a),
                    "besttest_buys_csv": len(buy_b),
                    "anytag_sells_csv": len(sell_a),
                    "besttest_sells_csv": len(sell_b),
                    "cond12_note": cond12_note,
                }
            ]
        ).to_excel(w, sheet_name="重叠摘要", index=False)

    mean_diff = sa["mean_ret"] - sb["mean_ret"]
    wr_diff = sa["winrate"] - sb["winrate"]

    print("=" * 60)
    print("条件一二三 Cond1+2+3：anytag vs besttest")
    print("=" * 60)
    print("\n[1] Overall（各日选股收益汇总 收益率pct，等权）")
    for s in (sa, sb):
        print(
            f"  {s['set']}: buys_csv={s['n_buys_csv']} sells_csv={s['n_sells_csv']} "
            f"trades={s['n']} days={s['n_sel_days']}"
        )
        print(
            f"    mean={s['mean_ret']:.3f}%  median={s['median_ret']:.3f}%  "
            f"winrate={s['winrate']:.1f}%  sum={s['sum_ret']:.2f}pp"
        )
        print(
            f"    maxDD(日均收益累加权益)={s['maxDD_day_mean_eq']:.2f}pp  "
            f"p25/p75={s['p25']:.2f}/{s['p75']:.2f}"
        )

    print("\n[2] Overlap（选股日+代码）")
    print(f"  both={len(both)}  only_anytag={len(only_a)}  only_besttest={len(only_b)}")
    print(
        f"  overlap returns identical: {n_same_ret}/{len(both)}  differ={n_diff_ret}  "
        f"max|Δret|={max_abs_diff:.6f}"
    )
    print(f"  overlap buy px identical: {int(overlap['px_same'].sum())}/{len(both)}")
    if n_diff_ret:
        print("  differing sample:")
        print(
            overlap.loc[
                ~overlap["ret_same"],
                ["选股日", "代码_n", "股票名称", "ret_anytag", "ret_besttest", "ret_diff"],
            ]
            .head(10)
            .to_string(index=False)
        )

    print("\n[3] Daily (选股日) side-by-side")
    print(daily.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print(
        f"  日均笔数 anytag={daily['n_anytag'].mean():.2f} "
        f"besttest={daily['n_besttest'].mean():.2f}"
    )
    print(
        f"  日均收益均值 anytag={daily['mean_ret_anytag'].mean():.3f} "
        f"besttest={daily['mean_ret_besttest'].mean():.3f}"
    )
    print(f"  {cond12_note}")

    print("\n[4] Extra segments")
    for s in (s_both, s_only_a, s_only_b):
        if s.get("n", 0) == 0:
            print(f"  {s['set']}: n=0")
            continue
        print(
            f"  {s['set']}: n={s['n']} mean={s['mean_ret']:.3f}% "
            f"median={s['median_ret']:.3f}% winrate={s['winrate']:.1f}% "
            f"sum={s['sum_ret']:.2f}pp"
        )

    print(
        f"\n  anytag extra (~{len(only_a)}) vs overlap: "
        f"mean {s_only_a.get('mean_ret', float('nan')):.3f}% vs "
        f"{s_both.get('mean_ret', float('nan')):.3f}%"
    )
    print(f"  besttest-only: n={len(only_b)}")
    if only_a and s_both.get("n", 0):
        helping = s_only_a["mean_ret"] >= s_both["mean_ret"]
        print(
            f"  extra helping? mean "
            f"{'YES (>= overlap)' if helping else 'NO (< overlap)'}; "
            f"winrate {s_only_a['winrate']:.1f}% vs overlap {s_both['winrate']:.1f}%"
        )

    print("\n[5] Verdict")
    print(
        f"  Δmean (anytag-besttest) = {mean_diff:+.3f}pp  "
        f"Δwinrate = {wr_diff:+.1f}pp  Δn = {sa['n'] - sb['n']:+d}"
    )
    overlap_mean = s_both.get("mean_ret", float("nan"))
    if abs(mean_diff) < 0.15 and abs(wr_diff) < 2:
        material = "差异不大（均值/胜率接近）"
    elif mean_diff > 0.15 and s_only_a.get("mean_ret", -999) >= overlap_mean - 0.2:
        material = "anytag 略优且增量尚可"
    elif mean_diff < -0.15:
        material = "besttest 质量略优（或 anytag 增量拖累）"
    else:
        material = "有差异但需结合容量偏好"

    if (
        s_only_a.get("n", 0)
        and s_only_a["mean_ret"] < s_both["mean_ret"] - 0.3
        and mean_diff <= 0
    ):
        live = "实盘更偏 besttest（质量优先：增量交易均值更差，整体不更好）"
    elif (
        s_only_a.get("n", 0)
        and s_only_a["mean_ret"] >= s_both["mean_ret"]
        and mean_diff > 0
    ):
        live = "实盘可偏 anytag（增量不拖累且整体略好，量更大）"
    elif abs(mean_diff) < 0.15:
        live = "实盘两者接近；若要控笔数/执行成本用 besttest，若要覆盖面用 anytag"
    else:
        live = "按均值/胜率择优，并看增量段是否拖累"

    print(f"  {material}")
    print(f"  {live}")
    print(f"\nWrote: {OUT}")


if __name__ == "__main__":
    main()
