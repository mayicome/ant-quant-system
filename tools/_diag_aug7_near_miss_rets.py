# -*- coding: utf-8 -*-
"""8/7 近失票：过了 Elig+RS 后，按均线差/空头排列分层看次日开→收。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.daily_cache_reader import load_daily_from_cache  # noqa: E402
from utils.eastmoney_board_rank_ctx import load_em_board_hot_map  # noqa: E402

ASOF = date(2026, 8, 7)
RULE_GLOB = "*de4a81d4*.json"


def load_select():
    p = list((ROOT / "data" / "sector_rules").glob(RULE_GLOB))[0]
    ns = {}
    exec(compile(json.loads(p.read_text(encoding="utf-8"))["code"], str(p), "exec"), ns, ns)
    return ns["select"], ns


def next_td(dd: pd.DataFrame, d: date):
    if dd is None or dd.empty:
        return None
    x = dd[dd["date"] > d]
    if x.empty:
        return None
    return x.iloc[0]


def main():
    select_fn, ns = load_select()
    em = load_em_board_hot_map(
        ASOF, top_n=50, rs_top_k=50, min_members=10, arms=["today"], elig_bands=[(1, 40)]
    )
    hits = em.get("today_code_hits") or {}
    rows = []
    for c6, hit in hits.items():
        if not isinstance(hit, dict):
            continue
        c6 = str(c6).zfill(6)
        dd = load_daily_from_cache(c6)
        ok, extra = select_fn(c6, "", [], dd, ASOF, {"em_board_hot": em})
        skip = "" if ok else str((extra or {}).get("_skip") or "")
        # 只关心进了 Elig+RS 的票（含最终通过/卡在均线条件）
        early = any(
            k in skip
            for k in (
                "序位不在",
                "RS",
                "无东财",
                "不在今日",
                "无热门",
                "缺合格",
                "无合格",
            )
        )
        if early and not ok:
            continue
        closes = []
        if dd is not None and not dd.empty:
            sub = dd[dd["date"] <= ASOF]
            closes = [float(x) for x in sub["close"].tolist() if float(x) > 0]
        ma5 = float(np.mean(closes[-5:])) if len(closes) >= 5 else None
        ma10 = float(np.mean(closes[-10:])) if len(closes) >= 10 else None
        ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else None
        gap = None
        if ma5 and ma10 and min(ma5, ma10) > 0:
            gap = abs(ma5 - ma10) / min(ma5, ma10)
        bear = (
            ma5 is not None
            and ma10 is not None
            and ma20 is not None
            and ma5 < ma10 < ma20
        )
        gap_ok = gap is not None and 0.005 <= gap <= 0.02
        nxt = next_td(dd, ASOF)
        o2c = None
        o_gap = None
        if nxt is not None and len(closes) >= 1:
            base = closes[-1]
            o = float(nxt["open"] or 0)
            c = float(nxt["close"] or 0)
            if base > 0 and o > 0 and c > 0:
                o_gap = (o / base - 1) * 100
                o2c = (c / o - 1) * 100
        bucket = "通过" if ok else (
            "仅差空头(已过均线差)" if (gap_ok and not bear) else (
                "空头但均线差不符" if (bear and not gap_ok) else (
                    "均线差+空头皆不符" if (not gap_ok and not bear) else skip
                )
            )
        )
        rows.append(
            dict(
                code=c6,
                ok=ok,
                skip=skip,
                bucket=bucket,
                gap=gap,
                bear=bear,
                gap_ok=gap_ok,
                o_gap=o_gap,
                o2c=o2c,
            )
        )

    df = pd.DataFrame(rows)
    print(f"Elig+RS后样本: {len(df)}  通过: {int(df['ok'].sum())}")
    for b, g in df.groupby("bucket"):
        r = g["o2c"].dropna()
        og = g["o_gap"].dropna()
        print(
            f"{b}: n={len(g)} 有次日={len(r)} "
            f"开盘均={og.mean():+.2f}% 开→收均={r.mean():+.2f}% "
            f"开收胜率={(r>0).mean()*100 if len(r) else float('nan'):.0f}%"
        )
    # 若捞「仅差空头」：用开盘夹档近似？这里只报裸开→收
    near = df[df["bucket"] == "仅差空头(已过均线差)"]
    print("\n仅差空头 开→收分位:", near["o2c"].quantile([0.25, 0.5, 0.75]).to_dict() if len(near) else {})
    bear_gap = df[df["bucket"] == "空头但均线差不符"]
    print("空头但均线差不符 开→收分位:", bear_gap["o2c"].quantile([0.25, 0.5, 0.75]).to_dict() if len(bear_gap) else {})


if __name__ == "__main__":
    main()
