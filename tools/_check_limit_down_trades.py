# -*- coding: utf-8 -*-
"""检查回测成交是否落在一字跌停/跌停价。"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from utils.daily_cache_reader import load_daily_from_cache

ROOT = Path(r"d:\蚂蚁量化系统\history_data\马总选股逻辑")
FILES = [
    ROOT / "回测成交明细_7-1-弹性买入.csv",
    ROOT / "回测成交明细_7-1-弹性买入-弹性卖出.csv",
]


def code6(v) -> str:
    try:
        return f"{int(float(v)):06d}"
    except Exception:
        s = str(v or "").strip()
        return s.zfill(6) if s.isdigit() else s


def as_date(v):
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def limit_ratio(c6: str, name: str, as_d: date) -> float:
    name_u = (name or "").upper()
    if c6.startswith(("300", "301", "688", "689")):
        return 0.20
    if c6.startswith(("8", "4", "920")):
        return 0.30
    if "ST" in name_u:
        return 0.10 if as_d >= date(2026, 7, 6) else 0.05
    return 0.10


def day_row(df: pd.DataFrame, d: date):
    if df is None or df.empty:
        return None
    x = df.copy()
    x["date"] = pd.to_datetime(x["date"]).dt.date
    x = x[x["date"] == d]
    if x.empty:
        return None
    return x.iloc[-1]


def prev_close(df: pd.DataFrame, d: date):
    if df is None or df.empty:
        return None
    x = df.copy()
    x["date"] = pd.to_datetime(x["date"]).dt.date
    x = x[x["date"] < d].sort_values("date")
    if x.empty:
        return None
    return float(x.iloc[-1]["close"])


def main() -> None:
    hits = []
    for path in FILES:
        if not path.exists():
            print("missing", path)
            continue
        df = pd.read_csv(path, encoding="utf-8-sig")
        df["代码6"] = df["代码"].map(code6)
        df["日期d"] = df["日期"].map(as_date)
        print(f"\n=== {path.name}  n={len(df)} ===")
        for _, r in df.iterrows():
            c6 = r["代码6"]
            d = r["日期d"]
            if not c6 or not d:
                continue
            px = float(r["价格"] or 0)
            if px <= 0:
                continue
            daily = load_daily_from_cache(c6, through_date=d)
            row = day_row(daily, d)
            pc = prev_close(daily, d)
            if row is None or pc is None or pc <= 0:
                continue
            name = str(r.get("股票名称") or "")
            lr = limit_ratio(c6, name, d)
            ld = round(pc * (1.0 - lr), 2)
            o = float(row.get("open") or 0)
            h = float(row.get("high") or 0)
            low = float(row.get("low") or 0)
            c = float(row.get("close") or 0)
            # 一字跌停：开=高=低=收=跌停价（允许 1 分误差）
            yizi = (
                abs(o - ld) <= 0.02
                and abs(h - ld) <= 0.02
                and abs(low - ld) <= 0.02
                and abs(c - ld) <= 0.02
            )
            # 成交价贴跌停（与跌停价相差 ≤2 分）
            at_ld = abs(px - ld) <= 0.02
            # 当日曾封/触及跌停（最低价贴跌停），但非一字
            touched_ld = abs(low - ld) <= 0.02
            if yizi or at_ld:
                hits.append(
                    {
                        "文件": path.name,
                        "日期": d.isoformat(),
                        "时间": r.get("时间"),
                        "代码": c6,
                        "名称": name,
                        "方向": r.get("方向"),
                        "成交价": px,
                        "跌停价": ld,
                        "开": o,
                        "高": h,
                        "低": low,
                        "收": c,
                        "一字跌停": yizi,
                        "贴跌停成交": at_ld,
                        "规则": r.get("规则名"),
                        "数量": int(r.get("数量") or 0),
                    }
                )

    if not hits:
        print("\n未发现一字跌停日成交，也未发现成交价贴跌停的记录。")
        return

    out = pd.DataFrame(hits)
    yizi_n = int(out["一字跌停"].sum())
    at_n = int(out["贴跌停成交"].sum())
    print(f"\n命中行数: {len(out)}  其中一字跌停日: {yizi_n}  贴跌停价成交: {at_n}")
    # 优先展示一字跌停
    show = out.sort_values(["一字跌停", "日期"], ascending=[False, True])
    cols = [
        "文件",
        "日期",
        "时间",
        "代码",
        "名称",
        "方向",
        "成交价",
        "跌停价",
        "一字跌停",
        "开",
        "高",
        "低",
        "收",
        "规则",
        "数量",
    ]
    print(show[cols].to_string(index=False))


if __name__ == "__main__":
    main()
