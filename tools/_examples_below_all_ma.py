# -*- coding: utf-8 -*-
from datetime import date
from pathlib import Path

import pandas as pd

from utils.daily_cache_reader import load_daily_from_cache

XLS = Path(
    r"d:\蚂蚁量化系统\history_data\马总选股逻辑\选股结果_马总选股逻辑-盘后_2026-07-01_2026-07-31排除除权.xls"
)


def code6(v) -> str:
    s = str(v or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    d = "".join(c for c in s if c.isdigit())
    return d.zfill(6)[-6:] if d else ""


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


def ma_at(df, as_of, n):
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"]).dt.date
    d = d[d["date"] <= as_of].sort_values("date")
    if len(d) < n:
        return None, None
    return float(d["close"].iloc[-1]), float(d["close"].astype(float).tail(n).mean())


def main() -> None:
    raw = pd.read_excel(XLS, engine="xlrd")
    ex = []
    for _, r in raw.iterrows():
        c6 = code6(r["股票代码"])
        sel = as_date(r["选股日"])
        if not c6 or not sel:
            continue
        df = load_daily_from_cache(c6, through_date=sel)
        if df is None or df.empty:
            continue
        close, ma5 = ma_at(df, sel, 5)
        _, ma10 = ma_at(df, sel, 10)
        _, ma20 = ma_at(df, sel, 20)
        if close is None:
            continue
        if close < ma5 and close < ma10 and close < ma20:
            ex.append(
                (
                    sel.isoformat(),
                    c6,
                    str(r.get("股票名称", "")),
                    close,
                    ma5,
                    ma10,
                    ma20,
                    r.get("满足条件"),
                )
            )
            if len(ex) >= 8:
                break
    for t in ex:
        print(
            f"{t[0]}  {t[1]} {t[2]:8s}  收盘={t[3]:.2f}  "
            f"MA5={t[4]:.2f}  MA10={t[5]:.2f}  MA20={t[6]:.2f}  满足={t[7]}"
        )


if __name__ == "__main__":
    main()
