# -*- coding: utf-8 -*-
"""安装策略：卖：马总短线-涨5%弹性可卖+次日1450清仓

配合「买：马总短线-跌破MA5/10/20各10万-单点」：
  - 卖出窗 = 该票【首次买入日】的下一交易日起，连续 2 个交易日
    （与接续回测「买入次日=持有第1日」对齐；不按选股日切窗）
  - 卖出首日：T+1 下可卖仓位天然只有更早买入的仓位
  - 卖出次日：可卖全部；14:50 无条件清仓
  - 逾期仍持仓：卖出窗之后每个交易日 14:50 继续兜底清仓
  - 日内：开盘涨 5% 触发弹性卖出（best_sell），卖全部当日可卖意图量

用法:
  python tools/install_ma_zong_short_term_sell_strategy.py
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "strategy_generator_app" / "config" / "strategies"

STRATEGY_NAME = "卖：马总短线-涨5%弹性可卖+次日1450清仓"
PREFERRED_ID = "strategy_mz_st_sell"
STATE_FILE = "ma_zong_short_term_sell_filled_legs.json"

STRATEGY_CODE = r'''# 卖：马总短线
# - 卖出窗：自【首次买入日】次日起连续 sell_window_trading_days（默认2）日
#   引擎 code_sell_day_index：买入日=1 → 卖出首日=2、卖出次日=3
# - 卖出首日：positions=可卖（T+1，通常仅更早买入）
# - 卖出次日：可卖全部；14:50 scheduled_clear 无条件清仓
# - 逾期仍持仓（index > 1+sell_window）：每日 14:50 继续兜底清仓
# - 弹性：trigger=今开盘*(1+gain_percent)，best_sell + drop_percent 回落卖出
#
# params：sell_window_trading_days(=2), gain_percent(=0.05), drop_percent(=1.0),
#         code_sell_day_index（引擎注入）, positions / positions_volume / _filled_legs

NAME_ELASTIC = "马总短线卖-开盘涨5%弹性"
NAME_CLEAR = "马总短线卖-次日1450清仓"
NAME_CLEAR_OVERDUE = "马总短线卖-逾期1450清仓"
LEG_ELASTIC_ID = "UP5"
STATE_FILE = "ma_zong_short_term_sell_filled_legs.json"


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

    def _project_root():
        if _os is None:
            return ""
        cands = [
            _os.getcwd(),
            _os.path.dirname(_os.getcwd()),
            r"d:\\蚂蚁量化系统",
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
            with open(state_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
            if isinstance(data, list):
                out |= set(str(x) for x in data)
            elif isinstance(data, dict):
                out |= set(str(k) for k, v in data.items() if v)
        except Exception:
            pass
        return out

    def _save_state_legs(legs):
        if not state_path or _os is None:
            return
        try:
            parent = _os.path.dirname(state_path)
            if parent:
                _os.makedirs(parent, exist_ok=True)
            with open(state_path, "w", encoding="utf-8") as f:
                _json.dump(sorted(legs), f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    filled = set()
    filled_raw = params.get("_filled_legs") or []
    if isinstance(filled_raw, dict):
        filled |= set(str(k) for k, v in filled_raw.items() if v)
    else:
        filled |= set(str(x) for x in filled_raw)
    filled |= _load_state_legs()

    try:
        sell_window = int(params.get("sell_window_trading_days", 2) or 2)
    except (TypeError, ValueError):
        sell_window = 2
    if sell_window < 1:
        sell_window = 1
    try:
        gain_pct = float(params.get("gain_percent", 0.05) or 0.05)
    except (TypeError, ValueError):
        gain_pct = 0.05
    if gain_pct < 0:
        gain_pct = 0.0
    try:
        drop_pct = float(params.get("drop_percent", 1.0) or 1.0)
    except (TypeError, ValueError):
        drop_pct = 1.0
    if drop_pct < 0:
        drop_pct = 0.0

    code_sell_day_index = params.get("code_sell_day_index") or {}
    if not isinstance(code_sell_day_index, dict):
        code_sell_day_index = {}

    def _full_lot(avail):
        avail = int(avail)
        if avail < 100:
            return 0
        return (avail // 100) * 100

    def _pos_avail(c6):
        raw = positions.get(c6)
        if raw is None:
            raw = positions.get(c6.lstrip("0")) if c6 else None
        try:
            return int(raw or 0)
        except (TypeError, ValueError):
            return 0

    def _pos_hold(c6):
        raw = positions_volume.get(c6) if positions_volume else None
        if raw is None:
            raw = positions.get(c6)
        try:
            return int(raw or 0)
        except (TypeError, ValueError):
            return 0

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

        # 买入日=1；卖出首日=2 … 卖出次日=1+sell_window；更大=逾期
        try:
            hold_idx = int(code_sell_day_index.get(c6) or 0)
        except (TypeError, ValueError):
            hold_idx = 0
        if hold_idx < 2:
            # 无首次买入日索引时：有可卖仓也允许当日弹性+收盘清（实盘兜底）
            if params.get("backtest_trade_date"):
                continue
            hold_idx = 2

        sell_day_idx = hold_idx - 1  # 1=卖出首日
        overdue = sell_day_idx > sell_window
        in_sell_window = 1 <= sell_day_idx <= sell_window

        p = prices.get(c6) or prices.get(code) or {}
        if not isinstance(p, dict):
            continue
        try:
            open_px = float(p.get("今开盘") or p.get("open") or 0)
        except (TypeError, ValueError):
            open_px = 0.0
        try:
            limit_up = float(p.get("涨停板") or 0)
            limit_down = float(p.get("跌停板") or 0)
        except (TypeError, ValueError):
            limit_up, limit_down = 0.0, 0.0

        name = (get_name(c6) if get_name else "") or ""
        leg_elastic = "%s:%s:%s" % (c6, LEG_ELASTIC_ID, trade_d.strftime("%Y%m%d"))

        if in_sell_window and leg_elastic not in filled and open_px > 0:
            trig = round(float(open_px) * (1.0 + float(gain_pct)), 2)
            if limit_up > 0 and limit_down > 0:
                trig = max(limit_down, min(limit_up, trig))
            if trig > 0:
                result.append({
                    "stock_code": c6,
                    "stock_name": name,
                    "rule_type": "best_sell",
                    "name": "%s-卖出%s" % (
                        NAME_ELASTIC,
                        "首日" if sell_day_idx == 1 else (
                            "次日" if sell_day_idx == 2 else ("第%d日" % sell_day_idx)
                        ),
                    ),
                    "leg_key": leg_elastic,
                    "trigger_price": float(trig),
                    "drop_percent": float(drop_pct),
                    "volume": int(avail),
                    "limit_down": float(limit_down) if limit_down > 0 else 0.0,
                    "sell_window_day": int(sell_day_idx),
                })

        # 卖出次日清仓；逾期仍持仓则每日 14:50 兜底
        want_clear = (sell_day_idx == sell_window) or overdue
        if want_clear and avail >= 100:
            result.append({
                "stock_code": c6,
                "stock_name": name,
                "rule_type": "scheduled_clear",
                "name": NAME_CLEAR_OVERDUE if overdue else NAME_CLEAR,
                "price": 0.0,
                "volume": int(avail),
                "scheduled_clear_time": "14:50:00",
                "scheduled_clear_force": True,
                "scheduled_clear_every_day": True,
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
        "gain_percent": float(prev_params.get("gain_percent", 0.05) or 0.05),
        "drop_percent": float(prev_params.get("drop_percent", 1.0) or 1.0),
        "sell_window_trading_days": int(prev_params.get("sell_window_trading_days", 2) or 2),
        "min_order_amount": float(prev_params.get("min_order_amount", 5000) or 5000),
        "selection_date_by_code": dict(prev_params.get("selection_date_by_code") or {}),
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
    print("params sell_window=2 gain=5% clear=14:50")


if __name__ == "__main__":
    main()
