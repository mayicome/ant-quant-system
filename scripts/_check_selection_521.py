# -*- coding: utf-8 -*-
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "strategy_generator_app"))

from backtest.engine import run_backtest
from backtest.data_provider import get_historical_prices_for_morning, get_today_high_low_at_time, load_ticks_for_codes
from strategy_runner import run_user_strategy

# main imports PyQt; load grouping via importlib to avoid side effects
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "sg_main", ROOT / "strategy_generator_app" / "main.py"
)
_sg_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sg_main)
group_codes_by_selection_date_from_file = _sg_main.group_codes_by_selection_date_from_file

SEL = date(2026, 5, 21)
TRADE = date(2026, 5, 22)
DIR = ROOT / "history_data" / "新回测"

files = sorted(DIR.glob("选股结果*.xls*"))
print("Selection files:", [f.name for f in files])

cfg = json.loads(
    (ROOT / "strategy_generator_app/config/strategies/strategy_508e9237.json").read_text(
        encoding="utf-8"
    )
)

for fp in files:
    by_day, hint = group_codes_by_selection_date_from_file(str(fp))
    codes = by_day.get(SEL, [])
    print("\n" + "=" * 60)
    print(fp.name)
    print(hint)
    print(f"2026-05-21: {len(codes)} stocks, 002845={'002845' in codes}")

# Full backtest with 5-01 file
fp = DIR / "选股结果_模式一_5-01_5-31.xls"
by_day, _ = group_codes_by_selection_date_from_file(str(fp))
codes = by_day[SEL]

prices = get_historical_prices_for_morning(codes, TRADE)
ticks = load_ticks_for_codes(codes, TRADE)
pr2 = {c: dict(prices[c]) for c in codes if c in prices}
for c in codes:
    if c not in pr2:
        continue
    hl = get_today_high_low_at_time([c], TRADE, "09:25", ticks_by_stock=ticks)
    if c in hl:
        pr2[c]["今日最高"] = hl[c]["今日最高"]
        pr2[c]["今日最低"] = hl[c]["今日最低"]
    elif not pr2[c].get("今日最高"):
        o = pr2[c].get("今开盘") or 0
        if float(o) > 0:
            pr2[c]["今日最高"] = round(float(o), 2)
            pr2[c]["今日最低"] = round(float(o), 2)

account = {"total_asset": 1e6, "cash": 1e6}
intents = run_user_strategy(
    cfg["strategy_code"], codes, pr2, lambda c: "", account, cfg["strategy_params"]
)
intent_codes = sorted({i["stock_code"] for i in intents})
print("\n=== Strategy intents @09:25 from full pool ===")
print(f"count={len(intent_codes)}")
print("002845 in intents:", "002845" in intent_codes)
print("codes:", intent_codes)

for cash in (500_000, 1_000_000):
    r = run_backtest(
        cfg["strategy_code"],
        cfg["strategy_params"],
        codes,
        TRADE,
        TRADE,
        initial_cash=cash,
        use_tick_level=True,
        strategy_generation_time="09:25",
    )
    buys = r.get("trades") or []
    buy_codes = sorted({t.get("code") for t in buys})
    print(f"\n=== Backtest cash={cash} buys={len(buys)} ===")
    print("codes:", buy_codes)
    print("002845 bought:", "002845" in buy_codes)
    for t in buys:
        if t.get("code") == "002845":
            print(" ", t)
