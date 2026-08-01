# -*- coding: utf-8 -*-
"""000006 深振业A：5/15 买入5000股，5/18 单日 tick 回测（新卖出，持有交易日数=1）。"""
import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SG = os.path.join(ROOT, "strategy_generator_app")
sys.path.insert(0, ROOT)
sys.path.insert(0, SG)

from strategy_generator_app.backtest.engine import run_backtest
from strategy_generator_app.backtest.data_provider import get_historical_prices_for_morning

CODE = "000006"
BUY_DATE = date(2026, 5, 15)
SELL_DATE = date(2026, 5, 18)
VOLUME = 5000
HOLD_DAYS = 1

cfg_path = os.path.join(SG, "config", "strategies", "strategy_e9c83928.json")
with open(cfg_path, encoding="utf-8") as f:
    cfg = json.load(f)

params = dict(cfg.get("strategy_params") or {})
params["scheduled_clear_on_sell_day"] = HOLD_DAYS
params["sell_hold_trading_days"] = HOLD_DAYS

# 成本价：买入日昨收（若无则用当日开盘）
buy_prices = get_historical_prices_for_morning([CODE], BUY_DATE, lambda c: "深振业A")
bp = buy_prices.get(CODE, {})
cost = float(bp.get("昨收盘") or bp.get("pre_close") or bp.get("今开盘") or bp.get("current") or 0)
if cost <= 0:
    cost = float(bp.get("最新价") or 7.0)

initial_positions = {CODE: {"volume": VOLUME, "cost": round(cost, 2)}}

result = run_backtest(
    strategy_code=cfg["strategy_code"],
    strategy_params=params,
    stock_codes_6=[CODE],
    start_date=SELL_DATE,
    end_date=SELL_DATE,
    initial_cash=1_000_000.0,
    get_stock_name=lambda c: "深振业A",
    use_tick_level=True,
    strategy_generation_time="09:25",
    strategy_run_start_time="09:30",
    strategy_run_end_time="15:00",
    initial_positions=initial_positions,
)

out_lines = []
out_lines.append(f"回测日: {SELL_DATE} | 初始持仓: {VOLUME}股 @ {cost:.2f} (买入参考日 {BUY_DATE})")
out_lines.append(f"持有交易日数: {HOLD_DAYS} | 回测第1日=末卖出日 (backtest_trade_day_index=1)")
out_lines.append("")

# 生成的规则
for entry in result.get("generated_intents") or []:
    out_lines.append(f"=== {entry.get('date')} 生成规则 ({entry.get('segment_name')}) ===")
    for it in entry.get("intents") or []:
        rt = it.get("rule_type")
        name = it.get("name")
        if rt in ("single_sell", "breakthrough_sell", "scheduled_clear"):
            out_lines.append(f"  {name} | 价={it.get('price')} 量={it.get('volume')}")
            if it.get("activation"):
                out_lines.append(f"    activation={json.dumps(it['activation'], ensure_ascii=False)}")
        elif rt == "best_sell":
            out_lines.append(
                f"  {name} | 触发={it.get('trigger_price')} 回落{it.get('drop_percent')}% 量={it.get('volume')}"
            )
    out_lines.append("")

trades = result.get("trades") or []
out_lines.append(f"=== 成交记录 ({len(trades)} 笔) ===")
if not trades:
    out_lines.append("（无成交）")
else:
    for i, t in enumerate(trades, 1):
        out_lines.append(
            f"{i}. {t.get('time') or t.get('datetime') or t.get('date')} "
            f"{t.get('side') or t.get('direction') or 'sell'} "
            f"{t.get('stock_code') or CODE} "
            f"价={t.get('price')} 量={t.get('volume')} "
            f"规则={t.get('rule_name') or t.get('name') or t.get('rule_type')} "
            f"类型={t.get('rule_type')}"
        )
        if t.get("trigger_info"):
            out_lines.append(f"   {t['trigger_info']}")

out_lines.append("")
out_lines.append(f"期末现金: {result.get('final_cash')}")
out_lines.append(f"期末持仓: {result.get('final_positions')}")
out_lines.append(f"总收益: {result.get('total_return_pct')}%")

if result.get("failure_reasons"):
    out_lines.append("\n=== 诊断 ===")
    for r in result["failure_reasons"]:
        out_lines.append(r)

tick_cov = result.get("tick_coverage") or []
if tick_cov:
    out_lines.append("\n=== Tick 覆盖 ===")
    for c in tick_cov:
        out_lines.append(str(c))

out_path = os.path.join(ROOT, "scripts", "_bt_000006_20260518.txt")
with open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(out_lines))
print(out_path)
