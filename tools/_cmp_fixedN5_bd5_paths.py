# -*- coding: utf-8 -*-
"""Split 跌破昨MA5 into two paths and compare returns.

Paths (among fixed-N=5 pre-open picks that eventually break MA5):
  1) 先破MA10再破MA5: first cross above 昨MA10, later cross below 昨MA5 → buy at MA5 break
  2) 未破MA10直接破MA5: no MA10 break before MA5 break (incl. never breaks MA10) → buy at MA5 break

Usage:
  set PYTHONIOENCODING=utf-8
  python tools/_cmp_fixedN5_bd5_paths.py
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
    MODE_OPEN,
    OUT_DIR,
    _ret_from_fill,
    build_candidates,
    enrich_universe_scans,
    load_universe,
    max_dd,
    next_td,
    resolve_fill,
    sim_mode,
    tdays,
)

OUT_XLSX = OUT_DIR / "固定N5_跌破MA5路径拆分.xlsx"
OUT_JSON = OUT_DIR / "固定N5_跌破MA5路径拆分.json"

PATH_AFTER10 = "先破MA10再破MA5"
PATH_DIRECT = "未破MA10直接破MA5"
PATH_NONE = "无跌破MA5"


def classify_bd5_path(r) -> str:
    if not bool(r.get("had_bd5")):
        return PATH_NONE
    bd_ts = r.get("bd5_ts")
    if bd_ts is None or (isinstance(bd_ts, float) and np.isnan(bd_ts)):
        return PATH_NONE
    bd_ts = float(bd_ts)
    if bool(r.get("had_brk10")) and r.get("brk10_ts") is not None:
        br_ts = float(r["brk10_ts"])
        if br_ts < bd_ts:
            return PATH_AFTER10
    # MA5 break without a prior MA10 break (never, or only after)
    return PATH_DIRECT


def trade_stats(g: pd.DataFrame) -> dict:
    return {
        "n": int(len(g)),
        "mean_ret_pct": float(g["ret_pct"].mean()),
        "median_ret_pct": float(g["ret_pct"].median()),
        "winrate_pct": float((g["ret_pct"] > 0).mean() * 100),
        "mean_open_ret_pct": float(g["open_ret_pct"].mean()),
        "mean_vs_open_pp": float((g["ret_pct"] - g["open_ret_pct"]).mean()),
    }


def sim_bd5_path(cands: pd.DataFrame, path_label: str) -> dict:
    cash = float(CAPITAL0)
    held, fills, skips = [], [], []
    eq_curve = []
    all_days = [d for d in tdays if "2026-07-01" <= d <= "2026-08-05"]
    by_day = {d: g for d, g in cands.groupby("买入日")}

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
                Seff = int(g["Seff"].iloc[0])
                target = equity_pre / float(Seff)
                for _, r in g.iterrows():
                    if r["bd5_path"] != path_label:
                        skips.append(1)
                        continue
                    fill_px, fill_t, trig = resolve_fill(r, MODE_BD5)
                    if fill_px is None:
                        skips.append(1)
                        continue
                    ret = _ret_from_fill(float(r["exit_px"]), fill_px)
                    if ret is None:
                        skips.append(1)
                        continue
                    spend = min(target, cash)
                    if spend < 1 or cash < spend * 0.99:
                        skips.append(1)
                        continue
                    cash -= spend
                    pnl = spend * (ret / 100.0)
                    held.append(
                        {
                            "release_day": next_td(str(r["end_date"]), 1),
                            "cost": spend,
                            "pnl": pnl,
                        }
                    )
                    fills.append(
                        {
                            "path": path_label,
                            "选股日": sel_day,
                            "买入日": d,
                            "代码": r["代码"],
                            "股票名称": r.get("股票名称", ""),
                            "spend": spend,
                            "ret_pct": ret,
                            "fill_t": fill_t,
                            "fill_px": fill_px,
                            "open_ret_pct": r["open_ret_pct"],
                            "pnl": pnl,
                            "brk10_t": r["brk10_t"],
                            "bd5_t": r["bd5_t"],
                            "MA5": r["MA5"],
                            "MA10": r["MA10"],
                        }
                    )

        equity = cash + sum(p["cost"] for p in held) + sum(p["pnl"] for p in held)
        eq_curve.append({"date": d, "equity": equity, "path": path_label})

    fills_df = pd.DataFrame(fills)
    curve = pd.DataFrame(eq_curve)
    final = float(curve["equity"].iloc[-1]) if len(curve) else CAPITAL0
    n = len(fills_df)
    return {
        "path": path_label,
        "ret_pct": (final / CAPITAL0 - 1.0) * 100.0,
        "final": final,
        "n_fill": n,
        "n_skip": len(skips),
        "mean_ret_pct": float(fills_df["ret_pct"].mean()) if n else float("nan"),
        "winrate_pct": float((fills_df["ret_pct"] > 0).mean() * 100) if n else float("nan"),
        "max_dd_pct": max_dd(curve["equity"]) if len(curve) else float("nan"),
        "_fills": fills_df,
        "_curve": curve,
    }


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    print("load+scan…")
    df, smap = load_universe()
    univ = enrich_universe_scans(df)
    cands = build_candidates(univ, smap, rank_mode="strength")
    cands = cands.copy()
    cands["bd5_path"] = cands.apply(classify_bd5_path, axis=1)
    print("path counts (N5 picks):")
    print(cands["bd5_path"].value_counts().to_string())

    sub = cands[cands["had_bd5"]].copy()
    sub["ret_pct"] = (sub["exit_px"] / sub["bd5_px"].astype(float) - 1.0) * 100.0

    trade_rows = []
    print("\n=== trade-level (N5 picks that break MA5) ===")
    for name, g in sub.groupby("bd5_path"):
        st = trade_stats(g)
        trade_rows.append({"口径": "票级(N5入选且能跌破)", "路径": name, **st})
        print(
            f"{name}: n={st['n']} mean={st['mean_ret_pct']:+.2f}% "
            f"win={st['winrate_pct']:.1f}% vs开盘笔均={st['mean_vs_open_pp']:+.2f}pp"
        )

    open_st = sim_mode(cands, MODE_OPEN)
    full = sim_mode(cands, MODE_BD5)
    port = {}
    port_rows = [
        {
            "口径": "组合",
            "路径": "开盘买入",
            "组合收益pct": open_st["ret_pct"],
            "成交": open_st["n_fill"],
            "跳过": open_st["n_skip"],
            "笔均": open_st["mean_ret_pct"],
            "胜率": open_st["winrate_pct"],
            "回撤": open_st["max_dd_pct"],
        },
        {
            "口径": "组合",
            "路径": "跌破MA5全量",
            "组合收益pct": full["ret_pct"],
            "成交": full["n_fill"],
            "跳过": full["n_skip"],
            "笔均": full["mean_ret_pct"],
            "胜率": full["winrate_pct"],
            "回撤": full["max_dd_pct"],
        },
    ]
    print("\n=== portfolio ===")
    print(f"开盘买入: {open_st['ret_pct']:+.2f}% fills={open_st['n_fill']}")
    print(f"跌破MA5全量: {full['ret_pct']:+.2f}% fills={full['n_fill']}")
    for path in (PATH_AFTER10, PATH_DIRECT):
        st = sim_bd5_path(cands, path)
        port[path] = st
        port_rows.append(
            {
                "口径": "组合(仅该路径成交)",
                "路径": path,
                "组合收益pct": st["ret_pct"],
                "成交": st["n_fill"],
                "跳过": st["n_skip"],
                "笔均": st["mean_ret_pct"],
                "胜率": st["winrate_pct"],
                "回撤": st["max_dd_pct"],
            }
        )
        print(
            f"{path}: ret={st['ret_pct']:+.2f}% fills={st['n_fill']} "
            f"mean={st['mean_ret_pct']:.2f}% win={st['winrate_pct']:.1f}% "
            f"dd={st['max_dd_pct']:.2f}%"
        )

    fills_all = pd.concat([port[p]["_fills"] for p in port], ignore_index=True)
    curves = pd.concat([port[p]["_curve"] for p in port], ignore_index=True)

    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        pd.DataFrame(trade_rows).to_excel(w, sheet_name="票级对比", index=False)
        pd.DataFrame(port_rows).to_excel(w, sheet_name="组合对比", index=False)
        cands.to_excel(w, sheet_name="入选分类", index=False)
        fills_all.to_excel(w, sheet_name="分路径成交", index=False)
        curves.to_excel(w, sheet_name="权益曲线", index=False)

    meta = {
        "counts": cands["bd5_path"].value_counts().to_dict(),
        "trade": trade_rows,
        "portfolio": port_rows,
        "curves": {
            p: port[p]["_curve"][["date", "equity"]].to_dict(orient="records")
            for p in port
        },
    }
    OUT_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT_XLSX}")
    print(f"wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
