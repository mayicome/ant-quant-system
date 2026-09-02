# -*- coding: utf-8 -*-
"""安装策略：买：布林%b回落-次日开盘

配合选股「布林%b回落选股」：
  选股日 T → 仅 T+1 一个交易日挂开盘买入（entry_window=1）。
  日线回测：触发价=涨停，成交≈开盘（same_day_ohlc）。

用法:
  python tools/install_bb_pctb_buy_strategy.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "strategy_generator_app" / "config" / "strategies"

STRATEGY_NAME = "买：布林%b回落-次日开盘"
PREFERRED_ID = "strategy_bb_pctb_buy"

STRATEGY_CODE = r'''# 买：布林%b回落 — 选股日后次日开盘买入
# - 挂单窗：选股日下一交易日起连续 entry_window_trading_days（默认 1）日
# - rule_type=single_buy + open_buy_ask；触发价=涨停 → 日线撮合约开盘成交
# - 开盘涨停则跳过
# params：buy_amount_per_stock, min_order_amount,
#         entry_window_trading_days(=1), selection_date_by_code, _filled_legs

NAME_BUY = "布林%b-次日开盘买"
LEG_ID = "OPEN"
LIMIT_OPEN_EPS = 0.011


def run(codes, prices, get_name, account, params):
    result = []
    params = params or {}
    try:
        amount_per = float(params.get("buy_amount_per_stock") or 50000)
    except (TypeError, ValueError):
        amount_per = 50000.0
    try:
        min_order = float(params.get("min_order_amount") or 5000)
    except (TypeError, ValueError):
        min_order = 5000.0
    try:
        entry_window = int(params.get("entry_window_trading_days", 1) or 1)
    except (TypeError, ValueError):
        entry_window = 1
    if entry_window < 1:
        entry_window = 1

    sel_map = params.get("selection_date_by_code") or {}
    if not isinstance(sel_map, dict):
        sel_map = {}
    filled_raw = params.get("_filled_legs") or []
    if isinstance(filled_raw, dict):
        filled = set(str(k) for k, v in filled_raw.items() if v)
    else:
        filled = set(str(x) for x in filled_raw)

    from datetime import date as _date, datetime as _dt, timedelta

    def _parse_d(v):
        if v is None or v == "":
            return None
        if isinstance(v, _dt):
            return v.date()
        if isinstance(v, _date):
            return v
        s = str(v).strip()[:10]
        try:
            return _date.fromisoformat(s)
        except Exception:
            return None

    trade_d = _parse_d(params.get("backtest_trade_date"))
    if trade_d is None:
        trade_d = _date.today()

    def _code6(c):
        s = str(c or "").strip()
        if "." in s:
            s = s.split(".", 1)[0]
        if s.isdigit():
            return s.zfill(6)
        return s

    def _next_td(d0):
        try:
            from strategy_generator_app.trading_calendar import next_trading_day_after
            nd = next_trading_day_after(d0)
            if nd is not None:
                return nd
        except Exception:
            pass
        try:
            from trading_calendar import next_trading_day_after as _n2
            nd = _n2(d0)
            if nd is not None:
                return nd
        except Exception:
            pass
        d = d0 + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d

    def _nth_td(d0, n):
        if n <= 1:
            return d0
        try:
            from strategy_generator_app.trading_calendar import get_trading_dates_in_range_sorted
            lst = get_trading_dates_in_range_sorted(d0, d0 + timedelta(days=400))
            if lst and len(lst) >= n:
                return lst[n - 1]
        except Exception:
            pass
        d = d0
        counted = 0
        while counted < n:
            if d.weekday() < 5:
                counted += 1
                if counted == n:
                    return d
            d += timedelta(days=1)
        return d

    def vol_for(amt, price):
        if price is None or price <= 0:
            return 0
        v = max(100, int(float(amt) / float(price) / 100) * 100)
        if v * float(price) < min_order:
            return 0
        return v

    def _f(p, *keys):
        for k in keys:
            try:
                v = float(p.get(k) or 0)
            except (TypeError, ValueError):
                v = 0.0
            if v > 0:
                return v
        return 0.0

    entry_range_by_code = {}
    sel_date_by_code6 = {}
    if sel_map:
        try:
            from strategy_generator_app.trading_calendar import get_trading_dates_in_range_sorted
        except Exception:
            try:
                from trading_calendar import get_trading_dates_in_range_sorted
            except Exception:
                get_trading_dates_in_range_sorted = None
        cal_list = None
        if get_trading_dates_in_range_sorted is not None:
            try:
                parsed_dates = [_parse_d(v) for v in sel_map.values()]
                parsed_dates = [d for d in parsed_dates if d is not None]
                if parsed_dates:
                    dmin = min(parsed_dates)
                    dmax = max(parsed_dates)
                    cal_list = get_trading_dates_in_range_sorted(
                        dmin, dmax + timedelta(days=400)
                    )
            except Exception:
                cal_list = None
        for raw_k, raw_v in sel_map.items():
            c6k = _code6(raw_k)
            sd = _parse_d(raw_v)
            if not c6k or sd is None:
                continue
            sel_date_by_code6[c6k] = sd
            if cal_list:
                after = [d for d in cal_list if d > sd]
                if not after:
                    continue
                start_d = after[0]
                end_d = after[entry_window - 1] if len(after) >= entry_window else after[-1]
            else:
                start_d = _next_td(sd)
                end_d = _nth_td(start_d, entry_window)
            entry_range_by_code[c6k] = (start_d, end_d)

    for code in codes or []:
        c6 = _code6(code)
        if not c6:
            continue
        leg_key = "%s:%s" % (c6, LEG_ID)
        if leg_key in filled:
            continue

        sel_d = sel_date_by_code6.get(c6) or _parse_d(sel_map.get(c6) or sel_map.get(code))
        win = entry_range_by_code.get(c6)
        if win is None:
            if sel_d is not None:
                start_d = _next_td(sel_d)
                end_d = _nth_td(start_d, entry_window)
                if trade_d < start_d or trade_d > end_d:
                    continue
            else:
                # 无选股日映射：允许当日挂（人工池）
                start_d = end_d = None
        else:
            start_d, end_d = win
            if trade_d < start_d or trade_d > end_d:
                continue

        p = prices.get(c6) or prices.get(code) or {}
        if not isinstance(p, dict):
            continue
        name = (get_name(c6) if get_name else "") or ""
        open_px = _f(p, "今开盘", "open", "最新价", "current")
        if open_px <= 0:
            continue
        try:
            lu = float(p.get("涨停板") or 0)
            ld = float(p.get("跌停板") or 0)
        except (TypeError, ValueError):
            lu, ld = 0.0, 0.0
        if lu <= 0 or ld <= 0:
            continue
        if open_px + 1e-9 >= lu - LIMIT_OPEN_EPS:
            continue
        trig = round(float(lu), 2)
        v = vol_for(amount_per, open_px)
        if v <= 0:
            continue
        result.append({
            "stock_code": c6,
            "stock_name": name,
            "rule_type": "single_buy",
            "name": NAME_BUY,
            "leg_key": leg_key,
            "price": float(trig),
            "volume": int(v),
            "limit_up": float(trig),
            "wait_unseal": False,
            "open_buy_ask": True,
            "early_order_enabled": False,
        })
    return result
'''


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{PREFERRED_ID}.json"
    prev = {}
    if path.is_file():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    prev_params = dict(prev.get("strategy_params") or {})
    out = {
        "id": PREFERRED_ID,
        "name": STRATEGY_NAME,
        "enabled": True,
        "stock_codes": list(prev.get("stock_codes") or []),
        "strategy_params": {
            "buy_amount_per_stock": float(prev_params.get("buy_amount_per_stock", 50000) or 50000),
            "min_order_amount": float(prev_params.get("min_order_amount", 5000) or 5000),
            "entry_window_trading_days": int(prev_params.get("entry_window_trading_days", 1) or 1),
            "sizing_mode": "fixed",
            "selection_date_by_code": prev_params.get("selection_date_by_code") or {},
            "_filled_legs": [],
        },
        "strategy_code": STRATEGY_CODE,
        "scheduled_generate_at": None,
    }
    compile(STRATEGY_CODE, PREFERRED_ID, "exec")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", path)
    print("name", STRATEGY_NAME)
    print("params entry_window=1 open_buy_ask")


if __name__ == "__main__":
    raise SystemExit(main())
