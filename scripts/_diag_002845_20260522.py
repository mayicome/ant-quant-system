# -*- coding: utf-8 -*-
"""Diagnose why 002845 appears in 2026-05-21 selection batch backtest."""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "strategy_generator_app"))

from backtest.data_provider import (
    get_historical_prices_for_morning,
    get_prices_at_time,
    get_today_high_low_at_time,
    load_ticks_for_codes,
)
from backtest.engine import run_backtest
from strategy_runner import run_user_strategy

CODE = "002845"
TRADE = date(2026, 5, 22)
POOL = ["002845", "301379", "600353"]

cfg = json.loads(
    (ROOT / "strategy_generator_app/config/strategies/strategy_508e9237.json").read_text(
        encoding="utf-8"
    )
)

prices = get_historical_prices_for_morning(POOL, TRADE)
ticks = load_ticks_for_codes(POOL, TRADE)
p = prices.get(CODE, {})
print("\n=== band_osc for full pool ===")
for code in POOL:
    p = prices.get(code, {})
    ma5 = p.get("5日")
    ma10 = p.get("10日")
    open_px = float(p.get("今开盘") or 0)
    for gen in ["09:25", "09:30"]:
        hl = get_today_high_low_at_time([code], TRADE, gen, ticks_by_stock=ticks)
        hi = hl.get(code, {}).get("今日最高")
        lo = hl.get(code, {}).get("今日最低")
        band = None
        if hi and lo and ma5 is not None and ma10 is not None:
            ma5f = float(ma5)
            ma10f = float(ma10)
            band = (
                open_px >= ma10f
                and open_px <= ma5f
                and float(lo) > ma10f
                and float(hi) < ma5f
            )
        print(f"  {code} gen={gen} open={open_px} hi={hi} lo={lo} ma5={ma5} band={band}")

print("\n=== EOD prices for", CODE, "on", TRADE, "===")
for k in ["current", "昨收盘", "5日", "10日", "20日", "今开盘", "今日最高", "今日最低"]:
    print(f"  {k}: {p.get(k)}")

print("\n=== band_oscillation by generation time (002845) ===")
for gen in ["09:25", "09:30", "09:45", "10:00", "11:00", "13:00"]:
    at = get_prices_at_time([CODE], TRADE, gen, ticks_by_stock=ticks)
    hl = get_today_high_low_at_time([CODE], TRADE, gen, ticks_by_stock=ticks)
    cur = at.get(CODE)
    hi = hl.get(CODE, {}).get("今日最高")
    lo = hl.get(CODE, {}).get("今日最低")
    ma5 = p.get("5日")
    ma10 = p.get("10日")
    band = None
    if cur and hi and lo and ma5 is not None and ma10 is not None:
        open_px = float(p.get("今开盘") or 0)
        ma5f = float(ma5)
        ma10f = float(ma10)
        band = (
            open_px >= ma10f
            and open_px <= ma5f
            and float(lo) > ma10f
            and float(hi) < ma5f
        )
    print(
        f"  gen={gen} cur={cur} hi={hi} lo={lo} ma5={ma5} ma10={ma10} band_osc={band}"
    )

pr2 = dict(prices)
at = get_prices_at_time([CODE], TRADE, "09:25", ticks_by_stock=ticks)
if CODE in pr2 and at.get(CODE):
    pr2[CODE]["current"] = at[CODE]
    pr2[CODE]["最新价"] = at[CODE]
hl = get_today_high_low_at_time([CODE], TRADE, "09:25", ticks_by_stock=ticks)
if CODE in hl:
    pr2[CODE]["今日最高"] = hl[CODE]["今日最高"]
    pr2[CODE]["今日最低"] = hl[CODE]["今日最低"]
account = {"total_asset": 1e6, "cash": 1e6}
intents = run_user_strategy(
    cfg["strategy_code"],
    [CODE],
    pr2,
    lambda c: "同兴达",
    account,
    cfg["strategy_params"],
)
print("\n=== strategy intents @09:25 (002845 only) ===")
print(intents)

print("\n=== full pool backtest 2026-05-22 @09:25 ===")
r = run_backtest(
    cfg["strategy_code"],
    cfg["strategy_params"],
    POOL,
    TRADE,
    TRADE,
    initial_cash=1_000_000,
    use_tick_level=True,
    strategy_generation_time="09:25",
)
print("total trades:", len(r.get("trades") or []))
for t in r.get("trades") or []:
    print(" ", t.get("datetime"), t.get("code"), t.get("direction"), t.get("price"), t.get("volume"))
for gi in r.get("generated_intents_log") or []:
    print("gen log", gi.get("date"), [i.get("stock_code") for i in gi.get("intents") or []])

print("\n=== full pool backtest 2026-05-22 @09:30 ===")
r30 = run_backtest(
    cfg["strategy_code"],
    cfg["strategy_params"],
    POOL,
    TRADE,
    TRADE,
    initial_cash=1_000_000,
    use_tick_level=True,
    strategy_generation_time="09:30",
)
print("total trades:", len(r30.get("trades") or []))
for t in r30.get("trades") or []:
    print(" ", t.get("datetime"), t.get("code"), t.get("direction"), t.get("price"), t.get("volume"))
for gi in r30.get("generated_intents_log") or []:
    print("gen log", gi.get("date"), [i.get("stock_code") for i in gi.get("intents") or []])

print("\n=== pool without 002845 @09:25 ===")
r2 = run_backtest(
    cfg["strategy_code"],
    cfg["strategy_params"],
    ["301379", "600353"],
    TRADE,
    TRADE,
    initial_cash=500_000,
    use_tick_level=True,
    strategy_generation_time="09:25",
)
print("total trades:", len(r2.get("trades") or []))
for t in r2.get("trades") or []:
    print(" ", t.get("datetime"), t.get("code"), t.get("price"), t.get("volume"))
