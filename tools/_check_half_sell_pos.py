# -*- coding: utf-8 -*-
"""核对弹性卖半仓：OPEN50/LU10 是否按全可卖一半（互不预扣）。"""
from __future__ import annotations

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
        return s.zfill(6) if s.isdigit() else s


def half_lot(n: int) -> int:
    n = int(n or 0)
    if n < 100:
        return n if n > 0 else 0
    return (n // 2) // 100 * 100


def main() -> None:
    buy = pd.read_csv(BUY, encoding="utf-8-sig")
    sell = pd.read_csv(SELL, encoding="utf-8-sig")
    for df in (buy, sell):
        df["代码6"] = df["代码"].map(code6)
        df["数量"] = pd.to_numeric(df["数量"], errors="coerce").fillna(0).astype(int)
        df["交易后持仓"] = pd.to_numeric(df["交易后持仓"], errors="coerce")

    sell["leg"] = "OTHER"
    sell.loc[sell["规则名"].astype(str).str.contains("开盘涨幅弹性半仓", na=False), "leg"] = "OPEN"
    sell.loc[sell["规则名"].astype(str).str.contains("近10日涨停", na=False), "leg"] = "LU10"
    sell.loc[sell["规则名"].astype(str).str.contains("破MA20", na=False), "leg"] = "MA20"
    sell.loc[sell["规则名"].astype(str).str.contains("清剩余", na=False), "leg"] = "OPEN_REST"
    sell.loc[sell["规则名"].astype(str) == "涨停即清仓", "leg"] = "LU_CLR"

    print("卖出规则分布:")
    print(sell["规则名"].value_counts().to_string())

    sell = sell.sort_values(["代码6", "日期", "时间", "腿键"]).reset_index(drop=True)
    # 卖前持仓：优先 交易后+数量；若交易后缺失则跳过比例
    sell["卖前持仓"] = sell["交易后持仓"] + sell["数量"]

    half = sell[sell["leg"].isin(["OPEN", "LU10"])].copy()
    half = half[half["卖前持仓"].notna() & (half["卖前持仓"] > 0)]
    half["卖前持仓"] = half["卖前持仓"].astype(int)
    half["期望半仓"] = half["卖前持仓"].map(half_lot)
    half["比"] = half["数量"] / half["卖前持仓"]
    half["差"] = half["数量"] - half["期望半仓"]

    print("\n=== 半仓腿 vs 本笔卖前持仓 ===")
    print(f"n={len(half)}")
    print(
        f"  数量==期望半仓: {(half['差']==0).sum()}  "
        f"|差|<=100: {(half['差'].abs()<=100).sum()}  "
        f"数量==卖前(清剩余形态): {(half['数量']==half['卖前持仓']).sum()}"
    )
    print(
        f"  比例 中位={half['比'].median():.3f} 均值={half['比'].mean():.3f} "
        f"≈0.5(±0.05): {((half['比']-0.5).abs()<=0.05).sum()}  "
        f"≈1.0(±0.02): {((half['比']-1.0).abs()<=0.02).sum()}"
    )

    # 同日 OPEN+LU10
    rows = []
    for (code, day), g in sell.groupby(["代码6", "日期"]):
        g2 = g[g["leg"].isin(["OPEN", "LU10"])]
        if not set(g2["leg"]) >= {"OPEN", "LU10"}:
            continue
        g2 = g2.sort_values("时间")
        first = g2.iloc[0]
        second = g2.iloc[1]
        avail0 = int(first["卖前持仓"]) if pd.notna(first["卖前持仓"]) else None
        if not avail0:
            continue
        exp = half_lot(avail0)
        # 旧 bug：第二笔 ≈ half(avail0-exp)
        exp_bug2 = half_lot(avail0 - exp)
        rows.append(
            {
                "日期": day,
                "代码": code,
                "名称": first["股票名称"],
                "先": first["leg"],
                "后": second["leg"],
                "avail0": avail0,
                "先量": int(first["数量"]),
                "后量": int(second["数量"]),
                "合计": int(first["数量"]) + int(second["数量"]),
                "期望半": exp,
                "旧bug后量": exp_bug2,
                "先比": int(first["数量"]) / avail0,
                "后相对卖前": int(second["数量"]) / int(second["卖前持仓"])
                if pd.notna(second["卖前持仓"]) and second["卖前持仓"]
                else None,
            }
        )
    bd = pd.DataFrame(rows)
    print(f"\n=== 同日 OPEN+LU10: {len(bd)} 次 ===")
    if len(bd):
        # 修复后期望：先≈半，后≈清掉剩余(≈半)，合计≈avail0
        ok = (
            ((bd["先量"] - bd["期望半"]).abs() <= 100)
            & ((bd["合计"] - bd["avail0"]).abs() <= 100)
        )
        bug = (
            ((bd["先量"] - bd["期望半"]).abs() <= 100)
            & ((bd["后量"] - bd["旧bug后量"]).abs() <= 100)
            & (bd["合计"] < bd["avail0"] * 0.85)
        )
        print(f"  符合独立半仓(先半+后清剩余≈满仓): {int(ok.sum())}/{len(bd)}")
        print(f"  仍像旧bug(后≈1/4、合计<85%): {int(bug.sum())}/{len(bd)}")
        print(
            f"  合计/avail0 中位={(bd['合计']/bd['avail0']).median():.3f} "
            f"均值={(bd['合计']/bd['avail0']).mean():.3f}"
        )
        print("\n样例:")
        print(
            bd.head(10)[
                ["日期", "代码", "名称", "先", "avail0", "先量", "后量", "合计", "期望半", "旧bug后量"]
            ].to_string(index=False)
        )
        if bug.any():
            print("\n疑似旧bug:")
            print(
                bd.loc[bug].head(10)[
                    ["日期", "代码", "名称", "avail0", "先量", "后量", "合计", "期望半", "旧bug后量"]
                ].to_string(index=False)
            )
        if (~ok).any():
            print("\n未完全清/不完全半仓:")
            print(
                bd.loc[~ok].head(15)[
                    ["日期", "代码", "名称", "avail0", "先量", "后量", "合计", "期望半"]
                ].to_string(index=False)
            )

    # 买入腿 1/3
    buy["金额"] = pd.to_numeric(buy["金额"], errors="coerce")
    ratios = []
    for _, sub in buy.groupby(["选股日", "代码6"]):
        if len(sub) != 3:
            continue
        tot = sub["金额"].sum()
        if tot > 0:
            ratios.extend((sub["金额"] / tot).tolist())
    if ratios:
        s = pd.Series(ratios)
        print(
            f"\n买入三腿齐全: {len(ratios)//3} 组；单腿占比 中位={s.median():.3f} 均值={s.mean():.3f}"
        )

    summ = pd.read_excel(SUM)
    bv = pd.to_numeric(summ["买入数量合计"], errors="coerce").fillna(0)
    sv = pd.to_numeric(summ["卖出数量合计"], errors="coerce").fillna(0)
    rem = pd.to_numeric(summ["剩余持仓数量"], errors="coerce").fillna(0)
    print("\n=== 汇总表 ===")
    print(f"行数={len(summ)} 买=卖+剩不一致={( (bv-sv-rem).abs()>0.5).sum()}")
    print(f"全部清仓={(rem==0).all()}  CSV总买=总卖={buy['数量'].sum()} vs {sell['数量'].sum()}")
    print(
        f"买入金额合计={summ['买入金额合计'].sum():.0f} "
        f"卖出金额合计={summ['卖出金额合计'].sum():.0f} "
        f"净现金流={summ['净现金流_卖减买'].sum():.0f}"
    )


if __name__ == "__main__":
    main()
