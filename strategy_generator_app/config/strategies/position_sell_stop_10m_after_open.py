# 持仓开盘10分钟后止损 — 与 strategy_f12e82e8.json 中 strategy_code 保持同步

# 持仓卖出策略：仅对有可用持仓的股票生成卖出任务
# 用法：开盘十分钟之后生成，检查后运行。
# 策略描述：
# 一档：（主板含 ST：基准+3%；创业板/科创板：基准+5%）
# 二档：（主板含 ST：基准+5%；创业板/科创板：基准+10%）
# 三档：（主板含 ST：基准+7%；创业板/科创板：基准+15%）
# 弹性卖出：突破三档后回落至二档价卖出；突破二档后回落至一档价；突破一档后回落至昨收价
# （规则字段 pullback_price；引擎按固定回落价触发，非最高价百分比）
# 上述三条卖出数量沿用原档逻辑：40% / 30% / 30% 持仓（手数向下取整）
# 再设置止损：
# 突破卖出 50%（昨收盘下跌 1.5% 触发）
# 如果 5日（线重合点）< 昨收盘：突破卖出 30% 价格 5日
# 如果 10日（线重合点）< 昨收盘：突破卖出 45% 价格 10日
# 定时清仓 100%（清仓价=20日线）
# 基准=max((昨收盘+今开盘)/2，昨收盘)；今开盘取「今开盘」/open，缺失时用昨收盘。
# params["positions"] 由运行前自动注入

def run(codes, prices, get_name, account, params):
    positions = params.get("positions") or {}
    result = []
    default_clear_time = "14:56:00"
    for code in codes:
        code_6 = (code or "").strip()
        if len(code_6) < 6:
            code_6 = code_6.zfill(6)
        avail = positions.get(code_6, 0)
        if not isinstance(avail, (int, float)):
            avail = 0
        avail = int(avail)
        if avail < 100:
            continue
        avail = (avail // 100) * 100
        p = prices.get(code_6, {})
        pre_close = float(p.get("昨收盘") or p.get("pre_close") or 0)
        open_px = float(p.get("今开盘") or p.get("open") or 0)
        if not open_px:
            open_px = pre_close
        latest_price = float(
            p.get("最新价") or p.get("current") or p.get("今开盘") or p.get("昨收盘") or 0
        )
        limit_up = float(p.get("涨停板") or 0)
        limit_down = float(p.get("跌停板") or 0)
        ma5 = p.get("5日")
        ma10 = p.get("10日")
        ma20 = p.get("20日")
        if not latest_price or not limit_up or not pre_close:
            continue
        if ma5 is None or ma10 is None or ma20 is None:
            continue
        ma5, ma10, ma20 = float(ma5), float(ma10), float(ma20)

        name_raw = (get_name(code_6) if get_name else "") or ""
        name = name_raw or "未知"
        if code_6.startswith(("300", "301", "688", "689")):
            m1, m2, m3 = 1.05, 1.10, 1.15
        else:
            m1, m2, m3 = 1.03, 1.05, 1.07

        base = max((pre_close + open_px) / 2.0, pre_close)
        tier1 = round(base * m1, 2)
        tier2 = round(base * m2, 2)
        tier3 = round(base * m3, 2)

        def in_range(price):
            pr = round(float(price), 2)
            if limit_down and pr < limit_down:
                return None
            if limit_up and pr > limit_up:
                return None
            return pr

        # 三档、二档、一档各一条弹性卖出：突破触发价后，回落至下一档（或昨收）价卖出
        best_specs = [
            ("弹性卖出（破三档回落至二档）", tier3, tier2, 0.40),
            ("弹性卖出（破二档回落至一档）", tier2, tier1, 0.30),
            ("弹性卖出（破一档回落至昨收）", tier1, pre_close, 0.30),
        ]
        for label, trig_px, pullback_px, vol_ratio in best_specs:
            tp = in_range(trig_px)
            pp = in_range(pullback_px)
            if tp is None or pp is None:
                continue
            if pullback_px >= trig_px:
                continue
            v = max(100, (int(avail * vol_ratio / 100) * 100))
            result.append(
                {
                    "stock_code": code_6,
                    "stock_name": name,
                    "rule_type": "best_sell",
                    "name": label,
                    "trigger_price": tp,
                    "pullback_price": pp,
                    "drop_percent": 0.3,
                    "volume": v,
                }
            )

        # 昨收盘下跌 1.5% 止损 50%
        bp = in_range(pre_close * 0.985)
        if bp is not None:
            v50 = max(100, (int(avail * 0.5 / 100) * 100))
            result.append(
                {
                    "stock_code": code_6,
                    "stock_name": name,
                    "rule_type": "breakthrough_sell",
                    "name": "突破卖出（昨收盘-1.5%）",
                    "price": bp,
                    "volume": v50,
                }
            )

        # 5日/10日线重合点低于昨收时，在均线价位突破卖出
        if ma5 < pre_close:
            p5 = in_range(ma5)
            if p5 is not None:
                v30 = max(100, (int(avail * 0.30 / 100) * 100))
                result.append(
                    {
                        "stock_code": code_6,
                        "stock_name": name,
                        "rule_type": "breakthrough_sell",
                        "name": "突破卖出（5日线重合点<昨收）",
                        "price": p5,
                        "volume": v30,
                    }
                )
        if ma10 < pre_close:
            p10 = in_range(ma10)
            if p10 is not None:
                v45 = max(100, (int(avail * 0.45 / 100) * 100))
                result.append(
                    {
                        "stock_code": code_6,
                        "stock_name": name,
                        "rule_type": "breakthrough_sell",
                        "name": "突破卖出（10日线重合点<昨收）",
                        "price": p10,
                        "volume": v45,
                    }
                )

        clear_price = round(ma20, 2)
        if limit_down:
            clear_price = max(clear_price, limit_down)
        result.append(
            {
                "stock_code": code_6,
                "stock_name": name,
                "rule_type": "scheduled_clear",
                "name": "定时清仓（低于20日线清仓）",
                "price": round(clear_price, 2),
                "volume": avail,
                "scheduled_clear_time": default_clear_time,
            }
        )
    return result
