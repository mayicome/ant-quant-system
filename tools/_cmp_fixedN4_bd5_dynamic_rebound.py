# -*- coding: utf-8 -*-
"""N=4 chrono race: fixed rebound vs dynamic-threshold rebound (live best_buy formula)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

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

N_FIXED = 4
OUT_XLSX = OUT_DIR / "固定N4_跌破MA5_动态反弹对比.xlsx"
OUT_JSON = OUT_DIR / "固定N4_跌破MA5_动态反弹对比.json"


def fill_px_row(row) -> float:
    a = row["_ask"]
    try:
        if a == a and float(a) > 0:
            return float(a)
    except Exception:
        pass
    return float(row["_last"])


def scan(
    code: str,
    buy_day: str,
    ma5,
    rise_pct: float,
    dynamic: bool = False,
    rise_scale: float = 0.35,
    max_rise: float = 4.0,
):
    try:
        ma5 = float(ma5)
    except Exception:
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
    if not m.any():
        return None
    df = df.loc[m].copy()
    df["_ts"] = ts[m]
    df["_last"] = last[m]
    df["_ask"] = ask[m]
    df["_hm"] = df["_ts"].map(_hm_from_ts)
    df = df[(df["_hm"] >= 930) & (df["_hm"] <= CLOSE_HM)].reset_index(drop=True)
    if df.empty:
        return None
    prev = None
    try:
        o0 = float(df.iloc[0]["open"]) if "open" in df.columns else float("nan")
        if o0 == o0 and o0 > 0:
            prev = o0
    except Exception:
        prev = None
    broken = False
    post_low = None
    break_ts = None
    for _, row in df.iterrows():
        px = float(row["_last"])
        tsv = float(row["_ts"])
        if not broken:
            if prev is not None and prev >= ma5 and px < ma5:
                broken = True
                break_ts = tsv
                post_low = px
                if rise_pct <= 0 and not dynamic:
                    return {
                        "fill_px": fill_px_row(row),
                        "fill_t": _hhmmss(tsv),
                        "fill_ts": tsv,
                        "break_t": _hhmmss(break_ts),
                        "post_low": post_low,
                        "eff_rise": 0.0,
                    }
            prev = px
            continue
        if post_low is None or px < post_low:
            post_low = px
            continue
        if dynamic:
            drop = max(0.0, (ma5 / float(post_low) - 1.0) * 100.0)
            eff = min(max_rise, float(rise_pct) + drop * rise_scale)
        else:
            eff = float(rise_pct)
        thr = float(post_low) * (1.0 + eff / 100.0)
        if px + 1e-12 >= thr:
            return {
                "fill_px": fill_px_row(row),
                "fill_t": _hhmmss(tsv),
                "fill_ts": tsv,
                "break_t": _hhmmss(break_ts),
                "post_low": post_low,
                "eff_rise": eff,
                "thr": thr,
            }
        prev = px
    return None


def sim(univ: pd.DataFrame, name: str, rise_pct: float, dynamic: bool = False):
    cash = float(CAPITAL0)
    held = []
    fills = []
    skips = []
    eq_curve = []
    cache = {}
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
                Seff = min(N_FIXED, len(ranked))
                if Seff <= 0:
                    continue
                target = equity_pre / float(Seff)
                events = []
                for _, r in ranked.iterrows():
                    code = r["代码"]
                    key = (code, d, name)
                    if key not in cache:
                        cache[key] = scan(
                            code, d, r.get("MA5"), rise_pct, dynamic=dynamic
                        )
                    ev = cache[key]
                    if ev is None:
                        continue
                    ret = _ret_from_fill(float(r["exit_px"]), float(ev["fill_px"]))
                    if ret is None:
                        continue
                    events.append({**ev, "ret_pct": ret, "code": code, "row": r})
                events.sort(key=lambda e: (e["fill_ts"], e["code"]))
                for ev in events[:Seff]:
                    spend = min(target, cash)
                    if spend < 1 or cash < spend * 0.99:
                        skips.append(1)
                        continue
                    cash -= spend
                    pnl = spend * (ev["ret_pct"] / 100)
                    held.append(
                        {
                            "release_day": next_td(str(ev["row"]["end_date"]), 1),
                            "cost": spend,
                            "pnl": pnl,
                        }
                    )
                    fills.append(
                        {
                            "scheme": name,
                            "选股日": sel_day,
                            "买入日": d,
                            "代码": ev["code"],
                            "spend": spend,
                            "fill_px": ev["fill_px"],
                            "fill_t": ev["fill_t"],
                            "break_t": ev.get("break_t"),
                            "post_low": ev.get("post_low"),
                            "eff_rise": ev.get("eff_rise"),
                            "ret_pct": ev["ret_pct"],
                            "pnl": pnl,
                        }
                    )
        equity = cash + sum(p["cost"] for p in held) + sum(p["pnl"] for p in held)
        eq_curve.append({"date": d, "equity": equity, "scheme": name})
    fills_df = pd.DataFrame(fills)
    curve = pd.DataFrame(eq_curve)
    final = float(curve["equity"].iloc[-1]) if len(curve) else CAPITAL0
    n = len(fills_df)
    return {
        "scheme": name,
        "ret_pct": (final / CAPITAL0 - 1) * 100,
        "final": final,
        "n_fill": n,
        "n_skip": len(skips),
        "mean_ret": float(fills_df["ret_pct"].mean()) if n else float("nan"),
        "winrate": float((fills_df["ret_pct"] > 0).mean() * 100) if n else float("nan"),
        "max_dd": max_dd(curve["equity"]) if len(curve) else float("nan"),
        "mean_eff_rise": (
            float(pd.to_numeric(fills_df["eff_rise"], errors="coerce").mean())
            if n
            else float("nan")
        ),
        "_fills": fills_df,
        "_curve": curve,
    }


def fill_keys(fills: pd.DataFrame):
    if fills is None or fills.empty:
        return set()
    return set(
        zip(
            fills["选股日"].astype(str),
            fills["代码"].astype(str).map(lambda x: str(int(float(x))).zfill(6)),
        )
    )


def main():
    print("load...")
    df, _smap = load_universe()
    univ = enrich_universe_scans(df)
    schemes = [
        ("刚破即买", 0.0, False),
        ("固定反弹0.5%", 0.5, False),
        ("动态反弹 base0.3%", 0.3, True),
        ("动态反弹 base0.5%", 0.5, True),
        ("动态反弹 base0.6%", 0.6, True),
    ]
    rows = []
    fills_all = []
    curves_all = []
    by = {}
    for name, rp, dyn in schemes:
        print("sim", name)
        st = sim(univ, name, rp, dynamic=dyn)
        by[name] = st
        print(
            f"  ret={st['ret_pct']:+.2f}% fills={st['n_fill']} "
            f"mean={st['mean_ret']:.2f}% win={st['winrate']:.1f}% "
            f"dd={st['max_dd']:.2f}% mean_eff_rise={st['mean_eff_rise']:.3f}%"
        )
        rows.append({k: st[k] for k in st if not k.startswith("_")})
        if len(st["_fills"]):
            fills_all.append(st["_fills"])
        if len(st["_curve"]):
            curves_all.append(st["_curve"])

    base = by["刚破即买"]["ret_pct"]
    summary = pd.DataFrame(
        [
            {
                "方案": r["scheme"],
                "组合收益pct": round(r["ret_pct"], 4),
                "vs刚破pp": round(r["ret_pct"] - base, 4),
                "成交": r["n_fill"],
                "笔均": round(r["mean_ret"], 4),
                "胜率": round(r["winrate"], 2),
                "回撤": round(r["max_dd"], 4),
                "成交时均有效反弹%": (
                    round(r["mean_eff_rise"], 4)
                    if r["mean_eff_rise"] == r["mean_eff_rise"]
                    else None
                ),
            }
            for r in rows
        ]
    )
    k_fix = fill_keys(by["固定反弹0.5%"]["_fills"])
    ov = []
    for name, _rp, _dyn in schemes:
        k = fill_keys(by[name]["_fills"])
        ov.append(
            {
                "方案": name,
                "成交": len(k),
                "与固定0.5同票": len(k & k_fix),
                "换票相对固定0.5": len(k - k_fix),
            }
        )
    print(summary.to_string(index=False))
    print(pd.DataFrame(ov).to_string(index=False))
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="汇总", index=False)
        pd.DataFrame(ov).to_excel(w, sheet_name="与固定0.5重叠", index=False)
        if fills_all:
            pd.concat(fills_all, ignore_index=True).to_excel(
                w, sheet_name="成交明细", index=False
            )
        if curves_all:
            pd.concat(curves_all, ignore_index=True).to_excel(
                w, sheet_name="权益曲线", index=False
            )
    OUT_JSON.write_text(
        json.dumps(
            {"summary": summary.to_dict("records"), "overlap": ov},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("wrote", OUT_XLSX)


if __name__ == "__main__":
    main()
