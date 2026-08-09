# -*- coding: utf-8 -*-
"""Stability compare: fixed-N full-equity vs clip(2,4) full-equity (旧空头 Cond123).

For each scheme:
  - baseline strength rank (Elig×8 + 标签内RS)
  - drop 选股日 2026-07-01
  - random shuffle among当日开盘夹档资格 × N_SEEDS

Does NOT change production defaults.

Usage:
  set PYTHONIOENCODING=utf-8
  python tools/_cmp_fixedN_vs_clip24_stability.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import akshare as ak
import numpy as np
import pandas as pd

_ROOT_PKG = Path(__file__).resolve().parents[1]
if str(_ROOT_PKG) not in sys.path:
    sys.path.insert(0, str(_ROOT_PKG))

from sector_stock_filter import EXPORT_ELIG_WEIGHT, clip_strength_sort_key  # noqa: E402

ROOT = Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门")
OUT_DIR = ROOT / "并集拆空头_对比"
OUT_XLSX = OUT_DIR / "固定N_vs_clip24_稳度对比.xlsx"
OUT_JSON = OUT_DIR / "固定N_vs_clip24_稳度对比.json"

CAPITAL0 = 100_000.0
W = int(EXPORT_ELIG_WEIGHT)
N_SEEDS = 100
SCHEMES = [
    {"name": "clip(2,4)", "kind": "clip", "L": 2, "U": 4},
    {"name": "固定N=2", "kind": "fixed", "N": 2},
    {"name": "固定N=3", "kind": "fixed", "N": 3},
    {"name": "固定N=4", "kind": "fixed", "N": 4},
    {"name": "固定N=5", "kind": "fixed", "N": 5},
    {"name": "固定N=6", "kind": "fixed", "N": 6},
    {"name": "固定N=7", "kind": "fixed", "N": 7},
    {"name": "固定N=8", "kind": "fixed", "N": 8},
]

SEL = ROOT / (
    "选股结果_东财热门-besttest-无涨停均线差0.5to2-MA空头排列_2026-07-01_2026-07-31.xls"
)
TRADES = ROOT / (
    "besttest_开盘夹档_条件一_无涨停_均线差0.5to2_"
    "条件二开盘相对MA5满足0to2_条件三MA5lt10lt20_各日选股收益汇总.xlsx"
)


def code6(v) -> str:
    if pd.isna(v):
        return ""
    try:
        return str(int(float(v))).zfill(6)
    except Exception:
        return str(v).strip().zfill(6)[-6:]


cal = ak.tool_trade_date_hist_sina()
cal["trade_date"] = pd.to_datetime(cal["trade_date"]).dt.strftime("%Y-%m-%d")
tdays = cal[(cal["trade_date"] >= "2026-07-01") & (cal["trade_date"] <= "2026-08-10")][
    "trade_date"
].tolist()
idx = {d: i for i, d in enumerate(tdays)}


def next_td(d: str, n: int = 1) -> str:
    return tdays[idx[d] + n]


def max_dd(equity: pd.Series) -> float:
    if equity.empty:
        return float("nan")
    peak = equity.cummax()
    return float((equity / peak - 1.0).min() * 100.0)


def load_universe():
    sel = pd.read_excel(SEL)
    sel["选股日"] = pd.to_datetime(sel["选股日"]).dt.strftime("%Y-%m-%d")
    sel["股票代码"] = sel["股票代码"].map(code6)
    sel = sel[sel["选股日"].str.startswith("2026-07")].copy()
    smap = sel.groupby("选股日").size().to_dict()
    strength = {
        (r["选股日"], r["股票代码"]): (r.get("合格榜内序位"), r.get("合格榜标签内RS排名"))
        for _, r in sel.iterrows()
    }
    order = {k: i for i, k in enumerate(zip(sel["选股日"], sel["股票代码"]))}
    # proper pool order per day
    sel = sel.copy()
    sel["池序"] = sel.groupby("选股日").cumcount()
    order = sel.set_index(["选股日", "股票代码"])["池序"].to_dict()

    tr = pd.read_excel(TRADES)
    tr["代码"] = tr["代码"].map(code6)
    tr["选股日"] = pd.to_datetime(tr["选股日"]).dt.strftime("%Y-%m-%d")
    tr["买入日"] = pd.to_datetime(tr["买入日"]).dt.strftime("%Y-%m-%d")
    tr["end_date"] = pd.to_datetime(tr["end_date"]).dt.strftime("%Y-%m-%d")
    tr["收益率pct"] = pd.to_numeric(tr["收益率pct"], errors="coerce")
    tip = tr["触发信息"].astype(str) if "触发信息" in tr.columns else pd.Series([""] * len(tr))
    tr["分支"] = np.where(
        tip.str.contains("开盘买入"),
        "开盘夹档",
        np.where(tip.str.contains("单点买入"), "高开回踩", "其他"),
    )
    tr["t"] = tr["买入时间"].astype(str) if "买入时间" in tr.columns else ""
    tr["池序"] = tr.apply(lambda r: order.get((r["选股日"], r["代码"]), 9999), axis=1)
    tr["in_pool"] = tr["池序"] < 9999
    if "合格榜内序位" not in tr.columns:
        tr["合格榜内序位"] = np.nan
    if "合格榜标签内RS排名" not in tr.columns:
        tr["合格榜标签内RS排名"] = np.nan
    for i, r in tr.iterrows():
        key = (r["选股日"], r["代码"])
        if key not in strength:
            continue
        elig, rs = strength[key]
        if pd.isna(r.get("合格榜内序位")):
            tr.at[i, "合格榜内序位"] = elig
        if pd.isna(r.get("合格榜标签内RS排名")):
            tr.at[i, "合格榜标签内RS排名"] = rs
    df = tr[tr["in_pool"]].copy()
    return df, smap


def seff_for(scheme: dict, S: int, n_avail: int) -> int:
    if scheme["kind"] == "clip":
        return int(max(scheme["L"], min(scheme["U"], S)))
    # fixed N: ignore S variability; cap by available fills
    return int(min(scheme["N"], n_avail))


def rank_strength(g: pd.DataFrame) -> pd.DataFrame:
    opens = g[g["分支"] == "开盘夹档"].copy()
    if opens.empty:
        return opens
    opens["_sk"] = opens.apply(
        lambda r: clip_strength_sort_key(
            r.get("合格榜内序位"),
            r.get("合格榜标签内RS排名"),
            r.get("代码"),
            elig_weight=W,
        ),
        axis=1,
    )
    return opens.sort_values(["_sk", "t", "代码"]).drop(columns=["_sk"])


def rank_random(g: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    opens = g[g["分支"] == "开盘夹档"].copy()
    if opens.empty:
        return opens
    return opens.iloc[rng.permutation(len(opens))].reset_index(drop=True)


def sim(
    df: pd.DataFrame,
    smap: dict,
    scheme: dict,
    *,
    mode: str = "strength",
    seed: int | None = None,
    min_sel_day: str = "2026-07-01",
) -> dict:
    cash = float(CAPITAL0)
    held, fills, skips = [], [], []
    eq_curve = []
    all_days = [d for d in tdays if "2026-07-01" <= d <= "2026-08-05"]
    by_day = {d: g for d, g in df.groupby("买入日")}
    rng = np.random.default_rng(seed) if seed is not None else None

    for d in all_days:
        still = []
        for p in held:
            if p["release_day"] == d:
                cash += p["cost"] + p["pnl"]
            else:
                still.append(p)
        held = still

        locked_cost0 = sum(p["cost"] for p in held)
        equity_pre = cash + locked_cost0
        day_budget = equity_pre  # 全仓
        day_budget_left = day_budget

        if d in by_day:
            for sel_day, g in by_day[d].groupby("选股日", sort=False):
                if sel_day < min_sel_day:
                    continue
                S = int(smap.get(sel_day, 0))
                if S <= 0:
                    continue
                if mode == "random":
                    assert rng is not None
                    opens = rank_random(g, rng)
                else:
                    opens = rank_strength(g)
                if opens.empty:
                    continue
                Seff = seff_for(scheme, S, len(opens))
                if Seff <= 0:
                    continue
                g2 = opens.head(Seff).reset_index(drop=True)
                target = day_budget / float(Seff)
                for _, r in g2.iterrows():
                    spend = min(target, cash, day_budget_left)
                    if spend < 1 or cash < spend * 0.99:
                        skips.append(1)
                        continue
                    cash -= spend
                    day_budget_left -= spend
                    ret = float(r["收益率pct"]) / 100.0
                    pnl = spend * ret
                    held.append(
                        {
                            "release_day": next_td(str(r["end_date"]), 1),
                            "cost": spend,
                            "pnl": pnl,
                        }
                    )
                    fills.append(
                        {
                            "scheme": scheme["name"],
                            "mode": mode,
                            "选股日": sel_day,
                            "买入日": d,
                            "代码": r["代码"],
                            "S": S,
                            "Seff": Seff,
                            "spend": spend,
                            "ret_pct": float(r["收益率pct"]),
                            "pnl": pnl,
                        }
                    )

        locked_cost = sum(p["cost"] for p in held)
        locked_pnl = sum(p["pnl"] for p in held)
        equity = cash + locked_cost + locked_pnl
        eq_curve.append({"date": d, "equity": equity})

    fills_df = pd.DataFrame(fills)
    curve = pd.DataFrame(eq_curve)
    final = float(curve["equity"].iloc[-1]) if len(curve) else CAPITAL0
    ret_pct = (final / CAPITAL0 - 1.0) * 100.0
    n = len(fills_df)
    return {
        "scheme": scheme["name"],
        "mode": mode,
        "min_sel_day": min_sel_day,
        "final": final,
        "ret_pct": ret_pct,
        "n_fill": n,
        "n_skip": len(skips),
        "mean_ret_pct": float(fills_df["ret_pct"].mean()) if n else float("nan"),
        "winrate_pct": float((fills_df["ret_pct"] > 0).mean() * 100) if n else float("nan"),
        "max_dd_pct": max_dd(curve["equity"]) if len(curve) else float("nan"),
        "mean_spend": float(fills_df["spend"].mean()) if n else float("nan"),
        "trade_days": int(fills_df["选股日"].nunique()) if n else 0,
        "_fills": fills_df,
        "_curve": curve,
    }


def accept_random(stats: dict) -> dict:
    """Heuristic: still acceptable if mean>0 and p10 not disastrous."""
    mean = stats["ret_mean"]
    p10 = stats["ret_p10"]
    ok = (mean > 0) and (p10 > -3.0)
    note = []
    if mean <= 0:
        note.append("随机均值≤0")
    if p10 <= -3.0:
        note.append("p10≤-3%")
    if not note:
        note.append("可接受(均值>0且p10>-3%)")
    return {"acceptable": ok, "note": ";".join(note)}


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    df, smap = load_universe()
    print(
        f"universe in_pool={len(df)} S-days={len(smap)} w={W} seeds={N_SEEDS} capital={CAPITAL0:.0f}"
    )

    summary_rows = []
    random_rows = []
    curves = {}

    for scheme in SCHEMES:
        name = scheme["name"]
        print(f"\n===== {name} =====")
        base = sim(df, smap, scheme, mode="strength", min_sel_day="2026-07-01")
        drop = sim(df, smap, scheme, mode="strength", min_sel_day="2026-07-02")
        print(
            f"  strength 含7/1: {base['ret_pct']:+.2f}% fills={base['n_fill']} "
            f"dd={base['max_dd_pct']:.2f}% skip={base['n_skip']}"
        )
        print(
            f"  strength 自7/2: {drop['ret_pct']:+.2f}% fills={drop['n_fill']} "
            f"dd={drop['max_dd_pct']:.2f}% skip={drop['n_skip']} "
            f"Δ={drop['ret_pct'] - base['ret_pct']:+.2f}pp"
        )

        rets = []
        for s in range(N_SEEDS):
            rr = sim(df, smap, scheme, mode="random", seed=s, min_sel_day="2026-07-01")
            rets.append(rr["ret_pct"])
            random_rows.append(
                {
                    "scheme": name,
                    "seed": s,
                    "ret_pct": rr["ret_pct"],
                    "n_fill": rr["n_fill"],
                    "max_dd_pct": rr["max_dd_pct"],
                }
            )
        arr = np.asarray(rets, dtype=float)
        rand_stats = {
            "ret_mean": float(arr.mean()),
            "ret_std": float(arr.std(ddof=1)),
            "ret_p10": float(np.quantile(arr, 0.10)),
            "ret_p50": float(np.quantile(arr, 0.50)),
            "ret_p90": float(np.quantile(arr, 0.90)),
            "ret_min": float(arr.min()),
            "ret_max": float(arr.max()),
            "frac_pos": float((arr > 0).mean()),
            "n_seeds": N_SEEDS,
        }
        acc = accept_random(rand_stats)
        print(
            f"  random×{N_SEEDS}: mean={rand_stats['ret_mean']:+.2f}% "
            f"std={rand_stats['ret_std']:.2f} p10={rand_stats['ret_p10']:+.2f}% "
            f"p90={rand_stats['ret_p90']:+.2f}% pos={rand_stats['frac_pos']*100:.0f}% "
            f"| {acc['note']}"
        )

        # random from 7/2 as extra robustness
        rets2 = []
        for s in range(N_SEEDS):
            rr = sim(df, smap, scheme, mode="random", seed=s, min_sel_day="2026-07-02")
            rets2.append(rr["ret_pct"])
        arr2 = np.asarray(rets2, dtype=float)
        rand2 = {
            "ret_mean": float(arr2.mean()),
            "ret_std": float(arr2.std(ddof=1)),
            "ret_p10": float(np.quantile(arr2, 0.10)),
            "ret_p50": float(np.quantile(arr2, 0.50)),
            "ret_p90": float(np.quantile(arr2, 0.90)),
            "frac_pos": float((arr2 > 0).mean()),
        }
        acc2 = accept_random(rand2)
        print(
            f"  random自7/2×{N_SEEDS}: mean={rand2['ret_mean']:+.2f}% "
            f"p10={rand2['ret_p10']:+.2f}% pos={rand2['frac_pos']*100:.0f}% | {acc2['note']}"
        )

        row = {
            "scheme": name,
            "strength_ret_含7/1": round(base["ret_pct"], 4),
            "strength_fills_含7/1": base["n_fill"],
            "strength_dd_含7/1": round(base["max_dd_pct"], 4),
            "strength_skip_含7/1": base["n_skip"],
            "strength_ret_自7/2": round(drop["ret_pct"], 4),
            "strength_fills_自7/2": drop["n_fill"],
            "strength_dd_自7/2": round(drop["max_dd_pct"], 4),
            "Δ_去7/1_pp": round(drop["ret_pct"] - base["ret_pct"], 4),
            "random_mean": round(rand_stats["ret_mean"], 4),
            "random_std": round(rand_stats["ret_std"], 4),
            "random_p10": round(rand_stats["ret_p10"], 4),
            "random_p50": round(rand_stats["ret_p50"], 4),
            "random_p90": round(rand_stats["ret_p90"], 4),
            "random_frac_pos": round(rand_stats["frac_pos"], 4),
            "random_acceptable": acc["acceptable"],
            "random_note": acc["note"],
            "random自7/2_mean": round(rand2["ret_mean"], 4),
            "random自7/2_p10": round(rand2["ret_p10"], 4),
            "random自7/2_frac_pos": round(rand2["frac_pos"], 4),
            "random自7/2_acceptable": acc2["acceptable"],
            "random自7/2_note": acc2["note"],
        }
        summary_rows.append(row)
        curves[name] = {
            "含7/1": base["_curve"].to_dict(orient="records"),
            "自7/2": drop["_curve"].to_dict(orient="records"),
        }

    summary = pd.DataFrame(summary_rows)
    rand_df = pd.DataFrame(random_rows)

    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="汇总", index=False)
        rand_df.to_excel(w, sheet_name="随机seeds_含7月1", index=False)
        # distribution by scheme
        dist = (
            rand_df.groupby("scheme")["ret_pct"]
            .agg(["count", "mean", "std", "min", "max"])
            .reset_index()
        )
        dist.to_excel(w, sheet_name="随机分布", index=False)

    meta = {
        "capital0": CAPITAL0,
        "w": W,
        "n_seeds": N_SEEDS,
        "accept_rule": "mean>0 and p10>-3%",
        "pool": "旧空头Elig30 + Cond123开盘夹档",
        "budget": "全仓 day_budget=equity_pre",
        "summary": summary_rows,
        "curves": curves,
    }
    OUT_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nwrote", OUT_XLSX)
    print("wrote", OUT_JSON)
    print("\n===== SUMMARY =====")
    print(
        summary[
            [
                "scheme",
                "strength_ret_含7/1",
                "strength_ret_自7/2",
                "Δ_去7/1_pp",
                "random_mean",
                "random_p10",
                "random_frac_pos",
                "random_acceptable",
                "random自7/2_mean",
                "random自7/2_p10",
                "random自7/2_acceptable",
            ]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    main()
