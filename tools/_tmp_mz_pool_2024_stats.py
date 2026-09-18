# -*- coding: utf-8 -*-
"""2024 马总MA10 全池（不含软筛选）统计"""
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


def block(r: pd.Series) -> dict:
    r = pd.to_numeric(r, errors="coerce").dropna()
    n = len(r)
    if n == 0:
        return {"n": 0}
    return dict(
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


def main() -> None:
    files = zb.discover_trade_files()
    pool = zb.load_pool(files, variant=None)
    m = (pool["start"] >= date(2024, 1, 1)) & (pool["start"] <= date(2024, 12, 31))
    p = pool.loc[m].copy()
    r = pd.to_numeric(p["ret"], errors="coerce")

    s_all = block(r)
    print("=== 全池 2024开买日 ===")
    for k, v in s_all.items():
        print(f"  {k}: {v}")

    p["ym"] = pd.to_datetime(p["start"]).dt.to_period("M").astype(str)
    p["q"] = pd.to_datetime(p["start"]).dt.to_period("Q").astype(str)
    months, qs = [], []
    print("=== 分月 ===")
    for ym, g in p.groupby("ym"):
        b = block(g["ret"])
        months.append({"ym": ym, **b})
        print(f"{ym} n={b['n']} 票均={b['mean']:.3f}% 胜率={b['win_rate']:.1f}% 累加={b['sum_ret']:.1f}")
    print("=== 季度 ===")
    for q, g in p.groupby("q"):
        b = block(g["ret"])
        qs.append({"q": q, **b})
        print(f"{q} n={b['n']} 票均={b['mean']:.3f}% 胜率={b['win_rate']:.1f}% 累加={b['sum_ret']:.1f}")

    bins = [-np.inf, -10, -5, -2, 0, 2, 5, 10, np.inf]
    labels = ["<-10%", "-10~-5", "-5~-2", "-2~0", "0~2", "2~5", "5~10", ">10%"]
    cat = pd.cut(r.dropna(), bins=bins, labels=labels)
    dist = {str(k): int(v) for k, v in cat.value_counts().sort_index().items()}
    print("dist", dist)

    # 日度组合（选股日篮子总收益率%）
    day_path = ROOT / "history_data" / "马总选股逻辑" / "各日选股收益汇总_日线-ma10-sell_half-单点_latest.xlsx"
    day = pd.read_excel(day_path)
    day["选股日"] = pd.to_datetime(day["选股日"])
    dm = (day["选股日"] >= "2024-01-01") & (day["选股日"] <= "2024-12-31")
    d = day.loc[dm].copy()
    dr = pd.to_numeric(d["总收益率%"], errors="coerce")
    day_stats = block(dr)
    print("=== 日度篮子总收益率% ===")
    for k, v in day_stats.items():
        print(f"  {k}: {v}")
    print("avg stocks/day", float(pd.to_numeric(d["股票数"], errors="coerce").mean()))

    out = {
        "source_ticket": files[0].name if files else "",
        "source_daily": day_path.name,
        "year": 2024,
        "axis": "开买日 start · 全池（含满足+不满足，无软筛选门槛）",
        "ticket": s_all,
        "n_starts": int(p["start"].nunique()),
        "n_codes": int(p["code"].nunique()),
        "months": months,
        "quarters": qs,
        "ret_dist": dist,
        "daily_basket": day_stats,
        "avg_stocks_per_day": float(pd.to_numeric(d["股票数"], errors="coerce").mean()),
    }
    path = ROOT / "data" / "_tmp_mz_pool_2024_stats.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", path)


if __name__ == "__main__":
    main()
