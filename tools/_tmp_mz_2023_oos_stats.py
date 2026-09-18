# -*- coding: utf-8 -*-
"""2023 OOS：全池 vs 排名[11,30]∧市值<40 等。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CANDS = [
    ROOT / "history_data" / "马总选股逻辑" / "2023",
    ROOT / "history_data" / "马总选股逻辑",
]


def find_ticket() -> Path:
    for base in CANDS:
        if not base.exists():
            continue
        hits = sorted(
            base.glob("*按票_已完成_收盘上MA10_latest.xlsx"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        # prefer file that looks like 2023 (smaller or selection overlap)
        for h in hits:
            return h
        hits = sorted(
            base.glob("*按票_已完成_收盘上MA10*.xlsx"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if hits:
            return hits[0]
    raise FileNotFoundError("no 2023 ticket xlsx")


def ab_true(s: pd.Series) -> pd.Series:
    return s.map(
        lambda x: True
        if x is True or str(x).strip().lower() in ("true", "1", "是", "真")
        else False
    )


def path_stats(sub: pd.DataFrame) -> dict:
    tmp = sub.copy()
    tmp["_d"] = tmp["_buy"].dt.normalize()
    day = tmp.groupby("_d", as_index=False)["_ret"].mean().sort_values("_d")
    if day.empty:
        return {}
    cum = day["_ret"].cumsum()
    dd = float((cum - cum.cummax()).min())
    return {
        "cum": float(cum.iloc[-1]),
        "max_dd": dd,
        "n_days": int(len(day)),
        "day_mean": float(day["_ret"].mean()),
        "day_win": float((day["_ret"] > 0).mean() * 100),
    }


def main() -> None:
    path = find_ticket()
    df = pd.read_excel(path)
    buy = pd.to_datetime(df.get("买入日"), errors="coerce")
    df = df.copy()
    df["_buy"] = buy
    df["_ret"] = pd.to_numeric(df["收益率pct"], errors="coerce")
    df["_sel"] = pd.to_datetime(df.get("选股日"), errors="coerce").dt.normalize()
    df["_y"] = buy.dt.year
    df["_m"] = buy.dt.to_period("M").astype(str)
    df = df.dropna(subset=["_buy", "_ret"])
    # keep 2023 only if mixed
    if (df["_y"] == 2023).any():
        df = df[df["_y"] == 2023].copy()

    rk = pd.to_numeric(df.get("最佳板块排名"), errors="coerce")
    mvcol = "流通市值_亿_选股日" if "流通市值_亿_选股日" in df.columns else "流通市值_亿"
    mv = pd.to_numeric(df.get(mvcol), errors="coerce")

    print("file:", path)
    print(
        "n=",
        len(df),
        "buy",
        str(df["_buy"].min())[:10],
        "->",
        str(df["_buy"].max())[:10],
        "rank%",
        round(float(rk.notna().mean()) * 100, 1),
        "mv%",
        round(float(mv.notna().mean()) * 100, 1),
    )

    masks = {
        "全池": pd.Series(True, index=df.index),
        "排名11-30∧市值<40": (rk >= 11) & (rk <= 30) & (mv < 40),
        "排名11-30∧市值<50": (rk >= 11) & (rk <= 30) & (mv < 50),
        "排名5-20∧市值<50": (rk >= 5) & (rk <= 20) & (mv < 50),
    }
    if "满足条件" in df.columns:
        masks["旧满足条件"] = ab_true(df["满足条件"])

    for name, m in masks.items():
        sub = df.loc[m.fillna(False)]
        r = sub["_ret"]
        per = sub.groupby("_sel").size()
        ps = path_stats(sub)
        print(f"\n=== {name} ===")
        if len(sub) == 0:
            print("  empty")
            continue
        print(
            f"  n={len(sub)} mean={r.mean():+.3f}% med={r.median():+.3f}% "
            f"win={(r > 0).mean() * 100:.1f}%"
        )
        print(
            f"  selDays={per.index.nunique()} avg/day={per.mean():.2f} "
            f"emptyDays≈{df['_sel'].nunique() - per.index.nunique()}"
        )
        if ps:
            ratio = abs(ps["max_dd"]) / ps["cum"] if ps["cum"] > 0 else float("inf")
            print(
                f"  path: cum={ps['cum']:+.1f}pp DD={ps['max_dd']:.1f} "
                f"ratio={ratio:.2f} dayMean={ps['day_mean']:+.3f} "
                f"dayWin={ps['day_win']:.1f}%"
            )

    m1 = (rk >= 11) & (rk <= 30) & (mv < 40)
    print("\n=== 2023 月度票均（%）===")
    print(
        f"{'月':8} {'全池n':>6} {'全池均':>8} {'全池胜':>6}  "
        f"{'滤n':>6} {'滤均':>8} {'滤胜':>6}"
    )
    for m in sorted(df["_m"].dropna().unique()):
        g = df[df["_m"] == m]
        f = g.loc[m1.reindex(g.index, fill_value=False)]
        a0 = float(g["_ret"].mean()) if len(g) else float("nan")
        w0 = float((g["_ret"] > 0).mean() * 100) if len(g) else float("nan")
        a1 = float(f["_ret"].mean()) if len(f) else float("nan")
        w1 = float((f["_ret"] > 0).mean() * 100) if len(f) else float("nan")
        print(
            f"{m:8} {len(g):6d} {a0:+8.3f} {w0:6.1f}  "
            f"{len(f):6d} {a1:+8.3f} {w1:6.1f}"
        )


if __name__ == "__main__":
    main()
