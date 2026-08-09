# -*- coding: utf-8 -*-
"""Compare Cond123 回踩 anytag vs besttest + overlap; brief vs open-clip Cond123."""
from __future__ import annotations

import pandas as pd
from pathlib import Path

P = Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门")

PREFIX = (
    "回踩_条件一_无涨停_均线差0.5to2_"
    "条件二成交相对MA5满足0to2_"
    "条件三MA5lt10lt20"
)
SUM_A = P / f"{PREFIX}_各日选股收益汇总.xlsx"
SUM_B = P / f"besttest_{PREFIX}_各日选股收益汇总.xlsx"
OPEN_A = P / (
    "开盘夹档_条件一_无涨停_均线差0.5to2_"
    "条件二开盘相对MA5满足0to2_"
    "条件三MA5lt10lt20_各日选股收益汇总.xlsx"
)
OPEN_B = P / (
    "besttest_开盘夹档_条件一_无涨停_均线差0.5to2_"
    "条件二开盘相对MA5满足0to2_"
    "条件三MA5lt10lt20_各日选股收益汇总.xlsx"
)
OUT = P / "条件一二三_回踩_anytag_vs_besttest对比.xlsx"


def norm_code(s: pd.Series) -> pd.Series:
    out = []
    for x in s.astype(str).str.strip():
        if x.endswith(".0"):
            x = x[:-2]
        out.append(x.zfill(6) if x.isdigit() else x)
    return pd.Series(out, index=s.index)


def prep(df: pd.DataFrame, tag: str) -> pd.DataFrame:
    d = df.copy()
    d["代码_n"] = norm_code(d["代码"])
    d["选股日"] = pd.to_datetime(d["选股日"]).dt.strftime("%Y-%m-%d")
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


def main() -> None:
    da = prep(pd.read_excel(SUM_A), "anytag")
    db = prep(pd.read_excel(SUM_B), "besttest")
    ka, kb = set(da["key"]), set(db["key"])
    inter = ka & kb
    only_a, only_b = ka - kb, kb - ka

    rows = [
        stats(da["收益率pct"], "anytag_回踩_Cond123"),
        stats(db["收益率pct"], "besttest_回踩_Cond123"),
        stats(da.loc[da["key"].isin(inter), "收益率pct"], "overlap_anytag_ret"),
        stats(db.loc[db["key"].isin(inter), "收益率pct"], "overlap_besttest_ret"),
        stats(da.loc[da["key"].isin(only_a), "收益率pct"], "anytag_only"),
        stats(db.loc[db["key"].isin(only_b), "收益率pct"], "besttest_only"),
    ]

    # vs open-clip Cond123
    if OPEN_A.exists() and OPEN_B.exists():
        oa = prep(pd.read_excel(OPEN_A), "anytag_open")
        ob = prep(pd.read_excel(OPEN_B), "besttest_open")
        rows.extend(
            [
                stats(oa["收益率pct"], "anytag_开盘夹档_Cond123"),
                stats(ob["收益率pct"], "besttest_开盘夹档_Cond123"),
            ]
        )

    summary = pd.DataFrame(rows)
    overlap_info = pd.DataFrame(
        [
            {
                "anytag_n": len(ka),
                "besttest_n": len(kb),
                "overlap_n": len(inter),
                "anytag_only_n": len(only_a),
                "besttest_only_n": len(only_b),
                "overlap_pct_of_any": round(len(inter) / len(ka) * 100, 2) if ka else 0,
                "overlap_pct_of_best": round(len(inter) / len(kb) * 100, 2) if kb else 0,
                "Cond2_note": "回踩 Cond2=成交相对买入日MA5∈[0,2]%；开盘夹档 Cond2=开盘相对MA5",
            }
        ]
    )

    with pd.ExcelWriter(OUT, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="统计对比", index=False)
        overlap_info.to_excel(w, sheet_name="重叠", index=False)
        da.to_excel(w, sheet_name="anytag明细", index=False)
        db.to_excel(w, sheet_name="besttest明细", index=False)

    print("===== 回踩 Cond123 anytag vs besttest =====")
    print(summary.to_string(index=False))
    print()
    print(overlap_info.to_string(index=False))
    print(f"\n导出: {OUT}")


if __name__ == "__main__":
    main()
