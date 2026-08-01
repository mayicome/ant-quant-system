# -*- coding: utf-8 -*-
"""
封单评级 v2 —— 持仓处置口径验证。

目的：评级给「手里已有该涨停股」的人看，提示次日：
  - 波动弱 → 倾向尽早出手 / 逢高就卖
  - 锁板强 → 可先安心拿，尾盘再定

验证（用 daily_cache 次日 OHLC，相对涨停日收盘）：
  - 开盘涨跌、收盘涨跌、午前最低
  - 持有到收盘相对「开盘就卖」的超额 = 收盘涨跌 - 开盘涨跌
  - 「早卖更优」比例 = 开盘价优于收盘价的占比
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(r"d:\蚂蚁量化系统")
sys.path.insert(0, str(ROOT / "tools"))
from seal_rating_v2 import RATING_V2_LABELS, rate_seal_v2  # noqa: E402

HIST = ROOT / "history_data"
DAILY = ROOT / "data" / "daily_cache"
OUT = HIST / f"封单评级v2_持仓处置验证_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"


def normalize_code(v: object) -> str:
    s = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", str(v or "").strip().upper())
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else ""


def find_col(df: pd.DataFrame, aliases: List[str]) -> Optional[str]:
    lower = {str(c).strip().lower(): c for c in df.columns}
    for a in aliases:
        if a.strip().lower() in lower:
            return lower[a.strip().lower()]
    for a in aliases:
        k = a.strip().lower()
        for c in df.columns:
            if k in str(c).strip().lower():
                return c
    return None


_daily: Dict[str, Optional[pd.DataFrame]] = {}


def load_daily(code6: str) -> Optional[pd.DataFrame]:
    if code6 in _daily:
        return _daily[code6]
    p = None
    for suf in (".SZ", ".SH", ".BJ"):
        cand = DAILY / f"{code6}{suf}.csv"
        if cand.is_file():
            p = cand
            break
    if p is None:
        hits = list(DAILY.glob(f"{code6}.*.csv"))
        p = hits[0] if hits else None
    if p is None:
        _daily[code6] = None
        return None
    df = pd.read_csv(p)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["date"]).sort_values("date")
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    _daily[code6] = df
    return df


def next_day_ohlc(
    code6: str, seal_ymd: str
) -> Optional[Tuple[float, float, float, float, float, str]]:
    """返回 (seal_close, open, high, low, close, next_ymd)。"""
    df = load_daily(code6)
    if df is None or df.empty:
        return None
    seal_dt = pd.Timestamp(datetime.strptime(seal_ymd, "%Y%m%d"))
    before = df[df["date"] <= seal_dt]
    if before.empty:
        return None
    seal_row = before.iloc[-1]
    seal_close = float(seal_row["close"] or 0)
    if seal_close <= 0:
        return None
    after = df[df["date"] > seal_row["date"]]
    if after.empty:
        return None
    nxt = after.iloc[0]
    o, h, l, c = float(nxt["open"] or 0), float(nxt["high"] or 0), float(nxt["low"] or 0), float(
        nxt["close"] or 0
    )
    if min(o, h, l, c) <= 0:
        return None
    return seal_close, o, h, l, c, nxt["date"].strftime("%Y%m%d")


def list_seal_files() -> List[Tuple[str, Path]]:
    out = []
    for p in HIST.glob("封单结构_*.xlsx"):
        if any(x in p.name for x in ("含次日", "滚动", "评估", "参数", "特征", "v2", "持仓")):
            continue
        if p.name.startswith("~$"):
            continue
        m = re.search(r"(\d{8})", p.name)
        if m:
            out.append((m.group(1), p))
    return sorted(out)


def load_pool() -> pd.DataFrame:
    rows = []
    files = list_seal_files()
    print(f"加载 {len(files)} 份封单结构…")
    for i, (ymd, path) in enumerate(files, 1):
        raw = pd.read_excel(path)
        code_c = find_col(raw, ["股票代码", "代码", "code"])
        name_c = find_col(raw, ["股票名称", "名称", "name"])
        amt_c = find_col(raw, ["收盘封单金额(亿)", "close_order_amount_yi"])
        hard_c = find_col(raw, ["封板硬度", "seal_hardness"])
        rush_c = find_col(raw, ["抢筹烈度", "rush_intensity"])
        stab_c = find_col(raw, ["封单稳定性", "order_stability"])
        trend_c = find_col(raw, ["封单运行趋势", "order_trend"])
        if code_c is None:
            continue
        for _, r in raw.iterrows():
            code = normalize_code(r[code_c])
            if not code:
                continue
            ohlc = next_day_ohlc(code, ymd)
            if ohlc is None:
                continue
            seal_close, o, h, l, c, nxt = ohlc
            v2 = rate_seal_v2(
                stability=r[stab_c] if stab_c else None,
                close_amt_yi=r[amt_c] if amt_c else None,
                hardness_pct=r[hard_c] if hard_c else None,
                rush_pct=r[rush_c] if rush_c else None,
                trend=r[trend_c] if trend_c else None,
            )
            open_ret = (o / seal_close - 1.0) * 100.0
            close_ret = (c / seal_close - 1.0) * 100.0
            low_ret = (l / seal_close - 1.0) * 100.0
            high_ret = (h / seal_close - 1.0) * 100.0
            # 逢高卖近似：能摸到次日最高价的一半溢价（偏乐观上界，仅作参考）
            mid_high_ret = ((o + h) / 2.0 / seal_close - 1.0) * 100.0
            hold_vs_open = close_ret - open_ret  # >0 拿到收盘优于开盘卖
            early_better = open_ret > close_ret  # 开盘卖优于拿到收盘
            # 开盘就砸（相对昨收）
            gap_down = open_ret < -1.0
            # 盘中相对开盘继续走弱
            weak_session = (c / o - 1.0) * 100.0 < -1.0
            rows.append(
                {
                    "seal_date": ymd,
                    "next_date": nxt,
                    "code": code,
                    "name": str(r[name_c]).strip() if name_c else "",
                    "rating_v2": v2["rating_v2"],
                    "rating_v2_score": v2["rating_v2_score"],
                    "open_ret": open_ret,
                    "close_ret": close_ret,
                    "low_ret": low_ret,
                    "high_ret": high_ret,
                    "mid_high_ret": mid_high_ret,
                    "hold_vs_open": hold_vs_open,
                    "early_better": early_better,
                    "gap_down": gap_down,
                    "weak_session": weak_session,
                    "intraday_oc": (c / o - 1.0) * 100.0,
                }
            )
        if i % 20 == 0 or i == len(files):
            print(f"  {i}/{len(files)} rows={len(rows)}")
    return pd.DataFrame(rows)


def tier_stats(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sc in sorted(df["rating_v2_score"].unique()):
        g = df[df["rating_v2_score"] == sc]
        lab = RATING_V2_LABELS.get(int(sc), str(sc))
        rows.append(
            {
                "档位": lab,
                "分": int(sc),
                "n": len(g),
                "开盘均%": g["open_ret"].mean(),
                "收盘均%": g["close_ret"].mean(),
                "最低均%": g["low_ret"].mean(),
                "持有相对开盘卖(pp)": g["hold_vs_open"].mean(),
                "中位持有相对开盘": g["hold_vs_open"].median(),
                "早卖更优%": g["early_better"].mean() * 100,
                "持有更优%": (g["hold_vs_open"] > 0).mean() * 100,
                "开盘低开<-1%%": g["gap_down"].mean() * 100,
                "开收走弱<-1%%": g["weak_session"].mean() * 100,
                "午前深砸<-5%%": (g["low_ret"] < -5).mean() * 100,
                # 三种处置的平均结果（相对涨停收盘）
                "若开盘卖均%": g["open_ret"].mean(),
                "若逢高中位卖均%": g["mid_high_ret"].mean(),
                "若拿到收盘均%": g["close_ret"].mean(),
                # 错误处置的代价：弱档却拿到收盘 vs 开盘卖
                "拿收盘相对开盘卖的吃亏次数占比%": (g["hold_vs_open"] < 0).mean() * 100,
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    df = load_pool()
    print(f"\n有效样本 {len(df)}，次日覆盖日 {df['next_date'].nunique()}")

    by_tier = tier_stats(df)
    print("\n======== 持仓处置口径：分档 ========")
    print(by_tier.to_string(index=False))

    # 核心对照：锁板强 vs 波动弱
    strong = df[df.rating_v2_score == 3]
    weak = df[df.rating_v2_score == 1]
    mid = df[df.rating_v2_score == 2]
    print("\n======== 核心对照 ========")
    print(
        f"早卖更优%%: 锁板强 {strong.early_better.mean()*100:.1f}% | "
        f"跟板观 {mid.early_better.mean()*100:.1f}% | "
        f"波动弱 {weak.early_better.mean()*100:.1f}%"
    )
    print(
        f"持有相对开盘卖(均pp): 强 {strong.hold_vs_open.mean():+.2f} | "
        f"观 {mid.hold_vs_open.mean():+.2f} | "
        f"弱 {weak.hold_vs_open.mean():+.2f}"
    )
    print(
        f"若一律开盘卖: 强 {strong.open_ret.mean():+.2f}% / "
        f"弱 {weak.open_ret.mean():+.2f}%"
    )
    print(
        f"若一律拿到收盘: 强 {strong.close_ret.mean():+.2f}% / "
        f"弱 {weak.close_ret.mean():+.2f}%"
    )
    print(
        f"策略差(拿收盘-开盘卖): 强 {strong.hold_vs_open.mean():+.2f}pp / "
        f"弱 {weak.hold_vs_open.mean():+.2f}pp  "
        f"→ 强弱差 {strong.hold_vs_open.mean()-weak.hold_vs_open.mean():+.2f}pp"
    )

    # 按月：持有相对开盘卖
    month_rows = []
    for m, g in df.groupby(df.seal_date.astype(str).str[:6]):
        for sc, lab in RATING_V2_LABELS.items():
            sub = g[g.rating_v2_score == sc]
            if len(sub) < 8:
                continue
            month_rows.append(
                {
                    "月份": m,
                    "档位": lab,
                    "n": len(sub),
                    "持有相对开盘卖": sub.hold_vs_open.mean(),
                    "早卖更优%": sub.early_better.mean() * 100,
                    "开盘均%": sub.open_ret.mean(),
                    "收盘均%": sub.close_ret.mean(),
                    "最低均%": sub.low_ret.mean(),
                }
            )
    by_month = pd.DataFrame(month_rows)
    print("\n======== 按月：持有相对开盘卖(pp) ========")
    if len(by_month):
        print(
            by_month.pivot_table(index="档位", columns="月份", values="持有相对开盘卖")
            .reindex(list(RATING_V2_LABELS.values()))
            .round(2)
            .to_string()
        )
        print("\n======== 按月：早卖更优% ========")
        print(
            by_month.pivot_table(index="档位", columns="月份", values="早卖更优%")
            .reindex(list(RATING_V2_LABELS.values()))
            .round(1)
            .to_string()
        )

    # 话术友好的决策表
    advice = []
    for _, r in by_tier.iterrows():
        if r["分"] == 3:
            tip = "更适合先拿着看，尾盘再定；开盘卖往往不是最优"
        elif r["分"] == 2:
            tip = "可逢高减；不必一开盘就清，也不宜死拿到尾"
        else:
            tip = "更常出现开盘卖优于拿到收盘；倾向尽早/逢高出手"
        advice.append(
            {
                "档位": r["档位"],
                "建议倾向": tip,
                "早卖更优%": round(r["早卖更优%"], 1),
                "持有相对开盘卖(pp)": round(r["持有相对开盘卖(pp)"], 2),
                "开盘低开<-1%占比": round(r["开盘低开<-1%%"], 1),
                "午前深砸<-5%占比": round(r["午前深砸<-5%%"], 1),
            }
        )
    advice_df = pd.DataFrame(advice)
    print("\n======== 给持仓人的倾向（由数据支撑） ========")
    print(advice_df.to_string(index=False))

    with pd.ExcelWriter(OUT, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="明细", index=False)
        by_tier.to_excel(w, sheet_name="分档汇总", index=False)
        by_month.to_excel(w, sheet_name="按月", index=False)
        advice_df.to_excel(w, sheet_name="话术倾向", index=False)
    print(f"\n已写出: {OUT}")


if __name__ == "__main__":
    main()
