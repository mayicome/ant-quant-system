# -*- coding: utf-8 -*-
"""Compare capital utilization for fixed-N=5: baseline vs realloc vs backup.

Buy modes: 开盘买入 / 跌破昨MA5
Schemes:
  1) baseline   — top min(5,pool), target=equity/Seff, no reallocation
  2) realloc    — among top Seff, only triggered names share equity/n_elig
  3) backup     — walk strength list until Seff fills or pool ends; target=equity/Seff
  4) backup_realloc — walk until up to Seff triggered fills; target=equity/n_fill

Usage:
  set PYTHONIOENCODING=utf-8
  python tools/_cmp_fixedN5_capital_util.py
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
    N_FIXED,
    N_SEEDS,
    OUT_DIR,
    _rand_stats,
    _ret_from_fill,
    enrich_universe_scans,
    load_universe,
    max_dd,
    next_td,
    rank_random,
    rank_strength,
    resolve_fill,
    tdays,
)

OUT_XLSX = OUT_DIR / "固定N5_资金利用对比.xlsx"
OUT_JSON = OUT_DIR / "固定N5_资金利用对比.json"

SCHEMES = ("baseline", "realloc", "backup", "backup_realloc")
BUY_MODES = (MODE_OPEN, MODE_BD5)
# random sensitivity focus: baseline vs backup
RAND_SCHEMES = ("baseline", "backup")
RAND_BUY_MODES = (MODE_OPEN, MODE_BD5)


def _prepare_row(r: pd.Series) -> dict:
    return {
        "选股日": r["选股日"],
        "买入日": r["买入日"],
        "end_date": r["end_date"],
        "代码": r["代码"],
        "股票名称": r.get("股票名称", ""),
        "MA5": r.get("MA5"),
        "MA10": r.get("MA10"),
        "open_px": r.get("open_px", r.get("买入成交价")),
        "open_ret_pct": r.get("open_ret_pct", r.get("收益率pct")),
        "exit_px": r.get("exit_px"),
        "tick_ok": r.get("tick_ok"),
        "had_brk10": r.get("had_brk10"),
        "had_bd5": r.get("had_bd5"),
        "brk10_px": r.get("brk10_px"),
        "brk10_t": r.get("brk10_t"),
        "brk10_ts": r.get("brk10_ts"),
        "bd5_px": r.get("bd5_px"),
        "bd5_t": r.get("bd5_t"),
        "bd5_ts": r.get("bd5_ts"),
        "close_px": r.get("close_px"),
        "close_t": r.get("close_t"),
        "夹档内收盘": r.get("夹档内收盘"),
        "合格榜内序位": r.get("合格榜内序位"),
        "合格榜标签内RS排名": r.get("合格榜标签内RS排名"),
        "t": r.get("t", ""),
    }


def _can_fill(row: dict, buy_mode: str):
    fill_px, fill_t, trig = resolve_fill(pd.Series(row), buy_mode)
    if fill_px is None:
        return None
    ret = _ret_from_fill(float(row["exit_px"]), fill_px)
    if ret is None:
        return None
    return {
        "fill_px": float(fill_px),
        "fill_t": fill_t,
        "trig": trig,
        "ret_pct": float(ret),
    }


def _select_fills(ranked_rows: list[dict], buy_mode: str, scheme: str, Seff: int):
    """Return list of (row, fill_info) to attempt, plus sizing_n for target=equity/sizing_n."""
    reserved = ranked_rows[:Seff]
    if scheme == "baseline":
        out = []
        for row in reserved:
            info = _can_fill(row, buy_mode)
            if info is not None:
                out.append((row, info))
        return out, Seff  # dead slots keep diluting? baseline keeps target=equity/Seff
        # actually baseline still uses Seff for target even if fewer fill — yes

    if scheme == "realloc":
        out = []
        for row in reserved:
            info = _can_fill(row, buy_mode)
            if info is not None:
                out.append((row, info))
        n = len(out)
        return out, (n if n > 0 else Seff)

    if scheme == "backup":
        out = []
        for row in ranked_rows:
            if len(out) >= Seff:
                break
            info = _can_fill(row, buy_mode)
            if info is not None:
                out.append((row, info))
        return out, Seff

    if scheme == "backup_realloc":
        out = []
        for row in ranked_rows:
            if len(out) >= Seff:
                break
            info = _can_fill(row, buy_mode)
            if info is not None:
                out.append((row, info))
        n = len(out)
        return out, (n if n > 0 else Seff)

    raise ValueError(scheme)


def sim_scheme(
    univ: pd.DataFrame,
    smap: dict,
    buy_mode: str,
    scheme: str,
    *,
    rank_mode: str = "strength",
    seed: int | None = None,
) -> dict:
    cash = float(CAPITAL0)
    held, fills, skips = [], [], []
    eq_curve = []
    all_days = [d for d in tdays if "2026-07-01" <= d <= "2026-08-05"]
    by_day = {d: g for d, g in univ.groupby("买入日")}
    rng = np.random.default_rng(seed) if seed is not None else None

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
                if rank_mode == "random":
                    assert rng is not None
                    ranked = rank_random(g, rng)
                else:
                    ranked = rank_strength(g)
                if ranked.empty:
                    continue
                S = int(smap.get(sel_day, 0))
                Seff = min(N_FIXED, len(ranked))
                if Seff <= 0:
                    continue
                rows = [_prepare_row(r) for _, r in ranked.iterrows()]
                chosen, sizing_n = _select_fills(rows, buy_mode, scheme, Seff)
                if sizing_n <= 0:
                    continue
                target = equity_pre / float(sizing_n)

                # mark reserved-but-not-chosen as skip for baseline/realloc clarity
                chosen_codes = {r["代码"] for r, _ in chosen}
                if scheme in ("baseline", "realloc"):
                    for row in rows[:Seff]:
                        if row["代码"] not in chosen_codes:
                            skips.append(
                                {
                                    "buy_mode": buy_mode,
                                    "scheme": scheme,
                                    "选股日": sel_day,
                                    "买入日": d,
                                    "代码": row["代码"],
                                    "skip_reason": "未触发/不可成交",
                                }
                            )

                for row, info in chosen:
                    spend = min(target, cash)
                    if spend < 1 or cash < spend * 0.99:
                        skips.append(
                            {
                                "buy_mode": buy_mode,
                                "scheme": scheme,
                                "选股日": sel_day,
                                "买入日": d,
                                "代码": row["代码"],
                                "skip_reason": "现金不足",
                            }
                        )
                        continue
                    cash -= spend
                    pnl = spend * (info["ret_pct"] / 100.0)
                    held.append(
                        {
                            "release_day": next_td(str(row["end_date"]), 1),
                            "cost": spend,
                            "pnl": pnl,
                        }
                    )
                    fills.append(
                        {
                            "buy_mode": buy_mode,
                            "scheme": scheme,
                            "触发": info["trig"],
                            "选股日": sel_day,
                            "买入日": d,
                            "end_date": row["end_date"],
                            "代码": row["代码"],
                            "股票名称": row.get("股票名称", ""),
                            "S": S,
                            "Seff": Seff,
                            "sizing_n": sizing_n,
                            "target": target,
                            "spend": spend,
                            "fill_px": info["fill_px"],
                            "fill_t": info["fill_t"],
                            "ret_pct": info["ret_pct"],
                            "open_ret_pct": row["open_ret_pct"],
                            "pnl": pnl,
                        }
                    )

        equity = cash + sum(p["cost"] for p in held) + sum(p["pnl"] for p in held)
        eq_curve.append(
            {"date": d, "equity": equity, "buy_mode": buy_mode, "scheme": scheme}
        )

    fills_df = pd.DataFrame(fills)
    curve = pd.DataFrame(eq_curve)
    final = float(curve["equity"].iloc[-1]) if len(curve) else CAPITAL0
    n = len(fills_df)
    avg_util = float("nan")
    if n and len(curve):
        # rough: mean daily spend / equity_pre proxy via spend sum / capital days — skip
        avg_util = float(fills_df["spend"].sum() / max(fills_df["target"].sum(), 1e-9))
    return {
        "buy_mode": buy_mode,
        "scheme": scheme,
        "ret_pct": (final / CAPITAL0 - 1.0) * 100.0,
        "final": final,
        "n_fill": n,
        "n_skip": len(skips),
        "mean_ret_pct": float(fills_df["ret_pct"].mean()) if n else float("nan"),
        "winrate_pct": float((fills_df["ret_pct"] > 0).mean() * 100) if n else float("nan"),
        "max_dd_pct": max_dd(curve["equity"]) if len(curve) else float("nan"),
        "mean_spend": float(fills_df["spend"].mean()) if n else float("nan"),
        "mean_sizing_n": float(fills_df["sizing_n"].mean()) if n else float("nan"),
        "spend_sum": float(fills_df["spend"].sum()) if n else 0.0,
        "target_hit_ratio": avg_util,
        "_fills": fills_df,
        "_curve": curve,
        "_skips": pd.DataFrame(skips),
    }


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    print("load+scan…")
    df, smap = load_universe()
    univ = enrich_universe_scans(df)
    print(f"universe={len(univ)}")

    results = []
    fills_all = []
    curves_all = []
    for buy_mode in BUY_MODES:
        for scheme in SCHEMES:
            print(f"sim {buy_mode} / {scheme}…")
            st = sim_scheme(univ, smap, buy_mode, scheme)
            results.append({k: v for k, v in st.items() if not k.startswith("_")})
            print(
                f"  ret={st['ret_pct']:+.2f}% fills={st['n_fill']} skip={st['n_skip']} "
                f"mean={st['mean_ret_pct']:.2f}% dd={st['max_dd_pct']:.2f}% "
                f"mean_spend={st['mean_spend']:.0f} sizing_n={st['mean_sizing_n']:.2f}"
            )
            if len(st["_fills"]):
                fills_all.append(st["_fills"])
            if len(st["_curve"]):
                curves_all.append(st["_curve"])

    summary = pd.DataFrame(results)
    # deltas vs baseline within buy_mode
    rows = []
    for buy_mode in BUY_MODES:
        base = summary[
            (summary["buy_mode"] == buy_mode) & (summary["scheme"] == "baseline")
        ].iloc[0]
        for _, r in summary[summary["buy_mode"] == buy_mode].iterrows():
            rows.append(
                {
                    **r.to_dict(),
                    "vs_baseline_pp": float(r["ret_pct"] - base["ret_pct"]),
                    "fills_delta": int(r["n_fill"] - base["n_fill"]),
                }
            )
    summary2 = pd.DataFrame(rows)

    # --- random: baseline vs backup ---
    print(f"random sensitivity seeds={N_SEEDS} (baseline vs backup)…")
    rand_rets: dict[tuple[str, str], list[float]] = {
        (bm, sch): [] for bm in RAND_BUY_MODES for sch in RAND_SCHEMES
    }
    rand_dds: dict[tuple[str, str], list[float]] = {
        (bm, sch): [] for bm in RAND_BUY_MODES for sch in RAND_SCHEMES
    }
    seed_rows = []
    for s in range(N_SEEDS):
        row = {"seed": s}
        for buy_mode in RAND_BUY_MODES:
            day = {}
            for scheme in RAND_SCHEMES:
                st = sim_scheme(
                    univ,
                    smap,
                    buy_mode,
                    scheme,
                    rank_mode="random",
                    seed=s,
                )
                key = (buy_mode, scheme)
                rand_rets[key].append(st["ret_pct"])
                rand_dds[key].append(st["max_dd_pct"])
                day[scheme] = st["ret_pct"]
                row[f"{buy_mode}|{scheme}"] = st["ret_pct"]
            row[f"{buy_mode}|backup-baseline_pp"] = day["backup"] - day["baseline"]
        seed_rows.append(row)
        if (s + 1) % 20 == 0:
            print(f"  seed {s + 1}/{N_SEEDS}")

    rand_summary_rows = []
    for buy_mode in RAND_BUY_MODES:
        for scheme in RAND_SCHEMES:
            rs = _rand_stats(rand_rets[(buy_mode, scheme)])
            ds = _rand_stats(rand_dds[(buy_mode, scheme)])
            strength = float(
                summary2[
                    (summary2["buy_mode"] == buy_mode)
                    & (summary2["scheme"] == scheme)
                ]["ret_pct"].iloc[0]
            )
            rand_summary_rows.append(
                {
                    "buy_mode": buy_mode,
                    "scheme": scheme,
                    "强度序收益pct": round(strength, 4),
                    "随机均值pct": round(rs["mean"], 4),
                    "随机p10pct": round(rs["p10"], 4),
                    "随机p50pct": round(rs["p50"], 4),
                    "随机p90pct": round(rs["p90"], 4),
                    "随机>0占比pct": round(rs["gt0_pct"], 2),
                    "随机回撤均值pct": round(ds["mean"], 4),
                    "随机回撤p10pct": round(ds["p10"], 4),
                }
            )
            print(
                f"  random {buy_mode}/{scheme}: mean={rs['mean']:+.2f}% "
                f"p10={rs['p10']:+.2f}% >0={rs['gt0_pct']:.0f}% | strength={strength:+.2f}%"
            )

    delta_rows = []
    for buy_mode in RAND_BUY_MODES:
        deltas = [
            rand_rets[(buy_mode, "backup")][i] - rand_rets[(buy_mode, "baseline")][i]
            for i in range(N_SEEDS)
        ]
        ds = _rand_stats(deltas)
        beat = float((np.asarray(deltas) > 0).mean() * 100)
        delta_rows.append(
            {
                "buy_mode": buy_mode,
                "backup_minus_baseline_mean_pp": round(ds["mean"], 4),
                "backup_minus_baseline_p10_pp": round(ds["p10"], 4),
                "backup_minus_baseline_p50_pp": round(ds["p50"], 4),
                "backup_beats_baseline_pct": round(beat, 2),
            }
        )
        print(
            f"  {buy_mode} backup-baseline: mean={ds['mean']:+.2f}pp "
            f"p10={ds['p10']:+.2f}pp backup更优占比={beat:.0f}%"
        )

    rand_summary = pd.DataFrame(rand_summary_rows)
    delta_df = pd.DataFrame(delta_rows)
    seed_df = pd.DataFrame(seed_rows)

    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        summary2.to_excel(w, sheet_name="汇总", index=False)
        rand_summary.to_excel(w, sheet_name="随机敏感性汇总", index=False)
        delta_df.to_excel(w, sheet_name="随机候补减基线", index=False)
        seed_df.to_excel(w, sheet_name="随机各seed", index=False)
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
        "fixed_n": N_FIXED,
        "n_seeds": N_SEEDS,
        "schemes": list(SCHEMES),
        "summary": summary2.to_dict(orient="records"),
        "random_summary": rand_summary_rows,
        "random_backup_minus_baseline": delta_rows,
        "curves": {},
    }
    if curves_all:
        call = pd.concat(curves_all, ignore_index=True)
        for (bm, sch), g in call.groupby(["buy_mode", "scheme"]):
            meta["curves"][f"{bm}|{sch}"] = g[["date", "equity"]].to_dict(
                orient="records"
            )

    OUT_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT_XLSX}")
    print(summary2.to_string(index=False))
    print(rand_summary.to_string(index=False))
    print(delta_df.to_string(index=False))


if __name__ == "__main__":
    main()
