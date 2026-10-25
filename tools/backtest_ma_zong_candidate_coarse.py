# -*- coding: utf-8 -*-
"""马总盘中候选 · 粗回测（Stage 1）

用已落盘的多时刻候选 CSV，粗估「当时最新价 + 滑点买入 → 下一交易日开盘卖出」的收益，
比较各 dump 时刻（11:00 / 14:00 / …）的差异，便于先筛出一两个较优买入时点。

=============================================================================
用法
=============================================================================
  # 默认读 data/ma_zong_intraday_candidates/*.csv，滑点 0.1%，输出到 backtest_coarse/
  python tools/backtest_ma_zong_candidate_coarse.py

  # 指定滑点（百分比，0.001 = 0.1%）与/或最小价位跳数（A 股默认 0.01 元/跳）
  python tools/backtest_ma_zong_candidate_coarse.py --slippage-pct 0.001
  python tools/backtest_ma_zong_candidate_coarse.py --slippage-ticks 1
  python tools/backtest_ma_zong_candidate_coarse.py --slippage-pct 0.001 --slippage-ticks 1

  # 只看某一段选股日 / 某几个落盘时钟
  python tools/backtest_ma_zong_candidate_coarse.py --date-from 2026-10-01 --date-to 2026-10-31
  python tools/backtest_ma_zong_candidate_coarse.py --clocks 14:40,14:50,14:55

  # 输入/输出目录
  python tools/backtest_ma_zong_candidate_coarse.py --dump-dir data/ma_zong_intraday_candidates
  python tools/backtest_ma_zong_candidate_coarse.py --out-dir data/ma_zong_intraday_candidates/backtest_coarse

=============================================================================
撮合口径（粗）
=============================================================================
  - 买入价 = 落盘字段「当时最新价」× (1 + slippage_pct) + slippage_ticks × 0.01
  - 卖出价 = 选股日的【下一交易日】日线 open（data/daily_cache，不复权）
  - 单笔收益率 = 卖出价 / 买入价 - 1（未扣佣金/印花税；粗比即可）
  - 缺价 / 无次日开盘 → 该行记 skip，不进收益统计

=============================================================================
硬门槛 vs 软门槛
=============================================================================
  落盘文件本身是「硬门槛全过」全集。本脚本默认同时出两套汇总：
  - hard_pass：全部硬通过行
  - soft_pass：其中「满足全部软门槛」= 是 的子集（与实盘马总盘中买筛选更接近）
  也可用 --universe hard|soft|both 只出其一。

=============================================================================
输出
=============================================================================
  data/ma_zong_intraday_candidates/backtest_coarse/
    trades_detail.csv          每笔明细（含 skip 原因）
    summary_by_clock.csv       按 落盘时钟 × 宇宙 的 mean/median/胜率/笔数
    summary_by_day_clock.csv   按 选股日 × 落盘时钟 × 宇宙 的明细汇总（可选看）

=============================================================================
TODO Stage 2（细回测，本次不实现）
=============================================================================
  # TODO(Stage2): 选定最优 dump 时点后，按策略规则生成动态 Python 文件，
  # 在 QMT 内部回测程序中按 tick 历史加载该规则重放（买入约满足后若干 tick /
  # 次日开盘弹性卖等），结果经 log 回灌策略生成系统「回测」页。
  # 粗回测只负责筛时点，不替代 tick 级成交。
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DUMP_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{4})\.csv$", re.I)
TICK_SIZE = 0.01  # A 股默认最小变动价位（元）


def _install_ipv4() -> None:
    try:
        from utils.http_ipv4 import install_prefer_ipv4

        install_prefer_ipv4()
    except Exception:
        pass


def _parse_date(s: str) -> date:
    s = str(s or "").strip()
    if len(s) == 8 and s.isdigit():
        return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
    return date.fromisoformat(s[:10])


def _norm_clock(s: str) -> str:
    """'14:40' / '1440' → '14:40'。"""
    t = str(s or "").strip().replace("：", ":")
    if ":" in t:
        parts = t.split(":")
        return "%02d:%02d" % (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    digits = "".join(ch for ch in t if ch.isdigit())
    if len(digits) >= 4:
        return "%s:%s" % (digits[:2], digits[2:4])
    if len(digits) == 3:
        return "0%s:%s" % (digits[0], digits[1:3])
    raise ValueError("bad clock: %r" % s)


def _clock_from_stem(hhmm: str) -> str:
    return _norm_clock(hhmm)


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if f != f:  # NaN
            return None
        return f
    s = str(v).strip().replace(",", "")
    if not s or s.lower() in ("nan", "none", "null", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _norm_code(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip().upper()
    if not s:
        return ""
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def _yes(v: Any) -> bool:
    s = str(v or "").strip()
    return s in ("是", "Y", "y", "1", "true", "True", "YES", "yes")


def next_tradeday_after(d: date, max_scan: int = 40) -> Optional[date]:
    """选股日之后的最近一个交易日。"""
    try:
        from utils.trading_day import is_tradeday, get_trading_dates_in_range_sorted

        end = d + timedelta(days=max(max_scan * 2, 60))
        days = get_trading_dates_in_range_sorted(d + timedelta(days=1), end)
        if days:
            return days[0]
        # 日历缺口时再逐日 is_tradeday
        cur = d + timedelta(days=1)
        for _ in range(max_scan):
            if is_tradeday(cur):
                return cur
            cur += timedelta(days=1)
    except Exception:
        cur = d + timedelta(days=1)
        for _ in range(max_scan):
            if cur.weekday() < 5:
                return cur
            cur += timedelta(days=1)
    return None


def buy_price_with_slippage(
    last_px: float,
    *,
    slippage_pct: float = 0.0,
    slippage_ticks: float = 0.0,
    tick_size: float = TICK_SIZE,
) -> float:
    """买入价 = 最新价 × (1+pct) + ticks×tick_size。"""
    px = float(last_px) * (1.0 + float(slippage_pct or 0.0))
    px += float(slippage_ticks or 0.0) * float(tick_size)
    return round(px, 4)


def load_open_on(code: str, day: date, cache: Dict[Tuple[str, str], Optional[float]]) -> Optional[float]:
    key = (code, day.isoformat())
    if key in cache:
        return cache[key]
    px: Optional[float] = None
    try:
        from utils.daily_cache_reader import load_daily_from_cache

        df = load_daily_from_cache(code, through_date=day)
        if df is not None and not getattr(df, "empty", True):
            row = df[df["date"] == day]
            if row is None or row.empty:
                # date 列可能是 Timestamp
                try:
                    import pandas as pd

                    row = df[pd.to_datetime(df["date"]).dt.date == day]
                except Exception:
                    row = None
            if row is not None and not row.empty:
                px = _to_float(row.iloc[-1].get("open"))
    except Exception:
        px = None
    cache[key] = px
    return px


def discover_dump_csvs(
    dump_dir: Path,
    *,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    clocks: Optional[Sequence[str]] = None,
) -> List[Tuple[Path, date, str]]:
    """返回 [(path, as_of, clock_hhmm), ...]。"""
    clock_set = None
    if clocks:
        clock_set = {_norm_clock(c) for c in clocks}
    out: List[Tuple[Path, date, str]] = []
    if not dump_dir.is_dir():
        return out
    for p in sorted(dump_dir.glob("*.csv")):
        m = DUMP_NAME_RE.match(p.name)
        if not m:
            continue
        as_of = _parse_date(m.group(1))
        clock = _clock_from_stem(m.group(2))
        if date_from and as_of < date_from:
            continue
        if date_to and as_of > date_to:
            continue
        if clock_set is not None and clock not in clock_set:
            continue
        out.append((p, as_of, clock))
    return out


def read_dump_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [dict(r) for r in reader]


def _pct(x: Optional[float]) -> str:
    if x is None:
        return ""
    return "%.4f%%" % (100.0 * x)


def _stats(returns: List[float]) -> Dict[str, Any]:
    n = len(returns)
    if n == 0:
        return {
            "笔数": 0,
            "胜率": "",
            "平均收益": "",
            "中位收益": "",
            "平均收益_小数": "",
            "中位收益_小数": "",
            "胜率_小数": "",
        }
    srt = sorted(returns)
    mean = sum(returns) / n
    mid = srt[n // 2] if n % 2 == 1 else 0.5 * (srt[n // 2 - 1] + srt[n // 2])
    wins = sum(1 for r in returns if r > 0)
    win_rate = wins / n
    return {
        "笔数": n,
        "胜率": _pct(win_rate),
        "平均收益": _pct(mean),
        "中位收益": _pct(mid),
        "平均收益_小数": round(mean, 6),
        "中位收益_小数": round(mid, 6),
        "胜率_小数": round(win_rate, 6),
    }


def run_backtest(
    *,
    dump_dir: Path,
    out_dir: Path,
    slippage_pct: float,
    slippage_ticks: float,
    tick_size: float,
    date_from: Optional[date],
    date_to: Optional[date],
    clocks: Optional[Sequence[str]],
    universe: str,
) -> Dict[str, Any]:
    files = discover_dump_csvs(
        dump_dir, date_from=date_from, date_to=date_to, clocks=clocks
    )
    open_cache: Dict[Tuple[str, str], Optional[float]] = {}
    detail_rows: List[Dict[str, Any]] = []

    universes: List[str] = []
    if universe in ("both", "hard"):
        universes.append("hard_pass")
    if universe in ("both", "soft"):
        universes.append("soft_pass")

    for path, as_of_file, clock_file in files:
        rows = read_dump_rows(path)
        for raw in rows:
            code = _norm_code(raw.get("股票代码"))
            name = str(raw.get("股票名称") or "").strip()
            as_of_s = str(raw.get("选股日") or "").strip() or as_of_file.isoformat()
            try:
                as_of = _parse_date(as_of_s)
            except Exception:
                as_of = as_of_file
            clock = str(raw.get("落盘时钟") or "").strip() or clock_file
            try:
                clock = _norm_clock(clock)
            except Exception:
                clock = clock_file
            soft_ok = _yes(raw.get("满足全部软门槛"))
            last_px = _to_float(raw.get("当时最新价"))

            base = {
                "选股日": as_of.isoformat(),
                "落盘时钟": clock,
                "来源文件": path.name,
                "股票代码": code,
                "股票名称": name,
                "满足全部软门槛": "是" if soft_ok else "否",
                "当时最新价": last_px if last_px is not None else "",
                "滑点_pct": slippage_pct,
                "滑点_ticks": slippage_ticks,
                "数据模式": str(raw.get("数据模式") or ""),
            }

            if not code:
                for uni in universes:
                    if uni == "soft_pass" and not soft_ok:
                        continue
                    detail_rows.append(
                        {
                            **base,
                            "宇宙": uni,
                            "买入价": "",
                            "卖出日": "",
                            "卖出开盘价": "",
                            "收益率": "",
                            "状态": "skip",
                            "原因": "无股票代码",
                        }
                    )
                continue

            if last_px is None or last_px <= 0:
                for uni in universes:
                    if uni == "soft_pass" and not soft_ok:
                        continue
                    detail_rows.append(
                        {
                            **base,
                            "宇宙": uni,
                            "买入价": "",
                            "卖出日": "",
                            "卖出开盘价": "",
                            "收益率": "",
                            "状态": "skip",
                            "原因": "无当时最新价",
                        }
                    )
                continue

            buy_px = buy_price_with_slippage(
                last_px,
                slippage_pct=slippage_pct,
                slippage_ticks=slippage_ticks,
                tick_size=tick_size,
            )
            sell_day = next_tradeday_after(as_of)
            if sell_day is None:
                for uni in universes:
                    if uni == "soft_pass" and not soft_ok:
                        continue
                    detail_rows.append(
                        {
                            **base,
                            "宇宙": uni,
                            "买入价": buy_px,
                            "卖出日": "",
                            "卖出开盘价": "",
                            "收益率": "",
                            "状态": "skip",
                            "原因": "无下一交易日",
                        }
                    )
                continue

            sell_px = load_open_on(code, sell_day, open_cache)
            if sell_px is None or sell_px <= 0:
                for uni in universes:
                    if uni == "soft_pass" and not soft_ok:
                        continue
                    detail_rows.append(
                        {
                            **base,
                            "宇宙": uni,
                            "买入价": buy_px,
                            "卖出日": sell_day.isoformat(),
                            "卖出开盘价": "",
                            "收益率": "",
                            "状态": "skip",
                            "原因": "无次日开盘价(daily_cache)",
                        }
                    )
                continue

            ret = sell_px / buy_px - 1.0
            for uni in universes:
                if uni == "soft_pass" and not soft_ok:
                    continue
                detail_rows.append(
                    {
                        **base,
                        "宇宙": uni,
                        "买入价": buy_px,
                        "卖出日": sell_day.isoformat(),
                        "卖出开盘价": sell_px,
                        "收益率": round(ret, 6),
                        "状态": "ok",
                        "原因": "",
                    }
                )

    # 汇总：时钟 × 宇宙
    by_clock: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    by_day_clock: Dict[Tuple[str, str, str], List[float]] = defaultdict(list)
    skip_counts: Dict[Tuple[str, str], int] = defaultdict(int)
    dump_days: set = set()
    dump_clocks: set = set()

    for r in detail_rows:
        dump_days.add(r["选股日"])
        dump_clocks.add(r["落盘时钟"])
        key = (r["落盘时钟"], r["宇宙"])
        if r["状态"] == "ok":
            by_clock[key].append(float(r["收益率"]))
            by_day_clock[(r["选股日"], r["落盘时钟"], r["宇宙"])].append(float(r["收益率"]))
        else:
            skip_counts[key] += 1

    summary_clock: List[Dict[str, Any]] = []
    for clock in sorted(dump_clocks):
        for uni in universes:
            st = _stats(by_clock.get((clock, uni), []))
            summary_clock.append(
                {
                    "落盘时钟": clock,
                    "宇宙": uni,
                    "选股日数": len({d for (d, c, u) in by_day_clock if c == clock and u == uni}),
                    "有效笔数": st["笔数"],
                    "跳过笔数": skip_counts.get((clock, uni), 0),
                    "胜率": st["胜率"],
                    "平均收益": st["平均收益"],
                    "中位收益": st["中位收益"],
                    "平均收益_小数": st["平均收益_小数"],
                    "中位收益_小数": st["中位收益_小数"],
                    "胜率_小数": st["胜率_小数"],
                    "滑点_pct": slippage_pct,
                    "滑点_ticks": slippage_ticks,
                }
            )

    summary_day: List[Dict[str, Any]] = []
    for (d, clock, uni), rets in sorted(by_day_clock.items()):
        st = _stats(rets)
        summary_day.append(
            {
                "选股日": d,
                "落盘时钟": clock,
                "宇宙": uni,
                "有效笔数": st["笔数"],
                "胜率": st["胜率"],
                "平均收益": st["平均收益"],
                "中位收益": st["中位收益"],
                "平均收益_小数": st["平均收益_小数"],
                "中位收益_小数": st["中位收益_小数"],
                "胜率_小数": st["胜率_小数"],
            }
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    detail_path = out_dir / "trades_detail.csv"
    summary_path = out_dir / "summary_by_clock.csv"
    day_path = out_dir / "summary_by_day_clock.csv"

    def _write(path: Path, rows: List[Dict[str, Any]], fieldnames: Sequence[str]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow({k: row.get(k, "") for k in fieldnames})

    detail_cols = [
        "选股日",
        "落盘时钟",
        "宇宙",
        "股票代码",
        "股票名称",
        "满足全部软门槛",
        "当时最新价",
        "买入价",
        "卖出日",
        "卖出开盘价",
        "收益率",
        "状态",
        "原因",
        "滑点_pct",
        "滑点_ticks",
        "数据模式",
        "来源文件",
    ]
    summary_cols = [
        "落盘时钟",
        "宇宙",
        "选股日数",
        "有效笔数",
        "跳过笔数",
        "胜率",
        "平均收益",
        "中位收益",
        "平均收益_小数",
        "中位收益_小数",
        "胜率_小数",
        "滑点_pct",
        "滑点_ticks",
    ]
    day_cols = [
        "选股日",
        "落盘时钟",
        "宇宙",
        "有效笔数",
        "胜率",
        "平均收益",
        "中位收益",
        "平均收益_小数",
        "中位收益_小数",
        "胜率_小数",
    ]
    _write(detail_path, detail_rows, detail_cols)
    _write(summary_path, summary_clock, summary_cols)
    _write(day_path, summary_day, day_cols)

    return {
        "files": len(files),
        "dump_days": sorted(dump_days),
        "dump_clocks": sorted(dump_clocks),
        "detail_rows": len(detail_rows),
        "ok_rows": sum(1 for r in detail_rows if r["状态"] == "ok"),
        "detail_path": str(detail_path),
        "summary_path": str(summary_path),
        "day_path": str(day_path),
        "summary_clock": summary_clock,
    }


def main(argv: Optional[List[str]] = None) -> int:
    _install_ipv4()
    ap = argparse.ArgumentParser(
        description="马总盘中候选粗回测：最新价+滑点买 / 次日开盘卖，按落盘时钟对比"
    )
    ap.add_argument(
        "--dump-dir",
        default="",
        help="候选落盘目录（默认 data/ma_zong_intraday_candidates）",
    )
    ap.add_argument(
        "--out-dir",
        default="",
        help="输出目录（默认 <dump-dir>/backtest_coarse）",
    )
    ap.add_argument(
        "--slippage-pct",
        type=float,
        default=0.001,
        help="买入滑点比例，默认 0.001=0.1%%",
    )
    ap.add_argument(
        "--slippage-ticks",
        type=float,
        default=0.0,
        help="买入额外跳数（×0.01 元），默认 0",
    )
    ap.add_argument(
        "--tick-size",
        type=float,
        default=TICK_SIZE,
        help="最小变动价位，默认 0.01",
    )
    ap.add_argument("--date-from", default="", help="选股日起 YYYY-MM-DD")
    ap.add_argument("--date-to", default="", help="选股日止 YYYY-MM-DD")
    ap.add_argument(
        "--clocks",
        default="",
        help="只统计这些落盘时钟，逗号分隔，如 14:40,14:50",
    )
    ap.add_argument(
        "--universe",
        choices=("both", "hard", "soft"),
        default="both",
        help="统计宇宙：hard=硬通过 / soft=全软满足 / both=两套都出（默认）",
    )
    args = ap.parse_args(argv)

    dump_dir = Path(args.dump_dir) if args.dump_dir else (ROOT / "data" / "ma_zong_intraday_candidates")
    if not dump_dir.is_absolute():
        dump_dir = ROOT / dump_dir
    out_dir = Path(args.out_dir) if args.out_dir else (dump_dir / "backtest_coarse")
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir

    date_from = _parse_date(args.date_from) if args.date_from else None
    date_to = _parse_date(args.date_to) if args.date_to else None
    clocks = None
    if str(args.clocks or "").strip():
        clocks = [_norm_clock(x) for x in str(args.clocks).replace("，", ",").split(",") if x.strip()]

    print(
        "[coarse-bt] dump_dir=%s out_dir=%s slippage_pct=%s slippage_ticks=%s universe=%s"
        % (dump_dir, out_dir, args.slippage_pct, args.slippage_ticks, args.universe)
    )

    files = discover_dump_csvs(dump_dir, date_from=date_from, date_to=date_to, clocks=clocks)
    if not files:
        print(
            "[coarse-bt] 未找到候选 CSV（期望名 YYYY-MM-DD_HHMM.csv）。"
            "请先跑 tools/dump_ma_zong_intraday_candidates.py 落盘，"
            "或盘外冒烟：python tools/dump_ma_zong_intraday_candidates.py --date 2026-10-22 --clock 14:40 --allow-file"
        )
        return 2

    result = run_backtest(
        dump_dir=dump_dir,
        out_dir=out_dir,
        slippage_pct=float(args.slippage_pct),
        slippage_ticks=float(args.slippage_ticks),
        tick_size=float(args.tick_size),
        date_from=date_from,
        date_to=date_to,
        clocks=clocks,
        universe=args.universe,
    )

    n_days = len(result["dump_days"])
    print(
        "[coarse-bt] 文件=%s 选股日=%s 时钟=%s 明细行=%s 有效=%s"
        % (
            result["files"],
            n_days,
            ",".join(result["dump_clocks"]),
            result["detail_rows"],
            result["ok_rows"],
        )
    )
    if n_days < 5:
        print(
            "[coarse-bt] 注意：当前仅 %s 个选股日样本，时点对比仅供冒烟/方向参考，"
            "等定时落盘攒够一段交易日后再下结论。" % n_days
        )

    print("[coarse-bt] —— 按落盘时钟汇总 ——")
    hdr = "%-6s %-10s %6s %6s %8s %10s %10s" % (
        "时钟",
        "宇宙",
        "有效",
        "跳过",
        "胜率",
        "平均",
        "中位",
    )
    print(hdr)
    for row in result["summary_clock"]:
        print(
            "%-6s %-10s %6s %6s %8s %10s %10s"
            % (
                row["落盘时钟"],
                row["宇宙"],
                row["有效笔数"],
                row["跳过笔数"],
                row["胜率"] or "-",
                row["平均收益"] or "-",
                row["中位收益"] or "-",
            )
        )

    print("[coarse-bt] 明细:", result["detail_path"])
    print("[coarse-bt] 时钟汇总:", result["summary_path"])
    print("[coarse-bt] 日×时钟:", result["day_path"])
    print(
        "[coarse-bt] TODO Stage2: 选定时点后生成动态策略 py → QMT 内部 tick 回测 → log 回策略生成回测页（本次未实现）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
