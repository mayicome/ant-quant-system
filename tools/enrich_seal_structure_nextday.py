#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从「封单结构」Excel 拉取 QMT 日线，追加列：
  - 次日交易日
  - 次日收盘涨幅(%)
  - 次日是否涨停

封板日从文件名解析：封单结构_YYYYMMDD.xlsx。
若次日交易日尚未产生完整日线（当前时间未到该日收盘后），对应行不统计次日指标（列为空）。

批量：--all 扫描目录下全部「封单结构*.xlsx」（排除「含次日」），输出「封单结构_含次日_YYYYMMDD.xlsx」。
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

from limit_up_structure_analysis_gui import _extract_open_close_map, _to_full_stock_code


def repo_root() -> str:
    return _ROOT


def history_dir() -> str:
    return os.path.join(repo_root(), "history_data")


def normalize_code(v: object) -> str:
    s = str(v or "").strip().upper()
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else s.zfill(6)[:6]


def find_column(df: pd.DataFrame, aliases: List[str]) -> Optional[str]:
    col_map = {str(c).strip().lower(): c for c in df.columns}
    for a in aliases:
        k = a.strip().lower()
        if k in col_map:
            return col_map[k]
    return None


def parse_seal_date_from_filename(path: str) -> Optional[str]:
    base = os.path.basename(path)
    m = re.search(r"(\d{8})", base)
    return m.group(1) if m else None


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
    price_diff = abs(close - limit_up_price)
    inc_ratio = close / prev_close - 1.0
    return (price_diff <= 0.02) or (inc_ratio >= limit_ratio * 0.99)


def _ts_list_to_yyyymmdd(trading_dates_ts) -> List[str]:
    out: List[str] = []
    if not trading_dates_ts:
        return out
    for ts in trading_dates_ts:
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts / 1000.0)
            out.append(dt.strftime("%Y%m%d"))
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
        raw = xtdata.get_trading_dates("SH", start_time=start_yyyymmdd, end_time=end_yyyymmdd)
        return _ts_list_to_yyyymmdd(raw)
    except Exception:
        return []


def next_trading_day_after(t_yyyymmdd: str, calendar: List[str]) -> Optional[str]:
    """calendar 升序；返回严格大于 t 的第一个交易日。"""
    for d in calendar:
        if d > t_yyyymmdd:
            return d
    return None


def last_settled_trading_day(now: Optional[datetime] = None) -> str:
    """
    已可认为「日线收盘已定」的最近一个交易日（A 股 15:00 收盘）。
    当日为交易日且当前时间早于 15:00，则上一交易日才算「已收盘」。
    """
    tz = timezone(timedelta(hours=8))
    now = now or datetime.now(tz)
    today = now.strftime("%Y%m%d")
    # 向前多取几天，保证覆盖上一交易日
    start = (now - timedelta(days=40)).strftime("%Y%m%d")
    end = (now + timedelta(days=5)).strftime("%Y%m%d")
    cal = get_trading_dates_range(start, end)
    if not cal:
        return ""

    if today not in cal:
        # 今天非交易日：已收盘的「最后」交易日为 <= today 的最大元素
        settled = [d for d in cal if d <= today]
        return settled[-1] if settled else ""

    idx = cal.index(today)
    if now.hour < 15:
        return cal[idx - 1] if idx > 0 else ""
    return today


def fetch_daily_ohlc(full_code: str, start_yyyymmdd: str, end_yyyymmdd: str) -> Dict[str, Tuple[float, float, float, float]]:
    if xtdata is None:
        return {}
    try:
        xtdata.enable_hello = False
    except Exception:
        pass
    start_full = start_yyyymmdd + "000000"
    end_full = end_yyyymmdd + "235959"
    try:
        xtdata.download_history_data(full_code, "1d", start_full, end_full)
        df_map = xtdata.get_market_data_ex(
            [],
            [full_code],
            period="1d",
            start_time=start_full,
            end_time=end_full,
            count=-1,
        )
    except Exception:
        return {}
    if df_map is None or full_code not in df_map or df_map[full_code] is None:
        return {}
    df = df_map[full_code]
    if getattr(df, "empty", True):
        return {}
    return _extract_open_close_map(df)


def newest_seal_file() -> Optional[str]:
    hd = history_dir()
    if not os.path.isdir(hd):
        return None
    best: Tuple[float, str] = (0.0, "")
    for name in os.listdir(hd):
        if not name.lower().endswith(".xlsx"):
            continue
        if "封单结构" not in name or "含次日" in name:
            continue
        fp = os.path.join(hd, name)
        try:
            mt = os.path.getmtime(fp)
        except OSError:
            continue
        if mt > best[0]:
            best = (mt, fp)
    return best[1] if best[1] else None


def list_seal_structure_files(
    scan_dir: str,
    *,
    start_yyyymmdd: str = "",
    end_yyyymmdd: str = "",
) -> List[Tuple[str, str]]:
    """
    扫描目录下「封单结构」原始 xlsx（排除文件名含「含次日」的产出物）。
    返回 [(封板日 YYYYMMDD, 绝对路径)]，按日期升序。
    文件名须含 8 位日期，否则跳过。
    """
    out: List[Tuple[str, str]] = []
    if not os.path.isdir(scan_dir):
        return out
    for name in os.listdir(scan_dir):
        if not name.lower().endswith(".xlsx"):
            continue
        if "封单结构" not in name:
            continue
        if "含次日" in name:
            continue
        m = re.search(r"(\d{8})", name)
        if not m:
            continue
        d = m.group(1)
        if start_yyyymmdd and d < start_yyyymmdd:
            continue
        if end_yyyymmdd and d > end_yyyymmdd:
            continue
        out.append((d, os.path.join(scan_dir, name)))
    out.sort(key=lambda x: x[0])
    return out


def default_out_path(seal_date: str, out_dir: str) -> str:
    return os.path.abspath(os.path.join(out_dir, f"封单结构_含次日_{seal_date}.xlsx"))


def enrich_one_file(
    in_path: str,
    *,
    seal_date: Optional[str],
    out_path: str,
    settled: str,
) -> Tuple[bool, str]:
    """
    处理单个封单结构文件，写出 out_path。
    返回 (是否成功, 说明信息)。
    """
    in_path = os.path.abspath(in_path)
    sd = (seal_date or "").strip() or parse_seal_date_from_filename(in_path)
    if not sd or len(sd) != 8 or not sd.isdigit():
        return False, f"无法确定封板日: {in_path}"

    cal = get_trading_dates_range(
        (datetime.strptime(sd, "%Y%m%d") - timedelta(days=20)).strftime("%Y%m%d"),
        (datetime.strptime(sd, "%Y%m%d") + timedelta(days=40)).strftime("%Y%m%d"),
    )
    t_next = next_trading_day_after(sd, cal)
    if not t_next:
        return False, f"{sd} 无法从交易日历得到次日"

    t1_ready = t_next <= settled

    try:
        df = pd.read_excel(in_path)
    except Exception as e:
        return False, f"读取失败 {in_path}: {e}"

    code_col = find_column(df, ["股票代码", "代码", "code"])
    if code_col is None:
        return False, f"表中未找到代码列: {in_path}"

    out = df.copy()
    out["封板日"] = sd
    out["次日交易日"] = pd.NA
    out["次日收盘涨幅%"] = pd.NA
    out["次日是否涨停"] = pd.NA

    if not t1_ready:
        out.to_excel(out_path, index=False)
        return True, (
            f"{sd} 次日{t_next}未到可统计时点(已收盘日{settled})，次日列留空 -> {os.path.basename(out_path)}"
        )

    codes = [normalize_code(x) for x in out[code_col].tolist()]
    start_dl = (datetime.strptime(sd, "%Y%m%d") - timedelta(days=5)).strftime("%Y%m%d")
    end_dl = (datetime.strptime(t_next, "%Y%m%d") + timedelta(days=2)).strftime("%Y%m%d")

    ohlc_by_code: Dict[str, Dict[str, Tuple[float, float, float, float]]] = {}
    for code in codes:
        if not code or len(code) != 6 or not code.isdigit():
            continue
        full = _to_full_stock_code(code)
        m = fetch_daily_ohlc(full, start_dl, end_dl)
        if m:
            ohlc_by_code[code] = m

    next_days: List = []
    rets: List = []
    limits: List = []

    for code in codes:
        if not code or len(code) != 6 or not code.isdigit():
            next_days.append(pd.NA)
            rets.append(pd.NA)
            limits.append(pd.NA)
            continue
        m = ohlc_by_code.get(code) or {}
        bar_t = m.get(sd)
        bar_next = m.get(t_next)
        if bar_t is None or bar_next is None:
            next_days.append(t_next)
            rets.append(pd.NA)
            limits.append(pd.NA)
            continue
        c0 = bar_t[3]
        c1 = bar_next[3]
        if c0 is None or c1 is None or c0 == 0:
            next_days.append(t_next)
            rets.append(pd.NA)
            limits.append(pd.NA)
            continue
        pct = (c1 - c0) / c0 * 100.0
        lim = "是" if is_limit_up_close(c0, c1, code) else "否"
        next_days.append(t_next)
        rets.append(round(pct, 4))
        limits.append(lim)

    out["次日交易日"] = next_days
    out["次日收盘涨幅%"] = rets
    out["次日是否涨停"] = limits
    out.to_excel(out_path, index=False)

    n_ok = sum(1 for x in rets if pd.notna(x))
    return True, (
        f"{sd} 次日{t_next} 有效样本{n_ok}/{len(codes)} -> {os.path.basename(out_path)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="封单结构表 + QMT 次日收盘涨幅 / 是否涨停",
    )
    parser.add_argument("--input", "-i", help="单个封单结构 xlsx（与 --all 互斥；默认取 history_data 下最新一份）")
    parser.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="批量处理扫描目录下全部「封单结构*.xlsx」（排除文件名含「含次日」）",
    )
    parser.add_argument(
        "--scan-dir",
        help="--all 时扫描目录（默认 history_data）",
    )
    parser.add_argument("--start", help="--all 时只处理封板日 >= YYYYMMDD")
    parser.add_argument("--end", help="--all 时只处理封板日 <= YYYYMMDD")
    parser.add_argument("--out", "-o", help="单文件模式输出路径（默认 history_data/封单结构_含次日_YYYYMMDD.xlsx）")
    parser.add_argument(
        "--out-dir",
        help="批量模式输出目录（默认与 --scan-dir 相同，即 history_data）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="批量模式下若 封单结构_含次日_*.xlsx 已存在则覆盖（默认跳过已存在）",
    )
    parser.add_argument("--seal-date", help="单文件模式：封板日 YYYYMMDD（文件名无日期时使用）")
    args = parser.parse_args()

    if xtdata is None:
        print("xtdata 不可用，请在 QMT/xtquant 环境下运行。")
        return 1

    settled = last_settled_trading_day()
    if not settled:
        print("无法取得交易日历或最近已收盘日，请检查 QMT 连接。")
        return 1

    scan_dir = os.path.abspath(args.scan_dir) if args.scan_dir else history_dir()
    out_dir = os.path.abspath(args.out_dir) if args.out_dir else scan_dir
    start_f = args.start.strip() if args.start else ""
    end_f = args.end.strip() if args.end else ""

    if args.all:
        items = list_seal_structure_files(scan_dir, start_yyyymmdd=start_f, end_yyyymmdd=end_f)
        if not items:
            print(f"在 {scan_dir} 未找到可处理的封单结构 xlsx（需文件名含 8 位日期且不含「含次日」）。")
            return 1
        os.makedirs(out_dir, exist_ok=True)
        ok = skip = fail = 0
        print(f"已收盘参考日: {settled}  共 {len(items)} 个文件\n")
        for seal_date, in_path in items:
            outp = default_out_path(seal_date, out_dir)
            if (not args.overwrite) and os.path.isfile(outp):
                print(f"[跳过] {seal_date} 已存在 {os.path.basename(outp)}")
                skip += 1
                continue
            good, msg = enrich_one_file(
                in_path,
                seal_date=seal_date,
                out_path=outp,
                settled=settled,
            )
            if good:
                print(f"[完成] {msg}")
                ok += 1
            else:
                print(f"[失败] {msg}")
                fail += 1
        print(f"\n汇总: 完成={ok} 跳过={skip} 失败={fail}")
        return 1 if fail else 0

    # 单文件
    in_path = args.input
    if not in_path:
        in_path = newest_seal_file()
    if not in_path or not os.path.isfile(in_path):
        print("未找到输入文件，请使用 --input 指定封单结构 xlsx，或使用 --all。")
        return 1
    in_path = os.path.abspath(in_path)

    seal_date = args.seal_date.strip() if args.seal_date else None
    if not seal_date:
        seal_date = parse_seal_date_from_filename(in_path)

    out_path = args.out
    if not out_path:
        sd_for_out = seal_date or parse_seal_date_from_filename(in_path) or ""
        if not sd_for_out:
            print("无法确定封板日：请在文件名中使用 封单结构_YYYYMMDD.xlsx 或传入 --seal-date。")
            return 1
        out_path = default_out_path(sd_for_out, history_dir())
    else:
        out_path = os.path.abspath(out_path)

    good, msg = enrich_one_file(in_path, seal_date=seal_date, out_path=out_path, settled=settled)
    if not good:
        print(msg)
        return 1
    print(f"已收盘参考日: {settled}")
    print(f"输入: {in_path}")
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
