"""
根据策略配置与股票池生成待生成任务列表。
支持纯自动：当 price_source 为 current/pre_close 且传入 price_map 时，自动用行情填充价格/触发价/笼子区间。
"""

from typing import List, Dict, Any, Callable, Optional


def run_strategy(
    stock_codes: List[str],
    get_stock_name: Callable[[str], str],
    strategy_params: Dict[str, Any],
    price_map: Optional[Dict[str, Dict[str, float]]] = None,
) -> List[Dict[str, Any]]:
    """
    对股票池中每只股票生成一条待生成任务（意图）。
    strategy_params 需含 rule_type、price_source（manual/current/pre_close）及各规则参数字段。
    若 price_source 为 current 或 pre_close 且 price_map 已提供，则用行情自动填充 price/trigger_price/price_low/price_high。
    """
    base = dict(strategy_params)
    rule_type = (base.get("rule_type") or "single_buy").strip()
    base["rule_type"] = rule_type
    price_source = (base.get("price_source") or "manual").strip().lower()
    if price_source not in ("current", "pre_close"):
        price_source = "manual"
    interval_pct = float(base.get("interval_pct") or 1.0)  # 笼子自动区间宽度%，默认 1%
    if "volume_per_stock" in base and "volume" not in base:
        base["volume"] = int(base.get("volume_per_stock") or 0)

    result = []
    for code in stock_codes:
        code = (code or "").strip()
        if len(code) < 6:
            code = code.zfill(6)
        if not code:
            continue
        name = (get_stock_name and get_stock_name(code)) or ""
        intent = {
            "stock_code": code,
            "stock_name": name or "未知名称",
            **base,
        }
        # 纯自动：用行情填充价格相关字段
        if price_map and price_source != "manual":
            prices = price_map.get(code, {})
            current = float(prices.get("current") or 0)
            pre_close = float(prices.get("pre_close") or 0)
            ref = current if price_source == "current" else pre_close
            if ref > 0:
                if rule_type in ("single_buy", "breakthrough_buy", "single_sell"):
                    intent["price"] = ref
                elif rule_type in ("cage_buy", "cage_sell"):
                    pct = interval_pct / 100.0
                    intent["price_low"] = round(ref * (1 - pct), 2)
                    intent["price_high"] = round(ref * (1 + pct), 2)
                elif rule_type in ("best_buy", "best_sell"):
                    intent["trigger_price"] = ref
        result.append(intent)
    return result
