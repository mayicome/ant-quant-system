# -*- coding: utf-8 -*-
"""边界敏感性：排名/市值附近桶是否也成立，还是仅精确点。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "history_data" / "马总选股逻辑"


def load() -> pd.DataFrame:
    parts = []
    for folder in (BASE / "2024", BASE / "最终"):
        hits = list(folder.glob("*按票_已完成_收盘上MA10_latest.xlsx"))
        df = pd.read_excel(hits[0])
        buy = pd.to_datetime(df.get("买入日"), errors="coerce")
        df = df.copy()
        df["_buy"] = buy
        df["_ret"] = pd.to_numeric(df["收益率pct"], errors="coerce")
        df["_y"] = buy.dt.year
        parts.append(df)
    out = pd.concat(parts, ignore_index=True).dropna(subset=["_buy", "_ret", "_y"])
    return out[out["_y"].isin([2024, 2025, 2026])].copy()


def eval_mask(df: pd.DataFrame, mask: pd.Series, label: str) -> None:
    sub = df.loc[mask.fillna(False)]
    sel = pd.to_datetime(sub.get("选股日"), errors="coerce") if "选股日" in sub.columns else sub["_buy"]
    sel = sel.dt.normalize()
    # coverage vs all select days in pool
    all_sel = pd.to_datetime(df.get("选股日"), errors="coerce") if "选股日" in df.columns else df["_buy"]
    all_sel = all_sel.dt.normalize()
    all_days = all_sel.nunique()
    hit_days = sel.nunique()
    empty_pct = 100 * (1 - hit_days / all_days) if all_days else 0

    means = []
    print(f"\n{label}")
    print(
        f"  n={len(sub)} hitDays={hit_days}/{all_days} emptyDay%={empty_pct:.1f} "
        f"avg/day={len(sub)/hit_days if hit_days else 0:.2f}"
    )
    for y in (2024, 2025, 2026):
        g = sub[sub["_y"] == y]
        m = float(g["_ret"].mean()) if len(g) else float("nan")
        means.append(m)
        print(f"  {y}: n={len(g):4d} mean={m:+.3f}%")
    day = sub.copy()
    day["_d"] = day["_buy"].dt.normalize()
    dm = day.groupby("_d")["_ret"].mean().sort_index()
    if len(dm) == 0:
        print("  path: empty")
        return
    cum = dm.cumsum()
    dd = (cum - cum.cummax()).min()
    print(
        f"  minY={min(means):+.3f} cum={cum.iloc[-1]:+.1f} DD={dd:.1f} "
        f"ratio={abs(dd)/cum.iloc[-1] if cum.iloc[-1] > 0 else float('inf'):.2f}"
    )


def main() -> None:
    df = load()
    rk = pd.to_numeric(df["最佳板块排名"], errors="coerce")
    mvcol = "流通市值_亿_选股日" if "流通市值_亿_选股日" in df.columns else "流通市值_亿"
    mv = pd.to_numeric(df[mvcol], errors="coerce")

    print("=== 精确点 vs 邻近桶 ===")
    grids = [
        ("精确 11-30 & <40", (rk >= 11) & (rk <= 30) & (mv < 40)),
        ("邻近 10-30 & <40", (rk >= 10) & (rk <= 30) & (mv < 40)),
        ("邻近 12-30 & <40", (rk >= 12) & (rk <= 30) & (mv < 40)),
        ("邻近 11-28 & <40", (rk >= 11) & (rk <= 28) & (mv < 40)),
        ("邻近 11-32 & <40", (rk >= 11) & (rk <= 32) & (mv < 40)),
        ("邻近 8-25 & <40", (rk >= 8) & (rk <= 25) & (mv < 40)),
        ("邻近 5-20 & <40", (rk >= 5) & (rk <= 20) & (mv < 40)),
        ("邻近 15-35 & <40", (rk >= 15) & (rk <= 35) & (mv < 40)),
        ("精确 11-30 & <35", (rk >= 11) & (rk <= 30) & (mv < 35)),
        ("精确 11-30 & <45", (rk >= 11) & (rk <= 30) & (mv < 45)),
        ("精确 11-30 & <50", (rk >= 11) & (rk <= 30) & (mv < 50)),
        ("仅 11-30", (rk >= 11) & (rk <= 30)),
        ("仅 <40", mv < 40),
        ("仅 5-20", (rk >= 5) & (rk <= 20)),
        ("仅 <50", mv < 50),
        ("旧 5-20 & <50", (rk >= 5) & (rk <= 20) & (mv < 50)),
    ]
    for label, m in grids:
        eval_mask(df, m, label)

    # placebo: random same-size subsample per year matching 11-30&<40 counts
    print("\n=== 安慰剂：按年随机等量抽样 200 次 ===")
    target = (rk >= 11) & (rk <= 30) & (mv < 40)
    rng = np.random.default_rng(42)
    real_means = []
    for y in (2024, 2025, 2026):
        real_means.append(float(df.loc[(df["_y"] == y) & target.fillna(False), "_ret"].mean()))
    real_min = min(real_means)

    beat = 0
    trials = 200
    for _ in range(trials):
        means = []
        ok = True
        for y in (2024, 2025, 2026):
            g = df[df["_y"] == y]
            n = int(((df["_y"] == y) & target.fillna(False)).sum())
            if n < 10 or n > len(g):
                ok = False
                break
            idx = rng.choice(g.index.to_numpy(), size=n, replace=False)
            means.append(float(g.loc[idx, "_ret"].mean()))
        if not ok:
            continue
        if min(means) >= real_min and float(np.mean(means)) >= float(np.mean(real_means)):
            beat += 1
    print(f"real yearly means={[round(x,3) for x in real_means]} min={real_min:+.3f}")
    print(f"random same-n: trials with min>=real_min AND avg>=real_avg: {beat}/{trials}")

    # how many empty select days
    print("\n=== 有选股日但过滤后为空的比例（相对全池有票日）===")
    all_sel = pd.to_datetime(df.get("选股日"), errors="coerce") if "选股日" in df.columns else df["_buy"]
    df["_sel"] = all_sel.dt.normalize()
    pool_days = set(df["_sel"].dropna().unique())
    sub = df.loc[target.fillna(False)]
    hit = set(sub["_sel"].dropna().unique())
    print(f"pool days={len(pool_days)} filter days={len(hit)} empty={len(pool_days-hit)} ({100*len(pool_days-hit)/len(pool_days):.1f}%)")


if __name__ == "__main__":
    main()
