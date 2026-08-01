# -*- coding: utf-8 -*-
"""解读 6月 不检测真突破 + 不看其他K线 回测，并与旧版6月对比。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _compare_jun_jul_tri import (  # noqa: E402
    CFG1,
    CFG2,
    KEYS,
    LIVE,
    SHORT,
    load_trades,
    scan,
    stats_cfg,
)

DIR = Path(r"d:\蚂蚁量化系统\history_data\买入条件弱化分析")
PATH_NEW = DIR / "各日选股收益汇总_6月_不检测真突破_不看其他k线.xlsx"
PATH_OLD = DIR / "各日选股收益汇总_6月_不检测真突破.xlsx"


def compare_overlap(old, new, ret_col: str) -> None:
    key_cols = ["选股日", "代码"]
    oldk = old[key_cols + [ret_col]].copy()
    newk = new[key_cols + [ret_col]].copy()
    oldk["选股日"] = oldk["选股日"].astype(str).str[:10]
    newk["选股日"] = newk["选股日"].astype(str).str[:10]
    oldk["代码"] = oldk["代码"].astype(str).str.replace(".0", "", regex=False).str.zfill(6)
    newk["代码"] = newk["代码"].astype(str).str.replace(".0", "", regex=False).str.zfill(6)
    merged = oldk.merge(newk, on=key_cols, how="outer", suffixes=("_old", "_new"), indicator=True)
    both = merged[merged["_merge"] == "both"]
    add = merged[merged["_merge"] == "right_only"]
    drop = merged[merged["_merge"] == "left_only"]
    print("\n## 与旧版成交对比（选股日+代码）")
    print(f"共有 {len(both)} 笔，仅新版 {len(add)} 笔，仅旧版 {len(drop)} 笔")
    if len(both):
        print(
            f"共有笔：新版均={both['收益率pct_new'].mean():+.2f}% "
            f"旧版均={both['收益率pct_old'].mean():+.2f}%"
        )
    if len(add):
        print(
            f"新增笔：均={add['收益率pct_new'].mean():+.2f}% "
            f"胜={(add['收益率pct_new'] > 0).mean() * 100:.1f}% n={len(add)}"
        )
    if len(drop):
        print(f"消失笔：均={drop['收益率pct_old'].mean():+.2f}% n={len(drop)}")


def main() -> None:
    print("## 样本总览")
    print("旧版6月（仍看其他K线约束）:")
    old, ret_o, dc_o = load_trades(PATH_OLD)
    print("新版6月（不看其他K线）:")
    new, ret_n, dc_n = load_trades(PATH_NEW)
    print(f"  增量：{len(new) - len(old)} 笔（+{(len(new) - len(old)) / len(old) * 100:.0f}%）")

    print("\n## 固定组合对照")
    print(f"{'组合':<8} {'旧n':>4} {'旧均%':>8} {'旧胜%':>6} {'旧撤':>7} | {'新n':>4} {'新均%':>8} {'新胜%':>6} {'新撤':>7}")
    for name, sw in [("实盘用", LIVE), ("6月#1", CFG1), ("6月#2", CFG2)]:
        so = stats_cfg(old, ret_o, dc_o, sw)
        sn = stats_cfg(new, ret_n, dc_n, sw)
        print(
            f"{name:<8} {so['n']:4d} {so['mean']:+8.2f} {so['win']:6.1f} {so['max_dd']:+7.1f} | "
            f"{sn['n']:4d} {sn['mean']:+8.2f} {sn['win']:6.1f} {sn['max_dd']:+7.1f}"
        )

    g_new = scan(new, ret_n, dc_n)
    stable = g_new[(g_new["n"] >= 20) & (g_new["days"] >= 10)].sort_values(
        ["mean", "n"], ascending=[False, False]
    )
    print("\n## 新版6月稳健 Top12（n≥20 日≥10）")
    for _, r in stable.head(12).iterrows():
        print(
            f"n={int(r['n']):3d} 日={int(r['days']):2d} "
            f"均={r['mean']:+6.2f}% 胜={r['win']:5.1f}% 回撤={r['max_dd']:+6.1f}pp | {r['label']}"
        )

    if not stable.empty:
        b = stable.iloc[0]
        sl = stats_cfg(new, ret_n, dc_n, LIVE)
        print("\n## 新版最优 vs 实盘")
        print(
            f"实盘：n={sl['n']} 均={sl['mean']:+.2f}% 胜={sl['win']:.1f}% 回撤={sl['max_dd']:+.1f}pp"
        )
        print(
            f"最优：n={int(b['n'])} 均={b['mean']:+.2f}% 胜={b['win']:.1f}% "
            f"回撤={b['max_dd']:+.1f}pp | {b['label']}"
        )

    compare_overlap(old, new, ret_n)

    print("\n## 新版全样本：诊断列 True 占比 & 单因子均收益")
    for c in KEYS:
        pct = (new[c] == "True").mean() * 100
        t_mean = new.loc[new[c] == "True", ret_n].mean()
        f_mean = new.loc[new[c] == "False", ret_n].mean()
        print(
            f"  {SHORT[c]:5s} True占{pct:5.1f}% | True均={t_mean:+.2f}% "
            f"False均={f_mean:+.2f}%"
        )

    # 旧版稳健 #1 在新样本上的表现
    s1_old = stats_cfg(old, ret_o, dc_o, CFG1)
    s1_new = stats_cfg(new, ret_n, dc_n, CFG1)
    print("\n## 旧版冠军组合 CFG1 在新样本上")
    print(
        f"旧样本：n={s1_old['n']} 均={s1_old['mean']:+.2f}% 胜={s1_old['win']:.1f}% "
        f"回撤={s1_old['max_dd']:+.1f}pp"
    )
    print(
        f"新样本：n={s1_new['n']} 均={s1_new['mean']:+.2f}% 胜={s1_new['win']:.1f}% "
        f"回撤={s1_new['max_dd']:+.1f}pp"
    )

    # 新增成交画像
    for df in (old, new):
        df["选股日"] = df["选股日"].astype(str).str[:10]
        df["代码"] = df["代码"].astype(str).str.replace(".0", "", regex=False).str.zfill(6)
    oldk = set(zip(old["选股日"], old["代码"]))
    new["is_new"] = ~new.apply(lambda r: (r["选股日"], r["代码"]) in oldk, axis=1)
    add = new[new["is_new"]]
    base = new[~new["is_new"]]
    print("\n## 新增 105 笔画像（相对旧版 210 笔）")
    print(f"{'因子':<6} {'新增True%':>10} {'基线True%':>10}")
    for c in KEYS:
        print(
            f"{SHORT[c]:<6} {(add[c]=='True').mean()*100:9.1f}% "
            f"{(base[c]=='True').mean()*100:9.1f}%"
        )
    for col, name in [
        ("REQUIRE_OLD_HIGH", "OLD"),
        ("REQUIRE_LOWER_SHADOW", "SHAD"),
        ("REQUIRE_BOLL_BREAK", "BOLL"),
        ("REQUIRE_MA_SUPPORT_AFTER", "MA"),
    ]:
        print(f"\n新增笔按 {name}:")
        for flag in ("True", "False"):
            sub = add[add[col] == flag]
            if len(sub):
                print(f"  {name}={flag}: n={len(sub)} 均={sub[ret_n].mean():+.2f}%")

    m = pd.Series(True, index=add.index)
    for c, v in [
        ("REQUIRE_PRIOR_LU_IN_L", "None"),
        ("REQUIRE_OLD_HIGH", "False"),
        ("REQUIRE_OBVIOUS_NEW_HIGH", "False"),
        ("REQUIRE_LOWER_SHADOW", "True"),
        ("REQUIRE_BOLL_BREAK", "False"),
        ("REQUIRE_MA_SUPPORT_AFTER", "True"),
    ]:
        if v == "True":
            m &= add[c] == "True"
        elif v == "False":
            m &= add[c] == "False"
    m &= add["REJECT_PRIOR_LIMIT_UP"] != "True"
    passed = add.loc[m]
    print(f"\n新增笔中通过 CFG1 的: {len(passed)} 笔", end="")
    if len(passed):
        print(f"，均收益 {passed[ret_n].mean():+.2f}%")
    else:
        print("（=0，说明多出来的都是 CFG1 会过滤掉的）")


if __name__ == "__main__":
    main()
