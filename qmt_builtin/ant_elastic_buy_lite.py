#coding:gbk
"""弹性买入反弹目标价（QMT 内嵌轻量版，兼容 Py3.6+）。"""


def compute_best_buy_rebound(lowest, trigger_price, rise_percent, rise_scale=0.35, max_rise_percent=4.0, dynamic_thresholds=1):
    """返回 (eff_rise_percent, target_price)。"""
    try:
        lowest = float(lowest or 0)
    except (TypeError, ValueError):
        lowest = 0.0
    if lowest <= 0:
        return float(rise_percent or 0), 0.0
    try:
        rise_nom = float(rise_percent or 0)
    except (TypeError, ValueError):
        rise_nom = 0.0
    try:
        dyn = int(dynamic_thresholds if dynamic_thresholds is not None else 1)
    except (TypeError, ValueError):
        dyn = 1
    if dyn <= 0:
        return rise_nom, lowest * (1.0 + rise_nom / 100.0)
    try:
        trig = float(trigger_price or 0)
    except (TypeError, ValueError):
        trig = 0.0
    try:
        drop_from_trigger_pct = max(0.0, (trig / lowest - 1.0) * 100.0) if trig and lowest else 0.0
    except Exception:
        drop_from_trigger_pct = 0.0
    try:
        scale = float(rise_scale if rise_scale is not None else 0.35)
    except (TypeError, ValueError):
        scale = 0.35
    try:
        max_rise = float(max_rise_percent if max_rise_percent is not None else 4.0)
    except (TypeError, ValueError):
        max_rise = 4.0
    eff = min(max_rise, rise_nom + drop_from_trigger_pct * scale)
    return eff, lowest * (1.0 + eff / 100.0)


def compute_best_buy_rebound_from_rule(lowest, rule, cfg_dyn=None):
    """从 rule / 武装任务字典计算反弹目标价。"""
    rule = rule or {}
    if cfg_dyn is None:
        dyn = rule.get("dynamic_thresholds")
        if dyn is None:
            dyn = 1
    else:
        dyn = cfg_dyn
    return compute_best_buy_rebound(
        lowest,
        rule.get("trigger_price"),
        rule.get("rise_percent") or 0.3,
        rise_scale=rule.get("rise_scale"),
        max_rise_percent=rule.get("max_rise_percent"),
        dynamic_thresholds=dyn,
    )
