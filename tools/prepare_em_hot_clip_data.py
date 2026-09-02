# -*- coding: utf-8 -*-
"""东财热门夹档 · 准备数据（缺口补选股 + 空头/并集双变体买/卖回测）

选股规则：东财热门-无涨停-MA排列并集-含均线差对照
买入：开盘夹档+Cond2 clip(2,4)（strategy_e6d1b97b）
卖出：综合卖出（strategy_e9c83928）

目录：
  history_data/东财热门夹档/
    选股结果_*.xls
    空头排列/   ← 命中_空头规则=是
    排列并集/   ← 命中_并集规则=是

用法:
  python tools/prepare_em_hot_clip_data.py
  python tools/prepare_em_hot_clip_data.py --days 15
  python tools/prepare_em_hot_clip_data.py --start 2026-06-01
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "history_data" / "东财热门夹档"
RULE_NAME = "东财热门-无涨停-MA排列并集-含均线差对照"
BUY_STRATEGY_ID = "strategy_e6d1b97b"
SELL_STRATEGY_ID = "strategy_e9c83928"
VARIANT_BEAR = "空头排列"
VARIANT_UNION = "排列并集"
VARIANTS = (VARIANT_BEAR, VARIANT_UNION)
VARIANT_HIT_COL = {
    VARIANT_BEAR: "命中_空头规则",
    VARIANT_UNION: "命中_并集规则",
}
BUY_SIZING_MODE = "clip_equity"
BUY_CLIP_L = 2
BUY_CLIP_U = 4

DEFAULT_DAYS = 15
REFRESH_LOOKBACK = 2
SELL_HOLD = 2
BUY_FROM_T1 = True
SELL_CLEAR_TIME = "14:56:00"
INITIAL_CASH = 100_000_000.0

ProgressCb = Optional[Callable[[str], None]]


def _log(msg: str, progress: ProgressCb = None) -> None:
    print(msg, flush=True)
    if progress:
        try:
            progress(msg)
        except Exception:
            pass


def _parse_d(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s).strip()[:10])
    except Exception:
        return None


def last_closed_trading_day(now: datetime | None = None) -> date:
    now = now or datetime.now()
    today = now.date()
    hi = today if now.time() >= dt_time(15, 0) else today - timedelta(days=1)
    try:
        from utils.trading_day import get_trading_dates_in_range_sorted

        days = list(get_trading_dates_in_range_sorted(hi - timedelta(days=21), hi) or [])
        if days:
            return days[-1]
    except Exception:
        pass
    d = hi
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def variant_dir(variant: str) -> Path:
    return OUT_DIR / str(variant)


def _read_sel_days_from_xlsx(path: Path) -> List[date]:
    import pandas as pd

    try:
        df = pd.read_excel(path)
    except Exception:
        return []
    if "选股日" not in df.columns or df.empty:
        return []
    s = pd.to_datetime(df["选股日"], errors="coerce").dropna()
    out = []
    for v in s:
        try:
            out.append(pd.Timestamp(v).date())
        except Exception:
            continue
    return out


def latest_backtest_sel_day() -> Optional[date]:
    """两套变体回测汇总里最晚的选股日。"""
    latest: Optional[date] = None
    for v in VARIANTS:
        d = variant_dir(v)
        if not d.is_dir():
            continue
        for p in d.glob("各日选股收益汇总*.xlsx"):
            for sd in _read_sel_days_from_xlsx(p):
                if latest is None or sd > latest:
                    latest = sd
    return latest


def summary_sel_days(variant: str) -> set:
    """变体汇总里已有的选股日集合。"""
    d = variant_dir(variant)
    out: set = set()
    if not d.is_dir():
        return out
    for p in d.glob("各日选股收益汇总*.xlsx"):
        for sd in _read_sel_days_from_xlsx(p):
            out.add(sd)
    return out


def plan_window(
    *,
    end: Optional[date] = None,
    max_days: int = DEFAULT_DAYS,
    force_days: Optional[int] = None,
    start: Optional[date] = None,
    refresh_lookback: int = REFRESH_LOOKBACK,
    progress: ProgressCb = None,
) -> Optional[Tuple[date, date, List[date], str]]:
    from utils.trading_day import get_trading_dates_in_range_sorted

    end_d = end or last_closed_trading_day()

    # 显式起点：回补 start→end 全部交易日（不截断）
    if start is not None:
        if start > end_d:
            _log(f"起点 {start} 晚于末日 {end_d}，跳过", progress)
            return None
        days = list(get_trading_dates_in_range_sorted(start, end_d) or [])
        if not days:
            return None
        reason = f"回补 {start} → {end_d}（{len(days)} 天）"
        _log(f"准备窗口(回补): {days[0]} → {days[-1]} | {reason}", progress)
        return days[0], days[-1], days, reason

    if force_days and int(force_days) > 0:
        n = int(force_days)
        lo = end_d - timedelta(days=max(n * 3, 40))
        days = list(get_trading_dates_in_range_sorted(lo, end_d) or [])
        win = days[-n:] if len(days) >= n else days
        if not win:
            return None
        reason = f"强制近 {len(win)} 天"
        _log(f"准备窗口(强制): {win[0]} → {win[-1]} | {reason}", progress)
        return win[0], win[-1], win, reason

    last_sel = latest_backtest_sel_day()
    _log(
        f"已有回测最晚选股日: {last_sel or '（无）'} → 目标末日 {end_d}",
        progress,
    )
    if last_sel is not None and last_sel >= end_d:
        return None

    gap: List[date] = []
    if last_sel is None:
        lo = end_d - timedelta(days=max(int(max_days) * 3, 40))
        days = list(get_trading_dates_in_range_sorted(lo, end_d) or [])
        gap = days[-int(max_days) :] if days else []
    else:
        days = list(
            get_trading_dates_in_range_sorted(last_sel, end_d) or []
        )
        gap = [d for d in days if d > last_sel]

    refresh: List[date] = []
    if last_sel is not None and int(refresh_lookback) > 0:
        lo = last_sel - timedelta(days=max(int(refresh_lookback) * 3, 20))
        days = list(get_trading_dates_in_range_sorted(lo, last_sel) or [])
        refresh = days[-int(refresh_lookback) :] if days else []

    win = sorted(set(gap) | set(refresh))
    if not win:
        return None
    if len(win) > int(max_days):
        win = win[-int(max_days) :]
    parts = []
    if gap:
        parts.append(f"缺口补 {len([d for d in win if d in gap])} 天")
    if refresh:
        parts.append(f"已有选股日重跑近 {len([d for d in win if d in refresh])} 天")
    reason = " + ".join(parts) or "补数"
    _log(f"准备窗口: {win[0]} → {win[-1]}（{len(win)} 天）| {reason}", progress)
    return win[0], win[-1], win, reason


def _missing_day_segments(missing: List[date]) -> List[Tuple[date, date]]:
    if not missing:
        return []
    from utils.trading_day import get_trading_dates_in_range_sorted

    segs: List[Tuple[date, date]] = []
    seg_lo = missing[0]
    prev = missing[0]
    for d in missing[1:]:
        between = list(get_trading_dates_in_range_sorted(prev, d) or [])
        mid = [x for x in between if prev < x < d]
        if mid:
            segs.append((seg_lo, prev))
            seg_lo = d
        prev = d
    segs.append((seg_lo, prev))
    return segs


def _normalize_sel_day_col(df):
    import pandas as pd

    if df is None or df.empty or "选股日" not in df.columns:
        return df
    out = df.copy()
    raw = out["选股日"]
    parsed = pd.to_datetime(raw, errors="coerce")
    need_serial = parsed.isna()
    if need_serial.any():
        nums = pd.to_numeric(raw, errors="coerce")
        serial_ok = need_serial & nums.notna() & (nums >= 20000) & (nums <= 80000)
        if serial_ok.any():
            parsed.loc[serial_ok] = pd.to_datetime(
                nums.loc[serial_ok], unit="D", origin="1899-12-30", errors="coerce"
            )
    out["选股日"] = parsed.dt.strftime("%Y-%m-%d")
    out.loc[parsed.isna(), "选股日"] = ""
    return out


def _read_selection_xls(path: Path):
    import pandas as pd

    try:
        if path.suffix.lower() == ".xls":
            return pd.read_excel(path, engine="xlrd")
        return pd.read_excel(path)
    except Exception:
        try:
            return pd.read_excel(path)
        except Exception:
            return None


def collect_existing_selection(want_days: List[date], progress: ProgressCb = None):
    import pandas as pd

    files = sorted(
        list(OUT_DIR.glob(f"选股结果_{RULE_NAME}_*.xls"))
        + list(OUT_DIR.glob(f"选股结果_{RULE_NAME}_*.xlsx")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    parts = []
    found: set = set()
    want = set(want_days)
    for p in files:
        df = _read_selection_xls(p)
        if df is None or df.empty or "选股日" not in df.columns:
            continue
        sel = pd.to_datetime(df["选股日"], errors="coerce").dt.date
        mask = sel.isin(want - found)
        if not mask.any():
            continue
        sub = df.loc[mask].copy()
        got = set(sel.loc[mask].dropna().tolist())
        sub["选股日"] = sel.loc[mask].map(lambda d: d.isoformat() if d else "").values
        parts.append(sub)
        found |= got
        _log(f"复用选股 {p.name}: +{len(got)} 日 / {len(sub)} 行", progress)
        if found >= want:
            break
    if not parts:
        return None, set()
    out = pd.concat(parts, ignore_index=True)
    if "股票代码" in out.columns:
        out["股票代码"] = out["股票代码"].astype(str).str.strip().str.zfill(6)
        out = out.drop_duplicates(subset=["选股日", "股票代码"], keep="first")
    return out, found


def write_selection_df(df, start: date, end: date, progress: ProgressCb = None) -> Path:
    from sector_stock_filter import save_xls_with_text_code

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = (
        f"{start.isoformat()}"
        if start == end
        else f"{start.isoformat()}_{end.isoformat()}"
    )
    out_path = OUT_DIR / f"选股结果_{RULE_NAME}_{suffix}.xls"
    df = _normalize_sel_day_col(df)
    if "股票代码" in df.columns:
        df = df.copy()
        df["股票代码"] = df["股票代码"].astype(str).str.strip().str.zfill(6)
    save_xls_with_text_code(str(out_path), df)
    _log(f"选股文件: {out_path.name}  共 {len(df)} 行", progress)
    return out_path


def run_selection(start: date, end: date, *, progress: ProgressCb = None) -> Path:
    """复用马总准备脚本的选股线程逻辑。"""
    from tools import prepare_ma10_regime_data as ma

    # 临时改 OUT_DIR / RULE_NAME
    old_out, old_rule = ma.OUT_DIR, ma.RULE_NAME
    ma.OUT_DIR = OUT_DIR
    ma.RULE_NAME = RULE_NAME
    try:
        return ma.run_selection(start, end, progress=progress, rule_name=RULE_NAME)
    finally:
        ma.OUT_DIR = old_out
        ma.RULE_NAME = old_rule


def _load_strategy(sid: str) -> Tuple[str, str, dict]:
    from strategy_generator_app.config.strategy_config import load_strategy_by_id

    cfg = load_strategy_by_id(sid)
    if cfg is None:
        raise FileNotFoundError(f"未找到策略 {sid}")
    return str(cfg.name or sid), str(cfg.strategy_code or ""), dict(cfg.strategy_params or {})


def _name_fn():
    path = ROOT / "data" / "all_a_stock_info.json"
    names: Dict[str, str] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for code, info in data.items():
                    c6 = "".join(ch for ch in str(code) if ch.isdigit()).zfill(6)[-6:]
                    if c6 and isinstance(info, dict):
                        names[c6] = str(info.get("name") or info.get("stock_name") or "")
        except Exception:
            pass

    def get_name(code: str) -> str:
        c6 = "".join(ch for ch in str(code or "") if ch.isdigit()).zfill(6)[-6:]
        return names.get(c6, "")

    return get_name



def _hit_yes(v) -> bool:
    s = str(v or "").strip()
    return s in ("1", "true", "True", "是", "Y", "y", "yes")


def _filter_selection_df_for_variant(df, variant: str):
    import pandas as pd

    if df is None or getattr(df, "empty", True):
        return df
    col = VARIANT_HIT_COL.get(str(variant) or "")
    if not col or col not in df.columns:
        return df
    return df.loc[df[col].map(_hit_yes)].copy().reset_index(drop=True)


def _codes_by_day_from_df(df):
    import pandas as pd

    by_day = {}
    strength_by_day = {}
    if df is None or df.empty or "选股日" not in df.columns:
        return by_day, strength_by_day, "空表"
    code_col = "股票代码" if "股票代码" in df.columns else ("代码" if "代码" in df.columns else None)
    if not code_col:
        return by_day, strength_by_day, "无代码列"
    sel = pd.to_datetime(df["选股日"], errors="coerce").dt.date
    for i, d in enumerate(sel.tolist()):
        if d is None or (isinstance(d, float) and pd.isna(d)):
            continue
        code = str(df.iloc[i][code_col] or "").strip()
        if "." in code:
            code = code.split(".", 1)[0]
        if code.isdigit():
            code = code.zfill(6)
        if len(code) != 6:
            continue
        by_day.setdefault(d, [])
        if code not in by_day[d]:
            by_day[d].append(code)
        meta = {}
        for k in ("合格榜内序位", "合格榜标签内RS排名", "选出标签内RS排名"):
            if k in df.columns:
                meta[k] = df.iloc[i][k]
        if "合格榜标签内RS排名" not in meta and "选出标签内RS排名" in meta:
            meta["合格榜标签内RS排名"] = meta.get("选出标签内RS排名")
        strength_by_day.setdefault(d, {})[code] = meta
    n = sum(len(v) for v in by_day.values())
    return by_day, strength_by_day, f"{len(by_day)} 日 / {n} 只"



def _trade_to_csv_row(t: dict, *, sel: date, get_name) -> dict:
    code = str(t.get("code") or "").zfill(6)
    side = str(t.get("side") or "").lower()
    side_zh = "买入" if side == "buy" else ("卖出" if side == "sell" else side)
    trig = str(t.get("trigger_info") or "")
    tag = f"[选股日 {sel.isoformat()}]"
    if tag not in trig:
        trig = f"{tag} {trig}".strip()
    row = {
        "日期": str(t.get("date") or ""),
        "时间": str(t.get("time") or ""),
        "代码": code,
        "股票名称": str(t.get("stock_name") or get_name(code) or ""),
        "选股日": sel.isoformat(),
        "方向": side_zh,
        "价格": t.get("price"),
        "数量": t.get("volume"),
        "金额": t.get("amount"),
        "交易后持仓": t.get("position_after"),
        "规则名": t.get("rule_name") or "",
        "腿键": t.get("leg_key") or "",
        "触发信息": trig,
    }
    for k in (
        "真突破①量均量比",
        "真突破①通过",
        "真突破②委卖委买比",
        "真突破②通过",
        "真突破③量被吃卖档比",
        "真突破③通过",
        "真突破③被吃档数",
    ):
        if k in t:
            row[k] = t.get(k)
    return row


def _write_trades_csv(path: Path, rows: List[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    # 稳定列序
    keys: List[str] = []
    for r in rows:
        for k in r.keys():
            if k not in keys:
                keys.append(k)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _buy_fills_for_chain(trades: List[dict], sel: date) -> List[dict]:
    out = []
    for t in trades:
        if str(t.get("side") or "").lower() != "buy":
            continue
        code = str(t.get("code") or "").zfill(6)
        try:
            vol = int(t.get("volume") or 0)
        except (TypeError, ValueError):
            vol = 0
        if not code or vol <= 0:
            continue
        out.append(
            {
                "code": code,
                "date": str(t.get("date") or "")[:10],
                "time": str(t.get("time") or "09:30:00"),
                "volume": vol,
                "price": float(t.get("price") or 0),
                "amount": float(t.get("amount") or 0),
                "commission": float(t.get("commission") or 0),
                "side": "buy",
                "rule_name": str(t.get("rule_name") or ""),
                "leg_key": str(t.get("leg_key") or ""),
                "选股日": sel.isoformat(),
            }
        )
    return out


def _first_buy_dates(fills: List[dict]) -> Dict[str, date]:
    out: Dict[str, date] = {}
    for f in fills:
        code = str(f.get("code") or "").zfill(6)
        d = _parse_d(f.get("date"))
        if not code or d is None:
            continue
        prev = out.get(code)
        if prev is None or d < prev:
            out[code] = d
    return out


def _sell_window_from_buys(
    first_buys: Dict[str, date], hold_n: int
) -> Tuple[Optional[date], Optional[date], str]:
    from strategy_generator_app.trading_calendar import (
        next_trading_day_after,
        trading_day_window_from_start,
    )

    if not first_buys:
        return None, None, "无买入成交"
    starts: List[date] = []
    ends: List[date] = []
    for _c, fb in first_buys.items():
        ns = next_trading_day_after(fb)
        if ns is None:
            return None, None, f"无法计算 {_c} 买入次日"
        _s, e_hold, wmsg = trading_day_window_from_start(fb, hold_n)
        if e_hold is None:
            return None, None, wmsg or "持有窗口不足"
        starts.append(ns)
        ends.append(e_hold if e_hold >= ns else ns)
    return min(starts), max(ends), f"按首次买入次日接续（持有{hold_n}日）"


def run_variant_backtest(
    sel_path: Path,
    *,
    variant: str,
    progress: ProgressCb = None,
    skip_existing: bool = False,
) -> Path:
    """对选股文件按命中列切片后跑买+接续卖，写入变体子目录。

    skip_existing=True：跳过该变体汇总里已有的选股日（回补时避免重跑）。
    """
    from strategy_generator_app.backtest import run_backtest
    from strategy_generator_app.trading_calendar import backtest_window_from_selection_day
    from tools.merge_backtest_trades_by_selection import (
        aggregate,
        apply_buy_day_ma5_ref_fields,
        apply_ma_fields_from_daily_cache,
        apply_mark_and_returns,
        apply_selection_file_fields,
        build_position_corrected_ledger,
        _build_prices_by_mark_date,
    )
    import pandas as pd

    out_v = variant_dir(variant)
    out_v.mkdir(parents=True, exist_ok=True)
    buy_name, buy_code, buy_params0 = _load_strategy(BUY_STRATEGY_ID)
    sell_name, sell_code, sell_params0 = _load_strategy(SELL_STRATEGY_ID)
    get_name = _name_fn()

    raw_df = _read_selection_xls(sel_path)
    filt_df = _filter_selection_df_for_variant(raw_df, variant)
    hit_col = VARIANT_HIT_COL.get(variant, "")
    if raw_df is not None and hit_col and hit_col in getattr(raw_df, "columns", []):
        _log(
            f"[{variant}] 命中切片 {hit_col}: "
            f"{0 if filt_df is None else len(filt_df)}/{len(raw_df)} 行",
            progress,
        )
    by_day, strength_by_day, hint = _codes_by_day_from_df(filt_df)
    _log(f"[{variant}] 解析选股: {hint}", progress)
    if skip_existing and by_day:
        have = summary_sel_days(variant)
        if have:
            before = len(by_day)
            by_day = {d: c for d, c in by_day.items() if d not in have}
            strength_by_day = {d: strength_by_day[d] for d in by_day if d in strength_by_day}
            _log(
                f"[{variant}] 跳过已有汇总 {before - len(by_day)} 日，"
                f"待回测 {len(by_day)} 日",
                progress,
            )
    if not by_day:
        _log(f"[{variant}] 切片后无可用选股日，跳过回测", progress)
        empty = out_v / "_empty.txt"
        empty.write_text("empty\n", encoding="utf-8")
        return empty

    buy_rows: List[dict] = []
    sell_rows: List[dict] = []
    combined_rows: List[dict] = []

    for i, sel_d in enumerate(sorted(by_day.keys())):
        codes = list(dict.fromkeys(by_day[sel_d] or []))
        if not codes:
            continue
        start_d, end_d, msg = backtest_window_from_selection_day(
            sel_d,
            start_next_trading_day=BUY_FROM_T1,
            hold_trading_days=1,
        )
        _log(
            f"[{variant}] 买 {i+1}/{len(by_day)} 选股日{sel_d} → {start_d}~{end_d} "
            f"池={len(codes)} | {msg}",
            progress,
        )
        if start_d is None:
            continue
        buy_params = dict(buy_params0)
        buy_params["entry_window_trading_days"] = 1
        buy_params["sizing_mode"] = BUY_SIZING_MODE
        buy_params["clip_L"] = int(BUY_CLIP_L)
        buy_params["clip_U"] = int(BUY_CLIP_U)
        day_strength = strength_by_day.get(sel_d) or {}
        if day_strength:
            buy_params["clip_strength_by_code"] = day_strength
        buy_params["selection_date_by_code"] = {
            c: sel_d.isoformat() for c in codes
        }
        try:
            buy_res = run_backtest(
                strategy_code=buy_code,
                strategy_params=buy_params,
                stock_codes_6=codes,
                start_date=start_d,
                end_date=end_d,
                initial_cash=INITIAL_CASH,
                get_stock_name=get_name,
                use_engine_form=False,
                use_tick_level=True,
                strategy_generation_time="09:25",
                strategy_run_start_time="09:30",
                strategy_run_end_time="15:00",
                clear_ticks_on_finish=False,
            )
        except Exception as e:
            _log(f"[{variant}] 买入失败 {sel_d}: {e}", progress)
            continue
        buy_trades = list(buy_res.get("trades") or [])
        for t in buy_trades:
            buy_rows.append(_trade_to_csv_row(t, sel=sel_d, get_name=get_name))

        fills = _buy_fills_for_chain(buy_trades, sel_d)
        if not fills:
            _log(f"[{variant}] {sel_d} 无买入成交，跳过卖出", progress)
            continue
        fb = _first_buy_dates(fills)
        s_sell, e_sell, snote = _sell_window_from_buys(fb, SELL_HOLD)
        if s_sell is None:
            _log(f"[{variant}] 卖出窗口失败 {sel_d}: {snote}", progress)
            continue
        sell_params = dict(sell_params0)
        sell_params["scheduled_clear_time"] = SELL_CLEAR_TIME
        sell_params["sell_hold_trading_days"] = SELL_HOLD
        sell_params["scheduled_clear_on_sell_day"] = SELL_HOLD
        # 窗前买入并入 initial_positions；流水 blotter_only
        init_pos: Dict[str, Dict[str, Any]] = {}
        scheduled: List[dict] = []
        for f in fills:
            code = str(f["code"]).zfill(6)
            bd = _parse_d(f.get("date"))
            vol = int(f.get("volume") or 0)
            px = float(f.get("price") or 0)
            amt = float(f.get("amount") or 0)
            if not code or bd is None or vol <= 0 or px <= 0:
                continue
            row = dict(f)
            if bd < s_sell:
                prev = init_pos.get(code) or {"volume": 0, "cost_amt": 0.0}
                nv = int(prev["volume"]) + vol
                cost_amt = float(prev.get("cost_amt") or 0) + amt
                init_pos[code] = {
                    "volume": nv,
                    "cost": round(cost_amt / nv, 4) if nv else px,
                    "cost_amt": cost_amt,
                    "entry_date": (fb.get(code) or bd).isoformat(),
                }
                row["blotter_only"] = True
            scheduled.append(row)
        for code, p in list(init_pos.items()):
            init_pos[code] = {
                "volume": int(p["volume"]),
                "cost": float(p["cost"]),
                "entry_date": p.get("entry_date"),
            }

        _log(
            f"[{variant}] 卖 {sel_d} → {s_sell}~{e_sell} 注入买入{len(fills)}笔 | {snote}",
            progress,
        )
        try:
            sell_res = run_backtest(
                strategy_code=sell_code,
                strategy_params=sell_params,
                stock_codes_6=sorted(
                    set(list(init_pos.keys()) + [str(f["code"]).zfill(6) for f in fills])
                ),
                start_date=s_sell,
                end_date=e_sell,
                initial_cash=INITIAL_CASH,
                get_stock_name=get_name,
                use_engine_form=False,
                use_tick_level=True,
                strategy_generation_time="09:25",
                strategy_run_start_time="09:30",
                strategy_run_end_time="15:00",
                initial_positions=init_pos or None,
                scheduled_buy_fills=scheduled,
                first_buy_date_hints=fb,
                clear_ticks_on_finish=True,
            )
        except Exception as e:
            _log(f"[{variant}] 卖出失败 {sel_d}: {e}", progress)
            continue
        for t in sell_res.get("trades") or []:
            row = _trade_to_csv_row(t, sel=sel_d, get_name=get_name)
            side = str(t.get("side") or "").lower()
            if side == "buy":
                # 卖出回测里的窗前买入流水已在 buy_rows，仅并入总明细
                combined_rows.append(row)
            else:
                sell_rows.append(row)
                combined_rows.append(row)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    buy_csv = out_v / f"_tmp_buy_{ts}.csv"
    sell_csv = out_v / f"_tmp_sell_{ts}.csv"
    fill_csv = out_v / f"回测成交明细_{ts}.csv"
    _write_trades_csv(buy_csv, buy_rows)
    _write_trades_csv(sell_csv, sell_rows)
    # 总明细：买入侧成交 + 卖出侧成交（避免窗前买入流水重复）
    _write_trades_csv(fill_csv, buy_rows + sell_rows)
    _log(
        f"[{variant}] 成交明细: {fill_csv.name} 买{len(buy_rows)}/卖{len(sell_rows)}",
        progress,
    )

    if not buy_rows:
        _log(f"[{variant}] 无买入成交，跳过汇总", progress)
        return fill_csv

    rows = aggregate(buy_csv, sell_csv)
    prices_by_mark, price_warn = _build_prices_by_mark_date(
        rows, mark_n=3, use_nth_trading_day=False, use_last_available=True
    )
    apply_mark_and_returns(
        rows,
        prices_by_mark,
        price_warn,
        mark_n=3,
        use_nth_trading_day=False,
        use_last_available=True,
    )
    try:
        apply_selection_file_fields(rows, sel_path)
    except Exception as e:
        _log(f"[{variant}] 回填选股列失败: {e}", progress)
    try:
        apply_ma_fields_from_daily_cache(rows)
    except Exception:
        pass
    try:
        apply_buy_day_ma5_ref_fields(rows)
    except Exception:
        pass

    new_df = pd.DataFrame(rows)
    sum_path = out_v / "各日选股收益汇总.xlsx"
    # 合并旧汇总：去掉本窗口选股日，再拼新行
    win_sels = {d.isoformat() for d in by_day.keys()}
    old_df = None
    if sum_path.is_file():
        try:
            old_df = pd.read_excel(sum_path, sheet_name=0)
            if "选股日" in old_df.columns:
                old_sel = pd.to_datetime(old_df["选股日"], errors="coerce").dt.strftime("%Y-%m-%d")
                old_df = old_df.loc[~old_sel.isin(win_sels)].copy()
        except Exception as e:
            _log(f"[{variant}] 读取旧汇总失败，将覆盖: {e}", progress)
            old_df = None
    if old_df is not None and not old_df.empty and not new_df.empty:
        merged = pd.concat([old_df, new_df], ignore_index=True, sort=False)
    else:
        merged = new_df if not new_df.empty else old_df
    if merged is None or merged.empty:
        _log(f"[{variant}] 汇总为空", progress)
        return fill_csv

    ledger_df = None
    try:
        ledger_rows = build_position_corrected_ledger(buy_csv, sell_csv)
        if ledger_rows:
            ledger_df = pd.DataFrame(ledger_rows)
    except Exception as e:
        _log(f"[{variant}] 成交流水生成失败: {e}", progress)

    with pd.ExcelWriter(sum_path, engine="openpyxl") as w:
        merged.to_excel(w, index=False, sheet_name="明细")
        if ledger_df is not None and not ledger_df.empty:
            ledger_df.to_excel(w, index=False, sheet_name="成交流水(持仓已校正)")
    _log(f"[{variant}] 已写入 {sum_path.name}（{len(merged)} 行）", progress)

    # 清理临时买卖 CSV
    for p in (buy_csv, sell_csv):
        try:
            p.unlink()
        except Exception:
            pass
    return fill_csv


def prepare(
    *,
    days: int = DEFAULT_DAYS,
    end: Optional[date] = None,
    start: Optional[date] = None,
    skip_select: bool = False,
    skip_backtest: bool = False,
    selection_file: Optional[Path] = None,
    force_days: Optional[int] = None,
    progress: ProgressCb = None,
) -> Dict[str, Any]:
    import pandas as pd

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for v in VARIANTS:
        variant_dir(v).mkdir(parents=True, exist_ok=True)

    planned = plan_window(
        end=end,
        start=start,
        max_days=int(days),
        force_days=force_days,
        progress=progress,
    )
    if planned is None:
        end_d = end or last_closed_trading_day()
        last_sel = latest_backtest_sel_day()
        msg = f"无需补数：回测选股日已覆盖到 {last_sel}（目标末日 {end_d}）"
        _log(msg, progress)
        return {
            "start": str(last_sel) if last_sel else "",
            "end": str(end_d),
            "n_days": 0,
            "selection": "",
            "skipped": True,
            "reason": msg,
        }

    start_d, end_d, win, reason = planned
    sel_path = selection_file
    skip_existing_bt = start is not None

    if skip_select:
        if sel_path is None:
            cands = sorted(
                list(OUT_DIR.glob(f"选股结果_{RULE_NAME}_*.xls"))
                + list(OUT_DIR.glob(f"选股结果_{RULE_NAME}_*.xlsx")),
                key=lambda p: p.stat().st_mtime,
            )
            if not cands:
                raise FileNotFoundError("未指定选股文件且目录中无可用选股结果")
            sel_path = cands[-1]
            _log(f"跳过选股，使用: {sel_path.name}", progress)
    else:
        reused_df, found = collect_existing_selection(win, progress=progress)
        missing = [d for d in win if d not in found]
        frames: List[Any] = []
        if reused_df is not None and not reused_df.empty:
            frames.append(reused_df)
            _log(f"复用选股日 {len(found)}/{len(win)}", progress)
        if missing:
            _log(f"需新选股 {len(missing)} 日：{missing[0]} … {missing[-1]}", progress)
            for lo, hi in _missing_day_segments(missing):
                new_path = run_selection(lo, hi, progress=progress)
                new_df = _read_selection_xls(new_path)
                if new_df is None or new_df.empty:
                    continue
                sel = pd.to_datetime(new_df["选股日"], errors="coerce").dt.date
                keep = sel.isin(set(missing))
                sub = new_df.loc[keep].copy()
                if sub.empty:
                    continue
                sub["选股日"] = sel.loc[keep].values
                frames.append(sub)
        else:
            _log("窗口内选股日均可复用，跳过重选", progress)
        if not frames:
            raise RuntimeError(f"窗口 {start_d}→{end_d} 无可用选股结果")
        merged = pd.concat(frames, ignore_index=True)
        if "选股日" in merged.columns:
            sel = pd.to_datetime(merged["选股日"], errors="coerce").dt.date
            merged = merged.loc[sel.isin(set(win))].copy()
            merged["选股日"] = sel.loc[sel.isin(set(win))].values
        if "股票代码" in merged.columns:
            merged["股票代码"] = merged["股票代码"].astype(str).str.strip().str.zfill(6)
            merged = merged.drop_duplicates(subset=["选股日", "股票代码"], keep="first")
        if merged.empty:
            raise RuntimeError(f"合并后选股为空（{start_d}→{end_d}）")
        sel_path = write_selection_df(merged, start_d, end_d, progress=progress)

    if not skip_backtest:
        for variant in VARIANTS:
            _log(f"===== 回测变体：{variant} =====", progress)
            run_variant_backtest(
                Path(sel_path),
                variant=variant,
                progress=progress,
                skip_existing=skip_existing_bt,
            )

    return {
        "start": str(start_d),
        "end": str(end_d),
        "n_days": len(win),
        "selection": str(sel_path),
        "skipped": False,
        "reason": reason,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="东财热门夹档：缺口选股 + 空头/并集双回测")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--force-days", type=int, default=0)
    ap.add_argument("--start", type=str, default="", help="回补起点 YYYY-MM-DD（如 2026-06-01）")
    ap.add_argument("--end", type=str, default="")
    ap.add_argument("--skip-select", action="store_true")
    ap.add_argument("--skip-backtest", action="store_true")
    ap.add_argument("--selection", type=str, default="")
    args = ap.parse_args(argv)
    prepare(
        days=int(args.days),
        end=_parse_d(args.end) if args.end else None,
        start=_parse_d(args.start) if args.start else None,
        skip_select=bool(args.skip_select),
        skip_backtest=bool(args.skip_backtest),
        selection_file=Path(args.selection) if args.selection else None,
        force_days=int(args.force_days) if int(args.force_days or 0) > 0 else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
