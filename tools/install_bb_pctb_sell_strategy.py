# -*- coding: utf-8 -*-
"""安装策略：卖：布林%b回落-斜率/%b/12日次日开盘

持仓每个交易日收盘后检查（策略在次日开盘前用昨收判定）：
  ① MA10 归一斜率 < -0.008 → 次日开盘止损
  ② %b ≥ 0.5 → 次日开盘止盈
  ③ 持仓天数 = 12 → 次日开盘强制离场（引擎买入日=第1日 → 第13日开盘强清）
  ④ 全部不触发则继续持有

日线回测：single_sell 触发价=跌停 → 成交≈开盘。

用法:
  python tools/install_bb_pctb_sell_strategy.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "strategy_generator_app" / "config" / "strategies"

STRATEGY_NAME = "卖：布林%b回落-斜率止损/%b止盈/12日强清"
PREFERRED_ID = "strategy_bb_pctb_sell"
STATE_FILE = "bb_pctb_sell_filled_legs.json"

STRATEGY_CODE = r'''# 卖：布林%b回落
# 昨收检查 → 今日开盘卖出（single_sell@跌停 ≈ 开盘成交）
# ① Slope_norm < MA10_SLOPE_STOP（默认 -0.008）止损
# ② %b >= PCTB_TP（默认 0.5）止盈
# ③ 持仓满 HOLD_DAYS（默认12）后次日开盘强清：code_sell_day_index > HOLD_DAYS
# params：hold_days(=12), ma10_slope_stop(=-0.008), pctb_tp(=0.5),
#         code_sell_day_index（引擎注入）, positions / positions_volume / _filled_legs

NAME_SLOPE = "布林%b卖-MA10斜率止损开盘"
NAME_PCTB = "布林%b卖-%b止盈开盘"
NAME_FORCE = "布林%b卖-满12日次日开盘强清"
LEG_SLOPE = "SLOPE"
LEG_PCTB = "PCTB"
LEG_FORCE = "FORCE12"
STATE_FILE = "bb_pctb_sell_filled_legs.json"
MA10_PERIOD = 10
MA10_SLOPE_DAYS = 5
BB_PERIOD = 20
BB_K = 2.0


def run(codes, prices, get_name, account, params):
    result = []
    params = params or {}
    positions = params.get("positions") or {}
    positions_volume = params.get("positions_volume") or {}

    from datetime import date as _date, datetime as _dt, timedelta
    import json as _json

    try:
        import os as _os
    except Exception:
        _os = None

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

    def _float_param(key, default):
        try:
            v = params.get(key, default)
            return float(v if v is not None else default)
        except (TypeError, ValueError):
            return float(default)

    def _int_param(key, default):
        try:
            v = params.get(key, default)
            return int(v if v is not None else default)
        except (TypeError, ValueError):
            return int(default)

    hold_days = _int_param("hold_days", 12)
    if hold_days < 1:
        hold_days = 12
    slope_stop = _float_param("ma10_slope_stop", -0.008)
    pctb_tp = _float_param("pctb_tp", 0.5)

    code_sell_day_index = params.get("code_sell_day_index") or {}
    if not isinstance(code_sell_day_index, dict):
        code_sell_day_index = {}

    def _project_root():
        if _os is None:
            return ""
        cands = [
            _os.getcwd(),
            _os.path.dirname(_os.getcwd()),
            r"d:\蚂蚁量化系统",
        ]
        for c in cands:
            if c and _os.path.isdir(_os.path.join(c, "data")):
                return c
            if c and _os.path.isdir(_os.path.join(c, "strategy_generator_app")):
                return c
        return _os.getcwd()

    root = _project_root()
    state_path = (
        _os.path.join(root, "data", STATE_FILE)
        if (_os is not None and root)
        else ""
    )

    def _load_state_legs():
        out = set()
        if not state_path or _os is None:
            return out
        try:
            if not _os.path.isfile(state_path):
                return out
            with open(state_path, "r", encoding="utf-8") as f:
                raw = _json.load(f)
            legs = raw.get("legs") if isinstance(raw, dict) else raw
            if isinstance(legs, list):
                out |= set(str(x) for x in legs if x)
            elif isinstance(legs, dict):
                out |= set(str(k) for k, v in legs.items() if v)
        except Exception:
            pass
        return out

    def _save_state_legs(legs_set):
        if not state_path or _os is None:
            return
        try:
            d = _os.path.dirname(state_path)
            if d:
                _os.makedirs(d, exist_ok=True)
            payload = {
                "legs": sorted(set(str(x) for x in legs_set if x)),
                "updated_at": _dt.now().strftime("%Y-%m-%dT%H:%M:%S"),
            }
            tmp = state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                _json.dump(payload, f, ensure_ascii=False, indent=2)
            _os.replace(tmp, state_path)
        except Exception:
            pass

    filled = set()
    filled_raw = params.get("_filled_legs") or []
    if isinstance(filled_raw, dict):
        filled |= set(str(k) for k, v in filled_raw.items() if v)
    else:
        filled |= set(str(x) for x in filled_raw)
    filled |= _load_state_legs()

    def _prev_trading_day(d0):
        try:
            from strategy_generator_app.trading_calendar import get_trading_dates_in_range_sorted
            lst = get_trading_dates_in_range_sorted(d0 - timedelta(days=40), d0)
            prev = [x for x in (lst or []) if x < d0]
            if prev:
                return prev[-1]
        except Exception:
            pass
        d = d0 - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d

    def _indicators_asof(c6, as_d):
        try:
            from utils.bb_pctb_indicators import (
                boll_bands,
                load_closes_for_code,
                ma10_slope_norm,
                pct_b,
            )
        except Exception:
            try:
                from bb_pctb_indicators import (  # type: ignore
                    boll_bands,
                    load_closes_for_code,
                    ma10_slope_norm,
                    pct_b,
                )
            except Exception:
                return None, None, None, "no_indicator_mod"
        closes, src = load_closes_for_code(c6, as_d)
        if not closes:
            return None, None, None, "no_daily:%s" % src
        _k, slope_n, _mas = ma10_slope_norm(
            closes, period=MA10_PERIOD, slope_days=MA10_SLOPE_DAYS
        )
        _mid, upper, lower = boll_bands(closes, period=BB_PERIOD, k=BB_K)
        pb = pct_b(float(closes[-1]), upper, lower)
        return slope_n, pb, float(closes[-1]), src

    check_d = _prev_trading_day(trade_d)

    for code in codes or []:
        c6 = _code6(code)
        if not c6:
            continue
        avail_raw = positions.get(c6, positions.get(code, 0))
        try:
            avail = int(avail_raw or 0)
        except (TypeError, ValueError):
            avail = 0
        hold_raw = positions_volume.get(c6, positions_volume.get(code, avail_raw))
        try:
            hold = int(hold_raw or 0)
        except (TypeError, ValueError):
            hold = avail
        if hold < avail:
            hold = avail
        if avail < 100:
            continue
        avail = (avail // 100) * 100
        if avail < 100:
            continue

        p = prices.get(c6) or prices.get(code) or {}
        if not isinstance(p, dict):
            continue
        try:
            ld = float(p.get("跌停板") or 0)
            lu = float(p.get("涨停板") or 0)
        except (TypeError, ValueError):
            ld, lu = 0.0, 0.0
        if ld <= 0:
            # 无跌停价则用昨收*0.9 近似，保证 high>=触发 → 开盘成交
            try:
                pre = float(p.get("昨收盘") or p.get("pre_close") or 0)
            except (TypeError, ValueError):
                pre = 0.0
            if pre > 0:
                if c6.startswith(("300", "301", "688", "689")):
                    ld = round(pre * 0.8, 2)
                else:
                    ld = round(pre * 0.9, 2)
        if ld <= 0:
            continue

        name = (get_name(c6) if get_name else "") or ""
        try:
            idx = int(code_sell_day_index.get(c6) or code_sell_day_index.get(code) or 0)
        except (TypeError, ValueError):
            idx = 0

        # 满 hold_days 后次日开盘强清：买入日=1 … 第 hold_days 日收盘后 → 第 hold_days+1 日开盘
        force = idx > int(hold_days)
        # 买入当日（idx=1）开盘不根据昨收做斜率/%b 卖出；首检=买入日收盘后 → 次日开盘
        slope_n, pb, last_c, src = None, None, None, ""
        stop_slope = False
        take_pctb = False
        if idx >= 2 or force:
            slope_n, pb, last_c, src = _indicators_asof(c6, check_d)
            if idx >= 2:
                stop_slope = slope_n is not None and float(slope_n) < float(slope_stop)
                take_pctb = pb is not None and float(pb) >= float(pctb_tp)

        reason = None
        leg_id = None
        rule_name = None
        if force:
            reason = "force_hold"
            leg_id = LEG_FORCE
            rule_name = NAME_FORCE
        elif stop_slope:
            reason = "slope"
            leg_id = LEG_SLOPE
            rule_name = NAME_SLOPE
        elif take_pctb:
            reason = "pctb"
            leg_id = LEG_PCTB
            rule_name = NAME_PCTB
        else:
            continue

        leg_key = "%s:%s:%s" % (c6, leg_id, trade_d.strftime("%Y%m%d"))
        if leg_key in filled:
            continue
        # 同日只挂一条开盘清仓
        day_prefix = "%s:" % c6
        if any(
            str(x).startswith(day_prefix) and str(x).endswith(":%s" % trade_d.strftime("%Y%m%d"))
            for x in filled
        ):
            continue

        result.append({
            "stock_code": c6,
            "stock_name": name,
            "rule_type": "single_sell",
            "name": rule_name,
            "leg_key": leg_key,
            "price": float(round(ld, 2)),
            "volume": int(avail),
            "open_sell": True,
            "bb_pctb_reason": reason,
            "bb_pctb_check_date": check_d.isoformat() if check_d else "",
            "bb_pctb_slope_norm": "" if slope_n is None else round(float(slope_n), 8),
            "bb_pctb_pctb": "" if pb is None else round(float(pb), 6),
            "bb_pctb_hold_index": int(idx),
            "bb_pctb_src": src or "",
        })

    if result:
        for it in result:
            lk = it.get("leg_key")
            if lk:
                filled.add(str(lk))
        _save_state_legs(filled)
        params["_filled_legs"] = sorted(filled)
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
            "hold_days": int(prev_params.get("hold_days", 12) or 12),
            "ma10_slope_stop": float(prev_params.get("ma10_slope_stop", -0.008)),
            "pctb_tp": float(prev_params.get("pctb_tp", 0.5)),
            "sell_hold_trading_days": int(prev_params.get("sell_hold_trading_days", 13) or 13),
            "entry_window_trading_days": int(prev_params.get("entry_window_trading_days", 13) or 13),
            "sizing_mode": "fixed",
            "_filled_legs": [],
        },
        "strategy_code": STRATEGY_CODE,
        "scheduled_generate_at": None,
    }
    compile(STRATEGY_CODE, PREFERRED_ID, "exec")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", path)
    print("name", STRATEGY_NAME)
    print("params hold_days=12 slope_stop=-0.008 pctb_tp=0.5 → next open")


if __name__ == "__main__":
    raise SystemExit(main())
