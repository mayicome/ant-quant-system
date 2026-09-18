# -*- coding: utf-8 -*-
"""在 最终/ 样本上评估 有大涨+排名"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PATH = (
    ROOT
    / "history_data"
    / "马总选股逻辑"
    / "最终"
    / "各日选股收益汇总_日线-ma10-sell_half-单点_按票_已完成_收盘上MA10_latest.xlsx"
)
OUT = ROOT / "data" / "_tmp_mz_bigmove_rank_final.json"


def ab(s: pd.Series) -> pd.Series:
    return s.map(
        lambda x: True
        if x is True or str(x).strip().lower() in ("true", "1", "yes", "是", "真")
        else (
            False
            if x is False
            or str(x).strip().lower() in ("false", "0", "no", "否", "假", "nan", "")
            else np.nan
        )
    )


def met(r: pd.Series) -> dict:
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


def show(name: str, r: pd.Series, base_mean: float) -> dict:
    m = met(r)
    if m["n"]:
        m["delta_mean"] = round(m["mean"] - base_mean, 4)
        print(
            f"{name}: n={m['n']} 票均={m['mean']:+.3f}% 中位={m['median']:+.3f}% "
            f"胜率={m['win']:.1f}% Δ={m['delta_mean']:+.3f}"
        )
    else:
        print(name, "empty")
    m["name"] = name
    return m


def main() -> None:
    df = pd.read_excel(PATH)
    buy = pd.to_datetime(df.get("买入日"), errors="coerce")
    ret = pd.to_numeric(df["收益率pct"], errors="coerce")
    print("file", PATH.name)
    print("buy range", buy.min(), "->", buy.max(), "rows", len(df))

    base = met(ret)
    print("BASE", base)
    bm = base["mean"]

    big = ab(df["条件_前10日无大涨"]) == False  # 有大涨
    no_big = ab(df["条件_前10日无大涨"]) == True
    rank = ab(df["条件_行业或概念排名[5,38]"]) == True
    dabiao = ab(df["条件_行业或概念排名达标"]) == True
    meet = ab(df["满足条件"]) == True
    rk = pd.to_numeric(df.get("最佳板块排名"), errors="coerce")
    r5_10 = (rk >= 5) & (rk <= 10)
    r5_20 = (rk >= 5) & (rk <= 20)

    rows = [
        show("全池", ret, bm),
        show("有大涨", ret[big], bm),
        show("无大涨", ret[no_big], bm),
        show("排名[5,38]", ret[rank], bm),
        show("排名达标", ret[dabiao], bm),
        show("有大涨+排名[5,38]", ret[big & rank], bm),
        show("有大涨+排名达标", ret[big & dabiao], bm),
        show("有大涨+排名[5,38]+达标", ret[big & rank & dabiao], bm),
        show("有大涨+最佳排名5-10", ret[big & r5_10], bm),
        show("有大涨+最佳排名5-20", ret[big & r5_20], bm),
        show("满足条件", ret[meet], bm),
        show("不满足条件", ret[~meet.fillna(False)], bm),
    ]

    # by year on buy date
    years = {}
    df2 = df.copy()
    df2["_ret"] = ret
    df2["_y"] = buy.dt.year
    df2["_big"] = big
    df2["_rank"] = rank
    df2["_dabiao"] = dabiao
    for y, g in df2.dropna(subset=["_y"]).groupby("_y"):
        y = int(y)
        bmean = float(g["_ret"].mean())
        print(f"\n=== {y} base mean={bmean:.3f} n={len(g)} ===")
        years[str(y)] = {
            "base": met(g["_ret"]),
            "big_rank538": met(g.loc[g["_big"] & g["_rank"], "_ret"]),
            "big_dabiao": met(g.loc[g["_big"] & g["_dabiao"], "_ret"]),
            "big_both": met(g.loc[g["_big"] & g["_rank"] & g["_dabiao"], "_ret"]),
            "meet": met(g.loc[ab(g["满足条件"]) == True, "_ret"]),
        }
        for k, v in years[str(y)].items():
            if v.get("n"):
                print(f"  {k}: n={v['n']} mean={v['mean']:+.3f} win={v['win']:.1f}")

    out = {
        "source": str(PATH),
        "buy_min": str(buy.min()),
        "buy_max": str(buy.max()),
        "base": base,
        "rows": rows,
        "by_year": years,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
