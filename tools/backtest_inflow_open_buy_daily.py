# -*- coding: utf-8 -*-
"""
主力净流入前100 · 开盘买入日线弱化回测（无 tick 时用）

规则：
- 选股日 T（默认 < 2026-06-09，数据来自 history_data/存档 的每日净流入 CSV）
- 买入：T 的下一交易日 open
- 卖出：买入日的下一交易日 close（「次日卖出」弱化为收盘卖）
- 一字涨停（开≈涨停且收≈涨停）跳过，无法模拟开板
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ARCH = ROOT / "history_data" / "存档"
INFLOW_LIVE = ROOT / "history_data" / "个股主力净流入"
CACHE = ROOT / "data" / "daily_cache"
OUT_DIR = ROOT / "history_data" / "主力净流入回测"
DATE_RE = re.compile(r"个股主力净流入_(\d{8})\.csv$", re.I)
RANK_COL = "净流入占流通%"
LIMIT_EPS = 0.011
COMMISSION = 0.0003

CANONICAL_COLS = [
    "序号",
    "代码",
    "名称",
    "最新价",
    "今日涨跌幅",
    "今日主力净流入-净额",
    "流通市值",
    "净流入占流通%",
    "今日主力净流入-净占比",
    "今日超大单净流入-净额",
    "今日超大单净流入-净占比",
    "今日大单净流入-净额",
    "今日大单净流入-净占比",
    "今日中单净流入-净额",
    "今日中单净流入-净占比",
    "今日小单净流入-净额",
    "今日小单净流入-净占比",
]
OLD_FORMAT_RENAME = {
    "今日\n涨跌幅": "今日涨跌幅",
    "今日主力净流入": "今日主力净流入-净额",
    "今日超大单净流入": "今日主力净流入-净占比",
    "今日大单净流入": "今日超大单净流入-净额",
    "今日中单净流入": "今日超大单净流入-净占比",
    "今日小单净流入": "今日大单净流入-净额",
    "净额": "今日大单净流入-净占比",
    "净占比": "今日中单净流入-净额",
    "净额.1": "今日中单净流入-净占比",
    "净占比.1": "今日小单净流入-净额",
    "净额.2": "今日小单净流入-净占比",
}


def _parse_date_from_name(path: Path) -> str | None:
    m = DATE_RE.search(path.name)
    if not m:
        return None
    s = m.group(1)
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


def _norm_code(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def _normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    work.columns = [str(c).strip() for c in work.columns]
    if "相关" in work.columns or "今日\n涨跌幅" in work.columns or "净额.2" in work.columns:
        work = work.rename(columns=OLD_FORMAT_RENAME)
        if "相关" in work.columns:
            work = work.drop(columns=["相关"])
    for c in CANONICAL_COLS:
        if c not in work.columns:
            work[c] = pd.NA
    return work[CANONICAL_COLS].copy()


def _full_code(code6: str) -> str:
    c = str(code6).zfill(6)
    if c.startswith(("5", "6", "9")):
        return f"{c}.SH"
    if c.startswith(("4", "8")):
        return f"{c}.BJ"
    return f"{c}.SZ"


def _load_daily(code6: str, cache: dict) -> pd.DataFrame | None:
    if code6 in cache:
        return cache[code6]
    fp = CACHE / f"{_full_code(code6)}.csv"
    if not fp.is_file():
        for suf in (".SZ", ".SH", ".BJ"):
            alt = CACHE / f"{code6}{suf}.csv"
            if alt.is_file():
                fp = alt
                break
        else:
            cache[code6] = None
            return None
    d = pd.read_csv(fp)
    d["date"] = pd.to_datetime(d["date"]).dt.normalize()
    for c in ("open", "high", "low", "close"):
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["date", "open", "close"]).sort_values("date").reset_index(drop=True)
    cache[code6] = d
    return d


def _market_calendar(cache: dict) -> list[pd.Timestamp]:
    # 用几只活跃股并集作交易日
    days = set()
    for code in ("000001", "600000", "000300"):
        d = _load_daily(code, cache)
        if d is not None:
            days.update(d["date"].tolist())
    return sorted(days)


def _next_trade_day(cal: list[pd.Timestamp], day: pd.Timestamp) -> pd.Timestamp | None:
    day = pd.Timestamp(day).normalize()
    for d in cal:
        if d > day:
            return d
    return None


def _limit_up(code6: str, pre_close: float) -> float:
    if pre_close <= 0:
        return 0.0
    if code6.startswith(("300", "301", "688", "689")):
        return round(pre_close * 1.2, 2)
    return round(pre_close * 1.1, 2)


def _read_csv(fp: Path) -> pd.DataFrame:
    last_err = None
    for enc in ("utf-8-sig", "gbk", "gb18030"):
        try:
            return pd.read_csv(fp, encoding=enc)
        except Exception as e:
            last_err = e
    raise last_err  # type: ignore


def build_selection(before: str, top_n: int, src_dirs: list[Path]) -> pd.DataFrame:
    before_ts = pd.Timestamp(before)
    files: list[Path] = []
    for d in src_dirs:
        if d.is_dir():
            files.extend(d.glob("个股主力净流入_*.csv"))
    files = sorted(set(files), key=lambda p: p.name)
    frames = []
    for fp in files:
        sel = _parse_date_from_name(fp)
        if not sel:
            continue
        if pd.Timestamp(sel) >= before_ts:
            continue
        try:
            raw = _read_csv(fp)
        except Exception as e:
            print(f"跳过无法读取: {fp.name} ({e})")
            continue
        df = _normalize_frame(raw)
        # 存档版常无无「净流入占流通%」，但导出时已按排行序号排好 → 用序号取前 N
        if RANK_COL in df.columns and df[RANK_COL].notna().any():
            df[RANK_COL] = pd.to_numeric(df[RANK_COL], errors="coerce")
            df = df.dropna(subset=[RANK_COL]).sort_values(
                RANK_COL, ascending=False, kind="mergesort"
            )
        elif "序号" in df.columns:
            df["序号"] = pd.to_numeric(df["序号"], errors="coerce")
            df = df.dropna(subset=["序号"]).sort_values("序号", ascending=True, kind="mergesort")
        else:
            print(f"跳过无排行列: {fp.name}")
            continue
        df = df.head(int(top_n)).copy()
        if df.empty:
            print(f"选股 {sel}: 0 条")
            continue
        df.insert(0, "选股日", sel)
        df.insert(1, "股票代码", df["代码"].map(_norm_code))
        df.insert(2, "股票名称", df["名称"].astype(str).where(df["名称"].notna(), ""))
        df["代码"] = df["股票代码"]
        frames.append(df)
        print(f"选股 {sel}: {len(df)} 条 (源 {len(raw)})")
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    cols = ["选股日", "股票代码", "股票名称"] + CANONICAL_COLS
    return out.reindex(columns=[c for c in cols if c in out.columns or c in ("选股日", "股票代码", "股票名称")])


def backtest_daily(sel: pd.DataFrame, amount: float = 50000.0) -> pd.DataFrame:
    cache: dict = {}
    cal = _market_calendar(cache)
    if not cal:
        raise SystemExit("daily_cache 无交易日")
    cal_set = set(cal)

    rows = []
    n_skip = {
        "no_daily": 0,
        "sel_not_in_cal": 0,
        "no_buy_day": 0,
        "no_sell_day": 0,
        "bad_px": 0,
        "limit_sealed": 0,
    }
    for _, r in sel.iterrows():
        code = _norm_code(r.get("股票代码") or r.get("代码"))
        name = str(r.get("股票名称") or r.get("名称") or "")
        sel_day = pd.Timestamp(r["选股日"]).normalize()
        if not code:
            continue
        # 选股日必须在日历内，避免缓存缺口把「下一交易日」跨到很久之后
        if sel_day not in cal_set:
            n_skip["sel_not_in_cal"] += 1
            continue
        daily = _load_daily(code, cache)
        if daily is None or daily.empty:
            n_skip["no_daily"] += 1
            continue
        buy_day = _next_trade_day(cal, sel_day)
        if buy_day is None:
            n_skip["no_buy_day"] += 1
            continue
        sell_day = _next_trade_day(cal, buy_day)
        if sell_day is None:
            n_skip["no_sell_day"] += 1
            continue
        b = daily[daily["date"] == buy_day]
        s = daily[daily["date"] == sell_day]
        if b.empty or s.empty:
            n_skip["no_buy_day" if b.empty else "no_sell_day"] += 1
            continue
        open_px = float(b.iloc[0]["open"])
        buy_close = float(b.iloc[0]["close"])
        sell_close = float(s.iloc[0]["close"])
        prev = daily[daily["date"] < buy_day]
        pre_close = float(prev.iloc[-1]["close"]) if len(prev) else open_px
        if open_px <= 0 or sell_close <= 0:
            n_skip["bad_px"] += 1
            continue
        lu = _limit_up(code, pre_close)
        # 一字板：开收都贴涨停 → 跳过
        if lu > 0 and open_px >= lu - LIMIT_EPS and buy_close >= lu - LIMIT_EPS:
            n_skip["limit_sealed"] += 1
            continue

        vol = max(100, int(amount / open_px / 100) * 100)
        buy_amt = open_px * vol * (1 + COMMISSION)
        sell_amt = sell_close * vol * (1 - COMMISSION)
        ret = (sell_amt / buy_amt - 1.0) * 100.0
        gap = (open_px / pre_close - 1.0) * 100.0 if pre_close > 0 else np.nan

        row = {
            "选股日": sel_day.strftime("%Y-%m-%d"),
            "end_date": sell_day.strftime("%Y-%m-%d"),
            "代码": code,
            "股票名称": name,
            "买入时间": "09:30:00",
            "买入笔数": 1,
            "卖出笔数": 1,
            "买入金额合计": round(open_px * vol, 2),
            "卖出金额合计": round(sell_close * vol, 2),
            "买入数量合计": vol,
            "卖出数量合计": vol,
            "剩余持仓数量": 0,
            "净现金流_卖减买": round(sell_close * vol - open_px * vol, 2),
            "盯市日期": np.nan,
            "收盘价": np.nan,
            "剩余市值_盯市": np.nan,
            "收益率pct": round(ret, 4),
            "备注": "已清仓(日线弱化:次日收盘卖)",
            "买入日": buy_day.strftime("%Y-%m-%d"),
            "买入成交价": round(open_px, 2),
            "触发信息": (
                f"[选股日 {sel_day.strftime('%Y-%m-%d')}] 开盘买入(日线): "
                f"买={buy_day.strftime('%Y-%m-%d')} open={open_px:.2f} "
                f"卖={sell_day.strftime('%Y-%m-%d')} close={sell_close:.2f} "
                f"开盘涨跌={gap:.2f}%"
            ),
            "股票代码": code,
            "开盘涨跌幅pct": round(gap, 4) if gap == gap else np.nan,
            "昨收盘": round(pre_close, 2) if pre_close == pre_close else np.nan,
            "卖出收盘价": round(sell_close, 2),
        }
        # 带上选股原始列
        for c in CANONICAL_COLS:
            if c in r.index and c not in row:
                row[c] = r.get(c)
        if "序号" in r.index:
            row["序号"] = r.get("序号")
        if "净流入占流通%" in r.index:
            row["净流入占流通%"] = r.get("净流入占流通%")
        rows.append(row)

    print("跳过统计:", n_skip)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description="净流入前100 · 日线开盘买/次日收盘卖")
    ap.add_argument("--before", default="2026-06-09", help="仅选股日 < 此日期")
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--amount", type=float, default=50000.0)
    ap.add_argument(
        "--out",
        type=Path,
        default=OUT_DIR / "各日选股收益汇总_基于日线_6月9日前.xlsx",
    )
    args = ap.parse_args()

    sel = build_selection(args.before, args.top, [ARCH, INFLOW_LIVE])
    if sel.empty:
        raise SystemExit(f"未找到选股日 < {args.before} 的净流入 CSV（请检查 history_data/存档）")

    # 日线覆盖过滤提示
    cache: dict = {}
    cal = _market_calendar(cache)
    cal_set = set(cal)
    cal_min = cal[0] if cal else None
    print(f"日线交易日历: {cal[0].date()} ~ {cal[-1].date()} 共 {len(cal)} 日")

    # 选股日必须落在日历内，否则「下一交易日」会跨到缓存起点（如 11 月选股误买 1/23）
    sel["选股日"] = pd.to_datetime(sel["选股日"]).dt.normalize()
    keep = []
    for d in sorted(sel["选股日"].unique()):
        if d not in cal_set:
            continue
        nt = _next_trade_day(cal, d)
        if nt is None:
            continue
        nt2 = _next_trade_day(cal, nt)
        if nt2 is None:
            continue
        keep.append(d)
    sel2 = sel[sel["选股日"].isin(keep)].copy()
    dropped_days = sel["选股日"].nunique() - len(keep)
    print(
        f"选股日可用 {len(keep)}/{sel['选股日'].nunique()} "
        f"(丢掉 {dropped_days} 天：不在日线日历内或无法覆盖买/卖日；"
        f"日历起点 {cal_min.date() if cal_min is not None else '?'} )"
    )
    print(f"候选行 {len(sel2)}")

    # 保存选股中间文件
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sel_path = OUT_DIR / f"选股_主力净流入占流通前{args.top}_日线回测_{args.before.replace('-', '')}前.xlsx"
    sel2_out = sel2.copy()
    sel2_out["选股日"] = sel2_out["选股日"].dt.strftime("%Y-%m-%d")
    sel2_out.to_excel(sel_path, index=False)
    print("选股文件:", sel_path)

    bt = backtest_daily(sel2, amount=args.amount)
    if bt.empty:
        raise SystemExit("回测无成交")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for c in ("代码", "股票代码"):
        if c in bt.columns:
            bt[c] = bt[c].map(_norm_code)
    bt.to_excel(args.out, index=False, sheet_name="汇总")
    print(
        f"完成: {len(bt)} 笔 | 笔均 {bt['收益率pct'].mean():.3f}% | "
        f"胜率 {(bt['收益率pct']>0).mean()*100:.1f}% → {args.out}"
    )
    print(
        "按月:",
        bt.assign(ym=pd.to_datetime(bt["选股日"]).dt.to_period("M"))
        .groupby("ym")["收益率pct"]
        .agg(["count", "mean"])
        .round(3)
        .to_string(),
    )


if __name__ == "__main__":
    main()
