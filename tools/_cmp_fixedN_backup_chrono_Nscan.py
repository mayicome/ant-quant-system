# -*- coding: utf-8 -*-
"""backup_chrono (no look-ahead) for fixed N in {4,5,6}.

Full open-clip pool races by 昨MA5 break time; N only caps fills and splits equity.

Usage:
  set PYTHONIOENCODING=utf-8
  python tools/_cmp_fixedN_backup_chrono_Nscan.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_TOOLS = _ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from _cmp_fixedN5_buy_triggers import (  # noqa: E402
    CAPITAL0,
    MODE_BD5,
    OUT_DIR,
    _ret_from_fill,
    enrich_universe_scans,
    load_universe,
    max_dd,
    next_td,
    rank_strength,
    resolve_fill,
    tdays,
)

OUT_XLSX = OUT_DIR / "固定N_候补时间序_N456对比.xlsx"
OUT_JSON = OUT_DIR / "固定N_候补时间序_N456对比.json"
NS = (4, 5, 6)


def _row_dict(r: pd.Series) -> dict:
    return {c: r.get(c) for c in r.index}


def _bd5_event(row: dict):
    fill_px, fill_t, trig = resolve_fill(pd.Series(row), MODE_BD5)
    if fill_px is None:
        return None
    ts = row.get("bd5_ts")
    if ts is None or (isinstance(ts, float) and np.isnan(ts)):
        return None
    ret = _ret_from_fill(float(row["exit_px"]), float(fill_px))
    if ret is None:
        return None
    return {
        "ts": float(ts),
        "fill_px": float(fill_px),
        "fill_t": fill_t,
        "trig": trig,
        "ret_pct": float(ret),
        "code": row["代码"],
        "row": row,
    }


def sim_chrono(univ: pd.DataFrame, smap: dict, n_fixed: int) -> dict:
    cash = float(CAPITAL0)
    held, fills, skips = [], [], []
    eq_curve = []
    all_days = [d for d in tdays if "2026-07-01" <= d <= "2026-08-05"]
    by_day = {d: g for d, g in univ.groupby("买入日")}

    for d in all_days:
        still = []
        for p in held:
            if p["release_day"] == d:
                cash += p["cost"] + p["pnl"]
            else:
                still.append(p)
        held = still
        equity_pre = cash + sum(p["cost"] for p in held)

        if d in by_day:
            for sel_day, g in by_day[d].groupby("选股日", sort=False):
                ranked = rank_strength(g)
                if ranked.empty:
                    continue
                S = int(smap.get(sel_day, 0))
                Seff = min(n_fixed, len(ranked))
                if Seff <= 0:
                    continue
                rows = [_row_dict(r) for _, r in ranked.iterrows()]
                target = equity_pre / float(Seff)

                events = []
                for row in rows:
                    ev = _bd5_event(row)
                    if ev is not None:
                        events.append(ev)
                events.sort(key=lambda e: (e["ts"], e["code"]))
                chosen = events[:Seff]

                for ev in chosen:
                    spend = min(target, cash)
                    if spend < 1 or cash < spend * 0.99:
                        skips.append(1)
                        continue
                    cash -= spend
                    pnl = spend * (ev["ret_pct"] / 100.0)
                    row = ev["row"]
                    held.append(
                        {
                            "release_day": next_td(str(row["end_date"]), 1),
                            "cost": spend,
                            "pnl": pnl,
                        }
                    )
                    fills.append(
                        {
                            "N": n_fixed,
                            "选股日": sel_day,
                            "买入日": d,
                            "end_date": row["end_date"],
                            "代码": ev["code"],
                            "股票名称": row.get("股票名称", ""),
                            "S": S,
                            "Seff": Seff,
                            "n_breakers": len(events),
                            "target": target,
                            "spend": spend,
                            "fill_px": ev["fill_px"],
                            "fill_t": ev["fill_t"],
                            "bd5_ts": ev["ts"],
                            "ret_pct": ev["ret_pct"],
                            "pnl": pnl,
                        }
                    )

        equity = cash + sum(p["cost"] for p in held) + sum(p["pnl"] for p in held)
        eq_curve.append({"date": d, "equity": equity, "N": n_fixed})

    fills_df = pd.DataFrame(fills)
    curve = pd.DataFrame(eq_curve)
    final = float(curve["equity"].iloc[-1]) if len(curve) else CAPITAL0
    n = len(fills_df)
    return {
        "N": n_fixed,
        "ret_pct": (final / CAPITAL0 - 1.0) * 100.0,
        "final": final,
        "n_fill": n,
        "n_skip": len(skips),
        "mean_ret_pct": float(fills_df["ret_pct"].mean()) if n else float("nan"),
        "winrate_pct": float((fills_df["ret_pct"] > 0).mean() * 100) if n else float("nan"),
        "max_dd_pct": max_dd(curve["equity"]) if len(curve) else float("nan"),
        "mean_spend": float(fills_df["spend"].mean()) if n else float("nan"),
        "mean_Seff": float(fills_df["Seff"].mean()) if n else float("nan"),
        "trade_days": int(fills_df["选股日"].nunique()) if n else 0,
        "_fills": fills_df,
        "_curve": curve,
    }


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    print("load+scan…")
    df, smap = load_universe()
    univ = enrich_universe_scans(df)

    rows = []
    fills_all, curves_all = [], []
    by_n = {}
    for n in NS:
        print(f"N={n}…")
        st = sim_chrono(univ, smap, n)
        by_n[n] = st
        print(
            f"  ret={st['ret_pct']:+.2f}% fills={st['n_fill']} "
            f"mean={st['mean_ret_pct']:.2f}% win={st['winrate_pct']:.1f}% "
            f"dd={st['max_dd_pct']:.2f}% spend={st['mean_spend']:.0f}"
        )
        if len(st["_fills"]):
            fills_all.append(st["_fills"])
        if len(st["_curve"]):
            curves_all.append(st["_curve"])

    base = by_n[5]["ret_pct"]
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

    # overlap of fill sets vs N=5
    overlap_rows = []
    f5 = by_n[5]["_fills"]
    if len(f5):
        keys5 = set(zip(f5["选股日"].astype(str), f5["代码"].astype(str)))
        for n in NS:
            fn = by_n[n]["_fills"]
            if not len(fn):
                continue
            keysn = set(zip(fn["选股日"].astype(str), fn["代码"].astype(str)))
            both = keys5 & keysn
            overlap_rows.append(
                {
                    "N": n,
                    "成交": len(keysn),
                    "与N5交集": len(both),
                    "仅本N": len(keysn - keys5),
                    "仅N5": len(keys5 - keysn),
                    "占N5比例": round(len(both) / len(keys5) * 100, 1) if keys5 else None,
                }
            )

    summary = pd.DataFrame(rows)
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="汇总", index=False)
        if overlap_rows:
            pd.DataFrame(overlap_rows).to_excel(w, sheet_name="与N5成交重叠", index=False)
        if fills_all:
            pd.concat(fills_all, ignore_index=True).to_excel(
                w, sheet_name="成交明细", index=False
            )
        if curves_all:
            pd.concat(curves_all, ignore_index=True).to_excel(
                w, sheet_name="权益曲线", index=False
            )

    meta = {
        "capital0": CAPITAL0,
        "ns": list(NS),
        "summary": rows,
        "overlap_vs_n5": overlap_rows,
        "curves": {
            str(n): by_n[n]["_curve"][["date", "equity"]].to_dict(orient="records")
            for n in NS
        },
    }
    OUT_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT_XLSX}")
    print(summary.to_string(index=False))
    if overlap_rows:
        print(pd.DataFrame(overlap_rows).to_string(index=False))


if __name__ == "__main__":
    main()
