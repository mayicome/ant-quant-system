# -*- coding: utf-8 -*-
"""
马总选股逻辑 · 满足条件对比监控（核心库）

按回测按票文件中的「满足条件」列，将同一回测池拆成两组对比：
  - 满足条件（软门槛五项全为真：涨幅榜行业前32/概念前8、流通市值<80亿、前10日无大涨、站上MA5/MA20、站上布林上轨）
  - 不满足条件

用法:
  python tools/ma_zong_meet_monitor.py
  python tools/ma_zong_meet_monitor.py --window 10 --months 2

目录:
  history_data/马总选股逻辑/
    选股结果_马总选股逻辑-次日MA10_*.xls
    各日选股收益汇总_日线-ma10-sell_half-单点_按票_*_收盘上MA10_latest.xlsx
"""
from __future__ import annotations

import argparse
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from em_hot_clip_monitor import (  # noqa: E402
    _col_ts,
    _discover_csv_under,
    _empty_sheet_hint,
    _sheet_name,
    as_bool,
    code6,
    cumulative_day_means,
    day_means,
    detail_for_start,
    ensure_start,
    last_closed_trading_day,
    pit_mask,
    prev_trading_day,
    rolling_stats,
    summary_stats,
    trading_days_inclusive,
    trigger_stats,
)

DIR = ROOT / "history_data" / "马总选股逻辑"
RULE_NAME = "马总选股逻辑-次日MA10"
MEET_COL = "满足条件"
DEFAULT_WINDOW = 10

VARIANT_MEET = "满足条件"
VARIANT_NOT_MEET = "不满足条件"
VARIANTS = (VARIANT_MEET, VARIANT_NOT_MEET)


def discover_trade_files() -> list[Path]:
    """优先 收盘上MA10 latest 按票汇总。"""
    pats = (
        "各日选股收益汇总_日线-ma10-sell_half*-单点_按票_*收盘上MA10_latest.xlsx",
        "各日选股收益汇总_日线-ma10*-单点_按票_*收盘上MA10_latest.xlsx",
        "各日选股收益汇总_日线-ma10-sell_half*-单点_按票_latest.xlsx",
        "各日选股收益汇总_日线-ma10*-单点_按票_latest.xlsx",
        "各日选股收益汇总_日线-ma10*-单点_按票_*.xlsx",
    )
    seen: set[Path] = set()
    out: list[Path] = []
    for pat in pats:
        for p in sorted(DIR.glob(pat), key=lambda x: x.stat().st_mtime, reverse=True):
            key = p.resolve()
            if key in seen:
                continue
            if p.name.startswith("选股结果_"):
                continue
            seen.add(key)
            out.append(p)
            if "_latest" in p.name:
                return [p]
        if out and "_latest" in out[0].name:
            return out[:1]
    return out


def discover_selection_files() -> list[Path]:
    """优先带 _boll 的补丁选股表，再按修改时间。"""
    cands = list(DIR.glob(f"选股结果_{RULE_NAME}_*.xls")) + list(
        DIR.glob(f"选股结果_{RULE_NAME}_*.xlsx")
    )
    if not cands:
        cands = list(DIR.glob("选股结果_马总选股逻辑*.xls")) + list(
            DIR.glob("选股结果_马总选股逻辑*.xlsx")
        )

    def _key(p: Path):
        boll = 0 if "_boll" in p.name.lower() else 1
        try:
            mt = -float(p.stat().st_mtime)
        except OSError:
            mt = 0.0
        return (boll, mt)

    return sorted(cands, key=_key)


def discover_fill_files() -> list[Path]:
    return _discover_csv_under(DIR, "回测成交明细_*.csv")


def _order_fill_files(files: list[Path]) -> list[Path]:
    """mtime 新→旧；同批优先 sell_half（与 MA10 风格切换一致）。"""
    files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
    prefer = [p for p in files if "sell_half" in p.name]
    others = [p for p in files if p not in prefer]
    return prefer + others


def _fill_files_for_side(side_zh: str) -> list[Path]:
    """成交明细：优先 ``*_latest.csv``（sell_half > 其它），目录=马总选股逻辑。"""
    side = str(side_zh or "").strip()
    for pat in (
        f"回测成交明细_日线-ma10-sell_half*{side}_latest.csv",
        f"回测成交明细_日线-ma10*{side}_latest.csv",
    ):
        hits = sorted(DIR.glob(pat), key=lambda p: p.stat().st_mtime, reverse=True)
        if hits:
            return hits
    return _order_fill_files(list(DIR.glob(f"回测成交明细_*{side}_*.csv")))


def load_last_exit_map() -> dict:
    """(sel, code) → 实际清仓日。与 ma10_regime_switch 同口径，读本目录卖出明细。"""
    files = _fill_files_for_side("卖出")
    if not files:
        return {}
    parts = []
    for p in files:
        try:
            d = pd.read_csv(p, encoding="utf-8-sig")
        except Exception:
            try:
                d = pd.read_csv(p, encoding="utf-8")
            except Exception:
                continue
        if "日期" not in d.columns or "代码" not in d.columns:
            continue
        t = pd.DataFrame(
            {
                "sel": pd.to_datetime(d.get("选股日"), errors="coerce").dt.date,
                "code": d["代码"].map(code6),
                "td": pd.to_datetime(d["日期"], errors="coerce").dt.date,
                "rem": pd.to_numeric(d.get("交易后持仓"), errors="coerce"),
            }
        )
        t["_mtime"] = float(p.stat().st_mtime)
        parts.append(t.dropna(subset=["sel", "code", "td"]))
    if not parts:
        return {}
    all_d = pd.concat(parts, ignore_index=True)
    all_d = all_d.sort_values(["_mtime", "td"])
    out: dict = {}
    for (sel, code), sub in all_d.groupby(["sel", "code"], sort=False):
        last_m = sub["_mtime"].max()
        sub = sub[sub["_mtime"] == last_m]
        zero = sub[sub["rem"].fillna(1) <= 0]
        if zero.empty:
            continue
        out[(sel, code)] = zero["td"].max()
    return out


def apply_realized_known_on(df: pd.DataFrame) -> pd.DataFrame:
    """收益可知日 = 实际清仓日；未卖完不算已知；无明细时已完成样本退回 end_date。

    与 MA10 风格切换统一；不再误用东财热门夹档成交目录，也不再把计划 end_date
    一刀切当成已了结（否则近期开买会因「满 8 日未到」整段从折线消失）。
    """
    if df is None or df.empty:
        return df
    df = df.copy()
    exit_map = load_last_exit_map()
    keys = list(zip(df["sel"], df["code"] if "code" in df.columns else [""] * len(df)))
    ex = pd.to_datetime([exit_map.get(k) for k in keys])
    if "剩余持仓数量" in df.columns:
        rem = pd.to_numeric(df["剩余持仓数量"], errors="coerce").fillna(0)
        still = rem > 0
    else:
        still = pd.Series(False, index=df.index)
    if "样本完成" in df.columns:
        done = as_bool(df["样本完成"])
    elif "备注" in df.columns:
        done = df["备注"].astype(str).str.contains("已清仓|完成", regex=True, na=False)
    else:
        done = pd.Series(True, index=df.index)
    end = _col_ts(df, "end_date")
    known = ex
    need_fb = known.isna() & (~still) & done.fillna(False)
    if end is not None:
        known = known.where(~need_fb, end)
    known = known.where(~still, pd.NaT)
    df["known_on"] = known
    return df


def _filter_variant(df: pd.DataFrame, variant: str) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if "meet" not in df.columns:
        return df.iloc[0:0].copy()
    meet = as_bool(df["meet"])
    if variant == VARIANT_MEET:
        return df.loc[meet].copy()
    if variant == VARIANT_NOT_MEET:
        return df.loc[~meet].copy()
    return df.copy()


def load_one(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path)
    if "选股日" not in df.columns:
        raise ValueError(f"缺少选股日列: {path.name}")
    code_col = "代码" if "代码" in df.columns else ("股票代码" if "股票代码" in df.columns else None)
    if code_col is None:
        raise ValueError(f"缺少代码列: {path.name}")
    if "收益率pct" not in df.columns:
        raise ValueError(f"缺少收益率pct: {path.name}")

    out = pd.DataFrame()
    out["sel"] = pd.to_datetime(df["选股日"], errors="coerce").dt.date
    out["code"] = df[code_col].map(code6)
    out["ret"] = pd.to_numeric(df["收益率pct"], errors="coerce")
    out["name"] = df["股票名称"] if "股票名称" in df.columns else ""
    out["buy_on"] = _col_ts(df, "买入日")
    if MEET_COL in df.columns:
        out["meet"] = as_bool(df[MEET_COL])
    elif "meet" in df.columns:
        out["meet"] = as_bool(df["meet"])
    else:
        out["meet"] = False
    if "end_date" in df.columns:
        out["end_date"] = df["end_date"]
    if "剩余持仓数量" in df.columns:
        out["剩余持仓数量"] = pd.to_numeric(df["剩余持仓数量"], errors="coerce")
    if "样本完成" in df.columns:
        out["样本完成"] = df["样本完成"]
    if "备注" in df.columns:
        out["备注"] = df["备注"]
    if "买入成交价" in df.columns:
        out["买入成交价"] = pd.to_numeric(df["买入成交价"], errors="coerce")
    if "选股日为涨停后第几日" in df.columns:
        out["lu_day"] = pd.to_numeric(df["选股日为涨停后第几日"], errors="coerce")
    if "不满足的原因" in df.columns:
        out["fail_reason"] = df["不满足的原因"].astype(str)

    out = out[out["ret"].notna() & out["sel"].notna() & out["code"].ne("")].copy()
    out = ensure_start(out)
    out = out[out["start"].notna()].copy()
    out["src"] = path.name
    try:
        out["_mtime"] = float(path.stat().st_mtime)
    except OSError:
        out["_mtime"] = 0.0
    return out


def load_pool(
    files: list[Path] | None = None,
    *,
    apply_known: bool = True,
    variant: Optional[str] = None,
) -> pd.DataFrame:
    files = files or discover_trade_files()
    if not files:
        raise FileNotFoundError(f"未找到按票/收益汇总文件: {DIR}")
    parts = []
    for p in files:
        try:
            parts.append(load_one(p))
        except Exception as e:
            print(f"跳过 {p.name}: {e}", flush=True)
    if not parts:
        raise RuntimeError(f"未能加载任何按票数据: {DIR}")
    df = pd.concat(parts, ignore_index=True)
    sort_cols = ["sel", "code"]
    if "_mtime" in df.columns:
        sort_cols.append("_mtime")
    df = df.sort_values(sort_cols).drop_duplicates(["sel", "code"], keep="last")
    if "_mtime" in df.columns:
        df = df.drop(columns=["_mtime"])
    if apply_known:
        df = apply_realized_known_on(df)
    try:
        from utils.monitor_stock_type_filter import apply_to_pool

        df = apply_to_pool(df)
    except Exception:
        pass
    if variant:
        df = _filter_variant(df, variant)
    return df.reset_index(drop=True)


def load_selection_counts(variant: str | None = None) -> pd.DataFrame:
    """按选股日统计入选只数。

    先按 (选股日, 代码) 取最新文件行，再按「满足条件」分组；
    避免旧文件里已过时的 True 在过滤后仍被计入量柱。
    """
    files = discover_selection_files()
    if not files:
        return pd.DataFrame(columns=["sel", "n_sel"])
    parts = []
    for p in files:
        try:
            df = pd.read_excel(p, engine="xlrd") if p.suffix.lower() == ".xls" else pd.read_excel(p)
        except Exception:
            try:
                df = pd.read_excel(p)
            except Exception:
                continue
        if df is None or df.empty or "选股日" not in df.columns:
            continue
        code_col = "股票代码" if "股票代码" in df.columns else ("代码" if "代码" in df.columns else None)
        t = pd.DataFrame({"sel": pd.to_datetime(df["选股日"], errors="coerce").dt.date})
        if MEET_COL in df.columns:
            t["meet"] = as_bool(df[MEET_COL])
        else:
            t["meet"] = False
        if code_col:
            t["code"] = df[code_col].map(code6)
            t["name"] = df["股票名称"] if "股票名称" in df.columns else ""
            try:
                from utils.monitor_stock_type_filter import filter_dataframe

                t = filter_dataframe(t, code_col="code", name_col="name")
            except Exception:
                pass
            t = t.dropna(subset=["sel"]).drop_duplicates(["sel", "code"])
        else:
            t = t.dropna(subset=["sel"])
        t["_mtime"] = float(p.stat().st_mtime)
        parts.append(t)
    if not parts:
        return pd.DataFrame(columns=["sel", "n_sel"])
    all_d = pd.concat(parts, ignore_index=True)
    if "code" in all_d.columns:
        all_d = all_d.sort_values(["sel", "code", "_mtime"]).drop_duplicates(
            ["sel", "code"], keep="last"
        )
    if variant == VARIANT_MEET:
        all_d = all_d.loc[all_d["meet"]].copy()
    elif variant == VARIANT_NOT_MEET:
        all_d = all_d.loc[~all_d["meet"]].copy()
    if all_d.empty:
        return pd.DataFrame(columns=["sel", "n_sel"])
    g = all_d.groupby("sel").size().reset_index(name="n_sel")
    return g.sort_values("sel").reset_index(drop=True)


def _empty_report(
    *,
    window: int,
    months: int,
    last_close: date,
    variant: str,
    reason: str = "",
) -> Dict[str, Any]:
    empty_day = pd.DataFrame(
        columns=["start", "day_mean", "n", "win_rate", "n_sel", "sel_day"]
    )
    return {
        "files": [],
        "df": pd.DataFrame(),
        "day": empty_day,
        "day_view": empty_day,
        "roll": pd.DataFrame(),
        "roll_view": pd.DataFrame(),
        "cum": pd.DataFrame(columns=["start", "cum_mean"]),
        "cum_view": pd.DataFrame(columns=["start", "cum_mean"]),
        "trigger": pd.DataFrame(columns=["sel", "n_sel", "n_buy", "trigger_pct"]),
        "sel_counts": pd.DataFrame(columns=["sel", "n_sel"]),
        "full": {
            "n_all": 0,
            "n_known": 0,
            "n_open": 0,
            "mean": None,
            "win": None,
            "n_days": 0,
            "day_mean": None,
        },
        "latest_roll": None,
        "window": int(window),
        "months": int(months),
        "last_close": last_close,
        "data_from": "",
        "data_to": "",
        "n_start_days": 0,
        "starts": [],
        "variant": variant,
        "trade_dir": str(DIR),
        "empty_reason": reason or f"目录无回测文件: {DIR}",
    }


def build_report(
    *,
    window: int = DEFAULT_WINDOW,
    months: int = 2,
    end: Optional[date] = None,
    variant: str = VARIANT_MEET,
    pool: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    variant = str(variant or VARIANT_MEET).strip() or VARIANT_MEET
    last_close = end or last_closed_trading_day()
    files = discover_trade_files()
    if not files and pool is None:
        return _empty_report(
            window=window,
            months=months,
            last_close=last_close,
            variant=variant,
        )
    try:
        base = pool if pool is not None else load_pool(files, variant=None)
        df = _filter_variant(base, variant)
    except Exception as e:
        return _empty_report(
            window=window,
            months=months,
            last_close=last_close,
            variant=variant,
            reason=str(e),
        )
    if df.empty:
        return _empty_report(
            window=window,
            months=months,
            last_close=last_close,
            variant=variant,
            reason=f"「{variant}」无样本（按票文件缺少{MEET_COL}列或该组为空）",
        )

    sel_counts = load_selection_counts(variant=variant)
    day = day_means(df, asof=last_close, sel_counts=sel_counts)
    if day.empty:
        day = day_means(
            df[df["known_on"].notna()] if "known_on" in df.columns else df,
            sel_counts=sel_counts,
        )
    cum = cumulative_day_means(day)
    trig = trigger_stats(df, sel_counts)
    cut = last_close - timedelta(days=int(months * 31))
    day_view = day[day["start"] >= cut].copy() if not day.empty else day
    starts = sorted(day["start"].tolist()) if not day.empty else []
    axis = trading_days_inclusive(cut, last_close, extra=starts or None)
    roll = rolling_stats(day, window=window, asofs=axis)
    roll_view = roll[roll["asof"] >= cut].copy() if not roll.empty else roll
    cum_view = cum[cum["start"] >= cut].copy() if not cum.empty else cum
    full = summary_stats(df, asof=last_close)
    latest_roll = roll.iloc[-1].to_dict() if not roll.empty else None

    return {
        "files": [p.name for p in files],
        "df": df,
        "day": day,
        "day_view": day_view,
        "roll": roll,
        "roll_view": roll_view,
        "cum": cum,
        "cum_view": cum_view,
        "trigger": trig,
        "sel_counts": sel_counts,
        "full": full,
        "latest_roll": latest_roll,
        "window": int(window),
        "months": int(months),
        "last_close": last_close,
        "data_from": str(starts[0]) if starts else "",
        "data_to": str(starts[-1]) if starts else "",
        "n_start_days": len(starts),
        "starts": starts,
        "variant": variant,
        "trade_dir": str(DIR),
        "empty_reason": "",
    }


def build_all_reports(
    *,
    window: int = DEFAULT_WINDOW,
    months: int = 2,
    end: Optional[date] = None,
) -> Dict[str, Dict[str, Any]]:
    files = discover_trade_files()
    pool = None
    if files:
        try:
            pool = load_pool(files, variant=None)
        except Exception:
            pool = None
    return {
        v: build_report(window=window, months=months, end=end, variant=v, pool=pool)
        for v in VARIANTS
    }


def load_selection_rows_for_day(sel_day: date, *, variant: str | None = None) -> pd.DataFrame:
    """取覆盖该选股日的最新文件行，再按满足条件过滤（不回退到旧文件）。"""
    files = sorted(discover_selection_files(), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files:
        try:
            df = pd.read_excel(p, engine="xlrd") if p.suffix.lower() == ".xls" else pd.read_excel(p)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        date_col = next((c for c in df.columns if "选股日" in str(c)), None)
        if not date_col:
            continue
        sel = pd.to_datetime(df[date_col], errors="coerce").dt.date
        sub = df.loc[sel == sel_day].copy()
        if sub.empty:
            continue
        # 已命中覆盖该日的最新文件：按 variant 过滤后直接返回（空也返回，勿回退旧表）
        if variant and MEET_COL in sub.columns:
            meet = as_bool(sub[MEET_COL])
            if variant == VARIANT_MEET:
                sub = sub.loc[meet].copy()
            elif variant == VARIANT_NOT_MEET:
                sub = sub.loc[~meet].copy()
        sub.insert(0, "_来源文件", p.name)
        try:
            from utils.monitor_stock_type_filter import filter_dataframe

            sub = filter_dataframe(sub)
        except Exception:
            pass
        return sub.reset_index(drop=True)
    return pd.DataFrame()


def _pick_latest_fill(substr: str) -> Optional[Path]:
    """在成交明细中按买入/卖出侧取 latest（优先 sell_half）。"""
    files = _fill_files_for_side(substr)
    return files[0] if files else None


def _read_fill_rows(path: Path, trade_day: date) -> pd.DataFrame:
    try:
        d = pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        try:
            d = pd.read_csv(path, encoding="utf-8")
        except Exception:
            return pd.DataFrame()
    if "日期" not in d.columns:
        return pd.DataFrame()
    td = pd.to_datetime(d["日期"], errors="coerce").dt.date
    return d.loc[td == trade_day].copy()


def load_fills_for_trade_day(trade_day: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    buy_path = _pick_latest_fill("买入")
    sell_path = _pick_latest_fill("卖出")
    buys = _read_fill_rows(buy_path, trade_day) if buy_path else pd.DataFrame()
    sells = _read_fill_rows(sell_path, trade_day) if sell_path else pd.DataFrame()
    if not buys.empty and "方向" in buys.columns:
        buys = buys[buys["方向"].astype(str).str.contains("买", na=False)].copy()
    if not sells.empty and "方向" in sells.columns:
        sells = sells[sells["方向"].astype(str).str.contains("卖", na=False)].copy()
    return buys.reset_index(drop=True), sells.reset_index(drop=True)


def build_asof_export_frames(
    asof: date,
    *,
    reports: Dict[str, dict],
    window: int = DEFAULT_WINDOW,
) -> Dict[str, pd.DataFrame]:
    prev = prev_trading_day(asof)
    sug_rows: list[dict] = [
        {"项目": "开买日(asof)", "值": str(asof)},
        {"项目": "前一交易日(选股日口径)", "值": str(prev) if prev else ""},
        {"项目": "近窗天数", "值": int(window)},
        {"项目": "选股规则", "值": RULE_NAME},
        {"项目": "分组列", "值": MEET_COL},
    ]
    compare_rows: list[dict] = []

    for v in VARIANTS:
        rep = reports.get(v) or {}
        day = rep.get("day_view")
        if isinstance(day, dict):
            day = pd.DataFrame(day)
        elif not isinstance(day, pd.DataFrame):
            day = pd.DataFrame()
        roll = rep.get("roll_view")
        if isinstance(roll, dict):
            roll = pd.DataFrame(roll)
        elif not isinstance(roll, pd.DataFrame):
            roll = pd.DataFrame()

        day_mean = n = win_rate = n_sel = None
        if not day.empty and "start" in day.columns:
            t = day.copy()
            t["start"] = pd.to_datetime(t["start"], errors="coerce").dt.date.astype(str)
            row = t[t["start"] == str(asof)]
            if not row.empty:
                r0 = row.iloc[0]
                day_mean = r0.get("day_mean")
                n = r0.get("n")
                win_rate = r0.get("win_rate")
                n_sel = r0.get("n_sel")

        roll_mean = roll_win = roll_n = win_from = win_to = None
        if not roll.empty and "asof" in roll.columns:
            t = roll.copy()
            t["asof"] = pd.to_datetime(t["asof"], errors="coerce").dt.date.astype(str)
            rr = t[t["asof"] == str(asof)]
            if not rr.empty:
                r0 = rr.iloc[0]
                roll_mean = r0.get("roll_mean")
                roll_win = r0.get("roll_win")
                roll_n = r0.get("roll_n")
                win_from = r0.get("window_from")
                win_to = r0.get("window_to")

        sug_rows.append({"项目": f"— {v} —", "值": ""})
        sug_rows.append({"项目": f"{v}·当日票均", "值": day_mean})
        sug_rows.append({"项目": f"{v}·当日成交只数", "值": n})
        sug_rows.append({"项目": f"{v}·当日票胜率%", "值": win_rate})
        sug_rows.append({"项目": f"{v}·前一日选股只数", "值": n_sel})
        sug_rows.append({"项目": f"{v}·近窗日均", "值": roll_mean})
        sug_rows.append({"项目": f"{v}·近窗日胜率%", "值": roll_win})
        compare_rows.append(
            {
                "分组": v,
                "当日票均%": day_mean,
                "当日成交只数": n,
                "当日票胜率%": win_rate,
                "前一日选股只数": n_sel,
                "近窗日均%": roll_mean,
                "近窗日胜率%": roll_win,
            }
        )

    frames: Dict[str, pd.DataFrame] = {
        "前一日选股_满足": (
            load_selection_rows_for_day(prev, variant=VARIANT_MEET) if prev else pd.DataFrame()
        ),
        "前一日选股_不满足": (
            load_selection_rows_for_day(prev, variant=VARIANT_NOT_MEET) if prev else pd.DataFrame()
        ),
        "当日摘要与近窗": pd.DataFrame(sug_rows),
        "当日对比汇总": pd.DataFrame(compare_rows),
    }

    buys, sells = load_fills_for_trade_day(asof)
    frames["当天买入_全池"] = buys
    frames["当天卖出_全池"] = sells

    for v in VARIANTS:
        trades = (reports.get(v) or {}).get("trades") or {}
        trade_rows = trades.get(str(asof)) or []
        if trade_rows:
            detail = pd.DataFrame(trade_rows)
            rename = {
                "code": "代码",
                "name": "名称",
                "sel": "选股日",
                "start": "买入日",
                "ret": "收益率pct",
                "known_on": "清仓日",
                "lu_day": "涨停后第N日",
                "note": "备注",
                "fail_reason": "不满足的原因",
            }
            detail = detail.rename(columns={k: vv for k, vv in rename.items() if k in detail.columns})
            frames[f"{v}_开买收益明细"] = detail
        else:
            try:
                pool = load_pool(variant=v)
                detail = detail_for_start(pool, asof, asof=asof)
            except Exception:
                detail = pd.DataFrame()
            frames[f"{v}_开买收益明细"] = detail

    return frames


def export_asof_report(
    asof: date,
    out_path: Path,
    *,
    reports: Dict[str, dict],
    window: int = DEFAULT_WINDOW,
) -> Path:
    frames = build_asof_export_frames(asof, reports=reports, window=window)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def _is_code_col(name: object) -> bool:
        s = str(name or "")
        return s in ("代码", "股票代码", "code") or s.endswith("代码")

    def _stringify_codes(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or getattr(df, "empty", True):
            return df
        out = df.copy()
        for c in out.columns:
            if not _is_code_col(c):
                continue
            out[c] = [
                (code6(v) or "")
                if not (v is None or (isinstance(v, float) and pd.isna(v)))
                else ""
                for v in out[c].tolist()
            ]
        return out

    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        for name, df in frames.items():
            sheet = _sheet_name(name)
            if df is None or (hasattr(df, "empty") and df.empty):
                _empty_sheet_hint(name).to_excel(w, index=False, sheet_name=sheet)
                continue
            df = _stringify_codes(df)
            df.to_excel(w, index=False, sheet_name=sheet)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description="马总选股逻辑 · 满足条件对比监控")
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    ap.add_argument("--months", type=int, default=2)
    args = ap.parse_args()
    reps = build_all_reports(window=args.window, months=args.months)
    for v in VARIANTS:
        rep = reps[v]
        full = rep.get("full") or {}
        print(
            f"[{v}] 开买日={rep.get('n_start_days')} "
            f"票均={full.get('mean')} 胜率={full.get('win')}% "
            f"({rep.get('data_from')} → {rep.get('data_to')})"
        )
        if rep.get("empty_reason"):
            print(f"  提示: {rep['empty_reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
