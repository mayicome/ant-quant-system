"""诊断 7 月批量回测为何无买卖信号。"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "strategy_generator_app"))
from repo_path import ensure_paths

ensure_paths()

from strategy_generator_app.backtest.data_provider import (  # noqa: E402
    get_historical_prices_for_morning,
    get_prices_at_time,
    get_today_high_low_at_time,
)
from strategy_generator_app.main import _diagnose_breakthrough_5day_10m  # noqa: E402
from strategy_generator_app.strategy_runner import run_user_strategy  # noqa: E402

SEL = ROOT / "history_data" / "回测七月" / "选股结果_涨停的第P到N天_7-01_7-08.xls"
STRAT = ROOT / "strategy_generator_app" / "config" / "strategies" / "strategy_508e9237.json"


def _norm_code(x) -> str:
    s = str(x).replace(".0", "").strip()
    return s.zfill(6) if s.isdigit() else s


def _enrich_prices(codes, trade_d, get_name, prices):
    try:
        atp = get_prices_at_time(codes, trade_d, "09:40")
        hl = get_today_high_low_at_time(codes, trade_d, "09:40")
        for c in codes:
            if c not in prices:
                continue
            if c in atp and float(atp[c]) > 0:
                prices[c]["current"] = float(atp[c])
                prices[c]["最新价"] = float(atp[c])
            if c in hl:
                prices[c]["今日最高"] = hl[c].get("今日最高", 0)
                prices[c]["今日最低"] = hl[c].get("今日最低", 0)
            if not prices[c].get("今日最高") or not prices[c].get("今日最低"):
                op = prices[c].get("今开盘") or prices[c].get("current") or 0
                if op and float(op) > 0:
                    prices[c]["今日最高"] = round(float(op), 2)
                    prices[c]["今日最低"] = round(float(op), 2)
    except Exception as e:
        print("  enrich err:", e)
    return prices


def main():
    with open(STRAT, encoding="utf-8") as f:
        strat = json.load(f)
    code_str = strat["strategy_code"]
    params = strat.get("strategy_params") or {}

    df = pd.read_excel(SEL)
    dc = next(c for c in df.columns if "选股日" in str(c))
    cc = next(c for c in df.columns if "股票" in str(c) and "代码" in str(c))

    get_name = lambda c: ""

    for sel_str in ["2026-07-01", "2026-07-02", "2026-07-03"]:
        sub = df[df[dc].astype(str).str[:10] == sel_str]
        codes = list(dict.fromkeys(_norm_code(x) for x in sub[cc]))
        trade_d = date.fromisoformat(sel_str) + timedelta(days=1)
        # skip weekend: 7-03 -> 7-04 Fri, 7-04+1=7-05 sat -> need calendar
        # use mapping from user output
        trade_map = {
            "2026-07-01": date(2026, 7, 2),
            "2026-07-02": date(2026, 7, 3),
            "2026-07-03": date(2026, 7, 6),
        }
        trade_d = trade_map[sel_str]

        prices = get_historical_prices_for_morning(codes, trade_d, get_name)
        prices = _enrich_prices(codes, trade_d, get_name, prices)
        valid = [c for c in codes if float(prices.get(c, {}).get("current") or 0) > 0]
        intents = run_user_strategy(code_str, valid, prices, get_name, {}, params)

        reasons = Counter()
        for c in valid:
            reasons[_diagnose_breakthrough_5day_10m(c, prices.get(c, {}), params).split("（")[0]] += 1

        print(f"\n=== 选股日 {sel_str} -> 回测 {trade_d} pool={len(codes)} valid={len(valid)} intents={len(intents)} ===")
        for reason, cnt in reasons.most_common(8):
            print(f"  {cnt:4d}  {reason}")
        if intents:
            print("  intent codes:", [i["stock_code"] for i in intents[:10]])


if __name__ == "__main__":
    main()
