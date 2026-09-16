# -*- coding: utf-8 -*-
"""rules_armed.json / results.json 读写（QMT 内置策略侧）。"""
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
# QMT 内置策略轮询 rules_armed.json 的间隔（秒）
RULES_RELOAD_INTERVAL_SEC = 1
RESULTS_FLUSH_INTERVAL_SEC = 1
# 盘中 OHLC 跟随 tick 字段；允许随行情纠正（不锁死只升不降）
HL_SOURCE_TICK_FOLLOW = "tick_follow"
# 兼容旧常量名
HL_SOURCE_TRADES_V2 = HL_SOURCE_TICK_FOLLOW
HL_SOURCE_TRADES_V3 = HL_SOURCE_TICK_FOLLOW


def default_paths(root: Optional[str] = None) -> Tuple[str, str]:
    base = (root or PROJECT_ROOT).rstrip("\\/")
    return (
        os.path.join(base, "data", "rules_armed.json"),
        os.path.join(base, "data", "results.json"),
    )


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _as_int(val: Any, default: int = 0) -> int:
    """整数解析；0 是合法值，不能用 `x or default`（可用=0 会被当成缺字段）。"""
    if val is None or val == "":
        return int(default)
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return int(default)


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
        "single_buy": "单点买入",
        "single_sell": "单点卖出",
        "breakthrough_buy": "突破买入",
        "breakthrough_sell": "突破卖出",
        "best_sell": "弹性卖出",
        "best_buy": "弹性买入",
        "cage_buy": "笼子买入",
        "cage_sell": "笼子卖出",
        "grid_buy": "网格买入",
        "grid_sell": "网格卖出",
        "scheduled_clear": "定时清仓",
        "night_buy": "夜市买入",
        "night_sell": "夜市卖出",
    }.get(rule_type, "突破买入")

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
    if "halt_on_open_gain" in raw:
        out["halt_on_open_gain"] = bool(raw.get("halt_on_open_gain"))
    if "require_true_breakthrough" in raw:
        out["require_true_breakthrough"] = bool(raw.get("require_true_breakthrough"))
    if "wait_unseal" in raw:
        out["wait_unseal"] = bool(raw.get("wait_unseal"))
    if "fill_at_limit_up" in raw:
        out["fill_at_limit_up"] = bool(raw.get("fill_at_limit_up"))
    if "open_buy_ask" in raw:
        out["open_buy_ask"] = bool(raw.get("open_buy_ask"))
    # 已执行分支：腿键随武装任务下发
    meta = out.get("metadata") if isinstance(out.get("metadata"), dict) else {}
    lk = str(raw.get("leg_key") or meta.get("leg_key") or "").strip()
    if lk:
        out["leg_key"] = lk
        meta = dict(meta)
        meta["leg_key"] = lk
        out["metadata"] = meta
    rn = str(meta.get("rule_name") or "").strip()
    if not rn:
        # 兼容顶层 rule_name
        rn = str(raw.get("rule_name") or "").strip()
        if rn:
            meta = dict(meta)
            meta["rule_name"] = rn
            out["metadata"] = meta
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
    """tasks ∪ watch_codes ∪ strategy_pool_watch → subscribe_whole_quote 列表。"""
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
    """tasks + watch_codes（不含 strategy_pool_watch）；仅用于 pool 已释放后的缩订阅。"""
    return collect_subscribe_codes(tasks, watch_codes, strategy_pool_watch=None)


def clear_intraday_ohl_fields(bucket: Dict[str, Any], *, clear_open: bool = True) -> None:
    """清空单票盘中 OHLC（换日 / 迁移时用）。"""
    if not isinstance(bucket, dict):
        return
    if clear_open:
        bucket["today_open"] = 0.0
    bucket["today_high"] = 0.0
    bucket["today_low"] = 0.0
    bucket.pop("hl_from_trades", None)
    bucket["hl_source"] = HL_SOURCE_TICK_FOLLOW


def ensure_results_trade_date(results: Dict[str, Any], trade_date: str) -> bool:
    """results.trade_date 与当日不一致时清空全部盘中 OHLC，避免昨高残留。"""
    if not isinstance(results, dict):
        return False
    td = str(trade_date or "").strip()
    if not td:
        return False
    old = str(results.get("trade_date") or "").strip()
    if old == td:
        return False
    results["trade_date"] = td
    stocks = results.get("stocks")
    if isinstance(stocks, dict):
        for bucket in stocks.values():
            if isinstance(bucket, dict):
                clear_intraday_ohl_fields(bucket, clear_open=True)
    return True


def prune_results_stocks(results: Dict[str, Any], keep_codes: Any) -> int:
    """移除 results.stocks 中不在 keep_codes 的条目，避免 strategy_pool 膨胀。"""
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
        "quotes_recv_at": "",
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
            "quote_recv_at": "",
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
    """买卖盘一档：可能是 list/tuple 或标量。"""
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
    """A 股开盘集合竞价 09:15–09:30（含 9:25 撮合后至连续竞价前）。"""
    try:
        t = datetime.now().time()
    except Exception:
        return False
    return (t.hour == 9) and (15 <= t.minute < 30)


def _in_call_auction_indicative_window() -> bool:
    """09:15–09:24：虚拟匹配/未成交参考价阶段（非正式开盘成交）。"""
    try:
        t = datetime.now().time()
    except Exception:
        return False
    return (t.hour == 9) and (15 <= t.minute < 25)


def extract_tick_price(row: Dict[str, Any]) -> float:
    """最新价；集合竞价阶段 lastPrice 常为 0，改用买卖一档参考价（竞价匹配价）。"""
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
    # 09:15–09:30：全推/快照里最新价未写，但买卖一已是集合竞价参考价
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
    """从 subscribe_whole_quote 快照取今开盘字段。"""
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
    # 仅 9:25 后允许用参考价充开盘；9:15–9:24 虚拟价不得当作今开
    if _in_opening_call_auction_window() and not _in_call_auction_indicative_window():
        px = extract_tick_price(row)
        if px > 0:
            return px
    return 0.0


def extract_tick_last_close(row: Dict[str, Any]) -> float:
    """昨收 / 前收（QMT lastClose）。"""
    for key in ("lastClose", "preClose", "pre_close", "last_close", "prevClose"):
        raw = row.get(key)
        if raw is None:
            continue
        try:
            val = float(raw)
            if val > 0:
                return val
        except (TypeError, ValueError):
            continue
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
    """更新现价；tick_row 存在时同步今开/当日高低。

    每次成功调用都会刷新 quote_recv_at / quotes_recv_at。

    规则（按用户结论）：
    - 9:15–9:24 集合竞价虚拟阶段：不写今开/最高/最低（脏数据源）
    - 9:25 后：直接跟随 tick 的 open/high/low，可上可下，不锁死
    - 偶发脏值随下一笔正确 tick 纠正即可
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
            "quote_recv_at": "",
            "last_close": 0.0,
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
    recv_at = _now_iso()
    if bucket.get("quote_recv_at") != recv_at:
        bucket["quote_recv_at"] = recv_at
        changed = True
    if results.get("quotes_recv_at") != recv_at:
        results["quotes_recv_at"] = recv_at
        changed = True

    if str(bucket.get("hl_source") or "") != HL_SOURCE_TICK_FOLLOW:
        bucket["hl_source"] = HL_SOURCE_TICK_FOLLOW
        changed = True

    row = tick_row if isinstance(tick_row, dict) else {}
    last_close = extract_tick_last_close(row) if row else 0.0
    if last_close > 0 and abs(float(bucket.get("last_close") or 0) - last_close) > 1e-9:
        bucket["last_close"] = float(last_close)
        changed = True

    # 集合竞价虚拟阶段：只更新现价/昨收，绝不采 open/high/low
    if _in_call_auction_indicative_window():
        if changed:
            results["updated_at"] = _now_iso()
        return changed

    tick_open = extract_tick_open(row) if row else 0.0
    tick_hi, tick_lo = extract_tick_high_low(row) if row else (0.0, 0.0)

    if tick_open > 0 and abs(float(bucket.get("today_open") or 0) - tick_open) > 1e-9:
        bucket["today_open"] = float(tick_open)
        changed = True

    if tick_hi > 0 and abs(float(bucket.get("today_high") or 0) - tick_hi) > 1e-9:
        bucket["today_high"] = float(tick_hi)
        changed = True

    if tick_lo > 0 and abs(float(bucket.get("today_low") or 0) - tick_lo) > 1e-9:
        bucket["today_low"] = float(tick_lo)
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
            "quote_recv_at": str(
                bucket.get("quote_recv_at") or data.get("quotes_recv_at") or ""
            ),
            "quotes_recv_at": str(data.get("quotes_recv_at") or ""),
            "last_close": float(bucket.get("last_close") or 0),
            "today_open": float(bucket.get("today_open") or 0),
            "today_high": float(bucket.get("today_high") or 0),
            "today_low": float(bucket.get("today_low") or 0),
            "updated_at": str(data.get("updated_at") or ""),
        }
    return out


def load_account_positions_snapshot(path: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """从 results.json 读取内置策略写入的资金/持仓（供外部主程序 UI）。"""
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
        vol = _as_int(meta.get("volume"), 0)
        # 余额 0 也保留（当日已清仓行），仓位条会画成空灰框
        name = str(meta.get("stock_name") or "").strip()
        if (not name or name in ("未知名称", "未知")) and get_name is not None:
            try:
                name = str(get_name(norm.split(".")[0]) or "").strip()
            except Exception:
                name = name or ""
            if name in ("未知名称", "未知"):
                name = ""
        # 可用=0 表示今日买入尚未解锁（T+1），必须保留 0，否则仓位条会全绿
        raw_can = meta.get("can_use_volume")
        if raw_can is None or raw_can == "":
            can_use = vol
        else:
            can_use = _as_int(raw_can, 0)
        if vol > 0:
            can_use = max(0, min(can_use, vol))
        else:
            can_use = 0
        positions[norm] = {
            "account_id": str(meta.get("account_id") or account.get("account_id") or ""),
            "stock_code": norm,
            "stock_name": name,
            "volume": vol,
            "can_use_volume": can_use,
            "open_price": float(meta.get("open_price") or meta.get("cost_price") or 0),
            "market_value": float(meta.get("market_value") or 0),
        }
    return asset, positions


def load_orders_snapshot(path: str) -> list:
    """从 results.json 读取内置 passorder 订单（供主程序订单列表/图表状态）。"""
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
    """从 results.json 读取弹性买卖跟踪状态（triggered / 极值价）。"""
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
    """从 results.json 读取大 QMT get_trade_detail_data(ORDER) 快照。"""
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

