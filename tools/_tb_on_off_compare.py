# -*- coding: utf-8 -*-
"""真突破开/关 与 事后 TB 诊断 对比。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def pnl_from_pair(buy_path: Path, sell_path: Path, month_prefix: str | None = None) -> pd.DataFrame:
    buy = pd.read_csv(buy_path, encoding="utf-8-sig")
    sell = pd.read_csv(sell_path, encoding="utf-8-sig")
    for df in (buy, sell):
        df["选股日"] = df["选股日"].astype(str).str[:10]
        df["代码"] = df["代码"].astype(str).str.replace(".0", "", regex=False).str.zfill(6)
    b = buy.groupby(["选股日", "代码"])["金额"].sum().rename("buy_amt")
    s = sell.groupby(["选股日", "代码"])["金额"].sum().rename("sell_amt")
    m = b.to_frame().join(s, how="inner")
    m["pnl_pct"] = (m["sell_amt"] - m["buy_amt"]) / m["buy_amt"] * 100
    names = buy.drop_duplicates(["选股日", "代码"]).set_index(["选股日", "代码"])["股票名称"]
    m = m.join(names, how="left")
    st = m["股票名称"].astype(str).str.contains("ST", case=False, na=False)
    is688 = m.index.get_level_values("代码").str.startswith("688")
    m = m[~st & ~is688]
    if month_prefix:
        m = m[m.index.get_level_values("选股日").str.startswith(month_prefix)]
    return m


def main() -> None:
    base5 = Path(r"d:\蚂蚁量化系统\history_data\回测五月")
    pairs = [
        ("3-5月 TB开", base5 / "回测成交明细_买入_判断真突破不开探测_3-5月.csv",
         base5 / "回测成交明细_卖出_判断真突破不开探测_3-5月.csv", None),
        ("3-5月 TB关", base5 / "回测成交明细_买入_不判断真突破不开探测_3-5月.csv",
         base5 / "回测成交明细_卖出_不判断真突破不开探测_3-5月.csv", None),
        ("5月 TB开", base5 / "回测成交明细_买入_判断真突破不开探测_3-5月.csv",
         base5 / "回测成交明细_卖出_判断真突破不开探测_3-5月.csv", "2026-05"),
        ("5月 TB关", base5 / "回测成交明细_买入_不判断真突破不开探测_3-5月.csv",
         base5 / "回测成交明细_卖出_不判断真突破不开探测_3-5月.csv", "2026-05"),
    ]
    print("## 同策略 TB 开 vs 关（买卖配对粗算收益率）")
    for name, bp, sp, mp in pairs:
        if not bp.exists():
            continue
        m = pnl_from_pair(bp, sp, mp)
        print(
            f"{name}: n={len(m)} 均={m['pnl_pct'].mean():+.2f}% "
            f"胜={(m['pnl_pct'] > 0).mean() * 100:.1f}%"
        )

    # TB关成交里，事后看有几项真突破通过
    off_buy = base5 / "回测成交明细_买入_不判断真突破不开探测_3-5月.csv"
    off_sell = base5 / "回测成交明细_卖出_不判断真突破不开探测_3-5月.csv"
    on_buy = base5 / "回测成交明细_买入_判断真突破不开探测_3-5月.csv"
    if off_buy.exists() and on_buy.exists():
        m_off = pnl_from_pair(off_buy, off_sell)
        m_on = pnl_from_pair(on_buy, base5 / "回测成交明细_卖出_判断真突破不开探测_3-5月.csv")
        only_off = m_off.index.difference(m_on.index)
        shared = m_off.index.intersection(m_on.index)
        print("\n## 3-5月 TB关 vs TB开 重叠")
        print(f"共有 {len(shared)} 笔，仅TB关 {len(only_off)} 笔，仅TB开 {len(m_on.index.difference(m_off.index))} 笔")
        if len(shared):
            print(
                f"共有笔 TB关均={m_off.loc[shared,'pnl_pct'].mean():+.2f}% "
                f"TB开均={m_on.loc[shared,'pnl_pct'].mean():+.2f}%"
            )
        if len(only_off):
            print(
                f"仅TB关多买: n={len(only_off)} 均={m_off.loc[only_off,'pnl_pct'].mean():+.2f}% "
                f"胜={(m_off.loc[only_off,'pnl_pct']>0).mean()*100:.1f}%"
            )

    p = Path(r"d:\蚂蚁量化系统\history_data\买入条件弱化分析\回测成交明细_3-5月_有tick的.csv")
    ps = Path(str(p).replace(".csv", "_卖出.csv"))
    if p.exists() and ps.exists():
        buy = pd.read_csv(p, encoding="utf-8-sig")
        m = pnl_from_pair(p, ps)
        buy["选股日"] = buy["选股日"].astype(str).str[:10]
        buy["代码"] = buy["代码"].astype(str).str.replace(".0", "", regex=False).str.zfill(6)
        for c in ["真突破①通过", "真突破②通过", "真突破③通过"]:
            buy[c] = buy[c].astype(str).str.strip().isin(["是", "True", "1"])
        buy["tb_any2"] = buy[["真突破①通过", "真突破②通过", "真突破③通过"]].sum(axis=1) >= 2
        tb = buy.set_index(["选股日", "代码"])[["tb_any2"]]
        mm = m.join(tb, how="inner")
        print("\n## 3-5月有tick样本：事后真突破≥2项 vs 收益")
        for v in (True, False):
            s = mm[mm["tb_any2"] == v]
            if len(s):
                print(
                    f"  tb_any2={v}: n={len(s)} 均={s['pnl_pct'].mean():+.2f}% "
                    f"胜={(s['pnl_pct'] > 0).mean() * 100:.1f}%"
                )


if __name__ == "__main__":
    main()
