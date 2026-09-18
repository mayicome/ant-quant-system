# -*- coding: utf-8 -*-
"""安装策略：买：马总逻辑1-涨停后跌破MA10/20各1/2-单点-MA5带

在「…-单点」基础上增加生成时过滤：
  触发价（拟成交参考价）相对当日早盘 MA5 偏离须在 [ma5_band_low_pct, ma5_band_high_pct]
  默认 [-5%, +3%]；超出则本腿不挂。

单点买入在早盘生成时触发价已知，故可直接丢掉不合规腿；
弹性买入成交价事前未知，无法在生成阶段做同等过滤。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "strategy_generator_app" / "config" / "strategies"

STRATEGY_NAME = "买：马总逻辑1-涨停后跌破MA10/20各1/2-单点-MA5带"
LEGACY_NAMES = (STRATEGY_NAME,)
PREFERRED_ID = "strategy_mz1_ma1020_single_ma5band"

STRATEGY_CODE = r'''# 买：马总选股逻辑1 — 涨停后第一次跌破 MA10/MA20 各买 1/2（单点 + MA5带过滤）
# - 触发价 = 均线重合点（行情「10日」「20日」）；不买 MA5 腿
# - 规则类型 single_buy：现价 <= 触发价即买
# - MA5带：生成时用触发价相对当日早盘「5日」偏离% 过滤；
#   默认须落在 [ma5_band_low_pct, ma5_band_high_pct]=[-5, +3]，否则本腿不挂
# - halt_on_open_gain：开盘相对涨幅超限则停买（主板5%/其它10%）
# - 首次跌破：生成时用日线查选股日之后～今天之前，挂单窗内是否已有 low<=早盘MA；
#   已破过的腿不再挂
# - 腿名固定：马总1单点带-跌破MA10 / MA20
# - 回测：params.selection_date_by_code[code]=选股日
#   挂单窗口：选股日下一交易日起，连续 entry_window_trading_days 个交易日（默认 10）
# - 回测已买腿：params._filled_legs 含 leg_key（如 000001:MA10）则不再挂
# - 疑似除权：近 ex_div_lookback 交易日开盘异常跳空 → 本票停买
#
# params：buy_amount_per_stock, min_order_amount,
#         entry_window_trading_days(=10), selection_date_by_code, _filled_legs,
#         ex_div_lookback(=20),
#         ma5_band_low_pct(=-5), ma5_band_high_pct(=3)

LEG_SPECS = (
    ("MA10", "10日", "马总1单点带-跌破MA10", 10),
    ("MA20", "20日", "马总1单点带-跌破MA20", 20),
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
    try:
        ma5_lo = float(params.get("ma5_band_low_pct", -5) or -5)
    except (TypeError, ValueError):
        ma5_lo = -5.0
    try:
        ma5_hi = float(params.get("ma5_band_high_pct", 3) or 3)
    except (TypeError, ValueError):
        ma5_hi = 3.0
    if ma5_lo > ma5_hi:
        ma5_lo, ma5_hi = ma5_hi, ma5_lo

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

    half = amount_per / 2.0

    def _already_touched(c6, sel_d, start_d, end_d, ma_period):
        if sel_d is None or start_d is None or end_d is None:
            return False
        try:
            from utils.first_ma_touch import already_touched_ma_in_entry_window

            hit, _ = already_touched_ma_in_entry_window(
                c6,
                selection_date=sel_d,
                before_date=trade_d,
                ma_period=int(ma_period),
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
                    ma_period=int(ma_period),
                    entry_start=start_d,
                    entry_end=end_d,
                )
                return bool(hit)
            except Exception:
                return False

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

        sel_d = _parse_d(sel_map.get(c6) or sel_map.get(code))
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

        # 当日早盘 MA5：生成时已知，用于触发价偏离过滤
        ma5 = None
        raw5 = p.get("5日")
        if raw5 is not None and raw5 != "":
            try:
                ma5 = float(raw5)
            except (TypeError, ValueError):
                ma5 = None
        if ma5 is not None and ma5 <= 0:
            ma5 = None

        for leg_id, field, rule_name, ma_period in LEG_SPECS:
            leg_key = "%s:%s" % (c6, leg_id)
            if leg_key in filled:
                print("[马总1单点带] skip filled %s" % leg_key)
                continue
            if _already_touched(c6, sel_d, start_d, end_d, ma_period):
                print("[马总1单点带] skip already_touched %s" % leg_key)
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

            # MA5带：触发价相对早盘MA5；缺MA5则无法判定 → 仍挂（避免行情缺字段整批空）
            if ma5 is not None and ma5 > 0:
                dev_pct = (float(trig) / float(ma5) - 1.0) * 100.0
                if not (ma5_lo <= dev_pct <= ma5_hi):
                    print(
                        "[马总1单点带] skip ma5_band %s trig=%.2f ma5=%.2f dev=%.2f%% not in [%.1f,%.1f]"
                        % (leg_key, trig, ma5, dev_pct, ma5_lo, ma5_hi)
                    )
                    continue

            v = vol_for(half, trig)
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
                "halt_on_open_gain": True,
            })
    return result'''


def main() -> None:
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
            if str(raw.get("name") or "") in LEGACY_NAMES:
                existing_id = str(raw.get("id") or "")
                prev = raw if isinstance(raw, dict) else {}
                break
    sid = existing_id or PREFERRED_ID or rid
    prev_params = prev.get("strategy_params") if isinstance(prev.get("strategy_params"), dict) else {}

    # 尽量从现有「单点」或「弹性半仓」策略拷贝股票池 / 选股日（优先非空池）
    seed_names = (
        "买：马总逻辑1-涨停后跌破MA10/20各1/2-单点",
        "买：马总逻辑1-涨停后跌破MA10/20各1/2",
    )
    seed: dict = {}
    best_n = -1
    for p in OUT_DIR.glob("strategy_*.json"):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(raw.get("name") or "") not in seed_names:
            continue
        n = len(raw.get("stock_codes") or [])
        if n > best_n:
            best_n = n
            seed = raw if isinstance(raw, dict) else {}
    seed_params = seed.get("strategy_params") if isinstance(seed.get("strategy_params"), dict) else {}

    def _pick(key, default):
        if key in prev_params and prev_params.get(key) not in (None, "", [], {}):
            return prev_params.get(key)
        if key in seed_params and seed_params.get(key) not in (None, "", [], {}):
            return seed_params.get(key)
        return default

    sp = {
        "buy_amount_per_stock": float(_pick("buy_amount_per_stock", 50000) or 50000),
        "min_order_amount": float(_pick("min_order_amount", 5000) or 5000),
        "sizing_mode": str(_pick("sizing_mode", "fixed") or "fixed"),
        "entry_window_trading_days": int(_pick("entry_window_trading_days", 10) or 10),
        "selection_date_by_code": dict(_pick("selection_date_by_code", {}) or {}),
        "_filled_legs": list(_pick("_filled_legs", []) or []),
        "ex_div_lookback": int(_pick("ex_div_lookback", 20) or 20),
        "ma5_band_low_pct": float(prev_params.get("ma5_band_low_pct", -5) or -5),
        "ma5_band_high_pct": float(prev_params.get("ma5_band_high_pct", 3) or 3),
    }
    for k, v in prev_params.items():
        if k not in sp:
            sp[k] = v

    stock_codes = list(prev.get("stock_codes") or [])
    if not stock_codes:
        stock_codes = list(seed.get("stock_codes") or [])

    out = {
        "id": sid,
        "name": STRATEGY_NAME,
        "enabled": bool(prev.get("enabled", True)),
        "stock_codes": stock_codes,
        "strategy_params": sp,
        "strategy_code": STRATEGY_CODE,
        "scheduled_generate_at": prev.get("scheduled_generate_at"),
    }
    path_by_id = OUT_DIR / ("%s.json" % sid)
    path_by_id.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", path_by_id)
    print("name", STRATEGY_NAME)
    print(
        "pool=%d sel=%d ma5_band=[%.1f, %.1f]"
        % (
            len(out["stock_codes"]),
            len(sp["selection_date_by_code"]),
            sp["ma5_band_low_pct"],
            sp["ma5_band_high_pct"],
        )
    )
    print(
        "用法：python tools/load_ma_zong1_pool_into_strategy.py "
        '--name "%s"' % STRATEGY_NAME
    )


if __name__ == "__main__":
    main()
