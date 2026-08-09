# -*- coding: utf-8 -*-
"""N=4 backup_chrono: buy on MA5 breakdown vs wait rebound R% from post-break low.

Schemes:
  immediate     — first cross below 昨MA5 (existing)
  rebound_0.3%  — after breakdown, buy when last >= post-break_low * (1+R)
  rebound_0.5%
  rebound_1.0%

Race: full open-clip pool, first Seff=min(4,pool) chronologically by fill time.

Usage:
  set PYTHONIOENCODING=utf-8
  python tools/_cmp_fixedN4_bd5_rebound.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
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
    CLOSE_HM,
    OUT_DIR,
    TICKS_ROOT,
    _hhmmss,
    _hm_from_ts,
    _ret_from_fill,
    enrich_universe_scans,
    load_universe,
    max_dd,
    next_td,
    rank_strength,
    tdays,
)

OUT_XLSX = OUT_DIR / "固定N4_跌破MA5_反弹再买对比.xlsx"
OUT_JSON = OUT_DIR / "固定N4_跌破MA5_反弹再买对比.json"
N_FIXED = 4
REBOUNDS = (0.0, 0.003, 0.005, 0.006, 0.007, 0.008, 0.009, 0.01)  # 0 = immediate


def _scheme_name(r: float) -> str:
    if r <= 0:
        return "刚破即买"
    return f"反弹{r * 100:.1f}%".replace(".0%", "%") if abs(r * 100 - round(r * 100)) < 1e-9 else f"反弹{r * 100:.1f}%"


def _scan_fill(code: str, buy_day: str, ma5: float, rebound: float) -> dict | None:
    """Return fill event dict or None. rebound=0 → first cross below ma5."""
    try:
        ma5 = float(ma5)
    except (TypeError, ValueError):
        return None
    if ma5 <= 0:
        return None

    path = TICKS_ROOT / buy_day.replace("-", "") / f"{code}.parquet"
    if not path.is_file():
        return None
    try:
        df = pd.read_parquet(path, columns=["time_ts", "lastPrice", "ask1", "open"])
    except Exception:
        try:
            df = pd.read_parquet(path)
        except Exception:
            return None
    if df is None or len(df) == 0:
        return None

    ts = pd.to_numeric(df["time_ts"], errors="coerce")
    last = pd.to_numeric(df["lastPrice"], errors="coerce")
    ask = (
        pd.to_numeric(df["ask1"], errors="coerce")
        if "ask1" in df.columns
        else pd.Series([np.nan] * len(df))
    )
    m = ts.notna() & last.notna() & (last > 0)
    if not bool(m.any()):
        return None
    df = df.loc[m].copy()
    df["_ts"] = ts[m]
    df["_last"] = last[m]
    df["_ask"] = ask[m]
    df["_hm"] = df["_ts"].map(_hm_from_ts)
    df = df[(df["_hm"] >= 930) & (df["_hm"] <= CLOSE_HM)].reset_index(drop=True)
    if df.empty:
        return None

    def fill_px(row) -> float:
        a = row["_ask"]
        try:
            if a == a and float(a) > 0:
                return float(a)
        except (TypeError, ValueError):
            pass
        return float(row["_last"])

    prev = None
    try:
        o0 = float(df.iloc[0]["open"]) if "open" in df.columns else float("nan")
        if o0 == o0 and o0 > 0:
            prev = o0
    except (TypeError, ValueError):
        prev = None

    broken = False
    break_ts = None
    post_low = None
    post_low_ts = None

    for _, row in df.iterrows():
        px = float(row["_last"])
        tsv = float(row["_ts"])
        if not broken:
            if prev is not None and prev >= ma5 and px < ma5:
                broken = True
                break_ts = tsv
                post_low = px
                post_low_ts = tsv
                if rebound <= 0:
                    return {
                        "fill_px": fill_px(row),
                        "fill_t": _hhmmss(tsv),
                        "fill_ts": tsv,
                        "break_t": _hhmmss(break_ts),
                        "break_ts": break_ts,
                        "post_low": post_low,
                        "rebound": rebound,
                    }
            prev = px
            continue

        # after breakdown
        if post_low is None or px < post_low:
            post_low = px
            post_low_ts = tsv
        thr = float(post_low) * (1.0 + float(rebound))
        if px + 1e-12 >= thr:
            return {
                "fill_px": fill_px(row),
                "fill_t": _hhmmss(tsv),
                "fill_ts": tsv,
                "break_t": _hhmmss(break_ts) if break_ts else "",
                "break_ts": break_ts,
                "post_low": post_low,
                "post_low_t": _hhmmss(post_low_ts) if post_low_ts else "",
                "rebound": rebound,
                "rebound_thr": thr,
            }
        prev = px

    return None


def _row_dict(r: pd.Series) -> dict:
    return {c: r.get(c) for c in r.index}


def sim(univ: pd.DataFrame, smap: dict, rebound: float) -> dict:
    scheme = _scheme_name(rebound)
    cash = float(CAPITAL0)
    held, fills, skips = [], [], []
    eq_curve = []
    all_days = [d for d in tdays if "2026-07-01" <= d <= "2026-08-05"]
    by_day = {d: g for d, g in univ.groupby("买入日")}
    # cache fills per (code, buy_day, rebound)
    cache: dict[tuple, dict | None] = {}

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
                Seff = min(N_FIXED, len(ranked))
                if Seff <= 0:
                    continue
                rows = [_row_dict(r) for _, r in ranked.iterrows()]
                target = equity_pre / float(Seff)

                events = []
                for row in rows:
                    key = (row["代码"], d, rebound)
                    if key not in cache:
                        cache[key] = _scan_fill(row["代码"], d, row.get("MA5"), rebound)
                    ev = cache[key]
                    if ev is None:
                        continue
                    ret = _ret_from_fill(float(row["exit_px"]), float(ev["fill_px"]))
                    if ret is None:
                        continue
                    events.append({**ev, "ret_pct": ret, "code": row["代码"], "row": row})

                events.sort(key=lambda e: (e["fill_ts"], e["code"]))
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
                            "scheme": scheme,
                            "rebound": rebound,
                            "选股日": sel_day,
                            "买入日": d,
                            "end_date": row["end_date"],
                            "代码": ev["code"],
                            "股票名称": row.get("股票名称", ""),
                            "S": S,
                            "Seff": Seff,
                            "target": target,
                            "spend": spend,
                            "fill_px": ev["fill_px"],
                            "fill_t": ev["fill_t"],
                            "break_t": ev.get("break_t", ""),
                            "post_low": ev.get("post_low"),
                            "ret_pct": ev["ret_pct"],
                            "open_px": row.get("open_px", row.get("买入成交价")),
                            "exit_px": row["exit_px"],
                            "pnl": pnl,
                            "vs_open_fill_pp": None,
                        }
                    )

        equity = cash + sum(p["cost"] for p in held) + sum(p["pnl"] for p in held)
        eq_curve.append({"date": d, "equity": equity, "scheme": scheme})

    fills_df = pd.DataFrame(fills)
    # vs open path ret if available
    if len(fills_df) and "open_px" in fills_df.columns:
        op = pd.to_numeric(fills_df["open_px"], errors="coerce")
        ex = pd.to_numeric(fills_df["exit_px"], errors="coerce")
        open_ret = (ex / op - 1.0) * 100.0
        fills_df["open_path_ret_pct"] = open_ret
        fills_df["vs_open_fill_pp"] = fills_df["ret_pct"] - open_ret

    curve = pd.DataFrame(eq_curve)
    final = float(curve["equity"].iloc[-1]) if len(curve) else CAPITAL0
    n = len(fills_df)
    return {
        "scheme": scheme,
        "rebound": rebound,
        "ret_pct": (final / CAPITAL0 - 1.0) * 100.0,
        "final": final,
        "n_fill": n,
        "n_skip_cash": len(skips),
        "mean_ret_pct": float(fills_df["ret_pct"].mean()) if n else float("nan"),
        "winrate_pct": float((fills_df["ret_pct"] > 0).mean() * 100) if n else float("nan"),
        "max_dd_pct": max_dd(curve["equity"]) if len(curve) else float("nan"),
        "mean_spend": float(fills_df["spend"].mean()) if n else float("nan"),
        "mean_fill_vs_post_low_pct": (
            float(
                (
                    (fills_df["fill_px"] / fills_df["post_low"] - 1.0) * 100.0
                ).mean()
            )
            if n and fills_df["post_low"].notna().any()
            else float("nan")
        ),
        "trade_days": int(fills_df["选股日"].nunique()) if n else 0,
        "_fills": fills_df,
        "_curve": curve,
    }


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    print("load+scan universe fields…")
    df, smap = load_universe()
    univ = enrich_universe_scans(df)

    results = []
    fills_all, curves_all = [], []
    by_r = {}
    for r in REBOUNDS:
        name = _scheme_name(r)
        print(f"sim {name}…")
        st = sim(univ, smap, r)
        by_r[r] = st
        print(
            f"  ret={st['ret_pct']:+.2f}% fills={st['n_fill']} "
            f"mean={st['mean_ret_pct']:.2f}% win={st['winrate_pct']:.1f}% "
            f"dd={st['max_dd_pct']:.2f}% days={st['trade_days']}"
        )
        if len(st["_fills"]):
            fills_all.append(st["_fills"])
        if len(st["_curve"]):
            curves_all.append(st["_curve"])

    base = by_r[0.0]["ret_pct"]
    rows = []
    for r in REBOUNDS:
        st = by_r[r]
        rows.append(
            {
                "方案": st["scheme"],
                "rebound": r,
                "组合收益pct": round(st["ret_pct"], 4),
                "vs刚破pp": round(st["ret_pct"] - base, 4),
                "成交": st["n_fill"],
                "现金跳过": st["n_skip_cash"],
                "笔均pct": round(st["mean_ret_pct"], 4) if st["n_fill"] else None,
                "胜率pct": round(st["winrate_pct"], 2) if st["n_fill"] else None,
                "回撤pct": round(st["max_dd_pct"], 4),
                "有成交选股日": st["trade_days"],
                "成交相对破后低点抬升pct": round(st["mean_fill_vs_post_low_pct"], 4)
                if st["n_fill"]
                else None,
            }
        )

    # last fill time per day
    last_rows = []
    for r in REBOUNDS:
        f = by_r[r]["_fills"]
        if not len(f):
            continue
        for buy_day, gg in f.groupby("买入日"):
            gg = gg.copy()
            gg["fill_ts"] = pd.to_numeric(gg.get("fill_ts", np.nan), errors="coerce")
            # fill_ts may not be in fills — use fill_t only
            last = gg.sort_values("fill_t").iloc[-1]
            last_rows.append(
                {
                    "方案": by_r[r]["scheme"],
                    "买入日": str(buy_day)[:10],
                    "最后买入时间": last["fill_t"],
                    "当日笔数": len(gg),
                }
            )
    last_df = pd.DataFrame(last_rows)
    piv = (
        last_df.pivot(index="买入日", columns="方案", values="最后买入时间")
        if len(last_df)
        else pd.DataFrame()
    )

    summary = pd.DataFrame(rows)
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="汇总", index=False)
        if fills_all:
            pd.concat(fills_all, ignore_index=True).to_excel(
                w, sheet_name="成交明细", index=False
            )
        if curves_all:
            pd.concat(curves_all, ignore_index=True).to_excel(
                w, sheet_name="权益曲线", index=False
            )
        if len(last_df):
            last_df.to_excel(w, sheet_name="每日最后买入", index=False)
        if len(piv):
            piv.to_excel(w, sheet_name="最后买入时间透视")

    meta = {
        "capital0": CAPITAL0,
        "fixed_n": N_FIXED,
        "rebounds": list(REBOUNDS),
        "summary": rows,
        "curves": {
            by_r[r]["scheme"]: by_r[r]["_curve"][["date", "equity"]].to_dict(
                orient="records"
            )
            for r in REBOUNDS
        },
    }
    OUT_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT_XLSX}")
    print(summary.to_string(index=False))
    if len(piv):
        print("\n最后买入时间:")
        print(piv.to_string())


if __name__ == "__main__":
    main()
