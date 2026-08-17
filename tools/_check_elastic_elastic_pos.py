# -*- coding: utf-8 -*-
"""核对 7-1 弹性-弹性 汇总仓位是否轧平、半仓卖法是否合理。"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(r"d:\蚂蚁量化系统\history_data\马总选股逻辑")
SUM = ROOT / "各日选股收益汇总_7-1-弹性-弹性.xlsx"
BUY_CSV = ROOT / "回测成交明细_7-1-弹性买入.csv"
SELL_CSV = ROOT / "回测成交明细_7-1-弹性买入-弹性卖出.csv"


def code6(v) -> str:
    s = str(v or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    d = "".join(c for c in s if c.isdigit())
    return d.zfill(6)[-6:] if d else ""


def main() -> None:
    df = pd.read_excel(SUM, sheet_name="汇总")
    print(f"汇总行数: {len(df)}")

    # --- 数量轧平 ---
    buy_v = pd.to_numeric(df["买入数量合计"], errors="coerce").fillna(0)
    sell_v = pd.to_numeric(df["卖出数量合计"], errors="coerce").fillna(0)
    rem = pd.to_numeric(df["剩余持仓数量"], errors="coerce").fillna(0)
    buy_n = pd.to_numeric(df["买入笔数"], errors="coerce").fillna(0)
    sell_n = pd.to_numeric(df["卖出笔数"], errors="coerce").fillna(0)
    buy_amt = pd.to_numeric(df["买入金额合计"], errors="coerce").fillna(0)

    mismatch = (buy_v - sell_v - rem).abs() > 0.5
    print(f"\n买-卖-剩 不一致: {int(mismatch.sum())}")
    if mismatch.any():
        bad = df.loc[mismatch, ["选股日", "代码", "股票名称", "买入数量合计", "卖出数量合计", "剩余持仓数量", "备注"]].head(20)
        print(bad.to_string(index=False))

    cleared = rem == 0
    with_buy = buy_v > 0
    print(f"有买入: {int(with_buy.sum())}")
    print(f"已清仓(剩=0): {int(cleared.sum())}")
    print(f"未清仓(剩>0): {int((~cleared & with_buy).sum())}")
    if (~cleared & with_buy).any():
        left = df.loc[~cleared & with_buy, ["选股日", "代码", "股票名称", "买入数量合计", "卖出数量合计", "剩余持仓数量", "备注"]].head(15)
        print(left.to_string(index=False))

    # 买卖数量完全相等（已清仓）
    eq = cleared & (buy_v == sell_v) & with_buy
    print(f"已清仓且买量=卖量: {int(eq.sum())} / {int((cleared & with_buy).sum())}")

    # 买入金额分布（仓位金额）
    print("\n=== 买入金额合计 分布（有买入）===")
    ba = buy_amt[with_buy]
    print(
        f"  n={len(ba)} 中位={ba.median():.0f} 均值={ba.mean():.0f} "
        f"P5={ba.quantile(0.05):.0f} P95={ba.quantile(0.95):.0f} "
        f"min={ba.min():.0f} max={ba.max():.0f}"
    )
    # 典型目标约 5万？看偏离
    near50k = ((ba >= 45000) & (ba <= 55000)).sum()
    print(f"  金额在 4.5~5.5万: {near50k} ({100*near50k/len(ba):.1f}%)")

    # 买入笔数
    print("\n=== 买入笔数分布 ===")
    print(buy_n[with_buy].value_counts().sort_index().to_string())
    print("卖出笔数分布:")
    print(sell_n[with_buy].value_counts().sort_index().to_string())

    # --- 成交明细核对 ---
    if not BUY_CSV.exists() or not SELL_CSV.exists():
        print("\n缺成交 CSV，跳过明细核对")
        print("BUY", BUY_CSV.exists(), BUY_CSV)
        print("SELL", SELL_CSV.exists(), SELL_CSV)
        return

    buy = pd.read_csv(BUY_CSV, encoding="utf-8-sig")
    sell = pd.read_csv(SELL_CSV, encoding="utf-8-sig")
    print(f"\n买入成交 CSV: {buy.shape} cols={list(buy.columns)[:12]}...")
    print(f"卖出成交 CSV: {sell.shape} cols={list(sell.columns)[:12]}...")

    # normalize
    for d in (buy, sell):
        if "代码" in d.columns:
            d["code"] = d["代码"].map(code6)
        elif "股票代码" in d.columns:
            d["code"] = d["股票代码"].map(code6)
        else:
            # try first col that looks like code
            for c in d.columns:
                if "代码" in str(c) or "code" in str(c).lower():
                    d["code"] = d[c].map(code6)
                    break

    vol_col_b = next((c for c in buy.columns if "数量" in str(c) or c.lower() in ("volume", "qty", "vol")), None)
    vol_col_s = next((c for c in sell.columns if "数量" in str(c) or c.lower() in ("volume", "qty", "vol")), None)
    side_b = next((c for c in buy.columns if "方向" in str(c) or "side" in str(c).lower() or "买卖" in str(c)), None)
    side_s = next((c for c in sell.columns if "方向" in str(c) or "side" in str(c).lower() or "买卖" in str(c)), None)
    print(f"buy vol_col={vol_col_b} side={side_b}; sell vol_col={vol_col_s} side={side_s}")

    # show sample rows
    print("\nbuy head:")
    print(buy.head(3).to_string())
    print("\nsell head:")
    print(sell.head(3).to_string())

    bv = buy.groupby("code")[vol_col_b].sum() if vol_col_b else None
    sv = sell.groupby("code")[vol_col_s].sum() if vol_col_s else None
    if bv is not None and sv is not None:
        codes = sorted(set(bv.index) | set(sv.index))
        diffs = []
        for c in codes:
            b = float(bv.get(c, 0) or 0)
            s = float(sv.get(c, 0) or 0)
            if abs(b - s) > 0.5:
                diffs.append((c, b, s, b - s))
        print(f"\nCSV 按代码买量-卖量 不一致: {len(diffs)} / {len(codes)}")
        print(f"CSV 总买量={bv.sum():.0f} 总卖量={sv.sum():.0f}")
        for t in diffs[:15]:
            print(f"  {t[0]} buy={t[1]:.0f} sell={t[2]:.0f} diff={t[3]:.0f}")

    # 半仓：同日同股多笔卖，检查是否接近 avail/2 各一笔
    # 需要日期、触发、数量列
    date_col = next((c for c in sell.columns if c in ("日期", "成交日", "交易日", "date") or "日期" in str(c)), None)
    reason_col = next((c for c in sell.columns if "触发" in str(c) or "原因" in str(c) or "腿" in str(c) or "规则" in str(c)), None)
    print(f"\nsell date_col={date_col} reason_col={reason_col}")

    # 汇总 vs CSV：按选股日+代码？汇总是按选股结果行，代码可能未补零
    df["code"] = df["代码"].map(code6)
    sum_buy = df.groupby("code")["买入数量合计"].sum()
    # 注意：同一股票可能多选股日出现多次，汇总是按选股日行独立回测，不能简单按代码合计对比全局 CSV
    # 改为：汇总表内每行买=卖+剩
    print("\n=== 结论摘要 ===")
    print(f"汇总行内 买=卖+剩 不一致: {int(mismatch.sum())}")
    print(f"有买入且已清仓且买=卖: {int(eq.sum())}/{int((cleared & with_buy).sum())}")
    rem_pos = df.loc[~cleared & with_buy]
    print(f"有剩余持仓行数: {len(rem_pos)}")


if __name__ == "__main__":
    main()
