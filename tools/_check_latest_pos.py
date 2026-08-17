# -*- coding: utf-8 -*-
"""检查最新 7-1 弹性-弹性：买卖仓位轧平与半仓结构。"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(r"d:\蚂蚁量化系统\history_data\马总选股逻辑")
BUY = ROOT / "回测成交明细_7-1-弹性买入.csv"
SELL = ROOT / "回测成交明细_7-1-弹性买入-弹性卖出.csv"
SUM = ROOT / "各日选股收益汇总_7-1-弹性-弹性.xlsx"


def code6(v) -> str:
    try:
        return f"{int(float(v)):06d}"
    except Exception:
        s = str(v or "").strip()
        d = "".join(c for c in s if c.isdigit())
        return d.zfill(6)[-6:] if d else ""


def half_lot(n: int) -> int:
    n = int(n or 0)
    if n < 100:
        return 0
    n = (n // 100) * 100
    return max(100, int(n * 0.5 / 100) * 100)


def main() -> None:
    buy = pd.read_csv(BUY, encoding="utf-8-sig")
    sell = pd.read_csv(SELL, encoding="utf-8-sig")
    summ = pd.read_excel(SUM, sheet_name="汇总")
    for df in (buy, sell):
        df["代码6"] = df["代码"].map(code6)
        df["数量"] = pd.to_numeric(df["数量"], errors="coerce").fillna(0).astype(int)
        df["金额"] = pd.to_numeric(df["金额"], errors="coerce").fillna(0.0)
        df["交易后持仓"] = pd.to_numeric(df["交易后持仓"], errors="coerce")

    print(f"买入CSV {len(buy)} 笔  卖出CSV {len(sell)} 笔  汇总 {len(summ)} 行")
    print(f"CSV总买量={buy['数量'].sum()} 总卖量={sell['数量'].sum()} 差={buy['数量'].sum()-sell['数量'].sum()}")

    # --- 汇总轧平 ---
    summ["代码6"] = summ["代码"].map(code6)
    bv = pd.to_numeric(summ["买入数量合计"], errors="coerce").fillna(0)
    sv = pd.to_numeric(summ["卖出数量合计"], errors="coerce").fillna(0)
    rem = pd.to_numeric(summ["剩余持仓数量"], errors="coerce").fillna(0)
    bad = (bv - sv - rem).abs() > 0.5
    print(f"\n[汇总] 买=卖+剩 不一致: {int(bad.sum())}")
    print(f"[汇总] 已清仓(剩=0): {int((rem==0).sum())}/{len(summ)}")
    print(f"[汇总] 有剩余: {int((rem>0).sum())}")
    if (rem > 0).any():
        print(
            summ.loc[
                rem > 0,
                ["选股日", "代码6", "股票名称", "买入数量合计", "卖出数量合计", "剩余持仓数量", "备注"],
            ]
            .head(20)
            .to_string(index=False)
        )

    # 汇总 vs CSV（按代码合计；汇总按选股日行，7-1 应为每股一行）
    buy_by = buy.groupby("代码6")["数量"].sum()
    sell_by = sell.groupby("代码6")["数量"].sum()
    sum_buy = summ.groupby("代码6")["买入数量合计"].sum()
    sum_sell = summ.groupby("代码6")["卖出数量合计"].sum()
    codes = sorted(set(buy_by.index) | set(sell_by.index) | set(sum_buy.index))
    mismatch = []
    for c in codes:
        b, s = float(buy_by.get(c, 0)), float(sell_by.get(c, 0))
        sb, ss = float(sum_buy.get(c, 0)), float(sum_sell.get(c, 0))
        if abs(b - s) > 0.5 or abs(b - sb) > 0.5 or abs(s - ss) > 0.5:
            mismatch.append((c, b, s, sb, ss, b - s))
    print(f"\n[CSV↔汇总] 代码级买/卖不一致: {len(mismatch)} / {len(codes)}")
    for t in mismatch[:15]:
        print(f"  {t[0]} csv买={t[1]:.0f} csv卖={t[2]:.0f} 汇总买={t[3]:.0f} 汇总卖={t[4]:.0f} 差={t[5]:.0f}")

    # --- 买入三腿 ---
    buy["金额"] = pd.to_numeric(buy["金额"], errors="coerce")
    ratios = []
    leg_cnt = buy.groupby(["选股日", "代码6"]).size()
    print("\n[买入] 每票腿数分布:")
    print(leg_cnt.value_counts().sort_index().to_string())
    for _, sub in buy.groupby(["选股日", "代码6"]):
        if len(sub) != 3:
            continue
        tot = sub["金额"].sum()
        if tot > 0:
            ratios.extend((sub["金额"] / tot).tolist())
    if ratios:
        s = pd.Series(ratios)
        print(f"三腿齐全金额占比 中位={s.median():.3f} 均值={s.mean():.3f} n组={len(ratios)//3}")

    ba = pd.to_numeric(summ["买入金额合计"], errors="coerce").dropna()
    print(
        f"[汇总] 买入金额 中位={ba.median():.0f} 均值={ba.mean():.0f} "
        f"P5={ba.quantile(0.05):.0f} P95={ba.quantile(0.95):.0f}"
    )

    # --- 卖出规则 / 半仓 ---
    print("\n[卖出] 规则分布:")
    print(sell["规则名"].value_counts().to_string())

    sell["leg"] = "OTHER"
    sell.loc[sell["规则名"].astype(str).str.contains("开盘涨幅", na=False), "leg"] = "OPEN"
    sell.loc[sell["规则名"].astype(str).str.contains("近10日涨停", na=False), "leg"] = "LU10"
    sell.loc[sell["规则名"].astype(str).str.contains("破MA20", na=False), "leg"] = "MA20"
    sell.loc[sell["规则名"].astype(str).str.contains("无条件清仓", na=False), "leg"] = "DAYN"
    sell.loc[sell["规则名"].astype(str).str.contains("涨停即清仓", na=False), "leg"] = "LU_CLR"
    sell.loc[sell["规则名"].astype(str).str.contains("清剩余", na=False), "leg"] = "OPEN_REST"

    # LU10 每票次数（整段应≤1；清仓后再买可再触发）
    lu = sell[sell["leg"] == "LU10"]
    lu_cnt = lu.groupby("代码6").size()
    print(f"\n[LU10] 有成交股票数={len(lu_cnt)}  次数>1: {int((lu_cnt>1).sum())}")
    if (lu_cnt > 1).any():
        print(lu_cnt[lu_cnt > 1].sort_values(ascending=False).head(15).to_string())

    # OPEN 每票次数（每日可多次跨日）
    op = sell[sell["leg"] == "OPEN"]
    op_cnt = op.groupby("代码6").size()
    print(f"[OPEN] 有成交股票数={len(op_cnt)}  次数分布:\n{op_cnt.value_counts().sort_index().to_string()}")

    # 同日 OPEN+LU10：应为互补凑满当时卖前仓（用 max 卖前）
    sell = sell.sort_values(["代码6", "日期", "时间"]).reset_index(drop=True)
    sell["卖前"] = sell["交易后持仓"] + sell["数量"]
    both = []
    for (c, d), g in sell.groupby(["代码6", "日期"]):
        g2 = g[g["leg"].isin(["OPEN", "LU10"])]
        if not ({"OPEN", "LU10"} <= set(g2["leg"])):
            continue
        avail0 = int(pd.to_numeric(g2["卖前"], errors="coerce").max())
        q = int(g2["数量"].sum())
        h = half_lot(avail0)
        both.append(
            {
                "代码": c,
                "日期": d,
                "avail0": avail0,
                "OPEN": int(g2.loc[g2["leg"] == "OPEN", "数量"].sum()),
                "LU10": int(g2.loc[g2["leg"] == "LU10", "数量"].sum()),
                "合计": q,
                "期望半": h,
                "期望满": avail0,
                "满仓OK": abs(q - avail0) <= 100,
            }
        )
    bd = pd.DataFrame(both)
    print(f"\n[同日 OPEN+LU10] {len(bd)} 次；合计≈满仓: {int(bd['满仓OK'].sum()) if len(bd) else 0}/{len(bd)}")
    if len(bd):
        print(f"  合计/avail0 中位={(bd['合计']/bd['avail0']).median():.3f}")
        bad_b = bd[~bd["满仓OK"]]
        if len(bad_b):
            print("  未满仓样例:")
            print(bad_b.head(12).to_string(index=False))
        print("  样例:")
        print(bd.head(8).to_string(index=False))

    # 半仓腿相对卖前比例
    half = sell[sell["leg"].isin(["OPEN", "LU10"])].copy()
    half = half[half["卖前"].notna() & (half["卖前"] > 0)]
    half["比"] = half["数量"] / half["卖前"]
    print(
        f"\n[半仓腿] n={len(half)} 比例中位={half['比'].median():.3f} "
        f"≈0.5: {((half['比']-0.5).abs()<=0.08).sum()}  "
        f"≈1.0(清剩余形态): {((half['比']-1).abs()<=0.02).sum()}"
    )

    # 买入金额/笔数
    print("\n[汇总] 买入笔数:\n" + pd.to_numeric(summ["买入笔数"], errors="coerce").value_counts().sort_index().to_string())
    print("[汇总] 卖出笔数:\n" + pd.to_numeric(summ["卖出笔数"], errors="coerce").value_counts().sort_index().to_string())

    print("\n======== 仓位结论 ========")
    print(
        f"数量轧平: CSV买=卖={buy['数量'].sum()==sell['数量'].sum()}；"
        f"汇总买=卖+剩不一致 {int(bad.sum())}；剩余持仓行 {(rem>0).sum()}"
    )
    print(f"LU10 多次触发股票: {int((lu_cnt>1).sum()) if len(lu_cnt) else 0}")
    print(
        f"同日双半仓凑满: {int(bd['满仓OK'].sum()) if len(bd) else 0}/{len(bd)}"
    )


if __name__ == "__main__":
    main()
