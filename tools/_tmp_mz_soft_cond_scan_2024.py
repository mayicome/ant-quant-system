# -*- coding: utf-8 -*-
"""扫描软条件：相对 2024 全池，哪些 True 子集票均/胜率更好"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "_tmp_mz_soft_cond_scan_2024.json"

PATH = (
    ROOT
    / "history_data"
    / "马总选股逻辑"
    / "各日选股收益汇总_日线-ma10-sell_half-单点_按票_已完成_收盘上MA10_latest.xlsx"
)

SOFT = [
    "条件_前10日无大涨",
    "条件_收盘站上MA5且MA20",
    "条件_收盘站上布林上轨",
    "条件_行业或概念排名[5,38]",
    "条件_行业或概念中热[9,32]",
    "条件_行业或概念排名达标",
    "条件_行业前N",
    "条件_概念前N",
    "条件_流通市值<80亿",
    "条件_当日涨停",
    "真突破①通过",
    "真突破②通过",
    "真突破③通过",
    "满足条件",
]

CORE_MEET = [
    "条件_前10日无大涨",
    "条件_收盘站上MA5且MA20",
    "条件_收盘站上布林上轨",
    "条件_行业或概念排名[5,38]",
]


def as_bool(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s
    return s.map(
        lambda x: True
        if x is True or str(x).strip().lower() in ("true", "1", "yes", "是", "真")
        else (
            False
            if x is False or str(x).strip().lower() in ("false", "0", "no", "否", "假", "nan", "")
            else np.nan
        )
    )


def metrics(r: pd.Series) -> dict:
    r = pd.to_numeric(r, errors="coerce").dropna()
    n = len(r)
    if n == 0:
        return {"n": 0}
    return {
        "n": n,
        "mean": round(float(r.mean()), 4),
        "median": round(float(r.median()), 4),
        "win": round(float((r > 0).mean() * 100), 2),
        "sum": round(float(r.sum()), 2),
    }


def main() -> None:
    df = pd.read_excel(PATH)
    df["买入日"] = pd.to_datetime(df.get("买入日", df.get("选股日")), errors="coerce")
    # 开买日优先；无则用选股日+推断
    if "买入日" in df.columns:
        start = df["买入日"]
    else:
        start = pd.to_datetime(df["选股日"], errors="coerce")
    m = (start >= "2024-01-01") & (start <= "2024-12-31")
    d = df.loc[m].copy()
    ret = pd.to_numeric(d["收益率pct"], errors="coerce")
    base = metrics(ret)
    print("BASE", base)

    singles = []
    for col in SOFT:
        if col not in d.columns:
            print("missing", col)
            continue
        b = as_bool(d[col])
        for flag, lab in ((True, "True"), (False, "False")):
            sub = ret[b == flag]
            met = metrics(sub)
            if met["n"] < 20:
                continue
            delta = met["mean"] - base["mean"]
            row = {
                "cond": col,
                "value": lab,
                **met,
                "delta_mean": round(delta, 4),
                "delta_win": round(met["win"] - base["win"], 2),
                "better": delta > 0.15 and met["win"] >= base["win"] - 1,  # soft heuristic
            }
            singles.append(row)
            print(
                f"{col}={lab}: n={met['n']} mean={met['mean']:+.3f} "
                f"win={met['win']:.1f} d_mean={delta:+.3f}"
            )

    # leave-one-out of meet AND vs leave-one-out OR
    print("\n=== meet 四件套：全 AND / 去掉一项 ===")
    combos = []
    for drop in [None] + CORE_MEET:
        used = [c for c in CORE_MEET if c != drop]
        mask = pd.Series(True, index=d.index)
        for c in used:
            mask &= as_bool(d[c]) == True  # noqa: E712
        met = metrics(ret[mask])
        label = "AND全部" if drop is None else f"AND去掉[{drop}]"
        row = {
            "combo": label,
            "parts": used,
            **met,
            "delta_mean": round(met["mean"] - base["mean"], 4) if met["n"] else None,
            "delta_win": round(met["win"] - base["win"], 2) if met["n"] else None,
        }
        combos.append(row)
        print(label, row)

    # 单项 True 且明显好于基线的集合上，扫 2～3 元组合
    promising = []
    for col in SOFT:
        if col not in d.columns or col == "满足条件":
            continue
        b = as_bool(d[col])
        met = metrics(ret[b == True])  # noqa: E712
        if met.get("n", 0) >= 30 and met["mean"] > base["mean"] + 0.2:
            promising.append(col)
    print("\npromising singles", promising)

    pair_rows = []
    for r in range(1, 4):
        for parts in itertools.combinations(promising, r):
            mask = pd.Series(True, index=d.index)
            for c in parts:
                mask &= as_bool(d[c]) == True  # noqa: E712
            met = metrics(ret[mask])
            if met["n"] < 30:
                continue
            delta = met["mean"] - base["mean"]
            if delta <= 0.15:
                continue
            pair_rows.append(
                {
                    "parts": list(parts),
                    **met,
                    "delta_mean": round(delta, 4),
                    "delta_win": round(met["win"] - base["win"], 2),
                }
            )
    pair_rows.sort(key=lambda x: (-x["delta_mean"], -x["n"]))
    print("\n=== 有希望组合 top ===")
    for row in pair_rows[:25]:
        print(row)

    # 也扫：仅「否定」某些坏条件（取 False）
    print("\n=== 取 False 更好？ ===")
    false_better = []
    for col in SOFT:
        if col not in d.columns:
            continue
        b = as_bool(d[col])
        mt = metrics(ret[b == True])  # noqa: E712
        mf = metrics(ret[b == False])  # noqa: E712
        if mf.get("n", 0) < 30 or mt.get("n", 0) < 30:
            continue
        if mf["mean"] > mt["mean"] + 0.3 and mf["mean"] > base["mean"] + 0.15:
            false_better.append(
                {
                    "cond": col,
                    "true": mt,
                    "false": mf,
                    "prefer": "False",
                    "lift_vs_base": round(mf["mean"] - base["mean"], 4),
                }
            )
            print(f"prefer False {col}: F mean={mf['mean']} T mean={mt['mean']}")

    # 排名区间变体：用最佳板块排名数值切分（若有）
    rank_col = None
    for c in ("最佳板块排名", "所属行业最高排名名次", "所属概念最高排名名次"):
        if c in d.columns:
            rank_col = c
            break
    rank_bins = []
    if "最佳板块排名" in d.columns:
        rk = pd.to_numeric(d["最佳板块排名"], errors="coerce")
        for lo, hi, name in [
            (1, 4, "rank1-4"),
            (5, 10, "rank5-10"),
            (11, 20, "rank11-20"),
            (21, 38, "rank21-38"),
            (39, 80, "rank39-80"),
            (81, 999, "rank81+"),
        ]:
            sub = ret[(rk >= lo) & (rk <= hi)]
            met = metrics(sub)
            if met["n"] >= 30:
                rank_bins.append({"bin": name, **met, "delta_mean": round(met["mean"] - base["mean"], 4)})
                print("rank", name, met)

    # mv
    mv_bins = []
    if "流通市值_亿" in d.columns or "流通市值_亿_选股日" in d.columns:
        mvcol = "流通市值_亿_选股日" if "流通市值_亿_选股日" in d.columns else "流通市值_亿"
        mv = pd.to_numeric(d[mvcol], errors="coerce")
        for lo, hi, name in [
            (0, 30, "mv<30"),
            (30, 50, "mv30-50"),
            (50, 80, "mv50-80"),
            (80, 150, "mv80-150"),
            (150, 1e9, "mv>=150"),
        ]:
            sub = ret[(mv >= lo) & (mv < hi)]
            met = metrics(sub)
            if met["n"] >= 30:
                mv_bins.append({"bin": name, **met, "delta_mean": round(met["mean"] - base["mean"], 4)})
                print("mv", name, met)

    out = {
        "base": base,
        "singles": singles,
        "meet_and_variants": combos,
        "promising_cols": promising,
        "good_combos": pair_rows[:40],
        "prefer_false": false_better,
        "rank_bins": rank_bins,
        "mv_bins": mv_bins,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
