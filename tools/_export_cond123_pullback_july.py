# -*- coding: utf-8 -*-
"""Export Cond1+2+3 for 高开回踩 (pullback) July anytag / besttest.

Cond2 adaptation (documented):
  Open-clip Cond2 = 开盘相对买入日MA5 ∈ [0%, 2%].
  回踩 fills via 单点买入 (single_buy), not open — 开盘相对 is absent / not the entry price.
  Adapted Cond2 = 成交相对买入日MA5_pct ∈ [0%, 2%] (buy fill vs buy-day morning MA5).

Cond1: 近10日涨停=0 AND 均线差占比 ∈ [0.5%, 2%]  (stored as fraction 0.005..0.02)
Cond3: MA5 < MA10 < MA20
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门")

# Cond2 field used for pullback (≠ open-clip 开盘相对)
COND2_COL = "成交相对买入日MA5_pct"
COND2_LO, COND2_HI = 0.0, 2.0  # percent
MA_GAP_LO, MA_GAP_HI = 0.005, 0.02  # fraction == 0.5%..2%

PREFIX_CORE = (
    "回踩_条件一_无涨停_均线差0.5to2_"
    "条件二成交相对MA5满足0to2_"
    "条件三MA5lt10lt20"
)

CONFIGS = [
    {
        "name": "anytag",
        "summary": "各日选股收益汇总.xlsx",
        "buy": "回测成交明细_买入.csv",
        "sell": "回测成交明细_卖出.csv",
        "prefix": PREFIX_CORE,
    },
    {
        "name": "besttest",
        "summary": "besttest_各日选股收益汇总.xlsx",
        "buy": "besttest_回测成交明细_买入.csv",
        "sell": "besttest_回测成交明细_卖出.csv",
        "prefix": f"besttest_{PREFIX_CORE}",
    },
]


def norm_code(c) -> str:
    s = re.sub(r"\D", "", str(c or ""))
    return s.zfill(6)[-6:] if s else ""


def make_key(df: pd.DataFrame) -> list[tuple[str, str]]:
    sel = pd.to_datetime(df["选股日"]).dt.strftime("%Y-%m-%d")
    code = df["代码"].map(norm_code)
    return list(zip(sel, code))


def is_pullback(tip: pd.Series) -> pd.Series:
    t = tip.astype(str)
    return t.str.contains("单点买入")


def apply_cond123(sm: pd.DataFrame) -> pd.DataFrame:
    zt = pd.to_numeric(sm["最近10个交易日内的涨停板数量"], errors="coerce")
    magap = pd.to_numeric(sm["均线差占比"], errors="coerce")
    fill_ma = pd.to_numeric(sm[COND2_COL], errors="coerce")
    m5 = pd.to_numeric(sm["MA5"], errors="coerce")
    m10 = pd.to_numeric(sm["MA10"], errors="coerce")
    m20 = pd.to_numeric(sm["MA20"], errors="coerce")
    c1 = (zt == 0) & magap.between(MA_GAP_LO, MA_GAP_HI)
    c2 = fill_ma.between(COND2_LO, COND2_HI)
    c3 = m5.notna() & m10.notna() & m20.notna() & (m5 < m10) & (m10 < m20)
    out = sm.loc[c1 & c2 & c3].copy()
    out["Cond2字段"] = COND2_COL
    out["Cond2说明"] = "回踩用成交价相对买入日早盘MA5；非开盘相对"
    return out


def main() -> None:
    print("Cond2 adaptation: open-clip uses 开盘相对买入日MA5;")
    print(f"  回踩 uses {COND2_COL} ∈ [{COND2_LO}%, {COND2_HI}%] (fill vs morning MA5).")
    print()

    for cfg in CONFIGS:
        sm = pd.read_excel(BASE / cfg["summary"])
        tip = sm["触发信息"].astype(str)
        sm_pb = sm.loc[is_pullback(tip)].copy()
        sm3 = apply_cond123(sm_pb)
        keys = set(make_key(sm3))

        buy = pd.read_csv(BASE / cfg["buy"], encoding="utf-8-sig")
        sell = pd.read_csv(BASE / cfg["sell"], encoding="utf-8-sig")
        # buys: filter 单点买入 then Cond123 keys; sells have no buy-branch tip → keys only
        buy_pb = buy.loc[is_pullback(buy["触发信息"])].copy()
        buy_m = buy_pb.loc[[k in keys for k in make_key(buy_pb)]].copy()
        sell_m = sell.loc[[k in keys for k in make_key(sell)]].copy()

        out_buy = BASE / f"{cfg['prefix']}_回测成交明细_买入.csv"
        out_sell = BASE / f"{cfg['prefix']}_回测成交明细_卖出.csv"
        out_sum = BASE / f"{cfg['prefix']}_各日选股收益汇总.xlsx"

        buy_m.to_csv(out_buy, index=False, encoding="utf-8-sig")
        sell_m.to_csv(out_sell, index=False, encoding="utf-8-sig")
        sm3.to_excel(out_sum, index=False)

        r = pd.to_numeric(sm3["收益率pct"], errors="coerce").dropna()
        mean = float(r.mean()) if len(r) else float("nan")
        med = float(r.median()) if len(r) else float("nan")
        win = float((r > 0).mean() * 100) if len(r) else float("nan")

        print("=" * 60)
        print(cfg["name"], "回踩 Cond1+2(成交相对MA5)+3")
        print(
            f"  pool_gap={len(sm_pb)} summary={len(sm3)} "
            f"buy={len(buy_m)} sell={len(sell_m)} "
            f"key_match={len(buy_m)==len(sm3)}"
        )
        print(f"  mean={mean:+.3f}% median={med:+.3f}% win={win:.1f}%")
        print(f"  {out_buy.name}")
        print(f"  {out_sell.name}")
        print(f"  {out_sum.name}")

        # layer funnel
        zt = pd.to_numeric(sm_pb["最近10个交易日内的涨停板数量"], errors="coerce")
        magap = pd.to_numeric(sm_pb["均线差占比"], errors="coerce")
        fill_ma = pd.to_numeric(sm_pb[COND2_COL], errors="coerce")
        m5 = pd.to_numeric(sm_pb["MA5"], errors="coerce")
        m10 = pd.to_numeric(sm_pb["MA10"], errors="coerce")
        m20 = pd.to_numeric(sm_pb["MA20"], errors="coerce")
        c1 = (zt == 0) & magap.between(MA_GAP_LO, MA_GAP_HI)
        c2 = fill_ma.between(COND2_LO, COND2_HI)
        c3 = m5.notna() & m10.notna() & m20.notna() & (m5 < m10) & (m10 < m20)
        print(
            f"  funnel: all={len(sm_pb)} C1={int(c1.sum())} "
            f"C1+2={int((c1&c2).sum())} C1+2+3={int((c1&c2&c3).sum())}"
        )


if __name__ == "__main__":
    main()
