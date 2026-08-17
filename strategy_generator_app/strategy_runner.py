"""
执行用户策略代码，生成任务意图列表。
用户代码需定义 run(codes, prices, get_name, account, params) -> list[dict]。
account 为 {"total_asset": 总资金, "cash": 可用资金}；params 为策略参数（如 buy_amount_per_stock、default_volume），客户在「策略参数」Tab 中配置。
"""

from typing import List, Dict, Any, Callable, Optional, Tuple, Set
import math
import hashlib

# 策略源码 -> 已编译的 run 可调用对象（回测按日反复 exec 的主要开销之一）
_COMPILED_RUN_CACHE: Dict[str, Any] = {}
_COMPILED_RUN_CACHE_MAX = 32


def _strategy_code_cache_key(code_str: str) -> str:
    return hashlib.sha1((code_str or "").encode("utf-8", errors="ignore")).hexdigest()


def clear_compiled_strategy_cache() -> None:
    _COMPILED_RUN_CACHE.clear()


def _limits_from_prices(p: Dict[str, Any]) -> Tuple[float, float]:
    """返回 (limit_down, limit_up)，无效时 (0, 0)。"""
    try:
        lu = float(p.get("涨停板") or p.get("limit_up") or 0)
        ld = float(p.get("跌停板") or p.get("limit_down") or 0)
    except (TypeError, ValueError):
        return 0.0, 0.0
    if lu <= 0 or ld <= 0 or lu < ld:
        return 0.0, 0.0
    return ld, lu


def _derive_limits_from_prices_row(code: str, p: Dict[str, Any]) -> Tuple[float, float]:
    """从行情行取涨跌停；缺失时按昨收与板块幅度推算（与回测/key_price 对齐）。"""
    ld, lu = _limits_from_prices(p)
    if ld > 0 and lu > 0:
        return ld, lu
    try:
        pre_close = float(p.get("昨收盘") or p.get("pre_close") or 0)
    except (TypeError, ValueError):
        pre_close = 0.0
    if pre_close <= 0:
        return 0.0, 0.0
    try:
        from strategy_generator_app.backtest.data_provider import (
            _limit_up_down_ratios,
            _price_precision_for_code,
            _stock_price_round,
        )
    except ImportError:
        from backtest.data_provider import (
            _limit_up_down_ratios,
            _price_precision_for_code,
            _stock_price_round,
        )
    code_6 = _normalize_code_6(code or p.get("stock_code") or "")
    name = str(p.get("stock_name") or p.get("name") or "")
    up_r, down_r = _limit_up_down_ratios(code_6, name)
    prec = _price_precision_for_code(code_6)
    lu = _stock_price_round(pre_close * up_r, prec)
    ld = _stock_price_round(pre_close * down_r, prec)
    if lu <= 0 or ld <= 0 or lu < ld:
        return 0.0, 0.0
    return ld, lu


def _round_px(x: float, nd: int = 2) -> float:
    return round(float(x), nd)


def _in_limit_band(p: float, ld: float, lu: float) -> bool:
    return ld <= p <= lu


def _clamp_to_limit_band(p: float, ld: float, lu: float) -> float:
    """将价格钳到 [跌停, 涨停]。"""
    return max(ld, min(lu, p))


def _sanitize_intent_prices(
    intent: Dict[str, Any], prices: Dict[str, Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """
    涨跌停板外不可成交（预览/画图也无意义）：
    - 单点类（price 或 trigger_price）：价格落在 [跌停, 涨停] 外 → 丢弃该条意图；
    - 笼子类（price_low + price_high）：仅当两端都不在 [跌停, 涨停] 内时整单丢弃；
      否则将超出的一端钳到跌停价或涨停价（max(跌停, min(涨停, 价))）；钳后若 low>high 则丢弃。
    无有效涨跌停数据（缺涨停板/跌停板）时不做处理，原样返回。
    """
    code = _normalize_code_6(intent.get("stock_code") or "")
    p = prices.get(code) or {}
    ld, lu = _derive_limits_from_prices_row(code, p)
    price_rule_types = (
        "single_buy", "breakthrough_buy", "single_sell", "breakthrough_sell",
        "scheduled_clear", "night_buy", "night_sell", "best_buy", "best_sell",
    )
    rt_peek = (intent.get("rule_type") or "").strip()
    if ld <= 0 or lu <= 0:
        if rt_peek in price_rule_types:
            return None
        return intent

    rt = (intent.get("rule_type") or "").strip()

    if rt in ("cage_buy", "cage_sell"):
        try:
            low = float(intent.get("price_low") or 0)
            high = float(intent.get("price_high") or 0)
        except (TypeError, ValueError):
            return None
        if low <= 0 or high <= 0 or low > high:
            return None
        low_in = _in_limit_band(low, ld, lu)
        high_in = _in_limit_band(high, ld, lu)
        # 仅当两端都不在涨跌停有效区间内时整单丢弃
        if not low_in and not high_in:
            return None
        cl = _clamp_to_limit_band(low, ld, lu)
        ch = _clamp_to_limit_band(high, ld, lu)
        if cl > ch:
            return None
        out = dict(intent)
        out["price_low"] = _round_px(cl)
        out["price_high"] = _round_px(ch)
        return out

    price_keys: Tuple[str, ...]
    if rt in ("single_buy", "breakthrough_buy", "single_sell", "breakthrough_sell", "scheduled_clear", "night_buy", "night_sell"):
        price_keys = ("price",)
    elif rt in ("best_buy", "best_sell"):
        price_keys = ("trigger_price",)
    else:
        return intent

    for key in price_keys:
        try:
            px = float(intent.get(key) or 0)
        except (TypeError, ValueError):
            return None
        if px <= 0:
            continue
        if px < ld or px > lu:
            # 向下突破卖出：止损线常在跌停附近，thr+0.01 舍入后可能略低于跌停价；
            # 若直接丢弃会导致整条意图消失（旧逻辑下易误判为「只有 n=0 能跑」）。
            if rt == "breakthrough_sell":
                intent = dict(intent)
                intent[key] = _round_px(_clamp_to_limit_band(px, ld, lu))
            elif rt == "single_buy" and (
                intent.get("open_buy_ask")
                or intent.get("wait_unseal")
                or intent.get("fill_at_limit_up")
                or abs(px - lu) <= 0.02
                or abs(px - ld) <= 0.02
            ):
                # 开盘买入触发价常贴涨停；四舍五入越界时钳回，避免预览/回测列表被清空
                intent = dict(intent)
                intent[key] = _round_px(_clamp_to_limit_band(px, ld, lu))
            else:
                return None
    # 价格带：钳 band_low/band_high（与 price 一并保留）
    if rt == "breakthrough_buy":
        out = dict(intent)
        changed = False
        for bk in (
            "band_low",
            "band_high",
            "band_accept_low",
            "accept_band_low",
            "price_low",
            "price_high",
        ):
            if out.get(bk) is None or str(out.get(bk)).strip() == "":
                continue
            try:
                bv = float(out.get(bk))
            except (TypeError, ValueError):
                continue
            if bv <= 0:
                continue
            cv = _clamp_to_limit_band(bv, ld, lu)
            if abs(cv - bv) > 1e-12:
                out[bk] = _round_px(cv)
                changed = True
            else:
                out[bk] = _round_px(bv)
                changed = True
        try:
            blo = float(out.get("band_low") or out.get("price_low") or 0)
            bhi = float(out.get("band_high") or out.get("price_high") or 0)
        except (TypeError, ValueError):
            blo, bhi = 0.0, 0.0
        if blo > 0 and bhi > 0 and blo > bhi:
            return None
        # accept_low 对齐字段，并保证落在监控带内（否则硬 pass 无意义）
        alo_raw = out.get("band_accept_low")
        if alo_raw is None or str(alo_raw).strip() == "":
            alo_raw = out.get("accept_band_low")
        if alo_raw is not None and str(alo_raw).strip() != "":
            try:
                alo = float(alo_raw)
            except (TypeError, ValueError):
                alo = 0.0
            if alo > 0 and blo > 0 and bhi > 0:
                alo = min(max(alo, blo), bhi)
                out["band_accept_low"] = _round_px(alo)
                changed = True
        if changed:
            intent = out
    # 为 best_* 补充跌停/涨停用于回测成交价钳制（成交价不一定等于 last_price，而是用 bid/ask 推导）
    if rt in ("best_buy", "best_sell") and ld > 0 and lu > 0:
        out = dict(intent)
        out["limit_down"] = ld
        out["limit_up"] = lu
        return out
    return intent


def _normalize_code_6(stock_code: str) -> str:
    """把 6 位代码提取出来，便于和 prices 的 key 对齐。"""
    s = (stock_code or "").strip()
    s = s.replace(".SH", "").replace(".SZ", "").replace(".BJ", "").replace(" ", "")
    if len(s) < 6:
        return s.zfill(6)
    return s[:6]


def _should_skip_cage_buy_intent(intent: Dict[str, Any], prices: Dict[str, Dict[str, Any]]) -> bool:
    """
    对笼子买入做边界校验：
    当 price_high <= 跌停价时，笼子上沿落在跌停价（或更低），该规则容易退化为“只买跌停价/无意义区间”，
    直接跳过，避免生成你说的这类“上沿=跌停”的记录。
    """
    rule_type = (intent.get("rule_type") or "").strip()
    if rule_type != "cage_buy":
        return False

    stock_code = _normalize_code_6(intent.get("stock_code") or "")
    p = prices.get(stock_code) or {}
    limit_down = p.get("跌停板") or p.get("limit_down") or 0
    price_high = intent.get("price_high")

    try:
        limit_down_f = float(limit_down or 0)
        price_high_f = float(price_high or 0)
    except (TypeError, ValueError):
        return False

    if limit_down_f <= 0 or price_high_f <= 0:
        return False

    return round(price_high_f, 2) <= round(limit_down_f, 2)


def run_user_strategy(
    code_str: str,
    codes: List[str],
    prices: Dict[str, Dict[str, Any]],
    get_name: Optional[Callable[[str], str]] = None,
    account: Optional[Dict[str, float]] = None,
    params: Optional[Dict[str, Any]] = None,
    strategy_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    执行用户策略代码，返回意图列表。
    code_str: 用户 Python 源码，需定义 run(codes, prices, get_name, account, params) 返回 list[dict]。
    params: 策略参数（如 buy_amount_per_stock 单股拟买入金额、min_order_amount 每笔最小交易金额），客户在界面配置，无需改代码。
    每个意图 dict 至少含 stock_code, rule_type，及该规则所需字段。
    """
    if not (code_str or "").strip():
        return []
    if account is None:
        account = {"total_asset": 0.0, "cash": 0.0}
    if params is None:
        params = {}
    import os
    if os.environ.get("ANT_BACKTEST_PARAMS_DEBUG", "").strip().lower() in ("1", "true", "yes", "on"):
        _has_lu = "limit_up_clear_on_sell_day" in params
        _lu_raw = params.get("limit_up_clear_on_sell_day")
        _bt_idx = params.get("backtest_trade_day_index")
        _bt_date = params.get("backtest_trade_date")
        print(
            f"[回测参数] limit_up_clear_on_sell_day in params={_has_lu} value={_lu_raw!r} | "
            f"backtest_trade_day_index={_bt_idx!r} date={_bt_date!r} | strategy_name={strategy_name!r}"
        )
    # 安全：仅暴露必要内置与类型，避免 open/file/exec 等
    # 允许使用 print，配合外层 redirect_stdout 将输出重定向到 GUI 文本框
    # 允许白名单 import（策略里常见 from datetime import date）
    _allowed_import_modules = {
        "datetime",
        "math",
        "json",
        "re",
        "collections",
        "itertools",
        "functools",
        "decimal",
        "copy",
        "time",
        "calendar",
        # 持仓卖出策略：落盘已卖腿、读 data/daily_cache 近涨停价
        "os",
        "pathlib",
        "utils",
        "strategy_generator_app",
        "task_builder",
        "daily_cache_reader",
    }

    def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        root = str(name or "").split(".", 1)[0]
        if root not in _allowed_import_modules:
            raise ImportError(f"策略代码不允许 import: {name}")
        return __import__(name, globals, locals, fromlist, level)

    safe_builtins = {
        "__import__": _safe_import,
        "round": round,
        "float": float,
        "int": int,
        "str": str,
        "len": len,
        "range": range,
        "min": min,
        "max": max,
        "abs": abs,
        "True": True,
        "False": False,
        "None": None,
        "list": list,
        "dict": dict,
        "tuple": tuple,
        "set": set,
        "zip": zip,
        "enumerate": enumerate,
        "sorted": sorted,
        "sum": sum,
        "map": map,
        "any": any,
        "all": all,
        "print": print,
        "isinstance": isinstance,
        "hasattr": hasattr,
        "getattr": getattr,
        "bool": bool,
        "open": open,
        "TypeError": TypeError,
        "ValueError": ValueError,
        "KeyError": KeyError,
        "AttributeError": AttributeError,
        "ZeroDivisionError": ZeroDivisionError,
        "ImportError": ImportError,
        "Exception": Exception,
    }
    # exec 阶段不把 codes/prices/params 放进全局命名空间：
    # 否则策略末尾若误留「测试用」顶层 run(codes,...) 会在 exec 时多跑一遍，
    # 且可能清空 params["positions"]，导致随后正式调用时全是 可用持仓=0。
    exec_namespace = {"__builtins__": safe_builtins}
    cache_key = _strategy_code_cache_key(code_str)
    run_fn = _COMPILED_RUN_CACHE.get(cache_key)
    if not callable(run_fn):
        try:
            exec(compile(code_str, "<strategy>", "exec"), exec_namespace)
        except SyntaxError as e:
            raise RuntimeError(f"策略代码语法错误: {e}") from e
        run_fn = exec_namespace.get("run")
        if not callable(run_fn):
            raise RuntimeError("策略代码中未定义可调用的 run(codes, prices, get_name, account, params)")
        _COMPILED_RUN_CACHE[cache_key] = run_fn
        while len(_COMPILED_RUN_CACHE) > _COMPILED_RUN_CACHE_MAX:
            try:
                _COMPILED_RUN_CACHE.pop(next(iter(_COMPILED_RUN_CACHE)))
            except Exception:
                break
    get_name_fn = get_name or (lambda c: "")
    # 持仓字典单独拷贝，避免 run() 内误改 params["positions"] 影响外层或其它逻辑
    pos_in = params.get("positions") or {}
    params_run = {
        **params,
        "positions": dict(pos_in) if isinstance(pos_in, dict) else {},
    }
    try:
        out = run_fn(codes, prices, get_name_fn, account, params_run)
    except TypeError:
        try:
            out = run_fn(codes, prices, get_name_fn, account)
        except TypeError:
            try:
                out = run_fn(codes, prices, get_name_fn)
            except Exception as e:
                raise RuntimeError(f"执行 run() 出错: {e}") from e
        except Exception as e:
            raise RuntimeError(f"执行 run() 出错: {e}") from e
    except Exception as e:
        raise RuntimeError(f"执行 run() 出错: {e}") from e
    if not isinstance(out, list):
        raise RuntimeError("run() 返回值必须是 list")

    result = []
    for i, item in enumerate(out):
        if not isinstance(item, dict):
            raise RuntimeError(f"run() 返回的第 {i+1} 项应为 dict")
        intent = dict(item)
        intent = _sanitize_intent_prices(intent, prices)
        if intent is None:
            continue
        # 笼子退化（上沿不高于跌停）在钳位后再判一次
        if _should_skip_cage_buy_intent(intent, prices):
            continue
        result.append(intent)

    # 回测专用：
    # 仅在第 N 个卖出交易日保留「涨停即清仓」规则。
    # - 推荐仅在目标策略参数里配置 limit_up_clear_on_sell_day（例如 3），
    #   这样不会影响其它策略；不配置则对其它策略不生效。
    try:
        bt_day_idx = int(params.get("backtest_trade_day_index") or 0)
    except Exception:
        bt_day_idx = 0
    if bt_day_idx > 0 and ("limit_up_clear_on_sell_day" in params):
        try:
            target_day = int(params.get("limit_up_clear_on_sell_day") or 3)
        except Exception:
            target_day = 3
        if target_day > 0:
            defer_next = params.get("limit_up_clear_defer_next_day")
            defer_next_b = defer_next is True or defer_next == 1 or (
                isinstance(defer_next, str) and defer_next.strip().lower() in ("1", "true", "yes", "on")
            )
            try:
                defer_days = int(params.get("limit_up_clear_defer_days") or 1)
            except Exception:
                defer_days = 1
            defer_days = max(1, defer_days)
            raw_dc = params.get("limit_up_clear_deferred_codes") or []
            defer_codes: Set[str] = set()
            if defer_next_b and isinstance(raw_dc, (list, tuple, set)):
                for x in raw_dc:
                    s = (str(x) or "").strip().replace(".", "")
                    if len(s) < 6:
                        s = s.zfill(6) if s else ""
                    else:
                        s = s[:6]
                    if len(s) == 6:
                        defer_codes.add(s)
            filtered: List[Dict[str, Any]] = []
            for intent in result:
                name = str(intent.get("name") or "").strip()
                if name in ("涨停即清仓", "涨停板卖出（全仓）"):
                    on_target = bt_day_idx == target_day
                    sc = (str(intent.get("stock_code") or "").strip().replace(".", ""))
                    if len(sc) < 6:
                        sc = sc.zfill(6) if sc else ""
                    else:
                        sc = sc[:6]
                    on_extend = (
                        defer_next_b
                        and sc in defer_codes
                        and (target_day + 1) <= bt_day_idx <= (target_day + defer_days)
                    )
                    if name == "涨停即清仓":
                        # 顺延规则：第 N 天若该股当下已在涨停价（典型一字板），当日不执行「涨停即清仓」。
                        # 该标的会在引擎日末被识别，并在顺延期内继续运行同样规则。
                        if defer_next_b and on_target:
                            p = prices.get(sc) or {}
                            try:
                                cur = float(p.get("current") or p.get("最新价") or 0)
                                lu = float(p.get("涨停板") or 0)
                            except Exception:
                                cur = 0.0
                                lu = 0.0
                            if cur > 0 and lu > 0 and cur + 1e-6 >= lu:
                                continue
                        if not (on_target or on_extend):
                            continue
                    else:
                        # 「涨停板卖出（全仓）」按“第 N 天启用”处理，不顺延到下一天。
                        if not on_target:
                            continue
                filtered.append(intent)
            result = filtered

    # 卖出统一考虑「每笔最小交易金额」：对每条卖出 intent 单独校正 volume
    # - 若 volume *（该规则的最低可能成交价）< min_order_amount，则增加到满足；
    # - 每条最多不超过当前可用持仓 cap；各条之间不按「先生成先扣剩余」串联分配——
    #   实际盘中哪条先触发不确定，串联会导致后几条变成 0/「全部」等错误展示。
    # - 定时清仓（scheduled_clear）仍单独按 cap 处理，与其它卖出规则互不扣减份额。
    try:
        result = _adjust_sell_volumes_by_min_order_amount(
            intents=result,
            prices=prices,
            params=params,
        )
    except Exception:
        # 调整失败不影响策略本身生成（保守：直接返回原结果）
        pass

    return result


def _normalize_positions(positions: Any) -> Dict[str, int]:
    out: Dict[str, int] = {}
    if not isinstance(positions, dict):
        return out
    for k, v in positions.items():
        code_6 = (str(k) or "").strip()
        code_6 = (code_6.split(".")[0] if "." in code_6 else code_6).strip()
        if not code_6:
            continue
        if len(code_6) < 6:
            code_6 = code_6.zfill(6)
        code_6 = code_6[:6]
        try:
            vol = int(v or 0)
        except Exception:
            vol = 0
        if code_6:
            out[code_6] = out.get(code_6, 0) + vol
    return out


def _estimate_sell_price_min(intent: Dict[str, Any]) -> float:
    """
    估计该卖出规则的“最低可能成交价”，用于判断 volume * price 是否达到 min_order_amount。
    说明：
    - cage_sell 在区间 [price_low, price_high] 内成交，取 price_low 为最保守下限；
    - best_sell 使用 trigger_price 与回落百分比 drop_percent，取 trigger*(1-drop_percent) 作为最保守下限；
    - 其他使用其 rule 的 price 字段。
    """
    rule_type = (intent.get("rule_type") or "").strip()
    if rule_type == "cage_sell":
        return float(intent.get("price_low") or 0)
    if rule_type == "best_sell":
        trigger = float(intent.get("trigger_price") or 0)
        if trigger <= 0:
            return 0.0
        pb = intent.get("pullback_price")
        if pb is not None and float(pb or 0) > 0:
            return float(pb)
        drop_pct = float(intent.get("drop_percent") or 0)
        # drop_percent 是百分比（0.3 表示 0.3%）
        return trigger * (1.0 - drop_pct / 100.0)

    # single_sell / breakthrough_sell / scheduled_clear 等
    return float(intent.get("price") or 0)


def _required_volume_for_min_amount(min_order_amount: float, price_min: float, lot_size: int = 100) -> int:
    if min_order_amount <= 0 or price_min <= 0:
        return 0
    # 至少 1 手（100 股），并向上取整到手数
    lots = int(math.ceil(min_order_amount / (price_min * lot_size)))
    if lots <= 0:
        return 0
    return max(lot_size, lots * lot_size)


def _adjust_sell_volumes_by_min_order_amount(
    intents: List[Dict[str, Any]],
    prices: Dict[str, Dict[str, Any]],
    params: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not params:
        return intents

    min_order_amount = float(params.get("min_order_amount") or 0)
    if min_order_amount <= 0:
        return intents

    positions = _normalize_positions(params.get("positions") or {})
    if not positions:
        return intents

    # 可安全估计最低成交价的卖出规则类型（与 backtest/simulator 覆盖保持一致）
    sell_rule_types = {"single_sell", "breakthrough_sell", "cage_sell", "best_sell", "scheduled_clear"}

    # 为每个股票代码建立卖出 intent 的出现顺序索引（不改 intents 总序）
    code_to_sell_indices: Dict[str, List[int]] = {}
    for idx, intent in enumerate(intents):
        rule_type = (intent.get("rule_type") or "").strip()
        if rule_type not in sell_rule_types:
            continue
        code_6 = (intent.get("stock_code") or "").strip()
        code_6 = (code_6.split(".")[0] if "." in code_6 else code_6).strip()
        code_6 = code_6.zfill(6) if len(code_6) < 6 and code_6 else code_6[:6] if code_6 else code_6
        if not code_6:
            continue
        if code_6 not in positions:
            # 策略可能对未持仓股票输出卖出意图，这里直接跳过（剩余为0）
            continue
        code_to_sell_indices.setdefault(code_6, []).append(idx)

    if not code_to_sell_indices:
        return intents

    # 先预计算每个 intent 的最低成交价和最低需要手数
    precomputed: Dict[Tuple[str, int], Tuple[float, int]] = {}
    for code_6, indices in code_to_sell_indices.items():
        for i in indices:
            intent = intents[i]
            price_min = _estimate_sell_price_min(intent)
            need_vol = _required_volume_for_min_amount(min_order_amount, price_min)
            precomputed[(code_6, i)] = (price_min, need_vol)

    # 非定时清仓：每条 intent 独立按「策略给出的比例/股数」与 min_order_amount 校正，上限均为 cap（不串联扣减）。
    # 定时清仓（scheduled_clear）：仍单独按 cap 与 min_order_amount 校正，与其它卖出互不抢占份额。
    for code_6, indices in code_to_sell_indices.items():
        cap = (int(positions.get(code_6, 0) or 0) // 100) * 100

        non_sched = [
            i
            for i in indices
            if (intents[i].get("rule_type") or "").strip() != "scheduled_clear"
        ]
        sched = [
            i
            for i in indices
            if (intents[i].get("rule_type") or "").strip() == "scheduled_clear"
        ]

        if cap <= 0:
            for intent_idx in indices:
                intents[intent_idx]["volume"] = 0
            continue

        # 仅非定时清仓：每条单独封顶 cap，不按顺序从「剩余持仓」里扣
        for intent_idx in non_sched:
            intent = intents[intent_idx]
            cur_rule_type = (intent.get("rule_type") or "").strip()
            if cur_rule_type not in sell_rule_types:
                continue

            cur_vol = int(intent.get("volume") or 0)
            cur_vol = (cur_vol // 100) * 100  # 对齐手数
            if cur_vol < 0:
                cur_vol = 0

            price_min, need_vol = precomputed.get((code_6, intent_idx), (0.0, 0))

            # 半仓腿已在策略内处理零头/最小金额，避免再抬量破坏「两笔独立半仓+零头并入」
            if intent.get("half_pair"):
                desired = min(cur_vol, cap)
            else:
                # 如果无法估价（price_min<=0），保守：不做 min_amount 调整，仅做可用上限截断
                desired = cur_vol
                if need_vol > 0:
                    desired = max(cur_vol, need_vol)
                desired = min(desired, cap)
                # 卖完后若剩余不够一手或不够最小单笔，并入本次，避免再付一次手续费
                remain = cap - desired
                if remain > 0 and remain < 100:
                    desired = cap
                elif (
                    remain >= 100
                    and price_min > 0
                    and min_order_amount > 0
                    and remain * price_min < min_order_amount
                ):
                    desired = cap

            intents[intent_idx]["volume"] = int(desired)

            intent["volume"] = int(desired) if desired > 0 else 0

        # 定时清仓：单独按「当前可用持仓 cap」与最小单笔金额校正
        for intent_idx in sched:
            intent = intents[intent_idx]
            cur_vol = int(intent.get("volume") or 0)
            cur_vol = (cur_vol // 100) * 100
            if cur_vol < 0:
                cur_vol = 0
            desired = cur_vol if cur_vol > 0 else cap
            desired = min(desired, cap)
            price_min, need_vol = precomputed.get((code_6, intent_idx), (0.0, 0))
            if need_vol > 0:
                desired = max(desired, need_vol)
            desired = min(desired, cap)
            intent["volume"] = int(desired) if desired > 0 else 0

    return intents
