"""
回测主循环：按交易日 T 的「早盘」价格（今开+昨收+日线关键点）生成信号，同一日 T 用 tick 模拟成交，按 T 日收盘价盯市。
适用于 9:25-9:30 运行策略、当日执行任务的模式（如 925buy）。

支持 run_backtest_segmented：同一交易日、同一股票池与资金账户内，按顺序执行多段策略（不同代码/参数、不同时段），
用于「早盘 A、随后 B」等组合回测。
"""

from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Callable, Optional, Set, Tuple

BacktestProgressFn = Optional[Callable[[str, Optional[int]], None]]


def _emit_backtest_progress(
    progress: BacktestProgressFn,
    trade_days: List[date],
    day: date,
    phase: str,
) -> None:
    if not progress:
        return
    total = len(trade_days)
    if total <= 0:
        progress(f"{day.isoformat()} {phase}", None)
        return
    try:
        idx = trade_days.index(day) + 1
    except ValueError:
        idx = 0
    pct = int(100 * idx / total) if idx else 0
    progress(f"{day.isoformat()} 第 {idx}/{total} 个交易日（{phase}）", pct)


try:
    from strategy_generator_app.repo_path import ensure_repo_root_on_sys_path
except ImportError:
    try:
        from repo_path import ensure_repo_root_on_sys_path
    except ImportError:
        import os
        import sys

        def ensure_repo_root_on_sys_path() -> str:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if root in sys.path:
                try:
                    sys.path.remove(root)
                except ValueError:
                    pass
            sys.path.insert(0, root)
            return root

ensure_repo_root_on_sys_path()


def _code6_for_name(code: str) -> str:
    code_6 = str(code or "").strip().replace(".SH", "").replace(".SZ", "")
    if len(code_6) < 6:
        code_6 = code_6.zfill(6) if code_6 else ""
    return code_6


def _enrich_trades_stock_names(
    trades: List[Dict[str, Any]],
    get_name: Optional[Callable[[str], str]],
) -> None:
    """为成交记录补全 stock_name（优先保留意图里已有的名称）。"""
    if not trades or not callable(get_name):
        return
    for t in trades:
        if (t.get("stock_name") or "").strip():
            continue
        code_6 = _code6_for_name(t.get("code") or "")
        if not code_6:
            continue
        try:
            name = (get_name(code_6) or "").strip()
        except Exception:
            name = ""
        t["stock_name"] = name or "未知名称"


def _is_real_trading_day(d: date) -> bool:
    """
    用真实交易日历判断是否交易日（含法定节假日）；失败时再退化为工作日判断。
    """
    try:
        from utils.trading_day import is_tradeday  # type: ignore
    except Exception:
        is_tradeday = None  # type: ignore
    if is_tradeday is not None:
        try:
            return bool(is_tradeday(d))
        except Exception:
            pass
    return d.weekday() < 5


def _segment_limit_up_clear_defer_settings(segments: List[Dict[str, Any]]) -> tuple[int, bool, int]:
    """从分段里读取「第 N 日涨停即清仓」与「涨停日顺延盯市」开关。"""
    target_day = 3
    defer_next = False
    defer_days = 1
    for seg in segments or []:
        sp = seg.get("strategy_params") or {}
        if "limit_up_clear_on_sell_day" in sp:
            try:
                target_day = int(sp.get("limit_up_clear_on_sell_day") or 3)
            except Exception:
                target_day = 3
        v = sp.get("limit_up_clear_defer_next_day")
        if v is True or v == 1 or (isinstance(v, str) and v.strip().lower() in ("1", "true", "yes", "on")):
            defer_next = True
        if "limit_up_clear_defer_days" in sp:
            try:
                defer_days = int(sp.get("limit_up_clear_defer_days") or 1)
            except Exception:
                defer_days = 1
    return max(1, target_day), defer_next, max(1, defer_days)


def _advance_trading_days(
    start_day: date,
    n_days: int,
    next_trading_day_after_fn: Optional[Callable[[date], Optional[date]]],
) -> Optional[date]:
    if next_trading_day_after_fn is None:
        return None
    cur = start_day
    for _ in range(max(0, int(n_days))):
        nxt = next_trading_day_after_fn(cur)
        if nxt is None:
            return None
        cur = nxt
    return cur


def _eod_close_at_limit_up(row: Optional[Dict[str, Any]]) -> bool:
    """以当日 EOD 收盘价与涨停价比较，判断是否收盘封在涨停附近（日线口径）。"""
    if not row or not isinstance(row, dict):
        return False
    try:
        close = float(row.get("current") or row.get("最新价") or 0)
        lu = float(row.get("涨停板") or 0)
        if close <= 0 or lu <= 0:
            return False
        return close + 1e-6 >= lu
    except (TypeError, ValueError):
        return False


def _union_codes_6(pool: List[str], held_keys: List[str]) -> List[str]:
    """股票池与当前持仓代码并集，6 位、去重，顺序：先池内再仅持仓有的代码（便于卖出策略覆盖隔夜仓）。"""
    out: List[str] = []
    seen: Set[str] = set()
    for c in pool or []:
        s = (str(c) or "").strip().replace(".", "")
        if len(s) < 6:
            s = s.zfill(6) if s else ""
        else:
            s = s[:6]
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    for c in held_keys or []:
        s = (str(c) or "").strip().replace(".", "")
        if len(s) < 6:
            s = s.zfill(6) if s else ""
        else:
            s = s[:6]
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _norm_code6_simple(c: str) -> str:
    s = (str(c) or "").strip().replace(".", "")
    if len(s) < 6:
        s = s.zfill(6) if s else ""
    else:
        s = s[:6]
    return s


def _tick_entry_valid(df: Any) -> bool:
    """tick 缓存条目是否为非空有效 DataFrame。"""
    if df is None:
        return False
    try:
        from .tick_cache_loader import tick_data_cache_module

        coerce_tick_dataframe = getattr(tick_data_cache_module(), "coerce_tick_dataframe", None)
        coerced = coerce_tick_dataframe(df) if callable(coerce_tick_dataframe) else None
        return coerced is not None and len(coerced) > 0
    except Exception:
        try:
            return len(df) > 0  # type: ignore[arg-type]
        except Exception:
            return False


def _tick_coverage_split(requested: List[str], cache: Dict[str, Any]) -> tuple[List[str], List[str]]:
    """返回 (有 tick 的代码, 缺 tick 的代码)。"""
    req = sorted({c for c in (_norm_code6_simple(x) for x in (requested or [])) if c})
    with_tick: List[str] = []
    missing: List[str] = []
    for c6 in req:
        if _tick_entry_valid((cache or {}).get(c6)):
            with_tick.append(c6)
        else:
            missing.append(c6)
    return with_tick, missing


def _append_tick_coverage(
    log: List[Dict[str, Any]],
    trade_date: date,
    scope: str,
    requested: List[str],
    cache: Dict[str, Any],
) -> None:
    with_tick, missing = _tick_coverage_split(requested, cache)
    log.append(
        {
            "date": trade_date.strftime("%Y-%m-%d"),
            "scope": scope,
            "requested_count": len(with_tick) + len(missing),
            "with_tick_count": len(with_tick),
            "missing_count": len(missing),
            "missing_codes": missing,
        }
    )


def _append_tick_coverage_disk(
    log: List[Dict[str, Any]],
    trade_date: date,
    scope: str,
    requested: List[str],
) -> None:
    """按本地 tick 文件是否存在统计覆盖，不读入 parquet（大批量选股日可用）。"""
    uniq = sorted({c for c in (_norm_code6_simple(x) for x in (requested or [])) if c})
    ready_fn = None
    try:
        from .tick_cache_loader import tick_data_cache_module

        ready_fn = getattr(tick_data_cache_module(), "tick_cache_file_ready", None)
    except Exception:
        ready_fn = None
    with_tick: List[str] = []
    missing: List[str] = []
    for c6 in uniq:
        ok = False
        if callable(ready_fn):
            try:
                ok = bool(ready_fn(c6, trade_date))
            except Exception:
                ok = False
        (with_tick if ok else missing).append(c6)
    log.append(
        {
            "date": trade_date.strftime("%Y-%m-%d"),
            "scope": scope,
            "requested_count": len(uniq),
            "with_tick_count": len(with_tick),
            "missing_count": len(missing),
            "missing_codes": missing,
        }
    )


def _finalize_tick_coverage(log: List[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总 tick 覆盖（批量回测备注/导出用）。"""
    pool_missing_union: Set[str] = set()
    intent_missing_union: Set[str] = set()
    by_date: Dict[str, Dict[str, Any]] = {}
    for e in log or []:
        d = str(e.get("date") or "")
        scope = str(e.get("scope") or "")
        missing = list(e.get("missing_codes") or [])
        if scope == "股票池":
            pool_missing_union.update(missing)
        elif scope == "意图":
            intent_missing_union.update(missing)
        if d:
            slot = by_date.setdefault(d, {})
            slot[scope] = dict(e)
    last_pool = next((e for e in reversed(log or []) if e.get("scope") == "股票池"), None)
    last_intent = next((e for e in reversed(log or []) if e.get("scope") == "意图"), None)
    return {
        "log": list(log or []),
        "by_date": by_date,
        "pool_missing_union": sorted(pool_missing_union),
        "intent_missing_union": sorted(intent_missing_union),
        "last_pool": last_pool,
        "last_intent": last_intent,
    }


def _preload_day_ticks(
    cache: Dict[str, Any],
    codes: List[str],
    trade_date: date,
    load_fn: Callable[..., Dict[str, Any]],
) -> None:
    """按需补全当日 tick 缓存，避免同一交易日重复 read_pickle / QMT。"""
    missing: List[str] = []
    for c in codes or []:
        c6 = _norm_code6_simple(c)
        if c6 and c6 not in cache:
            missing.append(c6)
    if not missing:
        return
    got = load_fn(missing, trade_date) or {}
    if isinstance(got, dict):
        cache.update(got)


def _release_day_ticks(
    day_ticks_cache: Dict[str, Any],
    trade_date: date,
    *,
    keep_global_lru: bool = True,
) -> None:
    """当日撮合结束后释放「本段」day cache；全局 tick 默认留给 LRU 跨日复用。"""
    try:
        day_ticks_cache.clear()
    except Exception:
        pass
    if keep_global_lru:
        try:
            from .data_provider import trim_tick_memory_cache

            trim_tick_memory_cache()
        except Exception:
            pass
        return
    # 兼容旧行为：按日清全局缓存
    try:
        from .data_provider import clear_tick_memory_cache

        clear_tick_memory_cache(trade_date)
    except Exception:
        try:
            from .tick_cache_loader import tick_data_cache_module

            fn = getattr(tick_data_cache_module(), "clear_tick_memory_cache", None)
            if callable(fn):
                fn(trade_date)
        except Exception:
            pass


def _clear_all_tick_memory() -> None:
    try:
        from .data_provider import clear_tick_memory_cache

        clear_tick_memory_cache(None)
    except Exception:
        try:
            from utils.tick_data_cache import clear_tick_memory_cache as _clr

            _clr(None)
        except Exception:
            pass


def _ticks_subset(cache: Dict[str, Any], codes: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for c in codes or []:
        c6 = _norm_code6_simple(c)
        if c6 and c6 in cache:
            out[c6] = cache[c6]
    return out


def _ticks_for_simulation_or_fail(
    codes: List[str],
    cache: Dict[str, Any],
    *,
    fill_day: date,
    seg_name: str,
    failure_reasons: List[str],
    tick_coverage_log: List[Dict[str, Any]],
    scope: str = "意图",
) -> Optional[Dict[str, Any]]:
    """意图撮合前加载 tick：有 tick 的照常撮合；仅当全部缺 tick 时返回 None（不降级开盘价）。"""
    uniq = sorted({c for c in (_norm_code6_simple(x) for x in (codes or [])) if c})
    if not uniq:
        return {}
    _append_tick_coverage(tick_coverage_log, fill_day, scope, uniq, cache)
    _with_tick, missing = _tick_coverage_split(uniq, cache)
    if missing:
        msg = f"[{fill_day}] [{seg_name}] 意图缺 tick（{len(missing)} 只）：{','.join(missing)}"
        if _with_tick:
            failure_reasons.append(f"{msg}，已跳过缺 tick 意图")
        else:
            failure_reasons.append(f"{msg}，无法回测")
            return None
    return _ticks_subset(cache, _with_tick if _with_tick else uniq)


def _apply_generation_time_prices_from_ticks(
    prices: Dict[str, Dict[str, Any]],
    codes_union: List[str],
    trade_date: date,
    generation_time: str,
    ticks_by_stock: Dict[str, Any],
) -> None:
    """生成时刻晚于 09:25 时，用 tick 刷新现价/今高低；缺 tick 不用日线填。

    恰好 09:25：早盘日线已把 current/最新价设为今开盘（竞价结束口径），
    不再整池灌入全日 tick——大批量选股日否则会卡死在读数百份 parquet。
    """
    from .data_provider import get_prices_at_time, get_today_high_low_at_time

    parts = (generation_time or "").strip().split(":")
    try:
        gh = int(parts[0]) if parts else 0
        gm = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return
    if gh < 9 or (gh == 9 and gm < 25):
        return
    # 09:25 整分：保留早盘今开盘，跳过整池 tick 刷新
    if gh == 9 and gm == 25:
        return

    # >09:25：按需补 tick（调用方可能未做整池预加载）
    need = [
        c
        for c in (_norm_code6_simple(x) for x in (codes_union or []))
        if c and c not in ticks_by_stock
    ]
    if need:
        try:
            from .data_provider import load_ticks_for_codes as _load_ticks
        except Exception:
            _load_ticks = None
        if callable(_load_ticks):
            try:
                got = _load_ticks(need, trade_date) or {}
                if isinstance(got, dict):
                    ticks_by_stock.update(got)
            except Exception:
                pass

    at_time_prices = get_prices_at_time(
        codes_union, trade_date, generation_time, ticks_by_stock=ticks_by_stock
    )
    for code_6, p in at_time_prices.items():
        if code_6 in prices and p and float(p) > 0:
            prices[code_6]["current"] = float(p)
            prices[code_6]["最新价"] = float(p)

    hl = get_today_high_low_at_time(
        codes_union, trade_date, generation_time, ticks_by_stock=ticks_by_stock
    )
    for code_6, v in hl.items():
        if code_6 in prices:
            prices[code_6]["今日最高"] = v.get("今日最高", 0)
            prices[code_6]["今日最低"] = v.get("今日最低", 0)

    for code_6 in codes_union:
        if code_6 not in prices:
            continue
        if code_6 not in at_time_prices:
            prices[code_6]["current"] = 0.0
            prices[code_6]["最新价"] = 0.0


def _inject_limit_up_defer_params(base: Dict[str, Any], deferred: Set[str]) -> None:
    if deferred:
        base["limit_up_clear_deferred_codes"] = sorted(deferred)


def _norm_code6(code: Any) -> str:
    s = str(code or "").strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    if s.isdigit():
        s = s.zfill(6)
    return s[:6] if len(s) >= 6 else s


def _parse_entry_date_val(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


_SELL_RULE_TYPES_BT = frozenset(
    {
        "single_sell",
        "breakthrough_sell",
        "cage_sell",
        "best_sell",
        "scheduled_clear",
    }
)


def _filter_sell_intents_before_first_buy_t1(
    intents: List[Dict[str, Any]],
    first_buy_date_by_code: Dict[str, date],
    as_of: date,
) -> List[Dict[str, Any]]:
    """卖出意图：仅在「首次买入日的下一交易日」及之后保留（买入当日及更早不卖）。"""
    if not intents or not first_buy_date_by_code:
        return intents
    out: List[Dict[str, Any]] = []
    for it in intents:
        rt = (it.get("rule_type") or "").strip()
        if rt not in _SELL_RULE_TYPES_BT and not rt.endswith("_sell"):
            out.append(it)
            continue
        c6 = _norm_code6(it.get("stock_code") or it.get("code"))
        bd = first_buy_date_by_code.get(c6) if c6 else None
        if bd is not None and as_of <= bd:
            continue
        out.append(it)
    return out


def _inject_code_sell_day_index_bt(
    params: Dict[str, Any],
    first_buy_date_by_code: Dict[str, date],
    as_of: date,
    get_trading_dates_in_range_sorted_fn: Callable[[date, date], List[date]],
) -> None:
    """回测：各股自首次买入日起的持有交易日序号（含 as_of），写入 code_sell_day_index。"""
    if not params.get("scheduled_clear_on_sell_day") and not params.get(
        "sell_hold_trading_days"
    ) and not params.get("entry_window_trading_days"):
        return
    out: Dict[str, int] = {}
    for code_6, bd in (first_buy_date_by_code or {}).items():
        if not code_6 or bd is None:
            continue
        if as_of < bd:
            out[code_6] = 0
            continue
        lst = get_trading_dates_in_range_sorted_fn(bd, as_of)
        out[code_6] = len(lst) if lst else 0
    params["code_sell_day_index"] = out


def _truthy_flag_bt(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def _filter_hold_day_force_intents_for_sim_day(
    intents: List[Dict[str, Any]],
    params: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    回测当日撮合：第 N 日无条件清仓仅在 code_sell_day_index==N 时保留。
    每日破均线类 scheduled_clear_every_day 不受影响。
    未标注的旧 scheduled_clear 仍按第 N 日过滤（兼容原「持有天数」语义）。
    实盘「生成任务」不做此过滤，以便一次写入后按 effective_date 到第 N 日再生效。
    """
    if not intents:
        return intents
    hold_n = (
        params.get("scheduled_clear_on_sell_day")
        or params.get("sell_hold_trading_days")
        or params.get("entry_window_trading_days")
    )
    try:
        hold_n_i = int(hold_n) if hold_n is not None else None
    except (TypeError, ValueError):
        hold_n_i = None
    day_map = params.get("code_sell_day_index") or {}
    if hold_n_i is None or not isinstance(day_map, dict):
        return intents
    out: List[Dict[str, Any]] = []
    for it in intents:
        if (it.get("rule_type") or "").strip() != "scheduled_clear":
            out.append(it)
            continue
        if _truthy_flag_bt(it.get("scheduled_clear_every_day")):
            out.append(it)
            continue
        c6 = _norm_code6(it.get("stock_code") or it.get("code"))
        try:
            di = int(day_map.get(c6)) if c6 in day_map else None
        except (TypeError, ValueError):
            di = None
        if di is None or di == hold_n_i:
            out.append(it)
    return out


def _update_first_buy_dates_from_trades(
    first_buy_date_by_code: Dict[str, date],
    day_trades: List[Dict[str, Any]],
    as_of: date,
    positions: Dict[str, Dict[str, Any]],
) -> None:
    """按成交更新首次买入日；仓位清零后移除，便于再次买入重新起算。"""
    for t in day_trades or []:
        side = str(t.get("side") or "").lower()
        code_6 = _norm_code6(t.get("code") or t.get("stock_code"))
        if not code_6:
            continue
        if side == "buy" and code_6 not in first_buy_date_by_code:
            first_buy_date_by_code[code_6] = as_of
    for code_6 in list(first_buy_date_by_code.keys()):
        vol = int((positions.get(code_6) or {}).get("volume") or 0)
        if vol <= 0:
            first_buy_date_by_code.pop(code_6, None)


def _index_scheduled_buy_fills(
    fills: Optional[List[Dict[str, Any]]],
) -> Dict[date, List[Dict[str, Any]]]:
    """接续卖出：按日索引上轮买入成交，供仿真日内注入（不改现金）。"""
    by_date: Dict[date, List[Dict[str, Any]]] = {}
    for f in fills or []:
        if not isinstance(f, dict):
            continue
        side = str(f.get("side") or "buy").strip().lower()
        if side and side not in ("buy", "买入", "b"):
            continue
        # 窗前建仓已进 initial_positions：只补流水、不再加仓
        if bool(f.get("blotter_only")):
            continue
        d = _parse_entry_date_val(f.get("date") or f.get("trade_date"))
        code_6 = _norm_code6(f.get("code") or f.get("stock_code"))
        try:
            vol = int(f.get("volume") or 0)
        except (TypeError, ValueError):
            vol = 0
        try:
            px = float(f.get("price") or 0)
        except (TypeError, ValueError):
            px = 0.0
        if d is None or not code_6 or vol <= 0 or px <= 0:
            continue
        try:
            amount = float(f.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        if amount <= 0:
            amount = round(px * vol, 2)
        try:
            commission = float(f.get("commission") or 0)
        except (TypeError, ValueError):
            commission = 0.0
        by_date.setdefault(d, []).append(
            {
                "code": code_6,
                "volume": vol,
                "price": px,
                "amount": amount,
                "commission": commission,
                "time": str(f.get("time") or "").strip() or "09:30:00",
                "rule_name": str(f.get("rule_name") or "").strip(),
                "leg_key": str(f.get("leg_key") or "").strip(),
            }
        )
    return by_date


def _blotter_trades_from_opening_buy_fills(
    fills: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """窗前买入已在 initial_positions：按成交顺序补买流水，交易后持仓为累计建仓量。"""
    rows: List[Tuple[date, str, Dict[str, Any]]] = []
    for f in fills or []:
        if not isinstance(f, dict) or not bool(f.get("blotter_only")):
            continue
        d = _parse_entry_date_val(f.get("date") or f.get("trade_date"))
        code_6 = _norm_code6(f.get("code") or f.get("stock_code"))
        try:
            vol = int(f.get("volume") or 0)
        except (TypeError, ValueError):
            vol = 0
        try:
            px = float(f.get("price") or 0)
        except (TypeError, ValueError):
            px = 0.0
        if d is None or not code_6 or vol <= 0 or px <= 0:
            continue
        tm = str(f.get("time") or "").strip() or "09:30:00"
        rows.append((d, tm, f))
    rows.sort(key=lambda x: (x[0], x[1], str(x[2].get("code") or "")))
    running: Dict[str, int] = {}
    out: List[Dict[str, Any]] = []
    for d, tm, f in rows:
        code_6 = _norm_code6(f.get("code") or f.get("stock_code"))
        vol = int(f.get("volume") or 0)
        px = float(f.get("price") or 0)
        try:
            amount = float(f.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        if amount <= 0:
            amount = round(px * vol, 2)
        try:
            commission = float(f.get("commission") or 0)
        except (TypeError, ValueError):
            commission = 0.0
        running[code_6] = int(running.get(code_6) or 0) + vol
        row: Dict[str, Any] = {
            "date": d.strftime("%Y-%m-%d"),
            "time": tm,
            "code": code_6,
            "side": "buy",
            "price": round(px, 2),
            "volume": vol,
            "amount": round(amount, 2),
            "commission": round(commission, 2),
            "position_after": int(running[code_6]),
            "rule_name": str(f.get("rule_name") or "").strip() or "买入注入",
            "reason": "chain_buy_fill_opening",
            "trigger_info": "接续卖出：窗前买入建仓流水",
            "slippage": 0.0,
        }
        lk = str(f.get("leg_key") or "").strip()
        if lk:
            row["leg_key"] = lk
        out.append(row)
    return out


def _apply_scheduled_buy_injections(
    positions: Dict[str, Dict[str, Any]],
    fills_by_date: Dict[date, List[Dict[str, Any]]],
    as_of: date,
    first_buy_date_by_code: Dict[str, date],
    first_buy_hints: Optional[Dict[str, date]] = None,
) -> List[Dict[str, Any]]:
    """
    将 as_of 及更早、尚未注入的买入成交写入持仓（T+1：只加 volume，不加 available）。
    同时返回带真实「交易后持仓」的买入流水（供接续卖出 CSV / 表格）。
    """
    try:
        from .simulator import apply_buy_fill_t1
    except ImportError:
        from strategy_generator_app.backtest.simulator import apply_buy_fill_t1  # type: ignore

    pending = sorted(d for d in list(fills_by_date.keys()) if d <= as_of)
    hints = first_buy_hints or {}
    emitted: List[Dict[str, Any]] = []
    for d in pending:
        rows = fills_by_date.pop(d, None) or []
        rows = sorted(
            rows,
            key=lambda r: (
                str(r.get("time") or ""),
                str(r.get("code") or ""),
            ),
        )
        for row in rows:
            code_6 = str(row.get("code") or "")
            vol = int(row.get("volume") or 0)
            px = float(row.get("price") or 0)
            apply_buy_fill_t1(positions, code_6, vol, px)
            if code_6 not in first_buy_date_by_code:
                hint = hints.get(code_6)
                first_buy_date_by_code[code_6] = hint if hint is not None else d
            pos_after = int((positions.get(code_6) or {}).get("volume") or 0)
            amount = float(row.get("amount") or 0) or round(px * vol, 2)
            tr: Dict[str, Any] = {
                "date": d.strftime("%Y-%m-%d"),
                "time": str(row.get("time") or "").strip() or "09:30:00",
                "code": code_6,
                "side": "buy",
                "price": round(px, 2),
                "volume": vol,
                "amount": round(amount, 2),
                "commission": round(float(row.get("commission") or 0), 2),
                "position_after": pos_after,
                "rule_name": str(row.get("rule_name") or "").strip() or "买入注入",
                "reason": "chain_buy_fill",
                "trigger_info": "接续卖出：按上轮买入成交注入",
                "slippage": 0.0,
            }
            lk = str(row.get("leg_key") or "").strip()
            if lk:
                tr["leg_key"] = lk
            emitted.append(tr)
    return emitted


def _post_eod_limit_up_defer(
    *,
    enabled: bool,
    target_day: int,
    defer_days: int,
    bt_trade_day_index: int,
    fill_day: date,
    original_end: date,
    effective_end_box: List[date],
    extension_once_box: List[bool],
    deferred: Set[str],
    positions: Dict[str, Dict[str, Any]],
    prices_eod: Dict[str, Dict[str, Any]],
    next_trading_day_after_fn: Optional[Callable[[date], Optional[date]]],
) -> None:
    if not enabled:
        return
    if next_trading_day_after_fn is None:
        if bt_trade_day_index >= target_day + max(1, defer_days):
            deferred.clear()
        return
    if bt_trade_day_index == target_day:
        for code_6, pos in positions.items():
            if int(pos.get("volume") or 0) <= 0:
                continue
            row = prices_eod.get(code_6) or {}
            if _eod_close_at_limit_up(row):
                deferred.add(code_6)
        if deferred and not extension_once_box[0]:
            nt = _advance_trading_days(fill_day, max(1, defer_days), next_trading_day_after_fn)
            if nt is not None and nt > original_end:
                effective_end_box[0] = max(effective_end_box[0], nt)
                extension_once_box[0] = True
    if bt_trade_day_index >= target_day + max(1, defer_days):
        deferred.clear()


def _record_filled_legs_from_trades(
    seg: Dict[str, Any],
    day_trades: List[Dict[str, Any]],
) -> None:
    """把当日成交里的 leg_key 写入 strategy_params._filled_legs（跨日去重）。

    买入、卖出都记：卖出策略（如 LU10/OPEN50）依赖此集合整段只触发一次；
    若只记买入，半仓腿会每日按「剩余可卖」重挂，变成对剩余取半。
    """
    if not day_trades:
        return
    sp = dict(seg.get("strategy_params") or {})
    filled = set(str(x) for x in (sp.get("_filled_legs") or []) if x)
    before = len(filled)
    for t in day_trades:
        side = str(t.get("side") or "").lower()
        if side not in ("buy", "sell"):
            continue
        lk = t.get("leg_key")
        if lk:
            filled.add(str(lk))
    if len(filled) == before:
        return
    sp["_filled_legs"] = sorted(filled)
    seg["strategy_params"] = sp


def run_backtest_segmented(
    segments: List[Dict[str, Any]],
    stock_codes_6: List[str],
    start_date: date,
    end_date: date,
    initial_cash: float = 100_000_000.0,
    get_stock_name: Optional[Callable[[str], str]] = None,
    use_engine_form: bool = False,
    use_tick_level: bool = True,
    fill_mode: str = "tick",
    initial_positions: Optional[Dict[str, Dict[str, Any]]] = None,
    carry_over_pending_intents: bool = False,
    progress: BacktestProgressFn = None,
    clear_ticks_on_finish: bool = True,
    scheduled_buy_fills: Optional[List[Dict[str, Any]]] = None,
    first_buy_date_hints: Optional[Dict[str, date]] = None,
) -> Dict[str, Any]:
    """
    每个交易日 T：
    1. 拉一次早盘行情；
    2. 按 segments 顺序：按该段「策略生成时间」刷新盘中价（若可获取）→ 跑策略 → 在该段「运行起止」内用 tick 成交；
    3. 各段共享 cash / positions；
    4. 日末按收盘价盯市记入权益曲线。

    scheduled_buy_fills：接续卖出时按买入成交明细按日注入仓位（只加量、不改现金；T+1 不可当日卖）。
    first_buy_date_hints：注入时若尚无首次买入日，优先用上轮导出的首次买入日。

    segments 每项建议包含：
      - strategy_code: str
      - strategy_params: dict（可选）
      - strategy_generation_time: str  "HH:mm"
      - strategy_run_start_time: str
      - strategy_run_end_time: str
      - name: str（可选，用于日志）
    """
    from .data_provider import (
        get_historical_prices_for_date,
        get_historical_prices_for_morning,
        get_daily_ohlc_for_codes,
        load_ticks_for_codes,
    )
    from .simulator import (
        simulate_fills,
        simulate_fills_with_ticks,
        simulate_fills_same_day_ohlc,
        next_day_open_prices,
    )
    import sys
    import os
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if app_root not in sys.path:
        sys.path.insert(0, app_root)
    try:
        if use_engine_form:
            from strategy_generator_app.engine import run_strategy as engine_run_strategy
        else:
            from strategy_generator_app.strategy_runner import run_user_strategy
    except ImportError:
        if use_engine_form:
            from engine import run_strategy as engine_run_strategy
        else:
            from strategy_runner import run_user_strategy

    if not segments:
        raise ValueError("run_backtest_segmented: segments 不能为空")

    _fm = (fill_mode or "tick").strip().lower()
    if _fm in ("same_day_ohlc", "daily_ohlc", "ohlc", "daily"):
        _fm = "same_day_ohlc"
    elif _fm in ("next_day_open", "next_day"):
        _fm = "next_day_open"
    else:
        _fm = "tick"
    # 兼容旧开关：fill_mode=tick 时仍看 use_tick_level；same_day_ohlc 强制走日线撮合
    use_same_day_ohlc = _fm == "same_day_ohlc"
    use_tick_level_eff = (not use_same_day_ohlc) and bool(use_tick_level) and _fm == "tick"
    use_next_day_open = (not use_same_day_ohlc) and (not use_tick_level_eff)

    # 末日出清 N：若策略参数已指定成交后持有天数则保留；否则用区间交易日数对齐
    try:
        from strategy_generator_app.trading_calendar import get_trading_dates_in_range_sorted
    except ImportError:
        from trading_calendar import get_trading_dates_in_range_sorted
    try:
        from strategy_generator_app.config.strategy_config import (
            strategy_uses_scheduled_clear,
            strip_unwanted_scheduled_clear_intents,
            strip_scheduled_clear_params,
        )
    except ImportError:
        from config.strategy_config import (
            strategy_uses_scheduled_clear,
            strip_unwanted_scheduled_clear_intents,
            strip_scheduled_clear_params,
        )
    _td_in_window = get_trading_dates_in_range_sorted(start_date, end_date)
    _trade_days_planned: List[date] = list(_td_in_window)
    _hold_in_window = len(_trade_days_planned) if _trade_days_planned else 0
    # 每轮回测重置已买腿（跨日状态仅在本轮内累积）
    for _seg in segments:
        _sp = dict(_seg.get("strategy_params") or {})
        if "_filled_legs" in _sp or "selection_date_by_code" in _sp:
            _sp["_filled_legs"] = []
            _seg["strategy_params"] = _sp
    if progress:
        progress(
            f"共 {len(_trade_days_planned)} 个交易日（{start_date}～{end_date}）",
            0,
        )
    if _hold_in_window >= 1:
        for _seg in segments:
            _code = _seg.get("strategy_code") or ""
            _seg_name = _seg.get("name") or ""
            _sp = dict(_seg.get("strategy_params") or {})
            if strategy_uses_scheduled_clear(_code, _sp, _seg_name):
                _has_hold = (
                    _sp.get("scheduled_clear_on_sell_day") is not None
                    or _sp.get("sell_hold_trading_days") is not None
                )
                if not _has_hold:
                    _sp["scheduled_clear_on_sell_day"] = _hold_in_window
                    _sp["sell_hold_trading_days"] = _hold_in_window
                _seg["strategy_params"] = _sp
            else:
                strip_scheduled_clear_params(_sp)
                _seg["strategy_params"] = _sp
    else:
        for _seg in segments:
            _sp = dict(_seg.get("strategy_params") or {})
            if not strategy_uses_scheduled_clear(
                _seg.get("strategy_code") or "", _sp, _seg.get("name") or ""
            ):
                strip_scheduled_clear_params(_sp)
                _seg["strategy_params"] = _sp

    get_name = get_stock_name or (lambda c: "")
    equity_curve: List[Dict[str, Any]] = []
    all_trades: List[Dict[str, Any]] = []
    cash = initial_cash
    positions: Dict[str, Dict[str, Any]] = {}
    # 首次买入日：优先用初始持仓 entry_date（接续卖出）；否则视为区间首日建仓
    first_buy_date_by_code: Dict[str, date] = {}
    try:
        from .simulator import (
            init_position_t1,
            settle_positions_t1,
            positions_for_strategy_params,
            positions_volume_for_strategy_params,
            init_positions_baseline_from_positions,
            note_buy_fills_on_baseline,
            prune_baseline_if_flat,
            positions_baseline_for_strategy_params,
        )
    except ImportError:
        from strategy_generator_app.backtest.simulator import (  # type: ignore
            init_position_t1,
            settle_positions_t1,
            positions_for_strategy_params,
            positions_volume_for_strategy_params,
            init_positions_baseline_from_positions,
            note_buy_fills_on_baseline,
            prune_baseline_if_flat,
            positions_baseline_for_strategy_params,
        )
    if initial_positions:
        for code_6, pos in initial_positions.items():
            code_6 = _norm_code6(code_6)
            vol = int((pos or {}).get("volume") or 0)
            cost = float((pos or {}).get("cost") or 0)
            if vol > 0:
                ed = (
                    _parse_entry_date_val((pos or {}).get("entry_date"))
                    or _parse_entry_date_val((pos or {}).get("first_buy_date"))
                    or _parse_entry_date_val((pos or {}).get("buy_date"))
                )
                first_buy_date_by_code[code_6] = ed if ed is not None else start_date
                # 建仓日落在回测首日及之后：首日尚不可卖（T+1）
                if ed is not None and ed >= start_date:
                    positions[code_6] = init_position_t1(vol, cost, available=0)
                else:
                    positions[code_6] = init_position_t1(vol, cost, available=vol)
    # 总仓位基准：本轮累计买入；起点用初始持仓（买段结束时尚无卖出）
    positions_baseline: Dict[str, int] = init_positions_baseline_from_positions(positions)
    fills_by_date = _index_scheduled_buy_fills(scheduled_buy_fills)
    buy_date_hints: Dict[str, date] = {}
    for k, v in (first_buy_date_hints or {}).items():
        c6 = _norm_code6(k)
        if c6 and isinstance(v, date):
            buy_date_hints[c6] = v
    # 用于回测说明展示：记录每个交易日、每个时段生成的 intents（等价于生成任务的原材料）
    generated_intents_log: List[Dict[str, Any]] = []
    failure_reasons: List[str] = []
    tick_coverage_log: List[Dict[str, Any]] = []
    # 窗前建仓流水（不重复加仓）：先写入成交明细，交易后持仓=真实累计
    all_trades.extend(_blotter_trades_from_opening_buy_fills(scheduled_buy_fills))

    try:
        from strategy_generator_app.trading_calendar import next_trading_day_after as _next_td_after
    except ImportError:
        try:
            from trading_calendar import next_trading_day_after as _next_td_after
        except ImportError:
            _next_td_after = None  # type: ignore

    lu_target_day, lu_defer_enabled, lu_defer_days = _segment_limit_up_clear_defer_settings(segments)
    lu_deferred_codes: Set[str] = set()
    effective_end_box: List[date] = [end_date]
    lu_extension_once_box: List[bool] = [False]

    current = start_date
    days_with_data = 0
    # 回测中第几个实际交易执行日（仅在成功进入当日撮合流程时+1）
    bt_trade_day_index = 0
    days_with_zero_prices = 0
    last_eod_prices: Dict[str, Dict[str, Any]] = {}
    prev_trade_day: Optional[date] = None
    while current <= effective_end_box[0]:
        if not _is_real_trading_day(current):
            current += timedelta(days=1)
            continue

        # T+1：跨交易日开盘，将昨日买入转为可卖
        if prev_trade_day is not None:
            settle_positions_t1(positions)
        prev_trade_day = current
        # 接续卖出：按上轮买入成交注入当日（及积压）仓位；策略只见 available
        # 同时写入买入流水，position_after 为扣减已卖后的真实持仓
        if fills_by_date:
            inj_trades = _apply_scheduled_buy_injections(
                positions,
                fills_by_date,
                current,
                first_buy_date_by_code,
                buy_date_hints,
            )
            if inj_trades:
                note_buy_fills_on_baseline(positions_baseline, inj_trades)
                all_trades.extend(inj_trades)

        _emit_backtest_progress(
            progress,
            _trade_days_planned,
            current,
            "生成意图+日线撮合" if use_same_day_ohlc else "生成意图+tick撮合",
        )

        codes_for_prices = _union_codes_6(stock_codes_6, list(positions.keys()))
        prices = get_historical_prices_for_morning(codes_for_prices, current, get_name)
        if not prices:
            from .messages import morning_prices_empty_message

            failure_reasons.append(morning_prices_empty_message(current))
            current += timedelta(days=1)
            continue
        if isinstance(prices, dict) and "_error" in prices:
            failure_reasons.append(f"[{current}] {prices['_error']}")
            current += timedelta(days=1)
            continue

        # 须包含持仓代码：若仅股票池全为 0 但仍有持仓标的有效，仍应跑分段（否则次日永远无法卖出）
        any_valid = bool(codes_for_prices) and any(
            float(prices.get(c, {}).get("current") or prices.get(c, {}).get("最新价") or 0) > 0
            for c in codes_for_prices
        )
        if not any_valid:
            days_with_zero_prices += 1
            from .messages import morning_prices_zero_message

            failure_reasons.append(morning_prices_zero_message(current))
            current += timedelta(days=1)
            continue
        days_with_data += 1
        bt_trade_day_index += 1

        fill_day = current
        day_had_any_intent = False
        day_ticks_cache: Dict[str, Any] = {}
        if use_tick_level_eff:
            # 股票池覆盖只查本地文件是否存在，不预读整池 parquet。
            # 真正读 tick 延后到：生成时刻>09:25 的价刷新，或意图撮合（通常仅数只）。
            _append_tick_coverage_disk(
                tick_coverage_log, fill_day, "股票池", codes_for_prices
            )
        # carry_over_pending_intents（tick 级）按“带执行状态”语义实现：
        # - False：时段2不继承任何时段1 intents（相当于删除时段1任务，重新使用时段2任务）
        # - True：时段2继承时段1在 tick 窗口内“仍未成交”的 intents；已成交的意图不会再重复成交
        pending_intents: List[Dict[str, Any]] = []

        # 两段策略的特殊处理：若时段2生成时间落在时段1运行窗口内，则在该时间点“插入生成时段2意图”
        # 以更贴近真实运行（时段1运行中到点生成下一段策略，但下一段仍按其 run_start/run_end 撮合）。
        if use_tick_level_eff and (not use_engine_form) and isinstance(segments, list) and len(segments) == 2:
            seg1, seg2 = segments[0] or {}, segments[1] or {}
            seg1_name = (seg1.get("name") or "").strip() or "时段1"
            seg2_name = (seg2.get("name") or "").strip() or "时段2"
            seg1_gen = seg1.get("strategy_generation_time") or "09:25"
            seg1_rs = seg1.get("strategy_run_start_time") or "09:30"
            seg1_re = seg1.get("strategy_run_end_time") or "15:00"
            seg2_gen = seg2.get("strategy_generation_time") or "09:25"
            seg2_rs = seg2.get("strategy_run_start_time") or "09:30"
            seg2_re = seg2.get("strategy_run_end_time") or "15:00"

            try:
                from datetime import time as _dt_time
                def _t(s: str) -> _dt_time:
                    parts = (s or "").strip().split(":")
                    h = int(parts[0]) if len(parts) > 0 else 0
                    m = int(parts[1]) if len(parts) > 1 else 0
                    sec = int(parts[2]) if len(parts) > 2 else 0
                    return _dt_time(h, m, sec)
                _seg1_rs_t, _seg1_re_t, _seg2_gen_t = _t(seg1_rs), _t(seg1_re), _t(seg2_gen)
                _seg2_rs_t, _seg2_re_t = _t(seg2_rs), _t(seg2_re)
            except Exception:
                _seg1_rs_t = _seg1_re_t = _seg2_gen_t = None
                _seg2_rs_t = _seg2_re_t = None

            # 仅在“时段2生成时间 ∈ 时段1运行窗口”时启用插入生成逻辑
            if _seg1_rs_t is not None and (_seg1_rs_t <= _seg2_gen_t <= _seg1_re_t):
                codes_union = _union_codes_6(stock_codes_6, list(positions.keys()))

                # 1) 先在 seg1_gen 时刷新 prices 并生成 seg1 意图
                try:
                    _apply_generation_time_prices_from_ticks(
                        prices, codes_union, current, seg1_gen, day_ticks_cache
                    )
                except Exception:
                    pass
                account = {"total_asset": 0.0, "cash": cash}
                for code_6, pos in positions.items():
                    p = prices.get(code_6, {}).get("current") or prices.get(code_6, {}).get("最新价") or 0
                    account["total_asset"] += pos["volume"] * float(p)
                account["total_asset"] += cash
                params1 = dict(seg1.get("strategy_params") or {})
                params1["positions"] = positions_for_strategy_params(positions)
                params1["positions_volume"] = positions_volume_for_strategy_params(positions)
                params1["positions_baseline"] = positions_baseline_for_strategy_params(
                    positions_baseline
                )
                params1["backtest_trade_day_index"] = int(bt_trade_day_index)
                params1["backtest_trade_date"] = fill_day.strftime("%Y-%m-%d")
                _inject_code_sell_day_index_bt(
                    params1, first_buy_date_by_code, fill_day, get_trading_dates_in_range_sorted
                )
                _inject_limit_up_defer_params(params1, lu_deferred_codes)
                intents1: List[Dict[str, Any]] = []
                code1 = (seg1.get("strategy_code") or "").strip()
                if code1:
                    try:
                        intents1 = run_user_strategy(code1, codes_union, prices, get_name, account, params1, strategy_name=seg1_name)
                    except Exception as e:
                        failure_reasons.append(
                            f"[{fill_day}] [{seg1_name}] 策略 run() 失败: {type(e).__name__}: {e}"
                        )
                        intents1 = []
                intents1 = _filter_hold_day_force_intents_for_sim_day(intents1, params1)
                intents1 = _filter_sell_intents_before_first_buy_t1(
                    intents1, first_buy_date_by_code, fill_day
                )
                if intents1:
                    generated_intents_log.append(
                        {
                            "date": fill_day.strftime("%Y-%m-%d"),
                            "segment_name": seg1_name,
                            "strategy_generation_time": seg1_gen,
                            "strategy_run_start_time": seg1_rs,
                            "strategy_run_end_time": seg1_re,
                            "intents": intents1,
                        }
                    )

                # 2) 在 seg1 窗口内分两段撮合：seg1_rs..seg2_gen 与 seg2_gen..seg1_re
                if intents1:
                    day_had_any_intent = True
                    codes_ticks1 = list({i.get("stock_code") for i in intents1 if i.get("stock_code")})
                    _preload_day_ticks(day_ticks_cache, codes_ticks1, fill_day, load_ticks_for_codes)
                    ticks_by_stock = _ticks_for_simulation_or_fail(
                        codes_ticks1,
                        day_ticks_cache,
                        fill_day=fill_day,
                        seg_name=seg1_name,
                        failure_reasons=failure_reasons,
                        tick_coverage_log=tick_coverage_log,
                    )
                    if ticks_by_stock is not None:
                        trades_a, cash, positions, rem_a = simulate_fills_with_ticks(
                            intents1, ticks_by_stock, fill_day, cash, positions,
                            run_start_time=seg1_rs, run_end_time=seg2_gen,
                        )
                        all_trades.extend(trades_a)
                        note_buy_fills_on_baseline(positions_baseline, trades_a)
                        prune_baseline_if_flat(positions_baseline, positions)
                        _update_first_buy_dates_from_trades(
                            first_buy_date_by_code, trades_a, fill_day, positions
                        )
                    else:
                        rem_a = []
                    # 到达 seg2_gen：刷新 prices 并生成 seg2 意图（使用此刻最新价 + 当前持仓/现金）
                    try:
                        _apply_generation_time_prices_from_ticks(
                            prices, codes_union, current, seg2_gen, day_ticks_cache
                        )
                    except Exception:
                        pass
                    account2 = {"total_asset": 0.0, "cash": cash}
                    for code_6, pos in positions.items():
                        p = prices.get(code_6, {}).get("current") or prices.get(code_6, {}).get("最新价") or 0
                        account2["total_asset"] += pos["volume"] * float(p)
                    account2["total_asset"] += cash
                    params2 = dict(seg2.get("strategy_params") or {})
                    params2["positions"] = positions_for_strategy_params(positions)
                    params2["positions_volume"] = positions_volume_for_strategy_params(
                        positions
                    )
                    params2["positions_baseline"] = positions_baseline_for_strategy_params(
                        positions_baseline
                    )
                    params2["backtest_trade_day_index"] = int(bt_trade_day_index)
                    params2["backtest_trade_date"] = fill_day.strftime("%Y-%m-%d")
                    _inject_code_sell_day_index_bt(
                        params2, first_buy_date_by_code, fill_day, get_trading_dates_in_range_sorted
                    )
                    _inject_limit_up_defer_params(params2, lu_deferred_codes)
                    intents2: List[Dict[str, Any]] = []
                    code2 = (seg2.get("strategy_code") or "").strip()
                    if code2:
                        try:
                            intents2 = run_user_strategy(code2, codes_union, prices, get_name, account2, params2, strategy_name=seg2_name)
                        except Exception as e:
                            failure_reasons.append(
                                f"[{fill_day}] [{seg2_name}] 策略 run() 失败: {type(e).__name__}: {e}"
                            )
                            intents2 = []
                    intents2 = _filter_hold_day_force_intents_for_sim_day(intents2, params2)
                    intents2 = _filter_sell_intents_before_first_buy_t1(
                        intents2, first_buy_date_by_code, fill_day
                    )
                    if intents2:
                        generated_intents_log.append(
                            {
                                "date": fill_day.strftime("%Y-%m-%d"),
                                "segment_name": seg2_name,
                                "strategy_generation_time": seg2_gen,
                                "strategy_run_start_time": seg2_rs,
                                "strategy_run_end_time": seg2_re,
                                "intents": intents2,
                            }
                        )

                    # seg1 后半段继续撮合（用未成交 rem_a）
                    if rem_a:
                        codes_ticks2 = list({i.get("stock_code") for i in rem_a if i.get("stock_code")})
                        _preload_day_ticks(day_ticks_cache, codes_ticks2, fill_day, load_ticks_for_codes)
                        ticks_by_stock2 = _ticks_for_simulation_or_fail(
                            codes_ticks2,
                            day_ticks_cache,
                            fill_day=fill_day,
                            seg_name=seg1_name,
                            failure_reasons=failure_reasons,
                            tick_coverage_log=tick_coverage_log,
                        )
                        if ticks_by_stock2 is not None:
                            trades_b, cash, positions, rem_b = simulate_fills_with_ticks(
                                rem_a, ticks_by_stock2, fill_day, cash, positions,
                                run_start_time=seg2_gen, run_end_time=seg1_re,
                            )
                            all_trades.extend(trades_b)
                            note_buy_fills_on_baseline(positions_baseline, trades_b)
                            prune_baseline_if_flat(positions_baseline, positions)
                            _update_first_buy_dates_from_trades(
                                first_buy_date_by_code, trades_b, fill_day, positions
                            )
                        else:
                            rem_b = []
                    else:
                        rem_b = []

                    # 3) 最后在 seg2 自己窗口撮合（可选继承 seg1 未成交）
                    effective2 = intents2
                    if carry_over_pending_intents and rem_b:
                        effective2 = rem_b + (intents2 or [])
                    if effective2:
                        day_had_any_intent = True
                        codes_ticks3 = list({i.get("stock_code") for i in effective2 if i.get("stock_code")})
                        _preload_day_ticks(day_ticks_cache, codes_ticks3, fill_day, load_ticks_for_codes)
                        ticks_by_stock3 = _ticks_for_simulation_or_fail(
                            codes_ticks3,
                            day_ticks_cache,
                            fill_day=fill_day,
                            seg_name=seg2_name,
                            failure_reasons=failure_reasons,
                            tick_coverage_log=tick_coverage_log,
                        )
                        if ticks_by_stock3 is not None:
                            trades_c, cash, positions, remaining2 = simulate_fills_with_ticks(
                                effective2, ticks_by_stock3, fill_day, cash, positions,
                                run_start_time=seg2_rs, run_end_time=seg2_re,
                            )
                            all_trades.extend(trades_c)
                            note_buy_fills_on_baseline(positions_baseline, trades_c)
                            prune_baseline_if_flat(positions_baseline, positions)
                            _update_first_buy_dates_from_trades(
                                first_buy_date_by_code, trades_c, fill_day, positions
                            )
                            pending_intents = remaining2 if carry_over_pending_intents else []
                        else:
                            pending_intents = []

                # 本日已按插入生成逻辑处理完两段，跳过旧的逐段串行逻辑
                #（仍会走日末盯市与权益曲线）
                current += timedelta(days=0)  # no-op，便于 diff 可读
                # 进入日末处理
                codes_eod = _union_codes_6(stock_codes_6, list(positions.keys()))
                prices_eod = get_historical_prices_for_date(codes_eod, current, get_name)
                if isinstance(prices_eod, dict) and "_error" in prices_eod:
                    failure_reasons.append(f"[{current}] 获取当日收盘价失败：{prices_eod['_error']}")
                    prices_eod = {}
                position_value = 0.0
                for code_6, pos in positions.items():
                    p = (prices_eod.get(code_6) or {}).get("current") or (prices_eod.get(code_6) or {}).get("最新价") or 0
                    position_value += pos["volume"] * float(p)
                equity_curve.append({
                    "date": current.strftime("%Y-%m-%d"),
                    "cash": cash,
                    "position_value": round(position_value, 2),
                    "total": round(cash + position_value, 2),
                })
                last_eod_prices = prices_eod
                _post_eod_limit_up_defer(
                    enabled=lu_defer_enabled,
                    target_day=lu_target_day,
                    defer_days=lu_defer_days,
                    bt_trade_day_index=bt_trade_day_index,
                    fill_day=fill_day,
                    original_end=end_date,
                    effective_end_box=effective_end_box,
                    extension_once_box=lu_extension_once_box,
                    deferred=lu_deferred_codes,
                    positions=positions,
                    prices_eod=prices_eod,
                    next_trading_day_after_fn=_next_td_after,
                )
                _release_day_ticks(day_ticks_cache, fill_day, keep_global_lru=True)
                current += timedelta(days=1)
                continue

        for seg_idx, seg in enumerate(segments):
            strategy_code = (seg.get("strategy_code") or "").strip()
            strategy_params = seg.get("strategy_params") or {}
            strategy_generation_time = seg.get("strategy_generation_time") or "09:25"
            strategy_run_start_time = seg.get("strategy_run_start_time") or "09:30"
            strategy_run_end_time = seg.get("strategy_run_end_time") or "15:00"
            seg_name = (seg.get("name") or "").strip() or f"时段{seg_idx + 1}"

            codes_union = _union_codes_6(stock_codes_6, list(positions.keys()))

            # 按该段生成时间刷新盘中价（与单日单策略逻辑一致，并支持后续时段）
            try:
                _apply_generation_time_prices_from_ticks(
                    prices,
                    codes_union,
                    current,
                    strategy_generation_time,
                    day_ticks_cache,
                )
            except Exception:
                pass

            account = {"total_asset": 0.0, "cash": cash}
            for code_6, pos in positions.items():
                p = prices.get(code_6, {}).get("current") or prices.get(code_6, {}).get("最新价") or 0
                account["total_asset"] += pos["volume"] * float(p)
            account["total_asset"] += cash

            params_for_run = dict(strategy_params)
            params_for_run["positions"] = positions_for_strategy_params(positions)
            params_for_run["positions_volume"] = positions_volume_for_strategy_params(
                positions
            )
            params_for_run["positions_baseline"] = positions_baseline_for_strategy_params(
                positions_baseline
            )
            params_for_run["backtest_trade_day_index"] = int(bt_trade_day_index)
            params_for_run["backtest_trade_date"] = fill_day.strftime("%Y-%m-%d")
            if not strategy_uses_scheduled_clear(strategy_code, params_for_run, seg_name):
                strip_scheduled_clear_params(params_for_run)
            _inject_code_sell_day_index_bt(
                params_for_run, first_buy_date_by_code, fill_day, get_trading_dates_in_range_sorted
            )
            _inject_limit_up_defer_params(params_for_run, lu_deferred_codes)
            # clip 强度字段写入 prices（策略也可从 params.clip_strength_by_code 读取）
            _str_map = params_for_run.get("clip_strength_by_code") or {}
            if isinstance(_str_map, dict) and _str_map and isinstance(prices, dict):
                for _c, _meta in _str_map.items():
                    if not isinstance(_meta, dict):
                        continue
                    _c6 = str(_c or "").strip()
                    if _c6.isdigit():
                        _c6 = _c6.zfill(6)
                    _p = prices.get(_c6)
                    if not isinstance(_p, dict):
                        continue
                    if _meta.get("合格榜内序位") not in (None, ""):
                        _p["合格榜内序位"] = _meta.get("合格榜内序位")
                    if _meta.get("合格榜标签内RS排名") not in (None, ""):
                        _p["合格榜标签内RS排名"] = _meta.get("合格榜标签内RS排名")

            intents: List[Dict[str, Any]] = []
            if use_engine_form:
                try:
                    intents = engine_run_strategy(codes_union, get_name, strategy_params, price_map=prices)
                except Exception as e:
                    failure_reasons.append(
                        f"[{fill_day}] [{seg_name}] 引擎策略执行失败: {type(e).__name__}: {e}"
                    )
                    intents = []
                intents = _filter_hold_day_force_intents_for_sim_day(
                    intents, params_for_run
                )
                intents = _filter_sell_intents_before_first_buy_t1(
                    intents, first_buy_date_by_code, fill_day
                )
            else:
                if strategy_code:
                    try:
                        intents = run_user_strategy(
                            strategy_code,
                            codes_union,
                            prices,
                            get_name,
                            account,
                            params_for_run,
                            strategy_name=seg_name,
                        )
                        intents = strip_unwanted_scheduled_clear_intents(
                            intents,
                            strategy_code,
                            strategy_params,
                            seg_name,
                        )
                        intents = _filter_hold_day_force_intents_for_sim_day(
                            intents, params_for_run
                        )
                        intents = _filter_sell_intents_before_first_buy_t1(
                            intents, first_buy_date_by_code, fill_day
                        )
                    except Exception as e:
                        # 勿静默吞掉：否则卖策略 import 失败会表现为「整天 0 卖出」且难排查
                        failure_reasons.append(
                            f"[{fill_day}] [{seg_name}] 策略 run() 失败: {type(e).__name__}: {e}"
                        )
                        intents = []

            # tick 级回测下：时段2是否继承上一段未成交 intents
            effective_intents: List[Dict[str, Any]] = intents
            if carry_over_pending_intents and seg_idx > 0 and pending_intents:
                effective_intents = pending_intents + intents

            # 记录该段生成并用于撮合的 intents（等价于该段生成任务）
            if effective_intents:
                generated_intents_log.append(
                    {
                        "date": fill_day.strftime("%Y-%m-%d"),
                        "segment_name": seg_name,
                        "strategy_generation_time": strategy_generation_time,
                        "strategy_run_start_time": strategy_run_start_time,
                        "strategy_run_end_time": strategy_run_end_time,
                        "intents": effective_intents,
                    }
                )

            if effective_intents:
                day_had_any_intent = True

            if effective_intents:
                if use_same_day_ohlc:
                    codes_for_ohlc = list({
                        (c or "").strip().zfill(6) if len((c or "").strip()) < 6 else (c or "").strip()[:6]
                        for c in [i.get("stock_code") for i in effective_intents]
                        if c
                    })
                    ohlc_map = get_daily_ohlc_for_codes(codes_for_ohlc, fill_day)
                    day_trades, cash, positions, remaining_intents = simulate_fills_same_day_ohlc(
                        effective_intents, ohlc_map, fill_day, cash, positions,
                    )
                    all_trades.extend(day_trades)
                    note_buy_fills_on_baseline(positions_baseline, day_trades)
                    prune_baseline_if_flat(positions_baseline, positions)
                    _record_filled_legs_from_trades(seg, day_trades)
                    _update_first_buy_dates_from_trades(
                        first_buy_date_by_code, day_trades, fill_day, positions
                    )
                    pending_intents = remaining_intents if carry_over_pending_intents else []
                    if len(day_trades) == 0 and codes_for_ohlc and not ohlc_map:
                        failure_reasons.append(
                            f"[{fill_day}] [{seg_name}] 日线撮合：意图标的均无 OHLC"
                        )
                elif use_tick_level_eff:
                    codes_for_ticks = list({
                        (c or "").strip().zfill(6) if len((c or "").strip()) < 6 else (c or "").strip()[:6]
                        for c in [i.get("stock_code") for i in effective_intents]
                    })
                    _preload_day_ticks(day_ticks_cache, codes_for_ticks, fill_day, load_ticks_for_codes)
                    ticks_by_stock = _ticks_for_simulation_or_fail(
                        codes_for_ticks,
                        day_ticks_cache,
                        fill_day=fill_day,
                        seg_name=seg_name,
                        failure_reasons=failure_reasons,
                        tick_coverage_log=tick_coverage_log,
                    )
                    if ticks_by_stock is not None:
                        _before_n = len(all_trades)
                        day_trades, cash, positions, remaining_intents = simulate_fills_with_ticks(
                            effective_intents, ticks_by_stock, fill_day, cash, positions,
                            run_start_time=strategy_run_start_time,
                            run_end_time=strategy_run_end_time,
                        )
                        all_trades.extend(day_trades)
                        note_buy_fills_on_baseline(positions_baseline, day_trades)
                        prune_baseline_if_flat(positions_baseline, positions)
                        _record_filled_legs_from_trades(seg, day_trades)
                        _update_first_buy_dates_from_trades(
                            first_buy_date_by_code, day_trades, fill_day, positions
                        )
                        pending_intents = remaining_intents if carry_over_pending_intents else []
                        if len(day_trades) == 0:
                            # 关键诊断：分段窗口内未成交，记录意图概况，便于定位“窗口/条件/无tick”等问题
                            rts = sorted(set(((i.get("rule_type") or "").strip() for i in (effective_intents or []))) )
                            failure_reasons.append(
                                f"[{fill_day}] [{seg_name}] 窗口 {strategy_run_start_time}–{strategy_run_end_time} 未产生成交；"
                                f"意图数={len(effective_intents)} 类型={rts} 读取tick标的数={len(ticks_by_stock)} "
                                f"remaining={len(remaining_intents or [])} trades_total+={len(all_trades) - _before_n}"
                            )
                            # best_buy/best_sell 细诊断：说明是“未触发/未回落到位/连续命中不足”
                            try:
                                details = []
                                for inv in (remaining_intents or [])[:8]:
                                    rt = (inv.get("rule_type") or "").strip()
                                    if rt not in ("best_sell", "best_buy"):
                                        continue
                                    dbg = inv.get("_sim_debug") or {}
                                    code = inv.get("stock_code") or ""
                                    if rt == "best_sell":
                                        details.append(
                                            f"  - {code} best_sell trigger={inv.get('trigger_price')} drop={inv.get('drop_percent')}% "
                                            f"triggered={dbg.get('triggered')} max_seen={dbg.get('max_seen')} highest={dbg.get('highest')} "
                                            f"last_fallback={dbg.get('last_fallback')} below_fallback={dbg.get('times_below_fallback')} "
                                            f"max_consecutive_hits={dbg.get('max_consecutive_hits')}"
                                            f" min_seen={dbg.get('min_seen')} min_after_trigger={dbg.get('min_after_trigger')} "
                                            f"min_after_highest={dbg.get('min_after_highest')} fixed_fallback={dbg.get('last_fixed_fallback')}"
                                            f" | base={inv.get('debug_base_high')} pre={inv.get('debug_pre_close')} latest={inv.get('debug_latest')} mult={inv.get('debug_mult')}"
                                        )
                                    else:
                                        details.append(
                                            f"  - {code} best_buy trigger={inv.get('trigger_price')} rise={inv.get('rise_percent')}% "
                                            f"triggered={dbg.get('triggered')} min_seen={dbg.get('min_seen')} lowest={dbg.get('lowest')} "
                                            f"max_seen={dbg.get('max_seen')}"
                                        )
                                if details:
                                    failure_reasons.append("[诊断] 弹性规则未成交细节：\n" + "\n".join(details))
                            except Exception:
                                pass
                    else:
                        pending_intents = []
                else:
                    # 旧日频：次日开盘近似（非 same_day_ohlc）
                    next_prices = next_day_open_prices(codes_union, fill_day, get_historical_prices_for_date)
                    day_trades, cash, positions = simulate_fills(effective_intents, next_prices, cash, positions)
                    for t in day_trades:
                        t["date"] = fill_day.strftime("%Y-%m-%d")
                        t["time"] = t.get("time", "")
                    all_trades.extend(day_trades)
                    note_buy_fills_on_baseline(positions_baseline, day_trades)
                    prune_baseline_if_flat(positions_baseline, positions)
                    _record_filled_legs_from_trades(seg, day_trades)
                    _update_first_buy_dates_from_trades(
                        first_buy_date_by_code, day_trades, fill_day, positions
                    )
                    pending_intents = []
            # 某段无意图时不逐段刷屏；见下方整日汇总

        if not day_had_any_intent:
            if len(segments) == 1:
                failure_reasons.append(f"[{current}] 策略在当日未产生任何交易意图")
            else:
                failure_reasons.append(
                    f"[{current}] 策略在当日未产生任何交易意图（所有分时段均无买卖信号）"
                )

        codes_eod = _union_codes_6(stock_codes_6, list(positions.keys()))
        prices_eod = get_historical_prices_for_date(codes_eod, current, get_name)
        if isinstance(prices_eod, dict) and "_error" in prices_eod:
            failure_reasons.append(f"[{current}] 获取当日收盘价失败：{prices_eod['_error']}")
            prices_eod = {}
        position_value = 0.0
        for code_6, pos in positions.items():
            p = (prices_eod.get(code_6) or {}).get("current") or (prices_eod.get(code_6) or {}).get("最新价") or 0
            position_value += pos["volume"] * float(p)
        equity_curve.append({
            "date": current.strftime("%Y-%m-%d"),
            "cash": cash,
            "position_value": round(position_value, 2),
            "total": round(cash + position_value, 2),
        })
        last_eod_prices = prices_eod
        _post_eod_limit_up_defer(
            enabled=lu_defer_enabled,
            target_day=lu_target_day,
            defer_days=lu_defer_days,
            bt_trade_day_index=bt_trade_day_index,
            fill_day=fill_day,
            original_end=end_date,
            effective_end_box=effective_end_box,
            extension_once_box=lu_extension_once_box,
            deferred=lu_deferred_codes,
            positions=positions,
            prices_eod=prices_eod,
            next_trading_day_after_fn=_next_td_after,
        )
        _release_day_ticks(day_ticks_cache, fill_day, keep_global_lru=True)
        current += timedelta(days=1)

    if clear_ticks_on_finish:
        _clear_all_tick_memory()
    else:
        try:
            from .data_provider import trim_tick_memory_cache

            trim_tick_memory_cache()
        except Exception:
            pass

    last_prices = {}
    if positions and last_eod_prices:
        for code_6 in positions:
            p = last_eod_prices.get(code_6, {}).get("current") or last_eod_prices.get(code_6, {}).get("最新价")
            if p is not None and float(p) > 0:
                last_prices[code_6] = float(p)

    buy_and_hold_total: Optional[float] = None
    if initial_positions and equity_curve:
        try:
            last_date_str = equity_curve[-1].get("date", "")
            if last_date_str:
                from datetime import datetime as _dt
                last_trade_date = _dt.strptime(last_date_str, "%Y-%m-%d").date()
                codes_bh = list(initial_positions.keys())
                prices_bh = get_historical_prices_for_date(codes_bh, last_trade_date, get_name)
                if not isinstance(prices_bh, dict) or "_error" not in prices_bh:
                    bh_val = float(initial_cash)
                    for code_6, pos in initial_positions.items():
                        p = (prices_bh.get(code_6) or {}).get("current") or (prices_bh.get(code_6) or {}).get("最新价") or 0
                        bh_val += (int(pos.get("volume") or 0)) * float(p)
                    buy_and_hold_total = round(bh_val, 2)
        except Exception:
            pass

    out_gen_parts = []
    out_run_parts = []
    if equity_curve:
        first_date = equity_curve[0].get("date", "")
        if first_date:
            for seg in segments:
                gn = seg.get("strategy_generation_time") or "09:25"
                rs = seg.get("strategy_run_start_time") or "09:30"
                re = seg.get("strategy_run_end_time") or "15:00"
                sn = (seg.get("name") or "").strip() or "段"
                out_gen_parts.append(f"{first_date} {gn}")
                out_run_parts.append(f"{sn} {rs}–{re}")

    _enrich_trades_stock_names(all_trades, get_name)

    if progress:
        progress("回测完成", 100)

    out: Dict[str, Any] = {
        "equity_curve": equity_curve,
        "trades": all_trades,
        "final_cash": cash,
        "final_positions": positions,
        "last_prices": last_prices,
        "generated_intents": generated_intents_log,
        "use_tick_level": use_tick_level_eff,
        "fill_mode": _fm,
        "days_with_data": days_with_data,
        "days_with_zero_prices": days_with_zero_prices,
        "failure_reasons": failure_reasons,
        "tick_coverage": _finalize_tick_coverage(tick_coverage_log),
        "strategy_generation_time": " | ".join(out_gen_parts) if out_gen_parts else "",
        "strategy_run_start": " | ".join(out_run_parts) if out_run_parts else "",
        "strategy_run_end": "",
        "segments": segments,
    }
    if buy_and_hold_total is not None:
        out["buy_and_hold_total"] = buy_and_hold_total
    return out


def run_backtest(
    strategy_code: str,
    strategy_params: Dict[str, Any],
    stock_codes_6: List[str],
    start_date: date,
    end_date: date,
    initial_cash: float = 100_000_000.0,
    get_stock_name: Optional[Callable[[str], str]] = None,
    use_engine_form: bool = False,
    use_tick_level: bool = True,
    fill_mode: str = "tick",
    strategy_generation_time: str = "09:25",
    strategy_run_start_time: str = "09:30",
    strategy_run_end_time: str = "15:00",
    initial_positions: Optional[Dict[str, Dict[str, Any]]] = None,
    progress: BacktestProgressFn = None,
    clear_ticks_on_finish: bool = True,
    scheduled_buy_fills: Optional[List[Dict[str, Any]]] = None,
    first_buy_date_hints: Optional[Dict[str, date]] = None,
) -> Dict[str, Any]:
    """
    单策略回测（兼容旧接口）：内部转为单元素 run_backtest_segmented。
    """
    seg = {
        "strategy_code": strategy_code or "",
        "strategy_params": strategy_params or {},
        "strategy_generation_time": strategy_generation_time,
        "strategy_run_start_time": strategy_run_start_time,
        "strategy_run_end_time": strategy_run_end_time,
        "name": "策略",
    }
    out = run_backtest_segmented(
        [seg],
        stock_codes_6,
        start_date,
        end_date,
        initial_cash=initial_cash,
        get_stock_name=get_stock_name,
        use_engine_form=use_engine_form,
        use_tick_level=use_tick_level,
        fill_mode=fill_mode,
        initial_positions=initial_positions,
        progress=progress,
        clear_ticks_on_finish=clear_ticks_on_finish,
        scheduled_buy_fills=scheduled_buy_fills,
        first_buy_date_hints=first_buy_date_hints,
    )
    # 与历史 UI 期望的单段文案一致
    if out.get("equity_curve"):
        fd = out["equity_curve"][0].get("date", "")
        if fd:
            out["strategy_generation_time"] = f"{fd} {strategy_generation_time}"
            out["strategy_run_start"] = f"{fd} {strategy_run_start_time}"
            out["strategy_run_end"] = f"{fd} {strategy_run_end_time}"
    return out
