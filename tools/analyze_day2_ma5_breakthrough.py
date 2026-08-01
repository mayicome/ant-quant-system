#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统计选股文件中每只股票在「选股日后第 N 个交易日」（默认 N=2）上穿 5 日线的次数及每次是否真突破。

用法:
  python tools/analyze_day2_ma5_breakthrough.py 选股结果.xlsx
  python tools/analyze_day2_ma5_breakthrough.py 选股.csv -o 输出.xlsx
  python tools/analyze_day2_ma5_breakthrough.py 选股.xlsx --after-days 2 --start 09:30 --end 15:00

输入表格须含：股票代码列 + 选股日列（每行可不同日期）；可选「股票名称」列。
输出 Excel：「突破明细」「按股汇总」两个 sheet。
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, time as dt_time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategy_generator_app.trading_calendar import next_trading_day_after
from strategy_generator_app.backtest.data_provider import (
    get_historical_prices_for_morning,
    load_tick_data_for_date,
)
from strategy_generator_app.backtest.true_breakthrough import (
    format_true_breakthrough_conditions_detail,
    infer_tick_vol_to_shares_multiplier,
    is_breakthrough_buy_price_cross_tick,
    per_tick_trade_volumes_list,
    round_price_like_display,
    true_breakthrough_export_fields,
)
from strategy_generator_app.backtest.simulator import (
    _evaluate_breakthrough_tb_metrics,
    _light_tb_row,
)

_CODE_COLS = ("股票代码", "代码", "证券代码", "code", "stock_code", "symbol")
_DATE_COLS = (
    "选股日",
    "screen_as_of",
    "基准日",
    "选股基准日",
    "选股日期",
    "交易日期",
    "trade_date",
)
_NAME_COLS = ("股票名称", "名称", "name", "stock_name")


def _norm_code_6(raw: Any) -> str:
    s = str(raw or "").strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = re.sub(r"\.0+$", "", s)
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return ""
    return digits[:6].zfill(6)


def _parse_date(raw: Any) -> Optional[date]:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if hasattr(raw, "date"):
        try:
            return raw.date()
        except Exception:
            pass
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s[:10].replace("/", "-").replace(".", "-"), fmt).date()
        except Exception:
            continue
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 8:
        try:
            return datetime.strptime(digits[:8], "%Y%m%d").date()
        except Exception:
            pass
    return None


def _pick_column(df: pd.DataFrame, candidates: Tuple[str, ...]) -> Optional[str]:
    cols = {str(c).strip(): c for c in df.columns}
    lower = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        if cand in cols:
            return cols[cand]
        if cand.lower() in lower:
            return lower[cand.lower()]
    for orig in df.columns:
        n = str(orig).strip()
        nl = n.lower()
        for cand in candidates:
            if cand in n or cand.lower() in nl:
                return orig
    return None


def _read_selection_file(path: Path) -> pd.DataFrame:
    ext = path.suffix.lower()
    if ext in (".xlsx", ".xls"):
        for kw in ({"dtype": object, "engine": "openpyxl"}, {"dtype": object}):
            try:
                return pd.read_excel(path, **kw)
            except Exception:
                continue
        return pd.read_excel(path, dtype=object)
    if ext == ".csv":
        for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
            try:
                return pd.read_csv(path, encoding=enc, dtype=object)
            except Exception:
                continue
    raise ValueError(f"不支持的文件类型: {ext}（请用 .xlsx / .xls / .csv）")


def load_selection_rows(path: Path) -> List[Dict[str, Any]]:
    df = _read_selection_file(path)
    if df.empty:
        raise ValueError("选股文件为空")
    code_col = _pick_column(df, _CODE_COLS) or df.columns[0]
    date_col = _pick_column(df, _DATE_COLS)
    if not date_col:
        raise ValueError("未找到选股日列（需要「选股日」或 screen_as_of 等）")
    name_col = _pick_column(df, _NAME_COLS)

    rows: List[Dict[str, Any]] = []
    seen = set()
    for _, r in df.iterrows():
        code = _norm_code_6(r.get(code_col))
        pick = _parse_date(r.get(date_col))
        if not code or not pick:
            continue
        key = (pick, code)
        if key in seen:
            continue
        seen.add(key)
        name = ""
        if name_col is not None:
            name = str(r.get(name_col) or "").strip()
            if name.lower() in ("nan", "none"):
                name = ""
        rows.append({"选股日": pick, "代码": code, "股票名称": name})
    if not rows:
        raise ValueError("没有有效行（代码或选股日为空）")
    return rows


def nth_trading_day_after(base: date, n: int) -> Optional[date]:
    cur = base
    for _ in range(n):
        nxt = next_trading_day_after(cur)
        if nxt is None:
            return None
        cur = nxt
    return cur


def _parse_hms(s: str) -> dt_time:
    parts = str(s or "09:30").strip().split(":")
    h = int(parts[0]) if parts else 9
    m = int(parts[1]) if len(parts) > 1 else 0
    sec = int(parts[2]) if len(parts) > 2 else 0
    return dt_time(h, m, sec)


def _tick_datetime(row: pd.Series, df: pd.DataFrame) -> Optional[datetime]:
    for col in ("datetime", "time"):
        if col not in df.columns:
            continue
        v = row.get(col)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        if isinstance(v, datetime):
            return v
        try:
            ts = pd.to_datetime(v, errors="coerce")
            if ts is not None and not pd.isna(ts):
                return ts.to_pydatetime()
        except Exception:
            pass
    return None


def ma5_trigger_price(code6: str, trade_day: date) -> Tuple[Optional[float], str]:
    """与回测早盘视角一致：第二交易日开盘前可见的 5 日线（前 4 日收盘均价）。"""
    prices = get_historical_prices_for_morning([code6], trade_day)
    if isinstance(prices, dict) and prices.get("_error"):
        return None, str(prices["_error"])
    p = (prices or {}).get(code6) or {}
    ma5 = p.get("5日")
    if ma5 is None:
        return None, "无法计算5日线（日线数据不足）"
    try:
        val = float(ma5)
    except (TypeError, ValueError):
        return None, "5日线无效"
    if val <= 0:
        return None, "5日线≤0"
    return val, ""


def scan_crosses_on_day(
    code6: str,
    trade_day: date,
    trigger: float,
    *,
    session_start: dt_time,
    session_end: dt_time,
) -> Tuple[List[Dict[str, Any]], str]:
    df = load_tick_data_for_date(code6, trade_day)
    if df is None or len(df) == 0:
        return [], "无 tick 数据"

    try:
        from strategy_generator_app.backtest.tick_cache_loader import tick_data_cache_module

        coerce = getattr(tick_data_cache_module(), "coerce_tick_dataframe", None)
        if callable(coerce):
            df = coerce(df)
    except Exception:
        pass

    if df is None or len(df) == 0:
        return [], "tick 为空"

    price_col = "lastPrice"
    if price_col not in df.columns:
        for alt in ("last", "price", "matchPrice"):
            if alt in df.columns:
                df = df.copy()
                df["lastPrice"] = df[alt]
                break
    if "lastPrice" not in df.columns:
        return [], "tick 缺少价格列"

    vm = infer_tick_vol_to_shares_multiplier(df)
    row_list = [r for _, r in df.iterrows()]
    vol_series = per_tick_trade_volumes_list(row_list, float(vm))

    vol_mul_by_code = {code6: float(vm)}
    tb_prefix_sum: Dict[str, float] = {code6: 0.0}
    tb_prefix_cnt: Dict[str, int] = {code6: 0}
    prev_tick_row: Dict[str, Dict[str, Any]] = {}
    recent_tick_rows: Dict[str, List[Dict[str, Any]]] = {code6: []}
    recent_break_vols: Dict[str, List[Optional[float]]] = {code6: []}
    last_tick_price: Optional[float] = None

    crosses: List[Dict[str, Any]] = []
    seq = 0
    in_session_ticks = 0

    for j, row in enumerate(row_list):
        tick_dt = _tick_datetime(row, df)
        if tick_dt is None:
            continue
        t = tick_dt.time()
        if t < session_start or t > session_end:
            continue

        lp = float(row.get("lastPrice") or 0)
        if lp <= 0:
            continue
        in_session_ticks += 1
        v_break = vol_series[j] if j < len(vol_series) else None

        prev_lp = last_tick_price
        if is_breakthrough_buy_price_cross_tick(code6, lp, trigger, prev_lp):
            seq += 1
            row_dict = _light_tb_row(row)
            tb_metrics = _evaluate_breakthrough_tb_metrics(
                code6,
                row_dict,
                vol_mul_by_code,
                tb_prefix_cnt,
                tb_prefix_sum,
                prev_tick_row,
                recent_tick_rows,
                recent_break_vols,
                v_break,
            )
            detail = str(tb_metrics.pop("_tb_detail", "") or "")
            if not detail:
                detail = format_true_breakthrough_conditions_detail(tb_metrics)
            disp_trig = round_price_like_display(code6, trigger)
            disp_lp = round_price_like_display(code6, lp)
            disp_prev = (
                round_price_like_display(code6, prev_lp)
                if prev_lp is not None and prev_lp > 0
                else None
            )
            crosses.append(
                {
                    "突破序号": seq,
                    "突破时间": tick_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "突破价": disp_lp,
                    "前价": disp_prev,
                    "5日线触发价": disp_trig,
                    "是否真突破": "是" if tb_metrics.get("passed") else "否",
                    "真突破详情": detail,
                    **true_breakthrough_export_fields(tb_metrics),
                }
            )

        row_dict = _light_tb_row(row)
        if row_dict:
            prev_tick_row[code6] = row_dict
            hist = recent_tick_rows.setdefault(code6, [])
            hist.append(row_dict)
            if len(hist) > 5:
                recent_tick_rows[code6] = hist[-5:]
        if v_break is not None:
            vhist = recent_break_vols.setdefault(code6, [])
            vhist.append(float(v_break))
            if len(vhist) > 15:
                recent_break_vols[code6] = vhist[-15:]
            tb_prefix_sum[code6] = tb_prefix_sum.get(code6, 0.0) + float(v_break)
            tb_prefix_cnt[code6] = tb_prefix_cnt.get(code6, 0) + 1
        last_tick_price = lp

    if in_session_ticks == 0:
        return [], f"交易时段 {session_start}-{session_end} 内无有效 tick"
    return crosses, ""


def build_outputs(
    selection_rows: List[Dict[str, Any]],
    *,
    after_days: int,
    session_start: dt_time,
    session_end: dt_time,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    detail_rows: List[Dict[str, Any]] = []
    summary_rows: List[Dict[str, Any]] = []

    total = len(selection_rows)
    for i, item in enumerate(selection_rows, 1):
        pick: date = item["选股日"]
        code6: str = item["代码"]
        name: str = item.get("股票名称") or ""
        trade_day = nth_trading_day_after(pick, after_days)
        base = {
            "选股日": pick.strftime("%Y-%m-%d"),
            "代码": code6,
            "股票名称": name,
            "第N交易日": after_days,
            "分析交易日": trade_day.strftime("%Y-%m-%d") if trade_day else "",
        }
        print(f"[{i}/{total}] {code6} 选股日={base['选股日']} → 分析日={base['分析交易日'] or '?'}")

        if trade_day is None:
            summary_rows.append(
                {
                    **base,
                    "5日线触发价": "",
                    "突破次数": 0,
                    "真突破次数": 0,
                    "首次突破时间": "",
                    "首次真突破时间": "",
                    "备注": f"选股日后第{after_days}个交易日不存在",
                }
            )
            continue

        trigger, trig_err = ma5_trigger_price(code6, trade_day)
        if trigger is None:
            summary_rows.append(
                {
                    **base,
                    "5日线触发价": "",
                    "突破次数": 0,
                    "真突破次数": 0,
                    "首次突破时间": "",
                    "首次真突破时间": "",
                    "备注": trig_err,
                }
            )
            continue

        crosses, tick_err = scan_crosses_on_day(
            code6,
            trade_day,
            float(trigger),
            session_start=session_start,
            session_end=session_end,
        )
        if tick_err and not crosses:
            summary_rows.append(
                {
                    **base,
                    "5日线触发价": round(float(trigger), 4),
                    "突破次数": 0,
                    "真突破次数": 0,
                    "首次突破时间": "",
                    "首次真突破时间": "",
                    "备注": tick_err,
                }
            )
            continue

        true_times = [c["突破时间"] for c in crosses if c.get("是否真突破") == "是"]
        summary_rows.append(
            {
                **base,
                "5日线触发价": round(float(trigger), 4),
                "突破次数": len(crosses),
                "真突破次数": len(true_times),
                "首次突破时间": crosses[0]["突破时间"] if crosses else "",
                "首次真突破时间": true_times[0] if true_times else "",
                "备注": tick_err or "",
            }
        )
        for c in crosses:
            detail_rows.append({**base, **c})

    detail_df = pd.DataFrame(detail_rows)
    summary_df = pd.DataFrame(summary_rows)
    return detail_df, summary_df


def main() -> int:
    parser = argparse.ArgumentParser(
        description="统计选股文件各股在选股日后第 N 个交易日突破 5 日线的次数及真突破情况"
    )
    parser.add_argument("input", help="选股文件路径（.xlsx / .xls / .csv）")
    parser.add_argument(
        "-o",
        "--output",
        help="输出 Excel 路径（默认：与输入同目录，文件名加 _day2_ma5_breakthrough.xlsx）",
    )
    parser.add_argument(
        "--after-days",
        type=int,
        default=2,
        help="选股日后的第几个交易日作为分析日（默认 2 = 第二个交易日）",
    )
    parser.add_argument("--start", default="09:30", help="tick 分析起始时刻 HH:MM 或 HH:MM:SS")
    parser.add_argument("--end", default="15:00", help="tick 分析结束时刻 HH:MM 或 HH:MM:SS")
    args = parser.parse_args()

    if args.after_days < 1:
        print("after-days 须 >= 1", file=sys.stderr)
        return 2

    in_path = Path(args.input).expanduser().resolve()
    if not in_path.is_file():
        print(f"文件不存在: {in_path}", file=sys.stderr)
        return 2

    out_path = Path(args.output).expanduser().resolve() if args.output else None
    if out_path is None:
        out_path = in_path.with_name(f"{in_path.stem}_day{args.after_days}_ma5_breakthrough.xlsx")

    session_start = _parse_hms(args.start)
    session_end = _parse_hms(args.end)
    if session_start >= session_end:
        print("start 须早于 end", file=sys.stderr)
        return 2

    print(f"读取: {in_path}")
    selection_rows = load_selection_rows(in_path)
    print(f"有效记录 {len(selection_rows)} 条；分析日=选股日后第 {args.after_days} 个交易日")
    print(f"时段: {session_start} – {session_end}")

    detail_df, summary_df = build_outputs(
        selection_rows,
        after_days=args.after_days,
        session_start=session_start,
        session_end=session_end,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="按股汇总", index=False)
        detail_df.to_excel(writer, sheet_name="突破明细", index=False)

    print(f"已写入: {out_path}")
    print(f"  按股汇总 {len(summary_df)} 行，突破明细 {len(detail_df)} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
