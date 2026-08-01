# 持仓开盘前10分钟止盈 策略代码（仅止盈三档，按涨跌停类型分档）

# 持仓卖出策略：仅对有可用持仓（≥100股）的股票生成卖出任务。
# 用法：9点25分后生成，9点30分前运行；无最新价时依次回退 current/今开盘/昨收盘。
# 策略描述：按涨跌停幅度分档，每档为「基准上方止盈比例 + 弹性卖出（达档后回落X%触发）」。
#   - 沪深主板 ST/*ST（2026-07-06 前 5% / 之后 10%）：与主板相同止盈档位
#   - 主板（10%涨跌停）：基准+3% 卖30% 回落0.5%，+5% 卖30% 回落1%，+7% 卖40% 回落2%
#   - 创业板/科创板（20%涨跌停）：基准+5% 卖30% 回落0.5%，+10% 卖30% 回落1%，+15% 卖40% 回落2%
# 基准 = max((昨收盘+最新价)/2, 昨收盘)；最新价取 prices 中「最新价」，缺省用 current，再回退今开盘/昨收盘。
# params["positions"] 由运行前自动注入：{ "000001": 1000, ... } 表示各股票可用持仓。

def run(codes, prices, get_name, account, params):
    positions = params.get("positions") or {}
    result = []
    for code in codes:
        code_6 = (code or "").strip()
        if len(code_6) < 6:
            code_6 = code_6.zfill(6)
        avail = positions.get(code_6, 0)
        if not isinstance(avail, (int, float)):
            avail = 0
        avail = int(avail)
        print(f"[持仓止盈] {code_6} 可用持仓={avail}")
        if avail < 100:
            print(f"[持仓止盈] {code_6} 跳过: 可用持仓<100")
            continue
        avail = (avail // 100) * 100
        p = prices.get(code_6, {})
        pre_close = float(p.get("昨收盘") or p.get("pre_close") or 0)
        latest_price = float(
            p.get("最新价") or p.get("current") or p.get("今开盘") or p.get("昨收盘") or 0
        )
        limit_up = float(p.get("涨停板") or 0)
        limit_down = float(p.get("跌停板") or 0)
        print(f"[持仓止盈] {code_6} 昨收={pre_close} 最新价={latest_price} 涨停={limit_up} 跌停={limit_down}")
        if not latest_price or not limit_up:
            print(f"[持仓止盈] {code_6} 跳过: 无有效最新价或涨停板")
            continue
        name = (get_name(code_6) if get_name else "") or "未知"
        base_high = max((pre_close + latest_price) / 2.0, pre_close)
        # 按涨跌停幅度选档位：创业板/科创板 20%；主板（含 ST）10%
        if code_6.startswith(("300", "301", "688", "689")):
            limit_type = "20%"
            take_profit_levels = [
                (1.05, 0.30, 0.5, "弹性卖出（止盈+5%，回落0.5%）"),
                (1.10, 0.30, 1.0, "弹性卖出（止盈+10%，回落1%）"),
                (1.15, 0.40, 2.0, "弹性卖出（止盈+15%，回落2%）"),
            ]
        else:
            limit_type = "10%"
            take_profit_levels = [
                (1.030, 0.30, 0.5, "弹性卖出（止盈+3%，回落0.5%）"),
                (1.050, 0.30, 1.0, "弹性卖出（止盈+5%，回落1%）"),
                (1.070, 0.40, 2.0, "弹性卖出（止盈+7%，回落2%）"),
            ]
        print(f"[持仓止盈] {code_6} base_high={base_high} (max((昨收+最新价)/2,昨收)) 涨跌停类型={limit_type}")
        for mult, ratio, drop_pct, rule_name in take_profit_levels:
            trigger_price = round(base_high * mult, 2)
            trigger_price = max(limit_down, min(limit_up, trigger_price))
            vol = max(100, (int(avail * ratio / 100) * 100))
            print(f"[持仓止盈] {code_6}   {rule_name} 倍数={mult} 触发价={trigger_price} 量={vol}")
            result.append({
                "stock_code": code_6, "stock_name": name, "rule_type": "best_sell",
                "name": rule_name,
                "trigger_price": trigger_price,
                "drop_percent": drop_pct,
                "volume": vol,
            })
    return result
