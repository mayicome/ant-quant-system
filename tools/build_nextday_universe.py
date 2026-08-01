#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成「通用次日字段」主表（与封单结构计算解耦）：
- 输入：history_data/涨停板数据_YYYY-MM-DD.csv
- 输出：history_data/次日字段通用_YYYYMMDD_HHMMSS.xlsx

主表键：seal_date + code
主表字段：next_trade_date / next_day_ret / next_day_limit_up
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd

try:
    import xtquant.xtdata as xtdata
except Exception:
    xtdata = None

from limit_up_structure_analysis_gui import _extract_open_close_map, _load_limitup_codes, _to_full_stock_code


def history_dir() -> str:
    return os.path.join(_ROOT, "history_data")


def normalize_code(v: object) -> str:
    s = str(v or "").strip().upper()
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else s.zfill(6)[:6]


def _limit_ratio_for_code(code_6: str) -> float:
    if code_6.startswith(("300", "301", "688", "689")):
        return 0.20
    if code_6.startswith(("8", "4", "920")):
        return 0.30
    return 0.10


def is_limit_up_close(prev_close: float, close: float, code_6: str) -> bool:
    if prev_close is None or close is None or prev_close == 0:
        return False
    limit_ratio = _limit_ratio_for_code(code_6)
    limit_up_price = round(prev_close * (1 + limit_ratio), 2)
    return (abs(close - limit_up_price) <= 0.02) or ((close / prev_close - 1.0) >= limit_ratio * 0.99)


def _ts_list_to_yyyymmdd(trading_dates_ts) -> List[str]:
    out: List[str] = []
    for ts in trading_dates_ts or []:
        if isinstance(ts, (int, float)):
            out.append(datetime.fromtimestamp(ts / 1000.0).strftime("%Y%m%d"))
        elif isinstance(ts, str):
            out.append(ts.replace("-", "")[:8])
        else:
            out.append(str(ts).replace("-", "")[:8])
    out = [x for x in out if len(x) == 8 and x.isdigit()]
    out.sort()
    return out


def get_trading_dates_range(start_yyyymmdd: str, end_yyyymmdd: str) -> List[str]:
    if xtdata is None:
        return []
    try:
        xtdata.enable_hello = False
    except Exception:
        pass
    try:
        return _ts_list_to_yyyymmdd(xtdata.get_trading_dates("SH", start_time=start_yyyymmdd, end_time=end_yyyymmdd))
    except Exception:
        return []


def last_settled_trading_day(now: Optional[datetime] = None) -> str:
    tz = timezone(timedelta(hours=8))
    now = now or datetime.now(tz)
    today = now.strftime("%Y%m%d")
    cal = get_trading_dates_range((now - timedelta(days=40)).strftime("%Y%m%d"), (now + timedelta(days=5)).strftime("%Y%m%d"))
    if not cal:
        return ""
    if today not in cal:
        c = [d for d in cal if d <= today]
        return c[-1] if c else ""
    idx = cal.index(today)
    return cal[idx - 1] if now.hour < 15 and idx > 0 else today


def list_limitup_csv_files(scan_dir: str, start: str = "", end: str = "") -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if not os.path.isdir(scan_dir):
        return out
    for name in os.listdir(scan_dir):
        if not (name.startswith("涨停板数据_") and name.lower().endswith(".csv")):
            continue
        m = re.search(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})", name)
        if not m:
            continue
        d = f"{m.group(1)}{m.group(2)}{m.group(3)}"
        if start and d < start:
            continue
        if end and d > end:
            continue
        out.append((d, os.path.join(scan_dir, name)))
    out.sort(key=lambda x: x[0])
    return out


def fetch_daily_ohlc(full_code: str, start_yyyymmdd: str, end_yyyymmdd: str) -> Dict[str, Tuple[float, float, float, float]]:
    start_full = start_yyyymmdd + "000000"
    end_full = end_yyyymmdd + "235959"
    try:
        xtdata.download_history_data(full_code, "1d", start_full, end_full)
        m = xtdata.get_market_data_ex([], [full_code], period="1d", start_time=start_full, end_time=end_full, count=-1)
    except Exception:
        return {}
    if not isinstance(m, dict) or full_code not in m or m[full_code] is None or getattr(m[full_code], "empty", True):
        return {}
    return _extract_open_close_map(m[full_code])


def build_for_one_date(seal_date: str, csv_path: str, settled: str) -> Tuple[pd.DataFrame, str]:
    cal = get_trading_dates_range((datetime.strptime(seal_date, "%Y%m%d") - timedelta(days=20)).strftime("%Y%m%d"), (datetime.strptime(seal_date, "%Y%m%d") + timedelta(days=40)).strftime("%Y%m%d"))
    next_dates = [d for d in cal if d > seal_date]
    if not next_dates:
        return pd.DataFrame(), f"{seal_date} 无法得到次日交易日"
    t1 = next_dates[0]
    if t1 > settled:
        return pd.DataFrame(), f"{seal_date} 次日 {t1} 未到可统计时点（已收盘日 {settled}）"

    code_to_name = _load_limitup_codes(csv_path)
    rows: List[Dict[str, object]] = []
    start_dl = (datetime.strptime(seal_date, "%Y%m%d") - timedelta(days=5)).strftime("%Y%m%d")
    end_dl = (datetime.strptime(t1, "%Y%m%d") + timedelta(days=2)).strftime("%Y%m%d")

    for code, name in sorted(code_to_name.items(), key=lambda x: x[0]):
        code6 = normalize_code(code)
        if len(code6) != 6 or (not code6.isdigit()):
            continue
        full = _to_full_stock_code(code6)
        ohlc = fetch_daily_ohlc(full, start_dl, end_dl)
        b0 = ohlc.get(seal_date)
        b1 = ohlc.get(t1)
        ret = None
        lim = None
        if b0 is not None and b1 is not None and b0[3] not in (None, 0) and b1[3] is not None:
            c0 = float(b0[3])
            c1 = float(b1[3])
            ret = round((c1 - c0) / c0 * 100.0, 4)
            lim = "是" if is_limit_up_close(c0, c1, code6) else "否"
        rows.append(
            {
                "seal_date": seal_date,
                "next_trade_date": t1,
                "code": code6,
                "name": name or "",
                "next_day_ret": ret,
                "next_day_limit_up": lim,
            }
        )

    return pd.DataFrame(rows), f"{seal_date} 次日={t1} rows={len(rows)}"


def main() -> int:
    parser = argparse.ArgumentParser(description="生成通用次日字段主表（date+code）")
    parser.add_argument("--scan-dir", default=history_dir(), help="涨停板CSV目录（默认 history_data）")
    parser.add_argument("--start", help="起始封板日 YYYYMMDD")
    parser.add_argument("--end", help="结束封板日 YYYYMMDD")
    parser.add_argument("--out", "-o", help="输出 xlsx 路径（默认 history_data/次日字段通用_时间戳.xlsx）")
    args = parser.parse_args()

    if xtdata is None:
        print("xtdata 不可用，请在 QMT/xtquant 环境下运行。")
        return 1
    settled = last_settled_trading_day()
    if not settled:
        print("无法取得已收盘交易日，请检查 QMT 连接。")
        return 1

    files = list_limitup_csv_files(os.path.abspath(args.scan_dir), args.start.strip() if args.start else "", args.end.strip() if args.end else "")
    if not files:
        print("未找到涨停板数据 CSV。")
        return 1

    parts: List[pd.DataFrame] = []
    for d, fp in files:
        try:
            df, msg = build_for_one_date(d, fp, settled)
            if not df.empty:
                parts.append(df)
            print(f"[处理] {msg}")
        except Exception as e:
            print(f"[失败] {d}: {e}")

    if not parts:
        print("无可用次日样本。")
        return 1

    out_df = pd.concat(parts, ignore_index=True)
    out_df = out_df.dropna(subset=["next_day_ret"]).drop_duplicates(subset=["seal_date", "code"], keep="first").reset_index(drop=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.abspath(args.out) if args.out else os.path.join(history_dir(), f"次日字段通用_{ts}.xlsx")
    out_df.to_excel(out_path, index=False)
    print(f"已收盘日: {settled}  文件日数: {len(files)}  样本: {len(out_df)}")
    print(f"已写出: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
