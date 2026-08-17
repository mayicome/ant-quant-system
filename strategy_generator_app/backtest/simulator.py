"""
订单模拟：
- 日频模式：将意图在下一日开盘价处统一成交（原有逻辑，可选）。
- 同日 OHLC 模式：用当日日线高低收近似触价成交（单点买/卖/定时清仓）。
- Tick 模式：用当日 tick 数据按规则在首次满足条件时成交（突破价、笼子区间、定时等）。
"""

from collections import defaultdict
from datetime import date, datetime, time as dt_time
from typing import List, Dict, Any, Optional, Tuple, Set
import os
import configparser

from strategy_generator_app.backtest.true_breakthrough import (
    TRUE_BREAKTHROUGH_COND1_WINDOW_PRIOR_TICKS,
    evaluate_true_breakthrough_tick_with_detail,
    infer_tick_vol_to_shares_multiplier,
    is_breakthrough_buy_price_cross_tick,
    is_breakthrough_break_below_trigger_tick,
    normalize_true_breakthrough_cond1_mode,
    per_tick_trade_volumes_list,
    true_breakthrough_export_fields,
    window_prior_ticks_from_seconds,
)

try:
    from core.elastic_sell import (
        compute_best_sell_fallback_from_rule,
        load_elastic_confirm_triple,
        load_elastic_global_config,
    )
except Exception:
    compute_best_sell_fallback_from_rule = None  # type: ignore
    load_elastic_confirm_triple = None  # type: ignore
    load_elastic_global_config = None  # type: ignore

try:
    from core.utils.security_type import SecurityTypeUtil
except Exception:
    class SecurityTypeUtil:  # fallback: keep simulator working
        @staticmethod
        def is_fund(security_code: str) -> bool:
            return False

        @staticmethod
        def get_price_precision(security_code: str) -> int:
            return 2


# ---------------------------------------------------------------------------
# T+1 可卖：当日买入不可卖；下一交易日开盘 available = volume
# ---------------------------------------------------------------------------

def position_available(pos: Optional[Dict[str, Any]]) -> int:
    """可卖数量；无 available 字段时兼容旧持仓（视作全部可卖）。"""
    if not pos:
        return 0
    if "available" in pos and pos.get("available") is not None:
        try:
            return max(0, int(pos.get("available") or 0))
        except (TypeError, ValueError):
            return 0
    try:
        return max(0, int(pos.get("volume") or 0))
    except (TypeError, ValueError):
        return 0


def settle_positions_t1(positions: Dict[str, Dict[str, Any]]) -> None:
    """新交易日开盘：昨日及更早持仓全部转为可卖。"""
    for pos in (positions or {}).values():
        if not isinstance(pos, dict):
            continue
        try:
            vol = max(0, int(pos.get("volume") or 0))
        except (TypeError, ValueError):
            vol = 0
        pos["available"] = vol


def init_position_t1(
    volume: int,
    cost: float = 0.0,
    *,
    available: Optional[int] = None,
) -> Dict[str, Any]:
    vol = max(0, int(volume or 0))
    if available is None:
        avail = vol
    else:
        try:
            avail = max(0, min(vol, int(available)))
        except (TypeError, ValueError):
            avail = vol
    return {"volume": vol, "cost": float(cost or 0), "available": avail}


def apply_buy_fill_t1(
    positions: Dict[str, Dict[str, Any]],
    code_6: str,
    qty: int,
    fill_px: float,
) -> None:
    """买入：总量增加，可卖不变（当日买不可卖）。"""
    q = max(0, int(qty or 0))
    if q <= 0:
        return
    if code_6 not in positions:
        positions[code_6] = init_position_t1(0, 0.0, available=0)
    pos = positions[code_6]
    if "available" not in pos or pos.get("available") is None:
        pos["available"] = max(0, int(pos.get("volume") or 0))
    ov = int(pos.get("volume") or 0)
    oc = float(pos.get("cost") or 0)
    nv = ov + q
    pos["volume"] = nv
    pos["cost"] = round((ov * oc + float(fill_px) * q) / nv, 2) if nv else 0.0


def apply_sell_fill_t1(
    positions: Dict[str, Dict[str, Any]],
    code_6: str,
    want_qty: int,
) -> int:
    """卖出：按可卖截断；同时扣减 volume 与 available。返回实际卖出数量。"""
    pos = positions.get(code_6)
    if not pos:
        return 0
    want = max(0, int(want_qty or 0))
    if want <= 0:
        return 0
    vol = max(0, int(pos.get("volume") or 0))
    avail = position_available(pos)
    sold = min(want, avail, vol)
    if sold <= 0:
        return 0
    nv = vol - sold
    na = avail - sold
    if nv <= 0:
        del positions[code_6]
    else:
        pos["volume"] = nv
        pos["available"] = max(0, na)
    return sold


def positions_for_strategy_params(
    positions: Dict[str, Dict[str, Any]],
) -> Dict[str, int]:
    """策略 params['positions']：与实盘一致，传可卖数量。"""
    out: Dict[str, int] = {}
    for code_6, pos in (positions or {}).items():
        av = position_available(pos)
        if av > 0:
            out[code_6] = av
    return out


def positions_volume_for_strategy_params(
    positions: Dict[str, Dict[str, Any]],
) -> Dict[str, int]:
    """策略 params['positions_volume']：当前持股数量（含当日买入、T+1 尚不可卖部分）。

    半仓「剩余是否并入」按此字段；半仓股数基数见 positions_baseline。
    """
    out: Dict[str, int] = {}
    for code_6, pos in (positions or {}).items():
        try:
            vol = max(0, int((pos or {}).get("volume") or 0))
        except (TypeError, ValueError):
            vol = 0
        if vol > 0:
            out[code_6] = vol
    return out


def init_positions_baseline_from_positions(
    positions: Dict[str, Dict[str, Any]],
) -> Dict[str, int]:
    """回测起点：尚无卖出时，当前持股≈本轮累计买入，作为总仓位基准。"""
    return dict(positions_volume_for_strategy_params(positions))


def note_buy_fills_on_baseline(
    baseline: Dict[str, int],
    trades: Optional[List[Dict[str, Any]]],
) -> None:
    """把买入成交累加进总仓位基准。"""
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        side = str(t.get("side") or "").strip().lower()
        if side not in ("buy", "买入", "b"):
            continue
        code_6 = str(t.get("code") or t.get("stock_code") or "").strip()
        if "." in code_6:
            code_6 = code_6.split(".", 1)[0]
        if code_6.isdigit():
            code_6 = code_6.zfill(6)
        try:
            vol = int(t.get("volume") or 0)
        except (TypeError, ValueError):
            vol = 0
        if not code_6 or vol <= 0:
            continue
        baseline[code_6] = int(baseline.get(code_6) or 0) + vol


def prune_baseline_if_flat(
    baseline: Dict[str, int],
    positions: Dict[str, Dict[str, Any]],
) -> None:
    for code_6 in list(baseline.keys()):
        try:
            vol = int((positions.get(code_6) or {}).get("volume") or 0)
        except (TypeError, ValueError):
            vol = 0
        if vol < 100:
            baseline.pop(code_6, None)


def positions_baseline_for_strategy_params(
    baseline: Dict[str, int],
) -> Dict[str, int]:
    """策略 params['positions_baseline']：半仓用的总仓位基准。"""
    out: Dict[str, int] = {}
    for code_6, v in (baseline or {}).items():
        try:
            n = max(0, int(v or 0))
        except (TypeError, ValueError):
            n = 0
        if n >= 100:
            out[code_6] = n
    return out


try:
    from core.breakthrough_probe_buy import (
        can_use_probe_mode,
        can_start_rearm_add_confirm,
        enter_await_rearm,
        finish_summary,
        init_confirm_state,
        init_rearm_add_confirm_state,
        is_past_rearm_add_cutoff,
        make_rearm_meta,
        process_confirm_tick,
        record_rearm_confirm_failed,
        should_defer_remain_to_rearm,
        split_probe_volumes,
        DECISION_ADD,
        DECISION_TIMEOUT_ADD,
    )
except Exception:
    can_use_probe_mode = None  # type: ignore
    can_start_rearm_add_confirm = None  # type: ignore
    enter_await_rearm = None  # type: ignore
    finish_summary = None  # type: ignore
    init_confirm_state = None  # type: ignore
    init_rearm_add_confirm_state = None  # type: ignore
    is_past_rearm_add_cutoff = None  # type: ignore
    make_rearm_meta = None  # type: ignore
    process_confirm_tick = None  # type: ignore
    record_rearm_confirm_failed = None  # type: ignore
    should_defer_remain_to_rearm = None  # type: ignore
    split_probe_volumes = None  # type: ignore
    DECISION_ADD = "add"  # type: ignore
    DECISION_TIMEOUT_ADD = "timeout_add"  # type: ignore

try:
    from core.rule_activation import has_activation_config, process_activation_tick
except Exception:
    def has_activation_config(_intent):  # type: ignore
        return False

    def process_activation_tick(*_a, **_k):  # type: ignore
        return None

try:
    from core.smart_sell import tick_row_to_dict as _smart_tick_row_to_dict
except Exception:
    _smart_tick_row_to_dict = None  # type: ignore

try:
    from core.trading_config import (
        breakthrough_buy_probe_enabled as _cfg_probe_enabled,
        breakthrough_buy_require_break_below_trigger as _cfg_require_break_below,
        breakthrough_buy_require_true_breakthrough as _cfg_require_tb,
        non_early_order_sell_smart_sell_enabled as _cfg_smart_sell_enabled,
    )
except Exception:
    def _cfg_probe_enabled(default: bool = False) -> bool:  # type: ignore
        return default

    def _cfg_require_break_below(default: bool = False) -> bool:  # type: ignore
        return default

    def _cfg_require_tb(default: bool = False) -> bool:  # type: ignore
        return default

    def _cfg_smart_sell_enabled(default: bool = False) -> bool:  # type: ignore
        return default


# 单次 simulate_fills_with_ticks 内缓存的任务设置（避免每 tick 读配置）
_FILL_PASS_CFG: Dict[str, bool] = {
    "require_tb": False,
    "probe": False,
    "break_below": False,
    "smart_sell": False,
}


def _begin_fill_pass_cfg() -> None:
    try:
        _FILL_PASS_CFG["require_tb"] = bool(_cfg_require_tb(default=False))
    except Exception:
        _FILL_PASS_CFG["require_tb"] = False
    try:
        _FILL_PASS_CFG["probe"] = bool(_cfg_probe_enabled(default=False))
    except Exception:
        _FILL_PASS_CFG["probe"] = False
    try:
        _FILL_PASS_CFG["break_below"] = bool(_cfg_require_break_below(default=False))
    except Exception:
        _FILL_PASS_CFG["break_below"] = False
    try:
        _FILL_PASS_CFG["smart_sell"] = bool(_cfg_smart_sell_enabled(default=False))
    except Exception:
        _FILL_PASS_CFG["smart_sell"] = False


def _smart_sell_backtest_enabled() -> bool:
    return bool(_FILL_PASS_CFG.get("smart_sell"))


def _tick_dict_for_smart_sell(row_raw, last_price: float, best_bid: float, best_ask: float) -> Dict[str, Any]:
    try:
        if _smart_tick_row_to_dict is not None and row_raw is not None:
            data = _smart_tick_row_to_dict(row_raw)
            if data.get("lastPrice") or data.get("last_price"):
                return data
    except Exception:
        pass
    return {
        "lastPrice": last_price,
        "bidPrice": [best_bid] if best_bid else [],
        "askPrice": [best_ask] if best_ask else [],
    }


def _smart_sell_pre_close_from_row(row_raw) -> float:
    if isinstance(row_raw, dict):
        return float(row_raw.get("lastClose") or row_raw.get("preClose") or 0)
    try:
        if row_raw is not None and hasattr(row_raw, "get"):
            return float(row_raw.get("lastClose") or row_raw.get("preClose") or 0)
    except Exception:
        pass
    return 0.0


def _apply_smart_sell_backtest_tick(
    ss: Dict[str, Any],
    *,
    row_raw,
    last_price: float,
    best_bid: float,
    best_ask: float,
    tick_dt: Any,
    code_6: str,
    pos: Dict[str, Any],
    new_positions: Dict[str, Dict[str, Any]],
    new_cash: float,
    trades: List[Dict[str, Any]],
    date_str: str,
    commission: float,
    rule_type: str,
) -> float:
    """回测智能卖出单 tick：与实盘相同的挂单/改价门闩后再撮合。"""
    from core.smart_sell import current_tranche_volume, process_backtest_smart_sell_tick, record_fill

    tick_dict = _tick_dict_for_smart_sell(row_raw, last_price, best_bid, best_ask)
    fill_result = process_backtest_smart_sell_tick(
        ss,
        tick_dict,
        tick_dt,
        pre_close=_smart_sell_pre_close_from_row(row_raw),
    )
    tranche_vol = min(current_tranche_volume(ss), position_available(pos))
    if tranche_vol > 0 and fill_result:
        fv, fp = fill_result
        fv = min(int(fv), tranche_vol, position_available(pos))
        if fv > 0:
            record_fill(ss, fv, fp)
            fee = fp * fv * commission
            new_cash += fp * fv - fee
            sold = apply_sell_fill_t1(new_positions, code_6, fv)
            if sold <= 0:
                return new_cash
            position_after = int(
                (new_positions.get(code_6) or {}).get("volume") or 0
            )
            _append_smart_sell_trade(
                trades,
                date_str=date_str,
                tick_dt=tick_dt,
                code_6=code_6,
                fill_px=fp,
                sell_vol=sold,
                commission=commission,
                rule_type=rule_type,
                trigger_info=str(ss.get("trigger_info") or "智能卖出"),
                position_after=position_after,
                session=ss,
            )
    return new_cash


def _append_smart_sell_trade(
    trades: List[Dict[str, Any]],
    *,
    date_str: str,
    tick_dt: Any,
    code_6: str,
    fill_px: float,
    sell_vol: int,
    commission: float,
    rule_type: str,
    trigger_info: str,
    position_after: int,
    session: Dict[str, Any],
) -> None:
    from core.smart_sell import PHASE_CLOSING, PHASE_FORCE, smart_sell_order_strategy_name

    fee = fill_px * sell_vol * commission
    time_str = _fmt_trade_time(tick_dt)
    rt = (session.get("rule_type") or rule_type or "").strip()
    phase = str(session.get("phase") or "").strip().upper()
    phase_tag = ""
    if phase == PHASE_FORCE:
        phase_tag = "强平"
    elif phase == PHASE_CLOSING:
        phase_tag = "收盘竞价"
    smart_note = smart_sell_order_strategy_name(
        session.get("mode"),
        rule_type=rt,
        phase_tag=phase_tag,
    )
    row: Dict[str, Any] = {
        "date": date_str,
        "time": time_str,
        "code": code_6,
        "side": "sell",
        "price": round(fill_px, 2),
        "volume": sell_vol,
        "amount": round(fill_px * sell_vol, 2),
        "commission": round(fee, 2),
        "rule_type": rule_type,
        "position_after": position_after,
        "trigger_info": trigger_info + f"; {smart_note}",
        "smart_sell": True,
    }
    stock_name = (session.get("stock_name") or "").strip()
    if stock_name:
        row["stock_name"] = stock_name
    if session.get("leg_key"):
        row["leg_key"] = str(session.get("leg_key"))
    if session.get("rule_name"):
        row["rule_name"] = str(session.get("rule_name"))
    trades.append(row)

# tick 表需含 datetime, lastPrice；若有 open 可用于开盘成交
def _code6(code: str) -> str:
    code = (code or "").strip()
    return code.zfill(6) if len(code) < 6 else code[:6]


def _truthy_flag(v: Any) -> bool:
    if v is True or v == 1:
        return True
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return False


def _intent_has_price_band(intent: Dict[str, Any]) -> bool:
    """是否为「价格带内量价买入」意图（MA5 带策略）。"""
    try:
        lo = float(
            intent.get("band_low")
            if intent.get("band_low") is not None
            else (intent.get("price_low") or 0)
        )
        hi = float(
            intent.get("band_high")
            if intent.get("band_high") is not None
            else (intent.get("price_high") or 0)
        )
    except (TypeError, ValueError):
        return False
    return lo > 0 and hi > 0 and hi >= lo


def _intent_price_band(intent: Dict[str, Any]) -> Tuple[float, float]:
    lo = float(
        intent.get("band_low")
        if intent.get("band_low") is not None
        else (intent.get("price_low") or 0)
    )
    hi = float(
        intent.get("band_high")
        if intent.get("band_high") is not None
        else (intent.get("price_high") or 0)
    )
    return lo, hi


def _intent_band_accept_low(intent: Dict[str, Any]) -> Optional[float]:
    """
    价格带「有效成交下沿」（硬 pass）。
    监控带仍用 band_low~band_high；首次真突破时若现价 < accept_low 则作废当日机会。
    字段：band_accept_low（或 accept_band_low）。
    """
    intent = intent or {}
    raw = intent.get("band_accept_low")
    if raw is None or str(raw).strip() == "":
        raw = intent.get("accept_band_low")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        alo = float(raw)
    except (TypeError, ValueError):
        return None
    if alo <= 0:
        return None
    return alo


def _band_hard_pass_skip_reason(
    intent: Dict[str, Any],
    last_price: float,
    *,
    buy_ref_price: Optional[float] = None,
) -> Optional[str]:
    """
    首次真突破后硬 pass：
    - 现价 < 有效下沿；或
    - 买入参考价（卖一+滑点/成交预估）> 硬上沿 band_high（MA5）
    返回带「已结束，」前缀的原因；否则 None。
    """
    if not _intent_has_price_band(intent or {}):
        return None
    blo, bhi = _intent_price_band(intent or {})
    alo = _intent_band_accept_low(intent or {})
    try:
        from core.price_band_buy import band_hard_pass_reason

        reason = band_hard_pass_reason(
            last_price=float(last_price),
            band_low=float(blo),
            band_high=float(bhi),
            accept_low=alo,
            buy_ref_price=buy_ref_price,
        )
    except Exception:
        reason = None
        try:
            px = float(last_price)
            ref = float(buy_ref_price) if buy_ref_price and float(buy_ref_price) > 0 else px
        except (TypeError, ValueError):
            return None
        if alo is not None and px + 1e-12 < float(alo):
            reason = (
                f"首次真突破放弃: 现价={px:.2f}<有效下沿={float(alo):.2f}"
                f"（监控带[{blo:.2f},{bhi:.2f}]）"
            )
        elif float(bhi) > 0 and ref > float(bhi) + 1e-12:
            reason = (
                f"首次真突破放弃: 买入参考价={ref:.2f}>硬上沿MA5={float(bhi):.2f}"
                f"（现价={px:.2f}，监控带[{blo:.2f},{bhi:.2f}]）"
            )
    if not reason:
        return None
    if str(reason).startswith("已结束"):
        return str(reason)
    return f"已结束，{reason}"


def _intent_requires_true_breakthrough(intent: Dict[str, Any]) -> bool:
    """
    是否对突破买入做真突破过滤。
    - 价格带策略：始终看量价真突破（这是买入主判据）
    - 其它：与实盘一致，仅由任务设置 breakthrough_buy_require_true_breakthrough 控制
    """
    if intent.get("_sim_has_band") is True:
        return True
    if _intent_has_price_band(intent or {}):
        return True
    return bool(_FILL_PASS_CFG.get("require_tb"))


def _intent_probe_enabled(intent: Dict[str, Any]) -> bool:
    """与实盘一致：仅由任务设置 breakthrough_buy_probe_enabled 控制。价格带策略暂不走试探。"""
    if intent.get("_sim_has_band") is True or _intent_has_price_band(intent or {}):
        return False
    return bool(_FILL_PASS_CFG.get("probe"))


def _intent_require_break_below(intent: Dict[str, Any]) -> bool:
    """
    是否要求先跌破触发价再上穿突破。
    价格带策略不要求先跌破。
    """
    if intent.get("_sim_has_band") is True or _intent_has_price_band(intent or {}):
        return False
    return bool(_FILL_PASS_CFG.get("break_below"))


def _intent_true_breakthrough_cond1_mode(intent: Dict[str, Any]) -> str:
    """条件①算法：价格带强制 window；其它看意图字段。"""
    if _intent_has_price_band(intent or {}):
        return normalize_true_breakthrough_cond1_mode("window")
    return normalize_true_breakthrough_cond1_mode(
        (intent or {}).get("true_breakthrough_cond1_mode")
    )


def _intent_true_breakthrough_lookback_prior(intent: Dict[str, Any]) -> int:
    """条件①窗口 lookback_prior：按 true_breakthrough_window_sec（默认45）换算。"""
    intent = intent or {}
    if intent.get("true_breakthrough_window_sec") is not None:
        return window_prior_ticks_from_seconds(intent.get("true_breakthrough_window_sec"))
    if _intent_has_price_band(intent):
        return window_prior_ticks_from_seconds(45)
    return TRUE_BREAKTHROUGH_COND1_WINDOW_PRIOR_TICKS


def _evaluate_breakthrough_tb_metrics(
    code_6: str,
    row_dict: Dict[str, Any],
    vol_mul_by_code: Dict[str, float],
    tb_prefix_cnt: Dict[str, int],
    tb_prefix_sum: Dict[str, float],
    prev_tick_row: Dict[str, Dict[str, Any]],
    recent_tick_rows: Dict[str, List[Dict[str, Any]]],
    recent_break_vols: Dict[str, List[Optional[float]]],
    v_break_sh: Optional[float],
    cond1_mode: str = "tick3",
    lookback_prior: Optional[int] = None,
) -> Dict[str, Any]:
    vm = float(vol_mul_by_code.get(code_6, 100.0))
    cnt0 = int(tb_prefix_cnt.get(code_6, 0))
    sm0 = float(tb_prefix_sum.get(code_6, 0.0))
    avg_before = (sm0 / float(cnt0)) if cnt0 > 0 else None
    pr = prev_tick_row.get(code_6)
    rd = row_dict if isinstance(row_dict, dict) else {}
    ratio_window = (list(recent_tick_rows.get(code_6) or []) + [rd])[-5:]
    vol_hist = list(recent_break_vols.get(code_6) or [])
    _tb_ok, _tb_msg, tb_detail, tb_metrics = evaluate_true_breakthrough_tick_with_detail(
        code_6,
        rd,
        pr,
        vm,
        avg_before,
        v_break_sh,
        ratio_window,
        recent_vols=vol_hist,
        cond1_mode=cond1_mode,
        lookback_prior=lookback_prior,
    )
    if isinstance(tb_metrics, dict):
        tb_metrics = dict(tb_metrics)
        tb_metrics["_tb_detail"] = tb_detail
    return tb_metrics if isinstance(tb_metrics, dict) else {}


# 真突破盘口判定用到的 tick 列（避免每 tick 全行 to_dict）
_TB_ROW_SCALAR_COLS = (
    "lastPrice", "last_price", "tradePrice", "matchPrice", "price", "last",
    "amount", "volume", "lastVol", "tradeVol", "tradeVolume", "tickVol",
    "singleVol", "matchQty", "qty", "volume_delta", "cumVol", "totalVol", "dealVol",
    "askPrice", "askVol", "bidPrice", "bidVol",
)
_ELASTIC_CFG_CACHE: Optional[Tuple[int, int, int]] = None


def _advance_tb_prefix_state(
    code_6: str,
    row_raw: Any,
    v_break_sh: Optional[float],
    *,
    tb_prefix_sum: Dict[str, float],
    tb_prefix_cnt: Dict[str, int],
    prev_tick_row: Dict[str, Dict[str, Any]],
    recent_tick_rows: Dict[str, List[Dict[str, Any]]],
    recent_break_vols: Dict[str, List[Optional[float]]],
) -> None:
    """推进真突破前缀均量/盘口窗口（与实盘 _advance_true_breakthrough_tick_state 对齐）。"""
    row_dict = _light_tb_row(row_raw)
    if row_dict:
        prev_tick_row[code_6] = row_dict
        hist = recent_tick_rows.setdefault(code_6, [])
        hist.append(row_dict)
        if len(hist) > 5:
            recent_tick_rows[code_6] = hist[-5:]
    if v_break_sh is not None:
        vhist = recent_break_vols.setdefault(code_6, [])
        vhist.append(float(v_break_sh))
        keep = max(5, TRUE_BREAKTHROUGH_COND1_WINDOW_PRIOR_TICKS, 20)
        if len(vhist) > keep:
            recent_break_vols[code_6] = vhist[-keep:]
        tb_prefix_sum[code_6] = tb_prefix_sum.get(code_6, 0.0) + float(v_break_sh)
        tb_prefix_cnt[code_6] = tb_prefix_cnt.get(code_6, 0) + 1


def _seed_break_below_for_tick(
    code_6: str,
    last_price: float,
    prev_lp: Optional[float],
    code_intents: List[tuple],
    break_below_state: Dict[int, bool],
) -> None:
    """窗口外 tick 也累计「先跌破触发价」状态，与实盘开盘前已有 tick 一致。"""
    for idx, intent in code_intents:
        if (intent.get("rule_type") or "").strip() != "breakthrough_buy":
            continue
        if not _intent_require_break_below(intent):
            continue
        if break_below_state.get(idx):
            continue
        if intent.get("break_below_trigger_done"):
            break_below_state[idx] = True
            continue
        trig_px = float(intent.get("price") or 0)
        if is_breakthrough_break_below_trigger_tick(
            code_6, float(last_price), trig_px, prev_lp
        ):
            break_below_state[idx] = True


def _light_tb_row(row: Any) -> Dict[str, Any]:
    """仅提取真突破判定需要的 tick 字段，比 Series.to_dict() 轻量。"""
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    out: Dict[str, Any] = {}
    try:
        idx = row.index if hasattr(row, "index") else []
        # index 为 Index 时用 set 加速多次 `in` 判断
        idx_set = set(idx) if not isinstance(idx, set) else idx
        for name in _TB_ROW_SCALAR_COLS:
            if name in idx_set:
                v = row[name]
                if v is not None:
                    out[name] = v
        for i in range(1, 6):
            for prefix in (
                "askPrice", "askVol", "bidPrice", "bidVol", "ask", "bid",
                "sellPrice", "sellVol", "buyPrice", "buyVol",
            ):
                cn = f"{prefix}{i}"
                if cn in idx_set:
                    out[cn] = row[cn]
    except Exception:
        pass
    if out:
        return out
    try:
        return row.to_dict() if hasattr(row, "to_dict") else {}
    except Exception:
        return {}


def _prep_tick_records(df: Any, price_col: str) -> List[Dict[str, Any]]:
    """把 DataFrame 预成 dict 列表，避免 iterrows 的 Series 开销。"""
    # 只抽撮合/真突破/盘口需要的列
    want: Set[str] = {
        "datetime",
        "time",
        price_col,
        "open",
        "high",
        "low",
        "amount",
        "volume",
        "lastClose",
        "preClose",
        "bidPrice",
        "askPrice",
        "bidVol",
        "askVol",
        "lastVol",
        "tradeVol",
        "tradeVolume",
        "tickVol",
        "singleVol",
        "matchQty",
        "qty",
        "volume_delta",
        "cumVol",
        "totalVol",
        "dealVol",
    }
    for i in range(1, 6):
        for prefix in (
            "askPrice", "askVol", "bidPrice", "bidVol", "ask", "bid",
            "sellPrice", "sellVol", "buyPrice", "buyVol",
        ):
            want.add(f"{prefix}{i}")
    cols = [c for c in df.columns if c in want]
    if not cols:
        return []
    try:
        return df.loc[:, cols].to_dict("records")
    except Exception:
        return [r for _, r in df.iterrows()]  # type: ignore[misc]


def _build_activation_tick_data(
    last_price: float,
    tick_dt: Any,
    row_raw: Any,
    intent: Dict[str, Any],
) -> Dict[str, Any]:
    """构造延迟激活 tick 载荷（Series / dict 均可，含 askVol 等盘口字段）。"""
    tick_sim: Dict[str, Any] = {
        "lastPrice": float(last_price),
        "time": tick_dt,
    }
    light = _light_tb_row(row_raw)
    for k in (
        "lastClose", "preClose", "pre_close", "askVol", "askVolume",
        "askPrice", "bidPrice", "bidVol",
    ):
        if light.get(k) is not None:
            tick_sim[k] = light[k]
    try:
        pre_close = 0.0
        for src in (
            intent.get("debug_pre_close"),
            intent.get("pre_close"),
            light.get("lastClose"),
            light.get("preClose"),
            light.get("pre_close"),
        ):
            try:
                v = float(src or 0)
            except (TypeError, ValueError):
                v = 0.0
            if v > 0:
                pre_close = v
                break
        if pre_close > 0:
            tick_sim["lastClose"] = pre_close
    except (TypeError, ValueError):
        pass
    return tick_sim


def _update_backtest_rule_activations(
    code_intents: List[tuple],
    removed_indices: set,
    code_6: str,
    last_price: float,
    tick_dt: Any,
    row_raw: Any,
) -> None:
    """每 tick 更新延迟激活观察状态（与实盘 RuleActivationManager 一致）。"""
    for idx, intent in code_intents:
        if idx in removed_indices:
            continue
        rule_type = (intent.get("rule_type") or "").strip()
        if rule_type not in ("single_sell", "breakthrough_sell"):
            continue
        if not has_activation_config(intent):
            continue
        sim_rule = dict(intent)
        sim_rule["type"] = rule_type
        tick_sim = _build_activation_tick_data(last_price, tick_dt, row_raw, intent)
        process_activation_tick(
            sim_rule,
            tick_sim,
            stock_code=code_6,
            stock_name=str(intent.get("stock_name") or "").strip(),
        )
        if isinstance(sim_rule.get("activation"), dict):
            intent["activation"] = dict(sim_rule["activation"])


def _check_fill_buy(rule_type: str, intent: Dict[str, Any], last_price: float) -> bool:
    """买入类：当前 tick 价格是否满足成交条件（突破买入在 tick 撮合内单独处理）。

    与 ui/stock_chart_widget 单点买入口径一致：
    - single_buy：现价 <= 触发价（等价格跌至设定价或更低时买入）
    """
    rule_type = (rule_type or "single_buy").strip()
    if rule_type == "single_buy":
        price = float(intent.get("price") or 0)
        return price > 0 and float(last_price) <= price + 1e-9
    if rule_type == "breakthrough_buy":
        return False
    if rule_type == "cage_buy":
        # cage_buy 在 tick 撮合中走“先进入内区间(考虑壁厚)，再突破端点”状态机
        return False
    if rule_type == "best_buy":
        # best_buy（弹性买入）在 tick 撮合中走“先跌破 trigger，再按最低价反弹 rise_percent”状态机，
        # 这里返回 False，避免被当作普通“>= trigger”直接成交。
        return False
    return False


def _check_fill_sell(rule_type: str, intent: Dict[str, Any], last_price: float, tick_time: Optional[datetime] = None, scheduled_time: Optional[str] = None) -> bool:
    """卖出类：当前 tick 是否满足成交条件；scheduled_clear 需时间匹配。

    与 ui/stock_chart_widget 单点/突破卖出口径一致：
    - single_sell：现价 >= 触发价（例如触发价=涨停价则表示涨至涨停附近卖出）
    - breakthrough_sell：现价 < 触发价（向下突破触发线）
    """
    rule_type = (rule_type or "single_sell").strip()
    if rule_type == "single_sell":
        price = float(intent.get("price") or 0)
        return price > 0 and float(last_price) + 1e-9 >= price
    if rule_type == "breakthrough_sell":
        price = float(intent.get("price") or 0)
        return price > 0 and float(last_price) < price
    if rule_type == "cage_sell":
        # cage_sell 在 tick 撮合中走“先进入内区间(考虑壁厚)，再突破端点”状态机
        return False
    if rule_type == "best_sell":
        # best_sell（弹性卖出）在 tick 撮合中走“先上破 trigger，再按最高价回落 drop_percent”状态机，
        # 这里返回 False，避免被当作普通“<= trigger”直接成交。
        return False
    if rule_type == "scheduled_clear" and scheduled_time and tick_time:
        # scheduled_clear_time 如 "14:56:00"，检查当前 tick 时间是否 >= 该时刻；
        # 触发价（intent["price"]）由上层状态机配合，与实盘一致：定时后首笔有效 tick 须 current < trigger。
        try:
            h, m, s = map(int, scheduled_time.strip().split(":")[:3])
            from datetime import time as dt_time
            t = dt_time(h, m, s)
            tick_t = tick_time.time() if hasattr(tick_time, "time") else tick_time
            if hasattr(tick_t, "replace"):
                tick_t = tick_t
            else:
                tick_t = datetime.combine(date.today(), tick_t).time() if isinstance(tick_t, datetime) else tick_t
            return (tick_t >= t) if hasattr(tick_t, "__ge__") else False
        except Exception:
            return False
    return False


def _parse_time_str(s: str):
    """将 "HH:mm" 或 "HH:mm:ss" 转为 datetime.time"""
    from datetime import time as dt_time
    s = (s or "").strip()
    parts = s.split(":")
    h = int(parts[0]) if len(parts) > 0 else 0
    m = int(parts[1]) if len(parts) > 1 else 0
    sec = int(parts[2]) if len(parts) > 2 else 0
    return dt_time(h, m, sec)


def _fmt_trade_time(tick_dt: Any) -> str:
    """
    回测成交时间字符串：若数据源带微秒则显示到毫秒，避免同一秒内多笔 tick 在界面上都显示成同一时刻。
    """
    if tick_dt is None:
        return ""
    if hasattr(tick_dt, "strftime"):
        try:
            if getattr(tick_dt, "microsecond", 0):
                return tick_dt.strftime("%H:%M:%S.%f")[:-3]
        except Exception:
            pass
        return tick_dt.strftime("%H:%M:%S")
    return str(tick_dt)


def _seconds_between(t0: Any, t1: Any) -> float:
    """tick 时间差（秒），兼容 datetime / pandas.Timestamp。"""
    if t0 is None or t1 is None:
        return 0.0
    try:
        if hasattr(t1, "timestamp") and hasattr(t0, "timestamp"):
            return float(t1.timestamp() - t0.timestamp())
    except Exception:
        pass
    try:
        return float((t1 - t0).total_seconds())  # type: ignore[union-attr]
    except Exception:
        return 0.0


def _load_elastic_confirm_config() -> Tuple[int, int, int]:
    """
    从 data/config.ini 读取弹性买卖（best_buy/best_sell）的全局确认/冷却参数（按 tick 次数）。
    返回 (confirm_ticks, cooldown_after_extreme_ticks, dynamic_thresholds)。
    """
    global _ELASTIC_CFG_CACHE
    if _ELASTIC_CFG_CACHE is not None:
        return _ELASTIC_CFG_CACHE
    try:
        if load_elastic_confirm_triple is not None:
            _ELASTIC_CFG_CACHE = load_elastic_confirm_triple()
            return _ELASTIC_CFG_CACHE
    except Exception:
        pass
    _ELASTIC_CFG_CACHE = (4, 2, 2)
    return _ELASTIC_CFG_CACHE


def _extract_best_bid_ask(row: Any) -> Tuple[float, float]:
    """
    从 tick 行提取 best bid/ask（买一/卖一）：
    - bidPrice / askPrice 通常为 list，取 [0]
    - 兜底：若字段缺失则返回 (0, 0)
    """
    def _to_price(x: Any) -> float:
        """从 tick 的 bid/ask 结构中尽量提取“价格数字”。"""
        if x is None:
            return 0.0
        try:
            if isinstance(x, (int, float)):
                return float(x)
            if isinstance(x, str):
                s = x.strip()
                if not s:
                    return 0.0
                return float(s)
            if isinstance(x, dict):
                for k in ("price", "Price", "p", "P"):
                    if k in x:
                        v = x.get(k)
                        if v is not None:
                            return float(v)
                return 0.0
            if isinstance(x, (list, tuple)):
                if not x:
                    return 0.0
                # 常见结构：[(price, vol), ...] 或 [price, ...]
                first = x[0]
                # 若 first 是 (price, vol)
                if isinstance(first, (list, tuple)) and first:
                    # 取 tuple/list 第一个元素作为价格
                    return _to_price(first[0])
                return _to_price(first)
            # 兜底
            return float(x)
        except Exception:
            return 0.0

    bid = 0.0
    ask = 0.0
    try:
        bv = row.get("bidPrice") if hasattr(row, "get") else None
        if bv is None and hasattr(row, "__getitem__"):
            try:
                bv = row["bidPrice"]
            except Exception:
                bv = None
        av = row.get("askPrice") if hasattr(row, "get") else None
        if av is None and hasattr(row, "__getitem__"):
            try:
                av = row["askPrice"]
            except Exception:
                av = None

        # 常见：bidPrice/askPrice 为 list/tuple，取 [0] 作为买一/卖一
        if isinstance(bv, (list, tuple)) and bv:
            bid = _to_price(bv[0])
        else:
            bid = _to_price(bv)

        if isinstance(av, (list, tuple)) and av:
            ask = _to_price(av[0])
        else:
            ask = _to_price(av)
    except Exception:
        pass
    return float(bid or 0.0), float(ask or 0.0)


def _calc_fill_price(code_6: str, side: str, last_price: float, best_bid: float, best_ask: float) -> float:
    """
    用于回测成交价：
    - 买入：以卖一价 askPrice[0] 为基准，上调一个最小滑点单位
    - 卖出：以买一价 bidPrice[0] 为基准，下调一个最小滑点单位
    触发判断仍用 lastPrice；这里只影响“成交价/金额/成本”。
    """
    precision = SecurityTypeUtil.get_price_precision(code_6)
    slippage = 0.001 if precision == 3 else 0.01

    if side == "buy":
        base = best_ask if best_ask and best_ask > 0 else last_price
        return round(float(base) + slippage, precision)
    else:
        base = best_bid if best_bid and best_bid > 0 else last_price
        return round(float(base) - slippage, precision)


def simulate_fills_with_ticks(
    intents: List[Dict[str, Any]],
    ticks_by_stock: Dict[str, Any],
    trade_date: date,
    cash: float,
    positions: Dict[str, Dict[str, Any]],
    commission: float = 0.0003,
    run_start_time: Optional[str] = None,
    run_end_time: Optional[str] = None,
) -> tuple[List[Dict[str, Any]], float, Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """
    使用 tick 数据在 trade_date 当日按时间顺序模拟成交。
    run_start_time / run_end_time: 仅在此时间段内的 tick 参与成交，格式 "HH:mm" 或 "HH:mm:ss"，如 "09:45"、"14:56"。
    """
    import pandas as pd

    try:
        from .tick_cache_loader import tick_data_cache_module

        coerce_tick_dataframe = getattr(tick_data_cache_module(), "coerce_tick_dataframe", None)
    except Exception:
        coerce_tick_dataframe = None  # type: ignore

    trades: List[Dict[str, Any]] = []
    new_cash = cash
    new_positions = {k: dict(v) for k, v in positions.items()}
    _begin_fill_pass_cfg()
    unfilled = list(enumerate(intents))
    unfilled_by_code: Dict[str, List[tuple]] = defaultdict(list)
    need_activation_update = False
    for idx, inv in unfilled:
        c6 = _code6(inv.get("stock_code") or "")
        inv["_sim_code6"] = c6
        has_band = _intent_has_price_band(inv)
        inv["_sim_has_band"] = bool(has_band)
        rt = (inv.get("rule_type") or "").strip()
        if rt in ("single_sell", "breakthrough_sell") and has_activation_config(inv):
            need_activation_update = True
        unfilled_by_code[c6].append((idx, inv))
    n_unfilled = len(unfilled)
    tb_codes = {
        c6
        for c6, pairs in unfilled_by_code.items()
        for _, inv in pairs
        if (inv.get("rule_type") or "").strip() == "breakthrough_buy"
    }

    # 仅「运行窗口」内的 tick 参与撮合（与引擎传入的 run_start/run_end 一致）
    # 每条 row：…, row_raw(dict), v_break_sh（股）
    rows: List[tuple] = []
    vol_mul_by_code: Dict[str, float] = {}
    run_start = _parse_time_str(run_start_time) if run_start_time else None
    run_end = _parse_time_str(run_end_time) if run_end_time else None
    # 与 intelligentbuy 一致：逐 tick 前缀均量、前 tick 行字典（按股票）
    tb_prefix_sum: Dict[str, float] = {}
    tb_prefix_cnt: Dict[str, int] = {}
    prev_tick_row: Dict[str, Dict[str, Any]] = {}
    recent_tick_rows: Dict[str, List[Dict[str, Any]]] = {}
    recent_break_vols: Dict[str, List[Optional[float]]] = {}
    last_tick_price_by_code: Dict[str, float] = {}
    break_below_state: Dict[int, bool] = {}

    for code_6, df in ticks_by_stock.items():
        if df is None or len(df) == 0:
            continue
        code_6 = _code6(code_6)
        if coerce_tick_dataframe is not None:
            df = coerce_tick_dataframe(df)
        elif "datetime" not in df.columns and "time" in df.columns:
            df = df.copy()
            df["datetime"] = pd.to_datetime(df["time"], unit="ms")
        if df is None or len(df) == 0 or "datetime" not in df.columns:
            continue
        price_col = "lastPrice" if "lastPrice" in df.columns else "last_price"
        if price_col not in df.columns:
            for alt in ("price", "last", "matchPrice"):
                if alt in df.columns:
                    df = df.copy()
                    df["lastPrice"] = df[alt]
                    price_col = "lastPrice"
                    break
        if price_col not in df.columns:
            continue
        vm = infer_tick_vol_to_shares_multiplier(df)
        vol_mul_by_code[code_6] = float(vm)
        row_list = _prep_tick_records(df, price_col)
        vol_series = per_tick_trade_volumes_list(row_list, float(vm))
        open_col = "open" if "open" in df.columns else None
        code_intents_seed = unfilled_by_code.get(code_6) or []
        for j, row in enumerate(row_list):
            ts = row.get("datetime") if hasattr(row, "get") else None
            if ts is None:
                ts = row.get("time") if hasattr(row, "get") else None
            t_sec = None
            if hasattr(ts, "timestamp"):
                try:
                    t_sec = ts.timestamp()
                except Exception:
                    t_sec = None
            elif isinstance(ts, (int, float)):
                t_sec = ts / 1000.0 if ts > 1e12 else ts
            elif ts is not None:
                try:
                    ts_p = pd.to_datetime(ts, errors="coerce")
                    if ts_p is not None and not pd.isna(ts_p):
                        t_sec = ts_p.timestamp()
                except Exception:
                    t_sec = None
            if t_sec is None:
                continue
            price = float(row.get(price_col) or 0)
            if price <= 0:
                continue
            open_px = float(row.get(open_col) or 0) if open_col else 0.0
            best_bid, best_ask = _extract_best_bid_ask(row)
            hi = 0.0
            lo = 0.0
            tick_t = None
            if run_start is not None and run_end is not None:
                tick_t = getattr(ts, "time", lambda: None)()
                if tick_t is None and hasattr(ts, "hour"):
                    tick_t = dt_time(getattr(ts, "hour", 0), getattr(ts, "minute", 0), getattr(ts, "second", 0))
                if tick_t is not None and not (run_start <= tick_t <= run_end):
                    # 窗口前 tick 仍参与突破买入的 prev 价/真突破前缀，与实盘 09:25 等盘前 tick 一致
                    if (
                        tick_t < run_start
                        and code_6 in tb_codes
                    ):
                        prev_lp_seed = last_tick_price_by_code.get(code_6)
                        if code_intents_seed:
                            _seed_break_below_for_tick(
                                code_6,
                                price,
                                prev_lp_seed,
                                code_intents_seed,
                                break_below_state,
                            )
                        v_break_seed = vol_series[j] if j < len(vol_series) else None
                        _advance_tb_prefix_state(
                            code_6,
                            row,
                            v_break_seed,
                            tb_prefix_sum=tb_prefix_sum,
                            tb_prefix_cnt=tb_prefix_cnt,
                            prev_tick_row=prev_tick_row,
                            recent_tick_rows=recent_tick_rows,
                            recent_break_vols=recent_break_vols,
                        )
                        if price > 0:
                            last_tick_price_by_code[code_6] = float(price)
                    continue
            v_break = vol_series[j] if j < len(vol_series) else None
            rows.append(
                (t_sec, code_6, price, ts, open_px, best_bid, best_ask, hi, lo, row, v_break)
            )
    rows.sort(key=lambda x: x[0])
    rows_by_code: Dict[str, List[tuple]] = {}
    for it in rows:
        rows_by_code.setdefault(it[1], []).append(it)

    date_str = trade_date.strftime("%Y-%m-%d")
    removed_indices = set()
    # best_buy / best_sell 逐意图状态
    # - best_buy: 先跌破 trigger 后记录最低价 lowest，价格回升至 lowest*(1+rise_percent/100) 买入
    # - best_sell: 先上破 trigger 后记录最高价 highest，价格回落至 highest*(1-drop_percent/100) 卖出
    best_buy_state: Dict[int, Dict[str, Any]] = {}
    best_sell_state: Dict[int, Dict[str, Any]] = {}
    cfg_confirm_ticks, cfg_cooldown_ticks, cfg_dynamic_thresholds = _load_elastic_confirm_config()
    # cage_buy / cage_sell 逐意图状态：是否已进入过有效内区间（考虑壁厚）
    cage_state: Dict[int, Dict[str, Any]] = {}
    # scheduled_clear：与实盘一致，到达定时后的「首笔有效价」决策（价>=触发价则放弃当日本条）
    scheduled_clear_state: Dict[int, Dict[str, Any]] = {}
    smart_sell_state: Dict[int, Dict[str, Any]] = {}
    probe_state: Dict[int, Dict[str, Any]] = {}
    probe_rearm_state: Dict[int, Dict[str, Any]] = {}

    def _log_best_sell_ticks_debug(
        code_6: str,
        idx: int,
        state: Dict[str, Any],
        trigger: float,
        drop_pct: float,
        confirm_ticks: int,
        cooldown_ticks: int,
        fallback_price: float,
        hit_count: int,
        tick_dt: Any,
    ) -> None:
        """
        调试用：当 best_sell 触发成交时，打印从触发到成交这一段相关 tick 的关键数据，方便肉眼核对逻辑。
        包含：time、lastPrice、是否创新高/当前最高、是否在回落阈值之下、到当前为止“低于回落阈值的 tick 计数”等。

        默认关闭（避免刷屏）。需要时设置环境变量：ANT_BACKTEST_BEST_SELL_DEBUG=1
        """
        if not (os.environ.get("ANT_BACKTEST_BEST_SELL_DEBUG") or "").strip():
            return
        code_rows = rows_by_code.get(code_6) or []
        if not code_rows:
            return
        try:
            triggered_idx = int(state.get("trigger_tick_idx") or 0)
            highest_idx = int(state.get("highest_tick_idx") or 0)
            cur_idx = int(state.get("tick_idx") or 0)
        except Exception:
            triggered_idx = 0
            highest_idx = 0
            cur_idx = 0
        if triggered_idx <= 0 or cur_idx <= 0:
            return
        # 只打印从“首次上破触发价”到当前成交 tick 的这段
        start_idx = max(1, triggered_idx)
        end_idx = cur_idx
        print(
            f"[回测 best_sell debug] {trade_date} {code_6} intent_idx={idx} "
            f"trigger={trigger:.4f} drop={drop_pct:.4f}% confirm={confirm_ticks} cooldown={cooldown_ticks} "
            f"highest={float(state.get('highest') or 0.0):.4f} highest_idx={highest_idx} "
            f"fallback_now={fallback_price:.4f} final_hits={hit_count} "
            f"final_time={_fmt_trade_time(tick_dt)}"
        )
        below_cnt = 0
        highest_seen = 0.0
        seq = 0
        for tick_rec in code_rows:
            if len(tick_rec) < 7:
                continue
            t_sec2, c2, px2, ts2, _open2, _bid2, _ask2 = tick_rec[0], tick_rec[1], tick_rec[2], tick_rec[3], tick_rec[4], tick_rec[5], tick_rec[6]
            seq += 1
            if seq < start_idx or seq > end_idx:
                continue
            hi2 = float(tick_rec[7] or 0) if len(tick_rec) > 7 else 0.0
            lo2 = float(tick_rec[8] or 0) if len(tick_rec) > 8 else 0.0
            last2 = float(px2)
            is_new_high = False
            if last2 > highest_seen:
                highest_seen = last2
                is_new_high = True
            obs_low2 = float(lo2) if lo2 and lo2 > 0 else last2
            below = bool(obs_low2 <= fallback_price and last2 < float(state.get("highest") or 0.0))
            if below:
                below_cnt += 1
            ts_str = _fmt_trade_time(ts2)
            print(
                f"[回测 best_sell debug] {code_6} seq={seq} time={ts_str} last={last2:.4f} "
                f"high={hi2:.4f} low={lo2:.4f} "
                f"is_new_high={is_new_high} "
                f"is_highest_tick={(seq == highest_idx)} "
                f"below_fallback={below} below_cnt={below_cnt}"
            )

    def _cage_inner_bounds(intent: Dict[str, Any]) -> tuple[float, float]:
        """笼子规则有效内区间：[price_low + wall_thickness, price_high - wall_thickness]。"""
        low = float(intent.get("price_low") or 0)
        high = float(intent.get("price_high") or 0)
        wt = float(intent.get("wall_thickness") or 0)
        if wt <= 0:
            return low, high
        inner_low = low + wt
        inner_high = high - wt
        if inner_low > inner_high:
            mid = (low + high) / 2.0
            inner_low = inner_high = mid
        return inner_low, inner_high
    for row_pack in rows:
        if n_unfilled <= 0:
            break
        if len(row_pack) >= 10:
            (
                t_sec,
                code_6,
                last_price,
                tick_dt,
                open_px,
                best_bid,
                best_ask,
                tick_high,
                tick_low,
                row_raw,
                v_break_sh,
            ) = row_pack
        else:
            t_sec, code_6, last_price, tick_dt, open_px, best_bid, best_ask, tick_high, tick_low = row_pack[:9]
            row_raw, v_break_sh = None, None
        code_intents = unfilled_by_code.get(code_6) or []
        if not code_intents:
            # 该标的已无未成交意图：仍推进 TB 前缀（与原先「扫完全部 tick」一致）
            if code_6 in tb_codes:
                _advance_tb_prefix_state(
                    code_6,
                    row_raw,
                    v_break_sh,
                    tb_prefix_sum=tb_prefix_sum,
                    tb_prefix_cnt=tb_prefix_cnt,
                    prev_tick_row=prev_tick_row,
                    recent_tick_rows=recent_tick_rows,
                    recent_break_vols=recent_break_vols,
                )
            if float(last_price or 0) > 0:
                last_tick_price_by_code[code_6] = float(last_price)
            continue
        if need_activation_update:
            _update_backtest_rule_activations(
                code_intents, removed_indices, code_6, float(last_price), tick_dt, row_raw
            )
        to_remove = []
        for idx, intent in code_intents:
            if idx in removed_indices:
                continue
            rule_type = (intent.get("rule_type") or "").strip()
            volume = int(intent.get("volume") or 0)
            intent_stock_name = (intent.get("stock_name") or "").strip()
            buy_volume = volume
            if volume <= 0:
                to_remove.append(idx)
                continue
            filled = False
            trigger_info = ""
            tb_metrics = None
            can_buy = False
            if rule_type in ("single_buy", "breakthrough_buy", "cage_buy", "best_buy"):
                if rule_type == "cage_buy":
                    low = float(intent.get("price_low") or 0)
                    high = float(intent.get("price_high") or 0)
                    if low <= 0 or high < low:
                        to_remove.append(idx)
                        continue
                    inner_low, inner_high = _cage_inner_bounds(intent)
                    st = cage_state.setdefault(idx, {"entered": bool(intent.get("cage_entered") or False)})
                    # 若开盘价就在内区间，则视为已进入（即便第一笔 lastPrice 已跳出区间）
                    if not st["entered"] and open_px and inner_low < float(open_px) < inner_high:
                        st["entered"] = True
                    if inner_low < last_price < inner_high:
                        st["entered"] = True
                        continue
                    if not st["entered"]:
                        continue
                    # 跌破内下沿，或突破外上沿时触发（与实盘一致）
                    if not (last_price <= inner_low or last_price >= high):
                        continue
                    endpoint = "内下沿" if last_price <= inner_low else "外上沿"
                    trigger_info = (
                        f"笼子买入: 已入笼后触发{endpoint}; "
                        f"low={low:.2f}, high={high:.2f}, inner=[{inner_low:.2f},{inner_high:.2f}]"
                    )
                    can_buy = True
                elif rule_type == "best_buy":
                    trigger = float(intent.get("trigger_price") or 0)
                    rise_pct = float(intent.get("rise_percent") or 0)
                    # 回测：一律使用 config.ini [Elastic]，避免意图字典里误带 confirm_ticks 覆盖全局
                    confirm_ticks = int(cfg_confirm_ticks)
                    cooldown_ticks = int(cfg_cooldown_ticks)
                    if confirm_ticks < 0:
                        confirm_ticks = 2
                    if confirm_ticks == 0:
                        confirm_ticks = 1
                    if cooldown_ticks < 0:
                        cooldown_ticks = 0

                    # state: triggered/lowest/lowest_tick_idx/tick_idx/rebound_hit_count
                    state = best_buy_state.setdefault(
                        idx,
                        {
                            "triggered": False,
                            "lowest": 0.0,
                            "tick_idx": 0,
                            "lowest_tick_idx": 0,
                            "rebound_hit_count": 0,
                            "first_rebound_ts": None,
                            # 诊断用
                            "max_seen": 0.0,
                            "min_seen": 0.0,
                        },
                    )
                    state["tick_idx"] = int(state.get("tick_idx") or 0) + 1
                    try:
                        lp = float(last_price)
                        state["max_seen"] = max(float(state.get("max_seen") or 0.0), lp)
                        state["min_seen"] = lp if float(state.get("min_seen") or 0.0) <= 0 else min(float(state.get("min_seen") or lp), lp)
                    except Exception:
                        pass
                    if trigger <= 0:
                        to_remove.append(idx)
                        continue
                    if not state["triggered"]:
                        # 必须先“跌破触发价”
                        if last_price <= trigger:
                            state["triggered"] = True
                            state["lowest"] = float(last_price)
                            state["lowest_tick_idx"] = int(state["tick_idx"])
                            state["rebound_hit_count"] = 0
                            state["first_rebound_ts"] = None
                        continue
                    # 已触发：更新最低价后检查是否反弹到目标价
                    if float(last_price) < float(state.get("lowest") or last_price) or float(state.get("lowest") or 0.0) <= 0:
                        state["lowest"] = float(last_price)
                        state["lowest_tick_idx"] = int(state["tick_idx"])
                        state["rebound_hit_count"] = 0
                        state["first_rebound_ts"] = None
                        continue

                    # 动态反弹阈值：跌得越深，反弹阈值更苛刻（减少小反抽误触发）
                    try:
                        drop_from_trigger_pct = max(0.0, (trigger / float(state["lowest"]) - 1.0) * 100.0) if state["lowest"] else 0.0
                    except Exception:
                        drop_from_trigger_pct = 0.0
                    rise_scale = float(intent.get("rise_scale") or 0.35)
                    max_rise = float(intent.get("max_rise_percent") or 4.0)
                    if int(cfg_dynamic_thresholds) <= 0:
                        eff_rise = float(rise_pct)
                    else:
                        eff_rise = min(max_rise, float(rise_pct) + drop_from_trigger_pct * rise_scale)
                    target_price = float(state["lowest"]) * (1.0 + eff_rise / 100.0)

                    # 冷却：创新低后的 cooldown_ticks 个 tick 内不允许确认
                    lowest_idx = int(state.get("lowest_tick_idx") or 0)
                    if lowest_idx > 0 and (int(state["tick_idx"]) - lowest_idx) <= cooldown_ticks:
                        continue

                    # 仅用快照最新价 lastPrice 判断是否满足反弹条件（与实盘一致）
                    hit = (float(last_price) >= target_price and float(last_price) > float(state["lowest"]))
                    if not hit:
                        state["rebound_hit_count"] = 0
                        state["first_rebound_ts"] = None
                        continue
                    if int(state.get("rebound_hit_count") or 0) == 0:
                        state["first_rebound_ts"] = tick_dt
                    state["rebound_hit_count"] = int(state.get("rebound_hit_count") or 0) + 1
                    if int(state["rebound_hit_count"]) < confirm_ticks:
                        continue
                    _t0 = state.get("first_rebound_ts")
                    _el = _seconds_between(_t0, tick_dt) if _t0 is not None else 0.0
                    trigger_info = (
                        f"弹性买入: trigger={trigger:.2f}, lowest={float(state['lowest']):.2f}, "
                        f"rise={rise_pct:.2f}%, target={target_price:.2f}, confirm={confirm_ticks}, cooldown={cooldown_ticks}, "
                        f"tick_idx={int(state['tick_idx'])}, hits={int(state.get('rebound_hit_count') or 0)}, "
                        f"first_hit={_fmt_trade_time(_t0)} elapsed_s={_el:.2f}"
                    )
                    can_buy = True
                elif rule_type == "breakthrough_buy":
                    if make_rearm_meta is None or can_use_probe_mode is None:
                        intent["_sim_skip_reason"] = "试探买入模块不可用"
                        to_remove.append(idx)
                        continue

                    trig_px = float(intent.get("price") or 0)
                    prev_lp = last_tick_price_by_code.get(code_6)
                    can_buy = False
                    buy_volume = volume
                    tb_metrics = None
                    crossed_up = False

                    if _intent_require_break_below(intent):
                        if not break_below_state.get(idx):
                            if intent.get("break_below_trigger_done"):
                                break_below_state[idx] = True
                            elif is_breakthrough_break_below_trigger_tick(
                                code_6, float(last_price), trig_px, prev_lp
                            ):
                                break_below_state[idx] = True

                    prm = probe_rearm_state.get(idx)
                    if not isinstance(prm, dict):
                        prm = make_rearm_meta(
                            trigger_price=trig_px, planned_volume=volume
                        )
                        probe_rearm_state[idx] = prm

                    if is_past_rearm_add_cutoff(tick_dt):
                        pst_cut = probe_state.get(idx)
                        if isinstance(pst_cut, dict) and pst_cut.get("active"):
                            if int(pst_cut.get("remain_volume") or 0) > 0:
                                intent["_sim_skip_reason"] = (
                                    finish_summary(pst_cut) or "14:57后截止，放弃补买"
                                )
                                probe_state.pop(idx, None)
                                to_remove.append(idx)
                                continue
                        if prm.get("await_rearm"):
                            if prm.get("probe_bought"):
                                intent["_sim_skip_reason"] = (
                                    prm.get("last_skip_reason") or "14:57后截止"
                                )
                            else:
                                intent["_sim_skip_reason"] = (
                                    prm.get("last_skip_reason") or "14:57后截止，非真突破未下单"
                                )
                            probe_rearm_state.pop(idx, None)
                            to_remove.append(idx)
                            continue

                    pst = probe_state.get(idx)
                    if isinstance(pst, dict) and pst.get("active"):
                        decision = process_confirm_tick(
                            pst, code_6, float(last_price), tick_dt, v_break_sh
                        )
                        if decision in (DECISION_ADD, DECISION_TIMEOUT_ADD):
                            buy_volume = int(pst.get("remain_volume") or 0)
                            can_buy = buy_volume > 0
                            pst["remain_filled_volume"] = buy_volume
                            pst["active"] = False
                            prm["remain_pending"] = 0
                            prm["await_rearm"] = False
                            trigger_info = f"突破买入补买80%: {finish_summary(pst)}"
                        elif decision:
                            skip_msg = finish_summary(pst) or str(decision)
                            intent["_sim_skip_reason"] = skip_msg
                            probe_state.pop(idx, None)
                            if should_defer_remain_to_rearm(prm, tick_dt):
                                if pst.get("rearm_cross"):
                                    record_rearm_confirm_failed(prm)
                                enter_await_rearm(prm, reason=skip_msg)
                                continue
                            to_remove.append(idx)
                            continue
                        else:
                            continue
                    else:
                        use_band = _intent_has_price_band(intent)
                        if use_band:
                            band_lo, band_hi = _intent_price_band(intent)
                            crossed_up = band_lo <= float(last_price) <= band_hi
                        else:
                            crossed_up = is_breakthrough_buy_price_cross_tick(
                                code_6, float(last_price), trig_px, prev_lp
                            )
                            if crossed_up and _intent_require_break_below(intent):
                                if not break_below_state.get(idx):
                                    if prev_lp is None or float(prev_lp) > trig_px:
                                        crossed_up = False
                    if crossed_up:
                        planned_vol = volume
                        min_amt = float(intent.get("min_order_amount") or 5000)
                        probe_mode = _intent_probe_enabled(intent) and can_use_probe_mode(
                            planned_vol, float(last_price), min_amt
                        )
                        cnt0 = int(tb_prefix_cnt.get(code_6, 0))
                        sm0 = float(tb_prefix_sum.get(code_6, 0.0))
                        avg_before = (sm0 / float(cnt0)) if cnt0 > 0 else 0.0

                        if (
                            probe_mode
                            and prm.get("await_rearm")
                            and prm.get("probe_bought")
                            and int(prm.get("remain_pending") or 0) > 0
                            and can_start_rearm_add_confirm(prm, tick_dt)
                        ):
                            prm["await_rearm"] = False
                            probe_state[idx] = init_rearm_add_confirm_state(
                                prm,
                                code=code_6,
                                break_tick_dt=tick_dt,
                                avg_vol_before=avg_before,
                            )
                            continue

                        need_tb = _intent_requires_true_breakthrough(intent)
                        tb_detail = ""
                        if need_tb and not prm.get("probe_bought"):
                            tb_metrics = _evaluate_breakthrough_tb_metrics(
                                code_6,
                                _light_tb_row(row_raw),
                                vol_mul_by_code,
                                tb_prefix_cnt,
                                tb_prefix_sum,
                                prev_tick_row,
                                recent_tick_rows,
                                recent_break_vols,
                                v_break_sh,
                                cond1_mode=_intent_true_breakthrough_cond1_mode(intent),
                                lookback_prior=_intent_true_breakthrough_lookback_prior(
                                    intent
                                ),
                            )
                            tb_detail = str(tb_metrics.pop("_tb_detail", "") or "")
                            if not bool(tb_metrics.get("passed")):
                                # 价格带：带内未过真突破则继续盯；经典突破：首穿失败即结束
                                if _intent_has_price_band(intent):
                                    continue
                                intent["_sim_skip_reason"] = (
                                    "已结束，非真突破未下单"
                                    + (f": {tb_detail}" if tb_detail else "")
                                )
                                probe_rearm_state.pop(idx, None)
                                to_remove.append(idx)
                                continue
                            # 价格带硬 pass：深位 / 买入参考价(卖一+滑点)>MA5 → 当日作废
                            fill_est = _calc_fill_price(
                                code_6, "buy", float(last_price), best_bid, best_ask
                            )
                            hp_reason = _band_hard_pass_skip_reason(
                                intent,
                                float(last_price),
                                buy_ref_price=float(fill_est),
                            )
                            if hp_reason:
                                intent["_sim_skip_reason"] = hp_reason
                                probe_rearm_state.pop(idx, None)
                                to_remove.append(idx)
                                continue

                        if probe_mode:
                            probe_v, remain_v = split_probe_volumes(planned_vol)
                            tb_vol_ratio = 0.0
                            if isinstance(tb_metrics, dict):
                                try:
                                    tb_vol_ratio = float(tb_metrics.get("ratio_cond1") or 0)
                                except (TypeError, ValueError):
                                    tb_vol_ratio = 0.0
                            probe_state[idx] = init_confirm_state(
                                code=code_6,
                                trigger_price=trig_px,
                                planned_volume=planned_vol,
                                probe_volume=probe_v,
                                remain_volume=remain_v,
                                break_tick_dt=tick_dt,
                                avg_vol_before=avg_before,
                                tb_vol_ratio=tb_vol_ratio,
                                true_breakthrough_passed=bool(need_tb and tb_metrics),
                            )
                            prm.update(
                                {
                                    "probe_bought": True,
                                    "probe_filled": probe_v,
                                    "remain_pending": remain_v,
                                    "planned_volume": planned_vol,
                                    "trigger_price": trig_px,
                                    "tb_vol_ratio": tb_vol_ratio,
                                    "true_breakthrough_passed": bool(need_tb and tb_metrics),
                                    "await_rearm": False,
                                }
                            )
                            buy_volume = probe_v
                            can_buy = True
                            if need_tb and tb_detail:
                                trigger_info = (
                                    f"突破买入(真突破+试探): 当前价={last_price:.2f}>{trig_px:.2f}; "
                                    f"{tb_detail}; 试探{probe_v}股/待补{remain_v}股"
                                )
                            else:
                                trigger_info = (
                                    f"突破买入试探20%: {probe_v}股"
                                    f"（规则{planned_vol}股，待确认补买{remain_v}股）"
                                )
                        else:
                            if need_tb and tb_metrics is None:
                                tb_metrics = _evaluate_breakthrough_tb_metrics(
                                    code_6,
                                    _light_tb_row(row_raw),
                                    vol_mul_by_code,
                                    tb_prefix_cnt,
                                    tb_prefix_sum,
                                    prev_tick_row,
                                    recent_tick_rows,
                                    recent_break_vols,
                                    v_break_sh,
                                    cond1_mode=_intent_true_breakthrough_cond1_mode(intent),
                                    lookback_prior=_intent_true_breakthrough_lookback_prior(
                                        intent
                                    ),
                                )
                                tb_detail = str(tb_metrics.pop("_tb_detail", "") or "")
                            tb_ok = bool((tb_metrics or {}).get("passed"))
                            if need_tb:
                                if tb_ok:
                                    # 硬 pass：深位 / 买入参考价(卖一+滑点)>MA5 → 当日机会作废
                                    fill_est = _calc_fill_price(
                                        code_6,
                                        "buy",
                                        float(last_price),
                                        best_bid,
                                        best_ask,
                                    )
                                    hp_reason = _band_hard_pass_skip_reason(
                                        intent,
                                        float(last_price),
                                        buy_ref_price=float(fill_est),
                                    )
                                    if hp_reason:
                                        intent["_sim_skip_reason"] = hp_reason
                                        probe_rearm_state.pop(idx, None)
                                        to_remove.append(idx)
                                        continue
                                    can_buy = True
                                    if _intent_has_price_band(intent):
                                        blo, bhi = _intent_price_band(intent)
                                        alo = _intent_band_accept_low(intent)
                                        accept_s = (
                                            f" 有效下沿={alo:.2f}"
                                            if alo is not None
                                            else ""
                                        )
                                        trigger_info = (
                                            f"价格带量价买入: 现价={last_price:.2f} "
                                            f"带=[{blo:.2f},{bhi:.2f}]{accept_s}; {tb_detail}"
                                        )
                                    else:
                                        trigger_info = (
                                            f"突破买入(真突破): 当前价={last_price:.2f}>{trig_px:.2f}; {tb_detail}"
                                        )
                                else:
                                    if _intent_has_price_band(intent):
                                        continue
                                    intent["_sim_skip_reason"] = (
                                        "已结束，非真突破未下单"
                                        + (f": {tb_detail}" if tb_detail else "")
                                    )
                                    probe_rearm_state.pop(idx, None)
                                    to_remove.append(idx)
                            else:
                                can_buy = True
                                suffix = f"; {tb_detail}" if tb_detail else ""
                                trigger_info = (
                                    f"突破买入(breakthrough_buy): 当前价={last_price:.2f}>{trig_px:.2f}{suffix}"
                                )
                    if can_buy:
                        volume = buy_volume
                elif rule_type == "single_buy":
                    wait_unseal = bool(intent.get("wait_unseal"))
                    if wait_unseal:
                        try:
                            from core.rule_activation import (
                                first_ask_volume,
                                is_limit_up_sealed,
                            )

                            lu = float(
                                intent.get("limit_up") or intent.get("price") or 0
                            )
                            ask_vol = first_ask_volume(row_raw)
                            if is_limit_up_sealed(last_price, lu, ask_vol):
                                continue
                            # 开板：以涨停价买入
                            if lu > 0 and float(last_price) <= lu + 1e-9:
                                can_buy = True
                                trigger_info = (
                                    f"开盘买入(等开板): 现价={last_price:.2f} "
                                    f"涨停={lu:.2f} askVol={ask_vol} → 涨停价买入"
                                )
                                intent["_fill_at_limit_up"] = True
                        except Exception:
                            can_buy = False
                    else:
                        can_buy = _check_fill_buy(rule_type, intent, last_price)
                        if can_buy:
                            px = float(intent.get("price") or 0)
                            if intent.get("open_buy_ask"):
                                trigger_info = (
                                    f"开盘买入(卖一): 当前价={last_price:.2f}"
                                    f"<=触发价={px:.2f}"
                                )
                            else:
                                trigger_info = (
                                    f"单点买入(single_buy): 当前价={last_price:.2f}"
                                    f"<=触发价={px:.2f}"
                                )

                if can_buy:
                    fill_px = _calc_fill_price(code_6, "buy", last_price, best_bid, best_ask)
                    # 开盘涨停等开板：按涨停价成交
                    if intent.get("fill_at_limit_up") or intent.get("_fill_at_limit_up"):
                        try:
                            lu = float(intent.get("limit_up") or intent.get("price") or 0)
                            if lu > 0:
                                precision = SecurityTypeUtil.get_price_precision(code_6)
                                fill_px = round(lu, precision)
                        except Exception:
                            pass
                    # best_buy：按意图提供的涨停钳制成交价
                    if rule_type == "best_buy":
                        try:
                            lu = float(intent.get("limit_up") or 0)
                            if lu > 0:
                                precision = SecurityTypeUtil.get_price_precision(code_6)
                                if fill_px > lu:
                                    fill_px = round(lu, precision)
                        except Exception:
                            pass
                    if new_cash >= fill_px * volume * (1 + commission):
                        new_cash -= fill_px * volume * (1 + commission)
                        fee = fill_px * volume * commission
                        apply_buy_fill_t1(new_positions, code_6, volume, fill_px)
                        time_str = _fmt_trade_time(tick_dt)
                        buy_row: Dict[str, Any] = {
                            "date": date_str, "time": time_str, "code": code_6, "side": "buy",
                            "price": round(fill_px, 2), "volume": volume,
                            "amount": round(fill_px * volume, 2), "commission": round(fee, 2),
                            "rule_type": rule_type, "position_after": int(new_positions[code_6]["volume"]),
                            "trigger_info": trigger_info,
                        }
                        if intent_stock_name:
                            buy_row["stock_name"] = intent_stock_name
                        if intent.get("leg_key"):
                            buy_row["leg_key"] = str(intent.get("leg_key"))
                        if intent.get("name"):
                            buy_row["rule_name"] = str(intent.get("name"))
                        if rule_type == "breakthrough_buy" and tb_metrics:
                            buy_row.update(true_breakthrough_export_fields(tb_metrics))
                        if rule_type == "best_buy" and idx in best_buy_state:
                            buy_row["tick_idx"] = int(best_buy_state[idx].get("tick_idx") or 0)
                        trades.append(buy_row)
                        filled = True
            elif rule_type in ("single_sell", "breakthrough_sell", "cage_sell", "best_sell", "scheduled_clear"):
                pos = new_positions.get(code_6, {"volume": 0, "cost": 0.0, "available": 0})
                sell_vol = min(volume, position_available(pos))
                if sell_vol <= 0:
                    to_remove.append(idx)
                    continue
                if rule_type in ("single_sell", "breakthrough_sell"):
                    from core.rule_activation import rule_activation_allows_trigger

                    sim_rule = dict(intent)
                    sim_rule["type"] = rule_type
                    if not rule_activation_allows_trigger(sim_rule):
                        continue
                if _smart_sell_backtest_enabled() and idx in smart_sell_state:
                    ss = smart_sell_state[idx]
                    if ss.get("active"):
                        from core.smart_sell import is_session_complete

                        pos = new_positions.get(code_6, {"volume": 0, "cost": 0.0})
                        new_cash = _apply_smart_sell_backtest_tick(
                            ss,
                            row_raw=row_raw,
                            last_price=float(last_price),
                            best_bid=float(best_bid),
                            best_ask=float(best_ask),
                            tick_dt=tick_dt,
                            code_6=code_6,
                            pos=pos,
                            new_positions=new_positions,
                            new_cash=new_cash,
                            trades=trades,
                            date_str=date_str,
                            commission=commission,
                            rule_type=rule_type,
                        )
                        if is_session_complete(ss):
                            to_remove.append(idx)
                        continue
                fill_px = _calc_fill_price(code_6, "sell", last_price, best_bid, best_ask)
                # best_sell：按意图提供的跌停钳制成交价
                if rule_type == "best_sell":
                    try:
                        ld = float(intent.get("limit_down") or 0)
                        if ld > 0:
                            precision = SecurityTypeUtil.get_price_precision(code_6)
                            if fill_px < ld:
                                fill_px = round(ld, precision)
                    except Exception:
                        pass
                if rule_type == "cage_sell":
                    low = float(intent.get("price_low") or 0)
                    high = float(intent.get("price_high") or 0)
                    if low <= 0 or high < low:
                        to_remove.append(idx)
                        continue
                    inner_low, inner_high = _cage_inner_bounds(intent)
                    st = cage_state.setdefault(idx, {"entered": bool(intent.get("cage_entered") or False)})
                    # 若开盘价就在内区间，则视为已进入（即便第一笔 lastPrice 已跳出区间）
                    if not st["entered"] and open_px and inner_low < float(open_px) < inner_high:
                        st["entered"] = True
                    if inner_low < last_price < inner_high:
                        st["entered"] = True
                        continue
                    if not st["entered"]:
                        continue
                    # 突破外下沿，或跌破内上沿时触发（与实盘一致）
                    if last_price > low and last_price < inner_high:
                        continue
                    endpoint = "外下沿" if last_price <= low else "内上沿"
                    trigger_info = (
                        f"笼子卖出: 已入笼后触发{endpoint}; "
                        f"low={low:.2f}, high={high:.2f}, inner=[{inner_low:.2f},{inner_high:.2f}]"
                    )
                elif rule_type == "best_sell":
                    # 特殊口径：涨停即清仓（用于「卖：止盈-28开_涨停即卖」）
                    # 当最新价触及/超过 trigger_price（通常为涨停板）时，立即按 trigger_price 清仓，
                    # 不走“先上破再回落”的弹性卖出状态机。
                    clear_name = str(intent.get("name") or "").strip()
                    if clear_name == "涨停即清仓":
                        trigger = float(intent.get("trigger_price") or 0)
                        if trigger <= 0:
                            to_remove.append(idx)
                            continue
                        if float(last_price) < trigger:
                            continue
                        try:
                            precision = SecurityTypeUtil.get_price_precision(code_6)
                        except Exception:
                            precision = 2
                        fill_px = round(trigger, precision)
                        trigger_info = (
                            f"涨停即清仓: 当前价={float(last_price):.2f}, "
                            f"触发价(涨停)={trigger:.2f}, 成交价={fill_px:.2f}"
                        )
                    else:
                        # 弹性卖出：先上破 trigger，再回落到 highest*(1-drop_percent/100) 才成交
                        trigger = float(intent.get("trigger_price") or 0)
                        drop_pct = float(intent.get("drop_percent") or 0)
                        confirm_ticks = int(cfg_confirm_ticks)
                        cooldown_ticks = int(cfg_cooldown_ticks)
                        if confirm_ticks < 0:
                            confirm_ticks = 2
                        if confirm_ticks == 0:
                            confirm_ticks = 1
                        if cooldown_ticks < 0:
                            cooldown_ticks = 0

                        state = best_sell_state.setdefault(
                            idx,
                            {
                                "triggered": False,
                                "highest": 0.0,
                                "tick_idx": 0,
                                "highest_tick_idx": 0,
                                "pullback_hit_count": 0,
                                "trigger_tick_idx": 0,
                                "first_pullback_ts": None,
                                # 诊断用
                                "max_seen": 0.0,
                                "min_seen": 0.0,
                                "min_after_trigger": 0.0,
                                "min_after_highest": 0.0,
                                "last_fallback": 0.0,
                                "last_fixed_fallback": 0.0,
                                "times_above_trigger": 0,
                                "times_below_fallback": 0,
                                "times_below_fixed_fallback": 0,
                                "max_consecutive_hits": 0,
                            },
                        )
                        state["tick_idx"] = int(state.get("tick_idx") or 0) + 1
                        try:
                            lp = float(last_price)
                            state["max_seen"] = max(float(state.get("max_seen") or 0.0), lp)
                            state["min_seen"] = lp if float(state.get("min_seen") or 0.0) <= 0 else min(float(state.get("min_seen") or lp), lp)
                        except Exception:
                            pass
                        if trigger <= 0:
                            to_remove.append(idx)
                            continue
                        if not state["triggered"]:
                            if last_price >= trigger:
                                state["triggered"] = True
                                state["highest"] = float(last_price)
                                state["highest_tick_idx"] = int(state["tick_idx"])
                                state["pullback_hit_count"] = 0
                                state["trigger_tick_idx"] = int(state["tick_idx"])
                                state["first_pullback_ts"] = None
                                state["times_above_trigger"] = int(state.get("times_above_trigger") or 0) + 1
                                # 触发后最低价从触发这一笔开始计
                                try:
                                    lp = float(last_price)
                                    state["min_after_trigger"] = lp
                                    state["min_after_highest"] = lp
                                except Exception:
                                    pass
                            continue
                        # 已触发：更新最高价后检查是否回落
                        try:
                            lp = float(last_price)
                            mat = float(state.get("min_after_trigger") or 0.0)
                            state["min_after_trigger"] = lp if mat <= 0 else min(mat, lp)
                        except Exception:
                            pass
                        if float(last_price) > float(state.get("highest") or 0.0):
                            state["highest"] = float(last_price)
                            state["highest_tick_idx"] = int(state["tick_idx"])
                            state["pullback_hit_count"] = 0
                            state["first_pullback_ts"] = None
                            state["last_fallback"] = 0.0
                            state["last_fixed_fallback"] = 0.0
                            # 最高价更新后，从当前 tick 重新开始统计“最高价之后的最低价”
                            try:
                                state["min_after_highest"] = float(last_price)
                            except Exception:
                                pass
                            continue
                        else:
                            # 最高价未更新：刷新“最高价之后的最低价”
                            try:
                                lp = float(last_price)
                                mah = float(state.get("min_after_highest") or 0.0)
                                state["min_after_highest"] = lp if mah <= 0 else min(mah, lp)
                            except Exception:
                                pass

                        # 回落触发价（与实盘 core.elastic_sell 一致）
                        try:
                            limit_up_px = float(intent.get("debug_limit_up") or 0)
                        except Exception:
                            limit_up_px = 0.0
                        try:
                            pre_close_px = float(intent.get("debug_pre_close") or 0)
                        except Exception:
                            pre_close_px = 0.0
                        if compute_best_sell_fallback_from_rule is not None:
                            eff_drop, fallback_price = compute_best_sell_fallback_from_rule(
                                float(state["highest"]),
                                intent,
                                limit_up=limit_up_px,
                                pre_close=pre_close_px,
                            )
                        else:
                            eff_drop = float(drop_pct)
                            fallback_price = float(state["highest"]) * (1.0 - eff_drop / 100.0)
                        state["last_fallback"] = float(fallback_price)
                        state["last_eff_drop"] = float(eff_drop)
                        try:
                            fixed_drop = float(drop_pct)
                        except Exception:
                            fixed_drop = 0.0
                        state["last_fixed_fallback"] = float(state["highest"]) * (1.0 - fixed_drop / 100.0)

                        # 冷却：创新高后的 cooldown_ticks 个 tick 内不允许确认
                        highest_idx = int(state.get("highest_tick_idx") or 0)
                        if highest_idx > 0 and (int(state["tick_idx"]) - highest_idx) <= cooldown_ticks:
                            continue

                        # 仅用快照最新价 lastPrice 判断是否满足回落条件（与实盘一致）
                        hit = (float(last_price) <= fallback_price and float(last_price) < float(state["highest"]))
                        if not hit:
                            state["pullback_hit_count"] = 0
                            state["first_pullback_ts"] = None
                            continue
                        if int(state.get("pullback_hit_count") or 0) == 0:
                            state["first_pullback_ts"] = tick_dt
                        state["pullback_hit_count"] = int(state.get("pullback_hit_count") or 0) + 1
                        state["max_consecutive_hits"] = max(
                            int(state.get("max_consecutive_hits") or 0),
                            int(state.get("pullback_hit_count") or 0),
                        )
                        state["times_below_fallback"] = int(state.get("times_below_fallback") or 0) + 1
                        if int(state["pullback_hit_count"]) < confirm_ticks:
                            continue
                        _tp0 = state.get("first_pullback_ts")
                        _el_s = _seconds_between(_tp0, tick_dt) if _tp0 is not None else 0.0
                        # 先打印逐 tick 详情，再构造触发说明
                        try:
                            _log_best_sell_ticks_debug(
                                code_6=code_6,
                                idx=idx,
                                state=state,
                                trigger=trigger,
                                drop_pct=drop_pct,
                                confirm_ticks=confirm_ticks,
                                cooldown_ticks=cooldown_ticks,
                                fallback_price=fallback_price,
                                hit_count=int(state.get("pullback_hit_count") or 0),
                                tick_dt=tick_dt,
                            )
                        except Exception:
                            pass
                        trigger_info = (
                            f"弹性卖出: trigger={trigger:.2f}, highest={float(state['highest']):.2f}, "
                            f"drop={drop_pct:.2f}%, fallback={fallback_price:.2f}, confirm={confirm_ticks}, cooldown={cooldown_ticks}, "
                            f"tick_idx={int(state['tick_idx'])}, hits={int(state.get('pullback_hit_count') or 0)}, "
                            f"first_hit={_fmt_trade_time(_tp0)} elapsed_s={_el_s:.2f}"
                        )
                elif rule_type == "scheduled_clear":
                    sched_str = (intent.get("scheduled_clear_time") or "").strip()
                    trigger_px = float(intent.get("price") or 0)
                    force_clear = bool(intent.get("scheduled_clear_force")) or bool(
                        intent.get("scheduled_clear_on_hold_day")
                    )
                    if not force_clear and trigger_px <= 0:
                        # 兼容：无触发价且非每日条件 → 无条件
                        force_clear = not bool(intent.get("scheduled_clear_every_day"))
                    st_sc = scheduled_clear_state.setdefault(idx, {})
                    if st_sc.get("cancelled"):
                        to_remove.append(idx)
                        continue
                    if st_sc.get("_filled_attempt"):
                        to_remove.append(idx)
                        continue
                    try:
                        tick_t = tick_dt.time() if hasattr(tick_dt, "time") else None
                    except Exception:
                        tick_t = None
                    if tick_t is None:
                        continue
                    sched_t = _parse_time_str(sched_str) if sched_str else None
                    if sched_t is None:
                        to_remove.append(idx)
                        continue
                    if tick_t < sched_t:
                        continue
                    lp = float(last_price)
                    if lp <= 0:
                        continue
                    if not st_sc.get("_decided", False):
                        st_sc["_decided"] = True
                        if (not force_clear) and trigger_px > 0 and lp >= trigger_px:
                            st_sc["cancelled"] = True
                            to_remove.append(idx)
                            continue
                    st_sc["_filled_attempt"] = True
                    if not _check_fill_sell(rule_type, intent, last_price, tick_dt, sched_str):
                        to_remove.append(idx)
                        continue
                    if force_clear:
                        trigger_info = f"定时清仓(无条件): at>={sched_str}, 当前价={lp:.2f}"
                    else:
                        trigger_info = (
                            f"定时清仓: at>={sched_str}, 当前价={lp:.2f}<触发价{trigger_px:.2f}"
                            if trigger_px > 0
                            else f"定时清仓: at>={sched_str}（无有效触发价，仅按时间）"
                        )
                else:
                    if not _check_fill_sell(rule_type, intent, last_price):
                        continue
                    if rule_type in ("single_sell", "breakthrough_sell"):
                        px = float(intent.get("price") or 0)
                        # 中文描述：单点卖出 / 突破卖出；若出现其他类型，直接显示原始 rule_type 便于发现异常
                        if rule_type == "single_sell":
                            rule_name_zh = "单点卖出"
                        elif rule_type == "breakthrough_sell":
                            rule_name_zh = "突破卖出"
                        else:
                            rule_name_zh = rule_type
                        trigger_info = f"{rule_name_zh}({rule_type}): 当前价={last_price:.2f}, 触发价={px:.2f}"
                if _smart_sell_backtest_enabled():
                    from core.smart_sell import init_session, is_session_complete, should_use_smart_sell

                    if should_use_smart_sell(rule_type, enabled=True) and idx not in smart_sell_state:
                        from core.smart_sell import resolve_smart_sell_p_ref

                        intent_px = float(intent.get("price") or 0)
                        p_ref = resolve_smart_sell_p_ref(rule_type, intent_px, float(last_price))
                        lu = float(intent.get("limit_up") or 0)
                        ld = float(intent.get("limit_down") or 0)
                        ss = init_session(
                            code_6,
                            p_ref,
                            sell_vol,
                            limit_up=lu,
                            limit_down=ld,
                            trigger_info=trigger_info or "智能卖出",
                            rule_type=rule_type,
                            scheduled_clear_time=str(intent.get("scheduled_clear_time") or ""),
                            cost_price=float(pos.get("cost") or 0),
                            session_start_dt=tick_dt,
                        )
                        smart_sell_state[idx] = ss
                        if intent_stock_name:
                            ss["stock_name"] = intent_stock_name
                        if intent.get("leg_key"):
                            ss["leg_key"] = str(intent.get("leg_key"))
                        if intent.get("name"):
                            ss["rule_name"] = str(intent.get("name"))
                        new_cash = _apply_smart_sell_backtest_tick(
                            ss,
                            row_raw=row_raw,
                            last_price=float(last_price),
                            best_bid=float(best_bid),
                            best_ask=float(best_ask),
                            tick_dt=tick_dt,
                            code_6=code_6,
                            pos=pos,
                            new_positions=new_positions,
                            new_cash=new_cash,
                            trades=trades,
                            date_str=date_str,
                            commission=commission,
                            rule_type=rule_type,
                        )
                        if is_session_complete(ss):
                            to_remove.append(idx)
                        continue
                sold = apply_sell_fill_t1(new_positions, code_6, sell_vol)
                if sold <= 0:
                    continue
                fee = fill_px * sold * commission
                new_cash += fill_px * sold - fee
                position_after = int(
                    (new_positions.get(code_6) or {}).get("volume") or 0
                )
                time_str = _fmt_trade_time(tick_dt)
                sell_row: Dict[str, Any] = {
                    "date": date_str, "time": time_str, "code": code_6, "side": "sell",
                    "price": round(fill_px, 2), "volume": sold,
                    "amount": round(fill_px * sold, 2), "commission": round(fee, 2),
                    "rule_type": rule_type, "position_after": position_after,
                    "trigger_info": trigger_info,
                }
                if intent_stock_name:
                    sell_row["stock_name"] = intent_stock_name
                if intent.get("leg_key"):
                    sell_row["leg_key"] = str(intent.get("leg_key"))
                if intent.get("name"):
                    sell_row["rule_name"] = str(intent.get("name"))
                if rule_type == "best_sell" and idx in best_sell_state:
                    sell_row["tick_idx"] = int(best_sell_state[idx].get("tick_idx") or 0)
                trades.append(sell_row)
                filled = True
            if filled:
                pst = probe_state.get(idx)
                prm = probe_rearm_state.get(idx)
                if isinstance(pst, dict) and pst.get("active"):
                    pass
                elif (
                    isinstance(prm, dict)
                    and prm.get("await_rearm")
                    and prm.get("probe_bought")
                ):
                    pass
                else:
                    probe_state.pop(idx, None)
                    probe_rearm_state.pop(idx, None)
                    to_remove.append(idx)
        if to_remove:
            for idx in to_remove:
                removed_indices.add(idx)
            kept = [(i, inv) for i, inv in code_intents if i not in removed_indices]
            unfilled_by_code[code_6] = kept
            n_unfilled -= len(to_remove)

        # 推进「真突破」前缀与上一 tick 行（仅突破买入标的，与 intelligentbuy 对齐）
        if code_6 in tb_codes:
            _advance_tb_prefix_state(
                code_6,
                row_raw,
                v_break_sh,
                tb_prefix_sum=tb_prefix_sum,
                tb_prefix_cnt=tb_prefix_cnt,
                prev_tick_row=prev_tick_row,
                recent_tick_rows=recent_tick_rows,
                recent_break_vols=recent_break_vols,
            )
        if float(last_price or 0) > 0:
            last_tick_price_by_code[code_6] = float(last_price)

    # scheduled_clear 补充撮合：仅当窗口内存在「tick 时间 >= scheduled_clear_time」时成交。
    # 不到定时点不卖出；运行结束时间早于定时点（如结束 10:00、清仓 10:40）则本段不成交。
    # 不再用「窗口最后一笔」近似，避免 9:59:58 误平定时 10:40 的仓。
    still_unfilled = [
        pair for pairs in unfilled_by_code.values() for pair in pairs
    ]
    still_unfilled.sort(key=lambda x: x[0])
    for idx, intent in still_unfilled:
        rule_type = (intent.get("rule_type") or "").strip()
        if rule_type != "scheduled_clear":
            continue
        if idx in smart_sell_state and (smart_sell_state[idx] or {}).get("active"):
            continue
        code_6 = _code6(intent.get("stock_code") or "")
        code_ticks = rows_by_code.get(code_6) or []
        if not code_ticks:
            continue

        sched_t = None
        try:
            sched_str = (intent.get("scheduled_clear_time") or "").strip()
            if sched_str:
                sched_t = _parse_time_str(sched_str)
        except Exception:
            sched_t = None
        if sched_t is None:
            continue
        if run_start is not None and run_end is not None:
            if not (run_start <= sched_t <= run_end):
                continue

        trigger_px = float(intent.get("price") or 0)
        force_clear = bool(intent.get("scheduled_clear_force")) or bool(
            intent.get("scheduled_clear_on_hold_day")
        )
        if not force_clear and trigger_px <= 0:
            force_clear = not bool(intent.get("scheduled_clear_every_day"))
        chosen = None
        for row in code_ticks:
            if len(row) < 7:
                continue
            t_sec2, c2, px2, dt2, _open2, _bid2, _ask2 = (
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
            )
            tt = dt2.time() if hasattr(dt2, "time") else None
            if tt is None or tt < sched_t:
                continue
            lp = float(px2 or 0)
            if lp <= 0:
                continue
            # 条件清仓：定时后首笔有效 tick，须 current < trigger；无条件清仓：到点即卖
            if (not force_clear) and trigger_px > 0 and lp >= trigger_px:
                removed_indices.add(idx)
                chosen = None
                break
            chosen = (t_sec2, c2, px2, dt2, _bid2, _ask2)
            break
        if chosen is None:
            if idx in removed_indices:
                continue
            continue

        _, _, last_px, fill_dt, bid2, ask2 = chosen
        fill_price = _calc_fill_price(code_6, "sell", float(last_px), float(bid2 or 0), float(ask2 or 0))
        pos = new_positions.get(code_6, {"volume": 0, "cost": 0.0, "available": 0})
        volume = int(intent.get("volume") or 0)
        sell_vol = min(volume, position_available(pos))
        if sell_vol <= 0:
            continue
        sold = apply_sell_fill_t1(new_positions, code_6, sell_vol)
        if sold <= 0:
            continue
        fee = float(fill_price) * sold * commission
        new_cash += float(fill_price) * sold - fee
        position_after = int((new_positions.get(code_6) or {}).get("volume") or 0)
        time_str = _fmt_trade_time(fill_dt)
        sched_row: Dict[str, Any] = {
            "date": date_str, "time": time_str, "code": code_6, "side": "sell",
            "price": round(float(fill_price), 2), "volume": sold,
            "amount": round(float(fill_price) * sold, 2), "commission": round(fee, 2),
            "rule_type": rule_type, "position_after": position_after,
            "trigger_info": (
                f"定时清仓补充撮合: at>={sched_str}, 当前价={float(last_px):.2f}<触发价{trigger_px:.2f}"
                if trigger_px > 0
                else f"定时清仓补充撮合: at>={sched_str}"
            ),
        }
        intent_stock_name = (intent.get("stock_name") or "").strip()
        if intent_stock_name:
            sched_row["stock_name"] = intent_stock_name
        if intent.get("leg_key"):
            sched_row["leg_key"] = str(intent.get("leg_key"))
        if intent.get("name"):
            sched_row["rule_name"] = str(intent.get("name"))
        trades.append(sched_row)
        removed_indices.add(idx)

    if removed_indices:
        for c6, pairs in list(unfilled_by_code.items()):
            unfilled_by_code[c6] = [(i, inv) for i, inv in pairs if i not in removed_indices]
    unfilled = [pair for pairs in unfilled_by_code.values() for pair in pairs]
    unfilled.sort(key=lambda x: x[0])

    # 将诊断信息附到未成交意图上，便于上层输出原因
    remaining_intents = []
    for idx, inv in unfilled:
        try:
            rt = (inv.get("rule_type") or "").strip()
            if rt == "best_sell" and idx in best_sell_state:
                st = best_sell_state.get(idx) or {}
                inv["_sim_debug"] = {
                    "triggered": bool(st.get("triggered")),
                    "highest": float(st.get("highest") or 0.0),
                    "max_seen": float(st.get("max_seen") or 0.0),
                    "min_seen": float(st.get("min_seen") or 0.0),
                    "min_after_trigger": float(st.get("min_after_trigger") or 0.0),
                    "min_after_highest": float(st.get("min_after_highest") or 0.0),
                    "last_fallback": float(st.get("last_fallback") or 0.0),
                    "last_fixed_fallback": float(st.get("last_fixed_fallback") or 0.0),
                    "times_above_trigger": int(st.get("times_above_trigger") or 0),
                    "times_below_fallback": int(st.get("times_below_fallback") or 0),
                    "times_below_fixed_fallback": int(st.get("times_below_fixed_fallback") or 0),
                    "max_consecutive_hits": int(st.get("max_consecutive_hits") or 0),
                }
            elif rt == "best_buy" and idx in best_buy_state:
                st = best_buy_state.get(idx) or {}
                inv["_sim_debug"] = {
                    "triggered": bool(st.get("triggered")),
                    "lowest": float(st.get("lowest") or 0.0),
                    "max_seen": float(st.get("max_seen") or 0.0),
                    "min_seen": float(st.get("min_seen") or 0.0),
                }
        except Exception:
            pass
        remaining_intents.append(inv)
    return trades, new_cash, new_positions, remaining_intents


def simulate_fills(
    intents: List[Dict[str, Any]],
    next_day_prices: Dict[str, float],
    cash: float,
    positions: Dict[str, Dict[str, Any]],
    commission: float = 0.0003,
) -> tuple[List[Dict[str, Any]], float, Dict[str, Dict[str, Any]]]:
    """
    日频模式：将当日意图在「下一日开盘价」处统一成交，返回成交记录、更新后现金、更新后持仓。
    """
    trades: List[Dict[str, Any]] = []
    new_cash = cash
    new_positions = {k: dict(v) for k, v in positions.items()}

    for intent in intents:
        code_6 = _code6(intent.get("stock_code") or "")
        rule_type = (intent.get("rule_type") or "single_buy").strip()
        volume = int(intent.get("volume") or 0)
        intent_stock_name = (intent.get("stock_name") or "").strip()
        if volume <= 0:
            continue
        fill_price = next_day_prices.get(code_6)
        if fill_price is None or fill_price <= 0:
            continue
        fill_price = float(fill_price)
        amount = fill_price * volume
        fee = amount * commission

        if rule_type in ("single_buy", "breakthrough_buy", "cage_buy", "best_buy"):
            if rule_type == "breakthrough_buy" and _intent_requires_true_breakthrough(intent):
                continue
            trig_px = float(intent.get("price") or 0)
            # 日频近似：与 tick 级 _check_fill_buy 一致（仅当有有效触发价时过滤）
            if rule_type == "single_buy" and trig_px > 0:
                if float(fill_price) - 1e-9 > trig_px:
                    continue
            if amount + fee > new_cash:
                continue
            new_cash -= amount + fee
            apply_buy_fill_t1(new_positions, code_6, volume, fill_price)
            trades.append({
                "code": code_6, "side": "buy", "price": fill_price, "volume": volume,
                "amount": amount, "commission": fee, "rule_type": rule_type,
                "position_after": int(new_positions[code_6]["volume"]),
                **({"stock_name": intent_stock_name} if intent_stock_name else {}),
                **({"leg_key": str(intent.get("leg_key"))} if intent.get("leg_key") else {}),
                **({"rule_name": str(intent.get("name"))} if intent.get("name") else {}),
            })
        elif rule_type in ("single_sell", "breakthrough_sell", "cage_sell", "best_sell", "scheduled_clear"):
            pos = new_positions.get(code_6, {"volume": 0, "cost": 0.0, "available": 0})
            sell_vol = min(volume, position_available(pos))
            if sell_vol <= 0:
                continue
            trig_px = float(intent.get("price") or 0)
            # 日频近似：与 tick 级 _check_fill_sell 一致（仅当有有效触发价时过滤）
            if rule_type == "single_sell" and trig_px > 0:
                if float(fill_price) + 1e-9 < trig_px:
                    continue
            elif rule_type == "breakthrough_sell" and trig_px > 0:
                if not (float(fill_price) < trig_px):
                    continue
            if rule_type == "scheduled_clear":
                trig = float(intent.get("price") or 0)
                if trig > 0 and not (float(fill_price) < trig):
                    continue
            sold = apply_sell_fill_t1(new_positions, code_6, sell_vol)
            if sold <= 0:
                continue
            sell_amount = fill_price * sold
            fee = sell_amount * commission
            new_cash += sell_amount - fee
            position_after = int((new_positions.get(code_6) or {}).get("volume") or 0)
            trades.append({
                "code": code_6, "side": "sell", "price": fill_price, "volume": sold,
                "amount": sell_amount, "commission": fee, "rule_type": rule_type,
                "position_after": position_after,
                **({"stock_name": intent_stock_name} if intent_stock_name else {}),
                **({"leg_key": str(intent.get("leg_key"))} if intent.get("leg_key") else {}),
                **({"rule_name": str(intent.get("name"))} if intent.get("name") else {}),
            })

    return trades, new_cash, new_positions


def _same_day_ohlc_sell_kind(intent: Dict[str, Any]) -> str:
    """日线同日卖出分类（勿用「涨停」子串，以免误伤「近10日涨停价半仓」）。"""
    rt = (intent.get("rule_type") or "").strip()
    name = str(intent.get("name") or "")
    leg = str(intent.get("leg_key") or "")
    force = bool(intent.get("scheduled_clear_force")) or bool(
        intent.get("scheduled_clear_on_hold_day")
    )
    if rt == "scheduled_clear":
        return "force" if force else "ma20_1455"
    if rt != "single_sell":
        return "other"
    if "涨停即清仓" in name:
        return "limit_up_clear"
    if "OPEN50" in leg or "开盘涨幅" in name:
        return "open50"
    if "LU10" in leg or "近10日涨停价" in name:
        return "lu10"
    return "single_up"


def _same_day_ohlc_sell_rank(intent: Dict[str, Any]) -> Tuple[int, float]:
    """同日卖出顺序（对齐上涨路径 open→high，尾盘再清仓）：

    1. 盘中向上单点（OPEN50 / LU10 / 其它触价卖）：按触发价升序
    2. 涨停即清仓（通常触发价最高）
    3. 1455 破 MA20
    4. 第 N 日强清
    """
    kind = _same_day_ohlc_sell_kind(intent)
    px = float(intent.get("price") or intent.get("trigger_price") or 0)
    if kind in ("open50", "lu10", "single_up"):
        # 触发价升序：先碰到较低的半仓腿
        return (0, px if px > 0 else 0.0)
    if kind == "limit_up_clear":
        return (1, px if px > 0 else 1e18)
    if kind == "ma20_1455":
        return (2, 0.0)
    if kind == "force":
        return (3, 0.0)
    return (0, px if px > 0 else 0.0)


def simulate_fills_same_day_ohlc(
    intents: List[Dict[str, Any]],
    ohlc_by_code: Dict[str, Dict[str, float]],
    fill_day: date,
    cash: float,
    positions: Dict[str, Dict[str, Any]],
    commission: float = 0.0003,
) -> tuple[List[Dict[str, Any]], float, Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """
    同日日线撮合（对齐单点买/卖 + scheduled_clear）：
    - single_buy：low <= 触发价 → 成交价 min(open, 触发价)
    - single_sell：high >= 触发价 → 成交价 max(open, 触发价)
      同日多腿：按触发价升序（OPEN→LU→涨停清仓），再处理 1455/强清
    - scheduled_clear 条件清仓（1455 破线）：close < 触发价 → 成交价 close
    - scheduled_clear 强清：到持仓日 → 成交价 close
    弹性/笼子/突破等非单点规则本路径不撮合（保留在 remaining）。
    """
    trades: List[Dict[str, Any]] = []
    new_cash = cash
    new_positions = {k: dict(v) for k, v in positions.items()}
    remaining: List[Dict[str, Any]] = []
    day_s = fill_day.strftime("%Y-%m-%d") if fill_day else ""

    buy_types = ("single_buy",)
    sell_types = ("single_sell", "scheduled_clear")

    buys: List[Dict[str, Any]] = []
    sells: List[Dict[str, Any]] = []
    for intent in intents or []:
        rt = (intent.get("rule_type") or "").strip()
        if rt in buy_types:
            buys.append(intent)
        elif rt in sell_types:
            sells.append(intent)
        else:
            remaining.append(intent)

    def _bar(code_6: str) -> Optional[Dict[str, float]]:
        b = ohlc_by_code.get(code_6) or {}
        if not isinstance(b, dict):
            return None
        try:
            o = float(b.get("open") or 0)
            h = float(b.get("high") or 0)
            low_v = float(b.get("low") or 0)
            cl = float(b.get("close") or 0)
        except (TypeError, ValueError):
            return None
        if cl <= 0 and o <= 0:
            return None
        if o <= 0:
            o = cl
        if cl <= 0:
            cl = o
        if h <= 0:
            h = max(o, cl)
        if low_v <= 0:
            low_v = min(o, cl)
        return {"open": o, "high": h, "low": low_v, "close": cl}

    for intent in buys:
        code_6 = _code6(intent.get("stock_code") or "")
        volume = int(intent.get("volume") or 0)
        if not code_6 or volume <= 0:
            continue
        bar = _bar(code_6)
        if not bar:
            remaining.append(intent)
            continue
        trig = float(intent.get("price") or 0)
        if trig <= 0:
            remaining.append(intent)
            continue
        if float(bar["low"]) - 1e-9 > trig:
            remaining.append(intent)
            continue
        fill_px = min(float(bar["open"]), trig)
        if fill_px <= 0:
            remaining.append(intent)
            continue
        try:
            precision = SecurityTypeUtil.get_price_precision(code_6)
            fill_px = round(fill_px, precision)
        except Exception:
            fill_px = round(fill_px, 2)
        amount = fill_px * volume
        fee = amount * commission
        if amount + fee > new_cash:
            remaining.append(intent)
            continue
        new_cash -= amount + fee
        apply_buy_fill_t1(new_positions, code_6, volume, fill_px)
        intent_stock_name = (intent.get("stock_name") or "").strip()
        trigger_info = (
            f"日线单点买入: low={bar['low']:.2f}<=触发价={trig:.2f} "
            f"成交={fill_px:.2f}(=min(open={bar['open']:.2f},触发价))"
        )
        trades.append({
            "code": code_6,
            "side": "buy",
            "price": fill_px,
            "volume": volume,
            "amount": amount,
            "commission": fee,
            "rule_type": "single_buy",
            "position_after": int(new_positions[code_6]["volume"]),
            "date": day_s,
            # 开盘已在线下→按开盘；盘中触线→记 10:00（日线无精确时点）
            "time": (
                "09:30:00"
                if abs(fill_px - float(bar["open"])) <= 1e-9
                else "10:00:00"
            ),
            "trigger_info": trigger_info,
            **({"stock_name": intent_stock_name} if intent_stock_name else {}),
            **({"leg_key": str(intent.get("leg_key"))} if intent.get("leg_key") else {}),
            **({"rule_name": str(intent.get("name"))} if intent.get("name") else {}),
        })

    sells_sorted = sorted(sells, key=_same_day_ohlc_sell_rank)
    for intent in sells_sorted:
        code_6 = _code6(intent.get("stock_code") or "")
        volume = int(intent.get("volume") or 0)
        rt = (intent.get("rule_type") or "").strip()
        if not code_6 or volume <= 0:
            continue
        bar = _bar(code_6)
        if not bar:
            remaining.append(intent)
            continue
        pos = new_positions.get(code_6, {"volume": 0, "cost": 0.0, "available": 0})
        sell_vol = min(volume, position_available(pos))
        if sell_vol <= 0:
            remaining.append(intent)
            continue
        trig = float(intent.get("price") or 0)
        force = bool(intent.get("scheduled_clear_force")) or bool(
            intent.get("scheduled_clear_on_hold_day")
        )
        fill_px = 0.0
        trigger_info = ""
        if rt == "single_sell":
            if trig <= 0 or float(bar["high"]) + 1e-9 < trig:
                remaining.append(intent)
                continue
            fill_px = max(float(bar["open"]), trig)
            trigger_info = (
                f"日线单点卖出: high={bar['high']:.2f}>=触发价={trig:.2f} "
                f"成交={fill_px:.2f}(=max(open={bar['open']:.2f},触发价))"
            )
        elif rt == "scheduled_clear":
            if force:
                fill_px = float(bar["close"])
                trigger_info = f"日线定时强清: 收盘价={fill_px:.2f}"
            else:
                if trig <= 0:
                    remaining.append(intent)
                    continue
                # 1455 破线 ≈ 收盘跌破触发价
                if float(bar["close"]) + 1e-9 >= trig:
                    # 当日放弃（与 tick：价>=触发价则不卖）一致
                    continue
                fill_px = float(bar["close"])
                trigger_info = (
                    f"日线定时清仓(收盘破线): close={fill_px:.2f}<触发价={trig:.2f}"
                )
        else:
            remaining.append(intent)
            continue
        if fill_px <= 0:
            remaining.append(intent)
            continue
        try:
            precision = SecurityTypeUtil.get_price_precision(code_6)
            fill_px = round(fill_px, precision)
        except Exception:
            fill_px = round(fill_px, 2)
        sold = apply_sell_fill_t1(new_positions, code_6, sell_vol)
        if sold <= 0:
            remaining.append(intent)
            continue
        sell_amount = fill_px * sold
        fee = sell_amount * commission
        new_cash += sell_amount - fee
        position_after = int((new_positions.get(code_6) or {}).get("volume") or 0)
        intent_stock_name = (intent.get("stock_name") or "").strip()
        trades.append({
            "code": code_6,
            "side": "sell",
            "price": fill_px,
            "volume": sold,
            "amount": sell_amount,
            "commission": fee,
            "rule_type": rt,
            "position_after": position_after,
            "date": day_s,
            "time": "15:00:00" if rt == "scheduled_clear" else "09:30:00",
            "trigger_info": trigger_info,
            **({"stock_name": intent_stock_name} if intent_stock_name else {}),
            **({"leg_key": str(intent.get("leg_key"))} if intent.get("leg_key") else {}),
            **({"rule_name": str(intent.get("name"))} if intent.get("name") else {}),
        })

    return trades, new_cash, new_positions, remaining


def next_day_open_prices(
    stock_codes_6: List[str],
    next_date: date,
    get_historical_prices_fn,
) -> Dict[str, float]:
    """
    获取 next_date 当日各标的开盘价（此处用收盘近似），用于日频 simulate_fills。
    """
    raw = get_historical_prices_fn(stock_codes_6, next_date)
    if not isinstance(raw, dict) or "_error" in raw:
        return {}
    return {c: float((r.get("current") or r.get("最新价") or 0)) for c, r in raw.items() if isinstance(r, dict)}
