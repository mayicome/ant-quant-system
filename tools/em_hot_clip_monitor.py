# -*- coding: utf-8 -*-
"""
东财热门夹档 · 策略表现跟踪（核心库）

用法:
  python tools/em_hot_clip_monitor.py
  python tools/em_hot_clip_monitor.py --window 10 --months 2
  python tools/em_hot_clip_monitor.py --variant 空头排列

目录:
  history_data/东财热门夹档/
    选股结果_*.xls          ← 共用（对照母集）
    空头排列/               ← 命中_空头规则
    排列并集/               ← 命中_并集规则

口径:
  - 横轴 / 聚合键 = 开买日（买入日）
  - 近窗 / 明细只用 asof 当时已了结的收益（known_on ≤ asof）
  - 未买入、未清仓样本不进窗内得分
"""
from __future__ import annotations

import argparse
import sys
import warnings
from datetime import date, datetime, timedelta, time as dt_time
from pathlib import Path
from typing import Any, Dict, List, Optional

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DIR = ROOT / "history_data" / "东财热门夹档"
RULE_NAME = "东财热门-无涨停-MA排列并集-含均线差对照"
DEFAULT_WINDOW = 10

# 回测结果分目录：选股文件仍在 DIR 根下；按票/成交明细在对应子目录
VARIANT_BEAR = "空头排列"
VARIANT_UNION = "排列并集"
VARIANTS = (VARIANT_BEAR, VARIANT_UNION)
VARIANT_HIT_COL = {
    VARIANT_BEAR: "命中_空头规则",
    VARIANT_UNION: "命中_并集规则",
}
# 兼容旧名（若外部脚本仍引用）
VARIANT_TRUE_BREAKTHROUGH = VARIANT_BEAR
VARIANT_NO_TRUE_BREAKTHROUGH = VARIANT_UNION


def variant_dir(variant: str) -> Path:
    return DIR / str(variant)


def _discover_under(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    if not root.exists():
        return out
    for pat in patterns:
        for p in sorted(root.glob(pat)):
            key = p.resolve()
            if key in seen:
                continue
            if p.suffix.lower() not in (".xlsx", ".xls"):
                continue
            if p.name.startswith("选股结果_"):
                continue
            seen.add(key)
            out.append(p)
    return out


def _discover_csv_under(root: Path, pattern: str) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.glob(pattern), key=lambda p: p.stat().st_mtime)


def code6(v) -> str:
    try:
        return str(int(float(v))).zfill(6)
    except Exception:
        s = "".join(c for c in str(v or "") if c.isdigit())
        return s.zfill(6)[-6:] if s else ""


def as_bool(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s.fillna(False)
    if s.dtype == object:
        return s.map(lambda x: str(x).strip().lower() in ("1", "true", "yes", "是", "y", "t"))
    return s.fillna(False).astype(bool)


def last_closed_trading_day(now: datetime | None = None) -> date:
    now = now or datetime.now()
    today = now.date()
    hi = today if now.time() >= dt_time(15, 0) else today - timedelta(days=1)
    days = trading_days_inclusive(hi - timedelta(days=21), hi)
    if days:
        return days[-1]
    d = hi
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def trading_days_inclusive(start: date, end: date, extra: list | None = None) -> list:
    days: list = []
    if start > end:
        return []
    try:
        from utils.trading_day import get_trading_dates_in_range_sorted

        days = list(get_trading_dates_in_range_sorted(start, end) or [])
    except Exception:
        days = []
    if not days:
        d = start
        while d <= end:
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
    extra_ok = {x for x in (extra or []) if start <= x <= end}
    return sorted(set(days) | extra_ok)


def _col_ts(df: pd.DataFrame, name: str) -> pd.Series | None:
    if name not in df.columns:
        return None
    return pd.to_datetime(df[name], errors="coerce")


def discover_trade_files(variant: Optional[str] = None) -> list[Path]:
    """按票收益文件：各日选股收益汇总*.xlsx 及日后 *_按票_*.xlsx。

    variant 指定时只读对应子目录；未指定时优先读各变体子目录，
    若子目录皆空则回退到 DIR 根（兼容旧布局）。
    """
    pats = (
        "各日选股收益汇总*.xlsx",
        "*_按票_*.xlsx",
        "*按票*.xlsx",
    )
    if variant:
        return _discover_under(variant_dir(variant), pats)
    out: list[Path] = []
    for v in VARIANTS:
        out.extend(_discover_under(variant_dir(v), pats))
    if out:
        return out
    return _discover_under(DIR, pats)


def discover_selection_files() -> list[Path]:
    """选股结果始终在 DIR 根目录。"""
    return sorted(
        list(DIR.glob(f"选股结果_{RULE_NAME}_*.xls"))
        + list(DIR.glob(f"选股结果_{RULE_NAME}_*.xlsx")),
        key=lambda p: p.stat().st_mtime,
    )


def discover_fill_files(variant: Optional[str] = None) -> list[Path]:
    """回测成交明细：与按票文件同目录（变体子目录或根目录回退）。"""
    if variant:
        return _discover_csv_under(variant_dir(variant), "回测成交明细_*.csv")
    out: list[Path] = []
    for v in VARIANTS:
        out.extend(_discover_csv_under(variant_dir(v), "回测成交明细_*.csv"))
    if out:
        return out
    return _discover_csv_under(DIR, "回测成交明细_*.csv")


def ensure_start(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        if df is not None and "start" not in df.columns:
            df = df.copy()
            df["start"] = pd.Series(dtype=object)
        return df
    d = df.copy()
    if "buy_on" in d.columns:
        b = pd.to_datetime(d["buy_on"], errors="coerce")
    else:
        b = _col_ts(d, "买入日")
        d["buy_on"] = b
    d["start"] = b.dt.date if b is not None else None
    return d


def pit_mask(df: pd.DataFrame, asof) -> pd.Series:
    if "known_on" not in df.columns:
        return pd.Series(True, index=df.index)
    k = pd.to_datetime(df["known_on"], errors="coerce")
    if not k.notna().any():
        return pd.Series(True, index=df.index)
    asof_ts = pd.Timestamp(asof).normalize()
    m = k.notna() & (k.dt.normalize() <= asof_ts)
    if "buy_on" in df.columns:
        b = pd.to_datetime(df["buy_on"], errors="coerce")
        if b.notna().any():
            m = m & (b.isna() | (b.dt.normalize() <= asof_ts))
    rem_col = "剩余持仓数量"
    if rem_col in df.columns:
        rem = pd.to_numeric(df[rem_col], errors="coerce").fillna(0)
        m = m & (rem <= 0)
    return m


def load_last_exit_map(variant: Optional[str] = None) -> dict:
    """(sel, code) → 清仓日。"""
    files = discover_fill_files(variant)
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
    out = {}
    for (sel, code), sub in all_d.groupby(["sel", "code"], sort=False):
        last_m = sub["_mtime"].max()
        sub = sub[sub["_mtime"] == last_m]
        zero = sub[sub["rem"].fillna(1) <= 0]
        if zero.empty:
            continue
        out[(sel, code)] = zero["td"].max()
    return out


def apply_realized_known_on(
    df: pd.DataFrame, *, variant: Optional[str] = None
) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    exit_map = load_last_exit_map(variant)
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
        done = ~still
    end = _col_ts(df, "end_date")
    known = ex
    need_fb = known.isna() & (~still) & done.fillna(False)
    if end is not None:
        known = known.where(~need_fb, end)
    # 无成交明细时：已清仓且有 end_date 也算已知
    if end is not None:
        no_exit = known.isna() & (~still)
        known = known.where(~no_exit, end)
    known = known.where(~still, pd.NaT)
    df["known_on"] = known
    return df


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

    out = out[out["ret"].notna() & out["sel"].notna() & out["code"].ne("")].copy()
    out = ensure_start(out)
    # 未触发买入的不进开买日曲线
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
    files = files or discover_trade_files(variant)
    if not files:
        raise FileNotFoundError(
            f"未找到按票/收益汇总文件: {variant_dir(variant) if variant else DIR}"
        )
    parts = []
    for p in files:
        try:
            parts.append(load_one(p))
        except Exception as e:
            print(f"跳过 {p.name}: {e}", flush=True)
    if not parts:
        raise RuntimeError(
            f"未能加载任何按票数据: {variant_dir(variant) if variant else DIR}"
        )
    df = pd.concat(parts, ignore_index=True)
    sort_cols = ["sel", "code"]
    if "_mtime" in df.columns:
        sort_cols.append("_mtime")
    df = df.sort_values(sort_cols).drop_duplicates(["sel", "code"], keep="last")
    if "_mtime" in df.columns:
        df = df.drop(columns=["_mtime"])
    if apply_known:
        df = apply_realized_known_on(df, variant=variant)
    try:
        from utils.monitor_stock_type_filter import apply_to_pool

        df = apply_to_pool(df)
    except Exception:
        pass
    return df.reset_index(drop=True)


def _hit_yes(v) -> bool:
    s = str(v or "").strip()
    return s in ("1", "true", "True", "是", "Y", "y", "yes")


def load_selection_counts(variant: str | None = None) -> pd.DataFrame:
    """选股日 → 入选只数；可按命中_*切片。"""
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
        if "选股日" not in df.columns:
            continue
        code_col = "股票代码" if "股票代码" in df.columns else ("代码" if "代码" in df.columns else None)
        use = df
        hit_col = VARIANT_HIT_COL.get(str(variant or ""), "") if variant else ""
        if hit_col and hit_col in df.columns:
            use = df.loc[df[hit_col].map(_hit_yes)].copy()
        if code_col:
            t = pd.DataFrame(
                {
                    "sel": pd.to_datetime(use["选股日"], errors="coerce").dt.date,
                    "code": use[code_col].map(code6),
                    "name": use["股票名称"] if "股票名称" in use.columns else "",
                }
            )
            try:
                from utils.monitor_stock_type_filter import filter_dataframe

                t = filter_dataframe(t, code_col="code", name_col="name")
            except Exception:
                pass
            t = t.dropna(subset=["sel"]).drop_duplicates(["sel", "code"])
        else:
            t = pd.DataFrame({"sel": pd.to_datetime(use["选股日"], errors="coerce").dt.date})
            t = t.dropna(subset=["sel"])
        t["_mtime"] = float(p.stat().st_mtime)
        parts.append(t)
    if not parts:
        return pd.DataFrame(columns=["sel", "n_sel"])
    all_d = pd.concat(parts, ignore_index=True)
    # 同日：用最新文件里的行
    if "code" in all_d.columns:
        all_d = all_d.sort_values(["sel", "code", "_mtime"]).drop_duplicates(
            ["sel", "code"], keep="last"
        )
        g = all_d.groupby("sel").size().reset_index(name="n_sel")
    else:
        g = all_d.groupby("sel").size().reset_index(name="n_sel")
    return g.sort_values("sel").reset_index(drop=True)


def prev_trading_day(d: date) -> Optional[date]:
    """开买日前一个交易日（通常即「前一日选股日」）。"""
    lo = d - timedelta(days=14)
    days = trading_days_inclusive(lo, d)
    days = [x for x in days if x < d]
    return days[-1] if days else None


def day_means(
    df: pd.DataFrame,
    asof=None,
    *,
    sel_counts: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """开买日 → 已实现等权票均 + n。asof 非空时做 PIT 过滤。

    ``n_sel`` = 前一交易日选股只数（当天准备跑策略的池子大小）。
    """
    d = ensure_start(df)
    if asof is not None:
        d = d[pit_mask(d, asof)].copy()
    d = d.dropna(subset=["start", "ret"])
    if d.empty:
        return pd.DataFrame(columns=["start", "day_mean", "n", "win_rate", "n_sel", "sel_day"])
    sc = sel_counts if sel_counts is not None else load_selection_counts()
    sel_map = {}
    if sc is not None and not sc.empty:
        for _, r in sc.iterrows():
            sel_map[r["sel"]] = int(r["n_sel"])
    rows = []
    for start, g in d.groupby("start"):
        rets = g["ret"]
        prev = prev_trading_day(start) if isinstance(start, date) else None
        rows.append(
            {
                "start": start,
                "day_mean": float(rets.mean()),
                "n": int(len(g)),
                "win_rate": float((rets > 0).mean() * 100),
                "sel_day": prev,
                "n_sel": sel_map.get(prev) if prev is not None else None,
            }
        )
    return pd.DataFrame(rows).sort_values("start").reset_index(drop=True)


def rolling_stats(
    day_df: pd.DataFrame,
    window: int = DEFAULT_WINDOW,
    asofs: list | None = None,
) -> pd.DataFrame:
    """近窗滚动统计。

    默认：仅在「有票均」的开买日上给点。
    若传入 ``asofs``（如连续交易日），则每个 asof 用 start≤asof 的最近
    ``window`` 个有票均开买日计算——票均为空的日子仍有近窗日均。
    """
    if day_df is None or day_df.empty:
        return pd.DataFrame(
            columns=["asof", "roll_mean", "roll_win", "roll_n", "roll_days", "window_from", "window_to"]
        )
    d = day_df.dropna(subset=["start", "day_mean"]).sort_values("start").reset_index(drop=True)
    if d.empty:
        return pd.DataFrame(
            columns=["asof", "roll_mean", "roll_win", "roll_n", "roll_days", "window_from", "window_to"]
        )
    w = max(1, int(window))
    axis = list(asofs) if asofs is not None else list(d["start"].tolist())
    starts = d["start"].tolist()
    means = d["day_mean"].astype(float).tolist()
    ns = d["n"].astype(int).tolist() if "n" in d.columns else [0] * len(d)

    rows = []
    j = -1  # d 中 start≤asof 的末下标
    for asof in axis:
        if asof is None:
            continue
        while j + 1 < len(starts) and starts[j + 1] <= asof:
            j += 1
        if j < 0:
            continue
        lo = max(0, j - w + 1)
        win_means = means[lo : j + 1]
        win_ns = ns[lo : j + 1]
        rows.append(
            {
                "asof": asof,
                "roll_mean": float(sum(win_means) / len(win_means)),
                "roll_win": float(sum(1 for x in win_means if x > 0) / len(win_means) * 100.0),
                "roll_n": int(sum(win_ns)),
                "roll_days": int(len(win_means)),
                "window_from": starts[lo],
                "window_to": starts[j],
            }
        )
    return pd.DataFrame(rows)


def cumulative_day_means(day_df: pd.DataFrame) -> pd.DataFrame:
    if day_df is None or day_df.empty:
        return pd.DataFrame(columns=["start", "cum_mean"])
    d = day_df.dropna(subset=["start", "day_mean"]).sort_values("start").copy()
    d["cum_mean"] = d["day_mean"].cumsum()
    return d[["start", "cum_mean"]].reset_index(drop=True)


def trigger_stats(df: pd.DataFrame, sel_counts: pd.DataFrame | None = None) -> pd.DataFrame:
    """选股日口径：入选只数 vs 实际买入只数（触发率）。"""
    d = ensure_start(df)
    bought = (
        d.dropna(subset=["sel", "start"])
        .groupby("sel")
        .size()
        .reset_index(name="n_buy")
    )
    sc = sel_counts if sel_counts is not None else load_selection_counts()
    if sc is None or sc.empty:
        out = bought.copy()
        out["n_sel"] = None
        out["trigger_pct"] = None
        return out
    out = sc.merge(bought, on="sel", how="left")
    out["n_buy"] = out["n_buy"].fillna(0).astype(int)
    out["trigger_pct"] = np.where(
        out["n_sel"].fillna(0) > 0,
        out["n_buy"] / out["n_sel"] * 100.0,
        np.nan,
    )
    return out.sort_values("sel").reset_index(drop=True)


def summary_stats(df: pd.DataFrame, asof=None) -> Dict[str, Any]:
    d = ensure_start(df)
    n_all = int(len(d))
    if asof is not None:
        known = d[pit_mask(d, asof)]
    else:
        known = d[d["known_on"].notna()] if "known_on" in d.columns else d
    n_known = int(len(known))
    n_open = n_all - n_known
    if known.empty:
        return {
            "n_all": n_all,
            "n_known": 0,
            "n_open": n_open,
            "mean": None,
            "win": None,
            "n_days": 0,
        }
    day = known.groupby("start")["ret"].mean()
    return {
        "n_all": n_all,
        "n_known": n_known,
        "n_open": n_open,
        "mean": round(float(known["ret"].mean()), 4),
        "win": round(float((known["ret"] > 0).mean() * 100), 2),
        "n_days": int(day.shape[0]),
        "day_mean": round(float(day.mean()), 4) if len(day) else None,
    }


def detail_for_start(df: pd.DataFrame, start_day, asof=None) -> pd.DataFrame:
    """某开买日的票明细（可选 PIT）。"""
    d = ensure_start(df)
    d = d[d["start"] == start_day].copy()
    if asof is not None:
        d = d[pit_mask(d, asof)].copy()
    cols = ["code", "name", "sel", "start", "ret", "known_on", "备注", "买入成交价", "lu_day"]
    cols = [c for c in cols if c in d.columns]
    out = d[cols].sort_values("ret", ascending=False) if cols else d
    return out.reset_index(drop=True)


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
        "trade_dir": str(variant_dir(variant)),
        "empty_reason": reason or f"目录无回测文件: {variant_dir(variant)}",
    }


def build_report(
    *,
    window: int = DEFAULT_WINDOW,
    months: int = 2,
    end: Optional[date] = None,
    variant: str = VARIANT_TRUE_BREAKTHROUGH,
) -> Dict[str, Any]:
    variant = str(variant or VARIANT_TRUE_BREAKTHROUGH).strip() or VARIANT_TRUE_BREAKTHROUGH
    last_close = end or last_closed_trading_day()
    files = discover_trade_files(variant)
    if not files:
        return _empty_report(
            window=window,
            months=months,
            last_close=last_close,
            variant=variant,
        )
    try:
        df = load_pool(files, variant=variant)
    except Exception as e:
        return _empty_report(
            window=window,
            months=months,
            last_close=last_close,
            variant=variant,
            reason=str(e),
        )
    sel_counts = load_selection_counts(variant=variant)

    # 全样本已了结日均（用于曲线底稿；滚动时再按 asof 过滤更严）
    # 曲线用「最终已知」的开买日票均：未完成的不进；n_sel=前一日选股只数
    day = day_means(df, asof=last_close, sel_counts=sel_counts)
    if day.empty:
        # 退回：凡有 known_on 的都算
        day = day_means(
            df[df["known_on"].notna()] if "known_on" in df.columns else df,
            sel_counts=sel_counts,
        )

    cum = cumulative_day_means(day)
    trig = trigger_stats(df, sel_counts)

    cut = last_close - timedelta(days=int(months * 31))
    day_view = day[day["start"] >= cut].copy() if not day.empty else day
    starts = sorted(day["start"].tolist()) if not day.empty else []
    # 近窗按连续交易日展开：票均为空的日子仍有「截至当日」的近窗日均
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
        "trade_dir": str(variant_dir(variant)),
        "empty_reason": "",
    }


def build_all_reports(
    *,
    window: int = DEFAULT_WINDOW,
    months: int = 2,
    end: Optional[date] = None,
) -> Dict[str, Dict[str, Any]]:
    """同时构建「空头排列」「排列并集」两套报告。"""
    return {
        v: build_report(window=window, months=months, end=end, variant=v)
        for v in VARIANTS
    }


def load_selection_rows_for_day(
    sel_day: date, *, variant: str | None = None
) -> pd.DataFrame:
    """从选股结果文件抽出某一选股日行；可按命中_*切片。"""
    files = sorted(
        discover_selection_files(),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in files:
        try:
            if p.suffix.lower() == ".xls":
                df = pd.read_excel(p, engine="xlrd")
            else:
                df = pd.read_excel(p)
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
        hit_col = VARIANT_HIT_COL.get(str(variant or ""), "") if variant else ""
        if hit_col and hit_col in sub.columns:
            sub = sub.loc[sub[hit_col].map(_hit_yes)].copy()
            if sub.empty:
                continue
        sub.insert(0, "_来源文件", p.name)
        try:
            from utils.monitor_stock_type_filter import filter_dataframe

            sub = filter_dataframe(sub)
        except Exception:
            pass
        return sub.reset_index(drop=True)
    return pd.DataFrame()


def load_fills_for_trade_day(
    trade_day: date, *, variant: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """某变体目录下，当天买入 / 卖出成交（取最新一份明细并按日期过滤）。"""
    files = discover_fill_files(variant)
    if not files:
        return pd.DataFrame(), pd.DataFrame()
    path = max(files, key=lambda p: p.stat().st_mtime)
    try:
        d = pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        try:
            d = pd.read_csv(path, encoding="utf-8")
        except Exception:
            return pd.DataFrame(), pd.DataFrame()
    if "日期" not in d.columns:
        return pd.DataFrame(), pd.DataFrame()
    td = pd.to_datetime(d["日期"], errors="coerce").dt.date
    day = d.loc[td == trade_day].copy()
    if day.empty:
        return pd.DataFrame(), pd.DataFrame()
    if "方向" in day.columns:
        buys = day[day["方向"].astype(str).str.contains("买", na=False)].copy()
        sells = day[day["方向"].astype(str).str.contains("卖", na=False)].copy()
    else:
        buys = day.copy()
        sells = pd.DataFrame()
    return buys.reset_index(drop=True), sells.reset_index(drop=True)


def _sheet_name(name: str) -> str:
    return str(name)[:31]


def _empty_sheet_hint(name: str) -> pd.DataFrame:
    return pd.DataFrame({"提示": [f"「{name}」无数据"]})


def build_asof_export_frames(
    asof: date,
    *,
    reports: Dict[str, dict],
    window: int = DEFAULT_WINDOW,
) -> Dict[str, pd.DataFrame]:
    """组装某开买日导出用的多 sheet（空头排列 + 排列并集同份）。"""
    prev = prev_trading_day(asof)
    sug_rows: list[dict] = [
        {"项目": "开买日(asof)", "值": str(asof)},
        {"项目": "前一交易日(选股日口径)", "值": str(prev) if prev else ""},
        {"项目": "近窗天数", "值": int(window)},
        {"项目": "策略", "值": RULE_NAME},
    ]

    compare_rows: list[dict] = []
    sel_counts = None
    for v in VARIANTS:
        sc = (reports.get(v) or {}).get("sel_counts")
        if isinstance(sc, pd.DataFrame) and not sc.empty:
            sel_counts = sc
            break
        if isinstance(sc, dict) and sc:
            sel_counts = pd.DataFrame(sc)
            break
    sel_map: dict = {}
    if sel_counts is not None and not getattr(sel_counts, "empty", True):
        t = sel_counts.copy()
        if "sel" in t.columns and "n_sel" in t.columns:
            t["sel"] = pd.to_datetime(t["sel"], errors="coerce").dt.date
            t["n_sel"] = pd.to_numeric(t["n_sel"], errors="coerce")
            t = t.dropna(subset=["sel", "n_sel"])
            sel_map = {r["sel"]: int(r["n_sel"]) for _, r in t.iterrows()}
    n_sel_shared = sel_map.get(prev) if prev is not None else None
    if n_sel_shared is None and prev is not None:
        try:
            sc2 = load_selection_counts()
            if sc2 is not None and not sc2.empty:
                sel_map = {r["sel"]: int(r["n_sel"]) for _, r in sc2.iterrows()}
                n_sel_shared = sel_map.get(prev)
        except Exception:
            pass

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
        if n_sel_shared is not None:
            n_sel = n_sel_shared

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
        sug_rows.append({"项目": f"{v}·近窗总笔数", "值": roll_n})
        sug_rows.append(
            {
                "项目": f"{v}·近窗区间",
                "值": f"{win_from} → {win_to}" if win_from is not None else "",
            }
        )

        compare_rows.append(
            {
                "变体": v,
                "当日票均%": day_mean,
                "当日成交只数": n,
                "当日票胜率%": win_rate,
                "前一日选股只数": n_sel,
                "近窗日均%": roll_mean,
                "近窗日胜率%": roll_win,
                "近窗总笔数": roll_n,
                "近窗起": str(win_from) if win_from is not None else "",
                "近窗止": str(win_to) if win_to is not None else "",
            }
        )

    frames: Dict[str, pd.DataFrame] = {
        "前一日选股_并集命中": (
            load_selection_rows_for_day(prev, variant=VARIANT_UNION) if prev else pd.DataFrame()
        ),
        "前一日选股_空头命中": (
            load_selection_rows_for_day(prev, variant=VARIANT_BEAR) if prev else pd.DataFrame()
        ),
        "当日摘要与近窗": pd.DataFrame(sug_rows),
        "当日对比汇总": pd.DataFrame(compare_rows),
    }

    for v in VARIANTS:
        buys, sells = load_fills_for_trade_day(asof, variant=v)
        frames[f"{v}_当天买入"] = buys
        frames[f"{v}_当天卖出"] = sells

        # 开买收益明细：优先用报告里已序列化的 trades；否则从磁盘按票重算
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
            }
            detail = detail.rename(columns={k: vv for k, vv in rename.items() if k in detail.columns})
            frames[f"{v}_开买收益明细"] = detail
        else:
            try:
                pool = load_pool(discover_trade_files(v), variant=v)
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
    """导出某开买日 Excel（空头排列 + 排列并集同份）。"""
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
            try:
                ws = w.sheets[sheet]
                for j, col in enumerate(df.columns, 1):
                    if not _is_code_col(col):
                        continue
                    for i in range(2, len(df) + 2):
                        cell = ws.cell(i, j)
                        cell.number_format = "@"
                        if cell.value not in (None, ""):
                            cell.value = code6(cell.value) or str(cell.value)
            except Exception:
                pass
    return out_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="东财热门夹档表现摘要")
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    ap.add_argument("--months", type=int, default=2)
    ap.add_argument(
        "--variant",
        choices=list(VARIANTS) + ["全部"],
        default="全部",
        help="空头排列 / 排列并集 / 全部（默认）",
    )
    args = ap.parse_args(argv)
    variants = list(VARIANTS) if args.variant == "全部" else [args.variant]
    for v in variants:
        rep = build_report(window=args.window, months=args.months, variant=v)
        full = rep["full"]
        print(f"=== {v} ===")
        print(f"目录: {rep.get('trade_dir') or DIR}")
        print(f"文件: {rep['files'] or '(无)'}")
        if rep.get("empty_reason") and not rep["starts"]:
            print(f"空: {rep['empty_reason']}")
            continue
        print(f"开买日: {rep['data_from']} → {rep['data_to']}（{rep['n_start_days']} 天）")
        print(
            f"已了结: n={full['n_known']} 票均={full['mean']} 胜率={full['win']}% "
            f"开买日均={full.get('day_mean')} 未完成={full['n_open']}"
        )
        lr = rep.get("latest_roll")
        if lr:
            print(
                f"近窗{rep['window']}: asof={lr['asof']} 日均={lr['roll_mean']:.3f}% "
                f"日胜率={lr['roll_win']:.1f}% 笔数={lr['roll_n']} "
                f"（{lr['window_from']}→{lr['window_to']}）"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
