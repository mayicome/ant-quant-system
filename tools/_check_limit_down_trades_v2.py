# -*- coding: utf-8 -*-
"""仔细检查最新 7-1 弹性买/卖成交与汇总：是否有一字跌停日买卖。"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from utils.daily_cache_reader import load_daily_from_cache

ROOT = Path(r"d:\蚂蚁量化系统\history_data\马总选股逻辑")
BUY = ROOT / "回测成交明细_7-1-弹性买入.csv"
SELL = ROOT / "回测成交明细_7-1-弹性买入-弹性卖出.csv"
SUM = ROOT / "各日选股收益汇总_7-1-弹性-弹性.xlsx"


def code6(v) -> str:
    try:
        return f"{int(float(v)):06d}"
    except Exception:
        s = str(v or "").strip()
        digits = "".join(c for c in s if c.isdigit())
        return digits.zfill(6)[-6:] if digits else ""


def as_date(v):
    if isinstance(v, date):
        return v
    if hasattr(v, "date"):
        try:
            return v.date()
        except Exception:
            pass
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


def daily_info(c6: str, d: date, name: str):
    df = load_daily_from_cache(c6, through_date=d)
    if df is None or df.empty:
        return None
    x = df.copy()
    x["date"] = pd.to_datetime(x["date"]).dt.date
    prev = x[x["date"] < d].sort_values("date")
    row = x[x["date"] == d]
    if prev.empty or row.empty:
        return None
    pc = float(prev.iloc[-1]["close"])
    r = row.iloc[-1]
    o, h, low, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
    lr = limit_ratio(c6, name, d)
    ld = round(pc * (1.0 - lr), 2)
    lu = round(pc * (1.0 + lr), 2)
    eps = 0.021  # 2分+浮点
    yizi_down = max(abs(o - ld), abs(h - ld), abs(low - ld), abs(c - ld)) <= eps
    # 跌停价附近开盘且全日未高于跌停+1分（近似一字/准一字）
    near_yizi = abs(o - ld) <= eps and h <= ld + 0.02
    touched_ld = abs(low - ld) <= eps or abs(c - ld) <= eps
    return {
        "prev_close": pc,
        "open": o,
        "high": h,
        "low": low,
        "close": c,
        "limit_down": ld,
        "limit_up": lu,
        "yizi_down": yizi_down,
        "near_yizi_down": near_yizi,
        "touched_ld": touched_ld,
    }


def scan_trades(path: Path, label: str):
    df = pd.read_csv(path, encoding="utf-8-sig")
    print(f"\n=== {label}  rows={len(df)} ===")
    yizi_hits = []
    near_hits = []
    at_ld_hits = []
    miss_daily = 0
    for _, r in df.iterrows():
        c6 = code6(r.get("代码"))
        d = as_date(r.get("日期"))
        name = str(r.get("股票名称") or "")
        if not c6 or not d:
            continue
        info = daily_info(c6, d, name)
        if info is None:
            miss_daily += 1
            continue
        px = float(r.get("价格") or 0)
        at_ld = abs(px - info["limit_down"]) <= 0.021
        base = {
            "日期": d.isoformat(),
            "时间": r.get("时间"),
            "代码": c6,
            "名称": name,
            "方向": r.get("方向"),
            "成交价": px,
            "跌停价": info["limit_down"],
            "开": info["open"],
            "高": info["high"],
            "低": info["low"],
            "收": info["close"],
            "数量": int(r.get("数量") or 0),
            "规则": r.get("规则名"),
        }
        if info["yizi_down"]:
            yizi_hits.append(base)
        elif info["near_yizi_down"]:
            near_hits.append({**base, "备注": "开≈跌停且高未离开"})
        if at_ld and not info["yizi_down"]:
            at_ld_hits.append({**base, "备注": "成交贴跌停但非一字"})

    print(f"缺日线: {miss_daily}")
    print(f"一字跌停日成交: {len(yizi_hits)}")
    print(f"准一字(开贴跌停且高未离开)成交: {len(near_hits)}")
    print(f"成交价贴跌停(非一字日): {len(at_ld_hits)}")
    if yizi_hits:
        print(pd.DataFrame(yizi_hits).to_string(index=False))
    if near_hits:
        print("--- 准一字 ---")
        print(pd.DataFrame(near_hits).to_string(index=False))
    if at_ld_hits:
        print("--- 贴跌停非一字 ---")
        print(pd.DataFrame(at_ld_hits).to_string(index=False))
    return yizi_hits, near_hits, at_ld_hits


def scan_summary(path: Path):
    df = pd.read_excel(path, sheet_name="汇总")
    print(f"\n=== 汇总xlsx  rows={len(df)} ===")
    # 用买入日/选股日？检查有成交的股票在买入日、以及若有卖出相关日期
    # 汇总未必有每笔卖出日；用买入日 + 代码核对买入日是否一字跌停
    yizi_buy = []
    for _, r in df.iterrows():
        c6 = code6(r.get("代码") if pd.notna(r.get("代码")) else r.get("股票代码"))
        buy_d = as_date(r.get("买入日"))
        name = str(r.get("股票名称") or "")
        if not c6 or not buy_d:
            continue
        buy_n = int(pd.to_numeric(r.get("买入笔数"), errors="coerce") or 0)
        if buy_n <= 0:
            continue
        info = daily_info(c6, buy_d, name)
        if info and info["yizi_down"]:
            yizi_buy.append(
                {
                    "选股日": str(r.get("选股日"))[:10],
                    "买入日": buy_d.isoformat(),
                    "代码": c6,
                    "名称": name,
                    "买入笔数": buy_n,
                    "开": info["open"],
                    "高": info["high"],
                    "低": info["low"],
                    "收": info["close"],
                    "跌停价": info["limit_down"],
                }
            )
    print(f"汇总「买入日」为一字跌停的行: {len(yizi_buy)}")
    if yizi_buy:
        print(pd.DataFrame(yizi_buy).to_string(index=False))

    # 交叉：汇总代码+买入日 是否在 buy csv 一字跌停集合（已在 trades 查）
    return yizi_buy


def main() -> None:
    for p in (BUY, SELL, SUM):
        print(f"{p.name}: exists={p.exists()} size={p.stat().st_size if p.exists() else 0}")

    y1, n1, a1 = scan_trades(BUY, "弹性买入CSV")
    y2, n2, a2 = scan_trades(SELL, "弹性卖出CSV")
    yb = scan_summary(SUM)

    print("\n======== 结论 ========")
    print(
        f"一字跌停日买卖: 买入CSV={len(y1)} 卖出CSV={len(y2)} 汇总买入日={len(yb)}"
    )
    print(
        f"准一字成交: 买={len(n1)} 卖={len(n2)}；贴跌停非一字: 买={len(a1)} 卖={len(a2)}"
    )


if __name__ == "__main__":
    main()
