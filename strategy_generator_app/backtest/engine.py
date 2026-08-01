"""
回测主循环：按交易日 T 的「早盘」价格（今开+昨收+日线关键点）生成信号，同一日 T 用 tick 模拟成交，按 T 日收盘价盯市。
适用于 9:25-9:30 运行策略、当日执行任务的模式（如 925buy）。

支持 run_backtest_segmented：同一交易日、同一股票池与资金账户内，按顺序执行多段策略（不同代码/参数、不同时段），
用于「早盘 A、随后 B」等组合回测。
"""

from datetime import date, timedelta
from typing import List, Dict, Any, Callable, Optional, Set

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


def _release_day_ticks(day_ticks_cache: Dict[str, Any], trade_date: date) -> None:
    """当日撮合结束后释放本地与全局 tick 内存（磁盘 pkl 保留）。"""
    try:
        day_ticks_cache.clear()
    except Exception:
        pass
    # 必须清 data_provider / tick_cache_loader 加载的那份模块缓存
    # （与 import utils.tick_data_cache 可能不是同一模块实例）
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
    try:
        import gc

        gc.collect()
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
    """9:25+ 与实盘一致：现价/今开高低仅来自 tick；缺 tick 不用日线今开盘填。"""
    from .data_provider import get_prices_at_time, get_today_high_low_at_time

    parts = (generation_time or "").strip().split(":")
    try:
        gh = int(parts[0]) if parts else 0
        gm = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return
    if not ((gh > 9) or (gh == 9 and gm >= 25)):
        return

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


def run_backtest_segmented(
    segments: List[Dict[str, Any]],
    stock_codes_6: List[str],
    start_date: date,
    end_date: date,
    initial_cash: float = 1_000_000.0,
    get_stock_name: Optional[Callable[[str], str]] = None,
    use_engine_form: bool = False,
    use_tick_level: bool = True,
    initial_positions: Optional[Dict[str, Dict[str, Any]]] = None,
    carry_over_pending_intents: bool = False,
    progress: BacktestProgressFn = None,
) -> Dict[str, Any]:
    """
    每个交易日 T：
    1. 拉一次早盘行情；
    2. 按 segments 顺序：按该段「策略生成时间」刷新盘中价（若可获取）→ 跑策略 → 在该段「运行起止」内用 tick 成交；
    3. 各段共享 cash / positions；
    4. 日末按收盘价盯市记入权益曲线。

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
        load_ticks_for_codes,
    )
    from .simulator import simulate_fills, simulate_fills_with_ticks, next_day_open_prices
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

    # 末日出清 N：仅对含 scheduled_clear 的策略段，与回测区间交易日数对齐
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
    # 用于回测说明展示：记录每个交易日、每个时段生成的 intents（等价于生成任务的原材料）
    generated_intents_log: List[Dict[str, Any]] = []
    if initial_positions:
        for code_6, pos in initial_positions.items():
            code_6 = (code_6 or "").strip()
            if len(code_6) < 6:
                code_6 = code_6.zfill(6)
            vol = int(pos.get("volume") or 0)
            cost = float(pos.get("cost") or 0)
            if vol > 0:
                positions[code_6] = {"volume": vol, "cost": cost}
    failure_reasons: List[str] = []
    tick_coverage_log: List[Dict[str, Any]] = []

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
    while current <= effective_end_box[0]:
        if not _is_real_trading_day(current):
            current += timedelta(days=1)
            continue

        _emit_backtest_progress(
            progress, _trade_days_planned, current, "生成意图+tick撮合"
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
        if use_tick_level:
            _preload_day_ticks(day_ticks_cache, codes_for_prices, fill_day, load_ticks_for_codes)
            _append_tick_coverage(tick_coverage_log, fill_day, "股票池", codes_for_prices, day_ticks_cache)
        # carry_over_pending_intents（tick 级）按“带执行状态”语义实现：
        # - False：时段2不继承任何时段1 intents（相当于删除时段1任务，重新使用时段2任务）
        # - True：时段2继承时段1在 tick 窗口内“仍未成交”的 intents；已成交的意图不会再重复成交
        pending_intents: List[Dict[str, Any]] = []

        # 两段策略的特殊处理：若时段2生成时间落在时段1运行窗口内，则在该时间点“插入生成时段2意图”
        # 以更贴近真实运行（时段1运行中到点生成下一段策略，但下一段仍按其 run_start/run_end 撮合）。
        if use_tick_level and (not use_engine_form) and isinstance(segments, list) and len(segments) == 2:
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
                params1["positions"] = {code_6: int(pos.get("volume") or 0) for code_6, pos in positions.items()}
                params1["backtest_trade_day_index"] = int(bt_trade_day_index)
                params1["backtest_trade_date"] = fill_day.strftime("%Y-%m-%d")
                _inject_limit_up_defer_params(params1, lu_deferred_codes)
                intents1: List[Dict[str, Any]] = []
                code1 = (seg1.get("strategy_code") or "").strip()
                if code1:
                    try:
                        intents1 = run_user_strategy(code1, codes_union, prices, get_name, account, params1, strategy_name=seg1_name)
                    except Exception:
                        intents1 = []
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
                    params2["positions"] = {code_6: int(pos.get("volume") or 0) for code_6, pos in positions.items()}
                    params2["backtest_trade_day_index"] = int(bt_trade_day_index)
                    params2["backtest_trade_date"] = fill_day.strftime("%Y-%m-%d")
                    _inject_limit_up_defer_params(params2, lu_deferred_codes)
                    intents2: List[Dict[str, Any]] = []
                    code2 = (seg2.get("strategy_code") or "").strip()
                    if code2:
                        try:
                            intents2 = run_user_strategy(code2, codes_union, prices, get_name, account2, params2, strategy_name=seg2_name)
                        except Exception:
                            intents2 = []
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
                _release_day_ticks(day_ticks_cache, fill_day)
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
            params_for_run["positions"] = {code_6: int(pos.get("volume") or 0) for code_6, pos in positions.items()}
            params_for_run["backtest_trade_day_index"] = int(bt_trade_day_index)
            params_for_run["backtest_trade_date"] = fill_day.strftime("%Y-%m-%d")
            if not strategy_uses_scheduled_clear(strategy_code, params_for_run, seg_name):
                strip_scheduled_clear_params(params_for_run)
            _inject_limit_up_defer_params(params_for_run, lu_deferred_codes)

            intents: List[Dict[str, Any]] = []
            if use_engine_form:
                try:
                    intents = engine_run_strategy(codes_union, get_name, strategy_params, price_map=prices)
                except Exception:
                    intents = []
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
                    except Exception:
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
                if use_tick_level:
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
                    next_prices = next_day_open_prices(codes_union, fill_day, get_historical_prices_for_date)
                    day_trades, cash, positions = simulate_fills(effective_intents, next_prices, cash, positions)
                    for t in day_trades:
                        t["date"] = fill_day.strftime("%Y-%m-%d")
                        t["time"] = t.get("time", "")
                    all_trades.extend(day_trades)
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
        _release_day_ticks(day_ticks_cache, fill_day)
        current += timedelta(days=1)

    try:
        from .data_provider import clear_tick_memory_cache

        clear_tick_memory_cache(None)
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
        "use_tick_level": use_tick_level,
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
    initial_cash: float = 1_000_000.0,
    get_stock_name: Optional[Callable[[str], str]] = None,
    use_engine_form: bool = False,
    use_tick_level: bool = True,
    strategy_generation_time: str = "09:25",
    strategy_run_start_time: str = "09:30",
    strategy_run_end_time: str = "15:00",
    initial_positions: Optional[Dict[str, Dict[str, Any]]] = None,
    progress: BacktestProgressFn = None,
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
        initial_positions=initial_positions,
        progress=progress,
    )
    # 与历史 UI 期望的单段文案一致
    if out.get("equity_curve"):
        fd = out["equity_curve"][0].get("date", "")
        if fd:
            out["strategy_generation_time"] = f"{fd} {strategy_generation_time}"
            out["strategy_run_start"] = f"{fd} {strategy_run_start_time}"
            out["strategy_run_end"] = f"{fd} {strategy_run_end_time}"
    return out
