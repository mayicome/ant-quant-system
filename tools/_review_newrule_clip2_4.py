# -*- coding: utf-8 -*-
"""Review new-rule backtest vs Cond123 + clip(2,4)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sim_cond123_clip2_4 import (  # noqa: E402
    CAPITAL0,
    clip_n,
    code6,
    day_stats,
    load_sel,
    load_trades,
    sim,
)

ROOT = Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门")
NEW_SUM = ROOT / "各日选股收益汇总_新规则.xlsx"
OLD_COND = ROOT / (
    "besttest_开盘夹档_条件一_无涨停_均线差0.5to2_"
    "条件二开盘相对MA5满足0to2_条件三MA5lt10lt20_各日选股收益汇总.xlsx"
)
SEL_NEW = ROOT / "选股结果_东财热门-besttest-无涨停均线差0.5to2-MA空头排列_2026-07-01_2026-07-31.xls"
SEL_OLD = ROOT / "选股结果_东财热门-besttest全量-无个股过滤_2026-07-01_2026-07-31.xls"


def keys(df: pd.DataFrame) -> set:
    return set(zip(df["选股日"], df["代码"]))


def pstats(st: dict) -> None:
    print(
        f"  {st['label']:<16} 期末={st['final']:.0f} 收益={st['ret_pct']:+.2f}% "
        f"成交={st['n_fill']} 跳过={st['n_skip']} 宇宙={st['n_universe']} "
        f"均ret={st['mean_ret_pct']:+.2f}% 胜率={st['winrate_pct']:.1f}% "
        f"最大回撤={st['max_dd_pct']:.2f}%"
    )


def main() -> None:
    new = pd.read_excel(NEW_SUM)
    old = pd.read_excel(OLD_COND)
    for df in (new, old):
        df["代码"] = df["代码"].map(code6)
        df["选股日"] = pd.to_datetime(df["选股日"]).dt.strftime("%Y-%m-%d")
        df["收益率pct"] = pd.to_numeric(df["收益率pct"], errors="coerce")

    print("=" * 60)
    print("1) 均收益 / 笔数")
    print("=" * 60)
    for name, df in [("新规则", new), ("旧Cond123 besttest", old)]:
        r = df["收益率pct"]
        print(
            f"{name}: n={len(df)} 均ret={r.mean():+.4f}% 中位={r.median():+.4f}% "
            f"胜率={(r > 0).mean() * 100:.1f}% 选股日数={df['选股日'].nunique()} "
            f"日均笔={df.groupby('选股日').size().mean():.2f}"
        )

    knew, kold = keys(new), keys(old)
    inter = knew & kold
    only_new = knew - kold
    only_old = kold - knew
    if knew <= kold:
        rel = "新 ⊆ 旧"
    elif kold <= knew:
        rel = "新 ⊇ 旧"
    else:
        rel = "交叉 (互不包含)"

    print()
    print("=" * 60)
    print("2) 集合关系 新规则 vs 旧Cond123(215)")
    print("=" * 60)
    print(
        f"|新|={len(knew)} |旧|={len(kold)} |交|={len(inter)} "
        f"仅新={len(only_new)} 仅旧={len(only_old)}"
    )
    print(f"关系: {rel}")
    print(f"覆盖旧集合比例: {len(inter) / len(kold) * 100:.1f}%")
    print(f"新在旧中比例: {len(inter) / len(knew) * 100:.1f}%")

    d1 = "2026-07-01"
    n1, o1 = keys(new[new["选股日"] == d1]), keys(old[old["选股日"] == d1])
    print(
        f"\nDay1 {d1}: 新={len(n1)} 旧Cond123={len(o1)} 交={len(n1 & o1)} "
        f"仅新={len(n1 - o1)} 仅旧={len(o1 - n1)}"
    )
    print(f"Day1 是否完整Cond123? {n1 == o1}  (新⊆旧={n1 <= o1}, 新⊇旧={o1 <= n1})")
    if n1 != o1:
        print("  Day1 新买入:", sorted(c for _, c in n1))
        miss = sorted(c for _, c in (o1 - n1))
        print("  Day1 旧缺失于新:", miss[:30], "..." if len(miss) > 30 else "")
    print(f"\n整体是否完整Cond123(=旧215)? {knew == kold}")

    print()
    print("=" * 60)
    print("3) 选股池对比 S")
    print("=" * 60)
    _, order_new, smap_new, strength_new = load_sel(SEL_NEW)
    _, order_old, smap_old, strength_old = load_sel(SEL_OLD)
    print(
        f"新选股池天数={len(smap_new)} 总票={sum(smap_new.values())} "
        f"日均S={np.mean(list(smap_new.values())):.1f}"
    )
    print(
        f"旧选股池天数={len(smap_old)} 总票={sum(smap_old.values())} "
        f"日均S={np.mean(list(smap_old.values())):.1f}"
    )
    print("按日 S_new / S_old / Seff_new(2,4):")
    for d in sorted(smap_new):
        Sn, So = smap_new.get(d, 0), smap_old.get(d, 0)
        print(
            f"  {d}: S_new={Sn:3d} S_old={So:3d} Seff={clip_n(Sn, 2, 4)}  "
            f"新买入={len(new[new['选股日'] == d])} 旧Cond={len(old[old['选股日'] == d])}"
        )

    print()
    print("=" * 60)
    print("4) 收益对比细节")
    print("=" * 60)
    new_m = new.set_index(["选股日", "代码"])
    old_m = old.set_index(["选股日", "代码"])
    common = sorted(inter)
    if common:
        nr = new_m.loc[common, "收益率pct"]
        or_ = old_m.loc[common, "收益率pct"]
        print(
            f"交集{len(common)}笔: 新均ret={nr.mean():+.4f}% 旧均ret={or_.mean():+.4f}% "
            f"ret差均值={(nr - or_).mean():+.6f}pp  "
            f"完全相同ret数={(nr.round(6) == or_.round(6)).sum()}"
        )

    print()
    print("=" * 60)
    print("5) clip(2,4) 离线回放 · 10万")
    print("=" * 60)
    trades_new = load_trades(NEW_SUM, order_new, strength_new)
    trades_old = load_trades(OLD_COND, order_old, strength_old)
    trades_old_neword = load_trades(OLD_COND, order_new, strength_new)
    trades_new_oldord = load_trades(NEW_SUM, order_old, strength_old)

    r_new = sim(trades_new, smap_new, 2, 4, CAPITAL0, "新规则")
    r_new["day"] = day_stats(r_new["fills"], smap_new, 2, 4)
    r_old = sim(trades_old, smap_old, 2, 4, CAPITAL0, "旧Cond123")
    r_old["day"] = day_stats(r_old["fills"], smap_old, 2, 4)
    r_old_neword = sim(trades_old_neword, smap_new, 2, 4, CAPITAL0, "旧Cond+新池序")
    r_new_oldsel = sim(trades_new_oldord, smap_old, 2, 4, CAPITAL0, "新规则+旧池序")

    for r in (r_new, r_old, r_old_neword, r_new_oldsel):
        pstats(r["stats"])

    print("\n参照: 旧 besttest clip(2,4) meta = +8.75% (n_fill=41, universe=215)")
    print(
        f"Δ(新规则clip - 旧clip) 收益pp = "
        f"{r_new['stats']['ret_pct'] - r_old['stats']['ret_pct']:+.2f}"
    )

    theo = r_old_neword["fills"]
    theo_keys = set(zip(theo["选股日"], theo["代码"])) if len(theo) else set()
    print()
    print("=" * 60)
    print("6) 新规则买入 vs 理论clip(2,4)填充 (旧Cond∩新池强度分)")
    print("=" * 60)
    print(
        f"理论clip成交={len(theo_keys)} 新规则={len(knew)} 交={len(theo_keys & knew)} "
        f"仅理论={len(theo_keys - knew)} 仅新={len(knew - theo_keys)}"
    )
    print(f"新==理论clip? {knew == theo_keys}")
    print(f"新⊇理论? {theo_keys <= knew}  新⊆理论? {knew <= theo_keys}")

    theo_new = r_new["fills"]
    theo_new_keys = set(zip(theo_new["选股日"], theo_new["代码"])) if len(theo_new) else set()
    print(
        f"\nclip(新规则自身): 成交={len(theo_new_keys)} vs 全量新={len(knew)} "
        f"(若相等说明新规则买入已≤Seff无需再截)"
    )
    print(f"新全量==clip(新)? {knew == theo_new_keys}")

    # day-by-day using selection order from order_new
    print()
    print("=" * 60)
    print("7) 按日: 新买入 vs Cond123∩选股序 前Seff")
    print("=" * 60)
    rows = []
    for d in sorted(smap_new):
        S = smap_new[d]
        Seff = clip_n(S, 2, 4)
        cond_d = set(old.loc[old["选股日"] == d, "代码"])
        day_ordered = sorted(
            [(ord_, c) for (dd, c), ord_ in order_new.items() if dd == d],
            key=lambda x: x[0],
        )
        elig = [c for _, c in day_ordered if c in cond_d]
        theo_codes = elig[:Seff]
        bought = list(dict.fromkeys(new.loc[new["选股日"] == d, "代码"].tolist()))
        bset, tset = set(bought), set(theo_codes)
        elig_set = set(elig)
        rows.append(
            {
                "选股日": d,
                "S": S,
                "Seff": Seff,
                "n_cond_old": len(cond_d),
                "n_elig": len(elig),
                "n_buy": len(bought),
                "target": min(Seff, len(elig)),
                "equal_clip": bset == tset,
                "is_prefix": (
                    (not (bset - elig_set))
                    and bset == set(elig[: len(bought)])
                    and len(bought) <= len(elig)
                ),
                "complete_cond": bset == cond_d,
                "bought": ",".join(bought),
                "theo_clip": ",".join(theo_codes),
                "missing_vs_clip": ",".join([c for c in theo_codes if c not in bset]),
                "extra_vs_clip": ",".join([c for c in bought if c not in tset]),
            }
        )
    daydf = pd.DataFrame(rows)
    print(
        daydf[
            [
                "选股日",
                "S",
                "Seff",
                "n_cond_old",
                "n_elig",
                "n_buy",
                "target",
                "equal_clip",
                "is_prefix",
                "complete_cond",
            ]
        ].to_string(index=False)
    )
    print(f"\nequal_clip: {daydf['equal_clip'].sum()}/{len(daydf)}")
    print(f"is_prefix: {daydf['is_prefix'].sum()}/{len(daydf)}")
    print(f"complete_cond: {daydf['complete_cond'].sum()}/{len(daydf)}")
    print(f"n_buy sum={daydf['n_buy'].sum()}  target sum={daydf['target'].sum()}")

    bad = daydf[~daydf["equal_clip"]]
    if len(bad):
        print("\n--- 与理论clip不一致的日子 ---")
        print(
            bad[
                [
                    "选股日",
                    "n_buy",
                    "target",
                    "theo_clip",
                    "bought",
                    "missing_vs_clip",
                    "extra_vs_clip",
                    "is_prefix",
                ]
            ].to_string(index=False)
        )

    out = ROOT / "新规则_clip2_4_复核对比.xlsx"
    summary = pd.DataFrame(
        [
            {"项": "新规则笔数", "值": len(new)},
            {"项": "旧Cond123笔数", "值": len(old)},
            {"项": "交集", "值": len(inter)},
            {"项": "仅新", "值": len(only_new)},
            {"项": "仅旧", "值": len(only_old)},
            {"项": "关系", "值": rel},
            {"项": "新均ret%", "值": round(float(new["收益率pct"].mean()), 4)},
            {"项": "旧均ret%", "值": round(float(old["收益率pct"].mean()), 4)},
            {"项": "新clip2_4收益%", "值": round(r_new["stats"]["ret_pct"], 4)},
            {"项": "旧clip2_4收益%", "值": round(r_old["stats"]["ret_pct"], 4)},
            {
                "项": "旧Cond+新池序clip收益%",
                "值": round(r_old_neword["stats"]["ret_pct"], 4),
            },
            {"项": "参照meta+8.75", "值": 8.7495},
            {"项": "Day1完整Cond123", "值": bool(n1 == o1)},
            {"项": "整体完整Cond123", "值": bool(knew == kold)},
            {"项": "新==理论clip(旧Cond∩新池)", "值": bool(knew == theo_keys)},
        ]
    )
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="结论摘要", index=False)
        daydf.to_excel(w, sheet_name="按日对照", index=False)
        r_new["fills"].to_excel(w, sheet_name="新规则clip成交", index=False)
        r_new["curve"].to_excel(w, sheet_name="新规则clip权益", index=False)
        r_old["fills"].to_excel(w, sheet_name="旧Condclip成交", index=False)
        r_old["curve"].to_excel(w, sheet_name="旧Condclip权益", index=False)
        r_old_neword["fills"].to_excel(w, sheet_name="旧Cond新池序clip成交", index=False)
        pd.DataFrame(
            [
                r_new["stats"],
                r_old["stats"],
                r_old_neword["stats"],
                r_new_oldsel["stats"],
            ]
        ).to_excel(w, sheet_name="clip统计", index=False)
        pd.DataFrame(
            [{"选股日": d, "代码": c, "侧": "仅新"} for d, c in sorted(only_new)]
            + [{"选股日": d, "代码": c, "侧": "仅旧"} for d, c in sorted(only_old)]
        ).to_excel(w, sheet_name="集合差集", index=False)

    # also dump equity csv for new clip
    r_new["fills"].to_csv(
        ROOT / "新规则_clip2_4_成交明细.csv", index=False, encoding="utf-8-sig"
    )
    r_new["curve"].to_csv(
        ROOT / "新规则_clip2_4_权益曲线.csv", index=False, encoding="utf-8-sig"
    )
    print(f"\n导出: {out}")


if __name__ == "__main__":
    main()
