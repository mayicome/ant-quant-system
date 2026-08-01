#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 QMT（xtdata）拉取「涨停日 T → 次一交易日 T+1」的收盘涨跌幅，
与 history_data 下「封单结构_YYYYMMDD.xlsx」对齐，做多日滚动检验。

规则：
- T+1 取上交所日历的下一交易日；收益口径与主程序一致：(close_T+1 - close_T) / close_T * 100。
- 若 T+1 尚未「走完」（T+1 日晚于今天，或 T+1 为当天且当前时间早于 15:00），
  该涨停日整批样本不参与统计（当天的封单结构文件整体跳过）。
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd

try:
    import xtquant.xtdata as xtdata
except Exception:
    xtdata = None

from limit_up_structure_analysis_gui import _extract_open_close_map, _to_full_stock_code

# 复用 evaluate_seal_structure 的分层与 Spearman，不依赖包路径
_EVAL_PATH = os.path.join(_ROOT, "tools", "evaluate_seal_structure.py")
_spec = importlib.util.spec_from_file_location("evaluate_seal_structure", _EVAL_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"无法加载: {_EVAL_PATH}")
_eval_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_eval_mod)
evaluate = _eval_mod.evaluate
find_column = _eval_mod.find_column
normalize_code = _eval_mod.normalize_code


def repo_root() -> str:
    return _ROOT


def history_dir() -> str:
    return os.path.join(repo_root(), "history_data")


def _parse_ts_to_yyyymmdd(ts: object) -> Optional[str]:
    try:
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(float(ts) / 1000.0)
            return dt.strftime("%Y%m%d")
        if isinstance(ts, str):
            return ts.replace("-", "")[:8]
        return str(ts).replace("-", "")[:8]
    except Exception:
        return None


def sh_trade_dates_between(start_yyyymmdd: str, end_yyyymmdd: str) -> List[str]:
    if xtdata is None:
        return []
    try:
        xtdata.enable_hello = False
    except Exception:
        pass
    raw = xtdata.get_trading_dates("SH", start_time=start_yyyymmdd, end_time=end_yyyymmdd)
    out: List[str] = []
    for ts in raw or []:
        d = _parse_ts_to_yyyymmdd(ts)
        if d and len(d) == 8:
            out.append(d)
    return sorted(set(out))


def next_trade_day_after(t_yyyymmdd: str) -> Optional[str]:
    """严格晚于 T 的第一个上交所交易日。"""
    dt = datetime.strptime(t_yyyymmdd, "%Y%m%d")
    end = (dt + timedelta(days=120)).strftime("%Y%m%d")
    dates = sh_trade_dates_between(t_yyyymmdd, end)
    for d in dates:
        if d > t_yyyymmdd:
            return d
    return None


def is_t1_session_complete(t_yyyymmdd: str, now: Optional[datetime] = None) -> bool:
    """
    T+1 是否已可统计：存在下一交易日 t1，且
    - t1 < 今天：可统计
    - t1 == 今天：仅当本地时间 >= 15:00（假定 A 股收盘后）
    - t1 > 今天：不可统计
    """
    now = now or datetime.now()
    t1 = next_trade_day_after(t_yyyymmdd)
    if not t1:
        return False
    today = now.strftime("%Y%m%d")
    if t1 > today:
        return False
    if t1 < today:
        return True
    return now.hour >= 15


def load_seal_rows_for_date(xlsx_path: str, limitup_date: str) -> pd.DataFrame:
    df = pd.read_excel(xlsx_path)
    code_col = find_column(df, ["股票代码", "代码", "code"])
    rating_col = find_column(df, ["封单评级", "order_rating"])
    name_col = find_column(df, ["股票名称", "名称", "name"])
    if code_col is None or rating_col is None:
        raise ValueError(f"封单结构文件缺少必要列（代码/封单评级）: {xlsx_path}")
    out = pd.DataFrame()
    out["code"] = df[code_col].map(normalize_code)
    out["seal_rating"] = df[rating_col].astype(str).str.strip()
    out["name"] = df[name_col].astype(str).str.strip() if name_col is not None else ""
    out["limitup_date"] = limitup_date
    out = out[(out["code"] != "") & (out["seal_rating"] != "")]
    return out.reset_index(drop=True)


def list_seal_structure_files(hd: str) -> List[Tuple[str, str]]:
    """[(YYYYMMDD, path), ...] 升序。"""
    cands: List[Tuple[str, str]] = []
    if not os.path.isdir(hd):
        return cands
    for name in os.listdir(hd):
        if not name.lower().endswith(".xlsx"):
            continue
        m = re.match(r"封单结构_(\d{8})\.xlsx$", name, flags=re.IGNORECASE)
        if not m:
            continue
        d = m.group(1)
        cands.append((d, os.path.join(hd, name)))
    cands.sort(key=lambda x: x[0])
    return cands


_ret_cache: Dict[Tuple[str, str], Optional[float]] = {}


def t1_close_return_pct(code_6: str, t: str, t1: str) -> Optional[float]:
    """收盘→收盘，百分比。"""
    key = (normalize_code(code_6), t)
    if key in _ret_cache:
        return _ret_cache[key]
    if xtdata is None:
        _ret_cache[key] = None
        return None
    full = _to_full_stock_code(code_6)
    start = f"{t}000000"
    end = f"{t1}235959"
    try:
        xtdata.enable_hello = False
    except Exception:
        pass
    try:
        xtdata.download_history_data(full, "1d", start, end)
        df_map = xtdata.get_market_data_ex(
            [],
            [full],
            period="1d",
            start_time=start,
            end_time=end,
            count=-1,
        )
    except Exception:
        _ret_cache[key] = None
        return None
    if df_map is None or full not in df_map or df_map[full] is None:
        _ret_cache[key] = None
        return None
    ohlc = _extract_open_close_map(df_map[full])
    row_t = ohlc.get(t)
    row_t1 = ohlc.get(t1)
    if not row_t or not row_t1:
        _ret_cache[key] = None
        return None
    c0, c1 = row_t[3], row_t1[3]
    if c0 is None or c1 is None or c0 == 0:
        _ret_cache[key] = None
        return None
    v = (c1 - c0) / c0 * 100.0
    _ret_cache[key] = v
    return v


def build_completed_samples(
    files: List[Tuple[str, str]],
    now: Optional[datetime] = None,
) -> Tuple[pd.DataFrame, List[str], List[str]]:
    """
    返回：明细 DataFrame；已完成的 limitup 日期列表；因 T+1 未就绪而跳过的日期列表。
    """
    now = now or datetime.now()
    skipped: List[str] = []
    parts: List[pd.DataFrame] = []
    completed_dates: List[str] = []

    for d, path in files:
        if not is_t1_session_complete(d, now=now):
            skipped.append(d)
            continue
        t1 = next_trade_day_after(d)
        if not t1:
            skipped.append(d)
            continue
        try:
            day_df = load_seal_rows_for_date(path, d)
        except Exception:
            skipped.append(d)
            continue
        rets: List[Optional[float]] = []
        for _, row in day_df.iterrows():
            rets.append(t1_close_return_pct(str(row["code"]), d, t1))
        day_df = day_df.copy()
        day_df["t1_date"] = t1
        day_df["next_day_ret"] = rets
        # 仅保留成功拉到 T+1 收盘的样本
        day_df = day_df[day_df["next_day_ret"].notna()].copy()
        if day_df.empty:
            skipped.append(d)
            continue
        parts.append(day_df)
        completed_dates.append(d)

    if not parts:
        return pd.DataFrame(), completed_dates, skipped
    merged = pd.concat(parts, ignore_index=True)
    return merged, completed_dates, skipped


def rolling_evaluate(
    merged: pd.DataFrame,
    completed_dates: List[str],
    window: int,
) -> pd.DataFrame:
    """每个窗口末端交易日：用该窗口内所有涨停日的样本跑一次 evaluate。"""
    rows: List[dict] = []
    if merged.empty or not completed_dates or window < 1:
        return pd.DataFrame(rows)

    dates_sorted = sorted(set(completed_dates))
    for end_i in range(window - 1, len(dates_sorted)):
        win = dates_sorted[end_i - window + 1 : end_i + 1]
        sub = merged[merged["limitup_date"].isin(win)].copy()
        _, summary = evaluate(sub)
        spearman = summary.get("spearman", float("nan"))
        rows.append(
            {
                "window_end_date": win[-1],
                "window_start_date": win[0],
                "window_trade_days": len(win),
                "sample_size": int(len(sub)),
                "spearman": spearman,
                "verdict": summary.get("verdict", ""),
                "high_median": summary.get("high_median"),
                "low_median": summary.get("low_median"),
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="QMT 拉取封单结构样本的 T+1 收盘表现并做多日滚动检验",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--history-dir", default=history_dir(), help="history_data 目录")
    parser.add_argument("--window", type=int, default=20, help="滚动窗口包含的涨停日数量（交易日个数）")
    parser.add_argument("--start", help="只处理 limitup_date >= YYYYMMDD")
    parser.add_argument("--end", help="只处理 limitup_date <= YYYYMMDD")
    parser.add_argument("--out", help="输出 xlsx（默认 history_data/封单结构_T1滚动检验_时间戳.xlsx）")
    args = parser.parse_args()

    if xtdata is None:
        print("错误: xtdata 不可用，请在已安装 QMT/xtquant 的环境中运行。")
        return 1

    hd = os.path.abspath(args.history_dir)
    files = list_seal_structure_files(hd)
    if args.start:
        files = [x for x in files if x[0] >= args.start.strip()]
    if args.end:
        files = [x for x in files if x[0] <= args.end.strip()]
    if not files:
        print(f"未在 {hd} 找到 封单结构_YYYYMMDD.xlsx 文件。")
        return 1

    merged, completed_dates, skipped_incomplete = build_completed_samples(files)

    print(f"history_data: {hd}")
    print(f"封单结构文件数（筛选后）: {len(files)}")
    print(f"T+1 已收盘、纳入样本的涨停日数: {len(set(completed_dates))}")
    if skipped_incomplete:
        preview = skipped_incomplete[:15]
        extra = f" ... 共{len(skipped_incomplete)}个" if len(skipped_incomplete) > 15 else ""
        print(f"跳过（T+1 未就绪或无效）的日期（节选）: {preview}{extra}")

    if merged.empty:
        print("无可用样本：请确认已有封单结构文件且对应 T+1 已收盘。")
        return 2

    grouped_all, summary_all = evaluate(merged)
    roll_df = rolling_evaluate(merged, sorted(set(completed_dates)), max(1, int(args.window)))

    out_path = args.out
    if not out_path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(hd, f"封单结构_T1滚动检验_{stamp}.xlsx")
    out_path = os.path.abspath(out_path)

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        grouped_all.to_excel(writer, index=False, sheet_name="全日分层统计")
        pd.DataFrame([summary_all]).to_excel(writer, index=False, sheet_name="全日结论摘要")
        merged.sort_values(["limitup_date", "code"]).to_excel(writer, index=False, sheet_name="样本明细")
        if not roll_df.empty:
            roll_df.to_excel(writer, index=False, sheet_name="滚动窗口摘要")
        else:
            pd.DataFrame(
                [{"note": "样本不足或窗口过大，无滚动行（需至少 window 个已完成涨停日）。"}]
            ).to_excel(writer, index=False, sheet_name="滚动窗口摘要")

    print(f"\n全日样本数: {summary_all['sample_size']}")
    if pd.notna(summary_all.get("spearman")):
        print(f"全日 Spearman: {summary_all['spearman']:.4f}")
    print(f"判断: {summary_all.get('verdict', '')}")
    print(f"\n已输出: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
