# -*- coding: utf-8 -*-
"""市场行情日度描绘：阈值规则 → 组合标签。

价格序列仅用中证全指 000985；禁止上证综指。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES_PATH = ROOT / "config" / "market_regime_rules.json"


def load_rules(path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(path) if path else DEFAULT_RULES_PATH
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("bad rules json")
    if str(raw.get("csi_code") or "") in ("000001.SH", "sh000001", "000001"):
        raise ValueError("rules 禁止使用上证综指")
    return raw


def _f(row: pd.Series, key: str) -> Optional[float]:
    v = row.get(key)
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except Exception:
        return None


def classify_backdrop(row: pd.Series, rules: Dict[str, Any]) -> str:
    """单日均线底色（backdrop_mode=ma 时用）。默认已改为指数高低点突破。"""
    labs = (rules.get("labels") or {}).get("backdrop") or {}
    c = _f(row, "csi_close")
    m5 = _f(row, "csi_close_ma5")
    m10 = _f(row, "csi_close_ma10")
    if c is None or m5 is None or m10 is None:
        return str(labs.get("range") or "震荡底色")
    if c > m10 and m5 > m10:
        return str(labs.get("bull") or "多头趋势底色")
    if c < m10 and m5 < m10:
        return str(labs.get("bear") or "空头趋势底色")
    return str(labs.get("range") or "震荡底色")


def classify_backdrop_breakout(closes: pd.Series, rules: Dict[str, Any]) -> list:
    """按中证全指收盘价的高低点突破划分底色（因子分组用）。

    - 收盘创近 entry_n 日新高 → 多头趋势底色
    - 收盘创近 entry_n 日新低 → 空头趋势底色
    - 多头中跌破近 exit_n 日低 / 空头中升破近 exit_n 日高 → 震荡
    - 趋势刚切换后的前 fail_bars 日内，自极值反向超过 invalidate_pct → 假突破回震荡
      （同向续创新高/新低不重置该计时，避免下跌中继被反抽打断）
    """
    labs = (rules.get("labels") or {}).get("backdrop") or {}
    bull = str(labs.get("bull") or "多头趋势底色")
    bear = str(labs.get("bear") or "空头趋势底色")
    range_ = str(labs.get("range") or "震荡底色")
    entry_n = int(rules.get("backdrop_entry_n") or 15)
    exit_n = int(rules.get("backdrop_exit_n") or 8)
    inv = float(rules.get("backdrop_invalidate_pct") or 0.0)
    fail_bars = int(rules.get("backdrop_fail_bars") or 0)
    if entry_n < 2 or exit_n < 1:
        raise ValueError("backdrop_entry_n / backdrop_exit_n 无效")

    n = len(closes)
    out: list = []
    state = range_
    extreme: Optional[float] = None
    age = 0
    for i in range(n):
        if i < max(entry_n, exit_n):
            out.append(range_)
            continue
        c = float(closes.iloc[i])
        if pd.isna(c):
            out.append(state)
            continue
        window_entry = closes.iloc[i - entry_n : i]
        window_exit = closes.iloc[i - exit_n : i]
        entry_high = float(window_entry.max())
        entry_low = float(window_entry.min())
        exit_high = float(window_exit.max())
        exit_low = float(window_exit.min())

        if state in (bull, bear):
            age += 1
            if state == bull:
                if extreme is None or c > extreme:
                    extreme = c
                if c < exit_low:
                    state = range_
                    extreme = None
                    age = 0
                elif (
                    inv > 0
                    and fail_bars > 0
                    and age <= fail_bars
                    and extreme is not None
                    and c < extreme * (1.0 - inv)
                ):
                    state = range_
                    extreme = None
                    age = 0
            else:
                if extreme is None or c < extreme:
                    extreme = c
                if c > exit_high:
                    state = range_
                    extreme = None
                    age = 0
                elif (
                    inv > 0
                    and fail_bars > 0
                    and age <= fail_bars
                    and extreme is not None
                    and c > extreme * (1.0 + inv)
                ):
                    state = range_
                    extreme = None
                    age = 0

        if c > entry_high:
            if state != bull:
                state = bull
                extreme = c
                age = 1
            else:
                if extreme is None or c > extreme:
                    extreme = c
        elif c < entry_low:
            if state != bear:
                state = bear
                extreme = c
                age = 1
            else:
                if extreme is None or c < extreme:
                    extreme = c

        out.append(state)
    return out


def smooth_label_runs(labels: list, min_run: int) -> list:
    """抹掉短于 min_run 的色段：并入前一段（首段并入后一段），迭代至稳定。"""
    if min_run <= 1 or not labels:
        return list(labels)
    out = list(labels)
    while True:
        runs = []
        i = 0
        n = len(out)
        while i < n:
            j = i + 1
            while j < n and out[j] == out[i]:
                j += 1
            runs.append((out[i], i, j))
            i = j
        short = [(k, lab, a, b) for k, (lab, a, b) in enumerate(runs) if (b - a) < min_run]
        if not short:
            break
        k, _lab, a, b = short[0]
        if k > 0:
            fill = runs[k - 1][0]
        elif k + 1 < len(runs):
            fill = runs[k + 1][0]
        else:
            break
        for t in range(a, b):
            out[t] = fill
    return out


def lead_label_runs(labels: list, lead_days: int) -> list:
    """整体提前 lead_days 日：out[i] = labels[i+lead]，用于抵消均线滞后。

    历史批处理可用；近端不足 lead 的交易日保持原值。
    """
    if lead_days <= 0 or not labels:
        return list(labels)
    n = len(labels)
    out = list(labels)
    for i in range(n):
        j = i + lead_days
        if j < n:
            out[i] = labels[j]
    return out


def classify_sentiment(row: pd.Series, rules: Dict[str, Any]) -> str:
    """辅助列：ADR+TRIN 相对 ma5；并入脉冲后不再进入 label_zh。"""
    labs = (rules.get("labels") or {}).get("sentiment") or {}
    adr = _f(row, "ADR")
    adr_m5 = _f(row, "ADR_ma5")
    trin = _f(row, "TRIN")
    trin_m5 = _f(row, "TRIN_ma5")
    if None in (adr, adr_m5, trin, trin_m5):
        return str(labs.get("neutral") or "短期情绪中性")
    if adr > adr_m5 and trin < trin_m5:
        return str(labs.get("strong") or "短期情绪转强")
    if adr < adr_m5 and trin > trin_m5:
        return str(labs.get("weak") or "短期情绪转弱")
    return str(labs.get("neutral") or "短期情绪中性")


def _classify_pulse_base(row: pd.Series, rules: Dict[str, Any]) -> str:
    labs = (rules.get("labels") or {}).get("pulse") or {}
    ret = _f(row, "csi_ret_1d")
    adr = _f(row, "ADR")
    adr_m5 = _f(row, "ADR_ma5")
    pulse_ret = float(rules.get("pulse_ret_abs") or 0.008)
    pulse_adr = float(rules.get("pulse_adr") or 1.5)
    flat_ret = float(rules.get("flat_ret_abs") or 0.003)
    if ret is None:
        return str(labs.get("mixed") or "分化波动")
    if abs(ret) < flat_ret:
        return str(labs.get("none") or "无脉冲")
    if (
        ret > pulse_ret
        and adr is not None
        and adr_m5 is not None
        and adr >= pulse_adr
        and adr > adr_m5
    ):
        return str(labs.get("broad_up") or "普涨脉冲")
    if (
        ret < -pulse_ret
        and adr is not None
        and adr_m5 is not None
        and adr <= (1.0 / pulse_adr if pulse_adr else 0.67)
        and adr < adr_m5
    ):
        return str(labs.get("broad_down") or "杀跌脉冲")
    return str(labs.get("mixed") or "分化波动")


def classify_pulse(
    row: pd.Series,
    rules: Dict[str, Any],
    sentiment: Optional[str] = None,
) -> str:
    """脉冲形态；可选把短期情绪并入：升级分化 / 冲突则压回分化。"""
    labs = (rules.get("labels") or {}).get("pulse") or {}
    sent_labs = (rules.get("labels") or {}).get("sentiment") or {}
    base = _classify_pulse_base(row, rules)
    if not bool(rules.get("pulse_use_sentiment", True)):
        return base

    up = str(labs.get("broad_up") or "普涨脉冲")
    down = str(labs.get("broad_down") or "杀跌脉冲")
    mixed = str(labs.get("mixed") or "分化波动")
    none = str(labs.get("none") or "无脉冲")
    strong = str(sent_labs.get("strong") or "短期情绪转强")
    weak = str(sent_labs.get("weak") or "短期情绪转弱")

    if base == none:
        return none

    s = sentiment if sentiment is not None else classify_sentiment(row, rules)
    ret = _f(row, "csi_ret_1d")

    # 脉冲与情绪方向打架 → 压回分化
    if base == up and s == weak:
        return mixed
    if base == down and s == strong:
        return mixed

    # 分化 + 情绪同向确认 → 升为真脉冲
    if base == mixed and ret is not None:
        if s == strong and ret > 0:
            return up
        if s == weak and ret < 0:
            return down

    return base


def classify_divergence(df: pd.DataFrame, i: int, rules: Dict[str, Any]) -> str:
    """原始背离定义：回看窗软极值 + 宽度未同步确认（ADR/UDV 任一即可）。"""
    labs = (rules.get("labels") or {}).get("divergence") or {}
    n = int(rules.get("divergence_lookback") or 10)
    if i < 1:
        return str(labs.get("none") or "无背离")
    start = max(0, i - n + 1)
    window = df.iloc[start : i + 1]
    if window.empty or "csi_close" not in window.columns:
        return str(labs.get("none") or "无背离")
    closes = window["csi_close"]
    if closes.isna().all():
        return str(labs.get("none") or "无背离")
    cur_c = closes.iloc[-1]
    max_c = closes.max()
    min_c = closes.min()
    adr_m5 = window["ADR_ma5"] if "ADR_ma5" in window.columns else None
    udv_m5 = window["UDV_ma5"] if "UDV_ma5" in window.columns else None

    def _soft_new_high() -> bool:
        return cur_c >= max_c * 0.998

    def _soft_new_low() -> bool:
        return cur_c <= min_c * 1.002

    breadth_confirm_high = True
    breadth_confirm_low = True
    if adr_m5 is not None and not adr_m5.isna().all():
        if adr_m5.iloc[-1] < adr_m5.max() * 0.98:
            breadth_confirm_high = False
        if adr_m5.iloc[-1] > adr_m5.min() * 1.02:
            breadth_confirm_low = False
    if udv_m5 is not None and not udv_m5.isna().all():
        if udv_m5.iloc[-1] < udv_m5.max() * 0.98:
            breadth_confirm_high = False
        if udv_m5.iloc[-1] > udv_m5.min() * 1.02:
            breadth_confirm_low = False

    if _soft_new_high() and not breadth_confirm_high:
        return str(labs.get("top") or "顶背离警告")
    if _soft_new_low() and not breadth_confirm_low:
        return str(labs.get("bottom") or "底背离提示")
    return str(labs.get("none") or "无背离")


def classify_backdrop_manual(trade_dates: pd.Series, rules: Dict[str, Any]) -> list:
    """按人工给定的日期区间划分底色（因子分组用）。"""
    labs = (rules.get("labels") or {}).get("backdrop") or {}
    default = str(labs.get("range") or "震荡底色")
    ranges = rules.get("backdrop_manual_ranges") or []
    if not isinstance(ranges, list) or not ranges:
        raise ValueError("backdrop_mode=manual 时需要 backdrop_manual_ranges")

    parsed = []
    for item in ranges:
        if not isinstance(item, dict):
            continue
        a = str(item.get("from") or "")[:10]
        b = str(item.get("to") or "")[:10]
        lab = str(item.get("label") or default)
        if len(a) == 10 and len(b) == 10:
            parsed.append((a, b, lab))
    if not parsed:
        raise ValueError("backdrop_manual_ranges 为空或格式错误")

    out = []
    for d in trade_dates.astype(str).str[:10].tolist():
        lab = default
        for a, b, name in parsed:
            if a <= d <= b:
                lab = name
                break
        out.append(lab)
    return out


def format_label_zh(backdrop: str, pulse: str, divergence: str) -> str:
    return "(%s，%s) +【%s】" % (backdrop, pulse, divergence)


def apply_regime_labels(df: pd.DataFrame, rules: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
    rules = rules or load_rules()
    if str(rules.get("csi_code") or "") != "000985.SH":
        # 允许缺省补全，但仍拒绝 000001
        if "000001" in str(rules.get("csi_code") or ""):
            raise ValueError("禁止使用上证综指")
        rules = dict(rules)
        rules["csi_code"] = "000985.SH"

    out = df.copy()
    mode = str(rules.get("backdrop_mode") or "swing_breakout")
    if mode == "manual":
        date_col = "trade_date" if "trade_date" in out.columns else None
        if date_col is None:
            raise ValueError("manual 底色需要 trade_date 列")
        backdrops = classify_backdrop_manual(out[date_col], rules)
    elif mode == "swing_breakout" and "csi_close" in out.columns:
        backdrops = classify_backdrop_breakout(out["csi_close"], rules)
    else:
        backdrops = [classify_backdrop(out.iloc[i], rules) for i in range(len(out))]

    sentiments = []
    pulses = []
    divs = []
    for i in range(len(out)):
        row = out.iloc[i]
        s = classify_sentiment(row, rules)
        sentiments.append(s)
        pulses.append(classify_pulse(row, rules, sentiment=s))
        divs.append(classify_divergence(out, i, rules))
    # 人工底色不做平滑/平移，避免改写给定区间
    if mode != "manual":
        min_run = int(rules.get("backdrop_min_run") or 1)
        backdrops = smooth_label_runs(backdrops, min_run)
        lead = int(rules.get("backdrop_lead_days") or 0)
        backdrops = lead_label_runs(backdrops, lead)
    labels = [format_label_zh(b, p, d) for b, p, d in zip(backdrops, pulses, divs)]
    out["backdrop"] = backdrops
    out["sentiment"] = sentiments  # 诊断用，不进 label_zh
    out["pulse"] = pulses
    out["divergence"] = divs
    out["label_zh"] = labels
    out["rule_version"] = str(rules.get("rule_version") or "")
    return out
