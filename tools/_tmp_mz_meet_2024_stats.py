# -*- coding: utf-8 -*-
"""2024 马总满足条件统计（开买日维度）"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import ma_zong_meet_monitor as zb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


def stats(df: pd.DataFrame, label: str) -> dict:
    r = pd.to_numeric(df["ret"], errors="coerce").dropna()
    n = len(r)
    if n == 0:
        print(label, "empty")
        return {"label": label, "n": 0}
    out = dict(
        label=label,
        n=n,
        mean=float(r.mean()),
        median=float(r.median()),
        win_rate=float((r > 0).mean() * 100),
        loss_rate=float((r < 0).mean() * 100),
        flat_rate=float((r == 0).mean() * 100),
        sum_ret=float(r.sum()),
        std=float(r.std(ddof=1)) if n > 1 else 0.0,
        p05=float(r.quantile(0.05)),
        p25=float(r.quantile(0.25)),
        p75=float(r.quantile(0.75)),
        p95=float(r.quantile(0.95)),
        mn=float(r.min()),
        mx=float(r.max()),
        n_win=int((r > 0).sum()),
        n_loss=int((r < 0).sum()),
        n_flat=int((r == 0).sum()),
    )
    print(
        f"{label}: n={n} 票均={out['mean']:.4f}% 中位={out['median']:.4f}% "
        f"胜率={out['win_rate']:.2f}% 亏损率={out['loss_rate']:.2f}% "
        f"累加={out['sum_ret']:.2f}% std={out['std']:.4f} "
        f"min={out['mn']:.2f} max={out['mx']:.2f}"
    )
    return out


def main() -> None:
    files = zb.discover_trade_files()
    print("files", [p.name for p in files[:5]])
    pool = zb.load_pool(files, variant=None)
    print("rows", len(pool))
    print("start", pool["start"].min(), "->", pool["start"].max())
    print("meet", pool["meet"].value_counts(dropna=False).to_dict())

    m = (pool["start"] >= date(2024, 1, 1)) & (pool["start"] <= date(2024, 12, 31))
    p24 = pool.loc[m].copy()
    print("=== 2024开买日 ===", len(p24))
    meet = p24[p24["meet"] == True].copy()  # noqa: E712
    notm = p24[p24["meet"] == False].copy()  # noqa: E712
    s_a = stats(p24, "全部")
    s_m = stats(meet, "满足")
    s_n = stats(notm, "不满足")

    print("=== 满足 分月 ===")
    meet["ym"] = pd.to_datetime(meet["start"]).dt.to_period("M").astype(str)
    months = []
    for ym, g in meet.groupby("ym"):
        r = pd.to_numeric(g["ret"], errors="coerce").dropna()
        row = dict(
            ym=ym,
            n=len(r),
            mean=float(r.mean()) if len(r) else None,
            win=float((r > 0).mean() * 100) if len(r) else None,
            sum=float(r.sum()) if len(r) else None,
            median=float(r.median()) if len(r) else None,
        )
        months.append(row)
        print(
            ym,
            f"n={row['n']} 票均={row['mean']:.3f}% 中位={row['median']:.3f}% "
            f"胜率={row['win']:.1f}% 累加={row['sum']:.2f}%",
        )

    print("=== 满足 季度 ===")
    meet["q"] = pd.to_datetime(meet["start"]).dt.to_period("Q").astype(str)
    qs = []
    for q, g in meet.groupby("q"):
        r = pd.to_numeric(g["ret"], errors="coerce").dropna()
        row = dict(
            q=q,
            n=len(r),
            mean=float(r.mean()),
            win=float((r > 0).mean() * 100),
            sum=float(r.sum()),
            median=float(r.median()),
        )
        qs.append(row)
        print(
            q,
            f"n={row['n']} 票均={row['mean']:.3f}% 中位={row['median']:.3f}% "
            f"胜率={row['win']:.1f}% 累加={row['sum']:.2f}%",
        )

    print("满足开买日数", meet["start"].nunique(), "标的数", meet["code"].nunique())
    print("不满足开买日数", notm["start"].nunique(), "标的数", notm["code"].nunique())

    # 分位桶：收益分布
    r_m = pd.to_numeric(meet["ret"], errors="coerce").dropna()
    bins = [-np.inf, -10, -5, -2, 0, 2, 5, 10, np.inf]
    labels = ["<-10%", "-10~-5", "-5~-2", "-2~0", "0~2", "2~5", "5~10", ">10%"]
    cat = pd.cut(r_m, bins=bins, labels=labels)
    dist = {str(k): int(v) for k, v in cat.value_counts().sort_index().items()}
    print("=== 满足 收益分布 ===", dist)

    out = {
        "source": files[0].name if files else "",
        "year": 2024,
        "axis": "开买日 start",
        "all": s_a,
        "meet": s_m,
        "not_meet": s_n,
        "meet_months": months,
        "meet_quarters": qs,
        "meet_n_starts": int(meet["start"].nunique()),
        "meet_n_codes": int(meet["code"].nunique()),
        "not_n_starts": int(notm["start"].nunique()),
        "not_n_codes": int(notm["code"].nunique()),
        "meet_share_pct": float(len(meet) / len(p24) * 100) if len(p24) else 0,
        "meet_ret_dist": dist,
        "delta_mean_meet_minus_not": float(s_m.get("mean", 0) - s_n.get("mean", 0))
        if s_m.get("n") and s_n.get("n")
        else None,
    }
    path = ROOT / "data" / "_tmp_mz_meet_2024_stats.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", path)


if __name__ == "__main__":
    main()
