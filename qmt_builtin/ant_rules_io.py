#coding:gbk
"""rules_armed.json / results.json ��д��QMT ���ò��Բࣩ��"""
import json
import os
import shutil
import tempfile
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    from ant_qmt_paths import PROJECT_ROOT, RESULTS_PATH, RULES_ARMED_PATH
except ImportError:
    from qmt_builtin.ant_qmt_paths import PROJECT_ROOT, RESULTS_PATH, RULES_ARMED_PATH

RULES_VERSION = 1
RESULTS_VERSION = 1
# QMT ���ò�����ѯ rules_armed.json �ļ�����룩
RULES_RELOAD_INTERVAL_SEC = 1
RESULTS_FLUSH_INTERVAL_SEC = 1


def default_paths(root: Optional[str] = None) -> Tuple[str, str]:
    base = (root or PROJECT_ROOT).rstrip("\\/")
    return (
        os.path.join(base, "data", "rules_armed.json"),
        os.path.join(base, "data", "results.json"),
    )


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def save_json_atomic(path: str, data: Dict[str, Any]) -> None:
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        last_err = None
        for attempt in range(12):
            try:
                os.replace(tmp, path)
                tmp = ""
                return
            except OSError as e:
                last_err = e
                if attempt >= 7:
                    try:
                        shutil.copy2(tmp, path)
                        tmp = ""
                        return
                    except OSError:
                        pass
                time.sleep(0.08 * (attempt + 1))
        if last_err is not None:
            raise last_err
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def normalize_armed_task(raw: Dict[str, Any]) -> Dict[str, Any]:
    code = str(raw.get("stock_code") or raw.get("code") or "").strip().upper()
    rule_type = str(raw.get("rule_type") or raw.get("type") or "breakthrough_buy").strip()
    if not rule_type:
        rule_type = "breakthrough_buy"
    default_name = {
        "single_buy": "��������",
        "single_sell": "��������",
        "breakthrough_buy": "ͻ������",
        "breakthrough_sell": "ͻ������",
        "best_sell": "��������",
        "best_buy": "��������",
        "cage_buy": "��������",
        "cage_sell": "��������",
        "grid_buy": "��������",
        "grid_sell": "��������",
        "scheduled_clear": "��ʱ���",
        "night_buy": "ҹ������",
        "night_sell": "ҹ������",
    }.get(rule_type, "ͻ������")

    executed_grids = []
    for x in raw.get("executed_grids") or []:
        try:
            executed_grids.append(int(x))
        except (TypeError, ValueError):
            pass

    out = {
        "task_id": str(raw.get("task_id") or code or "task"),
        "stock_code": code,
        "rule_type": rule_type,
        "strategy_name": str(raw.get("strategy_name") or default_name),
        "trigger_price": float(raw.get("trigger_price") or 0),
        "require_break_below": bool(raw.get("require_break_below", False)),
        "break_below_trigger_done": bool(raw.get("break_below_trigger_done", False)),
        "require_break_above": bool(raw.get("require_break_above", False)),
        "break_above_trigger_done": bool(raw.get("break_above_trigger_done", False)),
        "drop_percent": float(raw.get("drop_percent") or 0),
        "rise_percent": float(raw.get("rise_percent") or 0),
        "rise_scale": raw.get("rise_scale"),
        "max_rise_percent": raw.get("max_rise_percent"),
        "room_blend_start": raw.get("room_blend_start"),
        "pullback_price": raw.get("pullback_price"),
        "confirm_ticks": raw.get("confirm_ticks"),
        "cooldown_after_extreme_ticks": raw.get("cooldown_after_extreme_ticks"),
        "dynamic_thresholds": raw.get("dynamic_thresholds"),
        "price_low": float(raw.get("price_low") or 0),
        "price_high": float(raw.get("price_high") or 0),
        "wall_thickness": float(raw.get("wall_thickness") or 0),
        "cage_entered": bool(raw.get("cage_entered", False)),
        "start_price": float(raw.get("start_price") or 0),
        "end_price": float(raw.get("end_price") or 0),
        "num_grids": int(raw.get("num_grids") or 0),
        "grid_step": float(raw.get("grid_step") or 0),
        "volume_per_grid": int(raw.get("volume_per_grid") or 0),
        "executed_grids": executed_grids,
        "scheduled_clear_time": str(
            raw.get("scheduled_clear_time") or "14:56:00"
        ).strip(),
        "scheduled_clear_effective_date": str(
            raw.get("scheduled_clear_effective_date") or ""
        ).strip(),
        "enabled": bool(raw.get("enabled", True)),
        "true_breakthrough_cond1_mode": str(
            raw.get("true_breakthrough_cond1_mode")
            or raw.get("cond1_mode")
            or "tick3"
        ),
        "max_volume": int(raw.get("max_volume") or raw.get("volume") or 0),
        "metadata": dict(raw.get("metadata") or {}),
    }
    if "early_order_enabled" in raw:
        out["early_order_enabled"] = bool(raw.get("early_order_enabled"))
    if "require_true_breakthrough" in raw:
        out["require_true_breakthrough"] = bool(raw.get("require_true_breakthrough"))
    if "wait_unseal" in raw:
        out["wait_unseal"] = bool(raw.get("wait_unseal"))
    if "fill_at_limit_up" in raw:
        out["fill_at_limit_up"] = bool(raw.get("fill_at_limit_up"))
    if "open_buy_ask" in raw:
        out["open_buy_ask"] = bool(raw.get("open_buy_ask"))
    try:
        lu = float(raw.get("limit_up") or 0)
        if lu > 0:
            out["limit_up"] = lu
    except (TypeError, ValueError):
        pass
    for bk in (
        "band_low",
        "band_high",
        "band_accept_low",
        "accept_band_low",
        "true_breakthrough_window_sec",
        "true_breakthrough_cond1_mode",
        "price",
        "volume",
        "require_break_below",
    ):
        if raw.get(bk) is None or str(raw.get(bk)).strip() == "":
            continue
        if bk in ("true_breakthrough_cond1_mode",):
            out[bk] = str(raw.get(bk)).strip()
            continue
        try:
            if bk in ("true_breakthrough_window_sec", "volume"):
                out[bk] = int(float(raw.get(bk)))
            else:
                out[bk] = float(raw.get(bk))
        except (TypeError, ValueError):
            pass
    if out.get("band_accept_low") is None and out.get("accept_band_low") is not None:
        try:
            out["band_accept_low"] = float(out["accept_band_low"])
        except (TypeError, ValueError):
            pass
    return out


def normalize_watch_codes(codes: Any) -> List[str]:
    out: List[str] = []
    seen = set()
    if not codes:
        return out
    items = codes if isinstance(codes, list) else [codes]
    for raw in items:
        code = str(raw or "").strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return sorted(out)


def collect_subscribe_codes(
    tasks: List[Dict[str, Any]],
    watch_codes: Optional[List[str]] = None,
    strategy_pool_watch: Optional[List[str]] = None,
) -> List[str]:
    """tasks �� watch_codes �� strategy_pool_watch �� subscribe_whole_quote �б���"""
    codes = set()
    for item in tasks or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("stock_code") or item.get("code") or "").strip().upper()
        if code:
            codes.add(code)
    for code in normalize_watch_codes(watch_codes):
        codes.add(code)
    for code in normalize_watch_codes(strategy_pool_watch):
        codes.add(code)
    return sorted(codes)


def collect_live_subscribe_codes(
    tasks: List[Dict[str, Any]],
    watch_codes: Optional[List[str]] = None,
) -> List[str]:
    """tasks + watch_codes������ strategy_pool_watch���������� pool ���ͷź�������ġ�"""
    return collect_subscribe_codes(tasks, watch_codes, strategy_pool_watch=None)


def prune_results_stocks(results: Dict[str, Any], keep_codes: Any) -> int:
    """�Ƴ� results.stocks �в��� keep_codes ����Ŀ������ strategy_pool ���͡�"""
    if not isinstance(results, dict):
        return 0
    stocks = results.get("stocks")
    if not isinstance(stocks, dict):
        return 0
    keep = {str(c).strip().upper() for c in (keep_codes or []) if c}
    removed = 0
    for code in list(stocks.keys()):
        norm = str(code).strip().upper()
        if norm not in keep:
            del stocks[code]
            removed += 1
    results["stocks"] = stocks
    if removed:
        results["updated_at"] = _now_iso()
    return removed


def rules_file_signature(path: str) -> str:
#  rules_armed.json mtime + updated_at
    if not os.path.isfile(path):
        return ""
    try:
        mtime = os.path.getmtime(path)
        data = load_json(path)
        updated = str(data.get("updated_at") or "")
        ver = str(data.get("version") or "")
        n_tasks = len(data.get("tasks") or [])
        n_watch = len(data.get("watch_codes") or [])
        n_pool = len(data.get("strategy_pool_watch") or [])
        return f"{mtime:.6f}|{updated}|{ver}|{n_tasks}|{n_watch}|{n_pool}"
    except OSError:
        return ""


def load_rules_armed(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {
            "version": RULES_VERSION,
            "trade_date": "",
            "updated_at": _now_iso(),
            "tasks": [],
            "watch_codes": [],
            "strategy_pool_watch": [],
        }
    data = load_json(path)
    tasks_in = data.get("tasks") or []
    tasks: List[Dict[str, Any]] = []
    if isinstance(tasks_in, list):
        for item in tasks_in:
            if isinstance(item, dict):
                tasks.append(normalize_armed_task(item))
    return {
        "version": int(data.get("version") or RULES_VERSION),
        "trade_date": str(data.get("trade_date") or ""),
        "updated_at": str(data.get("updated_at") or ""),
        "tasks": [t for t in tasks if t.get("enabled") and t.get("stock_code")],
        "watch_codes": normalize_watch_codes(data.get("watch_codes")),
        "strategy_pool_watch": normalize_watch_codes(data.get("strategy_pool_watch")),
        "orders_enabled": bool(data.get("orders_enabled", True)),
        "early_order_enabled": bool(data.get("early_order_enabled", False)),
        "min_buy_amount": float(data.get("min_buy_amount") or 0),
        "buy_block_window_enabled": bool(data.get("buy_block_window_enabled", False)),
        "buy_block_start": str(data.get("buy_block_start") or "09:30:00"),
        "buy_block_end": str(data.get("buy_block_end") or "09:31:30"),
    }


def empty_results(mode: str = "shadow", trade_date: str = "") -> Dict[str, Any]:
    return {
        "version": RESULTS_VERSION,
        "trade_date": trade_date,
        "updated_at": _now_iso(),
        "mode": mode,
        "stocks": {},
    }


def append_stock_event(
    results: Dict[str, Any],
    stock_code: str,
    event: Dict[str, Any],
    *,
    last_price: Optional[float] = None,
    last_tick_time: str = "",
) -> None:
    stocks = results.setdefault("stocks", {})
    bucket = stocks.setdefault(
        stock_code,
        {
            "last_price": 0.0,
            "last_tick_time": "",
            "today_open": 0.0,
            "today_high": 0.0,
            "today_low": 0.0,
            "events": [],
        },
    )
    if last_price is not None:
        bucket["last_price"] = float(last_price)
    if last_tick_time:
        bucket["last_tick_time"] = last_tick_time
    events = bucket.setdefault("events", [])
    events.append(event)
    # shadow        200         
    if len(events) > 200:
        bucket["events"] = events[-200:]
    results["updated_at"] = _now_iso()


def _first_level_px(raw: Any) -> float:
    """������һ���������� list/tuple �������"""
    try:
        if isinstance(raw, (list, tuple)):
            if not raw:
                return 0.0
            v = float(raw[0])
            return v if v > 0 else 0.0
        if raw is None:
            return 0.0
        v = float(raw)
        return v if v > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _in_opening_call_auction_window() -> bool:
    """A �ɿ��̼��Ͼ��� 09:15�C09:30���� 9:25 ��Ϻ�����������ǰ����"""
    try:
        t = datetime.now().time()
    except Exception:
        return False
    return (t.hour == 9) and (15 <= t.minute < 30)


def _in_call_auction_indicative_window() -> bool:
    """09:15�C09:24������ƥ��/δ�ɽ��ο��۽׶Σ�����ʽ���̳ɽ�����"""
    try:
        t = datetime.now().time()
    except Exception:
        return False
    return (t.hour == 9) and (15 <= t.minute < 25)


def extract_tick_price(row: Dict[str, Any]) -> float:
    """���¼ۣ����Ͼ��۽׶� lastPrice ��Ϊ 0����������һ���ο��ۣ�����ƥ��ۣ���"""
    for key in ("lastPrice", "last_price", "tradePrice", "matchPrice", "price", "last"):
        raw = row.get(key)
        if raw is None:
            continue
        try:
            val = float(raw)
            if val > 0:
                return val
        except (TypeError, ValueError):
            continue
    # 09:15�C09:30��ȫ��/���������¼�δд��������һ���Ǽ��Ͼ��۲ο���
    if _in_opening_call_auction_window():
        bid = _first_level_px(row.get("bidPrice"))
        ask = _first_level_px(row.get("askPrice"))
        if bid > 0 and ask > 0:
            return bid if abs(bid - ask) < 1e-9 else (bid + ask) / 2.0
        if bid > 0:
            return bid
        if ask > 0:
            return ask
    return 0.0


def extract_tick_open(row: Dict[str, Any]) -> float:
    """�� subscribe_whole_quote ����ȡ�����ֶΡ�"""
    for key in ("open", "openPrice", "open_price", "todayOpen"):
        raw = row.get(key)
        if raw is None:
            continue
        try:
            val = float(raw)
            if val > 0:
                return val
        except (TypeError, ValueError):
            continue
    # �� 9:25 �������òο��۳俪�̣�9:15�C9:24 ����۲��õ�����
    if _in_opening_call_auction_window() and not _in_call_auction_indicative_window():
        px = extract_tick_price(row)
        if px > 0:
            return px
    return 0.0


def extract_tick_high_low(row: Dict[str, Any]) -> Tuple[float, float]:
    hi = lo = 0.0
    for key in ("high", "highPrice", "todayHigh"):
        raw = row.get(key)
        if raw is None:
            continue
        try:
            val = float(raw)
            if val > 0:
                hi = val
                break
        except (TypeError, ValueError):
            continue
    for key in ("low", "lowPrice", "todayLow"):
        raw = row.get(key)
        if raw is None:
            continue
        try:
            val = float(raw)
            if val > 0:
                lo = val
                break
        except (TypeError, ValueError):
            continue
    return hi, lo


def update_price_snapshot(
    results: Dict[str, Any],
    stock_code: str,
    last_price: float,
    last_tick_time: str = "",
    tick_row: Optional[Dict[str, Any]] = None,
) -> bool:
    """�����ּۣ�tick_row ����ʱͬ����/���ոߵͣ�����ʱ�β��������ã���

    ���ոߵ�ֻ�ϡ����̺�ɽ��ۡ��켣������ 9:15�C9:24 ���Ͼ�������ƥ���
    ���������ǵ�ͣ����Ⱦ today_low/today_high��
    """
    code = str(stock_code or "").strip().upper()
    if not code or last_price <= 0:
        return False
    stocks = results.setdefault("stocks", {})
    bucket = stocks.setdefault(
        code,
        {
            "last_price": 0.0,
            "last_tick_time": "",
            "today_open": 0.0,
            "today_high": 0.0,
            "today_low": 0.0,
            "events": [],
        },
    )
    changed = False
    new_price = float(last_price)
    if abs(float(bucket.get("last_price") or 0) - new_price) > 1e-9:
        bucket["last_price"] = new_price
        changed = True
    tick_time = str(last_tick_time or "").strip()
    if tick_time and bucket.get("last_tick_time") != tick_time:
        bucket["last_tick_time"] = tick_time
        changed = True

    row = tick_row if isinstance(tick_row, dict) else {}
    open_px = extract_tick_open(row) if row else 0.0
    if open_px > 0 and float(bucket.get("today_open") or 0) <= 0:
        bucket["today_open"] = open_px
        changed = True
    elif (
        float(bucket.get("today_open") or 0) <= 0
        and new_price > 0
        and not _in_call_auction_indicative_window()
    ):
        # 9:15�C9:24 ���������������
        bucket["today_open"] = new_price
        changed = True

    open_px = float(bucket.get("today_open") or 0) or float(open_px or 0)

    # ���⾺�۽׶Σ���д���ոߵ�
    if _in_call_auction_indicative_window():
        if changed:
            results["updated_at"] = _now_iso()
        return changed

    # 9:25 ���Կ���+�ɽ���ά���ߵͣ�����Դ low ���񾺼����⼫ֵ�����
    tick_hi, tick_lo = extract_tick_high_low(row) if row else (0.0, 0.0)
    cur_hi = float(bucket.get("today_high") or 0)
    cur_lo = float(bucket.get("today_low") or 0)

    trusted_lo = 0.0
    if tick_lo > 0:
        if new_price > 0 and new_price <= float(tick_lo) + 1e-6:
            trusted_lo = float(tick_lo)
        elif open_px > 0 and float(tick_lo) + 1e-9 >= open_px:
            trusted_lo = float(tick_lo)
        elif open_px > 0 and new_price + 1e-9 >= open_px * 0.98 and float(tick_lo) < open_px * 0.92:
            trusted_lo = 0.0  # �ּ��ѻؿ��̸������ٷ� low ȴ���ң���Ϊ���۵�ͣ����ۣ�
        else:
            trusted_lo = float(tick_lo)

    polluted = (
        open_px > 0
        and cur_lo > 0
        and cur_lo + 1e-9 < open_px * 0.92
        and new_price + 1e-9 >= open_px * 0.98
    )
    seed_needed = (not bool(bucket.get("hl_from_trades"))) or polluted
    trade_pts = [p for p in (open_px, new_price) if p and float(p) > 0]

    if seed_needed and trade_pts:
        hi0 = float(max(trade_pts))
        lo0 = float(min(trade_pts))
        if trusted_lo > 0:
            lo0 = min(lo0, trusted_lo)
        if tick_hi > 0:
            hi0 = max(hi0, float(tick_hi))
        bucket["today_high"] = hi0
        bucket["today_low"] = lo0
        bucket["hl_from_trades"] = True
        changed = True
    elif trade_pts:
        if new_price > 0 and (cur_hi <= 0 or new_price > cur_hi + 1e-12):
            bucket["today_high"] = new_price if cur_hi <= 0 else max(cur_hi, new_price)
            changed = True
        if tick_hi > 0:
            cur_hi = float(bucket.get("today_high") or 0)
            if cur_hi <= 0 or tick_hi > cur_hi + 1e-12:
                bucket["today_high"] = float(tick_hi)
                changed = True
        if new_price > 0 and (cur_lo <= 0 or new_price < cur_lo - 1e-12):
            bucket["today_low"] = new_price if cur_lo <= 0 else min(cur_lo, new_price)
            changed = True
        if trusted_lo > 0:
            cur_lo = float(bucket.get("today_low") or 0)
            if cur_lo <= 0 or trusted_lo < cur_lo - 1e-12:
                bucket["today_low"] = trusted_lo if cur_lo <= 0 else min(cur_lo, trusted_lo)
                changed = True
            elif (
                cur_lo > 0
                and trusted_lo > cur_lo + 1e-12
                and open_px > 0
                and cur_lo + 1e-9 < open_px
            ):
                bucket["today_low"] = float(trusted_lo)
                changed = True

    if changed:
        results["updated_at"] = _now_iso()
    return changed


def load_results_prices(path: str) -> Dict[str, Dict[str, Any]]:
#  results.json  UI 
    if not os.path.isfile(path):
        return {}
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    stocks = data.get("stocks") or {}
    if not isinstance(stocks, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for code, bucket in stocks.items():
        if not isinstance(bucket, dict):
            continue
        price = float(bucket.get("last_price") or 0)
        if price <= 0:
            continue
        norm = str(code or "").strip().upper()
        if not norm:
            continue
        out[norm] = {
            "last_price": price,
            "last_tick_time": str(bucket.get("last_tick_time") or ""),
            "today_open": float(bucket.get("today_open") or 0),
            "today_high": float(bucket.get("today_high") or 0),
            "today_low": float(bucket.get("today_low") or 0),
            "updated_at": str(data.get("updated_at") or ""),
        }
    return out


def load_account_positions_snapshot(path: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """�� results.json ��ȡ���ò���д����ʽ�/�ֲ֣����ⲿ������ UI����"""
    if not os.path.isfile(path):
        return None, {}
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError):
        return None, {}
    account = data.get("account") if isinstance(data.get("account"), dict) else {}
    pos_root = data.get("positions") if isinstance(data.get("positions"), dict) else {}
    if not account and not pos_root:
        return None, {}

    asset = None
    if account:
        total = float(account.get("total_asset") or 0)
        cash = float(account.get("cash") or 0)
        if total > 0 or cash != 0:
            asset = {
                "total_asset": total,
                "cash": cash,
                "frozen_cash": float(account.get("frozen_cash") or 0),
                "market_value": float(account.get("market_value") or 0),
                "account_id": str(account.get("account_id") or ""),
            }

    get_name = None
    try:
        from utils.stock_info_manager import get_stock_name as _get_stock_name

        get_name = _get_stock_name
    except Exception:
        get_name = None

    positions: Dict[str, Dict[str, Any]] = {}
    for code, meta in pos_root.items():
        if not isinstance(meta, dict):
            continue
        norm = str(code or "").strip().upper()
        if not norm:
            continue
        vol = int(meta.get("volume") or 0)
        if vol <= 0:
            continue
        name = str(meta.get("stock_name") or "").strip()
        if (not name or name in ("δ֪����", "δ֪")) and get_name is not None:
            try:
                name = str(get_name(norm.split(".")[0]) or "").strip()
            except Exception:
                name = name or ""
            if name in ("δ֪����", "δ֪"):
                name = ""
        positions[norm] = {
            "account_id": str(meta.get("account_id") or account.get("account_id") or ""),
            "stock_code": norm,
            "stock_name": name,
            "volume": vol,
            "can_use_volume": int(meta.get("can_use_volume") or vol),
            "open_price": float(meta.get("open_price") or meta.get("cost_price") or 0),
            "market_value": float(meta.get("market_value") or 0),
        }
    return asset, positions


def load_orders_snapshot(path: str) -> list:
    """�� results.json ��ȡ���� passorder �������������򶩵��б�/ͼ��״̬����"""
    if not os.path.isfile(path):
        return []
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError):
        return []
    raw = data.get("orders") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, dict):
            out.append(dict(item))
    return out


def load_elastic_states_snapshot(path: str) -> dict:
    """�� results.json ��ȡ������������״̬��triggered / ��ֵ�ۣ���"""
    if not os.path.isfile(path):
        return {}
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    raw = data.get("elastic_states") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    out = {}
    for tid, st in raw.items():
        if tid and isinstance(st, dict):
            out[str(tid)] = dict(st)
    return out


def load_broker_orders_snapshot(path: str) -> list:
    """�� results.json ��ȡ�� QMT get_trade_detail_data(ORDER) ���ա�"""
    if not os.path.isfile(path):
        return []
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError):
        return []
    raw = data.get("broker_orders") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, dict):
            out.append(dict(item))
    return out

