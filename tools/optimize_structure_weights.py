#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动搜索封单结构加权参数（6参数）以提升与次日收盘表现的相关性。

目标：
- 快速离线搜索（不重拉 tick）
- 使用已有 `封单结构_YYYYMMDD.xlsx` + `次日字段通用_*.xlsx`
- 输出 Top 参数组合与推荐参数文件

默认优化目标：
- 次日成功定义：next_day_ret > -1%
- 同时兼顾 Spearman / median_gap / up_ratio_gap
- 使用时间切分（前60%训练，后40%验证）并惩罚过拟合
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd

from limit_up_structure_analysis_gui import _score_close_order_amount, _score_rush_intensity, _score_seal_hardness

RATING_ORDER = {
    "🔴 虚封高危": 1,
    "🟠 弱势封板": 2,
    "🟡 中等封板": 3,
    "🟢 强势封板": 4,
    "🔥 超强极致封板": 5,
}


def history_dir() -> str:
    return os.path.join(_ROOT, "history_data")


def normalize_code(v: object) -> str:
    s = str(v or "").strip().upper()
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else s.zfill(6)[:6]


def find_column(df: pd.DataFrame, aliases: List[str]) -> Optional[str]:
    mp = {str(c).strip().lower(): c for c in df.columns}
    for a in aliases:
        k = a.strip().lower()
        if k in mp:
            return mp[k]
    return None


def list_seal_files(scan_dir: str, start: str = "", end: str = "") -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if not os.path.isdir(scan_dir):
        return out
    for name in os.listdir(scan_dir):
        if not name.lower().endswith(".xlsx"):
            continue
        if "封单结构" not in name:
            continue
        if ("含次日" in name) or ("滚动检验" in name) or ("封单结构评估" in name):
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
    c_col = find_column(df, ["code", "代码", "股票代码"])
    r_col = find_column(df, ["next_day_ret", "次日收盘涨幅%", "次日收盘涨幅"])
    if d_col is None or c_col is None or r_col is None:
        raise ValueError("通用次日字段缺少必要列(seal_date/code/next_day_ret)")
    out = pd.DataFrame()
    out["file_date"] = df[d_col].astype(str).str.replace(r"\D", "", regex=True).str[:8]
    out["code"] = df[c_col].map(normalize_code)
    out["next_day_ret"] = pd.to_numeric(df[r_col], errors="coerce")
    out = out.dropna(subset=["next_day_ret"])
    out = out[(out["file_date"].str.len() == 8) & (out["code"] != "")]
    out = out.drop_duplicates(subset=["file_date", "code"], keep="first").reset_index(drop=True)
    return out


def _parse_pct_to_ratio(v: object) -> Optional[float]:
    s = str(v or "").strip()
    if not s:
        return None
    s = s.replace("%", "").strip()
    try:
        return float(s) / 100.0
    except Exception:
        return None


def _stability_to_score(label: str) -> int:
    t = (label or "").strip()
    if "极度稳定" in t:
        return 10
    if "整体平稳" in t:
        return 5
    if "剧烈波动" in t:
        return 0
    return 0


def _trend_to_score(label: str) -> int:
    t = (label or "").strip()
    if "持续加强" in t:
        return 10
    if "持续减弱" in t:
        return 0
    if "小幅加强" in t:
        return 7
    if "小幅减弱" in t:
        return 3
    if "整体平稳" in t:
        return 5
    return 5 if t else 0


def _rank01(values: List[Optional[float]]) -> List[Optional[float]]:
    valid = [(i, float(v)) for i, v in enumerate(values) if v is not None]
    out: List[Optional[float]] = [None] * len(values)
    if not valid:
        return out
    if len(valid) == 1:
        out[valid[0][0]] = 0.5
        return out
    valid.sort(key=lambda x: x[1])
    n = len(valid)
    for rk, (i, _) in enumerate(valid):
        out[i] = rk / (n - 1)
    return out


def _rating_score_from_total(total: int) -> int:
    s = int(total)
    if s <= 18:
        return 1
    if s <= 45:
        return 2
    if s <= 68:
        return 3
    if s <= 92:
        return 4
    return 5


def load_feature_pool(
    scan_dir: str,
    start: str,
    end: str,
    nextday_df: Optional[pd.DataFrame],
    force_enriched_mode: bool = False,
) -> pd.DataFrame:
    use_enriched_self_ret = bool(force_enriched_mode)
    if use_enriched_self_ret:
        files = list_enriched_files(scan_dir, start, end)
    else:
        files = list_seal_files(scan_dir, start, end)
        if not files:
            files = list_enriched_files(scan_dir, start, end)
            use_enriched_self_ret = True
    if not files:
        return pd.DataFrame()
    parts: List[pd.DataFrame] = []
    for d, fp in files:
        try:
            df = pd.read_excel(fp)
            c_col = find_column(df, ["股票代码", "代码", "code"])
            hard_col = find_column(df, ["封板硬度", "seal_hardness"])
            rush_col = find_column(df, ["抢筹烈度", "rush_intensity"])
            amt_col = find_column(df, ["收盘封单金额(亿)", "close_order_amount_yi"])
            st_col = find_column(df, ["封单稳定性", "order_stability"])
            tr_col = find_column(df, ["封单运行趋势", "order_trend"])
            if c_col is None:
                continue
            x = pd.DataFrame()
            x["file_date"] = str(d)
            x["code"] = df[c_col].map(normalize_code)
            x["seal_ratio"] = df[hard_col].map(_parse_pct_to_ratio) if hard_col is not None else None
            x["rush_ratio"] = df[rush_col].map(_parse_pct_to_ratio) if rush_col is not None else None
            x["close_amt_yi"] = pd.to_numeric(df[amt_col], errors="coerce") if amt_col is not None else None
            x["stability_label"] = df[st_col].astype(str).str.strip() if st_col is not None else ""
            x["trend_label"] = df[tr_col].astype(str).str.strip() if tr_col is not None else ""
            x = x[x["code"] != ""].copy()
            x["score_hard"] = x["seal_ratio"].map(lambda v: _score_seal_hardness(v if pd.notna(v) else None))
            x["score_rush"] = x["rush_ratio"].map(lambda v: _score_rush_intensity(v if pd.notna(v) else None))
            x["score_amt"] = x["close_amt_yi"].map(lambda v: _score_close_order_amount(v if pd.notna(v) else None))
            x["score_stability"] = x["stability_label"].map(_stability_to_score)
            x["score_trend"] = x["trend_label"].map(_trend_to_score)

            # 与现有逻辑一致的截面加减项（不依赖权重）
            amt_rank = _rank01([v if pd.notna(v) else None for v in x["close_amt_yi"].tolist()]) if len(x) >= 8 else [None] * len(x)
            hard_rank = _rank01([v if pd.notna(v) else None for v in x["seal_ratio"].tolist()]) if len(x) >= 8 else [None] * len(x)
            rush_rank = _rank01([v if pd.notna(v) else None for v in x["rush_ratio"].tolist()]) if len(x) >= 8 else [None] * len(x)
            static_bonus: List[int] = []
            for i, row in x.reset_index(drop=True).iterrows():
                b = 0
                if amt_rank[i] is not None and hard_rank[i] is not None and rush_rank[i] is not None:
                    b += int(round((amt_rank[i] - 0.5) * 12))
                    b += int(round((hard_rank[i] - 0.5) * 16))
                    b += int(round((rush_rank[i] - 0.5) * 12))
                st = int(row["score_stability"] or 0)
                tr = int(row["score_trend"] or 0)
                if st >= 10:
                    b += 8
                elif st >= 5:
                    b += 3
                elif row["stability_label"]:
                    b -= 5
                if tr >= 8:
                    b += 8
                elif tr >= 5:
                    b += 3
                elif row["trend_label"]:
                    b -= 4
                static_bonus.append(b)
            x["static_bonus"] = static_bonus

            if nextday_df is not None and not use_enriched_self_ret:
                x["file_date"] = x["file_date"].astype(str).str.replace(r"\D", "", regex=True).str[:8]
                x["code"] = x["code"].astype(str).map(normalize_code)
                nd = nextday_df.copy()
                nd["file_date"] = nd["file_date"].astype(str).str.replace(r"\D", "", regex=True).str[:8]
                nd["code"] = nd["code"].astype(str).map(normalize_code)
                x = x.merge(nd, on=["file_date", "code"], how="inner")
            elif use_enriched_self_ret:
                ret_col = find_column(df, ["次日收盘涨幅%", "次日收盘涨幅", "next_day_ret"])
                if ret_col is None:
                    continue
                x["next_day_ret"] = pd.to_numeric(df[ret_col], errors="coerce")
                x = x.dropna(subset=["next_day_ret"])
            else:
                continue
            if not x.empty:
                x["_source_file"] = os.path.basename(fp)
                parts.append(x)
        except Exception as e:
            print(f"[跳过文件] {os.path.basename(fp)}: {e}")
            continue
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def evaluate_combo(df: pd.DataFrame, params: Dict[str, float], target_th: float = -1.0) -> Dict[str, float]:
    x = df.copy()
    base = (
        params["bias"]
        + params["w_hard"] * x["score_hard"].astype(float)
        + params["w_rush"] * x["score_rush"].astype(float)
        + params["w_amt"] * x["score_amt"].astype(float)
        + params["w_st"] * x["score_stability"].astype(float)
        + params["w_tr"] * x["score_trend"].astype(float)
    )
    total = (base + x["static_bonus"].astype(float)).round().clip(0, 120).astype(int)
    x["rating_score"] = total.map(_rating_score_from_total)
    if len(x) < 10:
        return {"spearman": float("nan"), "median_gap": float("nan"), "up_ratio_gap": float("nan"), "n": len(x)}
    spearman = x["rating_score"].corr(x["next_day_ret"], method="spearman")
    high = x[x["rating_score"] >= 4]
    low = x[x["rating_score"] <= 2]
    high_med = high["next_day_ret"].median() if not high.empty else float("nan")
    low_med = low["next_day_ret"].median() if not low.empty else float("nan")
    high_up = (high["next_day_ret"] > target_th).mean() if not high.empty else float("nan")
    low_up = (low["next_day_ret"] > target_th).mean() if not low.empty else float("nan")
    return {
        "spearman": float(spearman) if pd.notna(spearman) else float("nan"),
        "median_gap": (float(high_med - low_med) if pd.notna(high_med) and pd.notna(low_med) else float("nan")),
        "up_ratio_gap": (float(high_up - low_up) if pd.notna(high_up) and pd.notna(low_up) else float("nan")),
        "n": int(len(x)),
    }


def objective(train_m: Dict[str, float], test_m: Dict[str, float]) -> float:
    def clip01(v: float, denom: float) -> float:
        if pd.isna(v):
            return 0.0
        return max(0.0, min(1.0, v / denom))

    tr = 0.45 * clip01(train_m["spearman"], 0.12) + 0.35 * clip01(train_m["median_gap"], 1.2) + 0.20 * clip01(train_m["up_ratio_gap"], 0.08)
    te = 0.45 * clip01(test_m["spearman"], 0.12) + 0.35 * clip01(test_m["median_gap"], 1.2) + 0.20 * clip01(test_m["up_ratio_gap"], 0.08)
    gap = abs(tr - te)
    return 0.35 * tr + 0.65 * te - 0.35 * gap


def main() -> int:
    parser = argparse.ArgumentParser(description="自动优化封单结构六参数权重")
    parser.add_argument("--scan-dir", default=history_dir(), help="扫描目录（默认 history_data）")
    parser.add_argument("--start", help="起始封板日 YYYYMMDD")
    parser.add_argument("--end", help="结束封板日 YYYYMMDD")
    parser.add_argument("--nextday-file", help="次日字段通用文件（默认自动取最新）")
    parser.add_argument("--target-threshold", type=float, default=-1.0, help="目标口径：next_day_ret > 该值")
    parser.add_argument("--max-combos", type=int, default=1200, help="最大评估组合数（默认1200，0表示全量）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--out", "-o", help="输出xlsx路径")
    args = parser.parse_args()

    scan_dir = os.path.abspath(args.scan_dir)
    next_fp = os.path.abspath(args.nextday_file) if args.nextday_file else newest_nextday_universe(scan_dir)
    nextday_df: Optional[pd.DataFrame] = None
    if next_fp and os.path.isfile(next_fp):
        nextday_df = load_nextday_universe(next_fp)
        print(f"次日字段文件: {next_fp} rows={len(nextday_df)}")
    else:
        print("未找到次日字段通用文件，将尝试从 封单结构_含次日_*.xlsx 直接读取次日收益。")

    start_s = args.start.strip() if args.start else ""
    end_s = args.end.strip() if args.end else ""
    raw_files = list_seal_files(scan_dir, start_s, end_s)
    enr_files = list_enriched_files(scan_dir, start_s, end_s)
    print(f"封单结构原始文件数: {len(raw_files)}  含次日文件数: {len(enr_files)}")

    fallback_used = False
    pool = load_feature_pool(scan_dir, start_s, end_s, nextday_df, force_enriched_mode=False)
    if pool.empty:
        print("主模式样本为空，回退尝试：封单结构_含次日_*.xlsx 自带次日列。")
        fallback_used = True
        pool = load_feature_pool(scan_dir, start_s, end_s, nextday_df=None, force_enriched_mode=True)
    if pool.empty:
        if nextday_df is not None:
            seal_dates = sorted([d for d, _ in (raw_files or enr_files)])
            nd_dates = sorted(nextday_df["file_date"].astype(str).unique().tolist()) if not nextday_df.empty else []
            overlap = sorted(set(seal_dates).intersection(set(nd_dates)))
            print(
                "未构建出可用样本。"
                f" seal_dates={len(seal_dates)} nextday_dates={len(nd_dates)} overlap={len(overlap)}"
            )
            if overlap:
                print(f"重叠日期示例: {overlap[:5]}")
        else:
            print("未构建出可用样本（含次日文件中可能缺少次日涨幅列）。")
        return 1

    pool["file_date"] = pool["file_date"].astype(str).str.strip().str.replace(r"\D", "", regex=True).str[:8]
    date_counts = pool["file_date"].value_counts()
    dates = sorted([str(d).strip() for d in date_counts.index.tolist() if isinstance(d, str) and len(d.strip()) == 8 and d.strip().isdigit()])
    # 兜底：若样本日期列异常，回退到“文件名日期”恢复时间切分
    if len(dates) == 0:
        fallback_dates = [d for d, _ in (enr_files if fallback_used else raw_files)]
        fallback_dates = [str(d).strip() for d in fallback_dates if str(d).strip().isdigit() and len(str(d).strip()) == 8]
        if (not fallback_used) and nextday_df is not None and not nextday_df.empty:
            nd_dates = set(nextday_df["file_date"].astype(str).str.replace(r"\D", "", regex=True).str[:8].tolist())
            fallback_dates = [d for d in fallback_dates if d in nd_dates]
        dates = sorted(set(fallback_dates))
        print(f"[修复] 从文件名恢复可用日期数: {len(dates)}")
        # 关键修复：同步把 pool 内 file_date 也从来源文件名修正，避免后续按日期切分为空
        if "_source_file" in pool.columns:
            pool["file_date"] = (
                pool["_source_file"]
                .astype(str)
                .str.extract(r"(\d{8})", expand=False)
                .fillna("")
                .astype(str)
                .str.strip()
            )
            pool = pool[pool["file_date"].str.len() == 8].copy()
            if dates:
                pool = pool[pool["file_date"].isin(set(dates))].copy()
    print(f"样本日期数: {len(dates)}")
    if dates:
        print(f"日期示例: {dates[:5]}")

    if len(dates) >= 3:
        split_idx = max(4, int(len(dates) * 0.6))
        split_idx = min(split_idx, len(dates) - 2)
        train_dates = set(dates[:split_idx])
        test_dates = set(dates[split_idx:])
        train_df = pool[pool["file_date"].isin(train_dates)].copy()
        test_df = pool[pool["file_date"].isin(test_dates)].copy()
        # 若样本中的 file_date 质量差，导致按日期切分为空，则回退行切分
        if train_df.empty or test_df.empty:
            print("[警告] 按日期切分后样本为空，回退到按样本行切分(70/30)。")
            rnd = random.Random(args.seed)
            idx = list(range(len(pool)))
            rnd.shuffle(idx)
            cut = max(1, int(len(idx) * 0.7))
            train_idx = set(idx[:cut])
            train_df = pool.iloc[[i for i in range(len(pool)) if i in train_idx]].copy()
            test_df = pool.iloc[[i for i in range(len(pool)) if i not in train_idx]].copy()
            train_dates = set(train_df["file_date"].astype(str).tolist())
            test_dates = set(test_df["file_date"].astype(str).tolist())
    else:
        # 回退：日期维度不足时按行切分，避免出现“训练集为空”
        print("[警告] 可用日期过少，回退到按样本行切分(70/30)。")
        rnd = random.Random(args.seed)
        idx = list(range(len(pool)))
        rnd.shuffle(idx)
        cut = max(1, int(len(idx) * 0.7))
        train_idx = set(idx[:cut])
        train_df = pool.iloc[[i for i in range(len(pool)) if i in train_idx]].copy()
        test_df = pool.iloc[[i for i in range(len(pool)) if i not in train_idx]].copy()
        train_dates = set(train_df["file_date"].unique().tolist())
        test_dates = set(test_df["file_date"].unique().tolist())

    if train_df.empty or test_df.empty:
        print(f"切分异常: train_n={len(train_df)} test_n={len(test_df)}，请检查输入文件。")
        return 1

    # 六参数搜索空间（可按需加密）
    grids = {
        "w_hard": [0.4, 0.6, 0.75, 0.9, 1.1],
        "w_rush": [0.4, 0.6, 0.7, 0.9, 1.1],
        "w_amt": [0.3, 0.5, 0.6, 0.8, 1.0],
        "w_st": [1.0, 1.5, 2.0, 2.5, 3.0],
        "w_tr": [1.0, 1.4, 1.8, 2.2, 2.8],
        "bias": [-6.0, -3.0, 0.0, 3.0, 6.0],
    }
    keys = list(grids.keys())
    all_combos = list(itertools.product(*[grids[k] for k in keys]))
    random.seed(args.seed)
    if args.max_combos and args.max_combos > 0 and len(all_combos) > args.max_combos:
        combos = random.sample(all_combos, k=int(args.max_combos))
    else:
        combos = all_combos

    rows: List[Dict[str, object]] = []
    for vals in combos:
        p = {k: float(v) for k, v in zip(keys, vals)}
        tr = evaluate_combo(train_df, p, target_th=float(args.target_threshold))
        te = evaluate_combo(test_df, p, target_th=float(args.target_threshold))
        obj = objective(tr, te)
        rows.append(
            {
                **p,
                "train_spearman": tr["spearman"],
                "test_spearman": te["spearman"],
                "train_median_gap": tr["median_gap"],
                "test_median_gap": te["median_gap"],
                "train_up_ratio_gap": tr["up_ratio_gap"],
                "test_up_ratio_gap": te["up_ratio_gap"],
                "objective": obj,
                "train_n": tr["n"],
                "test_n": te["n"],
            }
        )

    res = pd.DataFrame(rows).sort_values(by=["objective", "test_spearman", "test_median_gap"], ascending=[False, False, False]).reset_index(drop=True)
    res["rank"] = range(1, len(res) + 1)
    best = res.iloc[0].to_dict()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.abspath(args.out) if args.out else os.path.join(scan_dir, f"封单结构_参数寻优_{ts}.xlsx")
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        res.head(200).to_excel(writer, index=False, sheet_name="Top200")
        pd.DataFrame([best]).to_excel(writer, index=False, sheet_name="Best")
        pd.DataFrame(
            [
                {
                    "scan_dir": scan_dir,
                    "nextday_file": next_fp,
                    "samples": len(pool),
                    "train_days": len(train_dates),
                    "test_days": len(test_dates),
                    "combos_total": len(all_combos),
                    "combos_eval": len(combos),
                    "target_threshold": float(args.target_threshold),
                }
            ]
        ).to_excel(writer, index=False, sheet_name="Meta")

    best_params = {
        "SCORE_WEIGHT_SEAL_HARDNESS": float(best["w_hard"]),
        "SCORE_WEIGHT_RUSH_INTENSITY": float(best["w_rush"]),
        "SCORE_WEIGHT_CLOSE_AMOUNT": float(best["w_amt"]),
        "SCORE_WEIGHT_STABILITY": float(best["w_st"]),
        "SCORE_WEIGHT_TREND": float(best["w_tr"]),
        "SCORE_BASE_BIAS": float(best["bias"]),
    }
    json_path = os.path.join(scan_dir, f"封单结构_最优参数_{ts}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(best_params, f, ensure_ascii=False, indent=2)

    print(f"样本数: {len(pool)}  训练天数: {len(train_dates)}  验证天数: {len(test_dates)}")
    print(f"搜索组合: {len(combos)}/{len(all_combos)}")
    print("最优参数:")
    for k, v in best_params.items():
        print(f"  {k} = {v}")
    print(f"best objective={best['objective']:.4f}  test_spearman={best['test_spearman']:.4f}  test_median_gap={best['test_median_gap']:.4f}")
    print(f"已写出: {out_path}")
    print(f"参数JSON: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
