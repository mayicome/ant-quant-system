# -*- coding: utf-8 -*-
"""突破买入试探建仓：先买 20%，确认窗口内再决定是否补买 80%（基于规则 volume，与策略「一半金额」无关）。"""
from __future__ import annotations

from datetime import datetime, time as dt_time
from typing import Any, Dict, Optional, Tuple

PROBE_RATIO = 0.20
REMAIN_RATIO = 0.80
CONFIRM_WINDOW_SECONDS = 45.0
MIN_OBS_SECONDS = 10.0
MIN_OBS_TICKS = 5
CONSECUTIVE_BELOW_ABORT = 2

# 补买截止：14:57:00 后不再等待「跌回再上穿」补买
REARM_ADD_CUTOFF_TIME = dt_time(14, 57, 0)
# 强突破才允许「确认窗失败后再次上穿补买」；每日最多 2 次失败的补买确认窗（不含成功那次）
MAX_REARM_ADD_ATTEMPTS = 2

# 追价：默认 2.5%；真突破且强量比(≥3)时不因追价放弃补买
CHASE_PCT = 0.025
STRONG_TB_VOL_RATIO = 3.0

# 补买：延续强势（窗口内接近新高 + 量维持），弱突破(量比<2)要求更严
CONTINUATION_ABOVE_RATIO = 0.50
CONTINUATION_NEAR_HIGH_TICKS = 2
WEAK_TB_VOL_RATIO = 2.0
WEAK_TB_ABOVE_RATIO = 0.65
WEAK_TB_MIN_PROGRESS_PCT = 0.004
MIN_PROGRESS_TICKS = 1
FOLLOW_THROUGH_RATIO = 1.0
WEAK_FOLLOW_THROUGH_RATIO = 1.15

DECISION_NONE = ""
DECISION_FAKE = "fake_breakout"
DECISION_CHASE = "chase_skip"
DECISION_ADD = "add_remain"
DECISION_TIMEOUT_ADD = "timeout_add"
DECISION_TIMEOUT_ABORT = "timeout_abort"


def split_probe_volumes(planned_volume: int) -> Tuple[int, int]:
    """将规则 volume 拆为试探仓与补买仓（100 股整数）；不可拆时返回 (0, 0)。"""
    probe, remain, remain_only = plan_probe_volumes(planned_volume)
    if remain_only or probe <= 0 or remain <= 0:
        return 0, 0
    return probe, remain


def plan_probe_volumes(planned_volume: int) -> Tuple[int, int, bool]:
    """不可拆时 remain_only=True（调用方应退回真突破一次性全买）。"""
    vol = int(planned_volume // 100 * 100)
    if vol < 100:
        return 0, 0, False
    probe = int(vol * PROBE_RATIO // 100 * 100)
    remain = vol - probe
    if probe >= 100 and remain >= 100:
        return probe, remain, False
    return 0, vol, True


def can_use_probe_mode(planned_volume: int, price: float, min_buy_amount: float = 0.0) -> bool:
    """仅当可拆成试探+补买两笔（均≥1手）时启用探测；不可拆则退回真突破一次性全买。"""
    probe, remain, remain_only = plan_probe_volumes(planned_volume)
    if remain_only or probe <= 0 or remain <= 0:
        return False
    if float(price or 0) <= 0:
        return False
    if float(min_buy_amount or 0) > 0 and probe * float(price) < float(min_buy_amount):
        return False
    return True


def _tick_dt_time(tick_dt: Any) -> Optional[dt_time]:
    if tick_dt is None:
        return None
    try:
        if isinstance(tick_dt, str):
            tick_dt = datetime.fromisoformat(tick_dt.replace("Z", ""))
        if hasattr(tick_dt, "time"):
            return tick_dt.time()
    except Exception:
        pass
    return None


def is_past_rearm_add_cutoff(tick_dt: Any) -> bool:
    """是否已过当日补买/再次上穿截止时间（14:57:00）。"""
    t = _tick_dt_time(tick_dt)
    if t is None:
        return False
    return t >= REARM_ADD_CUTOFF_TIME


def make_rearm_meta(
    *,
    await_rearm: bool = False,
    probe_bought: bool = False,
    probe_filled: int = 0,
    remain_pending: int = 0,
    planned_volume: int = 0,
    trigger_price: float = 0.0,
    tb_vol_ratio: float = 0.0,
    true_breakthrough_passed: bool = False,
    rearm_failed_attempts: int = 0,
) -> Dict[str, Any]:
    return {
        "await_rearm": bool(await_rearm),
        "probe_bought": bool(probe_bought),
        "probe_filled": int(probe_filled or 0),
        "remain_pending": int(remain_pending or 0),
        "planned_volume": int(planned_volume or 0),
        "trigger_price": float(trigger_price or 0),
        "tb_vol_ratio": float(tb_vol_ratio or 0),
        "true_breakthrough_passed": bool(true_breakthrough_passed),
        "rearm_failed_attempts": int(rearm_failed_attempts or 0),
        "last_skip_reason": "",
    }


def enter_await_rearm(meta: Dict[str, Any], *, reason: str) -> None:
    meta["await_rearm"] = True
    meta["last_skip_reason"] = str(reason or "").strip()


def is_strong_tb_meta(meta: Dict[str, Any]) -> bool:
    """首轮真突破量比≥3 才视为强突破（允许一次补买重试）。"""
    if not meta.get("true_breakthrough_passed"):
        return False
    return float(meta.get("tb_vol_ratio") or 0) >= STRONG_TB_VOL_RATIO


def should_defer_remain_to_rearm(meta: Dict[str, Any], tick_dt: Any) -> bool:
    """确认窗未补买：强突破 + 失败次数未用尽 + 14:57 前 → 等待下次上穿。"""
    if is_past_rearm_add_cutoff(tick_dt):
        return False
    if int(meta.get("remain_pending") or 0) <= 0:
        return False
    if not is_strong_tb_meta(meta):
        return False
    if int(meta.get("rearm_failed_attempts") or 0) >= MAX_REARM_ADD_ATTEMPTS:
        return False
    return True


def can_start_rearm_add_confirm(meta: Dict[str, Any], tick_dt: Any) -> bool:
    """再次上穿时是否可开启补买确认窗（失败次数按「确认窗结束未补买」累计）。"""
    if not meta.get("await_rearm"):
        return False
    if int(meta.get("remain_pending") or 0) <= 0:
        return False
    if is_past_rearm_add_cutoff(tick_dt):
        return False
    if not is_strong_tb_meta(meta):
        return False
    if int(meta.get("rearm_failed_attempts") or 0) >= MAX_REARM_ADD_ATTEMPTS:
        return False
    return True


def record_rearm_confirm_failed(meta: Dict[str, Any]) -> None:
    meta["rearm_failed_attempts"] = int(meta.get("rearm_failed_attempts") or 0) + 1


def _tick_size(code: str) -> float:
    from core.utils.security_type import SecurityTypeUtil

    precision = SecurityTypeUtil.get_price_precision(code)
    return 0.001 if precision == 3 else 0.01


def _round_display(code: str, price: float) -> float:
    from strategy_generator_app.backtest.true_breakthrough import round_price_like_display

    return round_price_like_display(code, float(price or 0))


def max_add_display_price(code: str, trigger_price: float, chase_pct: float = CHASE_PCT) -> float:
    p_trig = _round_display(code, trigger_price)
    cap = float(p_trig) * (1.0 + float(chase_pct))
    return _round_display(code, cap)


def _elapsed_seconds(t0: Any, t1: Any) -> float:
    if t0 is None or t1 is None:
        return 0.0
    try:
        if isinstance(t0, str):
            t0 = datetime.fromisoformat(t0.replace("Z", ""))
        if isinstance(t1, str):
            t1 = datetime.fromisoformat(t1.replace("Z", ""))
        return max(0.0, (t1 - t0).total_seconds())
    except Exception:
        return 0.0


def _is_strong_tb(state: Dict[str, Any]) -> bool:
    if not state.get("true_breakthrough_passed"):
        return False
    return float(state.get("tb_vol_ratio") or 0) >= STRONG_TB_VOL_RATIO


def _skip_chase_abort(state: Dict[str, Any]) -> bool:
    return _is_strong_tb(state)


def init_confirm_state(
    *,
    code: str,
    trigger_price: float,
    planned_volume: int,
    probe_volume: int,
    remain_volume: int,
    break_tick_dt: Any,
    avg_vol_before: float = 0.0,
    tb_vol_ratio: float = 0.0,
    true_breakthrough_passed: bool = False,
    rearm_cross: bool = False,
) -> Dict[str, Any]:
    p_trig = _round_display(code, trigger_price)
    return {
        "active": True,
        "code": str(code or ""),
        "trigger_price": float(trigger_price or 0),
        "trigger_display": p_trig,
        "max_add_display": max_add_display_price(code, trigger_price),
        "planned_volume": int(planned_volume),
        "probe_volume": int(probe_volume),
        "remain_volume": int(remain_volume),
        "probe_filled_volume": int(probe_volume) if int(probe_volume) > 0 else 0,
        "remain_filled_volume": 0,
        "break_tick_dt": break_tick_dt,
        "window_start_dt": break_tick_dt,
        "tick_count": 0,
        "above_count": 0,
        "min_display_price": None,
        "max_display_price": None,
        "consecutive_below": 0,
        "sum_vol_after": 0.0,
        "avg_vol_before": float(avg_vol_before or 0),
        "tb_vol_ratio": float(tb_vol_ratio or 0),
        "true_breakthrough_passed": bool(true_breakthrough_passed),
        "rearm_cross": bool(rearm_cross),
        "finish_reason": "",
    }


def init_rearm_add_confirm_state(
    meta: Dict[str, Any],
    *,
    code: str,
    break_tick_dt: Any,
    avg_vol_before: float,
) -> Dict[str, Any]:
    """已持试探仓，仅对剩余量开确认窗（再次上穿触发）。"""
    return init_confirm_state(
        code=code,
        trigger_price=float(meta.get("trigger_price") or 0),
        planned_volume=int(meta.get("planned_volume") or 0),
        probe_volume=int(meta.get("probe_filled") or 0),
        remain_volume=int(meta.get("remain_pending") or 0),
        break_tick_dt=break_tick_dt,
        avg_vol_before=avg_vol_before,
        tb_vol_ratio=float(meta.get("tb_vol_ratio") or 0),
        true_breakthrough_passed=bool(meta.get("true_breakthrough_passed")),
        rearm_cross=True,
    )


def _continuation_metrics(
    state: Dict[str, Any], code: str, display_price: float
) -> Tuple[float, bool, bool, str]:
    tick_count = int(state.get("tick_count") or 0)
    above_count = int(state.get("above_count") or 0)
    above_ratio = (above_count / tick_count) if tick_count > 0 else 0.0
    p_trig = float(state.get("trigger_display") or _round_display(code, state.get("trigger_price") or 0))
    ts = _tick_size(code)
    max_p = state.get("max_display_price")
    min_p = state.get("min_display_price")

    tb_ratio = float(state.get("tb_vol_ratio") or 0)
    weak_tb = bool(state.get("true_breakthrough_passed")) and 0 < tb_ratio < WEAK_TB_VOL_RATIO

    if weak_tb:
        progress_ok = (
            max_p is not None
            and float(max_p) >= p_trig * (1.0 + WEAK_TB_MIN_PROGRESS_PCT)
        )
    else:
        progress_ok = (
            max_p is not None and float(max_p) >= p_trig + ts * MIN_PROGRESS_TICKS
        )
    need_above = WEAK_TB_ABOVE_RATIO if weak_tb else CONTINUATION_ABOVE_RATIO
    need_follow = WEAK_FOLLOW_THROUGH_RATIO if weak_tb else FOLLOW_THROUGH_RATIO
    near_high = (
        max_p is not None
        and display_price >= float(max_p) - ts * CONTINUATION_NEAR_HIGH_TICKS
    )
    still_above = display_price > p_trig
    above_ok = above_ratio >= need_above

    follow_ok = True
    avg_before = float(state.get("avg_vol_before") or 0)
    if avg_before > 1e-12:
        follow_ok = float(state.get("sum_vol_after") or 0) / (tick_count * avg_before) >= need_follow

    min_ok = min_p is None or float(min_p) >= p_trig - ts

    continuation_ok = (
        tick_count >= MIN_OBS_TICKS
        and still_above
        and progress_ok
        and near_high
        and above_ok
        and min_ok
        and follow_ok
    )

    detail = (
        f"上方{above_ratio * 100:.0f}%"
        f" 新高{float(max_p) if max_p is not None else 0:.4f}"
        f" 价{display_price:.4f}"
        f" 量比{tb_ratio:.2f}"
    )
    return above_ratio, continuation_ok, follow_ok, detail


def process_confirm_tick(
    state: Dict[str, Any],
    code: str,
    last_price: float,
    tick_dt: Any,
    tick_vol: Optional[float] = None,
) -> str:
    """确认窗口内每 tick 更新；未补买且未过 14:57 由外层转入 await_rearm。"""
    if not state.get("active"):
        return DECISION_NONE

    display_p = _round_display(code, last_price)
    p_trig = float(state.get("trigger_display") or _round_display(code, state.get("trigger_price") or 0))

    state["tick_count"] = int(state.get("tick_count") or 0) + 1
    if display_p > p_trig:
        state["above_count"] = int(state.get("above_count") or 0) + 1

    if state.get("min_display_price") is None:
        state["min_display_price"] = display_p
    else:
        state["min_display_price"] = min(float(state["min_display_price"]), display_p)

    if state.get("max_display_price") is None:
        state["max_display_price"] = display_p
    else:
        state["max_display_price"] = max(float(state["max_display_price"]), display_p)

    if display_p <= p_trig:
        state["consecutive_below"] = int(state.get("consecutive_below") or 0) + 1
    else:
        state["consecutive_below"] = 0

    if tick_vol is not None and float(tick_vol) > 0:
        state["sum_vol_after"] = float(state.get("sum_vol_after") or 0) + float(tick_vol)

    if int(state["consecutive_below"]) >= CONSECUTIVE_BELOW_ABORT:
        tag = "再次上穿" if state.get("rearm_cross") else "首轮"
        state["finish_reason"] = (
            f"{tag}假突破：连续{CONSECUTIVE_BELOW_ABORT}个tick展示价≤触发价{p_trig:.4f}，放弃本次补买"
        )
        return DECISION_FAKE

    max_add = float(state.get("max_add_display") or max_add_display_price(code, p_trig))
    if not _skip_chase_abort(state) and display_p > max_add:
        state["finish_reason"] = (
            f"追价放弃：展示价{display_p:.4f}>补买上限{max_add:.4f}"
            f"（触发价+{CHASE_PCT * 100:.2f}%），放弃本次补买"
        )
        return DECISION_CHASE

    elapsed = _elapsed_seconds(state.get("window_start_dt"), tick_dt)
    above_ratio, continuation_ok, _, cont_detail = _continuation_metrics(state, code, display_p)

    if elapsed >= MIN_OBS_SECONDS and continuation_ok:
        chase_note = "强量比不限追价" if _skip_chase_abort(state) else f"价≤上限{max_add:.4f}"
        prefix = "再次上穿延续补买" if state.get("rearm_cross") else "延续强势补买"
        state["finish_reason"] = (
            f"{prefix}：窗口{elapsed:.0f}s tick={state['tick_count']} "
            f"{cont_detail} {chase_note}"
        )
        return DECISION_ADD

    if elapsed >= CONFIRM_WINDOW_SECONDS:
        if continuation_ok:
            prefix = "再次上穿超时补买" if state.get("rearm_cross") else "超时延续补买"
            state["finish_reason"] = (
                f"{prefix}：窗口{CONFIRM_WINDOW_SECONDS:.0f}s tick={state['tick_count']} "
                f"上方占比{above_ratio * 100:.0f}%"
            )
            return DECISION_TIMEOUT_ADD
        prefix = "再次上穿" if state.get("rearm_cross") else "首轮"
        state["finish_reason"] = (
            f"{prefix}超时未补买：窗口{CONFIRM_WINDOW_SECONDS:.0f}s 未满足延续强势"
            f"（tick={state['tick_count']} 上方占比{above_ratio * 100:.0f}%）"
        )
        return DECISION_TIMEOUT_ABORT

    return DECISION_NONE


def finish_summary(state: Dict[str, Any]) -> str:
    return str(state.get("finish_reason") or "").strip()


def total_filled_volume(state: Dict[str, Any]) -> int:
    return int(state.get("probe_filled_volume") or 0) + int(state.get("remain_filled_volume") or 0)
