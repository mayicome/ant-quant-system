# -*- coding: utf-8 -*-
"""Strip MA-relationship filters from 突破5日线 strategies (selection already does it)."""
from __future__ import annotations

import json
from pathlib import Path

SDIR = Path(r"d:\蚂蚁量化系统\strategy_generator_app\config\strategies")

CODE_MAIN = r'''# 买：突破5日线-真突破（建议10分钟后加入）
# 均线多头/日线关系已在选股侧处理（严多头 / 实盘用 REQUIRE_STRICT_BULL），此处不再判断。
# 回测：意图含 require_true_breakthrough 时，仅当 tick 满足「真突破」三条（与 intelligentbuy 一致）才成交。
# 仅生成一条突破买入，不生成笼子买入。
# 触发价（5日线）须在当日 [跌停, 涨停] 内，否则当日无法有效挂单。
# 触发后：在5日线处挂突破买入，金额=单股拟买入金额的一半
#
# params：
# - buy_amount_per_stock（单股拟买入金额）
# - min_order_amount（每笔最小交易金额）

def run(codes, prices, get_name, account, params):
    result = []
    amount_per_stock = float(params.get("buy_amount_per_stock", 50000))
    min_order_amount = float(params.get("min_order_amount", 5000))

    def vol_for(amt, price):
        if price <= 0:
            return 0
        v = max(100, int(amt / price / 100) * 100)
        if v * price < min_order_amount:
            return 0
        return v

    half = amount_per_stock / 2.0

    for code in codes:
        p = prices.get(code, {}) or {}

        cur = float(p.get("current") or p.get("最新价") or 0)
        ma5 = p.get("5日")
        if ma5 is None or cur <= 0:
            continue
        try:
            ma5 = float(ma5)
        except (TypeError, ValueError):
            continue
        if ma5 <= 0:
            continue

        v = vol_for(half, ma5)
        if v <= 0:
            continue

        trig = round(ma5, 2)
        try:
            limit_up = float(p.get("涨停板") or 0)
            limit_down = float(p.get("跌停板") or 0)
        except (TypeError, ValueError):
            limit_up, limit_down = 0.0, 0.0
        if limit_up > 0 and limit_down > 0 and not (limit_down <= trig <= limit_up):
            continue

        result.append({
            "stock_code": code,
            "stock_name": (get_name(code) if get_name else "") or "",
            "rule_type": "breakthrough_buy",
            "require_true_breakthrough": True,
            "name": "突破买入（突破5日线-真突破）",
            "price": trig,
            "volume": v,
        })

    return result
'''

CODE_NO10 = r'''# 买：突破5日线-真突破（建议10分钟后加入）
# 均线多头/日线关系已在选股侧处理，此处不再判断（与「突破5日线买入」同逻辑）。
# 回测：意图含 require_true_breakthrough 时，仅当 tick 满足「真突破」三条才成交。
# 仅生成一条突破买入；触发价（5日线）须在当日 [跌停, 涨停] 内。
#
# params：
# - buy_amount_per_stock（单股拟买入金额）
# - min_order_amount（每笔最小交易金额）

def run(codes, prices, get_name, account, params):
    result = []
    amount_per_stock = float(params.get("buy_amount_per_stock", 50000))
    min_order_amount = float(params.get("min_order_amount", 5000))

    def vol_for(amt, price):
        if price <= 0:
            return 0
        v = max(100, int(amt / price / 100) * 100)
        if v * price < min_order_amount:
            return 0
        return v

    half = amount_per_stock / 2.0

    for code in codes:
        p = prices.get(code, {}) or {}

        cur = float(p.get("current") or p.get("最新价") or 0)
        ma5 = p.get("5日")
        if ma5 is None or cur <= 0:
            continue
        try:
            ma5 = float(ma5)
        except (TypeError, ValueError):
            continue
        if ma5 <= 0:
            continue

        v = vol_for(half, ma5)
        if v <= 0:
            continue

        trig = round(ma5, 2)
        try:
            limit_up = float(p.get("涨停板") or 0)
            limit_down = float(p.get("跌停板") or 0)
        except (TypeError, ValueError):
            limit_up, limit_down = 0.0, 0.0
        if limit_up > 0 and limit_down > 0 and not (limit_down <= trig <= limit_up):
            continue

        result.append({
            "stock_code": code,
            "stock_name": (get_name(code) if get_name else "") or "",
            "rule_type": "breakthrough_buy",
            "require_true_breakthrough": True,
            "name": "突破买入（突破5日线）",
            "price": trig,
            "volume": v,
        })

    return result
'''

UPDATES = {
    "strategy_508e9237.json": CODE_MAIN,  # 突破5日线买入
    "strategy_4b5ab19f.json": CODE_NO10,  # 突破5日线不判断10日线
}


def main() -> None:
    for fname, code in UPDATES.items():
        compile(code, f"<{fname}>", "exec")
        path = SDIR / fname
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["strategy_code"] = code
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        print("OK", cfg.get("name"), fname)


if __name__ == "__main__":
    main()
