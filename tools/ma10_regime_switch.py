# -*- coding: utf-8 -*-
"""
马总 MA10 · 近窗风格切换打分

用法:
  python tools/ma10_regime_switch.py
  python tools/ma10_regime_switch.py --window 10
  python tools/ma10_regime_switch.py --asof 2026-07-31 --window 12

逻辑:
  - 默认核 CORE: B ∩ 市值≤91亿（6/7月双正交集）
  - 七月风 JULY: 价≤18 或 市值≤54（小票/低价）
  - 六月风 JUNE: 市值170~353 ∩ MA5>MA10（中大盘多头）
  - 黑名单: (MA5≤MA10且MA10≥MA20) 或 RS20>40% —— 永不建议

用最近 N 个交易开始日、且「当时已能看到」的票均收益打分（实际了结日 ≤ asof：
卖完或到期清仓；未卖完不计入，避免前视）。交易开始日 = 当日剩余池实际开买日：
上一选股日池 − 已触发后、当天实际开买的票（选股规则「次日MA10」+ 挂单窗 1 日）。
在 CORE / 七月整包(CORE∪七月) / 六月整包(CORE∪六月) 三者中选近窗日均最高者；
若领先次佳不足 edge，则默认 CORE。

规则失效对照（只打分、不进决策）:
  - 仅B: 上MA10 ∩ B − 黑名单（比 CORE 宽，不限市值≤91）
  - 非B: 上MA10 ∩ ¬B − 黑名单
  读法: CORE vs 仅B 看市值门槛；仅B vs 非B 看 B 门是否还在分离收益。
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import date, datetime, timedelta, time as dt_time
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DIR = ROOT / "history_data" / "马总选股逻辑"
OUT_JSON = DIR / "_ma10_regime_switch.json"
OUT_TXT = DIR / "_ma10_regime_switch.txt"

# thresholds (aligned with prior analysis)
MV_CORE = 91.0
MV_JULY_SMALL = 54.0
PX_JULY = 18.0
MV_JUNE_LO, MV_JUNE_HI = 170.0, 353.0
# B：近5/10日有动能，近20日尚未透支
RS5_B, RS10_B, RS20_B = 0.135, 0.06, 0.25
RS20_BLACK = 0.40
# satellite must beat the other by this much (pct points) to activate
LEAD_EDGE = 0.35
# 实盘/监控挂单窗：选股日 T+1 起连续这么多交易日（次日MA10 选股 → 只挂次日）
ENTRY_WINDOW = 1

# 与 install_ma_zong_ma10_regime_rules.py 安装的规则名一致
RULE_UI = {
    "CORE_ONLY": "马总-MA10核-CORE",
    "CORE_PLUS_JULY": "马总-MA10核-七月风",
    "CORE_PLUS_JUNE": "马总-MA10核-六月风",
}


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


def discover_files() -> list[Path]:
    """Prefer 收盘上MA10；优先固定 ``*_latest``，否则回退时间戳文件。

    文件名随买卖组合变化：旧版 `ma10-单点`，当前默认 `ma10-sell_half-单点`。
    有 latest 时只读 latest（不再拼多份旧文件）。
    """
    for pat in (
        "各日选股收益汇总_日线-ma10-sell_half*-单点_按票_*收盘上MA10_latest.xlsx",
        "各日选股收益汇总_日线-ma10*-单点_按票_*收盘上MA10_latest.xlsx",
    ):
        hits = sorted(DIR.glob(pat))
        if hits:
            return hits
    seen: set[Path] = set()
    out: list[Path] = []
    for pat in (
        "各日选股收益汇总_日线-ma10*-单点_按票_*_收盘上MA10.xlsx",
        "各日选股收益汇总_日线-ma10-单点_按票_*_收盘上MA10.xlsx",
    ):
        for p in sorted(DIR.glob(pat)):
            if "_latest" in p.name:
                continue
            key = p.resolve()
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
    if out:
        return out
    for pat in (
        "各日选股收益汇总_日线-ma10-sell_half*-单点_按票_latest.xlsx",
        "各日选股收益汇总_日线-ma10*-单点_按票_latest.xlsx",
    ):
        hits = sorted(DIR.glob(pat))
        if hits:
            return hits
    for pat in (
        "各日选股收益汇总_日线-ma10*-单点_按票_*.xlsx",
        "各日选股收益汇总_日线-ma10-单点_按票_*.xlsx",
    ):
        for p in sorted(DIR.glob(pat)):
            if "收盘上MA10" in p.name or "_latest" in p.name:
                continue
            if "已完成" in p.name:
                continue
            key = p.resolve()
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
    return out


def discover_all_selected_files() -> list[Path]:
    """已完成按票（含未上MA10）。不含 收盘上MA10 切片。优先 latest。"""
    for pat in (
        "各日选股收益汇总_日线-ma10-sell_half*-单点_按票_已完成_latest.xlsx",
        "各日选股收益汇总_日线-ma10*-单点_按票_已完成_latest.xlsx",
    ):
        hits = sorted(DIR.glob(pat))
        if hits:
            return hits
    seen: set[Path] = set()
    out: list[Path] = []
    for pat in (
        "各日选股收益汇总_日线-ma10*-单点_按票_*_已完成.xlsx",
        "各日选股收益汇总_日线-ma10-单点_按票_*_已完成.xlsx",
    ):
        for p in sorted(DIR.glob(pat)):
            if "收盘上MA10" in p.name or "_latest" in p.name:
                continue
            key = p.resolve()
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
    return out


def load_one(path: Path, filter_ma10: bool | None = None) -> pd.DataFrame:
    df = pd.read_excel(path)
    df["sel"] = pd.to_datetime(df["选股日"], errors="coerce").dt.date
    df["code"] = df["代码"].map(code6) if "代码" in df.columns else ""
    df["ret"] = pd.to_numeric(df["收益率pct"], errors="coerce")
    for c in [
        "MA5",
        "MA10",
        "MA20",
        "买入成交价",
        "流通市值_亿",
        "近5日RS",
        "近10日RS",
        "近20日RS",
        "sel_close",
    ]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # 收盘上MA10 文件已筛过；全样本对比时 filter_ma10=False
    if filter_ma10 is None:
        filter_ma10 = "收盘上MA10" not in path.name
    if filter_ma10:
        if "sel_close" in df.columns and "MA10" in df.columns:
            df = df[df["sel_close"] > df["MA10"]].copy()
        elif "MA10" in df.columns and "买入成交价" in df.columns:
            pass

    df = df[df["ret"].notna() & df["sel"].notna()].copy()
    if "股票名称" in df.columns:
        df["name"] = df["股票名称"].astype(str)
    elif "名称" in df.columns:
        df["name"] = df["名称"].astype(str)
    else:
        df["name"] = ""
    df = attach_known_on(df)
    df["px"] = pd.to_numeric(df.get("买入成交价"), errors="coerce")
    df["mv"] = pd.to_numeric(df.get("流通市值_亿"), errors="coerce")
    df["rs5"] = pd.to_numeric(df.get("近5日RS"), errors="coerce")
    df["rs10"] = pd.to_numeric(df.get("近10日RS"), errors="coerce")
    df["rs20"] = pd.to_numeric(df.get("近20日RS"), errors="coerce")
    df["src"] = path.name
    try:
        df["_mtime"] = float(path.stat().st_mtime)
    except OSError:
        df["_mtime"] = 0.0
    return df


def load_pool(
    files: list[Path] | None = None,
    *,
    filter_ma10: bool | None = None,
    apply_known: bool = True,
) -> pd.DataFrame:
    files = files or discover_files()
    if not files:
        raise FileNotFoundError(f"未找到 ma10 按票文件: {DIR}")
    parts = [load_one(p, filter_ma10=filter_ma10) for p in files]
    df = pd.concat(parts, ignore_index=True)
    # 同一选股日+代码：文件更新时间更晚的覆盖（今天 sell_half 覆盖 8-16 的 ma10-单点）
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
    return df.reset_index(drop=True)


def last_closed_trading_day(now: datetime | None = None) -> date:
    """最近一个已经收盘的交易日。当天 15:00 前不算今天。"""
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
    """[start, end] 内交易日；日历不可用时退回周一～周五。"""
    days: list = []
    if start > end:
        return []
    try:
        from utils.trading_day import get_trading_dates_in_range_sorted

        days = list(get_trading_dates_in_range_sorted(start, end))
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


def attach_known_on(df: pd.DataFrame) -> pd.DataFrame:
    """先挂买入日；真正的「收益可知日」在 load_pool 里按实际了结日回填。"""
    df = df.copy()
    df["buy_on"] = _col_ts(df, "买入日")
    if "known_on" not in df.columns:
        df["known_on"] = pd.NaT
    return ensure_start(df)


def ensure_start(df: pd.DataFrame) -> pd.DataFrame:
    """交易开始日 ``start`` = 买入日（当日剩余池里实际开买）。"""
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


def dedupe_by_trade_start(df: pd.DataFrame) -> pd.DataFrame:
    """同一买入日(start)+代码只保留一行。

    日线准备按选股日独立仿真时，多日入选的票可能在同一开买日各有一行
    （收益相同、选股日不同）。监控横轴按开买日计票均/只数时必须去重，
    否则会重复计入。保留最早选股日（与入场窗「先入池」一致）。
    """
    d = ensure_start(df)
    if d is None or d.empty:
        return d
    if "start" not in d.columns or "code" not in d.columns:
        return d
    sort_cols = ["start", "code"]
    if "sel" in d.columns:
        sort_cols.append("sel")
    if "_mtime" in d.columns:
        sort_cols.append("_mtime")
    d = d.sort_values(sort_cols, kind="mergesort")
    return d.drop_duplicates(["start", "code"], keep="first").reset_index(drop=True)


def pit_mask(df: pd.DataFrame, asof) -> pd.Series:
    """asof 收市时该行整段收益是否已知（实际了结日 ≤ asof）。无日期列时全 True。"""
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


def add_flags(df: pd.DataFrame) -> pd.DataFrame:
    d = ensure_start(df.copy())
    d["B"] = (d["rs5"] > RS5_B) & (d["rs10"] > RS10_B) & (d["rs20"] < RS20_B)
    d["core"] = d["B"] & (d["mv"] <= MV_CORE)
    d["july"] = (d["px"] <= PX_JULY) | (d["mv"] <= MV_JULY_SMALL)
    d["june"] = d["mv"].between(MV_JUNE_LO, MV_JUNE_HI) & (d["MA5"] > d["MA10"])
    d["black"] = ((d["MA5"] <= d["MA10"]) & (d["MA10"] >= d["MA20"])) | (d["rs20"] > RS20_BLACK)
    # actionable sleeves (exclude blacklist)
    d["core_ok"] = d["core"] & ~d["black"]
    d["july_sat"] = d["july"] & ~d["black"]  # satellite candidates (not requiring B)
    d["june_sat"] = d["june"] & ~d["black"]
    # recommended trade sets when satellite active = core ∪ (satellite ∩ above already)
    d["pack_july"] = (d["core_ok"] | (d["july_sat"] & d["B"])) & ~d["black"]
    d["pack_june"] = (d["core_ok"] | d["june_sat"]) & ~d["black"]
    # diagnostic only (rule decay): 仅B / 非B；池子已是上MA10
    d["b_only"] = d["B"] & ~d["black"]
    d["non_b"] = (~d["B"]) & ~d["black"]
    return d


def sleeve_stats(g: pd.DataFrame, mask: pd.Series) -> dict:
    sub = g[mask.fillna(False)]
    if sub.empty:
        return {
            "n": 0,
            "n_days": 0,
            "mean": None,
            "day_mean": None,
            "win": None,
            "pos_days": 0,
        }
    key = "start" if "start" in sub.columns else "sel"
    day = sub.dropna(subset=[key]).groupby(key)["ret"].mean()
    return {
        "n": int(len(sub)),
        "n_days": int(day.shape[0]),
        "mean": round(float(sub["ret"].mean()), 4),
        "day_mean": round(float(day.mean()), 4),
        "win": round(float((sub["ret"] > 0).mean() * 100), 2),
        "pos_days": int((day > 0).sum()),
    }


SLEEVE_COLS = (
    "core_ok",
    "july_sat",
    "june_sat",
    "pack_july",
    "pack_june",
    "b_only",
    "non_b",
)


def drop_blacklist(df: pd.DataFrame) -> pd.DataFrame:
    """监控/对照口径：池内不含黑名单（仅B+非B = 上MA10）。"""
    if df is None or df.empty or "black" not in df.columns:
        return df
    return df[~df["black"].fillna(False)].copy()


def trade_start_sleeve_means(df: pd.DataFrame) -> pd.DataFrame:
    """交易开始日(买入日) → 各袖套票均收益率%。

    当日点 = 近 ENTRY_WINDOW 个选股日池减去已触发后、当天实际开买的票。
    回测买入必落在选股日 T+1 起的窗口内，按买入日分组即该口径。
    上MA10 / 对照均已排除黑名单，故仅B + 非B = 上MA10只数。
    输出列名 ``sel`` 与横轴交易日对齐（此处是交易开始日，不是选股日）。
    """
    cols = ["baseline", *SLEEVE_COLS]
    d = drop_blacklist(dedupe_by_trade_start(df))
    d = d[d["start"].notna()].copy()
    rows = []
    for start, g in d.groupby("start"):
        row: dict = {"sel": start}
        row["baseline"] = float(g["ret"].mean()) if len(g) else None
        row["baseline_n"] = int(len(g))
        for c in cols[1:]:
            if c not in g.columns:
                row[c] = None
                row[f"{c}_n"] = 0
                continue
            sub = g[g[c].fillna(False)]
            row[c] = float(sub["ret"].mean()) if len(sub) else None
            row[f"{c}_n"] = int(len(sub))
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=["sel", "baseline", "baseline_n", *cols[1:]])
    return pd.DataFrame(rows).sort_values("sel").reset_index(drop=True)


def _as_date(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    t = pd.to_datetime(v, errors="coerce")
    if pd.isna(t):
        return None
    return t.date()


def discover_open_book_files() -> list[Path]:
    """含未完成样本的按票，用来算日历日持仓盯市。优先 latest。"""
    for pat in (
        "各日选股收益汇总_日线-ma10-sell_half*-单点_按票_latest.xlsx",
        "各日选股收益汇总_日线-ma10*-单点_按票_latest.xlsx",
    ):
        hits = sorted(DIR.glob(pat))
        if hits:
            return hits
    out: list[Path] = []
    for pat in (
        "各日选股收益汇总_日线-ma10*-单点_按票_*.xlsx",
        "各日选股收益汇总_日线-ma10-单点_按票_*.xlsx",
    ):
        for p in sorted(DIR.glob(pat)):
            if "已完成" in p.name or "收盘上MA10" in p.name or "_latest" in p.name:
                continue
            if p not in out:
                out.append(p)
    return out


def merge_open_book(df: pd.DataFrame) -> pd.DataFrame:
    files = discover_open_book_files()
    if not files:
        return df
    extra = add_flags(load_pool(files))
    out = pd.concat([df, extra], ignore_index=True)
    sort_cols = ["sel", "code"]
    if "_mtime" in out.columns:
        sort_cols.append("_mtime")
    out = out.sort_values(sort_cols).drop_duplicates(["sel", "code"], keep="last")
    if "_mtime" in out.columns:
        out = out.drop(columns=["_mtime"])
    return out.reset_index(drop=True)


def load_last_exit_map() -> dict:
    """(sel, code) → 清仓日（交易后持仓首次/最后变为 0 的卖出日）。未卖完的不进表。"""
    files = _fill_files_for_side("卖出")
    if not files:
        return {}
    parts = []
    for p in files:
        try:
            d = pd.read_csv(p, encoding="utf-8-sig")
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


def apply_realized_known_on(df: pd.DataFrame) -> pd.DataFrame:
    """收益可知日 = 实际清仓日。未卖完不算已知；无卖出明细时已完成样本退回 end_date。"""
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


def exit_day_sleeve_means(
    df: pd.DataFrame,
    days: list,
    *,
    progress=None,
) -> pd.DataFrame:
    """每个交易日：当天了结（卖完或到期清仓）的票，整段收益率pct 等权平均。

    未卖完的不计入；收益挂在了结日，不挂选股日，也不用未平仓盯市。
    """
    from collections import defaultdict

    empty_cols = ["sel", "baseline", *SLEEVE_COLS]
    if df is None or df.empty or not days:
        return pd.DataFrame(columns=empty_cols)
    if progress:
        progress("按了结日汇总已完成收益…")
    exit_map = load_last_exit_map()
    flag_cols = [c for c in SLEEVE_COLS if c in df.columns]
    flag_vals = {c: df[c].fillna(False).to_numpy() for c in flag_cols}

    codes = df["code"].tolist() if "code" in df.columns else [""] * len(df)
    sels = df["sel"].tolist()
    rets = pd.to_numeric(df["ret"], errors="coerce") if "ret" in df.columns else pd.Series(np.nan, index=df.index)
    end_s = df["end_date"] if "end_date" in df.columns else df.get("known_on")
    rem_s = df["剩余持仓数量"] if "剩余持仓数量" in df.columns else None
    done_s = None
    if "样本完成" in df.columns:
        done_s = as_bool(df["样本完成"])

    buckets = {c: defaultdict(list) for c in ["baseline", *flag_cols]}
    n = len(df)
    for i in range(n):
        r = rets.iloc[i]
        if r is None or (isinstance(r, float) and not np.isfinite(r)):
            continue
        try:
            if pd.isna(r):
                continue
        except Exception:
            continue
        still = False
        if rem_s is not None:
            try:
                still = float(rem_s.iloc[i] or 0) > 0
            except Exception:
                still = False
        if still:
            continue
        key = (sels[i], codes[i])
        ex = exit_map.get(key)
        if ex is None:
            done = bool(done_s.iloc[i]) if done_s is not None else True
            if not done:
                continue
            ex = _as_date(end_s.iloc[i] if end_s is not None else None)
        if ex is None:
            continue
        rr = float(r)
        buckets["baseline"][ex].append(rr)
        for col in flag_cols:
            if flag_vals[col][i]:
                buckets[col][ex].append(rr)

    rows = []
    for d in days:
        row: dict = {"sel": d}
        base = buckets["baseline"].get(d) or []
        row["baseline"] = float(np.mean(base)) if base else None
        row["baseline_n"] = int(len(base))
        for col in flag_cols:
            xs = buckets[col].get(d) or []
            row[col] = float(np.mean(xs)) if xs else None
            row[f"{col}_n"] = int(len(xs))
        rows.append(row)
    return pd.DataFrame(rows)


def decide(scores: dict, edge: float = LEAD_EDGE) -> dict:
    """三选一：CORE / 七月整包 / 六月整包（按近窗日均，需领先次佳 ≥ edge）。"""
    core = scores["core_ok"]["day_mean"]
    pack_jy = scores["pack_july"]["day_mean"]
    pack_jn = scores["pack_june"]["day_mean"]

    cands: list[tuple[str, float, str]] = []
    if core is not None:
        cands.append(("CORE_ONLY", float(core), "CORE"))
    if pack_jy is not None:
        cands.append(("CORE_PLUS_JULY", float(pack_jy), "七月整包"))
    if pack_jn is not None:
        cands.append(("CORE_PLUS_JUNE", float(pack_jn), "六月整包"))

    rules = {
        "CORE_ONLY": [
            "必选: 上MA10",
            f"必选: B = RS5>{RS5_B*100:g}% 且 RS10>{RS10_B*100:g}% 且 RS20<{RS20_B*100:g}%",
            "必选: 流通市值 ≤ 91亿",
            "禁止: MA5≤MA10 且 MA10≥MA20",
            "禁止: RS20 > 40%",
        ],
        "CORE_PLUS_JULY": [
            "默认核: B + 市值≤91亿",
            "卫星(七月风): 价≤18 或 市值≤54（建议仍要求 B，避免过宽）",
            "禁止: MA5≤MA10 且 MA10≥MA20；RS20>40%",
        ],
        "CORE_PLUS_JUNE": [
            "默认核: B + 市值≤91亿",
            "卫星(六月风): 市值170~353亿 且 MA5>MA10",
            "禁止: MA5≤MA10 且 MA10≥MA20；RS20>40%",
        ],
    }

    if not cands:
        return {
            "decision": "CORE_ONLY",
            "reason": "近窗无 CORE/整包样本 → 默认 CORE",
            "rules": rules["CORE_ONLY"],
            "core_day_mean": core,
        }

    cands.sort(key=lambda x: x[1], reverse=True)
    best_dec, best_v, best_lab = cands[0]

    if len(cands) == 1:
        decision = best_dec
        reason = f"近窗仅{best_lab}有样本 day={best_v:+.2f}%"
    else:
        second_dec, second_v, second_lab = cands[1]
        lead = best_v - second_v
        if lead < float(edge):
            # 差距不足：不切换风格，落回 CORE（有样本时）
            if core is not None:
                decision = "CORE_ONLY"
                reason = (
                    f"三包接近：最佳{best_lab} {best_v:+.2f}% / 次佳{second_lab} {second_v:+.2f}% "
                    f"差 {lead:.2f}pp < {edge:.2f} → 默认 CORE"
                )
            else:
                decision = best_dec
                reason = (
                    f"{best_lab} {best_v:+.2f}% 略优于{second_lab} {second_v:+.2f}% "
                    f"(差{lead:.2f}<{edge:.2f}且无CORE样本) → 仍选{best_lab}"
                )
        else:
            decision = best_dec
            reason = (
                f"{best_lab}近窗 {best_v:+.2f}% 领先次佳{second_lab} {second_v:+.2f}% "
                f"≥ {edge:.2f}pp"
            )

    return {
        "decision": decision,
        "reason": reason,
        "rules": rules[decision],
        "core_day_mean": core,
    }


def decay_hint(scores: dict, edge: float = LEAD_EDGE) -> str:
    """Human-readable rule-decay read from CORE / 仅B / 非B day means."""
    core = scores["core_ok"]["day_mean"]
    b_only = scores["b_only"]["day_mean"]
    non_b = scores["non_b"]["day_mean"]
    if b_only is None and non_b is None:
        return "近窗无仅B/非B样本，无法判断 B 门是否失效"
    if b_only is not None and non_b is not None:
        gap = b_only - non_b
        if non_b >= b_only:
            return f"警惕: 非B日均 {non_b:+.2f}% ≥ 仅B {b_only:+.2f}% → B 门近窗失去分离力"
        if gap < edge:
            return f"注意: 仅B仅领先非B {gap:+.2f}pp（<{edge:.2f}）→ B 门优势偏弱"
    if core is not None and b_only is not None:
        size_gap = core - b_only
        if size_gap < -edge:
            return f"注意: CORE 日均弱于仅B {abs(size_gap):.2f}pp → 市值≤91 近窗在拖累"
        if (
            abs(size_gap) < 0.20
            and non_b is not None
            and b_only is not None
            and (b_only - non_b) >= edge
        ):
            return (
                f"B 门仍有效（仅B领先非B），但市值门槛近窗几乎无增量"
                f"(CORE-仅B={size_gap:+.2f}pp)"
            )
    if b_only is not None and non_b is not None:
        return f"正常: 仅B {b_only:+.2f}% > 非B {non_b:+.2f}%（差 {b_only - non_b:+.2f}pp）"
    if b_only is not None:
        return f"仅有仅B样本 day={b_only:+.2f}%，缺非B对照"
    return f"仅有非B样本 day={non_b:+.2f}%，缺仅B对照"


def score_window(df: pd.DataFrame, sels: list, asof=None) -> dict:
    """近窗打分。横轴/分组用交易开始日（买入日），不是选股日。

    每个交易开始日 = 近 ``ENTRY_WINDOW`` 个选股日池 − 已触发后、当天实际开买的票
    （ENTRY_WINDOW=1 时即「昨日选股 − 已买」）。
    默认按 win 末日 asof 做时点过滤：只计入当时已实现的收益。
    """
    if not sels:
        raise ValueError("sels 为空")
    if asof is None:
        asof = max(sels)
    w0 = drop_blacklist(dedupe_by_trade_start(df))
    want = {_as_date(x) for x in sels}
    want.discard(None)
    w0 = w0[w0["start"].map(_as_date).isin(want)].copy()
    n_pool = int(len(w0))
    mask = pit_mask(w0, asof)
    w = w0[mask].copy()
    n_known = int(len(w))
    base_mask = pd.Series(True, index=w.index)
    return {
        "sel_from": str(min(sels)),
        "sel_to": str(max(sels)),
        "asof": str(asof),
        "n_sel_days": len(sels),
        "n_all": n_known,
        "n_pool": n_pool,
        "n_unknown": n_pool - n_known,
        "baseline": sleeve_stats(w, base_mask),
        "core_ok": sleeve_stats(w, w["core_ok"]),
        "july_sat": sleeve_stats(w, w["july_sat"]),
        "june_sat": sleeve_stats(w, w["june_sat"]),
        "pack_july": sleeve_stats(w, w["pack_july"]),
        "pack_june": sleeve_stats(w, w["pack_june"]),
        "b_only": sleeve_stats(w, w["b_only"]),
        "non_b": sleeve_stats(w, w["non_b"]),
        "black": sleeve_stats(w, w["black"]) if "black" in w.columns else sleeve_stats(w, pd.Series(False, index=w.index)),
    }


def rolling_report(df: pd.DataFrame, window: int) -> list[dict]:
    df = dedupe_by_trade_start(df)
    sels = sorted(x for x in df["start"].dropna().unique())
    rows = []
    for i in range(window - 1, len(sels)):
        win = sels[i - window + 1 : i + 1]
        sc = score_window(df, win, asof=win[-1])
        dec = decide(sc)
        rows.append(
            {
                "asof": str(sels[i]),
                "decision": dec["decision"],
                "july_day": sc["july_sat"]["day_mean"],
                "june_day": sc["june_sat"]["day_mean"],
                "core_day": sc["core_ok"]["day_mean"],
                "pack_july_day": sc["pack_july"]["day_mean"],
                "pack_june_day": sc["pack_june"]["day_mean"],
                "b_only_day": sc["b_only"]["day_mean"],
                "non_b_day": sc["non_b"]["day_mean"],
                "july_n": sc["july_sat"]["n"],
                "june_n": sc["june_sat"]["n"],
                "core_n": sc["core_ok"]["n"],
                "b_only_n": sc["b_only"]["n"],
                "non_b_n": sc["non_b"]["n"],
            }
        )
    return rows


def parse_asof(s: str | None) -> date | None:
    if not s:
        return None
    return pd.to_datetime(s).date()


def format_report(payload: dict) -> str:
    lines = []
    lines.append("=" * 60)
    lines.append("马总 MA10 · 近窗风格切换")
    lines.append("=" * 60)
    lines.append(f"数据覆盖交易开始日: {payload['data_from']} → {payload['data_to']}  (共 {payload['n_sel_days']} 天)")
    lines.append(f"评价窗口: 最近 {payload['window']} 个交易开始日  ({payload['window_from']} → {payload['window_to']})")
    lines.append(f"asof: {payload['asof']}")
    sc0 = payload.get("scores") or {}
    n_pool = sc0.get("n_pool")
    n_unk = sc0.get("n_unknown")
    if n_pool is not None:
        lines.append(
            f"时点过滤: 只计入实际了结日 ≤ asof 的已实现收益"
            f"（窗内 {n_pool} 票，当时已知 {sc0.get('n_all', 0)}，尚未实现 {n_unk}）"
        )
    lines.append("")
    lines.append("【近窗得分】日均等权% / 票均% / n  （当时已实现）")
    for key, lab in [
        ("baseline", "上MA10全样本"),
        ("core_ok", "默认核 CORE (B+市值≤91, 去黑名单)"),
        ("july_sat", "七月风卫星 (价≤18或市值≤54)"),
        ("june_sat", "六月风卫星 (市值170~353且MA5>MA10)"),
        ("pack_july", "CORE并(七月交B)"),
        ("pack_june", "CORE并六月卫星"),
    ]:
        s = payload["scores"][key]
        dm = "NA" if s["day_mean"] is None else f"{s['day_mean']:+.2f}%"
        m = "NA" if s["mean"] is None else f"{s['mean']:+.2f}%"
        lines.append(f"  {lab:36s}  day={dm:>8s}  mean={m:>8s}  n={s['n']:4d}  days={s['n_days']}")
    lines.append("")
    lines.append("【规则失效对照】仅打分、不进决策  |  CORE 含于 仅B  ;  非B 互斥")
    for key, lab in [
        ("b_only", "仅B (上MA10交B去黑名单)"),
        ("non_b", "非B (上MA10交非B去黑名单)"),
    ]:
        s = payload["scores"][key]
        dm = "NA" if s["day_mean"] is None else f"{s['day_mean']:+.2f}%"
        m = "NA" if s["mean"] is None else f"{s['mean']:+.2f}%"
        lines.append(f"  {lab:36s}  day={dm:>8s}  mean={m:>8s}  n={s['n']:4d}  days={s['n_days']}")
    hint = payload.get("decay_hint") or decay_hint(payload["scores"])
    lines.append(f"读法: {hint}")
    lines.append("")
    rule_name = payload.get("rule_ui") or RULE_UI.get(payload["decision"], "")
    lines.append(f"【本周建议】{payload['decision']}")
    if rule_name:
        lines.append(f"选股页勾选: {rule_name}")
    lines.append(f"原因: {payload['reason']}")
    lines.append("执行规则:")
    for r in payload["rules"]:
        lines.append(f"  - {r}")
    if payload.get("history_tail"):
        lines.append("")
        lines.append("【滚动决策轨迹】最近几次 asof → decision")
        for h in payload["history_tail"]:
            lines.append(
                f"  {h['asof']}  {h['decision']:16s}  "
                f"core={h['core_day']}  pack_july={h.get('pack_july_day')}  "
                f"pack_june={h.get('pack_june_day')}  "
                f"b_only={h.get('b_only_day')}  non_b={h.get('non_b_day')}"
            )
    lines.append("")
    lines.append(f"JSON: {payload['out_json']}")
    lines.append("=" * 60)
    return "\n".join(lines)


DEC_LABEL_ZH = {
    "CORE_ONLY": "CORE",
    "CORE_PLUS_JULY": "+七月风",
    "CORE_PLUS_JUNE": "+六月风",
}


def _prev_trading_day(asof: date) -> date | None:
    days = trading_days_inclusive(asof - timedelta(days=14), asof)
    prev = [d for d in days if d < asof]
    return prev[-1] if prev else None


def _score_cell(s: dict | None) -> str:
    if not isinstance(s, dict):
        return ""
    dm, m, n = s.get("day_mean"), s.get("mean"), s.get("n")
    dm_s = "NA" if dm is None else f"{dm:+.2f}%"
    m_s = "NA" if m is None else f"{m:+.2f}%"
    return f"day={dm_s}  mean={m_s}  n={n if n is not None else 0}"


def load_selection_rows_for_day(sel_day: date) -> pd.DataFrame:
    """从选股结果文件抽出某一选股日全部行（优先较新文件）。"""
    uni = load_selection_rows_for_days({sel_day})
    return uni.reset_index(drop=True) if uni is not None and not uni.empty else pd.DataFrame()


def _series_first(df: pd.DataFrame, *names: str) -> pd.Series:
    for n in names:
        if n in df.columns:
            return pd.to_numeric(df[n], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def selection_frame_with_flags(df: pd.DataFrame) -> pd.DataFrame:
    """选股结果行 → 上MA10 / B / 黑名单标记（字段优先用「_选股日」对照列）。"""
    if df is None or df.empty:
        return pd.DataFrame()
    d = df.copy()
    code_col = (
        "股票代码"
        if "股票代码" in d.columns
        else ("代码" if "代码" in d.columns else None)
    )
    d["code"] = d[code_col].map(code6) if code_col else ""
    date_col = next((c for c in d.columns if "选股日" in str(c)), None)
    if date_col is None:
        return pd.DataFrame()
    d["sel"] = pd.to_datetime(d[date_col], errors="coerce").dt.date
    close = _series_first(d, "收盘价_选股日", "收盘价", "sel_close")
    ma10 = _series_first(d, "MA10_选股日", "MA10")
    d["MA5"] = _series_first(d, "MA5_选股日", "MA5")
    d["MA10"] = ma10
    d["MA20"] = _series_first(d, "MA20_选股日", "MA20")
    d["px"] = close
    d["mv"] = _series_first(d, "流通市值_亿_选股日", "流通市值_亿")
    d["rs5"] = _series_first(d, "近5日RS_选股日", "近5日RS")
    d["rs10"] = _series_first(d, "近10日RS_选股日", "近10日RS")
    d["rs20"] = _series_first(d, "近20日RS_选股日", "近20日RS")
    d["above_ma10"] = close.notna() & ma10.notna() & (close > ma10)
    d = add_flags(d)
    d = d[d["sel"].notna() & d["code"].ne("")].copy()
    return d


def selection_day_counts(
    sel_days: list[date] | set[date] | None = None,
) -> pd.DataFrame:
    """选股日 → 全部入选 / 上MA10 / 仅B / 非B 只数。

    收盘后即可统计「当日选股」；与图上量柱对齐：
    - 全部入选：该日选股去黑名单
    - 上MA10：收盘>MA10 且去黑名单（仅B+非B）
    - 仅B / 非B：在上MA10 内划分
    """
    if sel_days is not None:
        want = {d for d in sel_days if d is not None}
        raw = load_selection_rows_for_days(want) if want else pd.DataFrame()
    else:
        # 扫全部次日MA10选股文件的选股日
        files = sorted(
            list(DIR.glob("选股结果_马总选股逻辑-次日MA10_*.xls"))
            + list(DIR.glob("选股结果_马总选股逻辑-次日MA10_*.xlsx"))
            + list(DIR.glob("选股结果_马总选股逻辑*.xls"))
            + list(DIR.glob("选股结果_马总选股逻辑*.xlsx")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        days: set[date] = set()
        for p in files:
            try:
                if p.suffix.lower() == ".xls":
                    df0 = pd.read_excel(p, engine="xlrd", usecols=lambda c: "选股日" in str(c))
                else:
                    df0 = pd.read_excel(p, usecols=lambda c: "选股日" in str(c))
            except Exception:
                continue
            col = next((c for c in df0.columns if "选股日" in str(c)), None)
            if not col:
                continue
            for v in pd.to_datetime(df0[col], errors="coerce").dropna():
                days.add(pd.Timestamp(v).date())
        raw = load_selection_rows_for_days(days) if days else pd.DataFrame()

    cols = [
        "sel",
        "all_sel_n",
        "baseline_n",
        "b_only_n",
        "non_b_n",
        "below_ma10_n",
    ]
    if raw is None or raw.empty:
        return pd.DataFrame(columns=cols)

    d = selection_frame_with_flags(raw)
    if d.empty:
        return pd.DataFrame(columns=cols)

    rows = []
    for sel, g0 in d.groupby("sel"):
        g = g0.drop_duplicates("code", keep="first")
        g = drop_blacklist(g)
        n_all = int(len(g))
        above = g[g["above_ma10"].fillna(False)]
        n_ma = int(len(above))
        n_b = int(above["b_only"].fillna(False).sum()) if "b_only" in above.columns else 0
        n_nb = int(above["non_b"].fillna(False).sum()) if "non_b" in above.columns else 0
        rows.append(
            {
                "sel": sel,
                "all_sel_n": n_all,
                "baseline_n": n_ma,
                "b_only_n": n_b,
                "non_b_n": n_nb,
                "below_ma10_n": max(n_all - n_ma, 0),
            }
        )
    if not rows:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame(rows).sort_values("sel").reset_index(drop=True)
    return out


def latest_selection_file_day() -> date | None:
    """选股结果文件中最晚的选股日（次日MA10 优先）。"""
    files = sorted(
        list(DIR.glob("选股结果_马总选股逻辑-次日MA10_*.xls"))
        + list(DIR.glob("选股结果_马总选股逻辑-次日MA10_*.xlsx")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        files = sorted(
            list(DIR.glob("选股结果_马总选股逻辑*.xls"))
            + list(DIR.glob("选股结果_马总选股逻辑*.xlsx")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    latest: date | None = None
    for p in files[:12]:
        try:
            if p.suffix.lower() == ".xls":
                df = pd.read_excel(p, engine="xlrd", usecols=lambda c: "选股日" in str(c))
            else:
                df = pd.read_excel(p, usecols=lambda c: "选股日" in str(c))
        except Exception:
            continue
        col = next((c for c in df.columns if "选股日" in str(c)), None)
        if not col or df.empty:
            continue
        s = pd.to_datetime(df[col], errors="coerce").dropna()
        if s.empty:
            continue
        d = pd.Timestamp(s.max()).date()
        if latest is None or d > latest:
            latest = d
    return latest


def load_selection_rows_for_days(sel_days: set[date] | set) -> pd.DataFrame:
    """合并多个选股日的选股行；同(选股日,代码)以较新文件为准。"""
    want = {d for d in sel_days if d is not None}
    if not want:
        return pd.DataFrame()
    files = sorted(
        list(DIR.glob("选股结果_马总选股逻辑*.xls"))
        + list(DIR.glob("选股结果_马总选股逻辑*.xlsx")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    parts: list[pd.DataFrame] = []
    covered: set[date] = set()
    for p in files:
        missing = want - covered
        if not missing:
            break
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
        mask = sel.isin(missing)
        if not mask.any():
            continue
        sub = df.loc[mask].copy()
        sub["_选股日"] = sel.loc[mask].values
        sub["_来源文件"] = p.name
        parts.append(sub)
        covered |= set(sel.loc[mask].dropna().unique().tolist())
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True)
    code_col = next(
        (c for c in ("股票代码", "代码", "code") if c in out.columns),
        None,
    )
    if code_col and "_选股日" in out.columns:
        out["_code6"] = out[code_col].map(code6)
        out = out[out["_code6"].astype(str).str.len() == 6]
        out = out.drop_duplicates(subset=["_选股日", "_code6"], keep="first")
    try:
        from utils.monitor_stock_type_filter import filter_dataframe

        out = filter_dataframe(out)
    except Exception:
        pass
    return out.reset_index(drop=True)


def active_selection_days_for_trade_day(
    trade_day: date, *, entry_window: int = ENTRY_WINDOW
) -> list[date]:
    """开买日 T 对应的「近 entry_window 个选股日」= T 之前连续 entry_window 个交易日。

    选股日 S 的挂单窗为 S 的下一交易日起连续 entry_window 天，故 T 落在窗内
    当且仅当 S ∈ 前 entry_window 个交易日。
    """
    w = max(1, int(entry_window))
    lo = trade_day - timedelta(days=w * 3 + 14)
    days = trading_days_inclusive(lo, trade_day)
    prev = [d for d in days if d < trade_day]
    return prev[-w:] if prev else []


def _order_fill_files(files: list[Path]) -> list[Path]:
    """mtime 新→旧；同批优先 sell_half（当前默认回测）。"""
    files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
    prefer = [p for p in files if "sell_half" in p.name]
    others = [p for p in files if p not in prefer]
    return prefer + others


def _fill_files_for_side(side_zh: str) -> list[Path]:
    """成交明细：优先 ``*_latest.csv``（sell_half > 其它），否则回退时间戳文件。"""
    side = str(side_zh or "").strip()
    for pat in (
        f"回测成交明细_日线-ma10-sell_half*{side}_latest.csv",
        f"回测成交明细_日线-ma10*{side}_latest.csv",
    ):
        hits = sorted(DIR.glob(pat), key=lambda p: p.stat().st_mtime, reverse=True)
        if hits:
            return hits
    return _order_fill_files(list(DIR.glob(f"回测成交明细_日线-ma10*{side}_*.csv")))


def _read_fill_csv(path: Path) -> pd.DataFrame | None:
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        try:
            return pd.read_csv(path, encoding="utf-8")
        except Exception:
            return None


def load_triggered_codes_before(
    asof: date,
    *,
    active_sel_days: set[date] | set | None = None,
) -> set[str]:
    """asof 之前已触达 MA10（已买入）的代码。

    扫描多份买入明细（增量回测最新文件可能不含更早日期），取并集；
    若仍空则退回按票表 start < asof。
    """
    want_sel = set(active_sel_days) if active_sel_days is not None else None
    triggered: set[str] = set()

    buy_files = _fill_files_for_side("买入")
    for path in buy_files:
        d = _read_fill_csv(path)
        if d is None or d.empty or "日期" not in d.columns or "代码" not in d.columns:
            continue
        td = pd.to_datetime(d["日期"], errors="coerce").dt.date
        m = td < asof
        if "方向" in d.columns:
            m = m & d["方向"].astype(str).str.contains("买", na=False)
        if want_sel is not None and "选股日" in d.columns:
            sel = pd.to_datetime(d["选股日"], errors="coerce").dt.date
            m = m & sel.isin(want_sel)
        triggered |= {code6(x) for x in d.loc[m, "代码"].tolist() if code6(x)}
        # latest 已是完整快照，不必再并旧文件
        if "_latest" in path.name:
            break

    if triggered:
        return {c for c in triggered if c}

    # 退回按票
    try:
        pool = ensure_start(load_pool(discover_files(), apply_known=False))
    except Exception:
        return set()
    if pool is None or pool.empty or "start" not in pool.columns:
        return set()
    m = pool["start"].notna() & (pool["start"] < asof)
    if want_sel is not None and "sel" in pool.columns:
        m = m & pool["sel"].isin(want_sel)
    if "code" in pool.columns:
        triggered |= {code6(x) for x in pool.loc[m, "code"].tolist() if code6(x)}
    return {c for c in triggered if c}


def build_remaining_entry_pool(
    trade_day: date,
    *,
    entry_window: int = ENTRY_WINDOW,
) -> pd.DataFrame:
    """当日剩余参与池 = 近 entry_window 选股日并集 − 窗内 asof 前已买入。

    entry_window=1 时即「昨日选股 − 已买」。多日重复出现的代码取最早选股日。
    """
    active = active_selection_days_for_trade_day(trade_day, entry_window=entry_window)
    if not active:
        return pd.DataFrame()
    uni = load_selection_rows_for_days(set(active))
    if uni is None or uni.empty:
        return pd.DataFrame()

    code_col = next(
        (c for c in ("股票代码", "代码", "code") if c in uni.columns),
        None,
    )
    if not code_col:
        return pd.DataFrame()
    if "_code6" not in uni.columns:
        uni = uni.copy()
        uni["_code6"] = uni[code_col].map(code6)
    if "_选股日" not in uni.columns:
        date_col = next((c for c in uni.columns if "选股日" in str(c)), None)
        if not date_col:
            return pd.DataFrame()
        uni = uni.copy()
        uni["_选股日"] = pd.to_datetime(uni[date_col], errors="coerce").dt.date

    uni = uni[uni["_code6"].astype(str).str.len() == 6].copy()
    uni = uni.sort_values("_选股日")
    # 每票最早选股日
    first = uni.drop_duplicates(subset=["_code6"], keep="first").copy()

    active_set = set(active)
    triggered = load_triggered_codes_before(trade_day, active_sel_days=active_set)
    rem = first[~first["_code6"].isin(triggered)].copy()

    rem.insert(0, "_开买日", str(trade_day))
    rem.insert(1, "_挂单窗选股日起", str(active[0]))
    rem.insert(2, "_挂单窗选股日止", str(active[-1]))
    rem.insert(3, "_入口窗口天数", int(entry_window))
    if "选股日" in rem.columns:
        rem["选股日"] = rem["_选股日"].map(lambda d: d.isoformat() if d else "")
    else:
        rem.insert(4, "选股日", rem["_选股日"].map(lambda d: d.isoformat() if d else ""))

    # 元信息 sheet 行也可在调用方写；此处返回池清单
    drop_meta = [c for c in ("_code6",) if c in rem.columns]
    # 保留 _选股日 便于核对，或改为选股日列已有
    if "_选股日" in rem.columns and "选股日" in rem.columns:
        rem = rem.drop(columns=["_选股日"])
    rem = rem.drop(columns=drop_meta, errors="ignore")
    return rem.reset_index(drop=True)


def load_fills_for_trade_day(trade_day: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """当天买入 / 卖出成交。

    优先读 ``*_latest.csv``（完整快照）；无 latest 再按 mtime 扫时间戳文件
    （优先 sell_half）。买入按代码去重（保留最早选股日）。
    """
    buy_files = _fill_files_for_side("买入")
    sell_files = _fill_files_for_side("卖出")

    def _day_from_files(files: list[Path], side_zh: str) -> pd.DataFrame:
        for path in files:
            d = _read_fill_csv(path)
            if d is None or d.empty or "日期" not in d.columns:
                continue
            td = pd.to_datetime(d["日期"], errors="coerce").dt.date
            sub = d.loc[td == trade_day].copy()
            if sub.empty:
                continue
            if "方向" in sub.columns:
                sub = sub[sub["方向"].astype(str).str.contains(side_zh, na=False)]
            if sub.empty:
                continue
            sub = sub.copy()
            sub.insert(0, "_来源文件", path.name)
            return sub.reset_index(drop=True)
        return pd.DataFrame()

    buys = _day_from_files(buy_files, "买")
    sells = _day_from_files(sell_files, "卖")
    if not buys.empty and "代码" in buys.columns:
        buys = buys.copy()
        buys["_code6"] = buys["代码"].map(code6)
        if "选股日" in buys.columns:
            buys["_sel"] = pd.to_datetime(buys["选股日"], errors="coerce")
            buys = buys.sort_values(["_code6", "_sel"], kind="mergesort")
        else:
            buys = buys.sort_values(["_code6"], kind="mergesort")
        buys = buys.drop_duplicates(subset=["_code6"], keep="first")
        buys = buys.drop(columns=[c for c in ("_code6", "_sel") if c in buys.columns])
        buys = buys.reset_index(drop=True)
    return buys, sells


def build_asof_export_frames(
    asof: date,
    *,
    hist_row: dict,
    daily_row: pd.Series | None = None,
    window: int = 10,
    pool: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    """组装某 asof（交易开始日）导出用的多个 DataFrame。"""
    prev = _prev_trading_day(asof)
    active = active_selection_days_for_trade_day(asof, entry_window=ENTRY_WINDOW)
    rem_pool = build_remaining_entry_pool(asof, entry_window=ENTRY_WINDOW)
    union_n = 0
    if active:
        uni = load_selection_rows_for_days(set(active))
        if uni is not None and not uni.empty:
            code_col = next(
                (c for c in ("股票代码", "代码", "code") if c in uni.columns),
                None,
            )
            if code_col:
                union_n = len({code6(x) for x in uni[code_col].tolist() if code6(x)})
    triggered_n = max(0, union_n - int(len(rem_pool)))

    sc = hist_row.get("scores") or {}

    sug_rows: list[dict] = [
        {"项目": "asof(交易开始日/开买日)", "值": str(asof)},
        {
            "项目": "当日剩余参与池口径",
            "值": (
                f"近{ENTRY_WINDOW}个选股日"
                f"{'（昨日选股）' if ENTRY_WINDOW == 1 else '并集'}"
                f" − asof前已买入；"
                f"选股日 {active[0] if active else '—'} → {active[-1] if active else '—'}"
            ),
        },
        {"项目": "当日剩余参与池只数", "值": int(len(rem_pool))},
        {
            "项目": "近选股日并集只数" if ENTRY_WINDOW != 1 else "昨日选股只数",
            "值": int(union_n),
        },
        {"项目": "窗内已触发剔除只数", "值": int(triggered_n)},
        {"项目": "前一交易日(仅参考)", "值": str(prev) if prev else ""},
        {
            "项目": "口径说明",
            "值": (
                "图上「全部入选/上MA10」是当天实际开买成交的票均，"
                "不是剩余挂单池只数；二者不可直接对比。"
            ),
        },
        {
            "项目": "当日建议",
            "值": DEC_LABEL_ZH.get(hist_row.get("decision"), hist_row.get("decision")),
        },
        {
            "项目": "本周执行",
            "值": DEC_LABEL_ZH.get(
                str(hist_row.get("exec_decision") or ""),
                str(hist_row.get("exec_decision") or "—"),
            ),
        },
        {"项目": "选股页勾选", "值": hist_row.get("rule_ui") or ""},
        {"项目": "原因", "值": hist_row.get("reason") or ""},
        {"项目": "失效读法", "值": hist_row.get("decay_hint") or ""},
        {
            "项目": "近窗打分",
            "值": f"{hist_row.get('window_from')} → {hist_row.get('window_to')} (window={window})",
        },
        {
            "项目": "窗内票数/已知/未实现",
            "值": f"{hist_row.get('n_pool')}/{hist_row.get('n_known')}/{hist_row.get('n_unknown')}",
        },
    ]
    for key, lab in [
        ("core_ok", "近窗·CORE"),
        ("pack_july", "近窗·七月整包"),
        ("pack_june", "近窗·六月整包"),
        ("b_only", "近窗·仅B"),
        ("non_b", "近窗·非B"),
        ("baseline", "近窗·上MA10"),
        ("july_sat", "近窗·七月卫星"),
        ("june_sat", "近窗·六月卫星"),
    ]:
        sug_rows.append({"项目": lab, "值": _score_cell(sc.get(key))})

    if daily_row is not None and len(daily_row):
        sug_rows.append({"项目": "—", "值": "— 当日实际开买票均（非整池）—"})
        for lab, col in [
            ("当日开买·全部入选", "all_sel"),
            ("当日开买·全部入选只数", "all_sel_n"),
            ("当日开买·上MA10", "baseline"),
            ("当日开买·上MA10只数", "baseline_n"),
            ("当日开买·CORE", "core_ok"),
            ("当日开买·七月整包", "pack_july"),
            ("当日开买·六月整包", "pack_june"),
            ("当日开买·仅B", "b_only"),
            ("当日开买·非B", "non_b"),
            ("当日开买·执行包", "exec_pack"),
        ]:
            if col in daily_row.index:
                sug_rows.append({"项目": lab, "值": daily_row.get(col)})
    suggest_df = pd.DataFrame(sug_rows)

    buys_df, sells_df = load_fills_for_trade_day(asof)

    day_trades = pd.DataFrame()
    sleeve_sum = pd.DataFrame()
    if pool is None:
        try:
            pool = drop_blacklist(
                dedupe_by_trade_start(add_flags(load_pool(discover_files())))
            )
        except Exception:
            pool = pd.DataFrame()
    elif not pool.empty and "core_ok" not in pool.columns:
        pool = drop_blacklist(dedupe_by_trade_start(add_flags(pool)))
    elif not pool.empty:
        pool = dedupe_by_trade_start(pool)

    if pool is not None and not pool.empty and "start" in pool.columns:
        g = pool[pool["start"] == asof].copy()
        if not g.empty:
            keep = [
                c
                for c in (
                    "code",
                    "name",
                    "代码",
                    "名称",
                    "sel",
                    "start",
                    "ret",
                    "买入成交价",
                    "px",
                    "mv",
                    "rs5",
                    "rs10",
                    "rs20",
                    "MA5",
                    "MA10",
                    "MA20",
                    "备注",
                    "剩余持仓数量",
                )
                if c in g.columns
            ]
            rename = {
                "code": "代码",
                "name": "名称",
                "sel": "选股日",
                "start": "买入日",
                "ret": "收益率pct",
                "px": "买入成交价",
                "mv": "流通市值_亿",
                "rs5": "近5日RS",
                "rs10": "近10日RS",
                "rs20": "近20日RS",
            }
            base = g[keep].rename(columns={k: v for k, v in rename.items() if k in keep})
            base = base.loc[:, ~base.columns.duplicated()]
            flag_frame = pd.DataFrame(
                {
                    "上MA10": ["是"] * len(g),
                    "B": g["B"].fillna(False).map(lambda x: "是" if bool(x) else "否"),
                    "CORE": g["core_ok"].fillna(False).map(lambda x: "是" if bool(x) else "否"),
                    "七月卫星": g["july_sat"].fillna(False).map(lambda x: "是" if bool(x) else "否"),
                    "六月卫星": g["june_sat"].fillna(False).map(lambda x: "是" if bool(x) else "否"),
                    "六月整包": g["pack_june"].fillna(False).map(lambda x: "是" if bool(x) else "否"),
                    "七月整包": g["pack_july"].fillna(False).map(lambda x: "是" if bool(x) else "否"),
                    "仅B": g["b_only"].fillna(False).map(lambda x: "是" if bool(x) else "否"),
                    "非B": g["non_b"].fillna(False).map(lambda x: "是" if bool(x) else "否"),
                }
            )
            day_trades = pd.concat(
                [base.reset_index(drop=True), flag_frame.reset_index(drop=True)], axis=1
            )

            rows = []
            for lab, col in [
                ("上MA10", None),
                ("B", "B"),
                ("CORE", "core_ok"),
                ("仅B", "b_only"),
                ("非B", "non_b"),
                ("七月整包", "pack_july"),
                ("六月整包", "pack_june"),
                ("七月卫星", "july_sat"),
                ("六月卫星", "june_sat"),
            ]:
                if col is None:
                    sub = g
                elif col not in g.columns:
                    continue
                else:
                    sub = g[g[col].fillna(False)]
                rows.append(
                    {
                        "袖套": lab,
                        "只数": int(len(sub)),
                        "票均收益率pct": float(sub["ret"].mean()) if len(sub) else None,
                        "胜率%": float((sub["ret"] > 0).mean() * 100) if len(sub) else None,
                    }
                )
            sleeve_sum = pd.DataFrame(rows)

    return {
        "当日剩余参与池": rem_pool,
        "当日建议与近窗": suggest_df,
        "当天买入": buys_df,
        "当天卖出": sells_df,
        "当日开买收益明细": day_trades,
        "当日袖套收益汇总": sleeve_sum,
    }


def export_asof_report(
    asof: date,
    out_path: Path,
    *,
    hist_row: dict,
    daily_row: pd.Series | None = None,
    window: int = 10,
    pool: pd.DataFrame | None = None,
) -> Path:
    """导出某 asof 的多 sheet Excel。"""
    frames = build_asof_export_frames(
        asof,
        hist_row=hist_row,
        daily_row=daily_row,
        window=window,
        pool=pool,
    )
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
            sheet = str(name)[:31]
            if df is None or (hasattr(df, "empty") and df.empty):
                pd.DataFrame({"提示": [f"「{name}」无数据"]}).to_excel(
                    w, index=False, sheet_name=sheet
                )
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


def main(argv=None):
    ap = argparse.ArgumentParser(description="MA10 近窗风格切换打分")
    ap.add_argument("--window", type=int, default=10, help="近窗交易开始日数量，默认10")
    ap.add_argument("--asof", type=str, default=None, help="评价截止日 YYYY-MM-DD，默认最近已收盘交易日")
    ap.add_argument("--edge", type=float, default=LEAD_EDGE, help="卫星领先阈值(百分点)，默认0.35")
    ap.add_argument("--files", nargs="*", default=None, help="指定按票xlsx；默认自动发现收盘上MA10")
    args = ap.parse_args(argv)

    files = [Path(x) for x in args.files] if args.files else discover_files()
    print("加载:", *[p.name for p in files], sep="\n  ")
    df = add_flags(load_pool(files))
    starts = sorted(x for x in df["start"].dropna().unique())
    if len(starts) < args.window:
        raise SystemExit(f"交易开始日不足 window={args.window}，当前仅 {len(starts)} 天")

    asof = parse_asof(args.asof) or last_closed_trading_day()
    axis = trading_days_inclusive(starts[0], asof, extra=list(starts))
    sels_use = [d for d in axis if d <= asof]
    if len(sels_use) < args.window:
        raise SystemExit(f"asof={asof} 之前不足 {args.window} 个交易日（仅{len(sels_use)}）")
    win = sels_use[-args.window :]
    sc = score_window(df, win, asof=asof)
    dec = decide(sc, edge=float(args.edge))

    hist = []
    for i in range(args.window - 1, len(sels_use)):
        wsel = sels_use[i - args.window + 1 : i + 1]
        asof_i = wsel[-1]
        sc_i = score_window(df, wsel, asof=asof_i)
        dec_i = decide(sc_i, edge=float(args.edge))
        hist.append(
            {
                "asof": str(asof_i),
                "decision": dec_i["decision"],
                "july_day": sc_i["july_sat"]["day_mean"],
                "june_day": sc_i["june_sat"]["day_mean"],
                "core_day": sc_i["core_ok"]["day_mean"],
                "pack_july_day": sc_i["pack_july"]["day_mean"],
                "pack_june_day": sc_i["pack_june"]["day_mean"],
                "b_only_day": sc_i["b_only"]["day_mean"],
                "non_b_day": sc_i["non_b"]["day_mean"],
                "july_n": sc_i["july_sat"]["n"],
                "june_n": sc_i["june_sat"]["n"],
                "core_n": sc_i["core_ok"]["n"],
                "b_only_n": sc_i["b_only"]["n"],
                "non_b_n": sc_i["non_b"]["n"],
            }
        )
    hint = decay_hint(sc, edge=float(args.edge))
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": [p.name for p in files],
        "data_from": str(starts[0]),
        "data_to": str(starts[-1]),
        "n_sel_days": len(starts),
        "asof": str(asof),
        "window": args.window,
        "window_from": str(win[0]),
        "window_to": str(win[-1]),
        "edge": args.edge,
        "scores": sc,
        "decision": dec["decision"],
        "rule_ui": RULE_UI.get(dec["decision"], ""),
        "reason": dec["reason"],
        "rules": dec["rules"],
        "decay_hint": hint,
        "history_tail": hist[-8:],
        "out_json": str(OUT_JSON),
    }
    text = format_report(payload)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    OUT_TXT.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
