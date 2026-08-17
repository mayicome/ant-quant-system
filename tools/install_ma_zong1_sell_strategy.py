# -*- coding: utf-8 -*-
"""安装策略：卖：马总逻辑1-开盘涨幅弹性半仓+近涨停弹性半仓+1455破MA20清仓

总仓位（半仓基准）= 本轮持仓周期累计买入；只随买入增加，半仓/部分卖不减；持仓归零重置。
开盘涨幅腿：每日达标则按「总仓位」卖约 50%（OPEN50，每日可触发）。
LU10：按「总仓位」卖约 50%；整段持仓期只触发一次。
两腿独立；同轮都挂则半仓+取整零头并入一腿；只挂一腿则半仓后若剩余不够一手或不够最小单笔金额则并入。
params.positions=可卖；positions_volume=当前持股；positions_baseline=总仓位基准（半仓用）。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "strategy_generator_app" / "config" / "strategies"

STRATEGY_NAME = "卖：马总逻辑1-开盘涨幅弹性半仓+近涨停弹性半仓+1455破MA20清仓"

STRATEGY_CODE = r'''# 卖：马总选股逻辑1
# 总仓位=本轮累计买入（positions_baseline）；半仓按总仓位取半；卖出不降总仓位；持股<100重置
# 开盘涨幅：每日按总仓位半仓(OPEN50)；近10日涨停价：按总仓位半仓(LU10，整段一次)
# 只挂一腿：半仓后剩余不够一手或不够最小单笔金额，则并入本次（对照当前持股 positions_volume）
# params.positions=可卖；positions_volume=当前持股；positions_baseline=总仓位基准
# N = params.scheduled_clear_on_sell_day / sell_hold_trading_days / entry_window_trading_days

NAME_OPEN50 = "马总1卖-开盘涨幅弹性半仓"
NAME_LU10 = "马总1卖-近10日涨停价弹性半仓"

def run(codes, prices, get_name, account, params):
    result = []
    params = params or {}
    positions = params.get("positions") or {}
    positions_volume = params.get("positions_volume") or {}
    positions_baseline = params.get("positions_baseline") or {}

    try:
        drop_pct = float(params.get("drop_percent", 1.0) or 1.0)
    except (TypeError, ValueError):
        drop_pct = 1.0
    if drop_pct < 0:
        drop_pct = 0.0

    from datetime import date as _date, datetime as _dt
    import json as _json

    # 沙箱可能禁 os/open：落盘与读任务失败时仍须生成卖出意图（回测靠 params._filled_legs）
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
        _os.path.join(root, "data", "ma_zong1_sell_filled_legs.json")
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

    def _name_to_leg(c6, rule_name):
        n = str(rule_name or "").strip()
        if n == NAME_OPEN50:
            # 开盘半仓按日；推断旧任务时用无日期键，生成时用带日期键
            return "%s:OPEN50" % c6
        if n == NAME_LU10:
            return "%s:LU10" % c6
        return None

    def _infer_executed_legs():
        """从 current_tasks / rules_armed 推断已成交腿（实盘跨日）。"""
        found = set()
        if _os is None or not root:
            return found
        paths = []
        try:
            from strategy_generator_app.task_builder import get_tasks_file_path
            paths.append(get_tasks_file_path(root))
        except Exception:
            try:
                from task_builder import get_tasks_file_path as _g2
                paths.append(_g2(root))
            except Exception:
                pass
        paths.append(_os.path.join(root, "data", "rules_armed.json"))
        # 兼容按日 current_tasks
        try:
            data_dir = _os.path.join(root, "data")
            if _os.path.isdir(data_dir):
                for fn in _os.listdir(data_dir):
                    if fn.startswith("current_tasks_") and fn.endswith(".xlsx"):
                        continue
                    if fn.startswith("current_tasks") and fn.endswith(".json"):
                        paths.append(_os.path.join(data_dir, fn))
        except Exception:
            pass

        def _walk_tasks(obj):
            if isinstance(obj, dict):
                # tasks map or single task
                if "stock_code" in obj or "params" in obj:
                    c6 = _code6(obj.get("stock_code") or obj.get("code") or "")
                    rules = (obj.get("params") or {}).get("rules") if isinstance(obj.get("params"), dict) else obj.get("rules")
                    if not isinstance(rules, list):
                        rules = []
                    for rule in rules:
                        if not isinstance(rule, dict):
                            continue
                        done = bool(rule.get("executed") or rule.get("scheduled_clear_executed"))
                        if not done:
                            continue
                        lk = rule.get("leg_key") or _name_to_leg(c6, rule.get("name"))
                        if lk:
                            found.add(str(lk))
                else:
                    for v in obj.values():
                        _walk_tasks(v)
            elif isinstance(obj, list):
                for it in obj:
                    _walk_tasks(it)

        for path in paths:
            if not path or not _os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = _json.load(f)
            except Exception:
                continue
            if isinstance(data, dict) and "tasks" in data:
                _walk_tasks(data.get("tasks"))
            else:
                _walk_tasks(data)
        return found

    filled = set()
    filled_raw = params.get("_filled_legs") or []
    if isinstance(filled_raw, dict):
        filled |= set(str(k) for k, v in filled_raw.items() if v)
    else:
        filled |= set(str(x) for x in filled_raw)
    filled |= _load_state_legs()
    filled |= _infer_executed_legs()

    # 持仓归零：按总仓位清腿（positions_volume 优先，否则可卖）
    pos_map = {}
    _src = positions_volume if positions_volume else positions
    for k, v in (_src or {}).items():
        c6 = _code6(k)
        try:
            vol = int(v or 0)
        except (TypeError, ValueError):
            vol = 0
        pos_map[c6] = max(pos_map.get(c6, 0), vol)
    cleared = set()
    for lk in list(filled):
        c6 = str(lk).split(":", 1)[0]
        if pos_map.get(c6, 0) < 100:
            # 仅当这只票出现在本次 codes/positions 语境，或 positions 明确无仓
            if c6 in pos_map or any(_code6(c) == c6 for c in (codes or [])):
                filled.discard(lk)
                cleared.add(c6)
    _save_state_legs(filled)
    # 回写 params，供回测引擎同轮可见
    params["_filled_legs"] = sorted(filled)

    try:
        thr_main = float(params.get("open_gain_main", 0.05) or 0.05)
    except (TypeError, ValueError):
        thr_main = 0.05
    try:
        thr_growth = float(params.get("open_gain_growth", 0.10) or 0.10)
    except (TypeError, ValueError):
        thr_growth = 0.10

    def _open_thr(c6):
        if c6.startswith(("300", "301", "688", "689", "8", "4", "920")):
            return thr_growth
        return thr_main

    def _limit_ratio(c6, name, as_d):
        name_u = str(name or "").upper()
        if c6.startswith(("300", "301", "688", "689")):
            return 0.20
        if c6.startswith(("8", "4", "920")):
            return 0.30
        if "ST" in name_u:
            return 0.10 if as_d >= _date(2026, 7, 6) else 0.05
        return 0.10

    def _half_lot(total):
        total = int(total)
        if total < 100:
            return 0
        total = (total // 100) * 100
        return max(100, (int(total * 0.5 / 100) * 100))

    def _full_lot(avail):
        avail = int(avail)
        if avail < 100:
            return 0
        return (avail // 100) * 100

    def _half_pair_absorb_dust(total):
        """同轮两笔独立半仓：各 half_lot；取整零头并入第二笔，避免剩一手。"""
        total = _full_lot(total)
        if total < 100:
            return 0, 0
        a = _half_lot(total)
        b = total - a
        if b < 100:
            return total, 0
        return a, b

    def _fold_dust(sell_vol, hold, price_min):
        """半仓后相对「当前持股」：剩余不够一手、或剩余金额 < 最小单笔，则并入本次全卖。"""
        sell_vol = _full_lot(sell_vol)
        hold = _full_lot(hold)
        if sell_vol <= 0 or hold <= 0:
            return 0
        sell_vol = min(sell_vol, hold)
        remain = hold - sell_vol
        if remain <= 0:
            return sell_vol
        if remain < 100:
            return hold
        try:
            min_amt = float(params.get("min_order_amount") or 5000)
        except (TypeError, ValueError):
            min_amt = 5000.0
        if min_amt > 0 and price_min > 0 and remain * float(price_min) < min_amt:
            return hold
        return sell_vol

    def _clamp(px, ld, lu):
        px = float(px)
        if ld > 0 and lu > 0:
            return max(ld, min(lu, px))
        return px

    def _recent_lu_trigger(c6, name, as_d):
        try:
            from utils.daily_cache_reader import load_daily_from_cache
        except Exception:
            try:
                from daily_cache_reader import load_daily_from_cache  # type: ignore
            except Exception:
                return None
        df = load_daily_from_cache(c6, through_date=as_d)
        if df is None or getattr(df, "empty", True):
            return None
        try:
            dd = df.copy()
            dd["date"] = dd["date"].map(_parse_d)
            dd = dd.dropna(subset=["date"]).sort_values("date")
            dd = dd[dd["date"] < as_d]
        except Exception:
            return None
        if dd is None or dd.empty or len(dd) < 2:
            return None
        window = dd.tail(11)
        if len(window) < 2:
            return None
        rows = list(window.itertuples(index=False))
        for i in range(len(rows) - 1, 0, -1):
            try:
                prev_c = float(getattr(rows[i - 1], "close"))
                cur_c = float(getattr(rows[i], "close"))
                d_i = getattr(rows[i], "date")
            except Exception:
                continue
            if prev_c <= 0 or cur_c <= 0:
                continue
            lr = _limit_ratio(c6, name, d_i if isinstance(d_i, _date) else as_d)
            lim = round(prev_c * (1.0 + lr), 2)
            inc = (cur_c - prev_c) / prev_c
            ok = abs(cur_c - lim) < 0.02 or inc >= lr * 0.99
            if ok:
                return float(lim)
        return None

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
        base_raw = positions_baseline.get(c6, positions_baseline.get(code, hold))
        try:
            base = int(base_raw or 0)
        except (TypeError, ValueError):
            base = hold
        if base < hold:
            base = hold
        if avail < 100:
            continue
        avail = (avail // 100) * 100
        hold = (hold // 100) * 100
        base = (base // 100) * 100
        if hold < 100 or base < 100:
            continue

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
        try:
            ma20 = float(p.get("20日") or 0)
        except (TypeError, ValueError):
            ma20 = 0.0

        name = (get_name(c6) if get_name else "") or ""
        # 开盘半仓每日可触发：leg_key 带当日，避免被「整段已成交」锁死
        leg_open = "%s:OPEN50:%s" % (c6, trade_d.strftime("%Y%m%d"))
        leg_lu = "%s:LU10" % c6
        half = _half_lot(base)

        thr = _open_thr(c6)
        open_trig = 0.0
        # OPEN50：不看整段 filled，只避开「当日已成交」
        if leg_open not in filled and open_px > 0 and half >= 100:
            open_trig = _clamp(round(open_px * (1.0 + thr), 2), limit_down, limit_up)
        lu_trig = 0.0
        if leg_lu not in filled and half >= 100:
            lu_trig_raw = _recent_lu_trigger(c6, name, trade_d)
            if lu_trig_raw is not None and lu_trig_raw > 0:
                lu_trig = _clamp(round(float(lu_trig_raw), 2), limit_down, limit_up)
        want_open = open_trig > 0
        want_lu = lu_trig > 0

        # 半仓按总仓位基准；并入零头对照当前持股 hold
        if want_open and want_lu:
            open_vol, lu_vol = _half_pair_absorb_dust(base)
            # 两腿合计可能超过当前持股：按持股截断到可分配量
            pair_cap = hold
            if open_vol + lu_vol > pair_cap:
                open_vol = min(open_vol, pair_cap)
                lu_vol = max(0, pair_cap - open_vol)
                if lu_vol < 100:
                    open_vol, lu_vol = pair_cap, 0
        elif want_open:
            px_min = open_trig * (1.0 - float(drop_pct) / 100.0) if open_trig > 0 else 0.0
            open_vol = _fold_dust(half, hold, px_min)
            lu_vol = 0
        elif want_lu:
            px_min = lu_trig * (1.0 - float(drop_pct) / 100.0) if lu_trig > 0 else 0.0
            lu_vol = _fold_dust(half, hold, px_min)
            open_vol = 0
        else:
            open_vol, lu_vol = 0, 0

        if want_open and open_vol >= 100:
            result.append({
                "stock_code": c6,
                "stock_name": name,
                "rule_type": "best_sell",
                "name": NAME_OPEN50,
                "leg_key": leg_open,
                "trigger_price": float(open_trig),
                "drop_percent": float(drop_pct),
                "volume": int(open_vol),
                "half_pair": True,
            })

        if want_lu and lu_vol >= 100:
            result.append({
                "stock_code": c6,
                "stock_name": name,
                "rule_type": "best_sell",
                "name": NAME_LU10,
                "leg_key": leg_lu,
                "trigger_price": float(lu_trig),
                "drop_percent": float(drop_pct),
                "volume": int(lu_vol),
                "half_pair": True,
            })

        if limit_up > 0 and avail >= 100:
            result.append({
                "stock_code": c6,
                "stock_name": name,
                "rule_type": "best_sell",
                "name": "涨停即清仓",
                "trigger_price": float(round(limit_up, 2)),
                "drop_percent": 0.0,
                "volume": int(avail),
            })

        # 破 MA20：每天挂（条件清仓）；到点现价 < MA20 才卖，否则仅取消当日
        if ma20 > 0 and avail >= 100:
            result.append({
                "stock_code": c6,
                "stock_name": name,
                "rule_type": "scheduled_clear",
                "name": "马总1卖-1455破MA20清仓",
                "price": float(round(ma20, 2)),
                "volume": int(avail),
                "scheduled_clear_time": "14:55:00",
                "scheduled_clear_every_day": True,
            })

        # 第 N 日无条件清仓（N 来自运行交易日数/持有天数）；14:56 在破 MA20 之后兜底
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
        if hold_n is not None and avail >= 100:
            result.append({
                "stock_code": c6,
                "stock_name": name,
                "rule_type": "scheduled_clear",
                "name": "马总1卖-第%d日无条件清仓" % int(hold_n),
                "price": 0.0,
                "volume": int(avail),
                "scheduled_clear_time": "14:56:00",
                "scheduled_clear_force": True,
                "scheduled_clear_on_hold_day": True,
                "scheduled_clear_sell_day_index": int(hold_n),
            })

    return result
'''


def main() -> None:
    rid = "strategy_" + uuid.uuid4().hex[:8]
    existing_id = None
    for p in OUT_DIR.glob("strategy_*.json"):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(raw.get("name") or "") == STRATEGY_NAME:
            existing_id = str(raw.get("id") or "")
            if existing_id and p.name != ("%s.json" % existing_id):
                try:
                    p.unlink()
                    print("removed duplicate", p.name)
                except Exception:
                    pass
            break
    sid = existing_id or rid
    prev = {}
    if existing_id:
        prev_path = OUT_DIR / ("%s.json" % existing_id)
        if prev_path.is_file():
            try:
                prev = json.loads(prev_path.read_text(encoding="utf-8"))
            except Exception:
                prev = {}
    sp = dict(prev.get("strategy_params") or {})
    sp.update(
        {
            "drop_percent": float(sp.get("drop_percent") or 1.0),
            "open_gain_main": float(sp.get("open_gain_main") or 0.05),
            "open_gain_growth": float(sp.get("open_gain_growth") or 0.10),
            "entry_window_trading_days": int(sp.get("entry_window_trading_days") or 4),
            "min_order_amount": float(sp.get("min_order_amount") or 5000),
            "_filled_legs": list(sp.get("_filled_legs") or []),
        }
    )
    out = {
        "id": sid,
        "name": STRATEGY_NAME,
        "enabled": bool(prev.get("enabled", True)),
        "stock_codes": list(prev.get("stock_codes") or []),
        "strategy_params": sp,
        "strategy_code": STRATEGY_CODE,
        "scheduled_generate_at": prev.get("scheduled_generate_at"),
    }
    path_by_id = OUT_DIR / ("%s.json" % sid)
    path_by_id.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", path_by_id)
    print("id", sid)
    print("name", STRATEGY_NAME)


if __name__ == "__main__":
    main()
