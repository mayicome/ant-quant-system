# -*- coding: utf-8 -*-
"""选股日收盘价 vs MA5/MA10/MA20 统计。"""
from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd

from utils.daily_cache_reader import load_daily_from_cache

XLS = Path(
    r"d:\蚂蚁量化系统\history_data\马总选股逻辑\选股结果_马总选股逻辑-盘后_2026-07-01_2026-07-31排除除权.xls"
)


def _code6(v) -> str:
    s = str(v or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    digits = "".join(c for c in s if c.isdigit())
    return digits.zfill(6)[-6:] if digits else ""


def _as_date(v):
    if isinstance(v, date):
        return v
    if hasattr(v, "date"):
        try:
            return v.date()
        except Exception:
            pass
    s = str(v or "").strip()[:10]
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


def ma_at(df: pd.DataFrame, as_of: date, n: int):
    """含当日收盘的 SMA(n)。"""
    if df is None or df.empty:
        return None, None
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"]).dt.date
    d = d[d["date"] <= as_of].sort_values("date")
    if len(d) < n:
        return None, None
    closes = d["close"].astype(float).tail(n)
    return float(d["close"].iloc[-1]), float(closes.mean())


def main() -> None:
    raw = pd.read_excel(XLS, engine="xlrd")
    # 去重：同一选股日+代码只留一行
    raw["code"] = raw["股票代码"].map(_code6)
    raw["sel"] = raw["选股日"].map(_as_date)
    raw = raw.dropna(subset=["code", "sel"])
    raw = raw[raw["code"].str.len() == 6]
    before = len(raw)
    raw = raw.drop_duplicates(subset=["code", "sel"], keep="first")
    print(f"行数 {before} → 去重后 {len(raw)}（按选股日+代码）")

    rows = []
    miss = 0
    for _, r in raw.iterrows():
        c6 = r["code"]
        sel = r["sel"]
        df = load_daily_from_cache(c6, through_date=sel)
        close, ma5 = ma_at(df, sel, 5)
        _, ma10 = ma_at(df, sel, 10)
        _, ma20 = ma_at(df, sel, 20)
        if close is None or ma5 is None or ma10 is None or ma20 is None:
            miss += 1
            continue
        # 文件里自带 MA（对照）
        f_ma5 = pd.to_numeric(r.get("MA5"), errors="coerce")
        f_ma10 = pd.to_numeric(r.get("MA10"), errors="coerce")
        f_ma20 = pd.to_numeric(r.get("MA20"), errors="coerce")
        rows.append(
            {
                "code": c6,
                "name": r.get("股票名称"),
                "sel": sel,
                "满足条件": r.get("满足条件"),
                "close": close,
                "ma5": ma5,
                "ma10": ma10,
                "ma20": ma20,
                "file_ma5": None if pd.isna(f_ma5) else float(f_ma5),
                "file_ma10": None if pd.isna(f_ma10) else float(f_ma10),
                "file_ma20": None if pd.isna(f_ma20) else float(f_ma20),
                "gt_ma5": close > ma5,
                "gt_ma10": close > ma10,
                "gt_ma20": close > ma20,
                "ge_ma5": close >= ma5,
                "ge_ma10": close >= ma10,
                "ge_ma20": close >= ma20,
                "pct_ma5": (close / ma5 - 1) * 100,
                "pct_ma10": (close / ma10 - 1) * 100,
                "pct_ma20": (close / ma20 - 1) * 100,
            }
        )

    df = pd.DataFrame(rows)
    n = len(df)
    print(f"有效样本 {n}，缺日线 {miss}")

    def pct(x):
        return f"{100.0 * x / n:.1f}%" if n else "n/a"

    print("\n=== 收盘价相对均线（严格 >）===")
    for k, lab in [
        ("gt_ma5", "收盘 > MA5"),
        ("gt_ma10", "收盘 > MA10"),
        ("gt_ma20", "收盘 > MA20"),
    ]:
        print(f"  {lab}: {int(df[k].sum())} / {n} = {pct(df[k].sum())}")

    print("\n=== 收盘价相对均线（≥）===")
    for k, lab in [
        ("ge_ma5", "收盘 ≥ MA5"),
        ("ge_ma10", "收盘 ≥ MA10"),
        ("ge_ma20", "收盘 ≥ MA20"),
    ]:
        print(f"  {lab}: {int(df[k].sum())} / {n} = {pct(df[k].sum())}")

    # 组合关系
    def combo(row):
        bits = []
        bits.append("上MA5" if row["gt_ma5"] else "下MA5")
        bits.append("上MA10" if row["gt_ma10"] else "下MA10")
        bits.append("上MA20" if row["gt_ma20"] else "下MA20")
        return "+".join(bits)

    df["combo"] = df.apply(combo, axis=1)
    print("\n=== 三均线位置组合（严格 >）===")
    vc = df["combo"].value_counts()
    for k, v in vc.items():
        print(f"  {k}: {v} ({pct(v)})")

    # 站上 MA5 且 MA20（与选股条件字段对齐）
    above_5_20 = (df["gt_ma5"] & df["gt_ma20"]).sum()
    print(f"\n收盘>MA5 且 >MA20: {above_5_20} ({pct(above_5_20)})")
    if "条件_收盘站上MA5且MA20" in raw.columns:
        # 对齐到有效样本
        print(
            "文件字段 条件_收盘站上MA5且MA20=True:",
            int(raw["条件_收盘站上MA5且MA20"].fillna(False).astype(bool).sum()),
            "/",
            len(raw),
        )

    print("\n=== 相对均线偏离%（收盘/MA-1）===")
    for col, lab in [
        ("pct_ma5", "vs MA5"),
        ("pct_ma10", "vs MA10"),
        ("pct_ma20", "vs MA20"),
    ]:
        s = df[col]
        print(
            f"  {lab}: 中位数 {s.median():+.2f}%  均值 {s.mean():+.2f}%  "
            f"P25 {s.quantile(0.25):+.2f}%  P75 {s.quantile(0.75):+.2f}%"
        )

    # 满足条件子集
    if "满足条件" in df.columns:
        m = df["满足条件"].map(
            lambda x: str(x).strip() in ("True", "true", "1", "是", "YES", "yes")
            or x is True
        )
        sub = df[m]
        if len(sub):
            print(f"\n=== 仅「满足条件=是」({len(sub)}只) ===")
            for k, lab in [
                ("gt_ma5", ">MA5"),
                ("gt_ma10", ">MA10"),
                ("gt_ma20", ">MA20"),
            ]:
                print(f"  {lab}: {int(sub[k].sum())}/{len(sub)}={100*sub[k].mean():.1f}%")
            print("  组合:")
            for k, v in sub["combo"].value_counts().items():
                print(f"    {k}: {v} ({100*v/len(sub):.1f}%)")

    # 与文件 MA 一致性抽查
    both = df.dropna(subset=["file_ma5", "file_ma20"])
    if len(both):
        d5 = (both["ma5"] - both["file_ma5"]).abs()
        d20 = (both["ma20"] - both["file_ma20"]).abs()
        print(
            f"\n与文件MA对照: |ΔMA5|中位={d5.median():.4f}  |ΔMA20|中位={d20.median():.4f}  n={len(both)}"
        )


if __name__ == "__main__":
    main()
