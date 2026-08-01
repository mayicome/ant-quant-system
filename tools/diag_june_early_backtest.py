# -*- coding: utf-8 -*-
"""诊断 6 月初批量回测为何无信号。"""
import json
import os
import sys
from collections import defaultdict
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "strategy_generator_app")
sys.path.insert(0, APP)
os.chdir(APP)
from repo_path import ensure_paths

ensure_paths()

import pandas as pd


def norm_code(v):
    s = str(v).strip().split(".")[0]
    return s.zfill(6) if s.isdigit() else s[:6]


def parse_date(v):
    if isinstance(v, date):
        return v
    return pd.to_datetime(v).date()


def main():
    sel_dir = os.path.join(ROOT, "history_data", "回测七月")
    sel_file = None
    prefer = os.environ.get("SEL_FILE", "").strip()
    if prefer and os.path.isfile(prefer):
        sel_file = prefer
    for fn in os.listdir(sel_dir):
        if sel_file:
            break
        if fn.endswith("6-01_6-30.xls") and "测试" in fn and prefer == "test":
            sel_file = os.path.join(sel_dir, fn)
            break
    if not sel_file:
        for fn in os.listdir(sel_dir):
            if fn.endswith("6-01_6-30.xls") and "全" in fn:
                sel_file = os.path.join(sel_dir, fn)
                break
    if not sel_file:
        for fn in os.listdir(sel_dir):
            if fn.endswith("6-01_6-30.xls"):
                sel_file = os.path.join(sel_dir, fn)
                break
    print("selection:", sel_file)

    df = pd.read_excel(sel_file)
    date_col = next((c for c in df.columns if "选股日" in str(c)), None)
    code_col = next(
        (c for c in df.columns if "代码" in str(c) or "股票代码" in str(c)), None
    )
    by_day = defaultdict(list)
    for _, row in df.iterrows():
        d = parse_date(row[date_col])
        c = norm_code(row[code_col])
        if c and d.month == 6 and d.year == 2026:
            by_day[d].append(c)
    for d in sorted(by_day):
        print(f"  sel {d}: {len(by_day[d])} codes")

    strat_path = os.path.join(APP, "config", "strategies", "strategy_508e9237.json")
    with open(strat_path, encoding="utf-8") as f:
        strat = json.load(f)
    code_str = strat["strategy_code"]
    print("strategy:", strat["name"])
    if "for x in (ma20):" in code_str.replace(" ", ""):
        print("WARN: strategy still has (ma20) without comma -> TypeError")

    from strategy_generator_app.backtest.data_provider import (
        get_historical_prices_for_morning,
        get_today_high_low_at_time,
        get_prices_at_time,
        load_tick_data_for_date,
    )
    from strategy_generator_app.trading_calendar import backtest_window_from_selection_day
    from strategy_generator_app.strategy_runner import run_user_strategy
    from strategy_generator_app.main import _diagnose_breakthrough_5day_10m

    get_name = lambda c: ""
    params = {"buy_amount_per_stock": 50000, "min_order_amount": 5000}

    print("\n=== all June selection days (T+1, hold=1) ===")
    for sel_d in sorted(by_day):
        codes = list(dict.fromkeys(by_day[sel_d]))
        start_d, end_d, msg = backtest_window_from_selection_day(
            sel_d, start_next_trading_day=True, hold_trading_days=1
        )
        print(f"\n--- sel {sel_d} -> trade {start_d}~{end_d} pool={len(codes)} ---")
        if not start_d:
            print("  SKIP:", msg)
            continue
        trade_d = start_d
        prices = get_historical_prices_for_morning(codes, trade_d, get_name)
        if isinstance(prices, dict) and prices.get("_error"):
            print("  PRICE ERR:", prices["_error"])
            continue
        valid = [c for c in codes if float(prices.get(c, {}).get("current") or 0) > 0]
        print(f"  morning valid={len(valid)} zero={len(codes)-len(valid)}")
        if not valid:
            print("  -> engine skips day (all prices 0)")
            continue

        try:
            atp = get_prices_at_time(codes, trade_d, "09:25")
            hl = get_today_high_low_at_time(codes, trade_d, "09:25")
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
            print("  tick enrich err:", e)

        try:
            intents = run_user_strategy(code_str, valid, prices, get_name, {}, params)
        except Exception as e:
            print("  STRATEGY FAIL:", type(e).__name__, e)
            continue

        print(f"  intents={len(intents)}")
        if intents:
            icodes = [(i.get("stock_code") or "").zfill(6) for i in intents]
            miss = [c for c in icodes if load_tick_data_for_date(c, trade_d) is None]
            print("  codes:", ",".join(icodes))
            print(f"  missing tick: {len(miss)}", miss)
        else:
            reasons = defaultdict(int)
            for c in valid:
                r = _diagnose_breakthrough_5day_10m(c, prices.get(c, {}), params)
                key = r.split("（")[0] if "（" in r else r[:50]
                reasons[key] += 1
            print("  top fail reasons:")
            for k, v in sorted(reasons.items(), key=lambda x: -x[1])[:8]:
                print(f"    {v}x {k}")


if __name__ == "__main__":
    main()
