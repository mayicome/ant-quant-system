#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
滚动窗口统计（支持结构与次日字段解耦）：

默认模式（推荐）：
- 从 history_data 读取「封单结构_YYYYMMDD.xlsx」（不含「含次日」）
- 再与「次日字段通用_*.xlsx」按 (seal_date, code) 合并

兼容模式：
- 若找不到通用次日字段，可回退读取「封单结构_含次日_*.xlsx」中的次日列

输出 Spearman、高低评级对比与 verdict。
"""

from __future__ import annotations

import argparse
import itertools
import os
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd

# 与 tools/evaluate_seal_structure.py 一致
RATING_ORDER = {
    "🔴 虚封高危": 1,
    "🟠 弱势封板": 2,
    "🟡 中等封板": 3,
    "🟢 强势封板": 4,
    "🔥 超强极致封板": 5,
}


def repo_root() -> str:
    return _ROOT


def history_dir() -> str:
    return os.path.join(repo_root(), "history_data")


def find_column(df: pd.DataFrame, aliases: List[str]) -> Optional[str]:
    col_map = {str(c).strip().lower(): c for c in df.columns}
    for a in aliases:
        k = a.strip().lower()
        if k in col_map:
            return col_map[k]
    return None


def normalize_code(v: object) -> str:
    s = str(v or "").strip().upper()
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else s.zfill(6)[:6]


def list_seal_files(scan_dir: str, start: str = "", end: str = "") -> List[Tuple[str, str]]:
    """返回 [(封板日 YYYYMMDD, 绝对路径)]，默认扫描原始封单结构文件。"""
    out: List[Tuple[str, str]] = []
    if not os.path.isdir(scan_dir):
        return out
    for name in os.listdir(scan_dir):
        if not name.lower().endswith(".xlsx"):
            continue
        if "封单结构" not in name:
            continue
        if ("含次日" in name) or ("滚动检验" in name) or ("封单结构评估" in name) or ("参数寻优" in name):
            continue
        m = re.search(r"(\d{8})", name)
        if not m:
            continue
        d = m.group(1)
        if start and d < start:
            continue
        if end and d > end:
            continue
        out.append((d, os.path.join(scan_dir, name)))
    out.sort(key=lambda x: x[0])
    return out


def list_enriched_files(scan_dir: str, start: str = "", end: str = "") -> List[Tuple[str, str]]:
    """兼容旧模式：扫描封单结构_含次日_*.xlsx。"""
    out: List[Tuple[str, str]] = []
    if not os.path.isdir(scan_dir):
        return out
    for name in os.listdir(scan_dir):
        if not name.lower().endswith(".xlsx"):
            continue
        if "封单结构" not in name or "含次日" not in name:
            continue
        m = re.search(r"(\d{8})", name)
        if not m:
            continue
        d = m.group(1)
        if start and d < start:
            continue
        if end and d > end:
            continue
        out.append((d, os.path.join(scan_dir, name)))
    out.sort(key=lambda x: x[0])
    return out


def newest_nextday_universe(scan_dir: str) -> Optional[str]:
    best: Tuple[float, str] = (0.0, "")
    if not os.path.isdir(scan_dir):
        return None
    for name in os.listdir(scan_dir):
        if not name.lower().endswith(".xlsx"):
            continue
        if "次日字段通用_" not in name:
            continue
        fp = os.path.join(scan_dir, name)
        try:
            mt = os.path.getmtime(fp)
        except OSError:
            continue
        if mt > best[0]:
            best = (mt, fp)
    return best[1] if best[1] else None


def load_nextday_universe(path: str) -> pd.DataFrame:
    df = pd.read_excel(path)
    d_col = find_column(df, ["seal_date", "封板日"])
    code_col = find_column(df, ["code", "代码", "股票代码"])
    ret_col = find_column(df, ["next_day_ret", "次日收盘涨幅%", "次日收盘涨幅"])
    lim_col = find_column(df, ["next_day_limit_up", "次日是否涨停"])
    if d_col is None or code_col is None or ret_col is None:
        raise ValueError("通用次日字段缺少必要列（seal_date/code/next_day_ret）")
    out = pd.DataFrame()
    out["file_date"] = df[d_col].astype(str).str.replace(r"\D", "", regex=True).str[:8]
    out["code"] = df[code_col].map(normalize_code)
    out["next_day_ret"] = pd.to_numeric(df[ret_col], errors="coerce")
    if lim_col is not None:
        s = df[lim_col].astype(str).str.strip()
        out["next_day_limit_up"] = s.isin(("是", "Y", "y", "true", "True", "1"))
    else:
        out["next_day_limit_up"] = pd.NA
    out = out[(out["file_date"].str.len() == 8) & (out["code"] != "")]
    out = out.dropna(subset=["next_day_ret"])
    out = out.drop_duplicates(subset=["file_date", "code"], keep="first")
    return out.reset_index(drop=True)


def _prepare_seal_frame(df: pd.DataFrame, file_date: str) -> pd.DataFrame:
    """封单结构单日文件 -> 标准列（不含次日字段）。"""
    code_col = find_column(df, ["股票代码", "代码", "code"])
    rating_col = find_column(df, ["封单评级", "order_rating"])
    score_col = find_column(df, ["评级分值", "rating_score"])
    amt_col = find_column(df, ["收盘封单金额(亿)", "close_order_amount_yi"])
    stability_col = find_column(df, ["封单稳定性", "order_stability"])
    trend_col = find_column(df, ["封单运行趋势", "order_trend"])
    conf_col = find_column(df, ["置信度", "置信度标签", "confidence_tag"])
    if code_col is None or rating_col is None:
        raise ValueError("缺少列：股票代码/封单评级")

    out = pd.DataFrame()
    out["code"] = df[code_col].map(normalize_code)
    out["seal_rating"] = df[rating_col].astype(str).str.strip()
    if score_col is not None:
        out["rating_score"] = pd.to_numeric(df[score_col], errors="coerce")
    else:
        out["rating_score"] = out["seal_rating"].map(RATING_ORDER)
    out["close_order_amount_yi"] = (
        pd.to_numeric(df[amt_col], errors="coerce")
        if amt_col is not None
        else pd.Series([float("nan")] * len(df))
    )
    out["order_stability"] = (
        df[stability_col].astype(str).str.strip()
        if stability_col is not None
        else ""
    )
    out["order_trend"] = (
        df[trend_col].astype(str).str.strip()
        if trend_col is not None
        else ""
    )
    out["confidence_tag"] = (
        df[conf_col].astype(str).str.strip()
        if conf_col is not None
        else ""
    )

    out["file_date"] = file_date
    out = out[(out["code"] != "") & (out["seal_rating"] != "")]
    out = out.dropna(subset=["rating_score"])
    out["rating_score"] = out["rating_score"].astype(int)
    out["has_process_features"] = (out["order_stability"] != "") & (out["order_trend"] != "")
    return out


def _prepare_enriched_fallback_frame(df: pd.DataFrame, file_date: str) -> pd.DataFrame:
    """兼容旧「含次日」文件：直接从同表读取次日列。"""
    x = _prepare_seal_frame(df, file_date)
    ret_col = find_column(df, ["次日收盘涨幅%", "次日收盘涨幅"])
    lim_col = find_column(df, ["次日是否涨停"])
    if ret_col is None:
        raise ValueError("缺少次日收盘涨幅列")
    x["next_day_ret"] = pd.to_numeric(df[ret_col], errors="coerce")
    if lim_col is not None:
        s = df[lim_col].astype(str).str.strip()
        x["next_day_limit_up"] = s.isin(("是", "Y", "y", "true", "True", "1"))
    else:
        x["next_day_limit_up"] = pd.NA
    x = x.dropna(subset=["next_day_ret"])
    return x


def load_all_pooled(paths: List[Tuple[str, str]], nextday_df: Optional[pd.DataFrame]) -> pd.DataFrame:
    parts: List[pd.DataFrame] = []
    for file_date, fp in paths:
        try:
            raw = pd.read_excel(fp)
            p = _prepare_seal_frame(raw, file_date)
            p["day_limitup_count"] = int(len(p))
            if nextday_df is not None:
                p = p.merge(nextday_df, on=["file_date", "code"], how="left")
                p = p.dropna(subset=["next_day_ret"])
            else:
                p = _prepare_enriched_fallback_frame(raw, file_date)
                p["day_limitup_count"] = int(len(p))
            p["_source_file"] = os.path.basename(fp)
            parts.append(p)
        except Exception as e:
            print(f"[跳过] {fp}: {e}")
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def apply_sample_quality_filter(
    pooled: pd.DataFrame,
    *,
    min_close_amt_yi: float,
    allow_missing_process: bool,
    min_confidence_tag: str,
) -> pd.DataFrame:
    if pooled is None or pooled.empty:
        return pooled
    x = pooled.copy()
    # 流动性底线：避免微小封单金额主导噪声
    if pd.notna(min_close_amt_yi) and float(min_close_amt_yi) > 0:
        x = x[pd.to_numeric(x["close_order_amount_yi"], errors="coerce") >= float(min_close_amt_yi)]
    # 过程特征完整性：需要同时有稳定性+趋势
    if not allow_missing_process:
        x = x[x["has_process_features"].fillna(False)]
    # 置信度筛选
    tag = (min_confidence_tag or "").strip()
    if tag in {"高", "中", "高置信", "中置信"}:
        if tag in {"高", "高置信"}:
            x = x[x["confidence_tag"].isin(["高", "高置信"])]
        else:
            x = x[x["confidence_tag"].isin(["高", "中", "高置信", "中置信"])]
    return x.reset_index(drop=True)


def evaluate_sample(x: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """与 evaluate_seal_structure.evaluate 同口径；分层里 limit_up 优先用「次日是否涨停」。"""
    if x.empty or len(x) < 3:
        empty_g = pd.DataFrame(
            columns=[
                "seal_rating",
                "N",
                "mean_ret",
                "median_ret",
                "up_ratio",
                "limit_up_ratio",
                "rating_score",
            ]
        )
        summary = {
            "sample_size": int(len(x)),
            "spearman": float("nan"),
            "high_median": float("nan"),
            "low_median": float("nan"),
            "high_up_ratio": float("nan"),
            "low_up_ratio": float("nan"),
            "verdict": "样本不足",
            "ok_count": 0,
        }
        return empty_g, summary

    df = x.copy()
    target_th = float(pd.to_numeric(df.get("target_threshold", -1.0), errors="coerce").iloc[0]) if "target_threshold" in df.columns else -1.0
    if df["next_day_limit_up"].notna().any():
        lim_ratio = lambda s: s.mean()  # noqa: E731
        grouped = (
            df.groupby("seal_rating", as_index=False)
            .agg(
                N=("code", "count"),
                mean_ret=("next_day_ret", "mean"),
                median_ret=("next_day_ret", "median"),
                up_ratio=("next_day_ret", lambda s: (s > target_th).mean()),
                limit_up_ratio=("next_day_limit_up", lambda s: pd.to_numeric(s, errors="coerce").fillna(0.0).gt(0).mean()),
            )
        )
    else:
        grouped = (
            df.groupby("seal_rating", as_index=False)
            .agg(
                N=("code", "count"),
                mean_ret=("next_day_ret", "mean"),
                median_ret=("next_day_ret", "median"),
                up_ratio=("next_day_ret", lambda s: (s > target_th).mean()),
                limit_up_ratio=("next_day_ret", lambda s: (s >= 9.8).mean()),
            )
        )
    grouped["rating_score"] = grouped["seal_rating"].map(RATING_ORDER)
    grouped = grouped.sort_values("rating_score").reset_index(drop=True)

    if len(df) >= 3:
        spearman = df["rating_score"].corr(df["next_day_ret"], method="spearman")
    else:
        spearman = float("nan")

    high = df[df["rating_score"] >= 4]
    low = df[df["rating_score"] <= 2]
    high_median = high["next_day_ret"].median() if not high.empty else float("nan")
    low_median = low["next_day_ret"].median() if not low.empty else float("nan")
    high_up = (high["next_day_ret"] > target_th).mean() if not high.empty else float("nan")
    low_up = (low["next_day_ret"] > target_th).mean() if not low.empty else float("nan")
    median_gap = (high_median - low_median) if (pd.notna(high_median) and pd.notna(low_median)) else float("nan")
    up_ratio_gap = (high_up - low_up) if (pd.notna(high_up) and pd.notna(low_up)) else float("nan")

    ok_count = 0
    if pd.notna(high_median) and pd.notna(low_median) and high_median >= low_median:
        ok_count += 1
    if pd.notna(high_up) and pd.notna(low_up) and high_up >= low_up:
        ok_count += 1
    if pd.notna(spearman) and spearman > 0:
        ok_count += 1

    if ok_count >= 2:
        verdict = "初步成立：封单评级对次日表现有正向区分能力。"
    else:
        verdict = "证据偏弱：评级与次日表现区分不明显，建议扩大样本期再评估。"

    summary = {
        "sample_size": int(len(df)),
        "spearman": spearman,
        "high_median": high_median,
        "low_median": low_median,
        "median_gap": median_gap,
        "high_up_ratio": high_up,
        "low_up_ratio": low_up,
        "up_ratio_gap": up_ratio_gap,
        "target_threshold": target_th,
        "verdict": verdict,
        "ok_count": ok_count,
    }
    return grouped, summary


def rolling_windows(
    dates: List[str],
    pooled: pd.DataFrame,
    window: int,
    min_rows: int,
) -> pd.DataFrame:
    """每个窗口：从 dates[start] 到 dates[end]（含）合并 file_date 属于这些日期的行。"""
    rows_out: List[Dict[str, object]] = []
    n = len(dates)
    if n == 0 or pooled.empty:
        return pd.DataFrame()

    for end_idx in range(n):
        start_idx = max(0, end_idx - window + 1)
        win_dates = set(dates[start_idx : end_idx + 1])
        sub = pooled[pooled["file_date"].isin(win_dates)]
        if len(sub) < min_rows:
            continue
        _, summ = evaluate_sample(sub)
        rows_out.append(
            {
                "window_start_封板日": dates[start_idx],
                "window_end_封板日": dates[end_idx],
                "window_days": end_idx - start_idx + 1,
                "sample_size": summ["sample_size"],
                "spearman": summ["spearman"],
                "high_median_ret": summ["high_median"],
                "low_median_ret": summ["low_median"],
                "median_gap": summ["median_gap"],
                "high_up_ratio": summ["high_up_ratio"],
                "low_up_ratio": summ["low_up_ratio"],
                "up_ratio_gap": summ["up_ratio_gap"],
                "ok_count": summ["ok_count"],
                "verdict": summ["verdict"],
            }
        )
    return pd.DataFrame(rows_out)


def build_robustness_summary(roll_df: pd.DataFrame) -> pd.DataFrame:
    """
    自动稳健性判断（抗过拟合）：
    - 不是看单一窗口，而是看「整体窗口中的持续性」
    - 同时约束相关性与效应强度（median_gap / up_ratio_gap）
    """
    if roll_df is None or roll_df.empty:
        return pd.DataFrame(
            [
                {
                    "windows_total": 0,
                    "strong_windows": 0,
                    "strong_ratio": float("nan"),
                    "median_spearman": float("nan"),
                    "median_median_gap": float("nan"),
                    "median_up_ratio_gap": float("nan"),
                    "max_consecutive_strong": 0,
                    "robustness_score_100": 0.0,
                    "auto_verdict": "样本不足",
                    "rule_note": "需更多窗口数据",
                }
            ]
        )

    x = roll_df.copy()
    x["strong_flag"] = (
        (pd.to_numeric(x["spearman"], errors="coerce") >= 0.06)
        & (pd.to_numeric(x["median_gap"], errors="coerce") >= 0.20)
        & (pd.to_numeric(x["up_ratio_gap"], errors="coerce") >= 0.01)
    )

    flags = list(x["strong_flag"].fillna(False).astype(bool))
    max_consec = 0
    cur = 0
    for f in flags:
        if f:
            cur += 1
            if cur > max_consec:
                max_consec = cur
        else:
            cur = 0

    n = len(x)
    n_strong = int(sum(flags))
    strong_ratio = n_strong / n if n > 0 else float("nan")
    med_spear = float(pd.to_numeric(x["spearman"], errors="coerce").median())
    med_gap = float(pd.to_numeric(x["median_gap"], errors="coerce").median())
    med_up_gap = float(pd.to_numeric(x["up_ratio_gap"], errors="coerce").median())

    # 100 分制：持续性优先，强度其次
    score = 0.0
    score += max(0.0, min(1.0, strong_ratio / 0.6)) * 45.0
    score += max(0.0, min(1.0, med_spear / 0.12)) * 20.0
    score += max(0.0, min(1.0, med_gap / 0.8)) * 20.0
    score += max(0.0, min(1.0, med_up_gap / 0.06)) * 10.0
    score += max(0.0, min(1.0, max_consec / 4.0)) * 5.0

    if score >= 75 and strong_ratio >= 0.50:
        auto_verdict = "稳健成立：正相关与分层差异在多数窗口持续出现。"
    elif score >= 55 and strong_ratio >= 0.30:
        auto_verdict = "阶段有效：存在正相关与分层信号，但稳定性中等。"
    else:
        auto_verdict = "证据偏弱：信号不稳定，建议继续优化结构算法并扩大样本。"

    return pd.DataFrame(
        [
            {
                "windows_total": n,
                "strong_windows": n_strong,
                "strong_ratio": strong_ratio,
                "median_spearman": med_spear,
                "median_median_gap": med_gap,
                "median_up_ratio_gap": med_up_gap,
                "max_consecutive_strong": max_consec,
                "robustness_score_100": round(score, 2),
                "auto_verdict": auto_verdict,
                "rule_note": "strong定义: spearman>=0.06 且 median_gap>=0.20 且 up_ratio_gap>=0.01",
            }
        ]
    )


def build_regime_analysis(pooled: pd.DataFrame, hot_limitup_threshold: int) -> pd.DataFrame:
    """
    市场二分（基于当日涨停家数）：
    - 非热市：< hot_limitup_threshold
    - 热市：>= hot_limitup_threshold
    """
    if pooled is None or pooled.empty:
        return pd.DataFrame()
    day_cov = (
        pooled.groupby("file_date", as_index=False)
        .agg(day_sample=("code", "count"), day_ret=("next_day_ret", "mean"), day_limitup_count=("day_limitup_count", "max"))
        .sort_values("file_date")
    )
    if len(day_cov) < 2:
        return pd.DataFrame()
    th = int(hot_limitup_threshold)
    day_cov["regime"] = day_cov["day_limitup_count"].map(lambda v: "热市" if float(v) >= th else "非热市")

    rows: List[Dict[str, object]] = []
    for rg in ["非热市", "热市"]:
        dates = set(day_cov.loc[day_cov["regime"] == rg, "file_date"].tolist())
        sub = pooled[pooled["file_date"].isin(dates)]
        if sub.empty:
            continue
        _, s = evaluate_sample(sub)
        rows.append(
            {
                "regime": rg,
                "days": len(dates),
                "hot_threshold": th,
                "sample_size": s["sample_size"],
                "spearman": s["spearman"],
                "median_gap": s["median_gap"],
                "up_ratio_gap": s["up_ratio_gap"],
                "ok_count": s["ok_count"],
                "verdict": s["verdict"],
            }
        )
    return pd.DataFrame(rows)


def build_final_policy(regime_df: pd.DataFrame) -> pd.DataFrame:
    """
    给出单一最终建议（内部可分热市/非热市评估）：
    - 仅非热市启用
    - 仅热市启用
    - 全市场启用
    - 暂不启用
    """
    if regime_df is None or regime_df.empty:
        return pd.DataFrame(
            [
                {
                    "final_policy": "暂不启用",
                    "reason": "市场分层样本不足",
                }
            ]
        )

    def _score_row(row: pd.Series) -> float:
        sp = float(pd.to_numeric(row.get("spearman"), errors="coerce") or 0.0)
        mg = float(pd.to_numeric(row.get("median_gap"), errors="coerce") or 0.0)
        ug = float(pd.to_numeric(row.get("up_ratio_gap"), errors="coerce") or 0.0)
        ok = float(pd.to_numeric(row.get("ok_count"), errors="coerce") or 0.0)
        # 统一尺度的轻量打分（非拟合，仅解释性）
        s = 0.0
        s += max(0.0, min(1.0, sp / 0.12)) * 0.35
        s += max(0.0, min(1.0, mg / 1.20)) * 0.35
        s += max(0.0, min(1.0, ug / 0.08)) * 0.20
        s += max(0.0, min(1.0, ok / 3.00)) * 0.10
        return float(s)

    non_hot = regime_df[regime_df["regime"] == "非热市"]
    hot = regime_df[regime_df["regime"] == "热市"]
    nh_score = _score_row(non_hot.iloc[0]) if not non_hot.empty else 0.0
    h_score = _score_row(hot.iloc[0]) if not hot.empty else 0.0

    nh_ok = int(pd.to_numeric(non_hot.iloc[0]["ok_count"], errors="coerce")) if not non_hot.empty else 0
    h_ok = int(pd.to_numeric(hot.iloc[0]["ok_count"], errors="coerce")) if not hot.empty else 0

    if nh_score >= 0.60 and h_score < 0.40 and nh_ok >= 2:
        policy = "仅非热市启用"
        reason = f"非热市显著优于热市（nh_score={nh_score:.2f}, hot_score={h_score:.2f}）"
    elif h_score >= 0.60 and nh_score < 0.40 and h_ok >= 2:
        policy = "仅热市启用"
        reason = f"热市显著优于非热市（hot_score={h_score:.2f}, nh_score={nh_score:.2f}）"
    elif nh_score >= 0.55 and h_score >= 0.55 and nh_ok >= 2 and h_ok >= 2:
        policy = "全市场启用"
        reason = f"热/非热均达可用门槛（nh_score={nh_score:.2f}, hot_score={h_score:.2f}）"
    else:
        policy = "暂不启用"
        reason = f"两类市场稳定性不足（nh_score={nh_score:.2f}, hot_score={h_score:.2f}）"

    return pd.DataFrame(
        [
            {
                "final_policy": policy,
                "reason": reason,
                "non_hot_score": round(nh_score, 4),
                "hot_score": round(h_score, 4),
                "non_hot_ok_count": nh_ok,
                "hot_ok_count": h_ok,
            }
        ]
    )


def _calc_rule_metrics(df: pd.DataFrame, sp_th: float, med_gap_th: float, up_gap_th: float) -> Dict[str, float]:
    if df is None or df.empty:
        return {"windows": 0, "strong_windows": 0, "strong_ratio": 0.0, "max_consecutive": 0}
    x = df.copy()
    strong = (
        (pd.to_numeric(x["spearman"], errors="coerce") >= float(sp_th))
        & (pd.to_numeric(x["median_gap"], errors="coerce") >= float(med_gap_th))
        & (pd.to_numeric(x["up_ratio_gap"], errors="coerce") >= float(up_gap_th))
    )
    flags = list(strong.fillna(False).astype(bool))
    max_consec = 0
    cur = 0
    for f in flags:
        if f:
            cur += 1
            if cur > max_consec:
                max_consec = cur
        else:
            cur = 0
    n = len(flags)
    n_strong = int(sum(flags))
    return {
        "windows": n,
        "strong_windows": n_strong,
        "strong_ratio": (n_strong / n) if n > 0 else 0.0,
        "max_consecutive": max_consec,
    }


def optimize_robust_rule(roll_df: pd.DataFrame) -> pd.DataFrame:
    """
    简单 walk-forward 寻优（抗过拟合）：
    - 按时间顺序切分前 60% 为训练窗，后 40% 为验证窗
    - 搜索阈值组合，优先验证集表现，并惩罚 train-test 偏差
    """
    if roll_df is None or len(roll_df) < 8:
        return pd.DataFrame(
            [
                {
                    "spearman_th": float("nan"),
                    "median_gap_th": float("nan"),
                    "up_ratio_gap_th": float("nan"),
                    "train_windows": 0,
                    "test_windows": 0,
                    "train_strong_ratio": float("nan"),
                    "test_strong_ratio": float("nan"),
                    "train_max_consec": 0,
                    "test_max_consec": 0,
                    "overfit_gap": float("nan"),
                    "objective_score": float("nan"),
                    "rank": 1,
                }
            ]
        )

    x = roll_df.reset_index(drop=True).copy()
    split_idx = max(4, int(len(x) * 0.6))
    split_idx = min(split_idx, len(x) - 3)
    train = x.iloc[:split_idx].copy()
    test = x.iloc[split_idx:].copy()

    sp_grid = [0.03, 0.04, 0.05, 0.06, 0.07, 0.08]
    med_grid = [0.05, 0.10, 0.15, 0.20, 0.30]
    up_grid = [-0.01, 0.00, 0.01, 0.02]

    rows: List[Dict[str, object]] = []
    for sp_th, med_th, up_th in itertools.product(sp_grid, med_grid, up_grid):
        tr = _calc_rule_metrics(train, sp_th, med_th, up_th)
        te = _calc_rule_metrics(test, sp_th, med_th, up_th)

        # 验证集优先；惩罚 train-test 差异（过拟合）
        overfit_gap = abs(tr["strong_ratio"] - te["strong_ratio"])
        objective = (
            0.60 * te["strong_ratio"]
            + 0.20 * (te["max_consecutive"] / max(1, te["windows"]))
            + 0.15 * tr["strong_ratio"]
            + 0.05 * (tr["max_consecutive"] / max(1, tr["windows"]))
            - 0.35 * overfit_gap
        )
        rows.append(
            {
                "spearman_th": sp_th,
                "median_gap_th": med_th,
                "up_ratio_gap_th": up_th,
                "train_windows": tr["windows"],
                "test_windows": te["windows"],
                "train_strong_ratio": tr["strong_ratio"],
                "test_strong_ratio": te["strong_ratio"],
                "train_max_consec": tr["max_consecutive"],
                "test_max_consec": te["max_consecutive"],
                "overfit_gap": overfit_gap,
                "objective_score": objective,
            }
        )

    out = pd.DataFrame(rows).sort_values(
        by=["objective_score", "test_strong_ratio", "overfit_gap"],
        ascending=[False, False, True],
    )
    out["rank"] = range(1, len(out) + 1)
    return out.reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="封单结构滚动窗口统计（支持通用次日字段合并）",
    )
    parser.add_argument(
        "--scan-dir",
        default=history_dir(),
        help="扫描目录（默认 history_data）",
    )
    parser.add_argument("--start", help="只使用封板日文件名 >= YYYYMMDD")
    parser.add_argument("--end", help="只使用封板日文件名 <= YYYYMMDD")
    parser.add_argument(
        "--nextday-file",
        help="通用次日字段 xlsx（默认自动取 scan_dir 下最新 次日字段通用_*.xlsx）",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=20,
        help="滚动窗口包含的封板日文件数量（按排序后的「日」数）",
    )
    parser.add_argument(
        "--min-rows",
        type=int,
        default=30,
        help="窗口内有效样本数低于此值则跳过该窗（不输出行）",
    )
    parser.add_argument(
        "--min-close-amt-yi",
        type=float,
        default=0.3,
        help="样本质控：收盘封单金额(亿)下限，默认 0.3",
    )
    parser.add_argument(
        "--allow-missing-process",
        action="store_true",
        help="样本质控：允许缺失稳定性/趋势（默认不允许）",
    )
    parser.add_argument(
        "--min-confidence",
        choices=["全部", "中", "高", "中置信", "高置信"],
        default="全部",
        help="样本质控：按置信度筛选（默认全部）",
    )
    parser.add_argument(
        "--target-threshold",
        type=float,
        default=-1.0,
        help="目标判定阈值：next_day_ret > 该值 视为成功（默认 -1.0）",
    )
    parser.add_argument(
        "--hot-limitup-threshold",
        type=int,
        default=50,
        help="热市阈值：当日涨停家数 >= 该值 视为热市（默认 50）",
    )
    parser.add_argument(
        "--out",
        "-o",
        help="输出 xlsx（默认 history_data/封单结构_滚动检验_YYYYMMDD_HHMMSS.xlsx）",
    )
    args = parser.parse_args()

    scan_dir = os.path.abspath(args.scan_dir)
    start_f = args.start.strip() if args.start else ""
    end_f = args.end.strip() if args.end else ""

    files = list_seal_files(scan_dir, start_f, end_f)
    nextday_file = os.path.abspath(args.nextday_file) if args.nextday_file else newest_nextday_universe(scan_dir)
    nextday_df: Optional[pd.DataFrame] = None
    if nextday_file and os.path.isfile(nextday_file):
        try:
            nextday_df = load_nextday_universe(nextday_file)
            print(f"次日字段来源: {nextday_file} rows={len(nextday_df)}")
        except Exception as e:
            print(f"[警告] 通用次日字段读取失败，回退含次日文件模式: {e}")
            nextday_df = None
    else:
        print("[提示] 未提供通用次日字段，回退读取封单结构文件内的次日列（需含次日文件）。")
        files = list_enriched_files(scan_dir, start_f, end_f)

    if not files:
        if nextday_df is not None:
            print(f"未找到封单结构文件（封单结构_YYYYMMDD.xlsx）: {scan_dir}")
        else:
            print(f"未找到含次日封单结构文件（封单结构_含次日_YYYYMMDD.xlsx）: {scan_dir}")
        return 1

    dates = [d for d, _ in files]
    pooled_raw = load_all_pooled(files, nextday_df)
    if pooled_raw.empty:
        print("合并后无有效样本（请确认通用次日字段或含次日列可用）。")
        return 1
    pooled = apply_sample_quality_filter(
        pooled_raw,
        min_close_amt_yi=float(args.min_close_amt_yi),
        allow_missing_process=bool(args.allow_missing_process),
        min_confidence_tag=args.min_confidence,
    )
    if pooled.empty:
        print("质控过滤后样本为空，请放宽筛选条件。")
        return 1

    # 记录每个样本对应的目标阈值（用于 evaluate_sample 同口径统计）
    pooled["target_threshold"] = float(args.target_threshold)

    win = max(1, int(args.window))
    min_rows = max(1, int(args.min_rows))

    roll_df = rolling_windows(dates, pooled, win, min_rows)
    full_grouped, full_summary = evaluate_sample(pooled)
    robust_df = build_robustness_summary(roll_df)
    opt_df = optimize_robust_rule(roll_df)
    regime_df = build_regime_analysis(pooled, int(args.hot_limitup_threshold))
    final_policy_df = build_final_policy(regime_df)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = args.out
    if not out_path:
        out_path = os.path.join(scan_dir, f"封单结构_滚动检验_{ts}.xlsx")
    out_path = os.path.abspath(out_path)

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        roll_df.to_excel(writer, index=False, sheet_name="滚动窗口汇总")
        pd.DataFrame([full_summary]).to_excel(writer, index=False, sheet_name="全样本结论")
        full_grouped.to_excel(writer, index=False, sheet_name="全样本_评级分层")
        robust_df.to_excel(writer, index=False, sheet_name="稳健性评分")
        opt_df.head(30).to_excel(writer, index=False, sheet_name="稳健阈值寻优Top30")
        regime_df.to_excel(writer, index=False, sheet_name="市场温度分层")
        final_policy_df.to_excel(writer, index=False, sheet_name="最终建议")
        meta = pd.DataFrame(
            [
                {
                    "scan_dir": scan_dir,
                    "files": len(files),
                    "window_days": win,
                    "min_rows": min_rows,
                    "pooled_rows_raw": len(pooled_raw),
                    "pooled_rows_after_qc": len(pooled),
                    "rolling_rows": len(roll_df),
                    "min_close_amt_yi": float(args.min_close_amt_yi),
                    "allow_missing_process": bool(args.allow_missing_process),
                    "min_confidence": args.min_confidence,
                    "target_threshold": float(args.target_threshold),
                    "hot_limitup_threshold": int(args.hot_limitup_threshold),
                }
            ]
        )
        meta.to_excel(writer, index=False, sheet_name="参数说明")

    print(
        f"文件数: {len(files)}  合并样本(raw): {len(pooled_raw)}  "
        f"质控后: {len(pooled)}  滚动输出行: {len(roll_df)}"
    )
    print(f"全样本 Spearman: {full_summary['spearman']:.4f}" if pd.notna(full_summary["spearman"]) else "全样本 Spearman: n/a")
    if not robust_df.empty:
        r = robust_df.iloc[0]
        print(f"稳健性评分: {r['robustness_score_100']}/100 | strong窗口占比={r['strong_ratio']:.2%} | {r['auto_verdict']}")
    if not opt_df.empty:
        b = opt_df.iloc[0]
        print(
            "推荐阈值: "
            f"spearman>={b['spearman_th']:.2f}, "
            f"median_gap>={b['median_gap_th']:.2f}, "
            f"up_ratio_gap>={b['up_ratio_gap_th']:.2f} "
            f"(test强窗口占比={b['test_strong_ratio']:.2%}, overfit_gap={b['overfit_gap']:.2%})"
        )
    if not final_policy_df.empty:
        p = final_policy_df.iloc[0]
        print(f"最终建议: {p['final_policy']} | {p['reason']}")
    print(f"已写出: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
