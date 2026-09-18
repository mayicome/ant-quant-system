# -*- coding: utf-8 -*-
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "history_data" / "马总选股逻辑"


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


parts = []
for folder in (BASE / "2024", BASE / "最终"):
    hits = list(folder.glob("*按票_已完成_收盘上MA10_latest.xlsx"))
    df = pd.read_excel(hits[0])
    buy = pd.to_datetime(df.get("买入日"), errors="coerce")
    code_col = "代码" if "代码" in df.columns else "股票代码"
    df = df.copy()
    df["_buy"] = buy
    df["_ret"] = pd.to_numeric(df["收益率pct"], errors="coerce")
    df["_y"] = buy.dt.year
    df["_code"] = df[code_col]
    parts.append(df)
df = pd.concat(parts, ignore_index=True).dropna(subset=["_buy", "_ret", "_y"])
df = df[df["_y"].isin([2024, 2025, 2026])].copy()
rk = pd.to_numeric(df["最佳板块排名"], errors="coerce")
mvcol = "流通市值_亿_选股日" if "流通市值_亿_选股日" in df.columns else "流通市值_亿"
mv = pd.to_numeric(df[mvcol], errors="coerce")
zt = ab(df["条件_当日涨停"])
sel = pd.to_datetime(df.get("选股日"), errors="coerce") if "选股日" in df.columns else df["_buy"]
df["_sel"] = sel.dt.normalize()

cands = {
    "全池": pd.Series(True, index=df.index),
    "旧:排名5-20&市值<50": (rk >= 5) & (rk <= 20) & (mv < 50),
    "旧满足": ab(df["满足条件"]) == True,  # noqa: E712
    "新:排名11-30&市值<40": (rk >= 11) & (rk <= 30) & (mv < 40),
    "新:排名11-30&市值<50": (rk >= 11) & (rk <= 30) & (mv < 50),
    "新:非涨停&11-30&<40": (zt == False) & (rk >= 11) & (rk <= 30) & (mv < 40),  # noqa: E712
    "新:排名11-30&市值<30": (rk >= 11) & (rk <= 30) & (mv < 30),
}

for name, m in cands.items():
    sub = df.loc[m.fillna(False)]
    per_day = sub.groupby("_sel").size()
    print(f"\n=== {name} ===")
    print(
        f"n={len(sub)} days={sub['_sel'].nunique()} "
        f"avg/day={per_day.mean():.2f} med/day={per_day.median():.1f}"
    )
    for y in (2024, 2025, 2026):
        g = sub[sub["_y"] == y]
        r = g["_ret"]
        pdays = g.groupby("_sel").size()
        print(
            f"  {y}: n={len(g)} mean={r.mean():+.3f}% "
            f"win={(r > 0).mean() * 100:.1f}% avg/day={pdays.mean():.2f}"
        )
    day = sub.copy()
    day["_d"] = day["_buy"].dt.normalize()
    dm = day.groupby("_d")["_ret"].mean().sort_index()
    cum = dm.cumsum()
    peak = cum.cummax()
    dd = (cum - peak).min()
    print(
        f"  path: cum={cum.iloc[-1]:+.1f}pp maxDD={dd:.1f}pp "
        f"ratio={abs(dd) / cum.iloc[-1]:.2f} day_mean={dm.mean():+.3f}"
    )
