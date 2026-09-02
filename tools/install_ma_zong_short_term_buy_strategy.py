# -*- coding: utf-8 -*-
"""安装策略：买：马总短线-跌破MA5/10/20各10万-单点

配合选股规则「马总短线选股逻辑」：
  - 选股日下一交易日起，连续 2 个交易日可挂腿
  - 首次跌破 MA5 / MA10 / MA20 各买 10 万元（单点）
  - 成交规则名带「首日/次日」，写入回测买入记录

用法:
  python tools/install_ma_zong_short_term_buy_strategy.py
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "strategy_generator_app" / "config" / "strategies"

STRATEGY_NAME = "买：马总短线-跌破MA5/10/20各10万-单点"
PREFERRED_ID = "strategy_mz_st_buy"

STRATEGY_CODE = r'''# 买：马总短线 — 选股日后 2 日内首次跌破 MA5/10/20 各买 10 万（单点）
# - 触发价 = 均线重合点（行情「5日」「10日」「20日」）
# - rule_type=single_buy：现价 <= 触发价即买
# - 挂单窗：选股日下一交易日起 entry_window_trading_days（默认 2）日
# - 规则名带「首日/次日」，写入成交 rule_name，便于核对买入日
# - 已买腿：params._filled_legs 含 leg_key（如 000001:MA5）则不再挂
# - 疑似除权：近 ex_div_lookback 日开盘缺口过大 → 本票停止买入
#
# params：buy_amount_per_leg(=100000), min_order_amount,
#         entry_window_trading_days(=2), selection_date_by_code, _filled_legs,
#         ex_div_lookback(=20)

LEG_SPECS = (
    ("MA5", "5日", "马总短线-跌破MA5"),
    ("MA10", "10日", "马总短线-跌破MA10"),
    ("MA20", "20日", "马总短线-跌破MA20"),
)


def run(codes, prices, get_name, account, params):
    result = []
    params = params or {}
    try:
        amount_leg = float(
            params.get("buy_amount_per_leg")
            or params.get("buy_amount_per_stock")
            or 100000
        )
    except (TypeError, ValueError):
        amount_leg = 100000.0
    min_order = float(params.get("min_order_amount", 5000) or 5000)
    try:
        entry_window = int(params.get("entry_window_trading_days", 2) or 2)
    except (TypeError, ValueError):
        entry_window = 2
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
        try:
            from trading_calendar import get_trading_dates_in_range_sorted as _g2
            lst = _g2(d0, d0 + timedelta(days=400))
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

    def _code6(c):
        s = str(c or "").strip()
        if "." in s:
            s = s.split(".", 1)[0]
        if s.isdigit():
            return s.zfill(6)
        return s

    def vol_for(amt, price):
        if price is None or price <= 0:
            return 0
        v = max(100, int(float(amt) / float(price) / 100) * 100)
        if v * float(price) < min_order:
            return 0
        return v

    entry_range_by_code = {}
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

        win = entry_range_by_code.get(c6)
        if win is None:
            sel_d = _parse_d(sel_map.get(c6) or sel_map.get(code))
            if sel_d is not None:
                start_d = _next_td(sel_d)
                end_d = _nth_td(start_d, entry_window)
                if trade_d < start_d or trade_d > end_d:
                    continue
            else:
                # 无选股日映射：池内默认可挂（实盘导入后请补 selection_date_by_code）
                start_d = trade_d
                end_d = trade_d
        else:
            start_d, end_d = win
            if trade_d < start_d or trade_d > end_d:
                continue

        # 买入窗内第几日：1=首日，2=次日…
        buy_day_idx = 1
        try:
            from strategy_generator_app.trading_calendar import get_trading_dates_in_range_sorted as _g3
            lst = _g3(start_d, trade_d)
            if lst:
                buy_day_idx = len(lst)
        except Exception:
            try:
                from trading_calendar import get_trading_dates_in_range_sorted as _g4
                lst = _g4(start_d, trade_d)
                if lst:
                    buy_day_idx = len(lst)
            except Exception:
                buy_day_idx = 1 if trade_d == start_d else 2
        if buy_day_idx < 1:
            buy_day_idx = 1
        day_label = "首日" if buy_day_idx == 1 else ("次日" if buy_day_idx == 2 else ("第%d日" % buy_day_idx))

        p = prices.get(c6) or prices.get(code) or {}
        if not isinstance(p, dict):
            continue
        try:
            limit_up = float(p.get("涨停板") or 0)
            limit_down = float(p.get("跌停板") or 0)
        except (TypeError, ValueError):
            limit_up, limit_down = 0.0, 0.0

        name = (get_name(c6) if get_name else "") or ""

        try:
            ex_lookback = int(params.get("ex_div_lookback", 20) or 20)
        except (TypeError, ValueError):
            ex_lookback = 20
        if ex_lookback < 1:
            ex_lookback = 20
        blocked = False
        try:
            from utils.ex_div_gap import has_ex_div_gap
            blocked = bool(
                has_ex_div_gap(
                    c6, stock_name=name, through_date=trade_d, lookback=ex_lookback
                )
            )
        except Exception:
            try:
                from ex_div_gap import has_ex_div_gap as _has2  # type: ignore
                blocked = bool(
                    _has2(
                        c6, stock_name=name, through_date=trade_d, lookback=ex_lookback
                    )
                )
            except Exception:
                blocked = False
        if blocked:
            continue

        for leg_id, field, rule_base in LEG_SPECS:
            leg_key = "%s:%s" % (c6, leg_id)
            if leg_key in filled:
                continue
            raw = p.get(field)
            if raw is None or raw == "":
                continue
            try:
                ma = float(raw)
            except (TypeError, ValueError):
                continue
            if ma <= 0:
                continue
            trig = round(ma, 2)
            if limit_up > 0 and limit_down > 0 and not (limit_down <= trig <= limit_up):
                continue
            v = vol_for(amount_leg, trig)
            if v <= 0:
                continue
            result.append({
                "stock_code": c6,
                "stock_name": name,
                "rule_type": "single_buy",
                "name": "%s-%s" % (rule_base, day_label),
                "leg_key": leg_key,
                "price": trig,
                "volume": int(v),
                "buy_window_day": int(buy_day_idx),
                "buy_window_day_label": day_label,
            })
    return result
'''


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rid = "strategy_" + uuid.uuid4().hex[:8]
    existing_id = None
    prev: dict = {}
    preferred = OUT_DIR / ("%s.json" % PREFERRED_ID)
    if preferred.is_file():
        try:
            raw = json.loads(preferred.read_text(encoding="utf-8"))
            existing_id = str(raw.get("id") or PREFERRED_ID)
            prev = raw if isinstance(raw, dict) else {}
        except Exception:
            pass
    if not existing_id:
        for p in OUT_DIR.glob("strategy_*.json"):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(raw.get("name") or "") == STRATEGY_NAME:
                existing_id = str(raw.get("id") or "")
                prev = raw if isinstance(raw, dict) else {}
                break
    sid = existing_id or PREFERRED_ID or rid
    prev_params = prev.get("strategy_params") if isinstance(prev.get("strategy_params"), dict) else {}
    sp = {
        "buy_amount_per_leg": float(prev_params.get("buy_amount_per_leg", 100000) or 100000),
        "buy_amount_per_stock": float(prev_params.get("buy_amount_per_leg", 100000) or 100000),
        "min_order_amount": float(prev_params.get("min_order_amount", 5000) or 5000),
        "sizing_mode": str(prev_params.get("sizing_mode") or "fixed"),
        "entry_window_trading_days": int(prev_params.get("entry_window_trading_days", 2) or 2),
        "selection_date_by_code": dict(prev_params.get("selection_date_by_code") or {}),
        "_filled_legs": list(prev_params.get("_filled_legs") or []),
        "ex_div_lookback": int(prev_params.get("ex_div_lookback", 20) or 20),
    }
    for k, v in prev_params.items():
        if k not in sp:
            sp[k] = v
    out = {
        "id": sid,
        "name": STRATEGY_NAME,
        "enabled": bool(prev.get("enabled", True)),
        "stock_codes": list(prev.get("stock_codes") or []),
        "strategy_params": sp,
        "strategy_code": STRATEGY_CODE,
        "scheduled_generate_at": prev.get("scheduled_generate_at") or "09:25:00",
    }
    path_by_id = OUT_DIR / ("%s.json" % sid)
    path_by_id.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for p in OUT_DIR.glob("strategy_*.json"):
        if p.resolve() == path_by_id.resolve():
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(raw.get("name") or "") == STRATEGY_NAME:
            p.unlink()
            print("removed duplicate", p.name)
    print("wrote", path_by_id)
    print("name", STRATEGY_NAME)
    print("params entry_window=2 buy_amount_per_leg=100000")


if __name__ == "__main__":
    main()
