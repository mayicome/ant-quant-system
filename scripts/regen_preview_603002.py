# -*- coding: utf-8 -*-
"""为 603002 重新生成「新卖出」预览任务（假定持仓 1300 + 末卖出日）。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SG_APP = os.path.join(ROOT, "strategy_generator_app")
sys.path.insert(0, ROOT)
sys.path.insert(0, SG_APP)
os.chdir(SG_APP)

from price_provider import get_prices_with_key_points
from strategy_runner import run_user_strategy
from strategy_generator_app.task_builder import (
    build_tasks_from_intents,
    get_tasks_file_path,
    write_tasks_to_excel,
    _normalize_stock_code,
)

CODE_6 = "603002"
FAKE_AVAIL = 1300
SELL_DAY_INDEX = 3

cfg_path = os.path.join(
    ROOT, "strategy_generator_app", "config", "strategies", "strategy_e9c83928.json"
)
with open(cfg_path, encoding="utf-8") as f:
    cfg = json.load(f)

codes = [CODE_6]
price_map, errs = get_prices_with_key_points(codes)
if errs:
    for e in errs[:5]:
        print("warn:", e)

params = dict(cfg.get("strategy_params") or {})
params["positions"] = {CODE_6: FAKE_AVAIL}
params["code_sell_day_index"] = {CODE_6: SELL_DAY_INDEX}
params["backtest_trade_day_index"] = SELL_DAY_INDEX

intents = run_user_strategy(
    cfg.get("strategy_code") or "",
    codes,
    price_map,
    lambda c: "宏昌电子" if c == CODE_6 else "",
    {"total_asset": 1_000_000, "cash": 500_000},
    params,
    strategy_name=cfg.get("name") or "新卖出",
)

print(f"生成意图 {len(intents)} 条:")
for it in intents:
    print(f"  - [{it.get('rule_type')}] {it.get('name')}")

tasks = build_tasks_from_intents(
    intents,
    sell_hold_trading_days=params.get("sell_hold_trading_days")
    or params.get("scheduled_clear_on_sell_day")
    or 3,
)

if not tasks:
    print("ERROR: 未生成任务")
    sys.exit(1)

# 先剔除当日任务文件中已有的 603002，再写入新任务，避免旧规则名残留
import pandas as pd
from strategy_generator_app.task_builder import PERSIST_TASK_COLUMNS

path = get_tasks_file_path(ROOT)
existing = []
if os.path.isfile(path):
    try:
        df = pd.read_excel(path)
        for c in PERSIST_TASK_COLUMNS:
            if c not in df.columns:
                df[c] = None
        for _, row in df.iterrows():
            task = row.to_dict()
            if isinstance(task.get("params"), str):
                try:
                    task["params"] = json.loads(task["params"])
                except Exception:
                    task["params"] = {}
            code = _normalize_stock_code(task.get("stock_code"))
            if code == CODE_6:
                continue
            existing.append(task)
    except Exception as ex:
        print("read existing warn:", ex)

final_tasks = existing + tasks
rows = []
for task in final_tasks:
    row = {k: task.get(k) for k in PERSIST_TASK_COLUMNS}
    if isinstance(row.get("params"), dict):
        row["params"] = json.dumps(row["params"], ensure_ascii=False)
    rows.append(row)

os.makedirs(os.path.dirname(path), exist_ok=True)
pd.DataFrame(rows, columns=PERSIST_TASK_COLUMNS).to_excel(path, index=False)

rules = tasks[0]["params"]["rules"]
print(f"\n已写入: {path}")
print(f"603002 规则数: {len(rules)}")
for r in rules:
    act = r.get("activation")
    extra = ""
    if isinstance(act, dict) and act.get("activate_at"):
        extra = f" | 激活={act.get('activate_at')} mode={act.get('mode')} check={act.get('check')}"
    print(f"  - {r.get('name')}{extra}")
