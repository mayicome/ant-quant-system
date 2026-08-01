"""诊断：卖策略意图数量 + 策略代码能否执行。"""
import json
import os
import sys

app_root = os.path.dirname(os.path.abspath(__file__))
repo = os.path.dirname(app_root)
for p in (app_root, repo):
    if p not in sys.path:
        sys.path.insert(0, p)

from strategy_runner import run_user_strategy

cfg_path = os.path.join(app_root, "config", "strategies", "strategy_13f884aa.json")
data = json.load(open(cfg_path, encoding="utf-8"))
code = data["strategy_code"]
print("strategy:", data.get("name"))
print("has _tp10_ov:", "_tp10_ov" in code)
print("has NameError except:", "except NameError" in code)

params = dict(data.get("strategy_params") or {})
params["positions"] = {"000001": 1000, "600000": 2000}
prices = {
    "000001": {"昨收盘": 10.0, "最新价": 10.5, "涨停板": 11.0, "跌停板": 9.0},
    "600000": {"昨收盘": 20.0, "最新价": 21.0, "涨停板": 22.0, "跌停板": 18.0},
}
try:
    intents = run_user_strategy(
        code,
        ["000001", "600000"],
        prices,
        lambda c: "T",
        {"total_asset": 0, "cash": 0},
        params,
        strategy_name=data.get("name", ""),
    )
    print("OK intents:", len(intents))
    for it in intents:
        print(" ", it.get("stock_code"), "vol=", it.get("volume"), it.get("name"))
except Exception as e:
    print("FAIL:", type(e).__name__, e)
