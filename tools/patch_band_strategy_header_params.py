# -*- coding: utf-8 -*-
"""Put band watch/accept ratios at top of MA5 price-band strategy code."""
from __future__ import annotations

import json
from pathlib import Path

PATH = Path(
    r"d:\蚂蚁量化系统\strategy_generator_app\config\strategies\strategy_fee5a636.json"
)

CODE = r'''# 买：MA5 价格带内量价真突破（硬pass + 硬上沿死卡 MA5）
# - 监控带：[MA5×(1-监控%), MA5]，再钳到当日 [跌停, 涨停]
# - 有效带下沿：MA5×(1-有效%) → band_accept_low（须 ≤ 监控%）
# - 回测/实盘：监控带内判真突破；现价 < 有效下沿 → 深位硬pass；
#   买入参考价(卖一+滑点) > band_high(MA5) → 上沿硬pass
# - 仅当首次真突破且买入参考价落在 [有效下沿, 上沿] 才买入
# - 不筛选 MA5 与 10/20/30/60/120 日线关系（选股侧处理）
# - 输出 breakthrough_buy；price=上沿(贴MA5)
#
# —— 本策略独立参数（改这里即可）——
BAND_PCT_BELOW_MA5 = 0.03          # 监控带：MA5 下方比例（3%）
ACCEPT_BAND_PCT_BELOW_MA5 = 0.01   # 有效带下沿：MA5 下方比例（1%；须 ≤ 监控）
TRUE_BREAKTHROUGH_WINDOW_SEC = 45  # 真突破时间窗（秒）
#
# params（策略参数面板）：
# - buy_amount_per_stock、min_order_amount


def run(codes, prices, get_name, account, params):
    result = []
    amount_per_stock = float(params.get("buy_amount_per_stock", 50000))
    min_order_amount = float(params.get("min_order_amount", 5000))

    # 监控/有效/时间窗：只用文件头常量（改开头即可，不在此处改数）
    try:
        band_pct = float(BAND_PCT_BELOW_MA5)
    except (TypeError, ValueError):
        band_pct = 0.03
    if band_pct < 0:
        band_pct = 0.0
    if band_pct > 0.2:
        band_pct = 0.2

    try:
        accept_pct = float(ACCEPT_BAND_PCT_BELOW_MA5)
    except (TypeError, ValueError):
        accept_pct = 0.01
    if accept_pct < 0:
        accept_pct = 0.0
    if accept_pct > band_pct:
        accept_pct = band_pct

    try:
        window_sec = int(TRUE_BREAKTHROUGH_WINDOW_SEC)
    except (TypeError, ValueError):
        window_sec = 45
    if window_sec < 5:
        window_sec = 5
    if window_sec > 300:
        window_sec = 300

    def vol_for(amt, price):
        if price <= 0:
            return 0
        v = max(100, int(amt / price / 100) * 100)
        if v * price < min_order_amount:
            return 0
        return v

    half = amount_per_stock / 2.0

    def _f(p, *keys):
        for k in keys:
            if k in p and p.get(k) is not None:
                try:
                    return float(p.get(k))
                except (TypeError, ValueError):
                    pass
        return None

    for code in codes:
        p = prices.get(code, {}) or {}

        cur = float(p.get("current") or p.get("最新价") or 0)
        ma5 = _f(p, "5日", "ma5")
        if ma5 is None or ma5 <= 0 or cur < 0:
            continue

        try:
            limit_up = float(p.get("涨停板") or 0)
            limit_down = float(p.get("跌停板") or 0)
        except (TypeError, ValueError):
            limit_up, limit_down = 0.0, 0.0
        if limit_up <= 0 or limit_down <= 0:
            continue

        raw_high = round(float(ma5), 2)
        raw_low = round(float(ma5) * (1.0 - band_pct), 2)
        raw_accept = round(float(ma5) * (1.0 - accept_pct), 2)
        band_high = min(raw_high, round(limit_up, 2))
        band_low = max(raw_low, round(limit_down, 2))
        if band_low > band_high:
            continue
        if band_high < round(limit_down, 2) or band_low > round(limit_up, 2):
            continue

        band_accept_low = min(max(raw_accept, band_low), band_high)

        v = vol_for(half, band_high)
        if v <= 0:
            continue

        ma10 = _f(p, "10日", "ma10")
        ma20 = _f(p, "20日", "ma20")
        ma30 = _f(p, "30日", "ma30")
        ma60 = _f(p, "60日", "ma60")
        ma120 = _f(p, "120日", "ma120")

        result.append({
            "stock_code": code,
            "stock_name": (get_name(code) if get_name else "") or "",
            "rule_type": "breakthrough_buy",
            "require_true_breakthrough": True,
            "true_breakthrough_cond1_mode": "window",
            "true_breakthrough_window_sec": window_sec,
            "name": "突破买入（MA5价格带量价·硬pass）",
            "price": band_high,
            "band_low": band_low,
            "band_high": band_high,
            "band_accept_low": band_accept_low,
            "volume": v,
            "debug_ma5": round(float(ma5), 2),
            "debug_ma10": None if ma10 is None else round(float(ma10), 2),
            "debug_ma20": None if ma20 is None else round(float(ma20), 2),
            "debug_ma30": None if ma30 is None else round(float(ma30), 2),
            "debug_ma60": None if ma60 is None else round(float(ma60), 2),
            "debug_ma120": None if ma120 is None else round(float(ma120), 2),
            "analysis_ma5": None if ma5 is None else round(float(ma5), 2),
            "analysis_ma10": None if ma10 is None else round(float(ma10), 2),
            "analysis_ma20": None if ma20 is None else round(float(ma20), 2),
            "analysis_ma30": None if ma30 is None else round(float(ma30), 2),
            "analysis_ma60": None if ma60 is None else round(float(ma60), 2),
            "analysis_ma120": None if ma120 is None else round(float(ma120), 2),
        })

    return result
'''


def main() -> None:
    compile(CODE, "<band>", "exec")
    cfg = json.loads(PATH.read_text(encoding="utf-8"))
    cfg["strategy_code"] = CODE
    PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print("OK", cfg.get("name"))
    assert "BAND_PCT_BELOW_MA5 = 0.03" in CODE
    assert "ACCEPT_BAND_PCT_BELOW_MA5 = 0.01" in CODE
    print("header constants OK")


if __name__ == "__main__":
    main()
