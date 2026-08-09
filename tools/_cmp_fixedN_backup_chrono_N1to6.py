# -*- coding: utf-8 -*-
"""backup_chrono N scan for N=1..6 + daily last fill time."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_TOOLS = _ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from _cmp_fixedN_backup_chrono_Nscan import sim_chrono  # noqa: E402
from _cmp_fixedN5_buy_triggers import (  # noqa: E402
    CAPITAL0,
    OUT_DIR,
    enrich_universe_scans,
    load_universe,
)

OUT_XLSX = OUT_DIR / "固定N_候补时间序_N1to6对比.xlsx"
OUT_JSON = OUT_DIR / "固定N_候补时间序_N1to6对比.json"
NS = (1, 2, 3, 4, 5, 6)


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    print("load+scan…")
    df, smap = load_universe()
    univ = enrich_universe_scans(df)

    by_n = {}
    for n in NS:
        print(f"N={n}…")
        st = sim_chrono(univ, smap, n)
        by_n[n] = st
        print(
            f"  ret={st['ret_pct']:+.2f}% fills={st['n_fill']} "
            f"mean={st['mean_ret_pct']:.2f}% win={st['winrate_pct']:.1f}% "
            f"dd={st['max_dd_pct']:.2f}%"
        )

    base = by_n[5]["ret_pct"]
    rows = []
    for n in NS:
        st = by_n[n]
        rows.append(
            {
                "N": n,
                "组合收益pct": round(st["ret_pct"], 4),
                "vs_N5_pp": round(st["ret_pct"] - base, 4),
                "成交": st["n_fill"],
                "跳过现金": st["n_skip"],
                "笔均pct": round(st["mean_ret_pct"], 4),
                "胜率pct": round(st["winrate_pct"], 2),
                "回撤pct": round(st["max_dd_pct"], 4),
                "笔均金额": round(st["mean_spend"], 2),
                "有成交选股日": st["trade_days"],
            }
        )
    summary = pd.DataFrame(rows)
    print(summary.to_string(index=False))

    f5 = by_n[5]["_fills"]
    keys5 = set(zip(f5["选股日"].astype(str), f5["代码"].astype(str)))
    ov = []
    for n in NS:
        fn = by_n[n]["_fills"]
        keysn = set(zip(fn["选股日"].astype(str), fn["代码"].astype(str)))
        both = keys5 & keysn
        ov.append(
            {
                "N": n,
                "成交": len(keysn),
                "与N5交集": len(both),
                "仅本N": len(keysn - keys5),
                "仅N5": len(keys5 - keysn),
                "占N5比例": round(len(both) / len(keys5) * 100, 1) if keys5 else None,
            }
        )
    print(pd.DataFrame(ov).to_string(index=False))

    last_all = []
    for n in NS:
        g = by_n[n]["_fills"]
        for buy_day, gg in g.groupby("买入日"):
            gg = gg.copy()
            gg["bd5_ts"] = pd.to_numeric(gg["bd5_ts"], errors="coerce")
            last = gg.loc[gg["bd5_ts"].idxmax()]
            code = last["代码"]
            try:
                code_s = str(int(float(code))).zfill(6)
            except Exception:
                code_s = str(code)
            last_all.append(
                {
                    "N": n,
                    "买入日": str(buy_day)[:10],
                    "最后买入时间": last["fill_t"],
                    "当日成交笔数": len(gg),
                    "代码": code_s,
                }
            )
    last_all = pd.DataFrame(last_all)
    piv = last_all.pivot(index="买入日", columns="N", values="最后买入时间")
    cnt = last_all.pivot(index="买入日", columns="N", values="当日成交笔数")
    print("\n=== 每日最后一笔 ===")
    print(piv.to_string())
    print("\n=== 当日笔数 ===")
    print(cnt.to_string())

    fills_all = pd.concat([by_n[n]["_fills"] for n in NS], ignore_index=True)
    curves_all = pd.concat([by_n[n]["_curve"] for n in NS], ignore_index=True)

    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="汇总", index=False)
        pd.DataFrame(ov).to_excel(w, sheet_name="与N5成交重叠", index=False)
        fills_all.to_excel(w, sheet_name="成交明细", index=False)
        curves_all.to_excel(w, sheet_name="权益曲线", index=False)
        last_all.to_excel(w, sheet_name="每日最后买入", index=False)
        piv.to_excel(w, sheet_name="最后买入时间透视")
        cnt.to_excel(w, sheet_name="当日笔数透视")

    meta = {
        "capital0": CAPITAL0,
        "ns": list(NS),
        "summary": rows,
        "overlap_vs_n5": ov,
        "curves": {
            str(n): by_n[n]["_curve"][["date", "equity"]].to_dict(orient="records")
            for n in NS
        },
    }
    OUT_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT_XLSX}")


if __name__ == "__main__":
    main()
