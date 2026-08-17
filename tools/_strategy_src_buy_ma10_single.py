# -*- coding: utf-8 -*-
# 买：跌MA10 — 涨停后第一次跌至 MA10 全额买入（单点）
# - 触发价 = 均线重合点（行情字段「10日」）
# - 规则类型 single_buy：现价 <= 触发价即买
# - 仓位：单股拟买入金额（buy_amount_per_stock）全额
# - 腿名固定：跌MA10（实盘重启靠同名合并 executed）
# - 回测：params.selection_date_by_code[code]=选股日
#   挂单窗口：选股日下一交易日起，连续 entry_window_trading_days 个交易日（默认 10）
# - 回测已买腿：params._filled_legs 含 leg_key（如 000001:MA10）则不再挂
# - 实盘冷启动：请在「导入股票」后由界面扫描「选股日后已触达MA10」并手动删池；
#   默认不再在生成任务时逐票扫日线（避免大池拖慢）。若需运行时再扫可设
#   params.skip_if_already_touched_ma=True
# - 实盘：无 backtest_trade_date 时用今天；无选股日映射则池内全部可挂
# - 疑似除权：近 ex_div_lookback（默认20）交易日开盘相对昨收跌幅超过涨跌停幅度+0.5%
#   → 本票停止买入
#
# params：buy_amount_per_stock, min_order_amount,
#         entry_window_trading_days(=10), selection_date_by_code, _filled_legs,
#         ex_div_lookback(=20), skip_if_already_touched_ma(=False),
#         scan_already_touched_ma_on_import(=True)

LEG_SPECS = (
    ("MA10", "10日", "跌MA10"),
)


def run(codes, prices, get_name, account, params):
    result = []
    params = params or {}
    amount_per = float(params.get("buy_amount_per_stock", 50000) or 50000)
    min_order = float(params.get("min_order_amount", 5000) or 5000)
    try:
        entry_window = int(params.get("entry_window_trading_days", 10) or 10)
    except (TypeError, ValueError):
        entry_window = 10
    if entry_window < 1:
        entry_window = 1

    skip_touched = params.get("skip_if_already_touched_ma", False)
    if isinstance(skip_touched, str):
        skip_touched = skip_touched.strip().lower() not in ("0", "false", "no", "off")
    else:
        skip_touched = bool(skip_touched)

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
        """d0 为第 1 个交易日，返回第 n 个（含 d0）。"""
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

    def _already_touched(c6, sel_d, start_d, end_d):
        if not skip_touched or sel_d is None:
            return False
        try:
            from utils.first_ma_touch import already_touched_ma_in_entry_window

            hit, _ = already_touched_ma_in_entry_window(
                c6,
                selection_date=sel_d,
                before_date=trade_d,
                ma_period=10,
                entry_start=start_d,
                entry_end=end_d,
            )
            return bool(hit)
        except Exception:
            try:
                from first_ma_touch import already_touched_ma_in_entry_window as _fn  # type: ignore

                hit, _ = _fn(
                    c6,
                    selection_date=sel_d,
                    before_date=trade_d,
                    ma_period=10,
                    entry_start=start_d,
                    entry_end=end_d,
                )
                return bool(hit)
            except Exception:
                return False

    # 全额：单股拟买入金额
    leg_amt = amount_per

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

        sel_d = sel_date_by_code6.get(c6) or _parse_d(sel_map.get(c6) or sel_map.get(code))
        win = entry_range_by_code.get(c6)
        if win is None:
            if sel_d is not None:
                start_d = _next_td(sel_d)
                end_d = _nth_td(start_d, entry_window)
                if trade_d < start_d or trade_d > end_d:
                    continue
            else:
                start_d = end_d = None
        else:
            start_d, end_d = win
            if trade_d < start_d or trade_d > end_d:
                continue

        if start_d is not None and end_d is not None and _already_touched(c6, sel_d, start_d, end_d):
            continue

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
                    c6,
                    stock_name=name,
                    through_date=trade_d,
                    lookback=ex_lookback,
                )
            )
        except Exception:
            try:
                from ex_div_gap import has_ex_div_gap as _has2  # type: ignore

                blocked = bool(
                    _has2(
                        c6,
                        stock_name=name,
                        through_date=trade_d,
                        lookback=ex_lookback,
                    )
                )
            except Exception:
                blocked = False
        if blocked:
            continue

        for leg_id, field, rule_name in LEG_SPECS:
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
            v = vol_for(leg_amt, trig)
            if v <= 0:
                continue
            result.append({
                "stock_code": c6,
                "stock_name": name,
                "rule_type": "single_buy",
                "name": rule_name,
                "leg_key": leg_key,
                "price": trig,
                "volume": int(v),
            })
    return result
