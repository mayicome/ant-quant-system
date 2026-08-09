# -*- coding: utf-8 -*-
"""Apples-to-apples: same 215 Cond123 set, score-key clip(2,4) on new vs old sel S.

Compares:
  A) 新规则 215 + 新选股池 S + 强度分取票
  B) 旧 Cond123 215 + 旧全量池 S + 强度分取票  (re-rank old path)
  C) prior file-order 新规则 sim (from saved 成交明细 if present) for delta note
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sim_cond123_clip2_4 import (  # noqa: E402
    CAPITAL0,
    EXPORT_ELIG_WEIGHT,
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
PRIOR_FILE_ORDER = ROOT / "新规则_无clip资格_clip2_4_成交明细.csv"
PRIOR_BEST_META = ROOT / "Cond123_anytag_vs_besttest_clip2_4_meta.json"


def keys(df: pd.DataFrame) -> set:
    return set(zip(df["选股日"], df["代码"]))


def main() -> None:
    _, order_new, smap_new, strength_new = load_sel(SEL_NEW)
    _, order_old, smap_old, strength_old = load_sel(SEL_OLD)

    tr_new = load_trades(NEW_SUM, order_new, strength_new)
    tr_old = load_trades(OLD_COND, order_old, strength_old)

    kn = keys(tr_new[tr_new["in_pool"]])
    ko = keys(tr_old[tr_old["in_pool"]])
    print(f"universe new={len(kn)} old={len(ko)} same={kn == ko} inter={len(kn & ko)}")

    r_new = sim(tr_new, smap_new, 2, 4, CAPITAL0, "新规则215_强度分_新S")
    r_old_oldS = sim(tr_old, smap_old, 2, 4, CAPITAL0, "旧Cond215_强度分_旧S")
    r_old_newS = sim(tr_old, smap_new, 2, 4, CAPITAL0, "旧Cond215_强度分_新S")
    r_new_oldS = sim(tr_new, smap_old, 2, 4, CAPITAL0, "新规则215_强度分_旧S")

    for r in (r_new, r_old_oldS, r_old_newS, r_new_oldS):
        st = r["stats"]
        print(
            f"{st['label']:<22} 期末={st['final']:.0f} 收益={st['ret_pct']:+.2f}% "
            f"成交={st['n_fill']} 均ret={st['mean_ret_pct']:+.2f}% "
            f"胜率={st['winrate_pct']:.1f}% 回撤={st['max_dd_pct']:.2f}%"
        )

    fn = keys(r_new["fills"]) if len(r_new["fills"]) else set()
    fo = keys(r_old_newS["fills"]) if len(r_old_newS["fills"]) else set()
    print(
        f"\n同215+同强度分+同新S: 新clip∩旧clip="
        f"{len(fn & fo)}/{len(fn | fo)} identical_picks={fn == fo}"
    )
    print(
        f"Δ收益(新S强度 新-旧)={r_new['stats']['ret_pct'] - r_old_newS['stats']['ret_pct']:+.4f}pp"
    )

    prior_best = None
    if PRIOR_BEST_META.exists():
        meta = json.loads(PRIOR_BEST_META.read_text(encoding="utf-8"))
        prior_best = (meta.get("stats") or {}).get("besttest") or {}
        print(
            f"\n历史 file-order Cond123_besttest_clip2_4: "
            f"收益={prior_best.get('ret_pct')}% 成交={prior_best.get('n_fill')}"
        )

    out_fills = ROOT / "Cond123_scorekey_clip2_4_成交明细对比.xlsx"
    with pd.ExcelWriter(out_fills, engine="openpyxl") as w:
        pd.DataFrame(
            [
                r_new["stats"],
                r_old_oldS["stats"],
                r_old_newS["stats"],
                r_new_oldS["stats"],
            ]
        ).to_excel(w, sheet_name="汇总", index=False)
        r_new["fills"].to_excel(w, sheet_name="新规则_新S", index=False)
        r_old_newS["fills"].to_excel(w, sheet_name="旧Cond_新S", index=False)
        r_old_oldS["fills"].to_excel(w, sheet_name="旧Cond_旧S", index=False)
    print(f"exported {out_fills.name}")
    print(f"EXPORT_ELIG_WEIGHT={EXPORT_ELIG_WEIGHT}")

if __name__ == "__main__":
    main()
