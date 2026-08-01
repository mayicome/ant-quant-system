# -*- coding: utf-8 -*-
"""
封单结构评级 v2（持仓次日处置提示，非买入信号）。

三档：强 / 中 / 弱
  强：极度稳定 且 金额≥0.5亿 且 (硬度≥50% 或 金额≥1.5亿 或 抢筹≥2%)
  中：极度稳定且金额≥0.3；或 整体平稳且金额≥0.3；或 金额≥1.0（未进强）
  弱：其余

趋势不参与加分。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

RATING_V2_LABELS = {
    3: "强",
    2: "中",
    1: "弱",
}

STAB_RANK = {
    "封单极度稳定": 3,
    "封单整体平稳": 2,
    "封单剧烈波动": 1,
}


def normalize_stability(v: Any) -> str:
    s = str(v or "").strip()
    if not s or s.lower() in ("nan", "none"):
        return ""
    for k in STAB_RANK:
        if k in s or s in k:
            return k
    return s


def parse_pct(v: Any) -> Optional[float]:
    """解析百分数。数值按「已经是百分数」理解（50 表示 50%）；字符串可带 %。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        if x != x:
            return None
        return x
    s = str(v).strip().replace("%", "").replace(",", "")
    if not s or s.lower() in ("nan", "none", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_amt_yi(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        s = str(v).strip().replace(",", "")
        try:
            x = float(s)
        except ValueError:
            return None
    if x != x:
        return None
    return float(x)


def rate_seal_v2(
    *,
    stability: Any,
    close_amt_yi: Any,
    hardness_pct: Any = None,
    rush_pct: Any = None,
    trend: Any = None,
) -> Dict[str, Any]:
    stab = normalize_stability(stability)
    stab_n = STAB_RANK.get(stab, 0)
    amt = parse_amt_yi(close_amt_yi)
    hard = parse_pct(hardness_pct)
    rush = parse_pct(rush_pct)
    amt_f = float(amt) if amt is not None else 0.0
    hard_f = float(hard) if hard is not None else 0.0
    rush_f = float(rush) if rush is not None else 0.0
    trend_s = str(trend or "").strip()

    reason_parts = []
    if stab_n >= 3 and amt_f >= 0.5 and (hard_f >= 50.0 or amt_f >= 1.5 or rush_f >= 2.0):
        score = 3
        reason_parts.append("极稳+金额≥0.5+强度增强(硬/额/抢)")
    elif (stab_n >= 3 and amt_f >= 0.3) or (stab_n >= 2 and amt_f >= 0.3) or amt_f >= 1.0:
        score = 2
        reason_parts.append("极稳/平稳放量或单纯大额")
    else:
        score = 1
        if stab_n <= 1:
            reason_parts.append("剧烈波动/欠稳定")
        elif amt_f < 0.3:
            reason_parts.append("封单金额偏小")
        else:
            reason_parts.append("未达稳/额门槛")

    note = ""
    if score >= 2 and "持续减弱" in trend_s and stab_n >= 3:
        note = "极稳下封单回落属常见锁板形态"
    elif "持续加强" in trend_s and stab_n <= 1:
        note = "加强但剧波，偏博弈非锁板"

    label = RATING_V2_LABELS[score]
    return {
        "rating_v2": label,
        "rating_v2_score": score,
        "rating_v2_reason": "+".join(reason_parts) if reason_parts else "",
        "rating_v2_note": note,
        "stability_norm": stab,
        "amt_yi": amt_f,
        "hardness_pct": hard_f,
        "rush_pct": rush_f,
    }


def rate_row_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    return rate_seal_v2(
        stability=row.get("order_stability") or row.get("封单稳定性") or row.get("stability"),
        close_amt_yi=row.get("_close_amt_yi")
        if row.get("_close_amt_yi") is not None
        else row.get("close_order_amount_yi") or row.get("收盘封单金额(亿)") or row.get("amt"),
        hardness_pct=row.get("seal_hardness") or row.get("封板硬度") or row.get("hardness"),
        rush_pct=row.get("rush_intensity") or row.get("抢筹烈度") or row.get("rush"),
        trend=row.get("order_trend") or row.get("封单运行趋势") or row.get("trend"),
    )
