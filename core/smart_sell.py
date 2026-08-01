# -*- coding: utf-8 -*-
"""非提前下单卖出任务的智能卖出逻辑（实盘与回测共用）。"""
from __future__ import annotations

import math
import re
from datetime import datetime, time as dt_time
from typing import Any, Dict, List, Optional, Tuple

from core.utils.security_type import SecurityTypeUtil

PHASE_A_END = dt_time(14, 56, 30)
PHASE_B_END = dt_time(14, 57, 0)
PHASE_C_START = dt_time(14, 57, 0)

LOSS_TOL = 0.004
PEAK_MIN_ABOVE_REF = 0.002
HARVEST_ENTER_PCT = 0.0025
HARVEST_ENTER_JUMPS = 1
HARVEST_EXIT_PCT = 0.003
HARVEST_EXIT_JUMPS = 2
STRONG_THRESHOLD = 0.38
WEAK_THRESHOLD = -0.27
# 现价低于 P_ref 该比例 → 强制弱盘快卖（避免 STRONG/NEUTRAL 挂高价拖到尾盘）
BELOW_REF_WEAK_PCT = 0.004
# 低于 P_ref 且自 peak 回落 → 允许见好就收（不必先涨过 PEAK_MIN_ABOVE_REF）
BELOW_REF_HARVEST_PCT = 0.001
# 挂单长期高于市价未成交 → 转弱盘（约 60 tick / 120 tick）
STALE_QUOTE_WEAK_TICKS = 60
STALE_BELOW_REF_WEAK_TICKS = 120
CEIL_ASK_JUMPS = 3
MIN_REQUOTE_SECONDS = 3.0
MIN_AMOUNT_SINGLE_TRANCHE = 10000.0
MAX_REQUOTE_COUNT = 30
MOMENTUM_WEAK_SCALE = 0.006
# 定时清仓浮盈耐心：有利润时先挂高一点，最短等待后再允许快卖
PROFIT_PATIENCE_MIN_PCT = 0.002
PROFIT_PATIENCE_MIN_SECONDS = 120.0
PROFIT_PATIENCE_MAX_SECONDS = 600.0
PROFIT_PATIENCE_PREMIUM_TICKS = 2.0
PROFIT_PATIENCE_STRENGTH_MIN = -0.10

ELIGIBLE_RULE_TYPES = frozenset({
    "single_sell",
    "breakthrough_sell",
    "cage_sell",
    "grid_sell",
    "scheduled_clear",
})

MODE_STRONG = "STRONG"
MODE_HARVEST = "HARVEST"
MODE_WEAK = "WEAK"
MODE_NEUTRAL = "NEUTRAL"
MODE_PROFIT_WAIT = "PROFIT_WAIT"
PHASE_SMART = "SMART"
PHASE_FORCE = "FORCE"
PHASE_CLOSING = "CLOSING"

SMART_SELL_MODE_LABELS_CN = {
    MODE_STRONG: "强盘",
    MODE_HARVEST: "见好就收",
    MODE_WEAK: "弱盘",
    MODE_NEUTRAL: "中性",
    MODE_PROFIT_WAIT: "浮盈等待",
}

SMART_SELL_PHASE_LABELS_CN = {
    PHASE_SMART: "智能",
    PHASE_FORCE: "强平",
    PHASE_CLOSING: "收盘",
}


def smart_sell_mode_label_cn(mode: Any) -> str:
    key = str(mode or "").strip().upper()
    return SMART_SELL_MODE_LABELS_CN.get(key, str(mode or "").strip() or "未知")


def smart_sell_mode_from_label_cn(label: Any) -> str:
    """中文模式名 → 内部 mode 常量（无法识别时原样返回）。"""
    s = str(label or "").strip()
    for mode, cn in SMART_SELL_MODE_LABELS_CN.items():
        if s == cn:
            return mode
    return s


_SMART_SELL_DISPLAY_RE = re.compile(
    r"^(?P<rtype>[\u4e00-\u9fff]+)-智能卖出（(?P<mode>[^）]+)）$"
)
_QMT_SMART_SELL_COMPACT_RE = re.compile(
    r"^(?P<rtype>[\u4e00-\u9fff]+)-(?P<mode>强盘|中性|弱盘|见好就收|浮盈等待|强平|收盘竞价)$"
)
_QMT_SMART_SELL_TRUNCATED_RE = re.compile(r"^(?P<rtype>[\u4e00-\u9fff]+)-智能卖")


def compact_order_strategy_remark(display_name: str) -> str:
    """
    QMT strategy_name 字段较短，完整「规则-智能卖出（模式）」常被截断。
    下发紧凑格式「规则-模式」（如 定时清仓-中性），展示时再还原。
    """
    s = str(display_name or "").strip()
    if not s:
        return s
    m = _SMART_SELL_DISPLAY_RE.match(s)
    if m:
        return f"{m.group('rtype')}-{m.group('mode')}"
    return s


def restore_order_display_from_qmt_remark(text: Any) -> Optional[str]:
    """将 QMT 回报/截断的策略备注还原为完整「规则类型-交易模式」。"""
    s = str(text or "").strip()
    if not s:
        return None
    if s.endswith("-直接卖出"):
        return s
    if _SMART_SELL_DISPLAY_RE.match(s):
        return s
    m = _QMT_SMART_SELL_COMPACT_RE.match(s)
    if m:
        rtype = m.group("rtype")
        mode_cn = m.group("mode")
        rule_type = _rule_type_from_label(rtype)
        if mode_cn in ("强平", "收盘竞价"):
            return smart_sell_order_strategy_name(
                "WEAK",
                rule_type=rule_type or rtype,
                phase_tag=mode_cn,
            )
        return smart_sell_order_strategy_name(
            smart_sell_mode_from_label_cn(mode_cn),
            rule_type=rule_type or rtype,
        )
    m = _QMT_SMART_SELL_TRUNCATED_RE.match(s)
    if m:
        rtype = m.group("rtype")
        rule_type = _rule_type_from_label(rtype)
        mode_m = re.search(r"（([^）]+)）", s)
        if mode_m:
            mode_cn = mode_m.group(1)
            if mode_cn in ("强平", "收盘竞价"):
                return smart_sell_order_strategy_name(
                    "WEAK",
                    rule_type=rule_type or rtype,
                    phase_tag=mode_cn,
                )
            return smart_sell_order_strategy_name(
                smart_sell_mode_from_label_cn(mode_cn),
                rule_type=rule_type or rtype,
            )
        return f"{rtype}-智能卖出"
    return None


def smart_sell_phase_label_cn(phase: Any) -> str:
    key = str(phase or "").strip().upper()
    return SMART_SELL_PHASE_LABELS_CN.get(key, str(phase or "").strip() or "未知")


def rule_type_label_cn(rule_type: Any, *, default: str = "未知") -> str:
    rt = str(rule_type or "").strip()
    if not rt:
        return default
    try:
        from core.trading_rules import RULE_TYPE_NAMES, RuleType

        return RULE_TYPE_NAMES.get(RuleType(rt), rt)
    except ValueError:
        return rt


TRIGGER_REASON_TO_RULE_TYPE = {
    "单点买入触发": "single_buy",
    "突破买入触发": "breakthrough_buy",
    "笼子买入触发": "cage_buy",
    "网格买入触发": "grid_buy",
    "弹性买入触发": "best_buy",
    "单点卖出触发": "single_sell",
    "突破卖出触发": "breakthrough_sell",
    "笼子卖出触发": "cage_sell",
    "网格卖出触发": "grid_sell",
    "弹性卖出触发": "best_sell",
}


def _rule_type_from_trigger_reason(reason: str) -> str:
    text = str(reason or "").strip()
    if not text:
        return ""
    if text in TRIGGER_REASON_TO_RULE_TYPE:
        return TRIGGER_REASON_TO_RULE_TYPE[text]
    if text.startswith("突破买入"):
        return "breakthrough_buy"
    if text.startswith("笼子买入"):
        return "cage_buy"
    if text.startswith("网格买入"):
        return "grid_buy"
    if text.startswith("弹性买入"):
        return "best_buy"
    if text.startswith("突破卖出"):
        return "breakthrough_sell"
    if text.startswith("笼子卖出"):
        return "cage_sell"
    if text.startswith("网格卖出"):
        return "grid_sell"
    if text.startswith("弹性卖出"):
        return "best_sell"
    return ""


def buy_order_strategy_name(rule_type: Any, trade_mode: str) -> str:
    mode = str(trade_mode or "").strip() or "直接买入"
    return f"{rule_type_label_cn(rule_type, default='买入')}-{mode}"


def direct_buy_order_strategy_name(rule_type: Any) -> str:
    return buy_order_strategy_name(rule_type, "直接买入")


def resolve_buy_trade_mode(
    trade_info: Dict[str, Any],
    *,
    in_closing_auction: bool = False,
    rule_type: str = "",
) -> str:
    ti = trade_info or {}
    explicit = str(ti.get("order_trade_mode") or "").strip()
    if explicit:
        return explicit
    if ti.get("breakthrough_probe_phase") == "remain":
        return "试探补买"
    if ti.get("breakthrough_probe_phase") == "probe":
        if ti.get("true_breakthrough_passed") or ti.get("breakthrough_probe_tb_passed"):
            return "真突破试探"
        return "试探建仓"
    if ti.get("true_breakthrough_passed") is True:
        return "真突破"
    if str(ti.get("true_breakthrough_detail") or "").strip():
        return "真突破"
    reason = str(ti.get("reason") or "")
    if reason.startswith("提前下单确认-") or ti.get("early_order_confirm"):
        return "提前下单确认"
    if ti.get("early_order") or reason.startswith("提前下单-"):
        return "提前下单"
    if "真突破" in reason:
        return "真突破"
    if "夜市" in reason or rule_type in ("night_buy", "night_sell"):
        return "夜市委托"
    if in_closing_auction:
        return "收盘竞价"
    return "直接买入"


def resolve_buy_order_strategy_name(
    rule: Dict[str, Any],
    trade_info: Dict[str, Any],
    *,
    in_closing_auction: bool = False,
) -> str:
    rule = rule or {}
    trade_info = trade_info or {}
    rule_type = str(rule.get("type") or trade_info.get("rule_type") or "").strip()
    if not rule_type:
        rule_type = _rule_type_from_trigger_reason(str(trade_info.get("reason") or ""))
    mode = resolve_buy_trade_mode(
        trade_info,
        in_closing_auction=in_closing_auction,
        rule_type=rule_type,
    )
    return buy_order_strategy_name(rule_type or "single_buy", mode)


def resolve_order_strategy_name(
    rule: Dict[str, Any],
    trade_info: Dict[str, Any],
    *,
    in_closing_auction: bool = False,
    smart_sell_mode: Any = None,
    smart_sell_phase_tag: str = "",
) -> str:
    """实盘/回测下单说明：规则类型-交易模式。"""
    rule = rule or {}
    trade_info = trade_info or {}
    side = str(trade_info.get("type") or "").strip().lower()
    rule_type = str(rule.get("type") or trade_info.get("rule_type") or "").strip()

    if side == "sell":
        if smart_sell_mode is not None:
            return smart_sell_order_strategy_name(
                smart_sell_mode,
                rule_type=rule_type,
                phase_tag=smart_sell_phase_tag,
            )
        reason = str(trade_info.get("reason") or "")
        if not rule_type:
            rule_type = _rule_type_from_trigger_reason(reason)
        if "夜市" in reason or rule_type == "night_sell":
            return buy_order_strategy_name(rule_type or "night_sell", "夜市委托")
        return direct_sell_order_strategy_name(rule_type or "single_sell")

    if side == "buy":
        return resolve_buy_order_strategy_name(
            rule,
            trade_info,
            in_closing_auction=in_closing_auction,
        )

    return str(trade_info.get("reason") or "未知")


def smart_sell_trade_mode_label(mode: Any, *, phase_tag: str = "") -> str:
    """交易模式：智能卖出（弱盘/中性/强平/…）。"""
    tag = str(phase_tag or "").strip()
    if tag == "强平":
        return "智能卖出（强平）"
    if tag == "收盘竞价":
        return "智能卖出（收盘竞价）"
    return f"智能卖出（{smart_sell_mode_label_cn(mode)}）"


def direct_sell_order_strategy_name(rule_type: Any) -> str:
    """非智能卖出路径：规则类型-直接卖出。"""
    return f"{rule_type_label_cn(rule_type)}-直接卖出"


def smart_sell_order_strategy_name(
    mode: Any,
    *,
    rule_type: str = "",
    phase_tag: str = "",
) -> str:
    """交易记录说明：规则类型-交易模式（不含随意规则名）。"""
    return (
        f"{rule_type_label_cn(rule_type)}-"
        f"{smart_sell_trade_mode_label(mode, phase_tag=phase_tag)}"
    )


def format_smart_sell_mode_phase_note(
    mode: Any,
    phase: Any = None,
    *,
    rule_type: str = "",
) -> str:
    phase_tag = ""
    ph = str(phase or "").strip().upper()
    if ph == PHASE_FORCE:
        phase_tag = "强平"
    elif ph == PHASE_CLOSING:
        phase_tag = "收盘竞价"
    return smart_sell_order_strategy_name(
        mode,
        rule_type=rule_type,
        phase_tag=phase_tag,
    )


def localize_order_display_text(text: Any, *, side: str = "") -> str:
    """将历史备注尽量归一为「规则类型-交易模式」。"""
    s = str(text or "")
    if not s:
        return s

    side_key = str(side or "").strip().lower()
    if side_key in ("buy", "买入"):
        side_key = "buy"
    elif side_key in ("sell", "卖出"):
        side_key = "sell"
    else:
        side_key = ""

    for en, cn in SMART_SELL_MODE_LABELS_CN.items():
        s = s.replace(f"-{en}-", f"-{cn}-")
        s = s.replace(f"智能卖出-{en}-", f"智能卖出-{cn}-")
        s = s.replace(f"智能卖出-{en}", f"智能卖出-{cn}")
        s = s.replace(f"mode={en}", f"模式={cn}")
        s = s.replace(f"（{en}）", f"（{cn}）")
    for en, cn in SMART_SELL_PHASE_LABELS_CN.items():
        s = s.replace(f"phase={en}", f"阶段={cn}")
    s = s.replace("smart_sell ", "智能卖出 ")

    restored = restore_order_display_from_qmt_remark(s)
    if restored:
        return restored

    if s.startswith("定时清仓-") and not s.startswith(
        ("定时清仓-智能卖出", "定时清仓-直接卖出")
    ):
        return direct_sell_order_strategy_name("scheduled_clear")

    s = re.sub(
        r"^智能卖出-(?P<mode>[^-]+)-定时清仓.*$",
        lambda m: smart_sell_order_strategy_name(
            m.group("mode"), rule_type="scheduled_clear"
        ),
        s,
    )
    s = re.sub(
        r"^定时清仓（[^）]*）（(?P<mode>[^）]+)）$",
        lambda m: smart_sell_order_strategy_name(
            m.group("mode"), rule_type="scheduled_clear"
        ),
        s,
    )
    known_types = "|".join(
        (
            "定时清仓",
            "弹性卖出",
            "突破卖出",
            "单点卖出",
            "笼子卖出",
            "网格卖出",
            "弹性买入",
            "突破买入",
            "单点买入",
            "笼子买入",
            "网格买入",
            "夜市买入",
            "夜市卖出",
        )
    )
    s = re.sub(
        rf"^智能卖出-(?P<mode>[^-]+)-(?P<rtype>{known_types}).*$",
        lambda m: smart_sell_order_strategy_name(
            m.group("mode"), rule_type=_rule_type_from_label(m.group("rtype"))
        ),
        s,
    )
    s = re.sub(
        rf"^(?P<rtype>{known_types})-智能卖出-(?P<mode>[^-]+)(?:-.*)?$",
        lambda m: smart_sell_order_strategy_name(
            m.group("mode"), rule_type=_rule_type_from_label(m.group("rtype"))
        ),
        s,
    )

    if s in TRIGGER_REASON_TO_RULE_TYPE:
        rt = TRIGGER_REASON_TO_RULE_TYPE[s]
        if side_key == "sell":
            return direct_sell_order_strategy_name(rt)
        return direct_buy_order_strategy_name(rt)

    if s.startswith("突破买入补买"):
        return buy_order_strategy_name("breakthrough_buy", "试探补买")
    if "真突破" in s and "试探" in s and s.startswith("突破买入"):
        return buy_order_strategy_name("breakthrough_buy", "真突破试探")
    if s.startswith("突破买入试探仓"):
        return buy_order_strategy_name("breakthrough_buy", "试探建仓")
    if "真突破" in s and s.startswith("突破买入"):
        return buy_order_strategy_name("breakthrough_buy", "真突破")
    if s.startswith("突破买入"):
        return direct_buy_order_strategy_name("breakthrough_buy")
    if s.startswith("笼子买入"):
        return direct_buy_order_strategy_name("cage_buy")
    if s.startswith("网格买入"):
        return direct_buy_order_strategy_name("grid_buy")
    if s.startswith("弹性买入"):
        return direct_buy_order_strategy_name("best_buy")
    if s.startswith("突破卖出"):
        return direct_sell_order_strategy_name("breakthrough_sell")
    if s.startswith("笼子卖出"):
        return direct_sell_order_strategy_name("cage_sell")
    if s.startswith("网格卖出"):
        return direct_sell_order_strategy_name("grid_sell")
    if s.startswith("弹性卖出"):
        return direct_sell_order_strategy_name("best_sell")

    if s.startswith("提前下单确认-"):
        rt = _rule_type_from_trigger_reason(s) or "single_buy"
        return buy_order_strategy_name(rt, "提前下单确认")
    if s.startswith("提前下单-"):
        rt = _rule_type_from_trigger_reason(s) or "single_buy"
        return buy_order_strategy_name(rt, "提前下单")

    if s == "夜市委托":
        if side_key == "sell":
            return buy_order_strategy_name("night_sell", "夜市委托")
        return buy_order_strategy_name("night_buy", "夜市委托")

    if "-" in s and re.match(r"^[\u4e00-\u9fff]+-[\u4e00-\u9fff（）]+$", s):
        return s

    return s


def localize_smart_sell_display_text(text: Any) -> str:
    return localize_order_display_text(text)


def _rule_type_from_label(label: str) -> str:
    mapping = {
        "定时清仓": "scheduled_clear",
        "弹性卖出": "best_sell",
        "突破卖出": "breakthrough_sell",
        "单点卖出": "single_sell",
        "笼子卖出": "cage_sell",
        "网格卖出": "grid_sell",
        "弹性买入": "best_buy",
        "突破买入": "breakthrough_buy",
        "单点买入": "single_buy",
        "笼子买入": "cage_buy",
        "网格买入": "grid_buy",
        "夜市买入": "night_buy",
        "夜市卖出": "night_sell",
    }
    return mapping.get(str(label or "").strip(), "")


def is_eligible_rule_type(rule_type: str) -> bool:
    return (rule_type or "").strip() in ELIGIBLE_RULE_TYPES


def should_use_smart_sell(
    rule_type: str,
    *,
    enabled: bool,
    is_early_order: bool = False,
    already_active: bool = False,
) -> bool:
    if not enabled or is_early_order or already_active:
        return False
    return is_eligible_rule_type(rule_type)


def resolve_smart_sell_p_ref(
    rule_type: str,
    intent_price: float,
    last_price: float,
) -> float:
    """
    智能卖出参考价 P_ref。
    定时清仓的 intent['price'] 是「现价须低于才卖」的上限，不是挂单目标价；
    必须用触发时刻市价，否则 P_floor/挂单价会远高于盘口，拖到尾盘 FORCE 才成交。
    """
    rt = (rule_type or "").strip()
    lp = float(last_price or 0)
    ip = float(intent_price or 0)
    if rt == "scheduled_clear":
        return lp if lp > 0 else ip
    if ip > 0:
        return ip
    return lp


def tick_size(code: str) -> float:
    precision = SecurityTypeUtil.get_price_precision(code)
    return 0.001 if precision == 3 else 0.01


def round_price(code: str, price: float) -> float:
    return SecurityTypeUtil.round_price(code, float(price))


def _session_elapsed_seconds(session: Dict[str, Any], tick_dt: Any) -> float:
    start = session.get("session_start_dt")
    if start is None or tick_dt is None:
        return 0.0
    try:
        return max(0.0, (tick_dt - start).total_seconds())
    except Exception:
        return 0.0


def is_position_profitable(session: Dict[str, Any], last_price: float) -> bool:
    cost = float(session.get("cost_price") or 0)
    if cost <= 0 or last_price <= 0:
        return False
    return last_price > cost * (1.0 + PROFIT_PATIENCE_MIN_PCT)


def profit_patience_active(session: Dict[str, Any], last_price: float, tick_dt: Any) -> bool:
    """定时清仓且浮盈、盘口不太弱、未超过最长等待 → 耐心挂单。"""
    if (session.get("rule_type") or "").strip() != "scheduled_clear":
        return False
    if not is_position_profitable(session, last_price):
        return False
    if _session_elapsed_seconds(session, tick_dt) >= PROFIT_PATIENCE_MAX_SECONDS:
        return False
    if float(session.get("strength") or 0) <= PROFIT_PATIENCE_STRENGTH_MIN:
        return False
    return True


def profit_patience_in_min_wait(session: Dict[str, Any], tick_dt: Any) -> bool:
    return _session_elapsed_seconds(session, tick_dt) < PROFIT_PATIENCE_MIN_SECONDS


def effective_p_floor(session: Dict[str, Any], last_price: float, code: str) -> float:
    """
    市价低于 P_ref 时，p_floor 随市下调，避免挂单价被触发时锚定高价卡住。
    """
    p_ref = float(session.get("p_ref") or 0)
    p_floor = float(session.get("p_floor") or 0)
    limit_down = float(session.get("limit_down") or 0)
    if last_price <= 0 or p_floor <= 0:
        return p_floor
    if p_ref > 0 and last_price < p_ref:
        dynamic = round_price(code, last_price * (1.0 - LOSS_TOL))
        if limit_down > 0:
            dynamic = max(dynamic, limit_down)
        return min(p_floor, dynamic)
    return p_floor


def split_tranches(planned_volume: int, p_ref: float, rule_type: str = "") -> List[int]:
    vol = int(planned_volume // 100 * 100)
    if vol <= 0:
        return []
    # 定时清仓意在到点全平，不应拆档留下第二笔悬空
    if (rule_type or "").strip() == "scheduled_clear":
        return [vol]
    if vol * float(p_ref) < MIN_AMOUNT_SINGLE_TRANCHE:
        return [vol]
    t1 = (vol // 2 // 100) * 100
    if t1 < 100:
        return [vol]
    t2 = vol - t1
    if t2 < 100:
        return [vol]
    return [t1, t2]


def _tick_time(tick_dt: Any) -> Optional[dt_time]:
    if tick_dt is None:
        return None
    try:
        if hasattr(tick_dt, "time"):
            return tick_dt.time()
    except Exception:
        pass
    return None


def compute_phase_deadlines(
    rule_type: str = "",
    scheduled_clear_time: str = "",
) -> Tuple[dt_time, dt_time]:
    """
    Phase A/B 全局截止（14:56:30 / 14:57:00），与 rule_type 无关。
    scheduled_clear_time 只决定「何时开始」智能卖出，不压缩尾盘强平窗口。
    """
    del rule_type, scheduled_clear_time
    return PHASE_A_END, PHASE_B_END


def phase_from_time(tick_dt: Any, session: Optional[Dict[str, Any]] = None) -> str:
    t = _tick_time(tick_dt)
    if t is None:
        return PHASE_SMART
    a_end = PHASE_A_END
    b_end = PHASE_B_END
    if session:
        a_end = session.get("phase_a_end") or PHASE_A_END
        b_end = session.get("phase_b_end") or PHASE_B_END
    if t >= PHASE_C_START:
        return PHASE_CLOSING
    if t >= b_end or t >= a_end:
        return PHASE_FORCE
    return PHASE_SMART


def _first_price(value: Any) -> float:
    if isinstance(value, (list, tuple)) and value:
        try:
            return float(value[0] or 0)
        except Exception:
            return 0.0
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _sum_volumes(value: Any, n: int = 5) -> float:
    if not isinstance(value, (list, tuple)):
        return 0.0
    total = 0.0
    for item in value[:n]:
        try:
            total += float(item or 0)
        except Exception:
            pass
    return total


def extract_tick_fields(tick: Dict[str, Any]) -> Dict[str, float]:
    last_price = float(tick.get("lastPrice") or tick.get("last_price") or 0)
    bid1 = _first_price(tick.get("bidPrice"))
    ask1 = _first_price(tick.get("askPrice"))
    if ask1 <= 0 and bid1 > 0:
        ask1 = bid1
    if bid1 <= 0 and last_price > 0:
        bid1 = last_price
    bid_vol1 = _first_price(tick.get("bidVol") or tick.get("bidVolume"))
    ask_vol1 = _first_price(tick.get("askVol") or tick.get("askVolume"))
    bid_depth = _sum_volumes(tick.get("bidVol") or tick.get("bidVolume"))
    ask_depth = _sum_volumes(tick.get("askVol") or tick.get("askVolume"))
    return {
        "last_price": last_price,
        "bid1": bid1,
        "ask1": ask1,
        "bid_vol1": bid_vol1,
        "ask_vol1": ask_vol1,
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
    }


def compute_strength(
    code: str,
    tick: Dict[str, Any],
    price_history: List[float],
    pre_close: float = 0.0,
) -> float:
    fields = extract_tick_fields(tick)
    bid1 = fields["bid1"]
    ask1 = fields["ask1"]
    last_price = fields["last_price"]
    eps = 1e-9

    vol_ratio = fields["bid_vol1"] / max(fields["ask_vol1"], eps)
    vol_score = max(-1.0, min(1.0, (vol_ratio - 1.0) / max(vol_ratio + 1.0, eps)))

    depth_ratio = fields["bid_depth"] / max(fields["ask_depth"], eps)
    depth_score = max(-1.0, min(1.0, (depth_ratio - 1.0) / max(depth_ratio + 1.0, eps)))

    mid = (bid1 + ask1) / 2.0 if bid1 > 0 and ask1 > 0 else last_price
    if mid > 0 and bid1 > 0:
        spread_score = max(-1.0, min(1.0, 1.0 - (mid - bid1) / mid * 20.0))
    else:
        spread_score = 0.0

    momentum_score = 0.0
    if price_history:
        ref_px = price_history[0]
        if ref_px > 0 and last_price > 0:
            base = pre_close if pre_close > 0 else ref_px
            move = (last_price - ref_px) / max(base, eps)
            momentum_score = max(-1.0, min(1.0, move / MOMENTUM_WEAK_SCALE))

    return 0.35 * vol_score + 0.30 * depth_score + 0.15 * spread_score + 0.20 * momentum_score


def _pullback_from_peak(last_price: float, peak: float, ts: float) -> Tuple[float, float]:
    if peak <= 0:
        return 0.0, 0.0
    pct = (peak - last_price) / peak
    jumps = (peak - last_price) / ts if ts > 0 else 0.0
    return pct, jumps


def _bounce_from_low(last_price: float, low: float, ts: float) -> Tuple[float, float]:
    if low <= 0:
        return 0.0, 0.0
    pct = (last_price - low) / low
    jumps = (last_price - low) / ts if ts > 0 else 0.0
    return pct, jumps


def update_price_tracking(session: Dict[str, Any], last_price: float) -> None:
    if last_price <= 0:
        return
    session["peak_price"] = max(float(session.get("peak_price") or last_price), last_price)
    if session.get("in_harvest"):
        cur = float(session.get("pullback_low") or last_price)
        session["pullback_low"] = min(cur, last_price)


def decide_mode(
    session: Dict[str, Any],
    strength: float,
    last_price: float,
    code: str,
    tick_dt: Any = None,
) -> str:
    phase = session.get("phase") or PHASE_SMART
    if phase in (PHASE_FORCE, PHASE_CLOSING):
        return MODE_WEAK

    if profit_patience_active(session, last_price, tick_dt):
        session["in_harvest"] = False
        session["pullback_low"] = None
        return MODE_PROFIT_WAIT

    ts = tick_size(code)
    p_ref = float(session.get("p_ref") or 0)
    peak = float(session.get("peak_price") or last_price)
    tick_count = int(session.get("tick_count") or 0)
    last_quote = float(session.get("last_quote") or 0)

    if p_ref > 0 and last_price < p_ref * (1.0 - BELOW_REF_WEAK_PCT):
        session["in_harvest"] = False
        session["pullback_low"] = None
        return MODE_WEAK

    if p_ref > 0 and last_price < p_ref and strength <= 0.06:
        session["in_harvest"] = False
        session["pullback_low"] = None
        return MODE_WEAK

    if (
        last_quote > 0
        and last_price < last_quote - 2 * ts
        and tick_count >= STALE_QUOTE_WEAK_TICKS
    ):
        session["in_harvest"] = False
        session["pullback_low"] = None
        return MODE_WEAK

    if p_ref > 0 and last_price < p_ref and tick_count >= STALE_BELOW_REF_WEAK_TICKS:
        session["in_harvest"] = False
        session["pullback_low"] = None
        return MODE_WEAK

    if strength <= WEAK_THRESHOLD:
        session["in_harvest"] = False
        session["pullback_low"] = None
        return MODE_WEAK

    peak_above = peak >= p_ref * (1.0 + PEAK_MIN_ABOVE_REF) if p_ref > 0 else False
    below_ref = p_ref > 0 and last_price < p_ref * (1.0 - BELOW_REF_HARVEST_PCT)
    pb_pct, pb_jumps = _pullback_from_peak(last_price, peak, ts)
    if (peak_above or below_ref) and (pb_pct >= HARVEST_ENTER_PCT or pb_jumps >= HARVEST_ENTER_JUMPS):
        if not session.get("in_harvest"):
            session["in_harvest"] = True
            session["pullback_low"] = last_price
        return MODE_HARVEST

    if session.get("in_harvest"):
        low = float(session.get("pullback_low") or last_price)
        bn_pct, bn_jumps = _bounce_from_low(last_price, low, ts)
        if (bn_pct >= HARVEST_EXIT_PCT or bn_jumps >= HARVEST_EXIT_JUMPS) and strength >= STRONG_THRESHOLD:
            session["in_harvest"] = False
            session["pullback_low"] = None
            return MODE_STRONG
        return MODE_HARVEST

    if strength >= STRONG_THRESHOLD:
        return MODE_STRONG
    return MODE_NEUTRAL


def compute_p_ceil(code: str, ask1: float, limit_up: float) -> float:
    ts = tick_size(code)
    if ask1 <= 0:
        ask1 = 0.0
    ceil_px = ask1 + CEIL_ASK_JUMPS * ts if ask1 > 0 else 0.0
    if limit_up > 0:
        if ceil_px <= 0:
            ceil_px = limit_up
        else:
            ceil_px = min(ceil_px, limit_up)
    return round_price(code, ceil_px) if ceil_px > 0 else 0.0


def compute_quote(
    code: str,
    session: Dict[str, Any],
    tick: Dict[str, Any],
    mode: str,
    tick_dt: Any = None,
) -> float:
    fields = extract_tick_fields(tick)
    last_price = fields["last_price"]
    bid1 = fields["bid1"]
    ask1 = fields["ask1"]
    ts = tick_size(code)
    p_ref = float(session.get("p_ref") or last_price)
    p_floor = effective_p_floor(session, last_price, code)
    limit_up = float(session.get("limit_up") or 0)
    limit_down = float(session.get("limit_down") or 0)
    phase = session.get("phase") or PHASE_SMART
    strength = float(session.get("strength") or 0.0)
    tranche_idx = int(session.get("current_tranche_idx") or 0)
    p_ceil = compute_p_ceil(code, ask1, limit_up)

    if profit_patience_active(session, last_price, tick_dt):
        base = max(p_ref, ask1 if ask1 > 0 else last_price, last_price)
        premium = PROFIT_PATIENCE_PREMIUM_TICKS
        if profit_patience_in_min_wait(session, tick_dt):
            premium = max(premium, 3.0)
        elif strength >= STRONG_THRESHOLD:
            premium += 1.0
        quote = base + premium * ts
        if p_ceil > 0:
            quote = min(quote, p_ceil)
        return round_price(code, max(p_floor, quote))

    if phase == PHASE_FORCE:
        base = bid1 if bid1 > 0 else last_price
        quote = round_price(code, base - ts)
        if limit_down > 0 and (last_price <= limit_down or quote < limit_down):
            quote = round_price(code, limit_down)
        return max(quote, ts)

    if phase == PHASE_CLOSING:
        return round_price(code, p_ref)

    if mode == MODE_WEAK:
        base = bid1 if bid1 > 0 else last_price
        extra = ts if strength <= WEAK_THRESHOLD else 0.0
        quote = round_price(code, max(p_floor, base - ts - extra))
        if limit_down > 0 and last_price <= limit_down:
            quote = round_price(code, limit_down)
        return quote

    if mode == MODE_HARVEST:
        base = bid1 if bid1 > 0 else last_price
        return round_price(code, max(p_floor, base - ts))

    if mode == MODE_STRONG:
        if p_ref > 0 and last_price < p_ref:
            base = bid1 if bid1 > 0 else last_price
            return round_price(code, max(p_floor, base - ts))
        base = max(p_ref, bid1, last_price)
        span = max(0.70, 1.0 - STRONG_THRESHOLD)
        premium_ticks = 1.0 + 2.0 * max(0.0, (strength - STRONG_THRESHOLD) / span)
        if tranche_idx >= 1:
            premium_ticks = min(premium_ticks, 2.0)
        quote = base + premium_ticks * ts
        if p_ceil > 0:
            quote = min(quote, p_ceil)
        return round_price(code, max(p_floor, quote))

    base = max(p_ref, bid1 if bid1 > 0 else last_price)
    if p_ref > 0 and last_price < p_ref:
        base = bid1 if bid1 > 0 else last_price
        return round_price(code, max(p_floor, base - ts))
    quote = base
    if p_ceil > 0:
        quote = min(quote, p_ceil)
    return round_price(code, max(p_floor, quote))


def should_requote(
    session: Dict[str, Any],
    new_quote: float,
    tick_dt: Any,
    strength: float,
) -> bool:
    """是否应撤单重挂。last_quote 表示已挂单价，勿与 update_session_tick 的 target_quote 混淆。"""
    phase = session.get("phase") or PHASE_SMART
    if phase in (PHASE_FORCE, PHASE_CLOSING):
        return True
    old_quote = float(session.get("last_quote") or 0)
    if old_quote <= 0:
        return True
    code = session.get("code") or ""
    ts = tick_size(code)
    price_changed = abs(new_quote - old_quote) >= ts * 0.5
    last_s = session.get("last_requote_strength")
    strength_changed = last_s is not None and abs(strength - float(last_s)) >= 0.15
    if not price_changed and not strength_changed:
        return False
    if int(session.get("requote_count") or 0) >= MAX_REQUOTE_COUNT:
        return False
    last_ts = session.get("last_requote_dt")
    if last_ts is not None and tick_dt is not None:
        try:
            delta = (tick_dt - last_ts).total_seconds()
            if delta < MIN_REQUOTE_SECONDS:
                return False
        except Exception:
            pass
    return True


def init_session(
    code: str,
    p_ref: float,
    planned_volume: int,
    *,
    limit_up: float = 0.0,
    limit_down: float = 0.0,
    trigger_info: str = "",
    rule_type: str = "",
    scheduled_clear_time: str = "",
    cost_price: float = 0.0,
    session_start_dt: Any = None,
) -> Dict[str, Any]:
    p_ref = round_price(code, float(p_ref))
    p_floor = p_ref * (1.0 - LOSS_TOL)
    if limit_down > 0:
        p_floor = max(p_floor, limit_down)
    p_floor = round_price(code, p_floor)
    tranches = split_tranches(int(planned_volume), p_ref, rule_type)
    phase_a_end, phase_b_end = compute_phase_deadlines(rule_type, scheduled_clear_time)
    return {
        "active": True,
        "code": code,
        "rule_type": (rule_type or "").strip(),
        "scheduled_clear_time": (scheduled_clear_time or "").strip(),
        "phase_a_end": phase_a_end,
        "phase_b_end": phase_b_end,
        "p_ref": p_ref,
        "p_floor": p_floor,
        "limit_up": float(limit_up or 0),
        "limit_down": float(limit_down or 0),
        "tranches": tranches,
        "current_tranche_idx": 0,
        "tranche_remaining": tranches[0] if tranches else 0,
        "filled_volume": 0,
        "filled_amount": 0.0,
        "phase": PHASE_SMART,
        "mode": MODE_NEUTRAL,
        "in_harvest": False,
        "peak_price": p_ref,
        "pullback_low": None,
        "strength": 0.0,
        "last_quote": 0.0,
        "target_quote": 0.0,
        "last_requote_dt": None,
        "requote_count": 0,
        "force_attempted": False,
        "closing_attempted": False,
        "tick_count": 0,
        "price_history": [],
        "trigger_info": trigger_info,
        "fills": [],
        "cost_price": round_price(code, float(cost_price)) if float(cost_price or 0) > 0 else 0.0,
        "session_start_dt": session_start_dt,
    }


def current_tranche_volume(session: Dict[str, Any]) -> int:
    return int(session.get("tranche_remaining") or 0)


def advance_tranche(session: Dict[str, Any]) -> bool:
    idx = int(session.get("current_tranche_idx") or 0)
    tranches = session.get("tranches") or []
    if idx + 1 >= len(tranches):
        session["tranche_remaining"] = 0
        return True
    session["current_tranche_idx"] = idx + 1
    session["tranche_remaining"] = int(tranches[idx + 1])
    session["last_quote"] = 0.0
    return False


def record_fill(session: Dict[str, Any], fill_vol: int, fill_px: float) -> None:
    fill_vol = int(fill_vol)
    if fill_vol <= 0:
        return
    session["filled_volume"] = int(session.get("filled_volume") or 0) + fill_vol
    session["filled_amount"] = float(session.get("filled_amount") or 0.0) + fill_vol * float(fill_px)
    remaining = int(session.get("tranche_remaining") or 0) - fill_vol
    session["tranche_remaining"] = max(0, remaining)
    session.setdefault("fills", []).append({"volume": fill_vol, "price": float(fill_px)})
    if remaining <= 0:
        if advance_tranche(session):
            session["active"] = False


def is_session_complete(session: Dict[str, Any]) -> bool:
    if not session.get("active", False):
        return True
    if int(session.get("tranche_remaining") or 0) > 0:
        return False
    idx = int(session.get("current_tranche_idx") or 0)
    tranches = session.get("tranches") or []
    return idx >= len(tranches) - 1


def average_fill_price(session: Dict[str, Any]) -> float:
    vol = int(session.get("filled_volume") or 0)
    if vol <= 0:
        return 0.0
    return float(session.get("filled_amount") or 0.0) / vol


def update_session_tick(
    session: Dict[str, Any],
    tick: Dict[str, Any],
    tick_dt: Any,
    *,
    pre_close: float = 0.0,
) -> Tuple[str, float, str]:
    code = session.get("code") or ""
    fields = extract_tick_fields(tick)
    last_price = fields["last_price"]

    session["phase"] = phase_from_time(tick_dt, session)
    if session.get("session_start_dt") is None and tick_dt is not None:
        session["session_start_dt"] = tick_dt
    session["tick_count"] = int(session.get("tick_count") or 0) + 1
    hist: List[float] = session.setdefault("price_history", [])
    hist.append(last_price)
    if len(hist) > 30:
        session["price_history"] = hist[-30:]
    ref_hist = session["price_history"]
    momentum_ref = ref_hist[0] if len(ref_hist) >= 6 else (ref_hist[0] if ref_hist else last_price)

    update_price_tracking(session, last_price)
    strength = compute_strength(code, tick, [momentum_ref], pre_close=pre_close)
    session["strength"] = strength
    mode = decide_mode(session, strength, last_price, code, tick_dt)
    session["mode"] = mode
    quote = compute_quote(code, session, tick, mode, tick_dt)
    session["target_quote"] = quote
    return session["phase"], quote, mode


def process_backtest_smart_sell_tick(
    session: Dict[str, Any],
    tick: Dict[str, Any],
    tick_dt: Any,
    *,
    pre_close: float = 0.0,
) -> Optional[Tuple[int, float]]:
    """回测执行层：与实盘相同的改价门闩，仅在已挂单后尝试撮合。"""
    phase, quote, mode = update_session_tick(session, tick, tick_dt, pre_close=pre_close)
    vol = current_tranche_volume(session)
    if vol <= 0:
        return None

    code = session.get("code") or ""
    fields = extract_tick_fields(tick)
    last_price = fields["last_price"]
    best_bid = fields["bid1"]
    best_ask = fields["ask1"]
    strength = float(session.get("strength") or 0)

    if phase == PHASE_FORCE:
        if not session.get("force_attempted"):
            session["phase"] = PHASE_FORCE
            fq = compute_quote(code, session, tick, MODE_WEAK, tick_dt)
            session["force_attempted"] = True
            session["last_quote"] = fq
            session["last_requote_dt"] = tick_dt
        return try_backtest_fill(code, session, last_price, best_bid, best_ask)

    if phase == PHASE_CLOSING:
        if not session.get("closing_attempted"):
            session["closing_attempted"] = True
            session["last_quote"] = quote
            session["last_requote_dt"] = tick_dt
        return try_backtest_fill(code, session, last_price, best_bid, best_ask)

    if should_requote(session, quote, tick_dt, strength):
        session["last_quote"] = quote
        session["last_requote_dt"] = tick_dt
        session["last_requote_strength"] = strength
        session["requote_count"] = int(session.get("requote_count") or 0) + 1

    if float(session.get("last_quote") or 0) <= 0:
        return None
    return try_backtest_fill(code, session, last_price, best_bid, best_ask)


def try_backtest_fill(
    code: str,
    session: Dict[str, Any],
    last_price: float,
    best_bid: float,
    best_ask: float,
) -> Optional[Tuple[int, float]]:
    """回测：尝试成交当前档剩余量。返回 (volume, price) 或 None。"""
    vol = current_tranche_volume(session)
    if vol <= 0:
        return None
    phase = session.get("phase") or PHASE_SMART
    quote = float(session.get("last_quote") or 0)
    precision = SecurityTypeUtil.get_price_precision(code)
    slippage = tick_size(code)

    if phase == PHASE_FORCE:
        base = best_bid if best_bid > 0 else last_price
        fill_px = round_price(code, base - slippage)
        limit_down = float(session.get("limit_down") or 0)
        if limit_down > 0 and (last_price <= limit_down or fill_px < limit_down):
            fill_px = round_price(code, limit_down)
        return vol, fill_px

    if phase == PHASE_CLOSING:
        fill_px = round_price(code, float(session.get("p_ref") or last_price))
        return vol, fill_px

    if quote <= 0:
        return None
    if last_price + 1e-9 >= quote:
        fill_px = round(quote, precision)
        return vol, fill_px
    return None


def tick_row_to_dict(row: Any) -> Dict[str, Any]:
    if isinstance(row, dict):
        return row
    data: Dict[str, Any] = {}
    for key in ("lastPrice", "last_price", "bidPrice", "askPrice", "bidVol", "askVol", "bidVolume", "askVolume"):
        try:
            if hasattr(row, "get"):
                val = row.get(key)
            else:
                val = row[key]
        except Exception:
            val = None
        if val is not None:
            data[key] = val
    return data
