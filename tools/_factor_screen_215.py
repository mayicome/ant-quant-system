# -*- coding: utf-8 -*-
"""Rough factor screen on 215 新规则 fills: corr, terciles, soft tighten sims."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门")
OUT = ROOT / "_factor_screen_215.json"

RET = "收益率pct"
DAY = "选股日"
ELIG = "合格榜内序位"
RS = "合格榜标签内RS排名"
GAP = "均线差占比"
MV = "流通市值_亿"
# 汇总表无「开盘相对早盘MA5」；用成交相对买入日MA5 作 Cond2 代理（开盘夹档多为开盘成交）
OPEN_REL = "成交相对买入日MA5_pct"
RS10 = "近10日RS"


def day_stats(df: pd.DataFrame, sub: pd.DataFrame) -> dict:
    if sub.empty:
        return dict(
            n=0,
            mean=None,
            win=None,
            days_gt0=0,
            thin_days_kept=0,
            thin_days_base=0,
            busy_cut=0,
            thin_cut=0,
            cut=len(df),
            delta_mean=None,
        )
    base_n = df.groupby(DAY).size()
    thin_base = set(base_n[base_n <= 5].index.astype(str))
    g = sub.groupby(DAY).size()
    kept = set(g.index.astype(str))
    removed_mask = ~df.index.isin(sub.index)
    mean = float(sub[RET].mean())
    base_mean = float(df[RET].mean())
    return dict(
        n=int(len(sub)),
        mean=mean,
        win=float((sub[RET] > 0).mean() * 100),
        days_gt0=int((g > 0).sum()),
        thin_days_kept=int(len(thin_base & kept)),
        thin_days_base=int(len(thin_base)),
        busy_cut=int((removed_mask & df["_busy"]).sum()),
        thin_cut=int((removed_mask & df["_thin"]).sum()),
        cut=int(len(df) - len(sub)),
        delta_mean=mean - base_mean,
    )


def main() -> None:
    df = pd.read_excel(ROOT / "各日选股收益汇总_新规则.xlsx")
    for c in [RET, DAY, ELIG, RS, GAP, MV, OPEN_REL, RS10]:
        if c not in df.columns:
            raise SystemExit("missing col %s" % c)

    df = df.copy()
    for c in [ELIG, RS, GAP, MV, OPEN_REL, RS10, RET]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["_n"] = df.groupby(DAY)[RET].transform("count")
    df["_busy"] = df["_n"] >= 15
    df["_thin"] = df["_n"] <= 5

    scale = "pct" if float(df[OPEN_REL].max()) > 0.5 else "frac"

    corr_rows = []
    for label, sub in [
        ("全部", df),
        ("忙日n>=15", df[df["_busy"]]),
        ("弱日n<=5", df[df["_thin"]]),
    ]:
        for fac, col in [
            ("合格榜内序位", ELIG),
            ("标签内RS", RS),
            ("均线差占比", GAP),
            ("开盘相对早盘MA5%", OPEN_REL),
            ("流通市值亿", MV),
            ("近10日RS", RS10),
            ("当日笔数", "_n"),
        ]:
            s = sub[[col, RET]].dropna()
            corr = float(s[col].corr(s[RET])) if len(s) >= 8 else None
            corr_rows.append(
                {"scope": label, "factor": fac, "corr": corr, "n": int(len(s))}
            )

    terc_rows = []
    for fac, col in [
        ("合格榜内序位", ELIG),
        ("标签内RS", RS),
        ("均线差占比", GAP),
        ("开盘相对早盘MA5%", OPEN_REL),
        ("流通市值亿", MV),
    ]:
        s = df[[col, RET]].dropna().copy()
        try:
            s["bin"] = pd.qcut(s[col], 3, labels=["低", "中", "高"], duplicates="drop")
        except ValueError:
            continue
        g = s.groupby("bin", observed=True).agg(
            n=(RET, "count"),
            mean=(RET, "mean"),
            win=(RET, lambda x: (x > 0).mean() * 100),
        )
        for b, r in g.iterrows():
            terc_rows.append(
                {
                    "factor": fac,
                    "bin": str(b),
                    "n": int(r["n"]),
                    "mean": float(r["mean"]),
                    "win": float(r["win"]),
                }
            )

    busy_bins = []
    busy = df[df["_busy"]].copy()
    for fac, col in [
        ("均线差占比", GAP),
        ("开盘相对早盘MA5%", OPEN_REL),
        ("合格榜内序位", ELIG),
        ("标签内RS", RS),
    ]:
        s = busy[[col, RET]].dropna().copy()
        if len(s) < 12:
            continue
        try:
            s["bin"] = pd.qcut(s[col], 3, labels=["低", "中", "高"], duplicates="drop")
        except ValueError:
            continue
        g = s.groupby("bin", observed=True)[RET].agg(["count", "mean"])
        for b, r in g.iterrows():
            busy_bins.append(
                {
                    "factor": fac,
                    "bin": str(b),
                    "n": int(r["count"]),
                    "mean": float(r["mean"]),
                }
            )

    sims = [{"name": "基线(215)", **day_stats(df, df)}]
    for hi in [0.018, 0.015, 0.012]:
        sims.append(
            {"name": "MA_GAP_HI≤%.1f%%" % (hi * 100), **day_stats(df, df[df[GAP] <= hi])}
        )
    for lo in [0.006, 0.008, 0.010]:
        sims.append(
            {"name": "MA_GAP_LO≥%.1f%%" % (lo * 100), **day_stats(df, df[df[GAP] >= lo])}
        )

    if scale == "pct":
        for hi in [1.5, 1.0, 0.5]:
            sims.append(
                {
                    "name": "开盘相对MA5≤%.1f%%" % hi,
                    **day_stats(df, df[df[OPEN_REL] <= hi]),
                }
            )
        for lo in [0.2, 0.5]:
            sims.append(
                {
                    "name": "开盘相对MA5≥%.1f%%" % lo,
                    **day_stats(df, df[df[OPEN_REL] >= lo]),
                }
            )
        soft = df[(df[GAP] <= 0.018) & (df[OPEN_REL] <= 1.5)]
    else:
        for hi in [0.015, 0.01, 0.005]:
            sims.append(
                {
                    "name": "开盘相对MA5≤%.1f%%" % (hi * 100),
                    **day_stats(df, df[df[OPEN_REL] <= hi]),
                }
            )
        soft = df[(df[GAP] <= 0.018) & (df[OPEN_REL] <= 0.015)]

    for hi in [25, 20, 15]:
        sims.append({"name": "ELIG_HI≤%d" % hi, **day_stats(df, df[df[ELIG] <= hi])})
    for hi in [40, 30, 20]:
        sims.append({"name": "标签RS≤%d" % hi, **day_stats(df, df[df[RS] <= hi])})
    for thr in [50, 80, 120]:
        sims.append(
            {"name": "市值≥%d亿" % thr, **day_stats(df, df[df[MV] >= thr])}
        )
    sims.append({"name": "软组合:gap≤1.8%&开盘rel≤1.5%", **day_stats(df, soft)})

    # rank candidates: lift mean, thin_cut small, prefer cut from busy
    ranked = []
    for s in sims[1:]:
        if s["mean"] is None or s["n"] < 80:
            continue
        score = (
            float(s["delta_mean"] or 0) * 10
            - float(s["thin_cut"]) * 0.15
            + float(s["busy_cut"]) * 0.02
            - max(0, 215 - s["n"] - 60) * 0.01
        )
        ranked.append({**s, "score": score})
    ranked.sort(key=lambda x: x["score"], reverse=True)

    day_rows = (
        df.groupby(DAY)
        .agg(n=(RET, "count"), mean=(RET, "mean"))
        .reset_index()
        .assign(**{DAY: lambda x: x[DAY].astype(str)})
        .to_dict("records")
    )

    out = {
        "source": "各日选股收益汇总_新规则.xlsx · 215笔 · 2026-07",
        "baseline": {
            "n": 215,
            "mean": float(df[RET].mean()),
            "win": float((df[RET] > 0).mean() * 100),
            "thin_days_base": int((df.groupby(DAY).size() <= 5).sum()),
            "busy_days": int((df.groupby(DAY).size() >= 15).sum()),
        },
        "open_rel_scale": scale,
        "corr": corr_rows,
        "tercile": terc_rows,
        "busy_bins": busy_bins,
        "sims": sims,
        "ranked": ranked[:8],
        "day_n_vs_mean": day_rows,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT)
    print("baseline mean", out["baseline"]["mean"])
    print("open_rel_scale", scale)
    print("--- top ranked ---")
    for r in ranked[:8]:
        print(
            r["name"],
            "n=%d" % r["n"],
            "mean=%+.3f" % r["mean"],
            "dmean=%+.3f" % r["delta_mean"],
            "busy_cut=%d" % r["busy_cut"],
            "thin_cut=%d" % r["thin_cut"],
            "thin_days=%d/%d" % (r["thin_days_kept"], r["thin_days_base"]),
        )


if __name__ == "__main__":
    main()
