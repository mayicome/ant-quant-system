# -*- coding: utf-8 -*-
from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path

PATH = (
    Path(__file__).resolve().parents[1]
    / "history_data"
    / "马总选股逻辑"
    / "最终"
    / "各日选股收益汇总_日线-ma10-sell_half-单点_按票_已完成_收盘上MA10_latest.xlsx"
)


def ab(s: pd.Series) -> pd.Series:
    return s.map(
        lambda x: True
        if x is True or str(x).strip().lower() in ("true", "1", "是", "真")
        else (
            False
            if x is False or str(x).strip().lower() in ("false", "0", "否", "假", "")
            else np.nan
        )
    )


def met(r: pd.Series) -> dict:
    r = pd.to_numeric(r, errors="coerce").dropna()
    n = len(r)
    if not n:
        return {"n": 0}
    return {
        "n": n,
        "mean": float(r.mean()),
        "median": float(r.median()),
        "win": float((r > 0).mean() * 100),
        "sum": float(r.sum()),
    }


def show(name: str, mask, ret: pd.Series, base_mean: float) -> dict:
    m = met(ret[mask])
    if not m["n"]:
        print(name, "empty")
        return m
    d = m["mean"] - base_mean
    print(
        f"{name}: n={m['n']} 票均={m['mean']:+.3f}% 中位={m['median']:+.3f}% "
        f"胜率={m['win']:.1f}% Δ={d:+.3f}"
    )
    return m


def main() -> None:
    df = pd.read_excel(PATH)
    ret = pd.to_numeric(df["收益率pct"], errors="coerce")
    buy = pd.to_datetime(df["买入日"], errors="coerce")

    ma = ab(df["条件_收盘站上MA5且MA20"]) == True  # noqa: E712
    boll = ab(df["条件_收盘站上布林上轨"]) == True  # noqa: E712
    rank = ab(df["条件_行业或概念排名[5,38]"]) == True  # noqa: E712
    nobig = ab(df["条件_前10日无大涨"]) == True  # noqa: E712
    meet = ab(df["满足条件"]) == True  # noqa: E712
    dabiao = ab(df["条件_行业或概念排名达标"]) == True  # noqa: E712

    base = met(ret)
    bm = base["mean"]
    print(
        "样本",
        buy.min().date(),
        "->",
        buy.max().date(),
        "BASE n=%d mean=%+.3f win=%.1f" % (base["n"], base["mean"], base["win"]),
    )
    print()
    show("全池", ret.notna(), ret, bm)
    show("满足条件(含无大涨)", meet, ret, bm)
    show("去掉无大涨: MA∧布林∧[5,38]", ma & boll & rank, ret, bm)
    show("去掉无大涨: MA∧[5,38]", ma & rank, ret, bm)
    show("去掉无大涨: 布林∧[5,38]", boll & rank, ret, bm)
    show("仅[5,38]", rank, ret, bm)
    show("仅布林", boll, ret, bm)
    show("仅MA", ma, ret, bm)
    show("无大涨∧MA∧布林∧[5,38]", nobig & ma & boll & rank, ret, bm)
    show("有大涨∧MA∧布林∧[5,38]", (~nobig) & ma & boll & rank, ret, bm)
    show("去掉无大涨: MA∧布林∧达标", ma & boll & dabiao, ret, bm)
    show("去掉无大涨: MA∧布林∧[5,38]∧达标", ma & boll & rank & dabiao, ret, bm)

    m_meet = met(ret[meet])
    m_drop = met(ret[ma & boll & rank])
    print()
    print(
        "相对原满足: 去掉无大涨 票均差=%+.3f 胜率差=%+.2f"
        % (m_drop["mean"] - m_meet["mean"], m_drop["win"] - m_meet["win"])
    )
    print(
        "相对全池: 去掉无大涨 Δ=%+.3f | 原满足 Δ=%+.3f"
        % (m_drop["mean"] - bm, m_meet["mean"] - bm)
    )

    df["_y"] = buy.dt.year
    for y, g in df.groupby("_y"):
        r = pd.to_numeric(g["收益率pct"], errors="coerce")
        b = float(r.mean())
        mm = ab(g["条件_收盘站上MA5且MA20"]) == True  # noqa: E712
        bb = ab(g["条件_收盘站上布林上轨"]) == True  # noqa: E712
        rr = ab(g["条件_行业或概念排名[5,38]"]) == True  # noqa: E712
        mt = ab(g["满足条件"]) == True  # noqa: E712
        print(f"--- {int(y)} base={b:+.3f} ---")
        for name, mask in [("满足", mt), ("去无大涨", mm & bb & rr)]:
            m = met(r[mask])
            print(
                f"  {name}: n={m['n']} mean={m['mean']:+.3f} "
                f"win={m['win']:.1f} Δ={m['mean'] - b:+.3f}"
            )


if __name__ == "__main__":
    main()
