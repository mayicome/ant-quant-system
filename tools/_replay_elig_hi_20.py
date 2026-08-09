# -*- coding: utf-8 -*-
"""Replay ELIG_HI 30→20 on 215 新规则 fills vs baseline."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门")
BUY_SUM = ROOT / "各日选股收益汇总_新规则.xlsx"
DIST = ROOT / "Cond12_MA空头_每日笔数.xlsx"
OUT_XLSX = ROOT / "ELIG_HI20_单独回放.xlsx"
OUT_JSON = ROOT / "_replay_elig_hi_20.json"

RET = "收益率pct"
DAY = "选股日"
ELIG = "合格榜内序位"
CODE = "代码"
NAME = "股票名称"
HI = 20


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


def main() -> None:
    df = pd.read_excel(BUY_SUM)
    df[RET] = pd.to_numeric(df[RET], errors="coerce")
    df[ELIG] = pd.to_numeric(df[ELIG], errors="coerce")
    df[DAY] = df[DAY].astype(str).str[:10]

    dist = pd.read_excel(DIST, sheet_name="by_选股日")
    cal_days = list(dist["选股日"].astype(str).str[:10])

    base = df.copy()
    keep = df[df[ELIG].notna() & (df[ELIG] <= HI)].copy()
    drop = df[df[ELIG].isna() | (df[ELIG] > HI)].copy()

    m_base = metrics(base)
    m_keep = metrics(keep)
    m_drop = metrics(drop)

    rows = []
    for d in cal_days:
        b = base[base[DAY] == d]
        k = keep[keep[DAY] == d]
        cut = b.loc[~b.index.isin(k.index)]
        rows.append(
            {
                "选股日": d,
                "基线笔数": int(len(b)),
                "ELIG≤20笔数": int(len(k)),
                "砍掉": int(len(b) - len(k)),
                "基线均收益%": float(b[RET].mean()) if len(b) else None,
                "保留均收益%": float(k[RET].mean()) if len(k) else None,
                "被砍均收益%": float(cut[RET].mean()) if len(cut) else None,
            }
        )
    daily = pd.DataFrame(rows)

    busy_base = daily[daily["基线笔数"] >= 15]
    weak_days = set(daily.loc[daily["基线笔数"].between(1, 5), "选股日"])
    weak_kept = set(
        daily.loc[
            (daily["选股日"].isin(weak_days)) & (daily["ELIG≤20笔数"] > 0), "选股日"
        ]
    )
    weak_empty = weak_days - weak_kept

    drop_cols = [
        c
        for c in [
            DAY,
            CODE,
            NAME,
            RET,
            ELIG,
            "合格榜标签内RS排名",
            "均线差占比",
            "成交相对买入日MA5_pct",
            "买入日",
        ]
        if c in drop.columns
    ]
    drop_detail = drop[drop_cols].sort_values([DAY, RET])

    summary = pd.DataFrame(
        [
            {"项": "过滤", "值": "合格榜内序位 ≤ 20（ELIG_HI 30→20）"},
            {"项": "基线笔数", "值": m_base["n"]},
            {"项": "基线均收益%", "值": round(m_base["mean"], 4)},
            {"项": "基线中位%", "值": round(m_base["med"], 4)},
            {"项": "基线胜率%", "值": round(m_base["win"], 2)},
            {"项": "保留笔数", "值": m_keep["n"]},
            {"项": "保留均收益%", "值": round(m_keep["mean"], 4)},
            {"项": "保留中位%", "值": round(m_keep["med"], 4)},
            {"项": "保留胜率%", "值": round(m_keep["win"], 2)},
            {"项": "Δ均收益pp", "值": round(m_keep["mean"] - m_base["mean"], 4)},
            {"项": "砍掉笔数", "值": m_drop["n"]},
            {
                "项": "被砍均收益%",
                "值": None if m_drop["mean"] is None else round(m_drop["mean"], 4),
            },
            {
                "项": "被砍中位%",
                "值": None if m_drop["med"] is None else round(m_drop["med"], 4),
            },
            {
                "项": "被砍胜率%",
                "值": None if m_drop["win"] is None else round(m_drop["win"], 2),
            },
            {"项": "有票日(基线)", "值": int((daily["基线笔数"] > 0).sum())},
            {"项": "有票日(过滤后)", "值": int((daily["ELIG≤20笔数"] > 0).sum())},
            {
                "项": "空仓日新增",
                "值": int(
                    ((daily["基线笔数"] > 0) & (daily["ELIG≤20笔数"] == 0)).sum()
                ),
            },
            {"项": "弱日n≤5(基线天数)", "值": len(weak_days)},
            {"项": "弱日仍有票(过滤后)", "值": len(weak_kept)},
            {"项": "弱日被抽空天数", "值": len(weak_empty)},
            {
                "项": "忙日砍掉笔数合计",
                "值": int(busy_base["砍掉"].sum()) if len(busy_base) else 0,
            },
            {
                "项": "判定",
                "值": (
                    "不建议收紧：被砍均收益≥保留"
                    if (m_drop["mean"] is not None and m_drop["mean"] >= m_keep["mean"])
                    else (
                        "理由偏弱：被砍仍为正期望"
                        if (m_drop["mean"] is not None and m_drop["mean"] > 0)
                        else "可考虑：被砍为负/接近零"
                    )
                ),
            },
        ]
    )

    keep_cols = [c for c in drop_cols if c in keep.columns]
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="summary", index=False)
        daily.to_excel(w, sheet_name="by_选股日", index=False)
        drop_detail.to_excel(w, sheet_name="被砍明细", index=False)
        keep[keep_cols].sort_values([DAY, RET], ascending=[True, False]).to_excel(
            w, sheet_name="保留明细", index=False
        )

    out = {
        "summary": summary.set_index("项")["值"].to_dict(),
        "daily": daily.replace({np.nan: None}).to_dict("records"),
        "weak_emptied": sorted(weak_empty),
        "metrics": {"base": m_base, "keep": m_keep, "drop": m_drop},
    }
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT_XLSX)
    print(summary.to_string(index=False))
    print("\n--- daily ---")
    print(daily.to_string(index=False))
    print("weak_emptied", sorted(weak_empty))
    if len(drop):
        print("\nworst cut:")
        print(drop_detail.nsmallest(6, RET).to_string(index=False))
        print("best cut:")
        print(drop_detail.nlargest(6, RET).to_string(index=False))


if __name__ == "__main__":
    main()
