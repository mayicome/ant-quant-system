# -*- coding: utf-8 -*-
"""安装策略：卖：开盘涨5%半仓+8%清仓+末1456清

规则（基准=当日开盘价）：
  1) 涨超开盘价 +5%：弹性卖出卖一半（best_sell）
  2) 涨超开盘价 +8%：弹性卖出清仓剩余（best_sell，量=当前可卖）
  3) 持有最后一个交易日 14:56 无条件全清（scheduled_clear）

持有天数与接续回测对齐：params.sell_hold_trading_days / scheduled_clear_on_sell_day
（买入次日=持有第1日，第 N 日末 14:56 强清，与综合卖出同口径）。

用法:
  python tools/install_open_rise_sell_strategy.py
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "strategy_generator_app" / "config" / "strategies"

STRATEGY_NAME = "卖：开盘涨5%半仓+8%清仓+末1456清"
PREFERRED_ID = "strategy_open_rise_sell"
STATE_FILE = "open_rise_sell_filled_legs.json"

STRATEGY_CODE = r'''# 卖：开盘涨5%半仓+8%清仓+末1456清
# 基准=当日开盘价；涨超后按 best_sell 回落确认成交。
# params：gain_half_pct(=0.05), gain_clear_pct(=0.08), drop_percent(=2.5),
#         sell_hold_trading_days(=3), scheduled_clear_on_sell_day(=3),
#         scheduled_clear_time(=14:56:00), code_sell_day_index（引擎注入）

NAME_HALF = "开盘涨5%卖半仓"
NAME_CLEAR8 = "开盘涨8%清仓"
NAME_LAST = "末交易日1456全清"
LEG_HALF = "OPEN5"
LEG_CLEAR8 = "OPEN8"
STATE_FILE = "open_rise_sell_filled_legs.json"


def run(codes, prices, get_name, account, params):
    result = []
    params = params or {}
    positions = params.get("positions") or {}
    positions_volume = params.get("positions_volume") or {}

    from datetime import date as _date, datetime as _dt
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

    gain_half = _float_param("gain_half_pct", 0.05)
    gain_clear = _float_param("gain_clear_pct", 0.08)
    drop_pct = _float_param("drop_percent", 2.5)
    if drop_pct < 0:
        drop_pct = 0.0

    hold_n = None
    for _hk in ("scheduled_clear_on_sell_day", "sell_hold_trading_days", "entry_window_trading_days"):
        _hv = params.get(_hk)
        if _hv is None or _hv == "":
            continue
        try:
            _hn = int(_hv)
        except (TypeError, ValueError):
            continue
        if _hn >= 1:
            hold_n = _hn
            break
    if hold_n is None:
        hold_n = 3

    clear_time = str(params.get("scheduled_clear_time") or "14:56:00").strip() or "14:56:00"
    if len(clear_time) == 5:
        clear_time = clear_time + ":00"

    code_sell_day_index = params.get("code_sell_day_index") or {}
    try:
        bt_day_idx = int(params.get("backtest_trade_day_index") or 0)
    except (TypeError, ValueError):
        bt_day_idx = 0

    filled = set()
    raw_legs = params.get("_filled_legs")
    if isinstance(raw_legs, (list, tuple, set)):
        for x in raw_legs:
            s = str(x or "").strip()
            if s:
                filled.add(s)

    def _load_state_legs():
        if _os is None:
            return set()
        try:
            root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
            path = _os.path.join(root, "data", STATE_FILE)
            if not _os.path.isfile(path):
                return set()
            with open(path, "r", encoding="utf-8") as f:
                raw = _json.load(f)
            if isinstance(raw, list):
                return set(str(x) for x in raw if str(x).strip())
        except Exception:
            pass
        return set()

    def _save_state_legs(legs):
        if _os is None:
            return
        try:
            root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
            path = _os.path.join(root, "data", STATE_FILE)
            _os.makedirs(_os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                _json.dump(sorted(legs), f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    if not filled:
        filled = _load_state_legs()

    def _pos_avail(c6):
        raw = positions.get(c6)
        if raw is None and positions_volume:
            raw = positions_volume.get(c6)
        try:
            if isinstance(raw, dict):
                return int(raw.get("available") or raw.get("volume") or 0)
            return int(raw or 0)
        except (TypeError, ValueError):
            return 0

    def _pos_hold(c6):
        raw = positions_volume.get(c6) if positions_volume else None
        if raw is None:
            raw = positions.get(c6)
        try:
            if isinstance(raw, dict):
                return int(raw.get("volume") or 0)
            return int(raw or 0)
        except (TypeError, ValueError):
            return 0

    def _full_lot(v):
        v = int(v or 0)
        if v < 100:
            return 0
        return int(v // 100) * 100

    def _half_lot(v):
        v = _full_lot(v)
        if v < 200:
            return 0
        return max(100, int(v // 200) * 100)

    for lk in list(filled):
        c6 = str(lk).split(":", 1)[0]
        if _pos_hold(c6) < 100:
            filled.discard(lk)
    _save_state_legs(filled)
    params["_filled_legs"] = sorted(filled)

    universe = list(codes or [])
    seen = set()
    for c in list(universe) + list(positions.keys()) + list(positions_volume.keys()):
        c6 = _code6(c)
        if c6 and c6 not in seen:
            seen.add(c6)
            if c6 not in universe:
                universe.append(c6)

    for code in universe:
        c6 = _code6(code)
        if not c6:
            continue
        avail = _full_lot(_pos_avail(c6))
        if avail < 100:
            continue

        try:
            hold_idx = int(code_sell_day_index.get(c6) or 0)
        except (TypeError, ValueError):
            hold_idx = 0
        if hold_idx <= 0:
            hold_idx = bt_day_idx
        # 买入日=1；卖出/弹性从次日(index>=2)起
        if hold_idx < 2:
            if params.get("backtest_trade_date"):
                continue
            hold_idx = 2

        p = prices.get(c6) or prices.get(code) or {}
        if not isinstance(p, dict):
            continue
        try:
            open_px = float(p.get("今开盘") or p.get("open") or 0)
        except (TypeError, ValueError):
            open_px = 0.0
        try:
            limit_up = float(p.get("涨停板") or p.get("limit_up") or 0)
            limit_down = float(p.get("跌停板") or p.get("limit_down") or 0)
        except (TypeError, ValueError):
            limit_up, limit_down = 0.0, 0.0

        name = (get_name(c6) if get_name else "") or ""
        day_tag = trade_d.strftime("%Y%m%d")
        leg_half = "%s:%s:%s" % (c6, LEG_HALF, day_tag)
        leg_clear8 = "%s:%s:%s" % (c6, LEG_CLEAR8, day_tag)

        def _clamp_trig(px):
            if px <= 0:
                return 0.0
            if limit_up > 0 and limit_down > 0:
                return max(limit_down, min(limit_up, px))
            return px

        in_hold_window = hold_idx >= 2 and hold_idx <= int(hold_n)
        is_last_hold_day = hold_idx > 0 and hold_idx == int(hold_n)

        if (in_hold_window or is_last_hold_day) and open_px > 0:
            half_vol = _half_lot(avail)
            if leg_half not in filled and half_vol >= 100:
                trig5 = _clamp_trig(round(open_px * (1.0 + float(gain_half)), 4))
                if trig5 > 0:
                    result.append({
                        "stock_code": c6,
                        "stock_name": name,
                        "rule_type": "best_sell",
                        "name": NAME_HALF,
                        "leg_key": leg_half,
                        "trigger_price": float(trig5),
                        "drop_percent": float(drop_pct),
                        "volume": int(half_vol),
                        "debug_open": float(open_px),
                        "debug_gain_pct": float(gain_half) * 100.0,
                    })
            if leg_clear8 not in filled:
                trig8 = _clamp_trig(round(open_px * (1.0 + float(gain_clear)), 4))
                if trig8 > 0:
                    result.append({
                        "stock_code": c6,
                        "stock_name": name,
                        "rule_type": "best_sell",
                        "name": NAME_CLEAR8,
                        "leg_key": leg_clear8,
                        "trigger_price": float(trig8),
                        "drop_percent": float(drop_pct),
                        "volume": int(avail),
                        "debug_open": float(open_px),
                        "debug_gain_pct": float(gain_clear) * 100.0,
                    })

        if is_last_hold_day and avail >= 100:
            clear_px = 0.0
            if limit_up > 0:
                clear_px = float(round(limit_up, 4))
            result.append({
                "stock_code": c6,
                "stock_name": name,
                "rule_type": "scheduled_clear",
                "name": NAME_LAST,
                "price": clear_px,
                "volume": int(avail),
                "scheduled_clear_time": clear_time,
                "scheduled_clear_force": True,
                "scheduled_clear_on_hold_day": True,
                "scheduled_clear_sell_day_index": int(hold_n),
            })

    return result
'''


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
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
    sid = existing_id or PREFERRED_ID or ("strategy_" + uuid.uuid4().hex[:8])
    prev_params = prev.get("strategy_params") if isinstance(prev.get("strategy_params"), dict) else {}
    sp = {
        "gain_half_pct": float(prev_params.get("gain_half_pct", 0.05) or 0.05),
        "gain_clear_pct": float(prev_params.get("gain_clear_pct", 0.08) or 0.08),
        "drop_percent": float(prev_params.get("drop_percent", 2.5) or 2.5),
        "sell_hold_trading_days": int(prev_params.get("sell_hold_trading_days", 3) or 3),
        "scheduled_clear_on_sell_day": int(
            prev_params.get("scheduled_clear_on_sell_day")
            or prev_params.get("sell_hold_trading_days", 3)
            or 3
        ),
        "scheduled_clear_time": str(prev_params.get("scheduled_clear_time") or "14:56:00"),
        "min_order_amount": float(prev_params.get("min_order_amount", 5000) or 5000),
        "_filled_legs": list(prev_params.get("_filled_legs") or []),
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


if __name__ == "__main__":
    main()
