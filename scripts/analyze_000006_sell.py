# -*- coding: utf-8 -*-
import json
import os
import sys
from datetime import date
from io import StringIO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SG = os.path.join(ROOT, "strategy_generator_app")
sys.path.insert(0, ROOT)
sys.path.insert(0, SG)
os.chdir(SG)

from trading_calendar import first_trading_day_on_or_after, get_trading_dates_in_range_sorted
from price_provider import get_prices_with_key_points
from strategy_runner import run_user_strategy

CODE = "000006"
BUY_DATE = date(2026, 5, 18)
TARGET_DAY = 3
FAKE_AVAIL = 1000

buf = StringIO()

def log(s=""):
    buf.write(s + "\n")

cfg_path = os.path.join(SG, "config", "strategies", "strategy_e9c83928.json")
with open(cfg_path, encoding="utf-8") as f:
    cfg = json.load(f)

price_map, _ = get_prices_with_key_points([CODE])
p = price_map.get(CODE, {})

start = first_trading_day_on_or_after(BUY_DATE)
end = date(2026, 6, 26)
all_days = get_trading_dates_in_range_sorted(start, end)

log(f"股票: {CODE} 深振业A")
log(f"买入日(自然日): {BUY_DATE} -> 首个交易日: {start}")
log(f"持有目标: {TARGET_DAY} 个交易日（末卖出日=第{TARGET_DAY}日）")
log(f"买入至今日共 {len(all_days)} 个交易日:")
for i, d in enumerate(all_days, 1):
    mark = " <-- 末卖出日" if i == TARGET_DAY else (" <-- 今日(参考)" if d == end else "")
    log(f"  第{i}日 {d}{mark}")

params_base = dict(cfg.get("strategy_params") or {})
params_base["positions"] = {CODE: FAKE_AVAIL}

log("\n=== 各关键日生成的规则（用今日行情算触发价，仅作结构说明） ===")
for day_idx in [1, 2, 3]:
    if day_idx > len(all_days):
        continue
    d = all_days[day_idx - 1]
    params = dict(params_base)
    params["code_sell_day_index"] = {CODE: day_idx}
    intents = run_user_strategy(
        cfg["strategy_code"], [CODE], price_map, lambda c: "深振业A", {}, params, strategy_name="新卖出",
    )
    log(f"\n--- 第{day_idx}个交易日 {d} ---")
    for it in intents:
        rt = it.get("rule_type")
        name = it.get("name")
        if rt == "breakthrough_sell":
            log(f"  [{rt}] {name}  触发价={it.get('price')}")
        elif rt == "best_sell":
            log(f"  [{rt}] {name}  触发价={it.get('trigger_price')} 回落{it.get('drop_percent')}% 量={it.get('volume')}")
        elif rt == "single_sell":
            act = it.get("activation") or {}
            log(f"  [{rt}] {name}  价={it.get('price')} 量={it.get('volume')}  激活={act.get('activate_at')}")
        elif rt == "scheduled_clear":
            log(f"  [{rt}] {name}  触发价={it.get('price')} 时间={it.get('scheduled_clear_time')}")

log("\n=== 今日行情快照（策略生成触发价依据） ===")
pre = float(p.get("昨收盘") or 0)
latest = float(p.get("最新价") or 0)
base_high = max((pre + latest) / 2, pre) if pre and latest else 0
log(f"  昨收盘: {pre}")
log(f"  最新价: {latest}")
log(f"  基准 base_high=max((昨收+最新)/2,昨收): {round(base_high, 2)}")
log(f"  涨停板: {p.get('涨停板')}")
log(f"  跌停板: {p.get('跌停板')}")

out_path = os.path.join(ROOT, "scripts", "_000006_sell_out.txt")
with open(out_path, "w", encoding="utf-8") as f:
    f.write(buf.getvalue())
print(out_path)
