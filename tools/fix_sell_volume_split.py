import json
from pathlib import Path


TARGET_FILES = [
    "strategy_107e9efa.json",
    "strategy_a3e87147.json",
    "strategy_3d674bae.json",
    "strategy_c249aa2c.json",
    "strategy_13f884aa.json",
    "strategy_38fe88de.json",
    "strategy_2e05be16.json",
]

OLD = """        for mult, ratio, drop_pct, rule_name in take_profit_levels:
            trigger_price = round(base_high * mult, 2)
            trigger_price = max(limit_down, min(limit_up, trigger_price))
            vol = max(100, (int(avail * ratio / 100) * 100))
            print(f"[持仓止盈] {code_6}   {rule_name} 倍数={mult} 触发价={trigger_price} 量={vol}")
            result.append({
                "stock_code": code_6,
                "stock_name": name,
                "rule_type": "best_sell",
                "name": rule_name,
                "trigger_price": trigger_price,
                "drop_percent": drop_pct,
                "volume": vol,
                "debug_pre_close": pre_close,
                "debug_latest": latest_price,
                "debug_base_high": base_high,
                "debug_limit_up": limit_up,
                "debug_limit_down": limit_down,
                "debug_mult": mult,
            })"""

NEW = """        # 分档卖出量：前几档按比例取整到百股，最后一档吃掉剩余，避免出现零碎残仓。
        total_lots = max(1, avail // 100)
        used_lots = 0
        for idx, (mult, ratio, drop_pct, rule_name) in enumerate(take_profit_levels):
            trigger_price = round(base_high * mult, 2)
            trigger_price = max(limit_down, min(limit_up, trigger_price))
            if idx < len(take_profit_levels) - 1:
                lots = int(total_lots * float(ratio))
                # 前几档至少给 1 手，避免档位被取整成 0
                lots = max(1, lots)
                remain_for_last = total_lots - used_lots
                # 预留最后一档至少 1 手
                lots = min(lots, max(1, remain_for_last - 1))
            else:
                lots = max(1, total_lots - used_lots)
            used_lots += lots
            vol = int(lots * 100)
            print(f"[持仓止盈] {code_6}   {rule_name} 倍数={mult} 触发价={trigger_price} 量={vol}")
            result.append({
                "stock_code": code_6,
                "stock_name": name,
                "rule_type": "best_sell",
                "name": rule_name,
                "trigger_price": trigger_price,
                "drop_percent": drop_pct,
                "volume": vol,
                "debug_pre_close": pre_close,
                "debug_latest": latest_price,
                "debug_base_high": base_high,
                "debug_limit_up": limit_up,
                "debug_limit_down": limit_down,
                "debug_mult": mult,
            })"""


def main():
    base = Path(r"d:\蚂蚁量化交易策略第五版\strategy_generator_app\config\strategies")
    changed = []
    for name in TARGET_FILES:
        p = base / name
        data = json.loads(p.read_text(encoding="utf-8"))
        code = data.get("strategy_code", "")
        if OLD not in code:
            continue
        data["strategy_code"] = code.replace(OLD, NEW, 1)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        changed.append(name)
    print("changed:", ",".join(changed))


if __name__ == "__main__":
    main()
