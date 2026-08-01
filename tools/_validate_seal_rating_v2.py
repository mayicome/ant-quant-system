# -*- coding: utf-8 -*-
"""在历史封单结构上验证 seal_rating_v2，并导出对照表。"""
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
OUT = HIST / f"封单评级v2验证_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

OLD_RATING = {
    "🔴 虚封高危": 1,
    "🟠 弱势封板": 2,
    "🟡 中等封板": 3,
    "🟢 强势封板": 4,
    "🔥 超强极致封板": 5,
}


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


def parse_pct(v: object) -> float:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return float("nan")
    if isinstance(v, (int, float)):
        x = float(v)
        return x * 100 if 0 < x <= 1.5 else x
    s = str(v).strip().replace("%", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return float("nan")


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
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    _daily[code6] = df
    return df


def next_ret(code6: str, seal_ymd: str) -> Optional[float]:
    df = load_daily(code6)
    if df is None or df.empty:
        return None
    seal_dt = pd.Timestamp(datetime.strptime(seal_ymd, "%Y%m%d"))
    before = df[df["date"] <= seal_dt]
    if before.empty:
        return None
    seal_close = float(before.iloc[-1]["close"] or 0)
    if seal_close <= 0:
        return None
    after = df[df["date"] > before.iloc[-1]["date"]]
    if after.empty:
        return None
    nxt = float(after.iloc[0]["close"] or 0)
    if nxt <= 0:
        return None
    return (nxt / seal_close - 1.0) * 100.0


def is_limit_up(prev: float, close: float, code6: str) -> bool:
    r = 0.20 if code6.startswith(("300", "301", "688", "689")) else 0.10
    if code6.startswith(("8", "4", "920")):
        r = 0.30
    lim = round(prev * (1 + r), 2)
    return abs(close - lim) <= 0.02 or (close / prev - 1.0) >= r * 0.99


def next_ret_lim(code6: str, seal_ymd: str) -> Tuple[Optional[float], Optional[bool]]:
    df = load_daily(code6)
    if df is None or df.empty:
        return None, None
    seal_dt = pd.Timestamp(datetime.strptime(seal_ymd, "%Y%m%d"))
    before = df[df["date"] <= seal_dt]
    if before.empty:
        return None, None
    seal_close = float(before.iloc[-1]["close"] or 0)
    if seal_close <= 0:
        return None, None
    after = df[df["date"] > before.iloc[-1]["date"]]
    if after.empty:
        return None, None
    nxt = float(after.iloc[0]["close"] or 0)
    if nxt <= 0:
        return None, None
    return (nxt / seal_close - 1.0) * 100.0, is_limit_up(seal_close, nxt, code6)


def load_pool() -> pd.DataFrame:
    rows = []
    files = []
    for p in HIST.glob("封单结构_*.xlsx"):
        if any(x in p.name for x in ("含次日", "滚动", "评估", "参数", "特征", "v2")):
            continue
        if p.name.startswith("~$"):
            continue
        m = re.search(r"(\d{8})", p.name)
        if m:
            files.append((m.group(1), p))
    files.sort()
    print(f"加载 {len(files)} 份…")
    for i, (ymd, path) in enumerate(files, 1):
        raw = pd.read_excel(path)
        code_c = find_col(raw, ["股票代码", "代码", "code"])
        name_c = find_col(raw, ["股票名称", "名称", "name"])
        amt_c = find_col(raw, ["收盘封单金额(亿)", "close_order_amount_yi"])
        hard_c = find_col(raw, ["封板硬度", "seal_hardness"])
        rush_c = find_col(raw, ["抢筹烈度", "rush_intensity"])
        stab_c = find_col(raw, ["封单稳定性", "order_stability"])
        trend_c = find_col(raw, ["封单运行趋势", "order_trend"])
        rating_c = find_col(raw, ["封单评级", "order_rating"])
        score_c = find_col(raw, ["评级分值", "rating_score"])
        if code_c is None:
            continue
        for _, r in raw.iterrows():
            code = normalize_code(r[code_c])
            if not code:
                continue
            ret, lim = next_ret_lim(code, ymd)
            old_r = str(r[rating_c]).strip() if rating_c else ""
            old_s = pd.to_numeric(r[score_c], errors="coerce") if score_c else np.nan
            if pd.isna(old_s) and old_r in OLD_RATING:
                old_s = OLD_RATING[old_r]
            v2 = rate_seal_v2(
                stability=r[stab_c] if stab_c else None,
                close_amt_yi=r[amt_c] if amt_c else None,
                hardness_pct=r[hard_c] if hard_c else None,
                rush_pct=r[rush_c] if rush_c else None,
                trend=r[trend_c] if trend_c else None,
            )
            rows.append(
                {
                    "seal_date": ymd,
                    "code": code,
                    "name": str(r[name_c]).strip() if name_c else "",
                    "old_rating": old_r,
                    "old_score": old_s,
                    "rating_v2": v2["rating_v2"],
                    "rating_v2_score": v2["rating_v2_score"],
                    "rating_v2_reason": v2["rating_v2_reason"],
                    "amt": v2["amt_yi"],
                    "hardness": v2["hardness_pct"],
                    "rush": v2["rush_pct"],
                    "stability": v2["stability_norm"],
                    "next_day_ret": ret,
                    "next_day_limit_up": lim,
                }
            )
        if i % 20 == 0 or i == len(files):
            print(f"  {i}/{len(files)}")
    return pd.DataFrame(rows).dropna(subset=["next_day_ret"])


def summarize(df: pd.DataFrame, score_col: str, label_col: str, name: str) -> pd.DataFrame:
    rows = []
    for sc in sorted(df[score_col].dropna().unique()):
        g = df[df[score_col] == sc]
        rets = g["next_day_ret"]
        lims = g["next_day_limit_up"].dropna()
        lab = g[label_col].iloc[0] if len(g) else str(sc)
        rows.append(
            {
                "体系": name,
                "分": int(sc),
                "标签": lab,
                "n": len(g),
                "覆盖%": len(g) / len(df) * 100,
                "均涨%": float(rets.mean()),
                "中位%": float(rets.median()),
                "胜率%": float((rets > 0).mean() * 100),
                "大亏率%": float((rets < -5).mean() * 100),
                "涨停率%": float(lims.mean() * 100) if len(lims) else np.nan,
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    df = load_pool()
    print(f"有效 {len(df)} 笔 / {df.seal_date.nunique()} 日")

    new_s = summarize(df, "rating_v2_score", "rating_v2", "v2新评级")
    old_s = summarize(df.dropna(subset=["old_score"]), "old_score", "old_rating", "旧评级")
    print("\n======== v2 新评级 ========")
    print(new_s.to_string(index=False))
    print("\n======== 旧评级 ========")
    print(old_s.to_string(index=False))

    rho_new = df["rating_v2_score"].corr(df["next_day_ret"], method="spearman")
    rho_old = df.dropna(subset=["old_score"])["old_score"].corr(
        df.dropna(subset=["old_score"])["next_day_ret"], method="spearman"
    )
    print(f"\nSpearman 新={rho_new:+.3f} 旧={rho_old:+.3f}")

    # 高低差
    hi_n = df[df.rating_v2_score >= 3]
    mid_n = df[df.rating_v2_score == 2]
    lo_n = df[df.rating_v2_score <= 1]
    hi_o = df[df.old_score >= 4]
    lo_o = df[df.old_score <= 2]
    print(
        f"v2 锁板强 vs 波动弱: "
        f"{hi_n.next_day_ret.mean():+.2f}% (n={len(hi_n)}) vs "
        f"{lo_n.next_day_ret.mean():+.2f}% (n={len(lo_n)}) "
        f"差={hi_n.next_day_ret.mean()-lo_n.next_day_ret.mean():+.2f}pp"
    )
    print(
        f"v2 跟板观: {mid_n.next_day_ret.mean():+.2f}% (n={len(mid_n)})"
    )
    print(
        f"旧 强势+ vs 弱势-: "
        f"{hi_o.next_day_ret.mean():+.2f}% (n={len(hi_o)}) vs "
        f"{lo_o.next_day_ret.mean():+.2f}% (n={len(lo_o)}) "
        f"差={hi_o.next_day_ret.mean()-lo_o.next_day_ret.mean():+.2f}pp"
    )

    # 按月
    month_rows = []
    for m, g in df.groupby(df.seal_date.str[:6]):
        for sc, lab in RATING_V2_LABELS.items():
            sub = g[g.rating_v2_score == sc]
            if len(sub) < 5:
                continue
            month_rows.append(
                {
                    "月份": m,
                    "分": sc,
                    "标签": lab,
                    "n": len(sub),
                    "均涨%": sub.next_day_ret.mean(),
                    "胜率%": (sub.next_day_ret > 0).mean() * 100,
                }
            )
    by_month = pd.DataFrame(month_rows)
    print("\n======== v2 按月均涨 ========")
    if len(by_month):
        print(
            by_month.pivot_table(index="标签", columns="月份", values="均涨%", aggfunc="first")
            .reindex(list(RATING_V2_LABELS.values()))
            .to_string()
        )

    # 混淆：旧强势落在 v2 哪档
    print("\n======== 旧强势+ 落在 v2 ========")
    print(df[df.old_score >= 4].rating_v2.value_counts())
    print("\n======== v2锁板强 的旧评级 ========")
    print(df[df.rating_v2_score == 3].old_rating.value_counts())

    with pd.ExcelWriter(OUT, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="明细", index=False)
        new_s.to_excel(w, sheet_name="v2按档", index=False)
        old_s.to_excel(w, sheet_name="旧按档", index=False)
        by_month.to_excel(w, sheet_name="v2按月", index=False)
        pd.DataFrame(
            [
                {"指标": "Spearman_v2", "值": rho_new},
                {"指标": "Spearman_旧", "值": rho_old},
                {"指标": "样本n", "值": len(df)},
            ]
        ).to_excel(w, sheet_name="总览", index=False)
    print(f"\n已写出 {OUT}")


if __name__ == "__main__":
    main()
