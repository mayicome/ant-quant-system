# -*- coding: utf-8 -*-
"""Export Cond1+2+3 (MA5<MA10<MA20) buy/sell/summary from Cond1+2 files."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

BASE = Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门")

CONFIGS = [
    {
        "name": "anytag",
        "summary": "开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_各日选股收益汇总.xlsx",
        "buy": "开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_回测成交明细_买入.csv",
        "sell": "开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_回测成交明细_卖出.csv",
        "prefix": "开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_条件三MA5lt10lt20",
        "expect": 253,
    },
    {
        "name": "besttest",
        "summary": "besttest_开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_各日选股收益汇总.xlsx",
        "buy": "besttest_开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_回测成交明细_买入.csv",
        "sell": "besttest_开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_回测成交明细_卖出.csv",
        "prefix": "besttest_开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_条件三MA5lt10lt20",
        "expect": 215,
    },
]


def norm_code(c) -> str:
    s = re.sub(r"\D", "", str(c or ""))
    return s.zfill(6)[-6:] if s else ""


def make_key(df: pd.DataFrame) -> list[tuple[str, str]]:
    sel = pd.to_datetime(df["选股日"]).dt.strftime("%Y-%m-%d")
    code = df["代码"].map(norm_code)
    return list(zip(sel, code))


def main() -> None:
    for cfg in CONFIGS:
        sm = pd.read_excel(BASE / cfg["summary"])
        for c in ["MA5", "MA10", "MA20", "收益率pct"]:
            sm[c] = pd.to_numeric(sm[c], errors="coerce")
        has = sm["MA5"].notna() & sm["MA10"].notna() & sm["MA20"].notna()
        cond3 = has & (sm["MA5"] < sm["MA10"]) & (sm["MA10"] < sm["MA20"])
        sm3 = sm.loc[cond3].copy()
        keys = set(make_key(sm3))

        buy = pd.read_csv(BASE / cfg["buy"], encoding="utf-8-sig")
        sell = pd.read_csv(BASE / cfg["sell"], encoding="utf-8-sig")
        buy_m = buy.loc[[k in keys for k in make_key(buy)]].copy()
        sell_m = sell.loc[[k in keys for k in make_key(sell)]].copy()

        out_buy = BASE / f"{cfg['prefix']}_回测成交明细_买入.csv"
        out_sell = BASE / f"{cfg['prefix']}_回测成交明细_卖出.csv"
        out_sum = BASE / f"{cfg['prefix']}_各日选股收益汇总.xlsx"

        buy_m.to_csv(out_buy, index=False, encoding="utf-8-sig")
        sell_m.to_csv(out_sell, index=False, encoding="utf-8-sig")
        sm3.to_excel(out_sum, index=False)

        r = pd.to_numeric(sm3["收益率pct"], errors="coerce").dropna()
        mean = float(r.mean()) if len(r) else float("nan")
        win = float((r > 0).mean() * 100) if len(r) else float("nan")
        ok = len(buy_m) == cfg["expect"] == len(sm3)

        print("=" * 60)
        print(cfg["name"], "Cond1+2+3")
        print(
            f"  summary={len(sm3)} buy={len(buy_m)} sell={len(sell_m)} "
            f"expect={cfg['expect']} match={ok}"
        )
        print(f"  mean={mean:+.3f}% win={win:.1f}%")
        print(f"  {out_buy}")
        print(f"  {out_sell}")
        print(f"  {out_sum}")


if __name__ == "__main__":
    main()
