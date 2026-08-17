# -*- coding: utf-8 -*-
"""马总逻辑1 · 单点买 × 单点卖 · 同日日线 OHLC 回测（命令行）

不依赖 tick、不改 UI「回测」页。选股文件 → 买入/卖出成交明细 CSV + 收益汇总。

撮合口径：
  买：low<=MA → min(open, MA)
  卖半仓/涨停：high>=触发价 → max(open, 触发价)
  1455 破 MA20：close<MA20 → close
  第 N 日强清：close

用法:
  python tools/run_ma_zong1_single_daily_backtest.py 选股结果.xls
  python tools/run_ma_zong1_single_daily_backtest.py 选股结果.xls --buy combo
  python tools/run_ma_zong1_single_daily_backtest.py 选股结果.xls --buy ma5
  python tools/run_ma_zong1_single_daily_backtest.py 选股结果.xls --buy ma10 --sell-hold 8
  python tools/run_ma_zong1_single_daily_backtest.py 选股结果.xls --buy ma10 --export-above-ma10
  python tools/run_ma_zong1_single_daily_backtest.py 选股结果.xls --buy ma10 --sell half
  python tools/run_ma_zong1_single_daily_backtest.py 选股结果.xls --buy ma10 --sell full
  python tools/run_ma_zong1_single_daily_backtest.py 选股结果.xls --buy ma10 --sell third

--buy：combo | ma5 | ma10 | ma20（或策略全名/id）

--sell：half（默认，半仓）| full（全仓）| third（三分之一仓）
  对应三条「马总逻辑1 单点卖」仓位变体；跑三遍换 --sell 即可对比。

--sell-hold N（与因子分析 sell_hold 表统一）：
  买入日的【下一个交易日】= 持有第 1 日；第 N 日无条件清仓。
  例：N=2 → 买入次日为1、再下一交易日为2 强清。
  底层引擎按「含买入日」计数，本脚本注入时自动 N+1。

--export-above-ma10：额外输出选股日收盘>MA10 子集（默认 --buy ma10 开启）。
统计默认剔除未完成样本（*_已完成.xlsx）；见 --exclude-incomplete。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 别名 → (策略 id, 输出文件短标签)
BUY_STRATEGIES: Dict[str, Tuple[str, str]] = {
    # 原组合（MA10+MA20 各 1/2）
    "combo": ("strategy_dacdcef1", "combo"),
    "default": ("strategy_dacdcef1", "combo"),
    "ma10_20": ("strategy_dacdcef1", "combo"),
    "ma10/20": ("strategy_dacdcef1", "combo"),
    "strategy_dacdcef1": ("strategy_dacdcef1", "combo"),
    "买：马总逻辑1-涨停后跌破MA10/20各1/2-单点": ("strategy_dacdcef1", "combo"),
    "买：马总逻辑1-涨停后跌破MA5/10/20各1/3-单点": ("strategy_dacdcef1", "combo"),
    # 独立腿
    "ma5": ("strategy_ma5_single", "ma5"),
    "跌ma5": ("strategy_ma5_single", "ma5"),
    "跌破ma5": ("strategy_ma5_single", "ma5"),
    "买：跌ma5": ("strategy_ma5_single", "ma5"),
    "strategy_ma5_single": ("strategy_ma5_single", "ma5"),
    "ma10": ("strategy_ma10_single", "ma10"),
    "跌ma10": ("strategy_ma10_single", "ma10"),
    "跌破ma10": ("strategy_ma10_single", "ma10"),
    "买：跌ma10": ("strategy_ma10_single", "ma10"),
    "strategy_ma10_single": ("strategy_ma10_single", "ma10"),
    "ma20": ("strategy_ma20_single", "ma20"),
    "跌ma20": ("strategy_ma20_single", "ma20"),
    "跌破ma20": ("strategy_ma20_single", "ma20"),
    "买：跌ma20": ("strategy_ma20_single", "ma20"),
    "strategy_ma20_single": ("strategy_ma20_single", "ma20"),
}

# 别名 → (策略 id, 输出文件短标签)
SELL_STRATEGIES: Dict[str, Tuple[str, str]] = {
    "half": ("strategy_32cf91e7", "sell_half"),
    "半仓": ("strategy_32cf91e7", "sell_half"),
    "default": ("strategy_32cf91e7", "sell_half"),
    "strategy_32cf91e7": ("strategy_32cf91e7", "sell_half"),
    "卖：马总逻辑1-开盘涨幅单点半仓+近涨停单点半仓+1455破ma20清仓": (
        "strategy_32cf91e7",
        "sell_half",
    ),
    "full": ("strategy_69ab04e8", "sell_full"),
    "全仓": ("strategy_69ab04e8", "sell_full"),
    "strategy_69ab04e8": ("strategy_69ab04e8", "sell_full"),
    "卖：马总逻辑1-开盘涨幅单点全仓+近涨停单点全仓+1455破ma20清仓": (
        "strategy_69ab04e8",
        "sell_full",
    ),
    "third": ("strategy_6e0a11b6", "sell_third"),
    "1/3": ("strategy_6e0a11b6", "sell_third"),
    "三分之一": ("strategy_6e0a11b6", "sell_third"),
    "三分之一仓": ("strategy_6e0a11b6", "sell_third"),
    "strategy_6e0a11b6": ("strategy_6e0a11b6", "sell_third"),
    "卖：马总逻辑1-开盘涨幅单点三分之一仓+近涨停单点三分之一仓+1455破ma20清仓": (
        "strategy_6e0a11b6",
        "sell_third",
    ),
}

STRAT_DIR = ROOT / "strategy_generator_app" / "config" / "strategies"
DEFAULT_OUT = ROOT / "history_data" / "马总选股逻辑"


def _norm_strategy_key(raw: str) -> str:
    s = str(raw or "").strip()
    s = re.sub(r"\s+", "", s)
    return s.casefold()


def _norm_buy_key(raw: str) -> str:
    return _norm_strategy_key(raw)


def resolve_buy_strategy(spec: str) -> Tuple[str, str]:
    """返回 (strategy_id, out_tag)。"""
    key = _norm_buy_key(spec)
    # 精确别名（已 casefold）
    for alias, pair in BUY_STRATEGIES.items():
        if _norm_buy_key(alias) == key:
            return pair
    # 直接当策略 id / 文件名
    cand = key if key.startswith("strategy_") else f"strategy_{key}"
    p = STRAT_DIR / f"{cand}.json"
    if p.is_file():
        tag = cand.replace("strategy_", "").replace("_single", "")
        return cand, tag
    # 按策略名模糊匹配
    want = key
    for p in STRAT_DIR.glob("strategy_*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        name = _norm_buy_key(str(d.get("name") or ""))
        if name == want or want in name:
            sid = str(d.get("id") or p.stem)
            tag = sid.replace("strategy_", "").replace("_single", "")
            return sid, tag
    known = "combo, ma5, ma10, ma20（或策略全名/id）"
    raise SystemExit(f"未知买入策略：{spec!r}。可用：{known}")


def resolve_sell_strategy(spec: str) -> Tuple[str, str]:
    """返回 (strategy_id, out_tag)。"""
    key = _norm_strategy_key(spec)
    for alias, pair in SELL_STRATEGIES.items():
        if _norm_strategy_key(alias) == key:
            return pair
    cand = key if key.startswith("strategy_") else f"strategy_{key}"
    p = STRAT_DIR / f"{cand}.json"
    if p.is_file():
        tag = "sell_" + cand.replace("strategy_", "")
        return cand, tag
    want = key
    for p in STRAT_DIR.glob("strategy_*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        display = str(d.get("name") or "")
        if "卖" not in display:
            continue
        name = _norm_strategy_key(display)
        if name == want or want in name:
            sid = str(d.get("id") or p.stem)
            tag = "sell_" + sid.replace("strategy_", "")
            return sid, tag
    known = "half, full, third（或策略全名/id）"
    raise SystemExit(f"未知卖出策略：{spec!r}。可用：{known}")

TRADE_CSV_COLS = [
    "日期",
    "时间",
    "代码",
    "股票名称",
    "选股日",
    "方向",
    "价格",
    "数量",
    "金额",
    "交易后持仓",
    "规则名",
    "腿键",
    "触发信息",
    "start_date",
    "end_date",
]


def _load_strategy(sid: str) -> Tuple[str, str, Dict[str, Any]]:
    p = STRAT_DIR / f"{sid}.json"
    if not p.is_file():
        raise FileNotFoundError(f"策略不存在: {p}")
    d = json.loads(p.read_text(encoding="utf-8"))
    return (
        str(d.get("name") or sid),
        str(d.get("strategy_code") or ""),
        dict(d.get("strategy_params") or {}),
    )


def _norm_code6(v: Any) -> str:
    s = str(v or "").strip()
    if not s or s.lower() in ("nan", "none"):
        return ""
    if "." in s:
        s = s.split(".", 1)[0]
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)[-6:]


def _parse_d(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        import pandas as pd

        ts = pd.to_datetime(v, errors="coerce")
        if ts is not None and not pd.isna(ts):
            return ts.date()
    except Exception:
        pass
    try:
        return date.fromisoformat(str(v).strip()[:10])
    except Exception:
        return None


def load_selection_by_day(path: Path) -> Dict[date, List[str]]:
    import pandas as pd

    ext = path.suffix.lower()
    if ext == ".csv":
        df = pd.read_csv(path, dtype=str)
    elif ext in (".xlsx", ".xls"):
        df = pd.read_excel(path, dtype=str)
    else:
        raise ValueError("选股文件请用 .xls / .xlsx / .csv")
    if df is None or df.empty:
        raise ValueError("选股文件为空")

    def _find(cands: Tuple[str, ...]) -> Optional[str]:
        cols = list(df.columns)
        norm = {str(c).strip().replace("\n", ""): c for c in cols}
        for k in cands:
            if k in norm:
                return str(norm[k])
        for c in cols:
            n = str(c).strip().replace("\n", "")
            for k in cands:
                if k in n:
                    return str(c)
        return None

    dc = _find(("选股日", "screen_as_of", "基准日", "选股基准日", "选股日期", "交易日期"))
    cc = _find(("股票代码", "证券代码", "代码", "code", "股票代码(无后缀)"))
    if not dc or not cc:
        raise ValueError("选股文件需含「选股日」与「股票代码」列")

    out: Dict[date, List[str]] = {}
    for _, row in df.iterrows():
        d = _parse_d(row.get(dc))
        c6 = _norm_code6(row.get(cc))
        if d is None or not c6:
            continue
        bucket = out.setdefault(d, [])
        if c6 not in bucket:
            bucket.append(c6)
    if not out:
        raise ValueError("未解析到任何选股日/代码")
    return dict(sorted(out.items(), key=lambda x: x[0]))


def _cell(v: Any) -> str:
    """导出单元格：保留数字 0（勿用 `x or ''`，否则清仓后持仓会变成空）。"""
    if v is None:
        return ""
    if isinstance(v, float) and v != v:  # NaN
        return ""
    return str(v)


def _trade_row(t: Dict[str, Any], *, sel: str, start_s: str, end_s: str) -> Dict[str, str]:
    side = str(t.get("side") or "").lower()
    direction = "买入" if side in ("buy", "买入", "b") else "卖出"
    ti = str(t.get("trigger_info") or "").strip()
    if sel and f"[选股日 {sel}]" not in ti:
        ti = f"[选股日 {sel}] {ti}".strip()
    code = _norm_code6(t.get("code"))
    return {
        "日期": _cell(t.get("date")),
        "时间": _cell(t.get("time")),
        "代码": code,
        "股票名称": _cell(t.get("stock_name")),
        "选股日": sel,
        "方向": direction,
        "价格": _cell(t.get("price")),
        "数量": _cell(t.get("volume")),
        "金额": _cell(t.get("amount")),
        "交易后持仓": _cell(t.get("position_after")),
        "规则名": _cell(t.get("rule_name") or t.get("name")),
        "腿键": _cell(t.get("leg_key")),
        "触发信息": ti,
        "start_date": start_s,
        "end_date": end_s,
    }


def write_trades_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_CSV_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _parse_ymd(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        if isinstance(v, float) and v != v:
            return None
    except Exception:
        pass
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none"):
        return None
    s10 = s[:10].replace("/", "-")
    try:
        return datetime.strptime(s10, "%Y-%m-%d").date()
    except Exception:
        try:
            import pandas as _pd

            ts = _pd.to_datetime(s, errors="coerce")
            if ts is None or _pd.isna(ts):
                return None
            return ts.date()
        except Exception:
            return None


def resolve_last_available_trade_date() -> Optional[date]:
    """日线缓存同步日优先，否则交易日历上不晚于今天的最近交易日。"""
    try:
        from utils.daily_cache_reader import get_sync_trade_date

        d = get_sync_trade_date()
        if d is not None:
            return d
    except Exception:
        pass
    try:
        from datetime import timedelta

        from utils.trading_day import get_trading_dates_in_range_sorted

        today = date.today()
        lst = get_trading_dates_in_range_sorted(today - timedelta(days=30), today)
        return lst[-1] if lst else today
    except Exception:
        return date.today()


def annotate_sample_completion(
    rows: List[Dict[str, Any]],
    *,
    last_available: Optional[date] = None,
) -> Tuple[int, int]:
    """给每行打 样本完成 / 未完成原因。返回 (完成数, 未完成数)。

    未完成判定（满足任一）：
      1) 剩余持仓数量 != 0（含未清仓盯市）
      2) 备注含「未清仓」
      3) 无有效收益率pct
      4) 仍持仓且计划 end_date > 最后可得交易日（持有窗未走完）
    已清仓但 end_date 仍在未来 → 策略提前卖出，算完成。
    """
    last_d = last_available or resolve_last_available_trade_date()
    n_ok = n_bad = 0
    for r in rows:
        reasons: List[str] = []
        try:
            rem = int(r.get("剩余持仓数量") or 0)
        except (TypeError, ValueError):
            rem = 0
            reasons.append("剩余持仓无法解析")
        note = str(r.get("备注") or "")
        if rem != 0:
            reasons.append(f"剩余持仓={rem}")
        if "未清仓" in note:
            reasons.append("备注含未清仓")
        ret_ok = False
        try:
            float(r.get("收益率pct"))
            ret_ok = True
        except (TypeError, ValueError):
            pass
        if not ret_ok:
            reasons.append("无收益率")
        end_d = _parse_ymd(r.get("end_date"))
        if rem != 0 and last_d is not None and end_d is not None and end_d > last_d:
            reasons.append(f"持有窗未结束(end_date={end_d}>最后可得{last_d})")
        seen = set()
        uniq: List[str] = []
        for x in reasons:
            if x not in seen:
                seen.add(x)
                uniq.append(x)
        ok = len(uniq) == 0
        r["样本完成"] = bool(ok)
        r["未完成原因"] = "" if ok else "；".join(uniq)
        if last_d is not None:
            r["最后可得交易日"] = last_d.strftime("%Y-%m-%d")
        if ok:
            n_ok += 1
        else:
            n_bad += 1
    return n_ok, n_bad


def _write_stock_xlsx(path: Path, sdf: "pd.DataFrame") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with __import__("pandas").ExcelWriter(path, engine="openpyxl") as writer:
        sdf.to_excel(writer, index=False)
        try:
            ws = writer.sheets[list(writer.sheets.keys())[0]]
            for j, col in enumerate(sdf.columns, 1):
                if str(col) != "代码":
                    continue
                for i in range(2, len(sdf) + 2):
                    cell = ws.cell(i, j)
                    cell.number_format = "@"
                    cell.value = str(cell.value or "")
        except Exception:
            pass


def export_above_ma10_sheet(sdf: "pd.DataFrame", out_path: Path) -> Tuple[int, int]:
    """按选股日收盘价 > MA10 过滤按票表，写出 out_path。

    返回 (上MA10行数, 总行数)。需要列：选股日、代码、以及 MA10（选股回填）或从日线重算。
    会写入列：sel_close、above_ma10、MA10（若缺失则补）。
    """
    import math

    import pandas as pd
    from utils.daily_cache_reader import load_daily_from_cache

    if sdf is None or sdf.empty:
        raise ValueError("按票表为空，无法导出上MA10")

    df = sdf.copy()
    if "选股日" not in df.columns or "代码" not in df.columns:
        raise ValueError("按票表缺少 选股日/代码 列")

    def _finite(v: Any) -> Optional[float]:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        if math.isnan(x) or math.isinf(x):
            return None
        return x

    df["_sel"] = pd.to_datetime(df["选股日"], errors="coerce").dt.date
    df["_code"] = df["代码"].map(_norm_code6)
    if "MA10" in df.columns:
        df["_ma10"] = pd.to_numeric(df["MA10"], errors="coerce")
    else:
        df["_ma10"] = float("nan")

    cache: Dict[str, Any] = {}
    closes: List[Optional[float]] = []
    ma10s: List[Optional[float]] = []
    above: List[bool] = []
    n = len(df)
    codes = df["_code"].tolist()
    sels = df["_sel"].tolist()
    ma10_col = [_finite(x) for x in df["_ma10"].tolist()]
    for i in range(n):
        code = str(codes[i] or "")
        sel = sels[i]
        ma10 = ma10_col[i]
        cl: Optional[float] = None
        if code and sel is not None and not (isinstance(sel, float)):
            if code not in cache:
                try:
                    cache[code] = load_daily_from_cache(code, through_date=None)
                except Exception:
                    cache[code] = None
            dd = cache[code]
            if dd is not None and not getattr(dd, "empty", True):
                sub = dd[dd["date"] == sel]
                if not sub.empty:
                    cl = float(sub.iloc[-1]["close"])
                if cl is not None and ma10 is None:
                    hist = dd[dd["date"] <= sel].sort_values("date")
                    if len(hist) >= 10:
                        ma10 = float(hist["close"].astype(float).tail(10).mean())
        ok = bool(cl is not None and ma10 is not None and ma10 > 0 and float(cl) > float(ma10))
        closes.append(cl)
        ma10s.append(ma10)
        above.append(ok)
        if (i + 1) % 400 == 0:
            print(f"  上MA10过滤进度 {i + 1}/{n}", flush=True)

    df["sel_close"] = closes
    df["above_ma10"] = above
    if "MA10" not in df.columns:
        df["MA10"] = ma10s
    else:
        miss = pd.to_numeric(df["MA10"], errors="coerce").isna()
        if bool(miss.any()):
            filled = list(df["MA10"])
            for j, m in enumerate(ma10s):
                if bool(miss.iloc[j]) and m is not None:
                    filled[j] = m
            df["MA10"] = filled

    out = df[df["above_ma10"]].drop(columns=["_sel", "_code", "_ma10"], errors="ignore")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        out.to_excel(writer, index=False)
        try:
            ws = writer.sheets[list(writer.sheets.keys())[0]]
            for j, col in enumerate(out.columns, 1):
                if str(col) != "代码":
                    continue
                for ii in range(2, len(out) + 2):
                    cell = ws.cell(ii, j)
                    cell.number_format = "@"
                    cell.value = str(cell.value or "")
        except Exception:
            pass
    return int(len(out)), int(len(df))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="马总单点买×单点卖 · 同日日线 OHLC 回测（选股文件 → 买卖明细 + 收益汇总）"
    )
    ap.add_argument("selection", help="选股结果文件（xls/xlsx/csv，需含选股日+代码）")
    ap.add_argument(
        "--entry-window",
        type=int,
        default=10,
        help="买入挂单窗口交易日数（选股日 T+1 起，默认 10）",
    )
    ap.add_argument(
        "--sell-hold",
        type=int,
        default=8,
        help=(
            "卖出持有交易日数（默认 8）：买入【次日】为第 1 日，第 N 日强清；"
            "与因子分析 hold 表同口径（注入引擎时自动 +1）"
        ),
    )
    ap.add_argument(
        "--cash",
        type=float,
        default=100_000_000.0,
        help="每档初始资金（默认 1e8）",
    )
    ap.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT),
        help="输出目录（默认 history_data/马总选股逻辑）",
    )
    ap.add_argument(
        "--buy",
        default="combo",
        metavar="STRATEGY",
        help=(
            "买入策略：combo（默认，MA10/20 各1/2）| ma5 | ma10 | ma20；"
            "也可写策略全名或 id（如 买：跌MA5 / strategy_ma5_single）"
        ),
    )
    ap.add_argument(
        "--sell",
        default="half",
        metavar="STRATEGY",
        help=(
            "卖出策略：half（默认，半仓）| full（全仓）| third（三分之一仓）；"
            "也可写策略全名或 id"
        ),
    )
    ap.add_argument(
        "--max-days",
        type=int,
        default=0,
        help="最多处理前 N 个选股日（0=全部；冒烟可设 1）",
    )
    ap.add_argument(
        "--export-above-ma10",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "按票汇总后额外输出选股日收盘>MA10 子集（文件名 *_收盘上MA10.xlsx）。"
            "默认：--buy ma10 时开启；可用 --export-above-ma10 / --no-export-above-ma10 强制开关"
        ),
    )
    ap.add_argument(
        "--exclude-incomplete",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "统计/上MA10 默认只用已完成样本（剔除未清仓盯市等）；"
            "全量按票仍会写出。--no-exclude-incomplete 则上MA10也用全量"
        ),
    )
    args = ap.parse_args()

    try:
        buy_id, buy_tag = resolve_buy_strategy(args.buy)
        sell_id, sell_tag = resolve_sell_strategy(args.sell)
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 1

    pair_tag = f"{buy_tag}-{sell_tag}"

    sel_path = Path(args.selection)
    if not sel_path.is_file():
        # 相对仓库根再试
        alt = ROOT / args.selection
        if alt.is_file():
            sel_path = alt
        else:
            print(f"选股文件不存在: {args.selection}", file=sys.stderr)
            return 1

    entry_w = max(1, int(args.entry_window))
    sell_hold = max(1, int(args.sell_hold))
    # 统一口径：次日=第1日；引擎 code_sell_day_index 含买入日 → 注入 N+1
    engine_hold_n = sell_hold + 1
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    buy_name, buy_code, buy_params0 = _load_strategy(buy_id)
    sell_name, sell_code, sell_params0 = _load_strategy(sell_id)
    print(f"买：{buy_name}（id={buy_id}, tag={buy_tag}）")
    print(f"卖：{sell_name}（id={sell_id}, tag={sell_tag}）")
    print(f"选股：{sel_path}")
    print(
        f"入口窗口={entry_w} 卖持有={sell_hold}（次日=第1日；引擎N={engine_hold_n}） "
        f"fill_mode=same_day_ohlc"
    )

    by_day = load_selection_by_day(sel_path)
    days = list(by_day.keys())
    if int(args.max_days or 0) > 0:
        days = days[: int(args.max_days)]
    print(f"选股日 {len(days)} 个，标的合计 {sum(len(by_day[d]) for d in days)} 条")

    from strategy_generator_app.trading_calendar import (
        backtest_window_from_selection_day,
        sim_hold_days_covering_entry_window,
    )
    from strategy_generator_app.backtest import run_backtest_segmented, compute_metrics

    def _get_name(c: str) -> str:
        try:
            from utils.stock_info_manager import get_stock_name

            return (get_stock_name(c) or "").strip()
        except Exception:
            try:
                from strategy_generator_app.account_provider import get_stock_name as _gn

                return (_gn(c) or "").strip()
            except Exception:
                return ""

    sim_days = sim_hold_days_covering_entry_window(entry_w, engine_hold_n)
    # 计划结束日：覆盖「最晚买入 + 次日起持有 N 日」
    calendar_hold_n = entry_w + sell_hold
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    buy_rows: List[Dict[str, str]] = []
    sell_rows: List[Dict[str, str]] = []
    day_summaries: List[Dict[str, Any]] = []

    def _progress(msg: str, pct: Optional[int] = None) -> None:
        if pct is None:
            print(f"  {msg}", flush=True)
        else:
            print(f"  [{pct:3d}%] {msg}", flush=True)

    for i, d in enumerate(days):
        codes = by_day[d]
        start_d, end_d, hint = backtest_window_from_selection_day(
            d, start_next_trading_day=True, hold_trading_days=sim_days
        )
        sel_s = d.strftime("%Y-%m-%d")
        tag = f"[{i + 1}/{len(days)}] {sel_s} {len(codes)}只"
        if start_d is None or end_d is None:
            print(f"{tag} 跳过：{hint}")
            day_summaries.append(
                {
                    "选股日": sel_s,
                    "回测开始": "",
                    "回测结束": "",
                    "股票数": len(codes),
                    "总收益率%": None,
                    "成交笔数": 0,
                    "备注": hint,
                }
            )
            continue
        start_s = start_d.strftime("%Y-%m-%d")
        end_s = end_d.strftime("%Y-%m-%d")
        print(f"{tag} 窗口 {start_s}~{end_s}（{hint}，仿真{sim_days}日）")

        buy_params = dict(buy_params0)
        buy_params["entry_window_trading_days"] = entry_w
        buy_params["selection_date_by_code"] = {c: sel_s for c in codes}
        buy_params["_filled_legs"] = []

        sell_params = dict(sell_params0)
        sell_params["scheduled_clear_on_sell_day"] = engine_hold_n
        sell_params["sell_hold_trading_days"] = engine_hold_n
        sell_params["entry_window_trading_days"] = engine_hold_n
        sell_params["_filled_legs"] = []
        # 供意图/日志辨认用户口径（次日=1）
        sell_params["sell_hold_from_next_day"] = sell_hold

        segments = [
            {
                "strategy_code": buy_code,
                "strategy_params": buy_params,
                "strategy_generation_time": "09:25",
                "strategy_run_start_time": "09:30",
                "strategy_run_end_time": "15:00",
                "name": "单点买",
            },
            {
                "strategy_code": sell_code,
                "strategy_params": sell_params,
                "strategy_generation_time": "09:25",
                "strategy_run_start_time": "09:30",
                "strategy_run_end_time": "15:00",
                "name": "单点卖",
            },
        ]
        try:
            result = run_backtest_segmented(
                segments,
                codes,
                start_d,
                end_d,
                initial_cash=float(args.cash),
                get_stock_name=_get_name,
                use_engine_form=False,
                use_tick_level=False,
                fill_mode="same_day_ohlc",
                progress=_progress if len(days) == 1 else None,
                clear_ticks_on_finish=False,
            )
        except Exception as ex:
            print(f"{tag} 失败：{ex}")
            day_summaries.append(
                {
                    "选股日": sel_s,
                    "回测开始": start_s,
                    "回测结束": end_s,
                    "股票数": len(codes),
                    "总收益率%": None,
                    "成交笔数": 0,
                    "备注": str(ex),
                }
            )
            continue

        metrics = compute_metrics(
            result.get("equity_curve") or [],
            result.get("trades") or [],
            float(args.cash),
            result.get("final_positions"),
            result.get("last_prices"),
        )
        tr = float(metrics.get("total_return") or 0) * 100
        tc = int(metrics.get("trade_count") or 0)
        print(f"  → 收益 {tr:.2f}% 成交 {tc} fill_mode={result.get('fill_mode')}")

        n_buy = n_sell = 0
        for t in result.get("trades") or []:
            side = str(t.get("side") or "").lower()
            row = _trade_row(t, sel=sel_s, start_s=start_s, end_s=end_s)
            if side in ("buy", "买入", "b"):
                # 跳过接续注入流水（本 CLI 无 scheduled 注入）
                if bool(t.get("injected") or t.get("blotter_only")):
                    continue
                if "买入注入" in str(t.get("rule_name") or ""):
                    continue
                buy_rows.append(row)
                n_buy += 1
            else:
                sell_rows.append(row)
                n_sell += 1
        day_summaries.append(
            {
                "选股日": sel_s,
                "回测开始": start_s,
                "回测结束": end_s,
                "股票数": len(codes),
                "总收益率%": round(tr, 4),
                "成交笔数": tc,
                "买入笔数": n_buy,
                "卖出笔数": n_sell,
                "备注": "",
            }
        )

    buy_csv = out_dir / f"回测成交明细_日线-{pair_tag}买入_{stamp}.csv"
    sell_csv = out_dir / f"回测成交明细_日线-{pair_tag}卖出_{stamp}.csv"
    write_trades_csv(buy_csv, buy_rows)
    write_trades_csv(sell_csv, sell_rows)
    print(f"买入明细：{buy_csv}（{len(buy_rows)} 笔）")
    print(f"卖出明细：{sell_csv}（{len(sell_rows)} 笔）")

    import pandas as pd

    day_xlsx = out_dir / f"各日选股收益汇总_日线-{pair_tag}-单点_{stamp}.xlsx"
    pd.DataFrame(day_summaries).to_excel(day_xlsx, index=False)
    print(f"按日汇总：{day_xlsx}")

    # 按选股日+代码收益汇总（复用 merge 工具）
    stock_xlsx = out_dir / f"各日选股收益汇总_日线-{pair_tag}-单点_按票_{stamp}.xlsx"
    if buy_rows and sell_rows:
        try:
            from tools.merge_backtest_trades_by_selection import (
                aggregate,
                apply_end_date_from_trading_calendar,
                apply_mark_and_returns,
                _build_prices_by_mark_date,
            )

            rows = aggregate(buy_csv, sell_csv)
            apply_end_date_from_trading_calendar(
                rows, from_t1=True, hold_n=calendar_hold_n
            )
            prices_by_mark, price_warn = _build_prices_by_mark_date(
                rows, mark_n=calendar_hold_n, use_nth_trading_day=False
            )
            if price_warn:
                print(f"⚠ 盯市：{price_warn}")
            apply_mark_and_returns(
                rows,
                prices_by_mark,
                price_warn,
                mark_n=calendar_hold_n,
                use_nth_trading_day=False,
            )
            try:
                from tools.merge_backtest_trades_by_selection import (
                    apply_selection_file_fields,
                )

                sel_msg = apply_selection_file_fields(rows, sel_path)
                print(sel_msg)
            except Exception as ex:
                print(f"⚠ 选股列回填失败：{ex}")

            last_d = resolve_last_available_trade_date()
            n_ok, n_bad = annotate_sample_completion(rows, last_available=last_d)
            print(
                f"样本完成标注：完成 {n_ok} / 未完成 {n_bad}"
                + (f"（最后可得交易日 {last_d}）" if last_d else "")
            )

            sdf = pd.DataFrame(rows)
            if "代码" in sdf.columns:
                sdf["代码"] = sdf["代码"].map(
                    lambda x: _norm_code6(x) if _norm_code6(x) else str(x or "")
                )
            _write_stock_xlsx(stock_xlsx, sdf)

            sdf_done = sdf
            if "样本完成" in sdf.columns:
                sdf_done = sdf[sdf["样本完成"] == True].copy()  # noqa: E712
            done_xlsx = out_dir / (
                f"各日选股收益汇总_日线-{pair_tag}-单点_按票_{stamp}_已完成.xlsx"
            )
            _write_stock_xlsx(done_xlsx, sdf_done)

            def _ret_stats(frame: "pd.DataFrame") -> Tuple[int, Optional[float], Optional[float]]:
                arets = pd.to_numeric(frame.get("收益率pct"), errors="coerce").dropna()
                if len(arets) == 0:
                    return 0, None, None
                return int(len(arets)), float(arets.mean()), float(arets.median())

            n_all, m_all, med_all = _ret_stats(sdf)
            n_done, m_done, med_done = _ret_stats(sdf_done)
            if n_all:
                print(
                    f"按票汇总(全量)：{stock_xlsx}（n={n_all} "
                    f"均值={m_all:.2f}% 中位={med_all:.2f}%）"
                )
            else:
                print(f"按票汇总(全量)：{stock_xlsx}（无收益率）")
            if n_done:
                print(
                    f"按票汇总(已完成)：{done_xlsx}（n={n_done}/{n_all} "
                    f"均值={m_done:.2f}% 中位={med_done:.2f}%；已剔除未完成 {n_bad}）"
                )
            else:
                print(f"按票汇总(已完成)：{done_xlsx}（n=0/{n_all}，无完成样本）")

            do_above = (
                bool(args.export_above_ma10)
                if args.export_above_ma10 is not None
                else (buy_tag == "ma10")
            )
            if do_above:
                above_src = sdf_done if bool(args.exclude_incomplete) else sdf
                above_xlsx = out_dir / (
                    f"各日选股收益汇总_日线-{pair_tag}-单点_按票_{stamp}_收盘上MA10.xlsx"
                )
                if bool(args.exclude_incomplete):
                    above_xlsx = out_dir / (
                        f"各日选股收益汇总_日线-{pair_tag}-单点_按票_{stamp}_已完成_收盘上MA10.xlsx"
                    )
                try:
                    src_lab = "已完成" if bool(args.exclude_incomplete) else "全量"
                    print(f"导出上MA10子集（选股日收盘>MA10，基于{src_lab}）…", flush=True)
                    if above_src is None or above_src.empty:
                        print("⚠ 上MA10导出跳过：无可用样本", file=sys.stderr)
                    else:
                        n_above, n_base = export_above_ma10_sheet(above_src, above_xlsx)
                        above_df = pd.read_excel(above_xlsx)
                        an, am, amed = _ret_stats(above_df)
                        if an:
                            print(
                                f"上MA10按票：{above_xlsx}（{n_above}/{n_base} "
                                f"均值={am:.2f}% 中位={amed:.2f}%）"
                            )
                        else:
                            print(
                                f"上MA10按票：{above_xlsx}（{n_above}/{n_base}，无收益率）"
                            )
                except Exception as ex:
                    print(f"⚠ 上MA10导出失败：{ex}", file=sys.stderr)
        except Exception as ex:
            print(f"按票汇总失败（买卖明细已写出）：{ex}", file=sys.stderr)
    else:
        print("买入或卖出为空，跳过按票汇总")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
