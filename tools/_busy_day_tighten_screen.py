# -*- coding: utf-8 -*-
"""Screen asymmetric (busy-day-only) tighten rules on 215 新规则 fills.

Busy day: baseline daily n >= BUSY_N (default 15).
Thin/other days: never filtered.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门")
BUY_SUM = ROOT / "各日选股收益汇总_新规则.xlsx"
DIST = ROOT / "Cond12_MA空头_每日笔数.xlsx"
OUT_XLSX = ROOT / "忙日加严_筛选.xlsx"
OUT_JSON = ROOT / "_busy_day_tighten_screen.json"

RET = "收益率pct"
DAY = "选股日"
ELIG = "合格榜内序位"
RS = "合格榜标签内RS排名"
GAP = "均线差占比"
OPEN_REL = "成交相对买入日MA5_pct"
MV = "流通市值_亿"
RS10 = "近10日RS"
BUSY_N = 15


def metrics(sub: pd.DataFrame) -> dict:
    if sub.empty:
        return dict(n=0, mean=None, med=None, win=None)
    r = sub[RET]
    return dict(
        n=int(len(sub)),
        mean=float(r.mean()),
        med=float(r.median()),
        win=float((r > 0).mean() * 100),
    )


def apply_rule(df: pd.DataFrame, busy_mask: pd.Series, drop_busy: pd.Series) -> pd.DataFrame:
    """drop_busy True => remove that busy-day row; non-busy always kept."""
    keep = df.loc[~busy_mask | ~drop_busy].copy()
    return keep


def eval_rule(df: pd.DataFrame, busy_mask: pd.Series, drop_busy: pd.Series, name: str) -> dict:
    keep = apply_rule(df, busy_mask, drop_busy)
    cut = df.loc[busy_mask & drop_busy]
    m_base = metrics(df)
    m_keep = metrics(keep)
    m_cut = metrics(cut)
    m_busy_base = metrics(df.loc[busy_mask])
    m_busy_keep = metrics(df.loc[busy_mask & ~drop_busy])

    # coverage: thin days untouched by construction
    daily_base = df.groupby(DAY).size()
    daily_keep = keep.groupby(DAY).size()
    thin_days = set(daily_base[daily_base.between(1, 5)].index.astype(str))
    thin_kept = set(daily_keep.reindex(list(thin_days)).fillna(0).loc[lambda s: s > 0].index.astype(str)) if thin_days else set()

    busy_days = sorted(df.loc[busy_mask, DAY].unique())
    per_busy = []
    for d in busy_days:
        b = df[(df[DAY] == d)]
        k = keep[keep[DAY] == d]
        c = b.loc[~b.index.isin(k.index)]
        per_busy.append(
            {
                "选股日": d,
                "基线n": int(len(b)),
                "保留n": int(len(k)),
                "砍掉": int(len(c)),
                "基线均": float(b[RET].mean()),
                "保留均": float(k[RET].mean()) if len(k) else None,
                "被砍均": float(c[RET].mean()) if len(c) else None,
            }
        )

    score = None
    if m_keep["mean"] is not None and m_cut["n"] >= 5:
        # prefer: lift overall mean, cut mean low/negative, don't cut too many, cut less than keep mean
        score = (
            (m_keep["mean"] - m_base["mean"]) * 20
            - max(0.0, float(m_cut["mean"] or 0)) * 3
            - m_cut["n"] * 0.02
            + (1.0 if (m_cut["mean"] is not None and m_cut["mean"] < 0.5) else 0.0)
            + (2.0 if (m_cut["mean"] is not None and m_cut["mean"] < 0) else 0.0)
        )

    return {
        "name": name,
        "cut_n": int(m_cut["n"]),
        "keep_n": int(m_keep["n"]),
        "base_mean": m_base["mean"],
        "keep_mean": m_keep["mean"],
        "delta_mean": None
        if m_keep["mean"] is None
        else m_keep["mean"] - m_base["mean"],
        "keep_win": m_keep["win"],
        "cut_mean": m_cut["mean"],
        "cut_win": m_cut["win"],
        "busy_base_mean": m_busy_base["mean"],
        "busy_keep_mean": m_busy_keep["mean"],
        "busy_delta": None
        if m_busy_keep["mean"] is None
        else m_busy_keep["mean"] - m_busy_base["mean"],
        "thin_days_kept": len(thin_kept),
        "thin_days_base": len(thin_days),
        "score": score,
        "per_busy": per_busy,
    }


def main() -> None:
    df = pd.read_excel(BUY_SUM)
    for c in [RET, ELIG, RS, GAP, OPEN_REL, MV, RS10]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df[DAY] = df[DAY].astype(str).str[:10]
    df["_n"] = df.groupby(DAY)[RET].transform("count")
    busy_mask = df["_n"] >= BUSY_N
    busy = df.loc[busy_mask]

    print(
        "busy days:",
        sorted(busy[DAY].unique()),
        "n_busy=",
        len(busy),
        "mean=",
        busy[RET].mean(),
    )

    # terciles on busy only for idea generation
    terc_rows = []
    for fac, col in [
        ("开盘相对MA5%", OPEN_REL),
        ("均线差占比", GAP),
        ("合格榜内序位", ELIG),
        ("标签内RS", RS),
        ("近10日RS", RS10),
        ("流通市值亿", MV),
    ]:
        if col not in df.columns:
            continue
        s = busy[[col, RET]].dropna().copy()
        if len(s) < 20:
            continue
        try:
            s["bin"] = pd.qcut(s[col], 3, labels=["低", "中", "高"], duplicates="drop")
        except ValueError:
            continue
        g = s.groupby("bin", observed=True)[RET].agg(["count", "mean"])
        for b, r in g.iterrows():
            terc_rows.append(
                {
                    "factor": fac,
                    "bin": str(b),
                    "n": int(r["count"]),
                    "mean": float(r["mean"]),
                }
            )

    rules = []

    def add(name: str, drop_busy: pd.Series):
        # only evaluate drop on busy rows; align index
        db = pd.Series(False, index=df.index)
        db.loc[busy_mask] = drop_busy.reindex(df.loc[busy_mask].index).fillna(False).values
        # fix: drop_busy should be boolean series on full index or busy index
        rules.append(eval_rule(df, busy_mask, db, name))

    # rebuild add more carefully
    rules = []

    def add2(name: str, cond_on_busy: pd.Series):
        """cond_on_busy: True on busy rows that should be DROPPED."""
        drop = pd.Series(False, index=df.index)
        drop.loc[cond_on_busy.index] = cond_on_busy.astype(bool)
        # ensure non-busy never dropped
        drop = drop & busy_mask
        rules.append(eval_rule(df, busy_mask, drop, name))

    b = df.loc[busy_mask]

    # Cond2-like on busy only
    for hi in [1.5, 1.2, 1.0]:
        add2(
            "忙日:开盘rel≤%.1f%%(砍>)" % hi,
            b[OPEN_REL] > hi,
        )
    # ELIG on busy
    for hi in [25, 20, 15]:
        add2("忙日:ELIG≤%d(砍>)" % hi, b[ELIG] > hi)
    # RS on busy
    for hi in [40, 30, 20]:
        add2("忙日:标签RS≤%d(砍>)" % hi, b[RS] > hi)
    # MA gap band tighten on busy
    add2("忙日:gap≤1.8%(砍>)", b[GAP] > 0.018)
    add2("忙日:gap≥0.8%(砍<)", b[GAP] < 0.008)
    add2("忙日:gap∈[0.8%,1.8%]", (b[GAP] < 0.008) | (b[GAP] > 0.018))
    # MV: cut large or small?
    if MV in df.columns:
        med = float(b[MV].median())
        add2("忙日:砍市值≥中位(%.0f亿)" % med, b[MV] >= med)
        add2("忙日:砍市值<中位", b[MV] < med)
        add2("忙日:砍市值≥120亿", b[MV] >= 120)
    # RS10: busy corr was positive — cut LOW rs10
    q33 = float(b[RS10].quantile(0.33))
    q66 = float(b[RS10].quantile(0.66))
    add2("忙日:砍近10日RS低三分位(<%.4f)" % q33, b[RS10] < q33)
    add2("忙日:砍近10日RS高三分位(>%.4f)" % q66, b[RS10] > q66)
    # open_rel high tercile only
    o66 = float(b[OPEN_REL].quantile(0.66))
    add2("忙日:砍开盘rel高三分位(>%.3f)" % o66, b[OPEN_REL] > o66)
    # combo soft
    add2(
        "忙日:开盘rel>1.5% 或 ELIG>25",
        (b[OPEN_REL] > 1.5) | (b[ELIG] > 25),
    )
    add2(
        "忙日:开盘rel>1.5% 且 ELIG>20",
        (b[OPEN_REL] > 1.5) & (b[ELIG] > 20),
    )
    add2(
        "忙日:开盘rel>1.2% 且 gap>1.5%",
        (b[OPEN_REL] > 1.2) & (b[GAP] > 0.015),
    )
    # top-K per busy day by strength (lower elig*8+rs = stronger)
    for mode, keep_k in [("保留强度最好4只", 4), ("保留强度最好半数", None)]:
        drop = pd.Series(False, index=b.index)
        for _, g in b.groupby(DAY):
            score = g[ELIG].fillna(999) * 8 + g[RS].fillna(999)
            k = keep_k if keep_k is not None else max(1, len(g) // 2)
            keep_idx = score.nsmallest(min(k, len(g))).index
            drop.loc[g.index.difference(keep_idx)] = True
        add2("忙日:" + mode, drop)

    for frac, seed in [(0.2, 42), (0.3, 42)]:
        rng = np.random.default_rng(seed)
        drop = pd.Series(False, index=b.index)
        for _, g in b.groupby(DAY):
            n_drop = max(1, int(round(len(g) * frac)))
            choose = rng.choice(
                g.index.to_numpy(), size=min(n_drop, len(g)), replace=False
            )
            drop.loc[choose] = True
        add2("忙日:随机砍%.0f%%/日(seed%d)" % (frac * 100, seed), drop)

    # rank rules
    sim_df = pd.DataFrame([{k: v for k, v in r.items() if k != "per_busy"} for r in rules])
    sim_df = sim_df.sort_values(["score", "delta_mean"], ascending=[False, False])

    # export
    base_m = metrics(df)
    summary_rows = [
        {"项": "忙日定义", "值": "当日基线笔数 n≥%d" % BUSY_N},
        {"项": "忙日列表", "值": ", ".join(sorted(busy[DAY].unique()))},
        {"项": "忙日笔数", "值": len(busy)},
        {"项": "忙日均收益%", "值": round(float(busy[RET].mean()), 4)},
        {"项": "全体基线均收益%", "值": round(base_m["mean"], 4)},
        {"项": "说明", "值": "以下规则只在忙日额外淘汰；弱日/普通日原样保留"},
    ]

    daily_base = (
        df.groupby(DAY)
        .agg(n=(RET, "count"), mean=(RET, "mean"))
        .reset_index()
    )

    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        pd.DataFrame(summary_rows).to_excel(w, sheet_name="说明", index=False)
        sim_df.to_excel(w, sheet_name="规则对比", index=False)
        pd.DataFrame(terc_rows).to_excel(w, sheet_name="忙日三分位", index=False)
        daily_base.to_excel(w, sheet_name="基线按日", index=False)
        # top 5 detail sheets
        for i, r in enumerate(rules):
            if r["name"] not in set(sim_df.head(8)["name"]):
                continue
        # dump per_busy for top scored
        top_names = list(sim_df.head(10)["name"])
        detail_frames = []
        for r in rules:
            if r["name"] not in top_names:
                continue
            pb = pd.DataFrame(r["per_busy"])
            pb.insert(0, "规则", r["name"])
            detail_frames.append(pb)
        if detail_frames:
            pd.concat(detail_frames, ignore_index=True).to_excel(
                w, sheet_name="Top规则按忙日", index=False
            )

    out = {
        "busy_days": sorted(busy[DAY].unique()),
        "busy_n": int(len(busy)),
        "busy_mean": float(busy[RET].mean()),
        "tercile_busy": terc_rows,
        "rules": [{k: v for k, v in r.items() if k != "per_busy"} for r in rules],
        "ranked": sim_df.head(12).replace({np.nan: None}).to_dict("records"),
        "per_busy_top": {
            r["name"]: r["per_busy"]
            for r in rules
            if r["name"] in set(sim_df.head(8)["name"])
        },
    }
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT_XLSX)
    print(sim_df.head(15).to_string(index=False))


if __name__ == "__main__":
    main()
