# -*- coding: utf-8 -*-
"""新规则：开买日票均累计曲线 → 每月最大回撤。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "history_data" / "马总选股逻辑"
OUT_DIR = BASE / "过滤_排名5-20_市值lt50"


def max_drawdown(equity: np.ndarray) -> tuple[float, float, float]:
    """equity 为累计点位（可从0起）。返回 (max_dd, peak, trough) 均为相对峰值的回撤幅度（负或0）。"""
    if equity is None or len(equity) == 0:
        return 0.0, float("nan"), float("nan")
    eq = np.asarray(equity, dtype=float)
    peak = np.maximum.accumulate(eq)
    # 累计票均是「加总 pp」，回撤用峰值回落的点数（pp），不是比率
    dd = eq - peak
    i = int(np.argmin(dd))
    return float(dd[i]), float(peak[i]), float(eq[i])


def main() -> None:
    frames = []
    for folder in (BASE / "2024", BASE / "最终"):
        hits = list(folder.glob("*按票_已完成_收盘上MA10_latest.xlsx"))
        if not hits:
            continue
        df = pd.read_excel(hits[0])
        buy = pd.to_datetime(df.get("买入日"), errors="coerce")
        rk = pd.to_numeric(df["最佳板块排名"], errors="coerce")
        mvcol = "流通市值_亿_选股日" if "流通市值_亿_选股日" in df.columns else "流通市值_亿"
        mv = pd.to_numeric(df[mvcol], errors="coerce")
        mask = (rk >= 5) & (rk <= 20) & (mv < 50)
        t = pd.DataFrame(
            {
                "buy": buy.loc[mask].dt.normalize(),
                "ret": pd.to_numeric(df.loc[mask, "收益率pct"], errors="coerce"),
            }
        )
        frames.append(t)

    d = pd.concat(frames, ignore_index=True).dropna(subset=["buy", "ret"])
    day = (
        d.groupby("buy", as_index=False)
        .agg(n=("ret", "size"), day_mean=("ret", "mean"))
        .sort_values("buy")
        .reset_index(drop=True)
    )
    # 全样本累计（开买日简单加总）
    day["cum"] = day["day_mean"].cumsum()
    # 全局滚动回撤（相对历史峰值）
    peak = day["cum"].cummax()
    day["dd_from_peak"] = day["cum"] - peak

    day["ym"] = day["buy"].dt.to_period("M").astype(str)

    rows = []
    print(
        "口径: 新规则 | 权益=开买日票均简单累加(pp) | 月回撤=该月内相对月内峰值的最大回落(pp)"
    )
    print("另列: 月末相对全局峰值回撤 | 月收益=该月开买日均之和")
    print()
    print(
        "%-8s %5s %9s %9s %9s %10s %10s"
        % ("月份", "开买日", "月收益pp", "月内最大DD", "月末全局DD", "月末累计", "月内谷底")
    )

    for ym, g in day.groupby("ym", sort=True):
        g = g.reset_index(drop=True)
        # 月内局部累计（从0起，便于看当月回撤）
        local = g["day_mean"].cumsum().to_numpy()
        mdd, pk, tr = max_drawdown(local)
        month_ret = float(g["day_mean"].sum())
        end_global_dd = float(g["dd_from_peak"].iloc[-1])
        end_cum = float(g["cum"].iloc[-1])
        rows.append(
            {
                "月份": ym,
                "开买日数": len(g),
                "月收益_开买日均累加_pp": round(month_ret, 2),
                "月内最大回撤_pp": round(mdd, 2),
                "月末相对全局峰值回撤_pp": round(end_global_dd, 2),
                "月末全局累计_pp": round(end_cum, 2),
                "月内峰值_pp": round(pk, 2) if pk == pk else None,
                "月内谷底累计_pp": round(tr, 2) if tr == tr else None,
                "日均开买只数": round(float(g["n"].mean()), 2),
            }
        )
        print(
            "%-8s %5d %+9.2f %+9.2f %+9.2f %+10.2f %+10.2f"
            % (ym, len(g), month_ret, mdd, end_global_dd, end_cum, tr if tr == tr else 0.0)
        )

    # 全年最大回撤
    print()
    print("分年最大回撤（年内局部累计）:")
    day["y"] = day["buy"].dt.year
    year_rows = []
    for y, g in day.groupby("y"):
        local = g["day_mean"].cumsum().to_numpy()
        mdd, pk, tr = max_drawdown(local)
        year_rows.append(
            {
                "年": int(y),
                "开买日数": len(g),
                "年收益_pp": round(float(g["day_mean"].sum()), 2),
                "年内最大回撤_pp": round(mdd, 2),
                "年末全局累计_pp": round(float(g["cum"].iloc[-1]), 2),
                "年末全局回撤_pp": round(float(g["dd_from_peak"].iloc[-1]), 2),
            }
        )
        print(
            "  %d 收益=%+.1fpp 年内最大DD=%+.1fpp 年末累计=%+.1f 年末全局DD=%+.1f"
            % (
                int(y),
                float(g["day_mean"].sum()),
                mdd,
                float(g["cum"].iloc[-1]),
                float(g["dd_from_peak"].iloc[-1]),
            )
        )

    # 全样本
    mdd_all, _, _ = max_drawdown(day["cum"].to_numpy())
    print()
    print(
        "全样本: 累计=%+.1fpp 最大回撤=%+.1fpp (相对历史峰值)"
        % (float(day["cum"].iloc[-1]), mdd_all)
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    xlsx = OUT_DIR / "按月回撤_排名市值_三年.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        pd.DataFrame(rows).to_excel(w, sheet_name="每月回撤", index=False)
        pd.DataFrame(year_rows).to_excel(w, sheet_name="每年回撤", index=False)
        out_day = day.copy()
        out_day["buy"] = out_day["buy"].dt.strftime("%Y-%m-%d")
        out_day.to_excel(w, sheet_name="开买日权益曲线", index=False)
    print("wrote", xlsx)


if __name__ == "__main__":
    main()
