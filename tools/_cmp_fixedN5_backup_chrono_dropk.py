# -*- coding: utf-8 -*-
"""Pseudo-random robustness for backup_chrono: drop earliest k breakers each day.

Each trading day, among open-clip names that break 昨MA5:
  k=0: buy first Seff by break time (original chrono)
  k=1: ignore earliest 1, buy next Seff
  ...
  k=5: ignore earliest 5, buy next Seff (if any remain)

If fewer than Seff remain after drop, buy whatever is left (sizing still equity/Seff
pre-committed — same as capital sim: target=equity/Seff, may leave cash).

Usage:
  set PYTHONIOENCODING=utf-8
  python tools/_cmp_fixedN5_backup_chrono_dropk.py
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
    N_FIXED,
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

OUT_XLSX = OUT_DIR / "固定N5_候补时间序_去最早K名.xlsx"
OUT_JSON = OUT_DIR / "固定N5_候补时间序_去最早K名.json"

DROP_KS = (0, 1, 2, 3, 4, 5)


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


def sim_dropk(univ: pd.DataFrame, smap: dict, drop_k: int) -> dict:
    cash = float(CAPITAL0)
    held, fills, skips = [], [], []
    eq_curve = []
    all_days = [d for d in tdays if "2026-07-01" <= d <= "2026-08-05"]
    by_day = {d: g for d, g in univ.groupby("买入日")}
    day_meta = []

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
                ranked = rank_strength(g)  # rank unused for chrono; keep stable pool order
                if ranked.empty:
                    continue
                S = int(smap.get(sel_day, 0))
                Seff = min(N_FIXED, len(ranked))
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
                n_events = len(events)
                code_to_rank = {e["code"]: i + 1 for i, e in enumerate(events)}
                pool = events[drop_k:] if drop_k > 0 else events
                chosen = pool[:Seff]
                day_meta.append(
                    {
                        "选股日": sel_day,
                        "买入日": d,
                        "drop_k": drop_k,
                        "n_breakers": n_events,
                        "n_after_drop": len(pool),
                        "n_chosen": len(chosen),
                        "Seff": Seff,
                    }
                )

                for ev in chosen:
                    spend = min(target, cash)
                    if spend < 1 or cash < spend * 0.99:
                        skips.append(
                            {
                                "drop_k": drop_k,
                                "选股日": sel_day,
                                "买入日": d,
                                "代码": ev["code"],
                                "skip_reason": "现金不足",
                            }
                        )
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
                            "drop_k": drop_k,
                            "选股日": sel_day,
                            "买入日": d,
                            "end_date": row["end_date"],
                            "代码": ev["code"],
                            "股票名称": row.get("股票名称", ""),
                            "S": S,
                            "Seff": Seff,
                            "n_breakers": n_events,
                            "rank_in_breakers": code_to_rank.get(ev["code"]),
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
        eq_curve.append({"date": d, "equity": equity, "drop_k": drop_k})

    fills_df = pd.DataFrame(fills)
    curve = pd.DataFrame(eq_curve)
    final = float(curve["equity"].iloc[-1]) if len(curve) else CAPITAL0
    n = len(fills_df)
    return {
        "drop_k": drop_k,
        "ret_pct": (final / CAPITAL0 - 1.0) * 100.0,
        "final": final,
        "n_fill": n,
        "n_skip": len(skips),
        "mean_ret_pct": float(fills_df["ret_pct"].mean()) if n else float("nan"),
        "winrate_pct": float((fills_df["ret_pct"] > 0).mean() * 100) if n else float("nan"),
        "max_dd_pct": max_dd(curve["equity"]) if len(curve) else float("nan"),
        "mean_spend": float(fills_df["spend"].mean()) if n else float("nan"),
        "trade_days": int(fills_df["选股日"].nunique()) if n else 0,
        "_fills": fills_df,
        "_curve": curve,
        "_day_meta": pd.DataFrame(day_meta),
    }


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    print("load+scan…")
    df, smap = load_universe()
    univ = enrich_universe_scans(df)

    summary_rows = []
    fills_all, curves_all, days_all = [], [], []
    base_ret = None

    for k in DROP_KS:
        print(f"drop_k={k}…")
        st = sim_dropk(univ, smap, k)
        if k == 0:
            base_ret = st["ret_pct"]
        print(
            f"  ret={st['ret_pct']:+.2f}% fills={st['n_fill']} "
            f"mean={st['mean_ret_pct']:.2f}% win={st['winrate_pct']:.1f}% "
            f"dd={st['max_dd_pct']:.2f}% days={st['trade_days']}"
        )
        summary_rows.append(
            {
                "drop_k": k,
                "说明": "原时间序" if k == 0 else f"去掉最早{k}名后再取Seff",
                "组合收益pct": round(st["ret_pct"], 4),
                "vs_k0_pp": round(st["ret_pct"] - (base_ret or st["ret_pct"]), 4),
                "成交": st["n_fill"],
                "跳过现金": st["n_skip"],
                "笔均pct": round(st["mean_ret_pct"], 4) if st["n_fill"] else None,
                "胜率pct": round(st["winrate_pct"], 2) if st["n_fill"] else None,
                "回撤pct": round(st["max_dd_pct"], 4),
                "有成交选股日": st["trade_days"],
            }
        )
        if len(st["_fills"]):
            fills_all.append(st["_fills"])
        if len(st["_curve"]):
            curves_all.append(st["_curve"])
        if len(st["_day_meta"]):
            days_all.append(st["_day_meta"])

    summary = pd.DataFrame(summary_rows)
    # scarcity: how often after drop_k fewer than Seff remain
    day_df = pd.concat(days_all, ignore_index=True) if days_all else pd.DataFrame()
    scarcity = []
    if len(day_df):
        for k, g in day_df.groupby("drop_k"):
            scarcity.append(
                {
                    "drop_k": int(k),
                    "选股日数": int(g["选股日"].nunique()),
                    "日均破位只数": round(float(g["n_breakers"].mean()), 2),
                    "日均去掉后剩余": round(float(g["n_after_drop"].mean()), 2),
                    "日均实际入选": round(float(g["n_chosen"].mean()), 2),
                    "剩余<Seff日数": int((g["n_after_drop"] < g["Seff"]).sum()),
                    "剩余=0日数": int((g["n_after_drop"] == 0).sum()),
                }
            )

    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="汇总", index=False)
        if scarcity:
            pd.DataFrame(scarcity).to_excel(w, sheet_name="每日稀缺", index=False)
        if fills_all:
            pd.concat(fills_all, ignore_index=True).to_excel(
                w, sheet_name="成交明细", index=False
            )
        if curves_all:
            pd.concat(curves_all, ignore_index=True).to_excel(
                w, sheet_name="权益曲线", index=False
            )
        if len(day_df):
            day_df.to_excel(w, sheet_name="每日名额", index=False)

    meta = {
        "capital0": CAPITAL0,
        "fixed_n": N_FIXED,
        "drop_ks": list(DROP_KS),
        "summary": summary_rows,
        "scarcity": scarcity,
        "curves": {},
    }
    if curves_all:
        call = pd.concat(curves_all, ignore_index=True)
        for k, g in call.groupby("drop_k"):
            meta["curves"][str(int(k))] = g[["date", "equity"]].to_dict(orient="records")

    OUT_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT_XLSX}")
    print(summary.to_string(index=False))
    if scarcity:
        print(pd.DataFrame(scarcity).to_string(index=False))


if __name__ == "__main__":
    main()
