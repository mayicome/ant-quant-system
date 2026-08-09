# -*- coding: utf-8 -*-
"""Fixed-N=5 跌破昨MA5：无未来函数的时间序候补 vs 基线。

基线 baseline：
  盘前按强度序锁定 top Seff；仅这些票在各自跌破时买入；不补位。

候补 backup_chrono（无未来函数）：
  当日开盘夹档全池都可买；按跌破时刻先后成交；买满 Seff 为止。
  （不再先扫完整日再决定跳过谁——那是未来函数。）

对照 lookahead_backup：旧逻辑（先看全日是否会破再按序取）仅作偏差对照。

Usage:
  set PYTHONIOENCODING=utf-8
  python tools/_cmp_fixedN5_backup_chrono.py
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

OUT_XLSX = OUT_DIR / "固定N5_候补时间序对比.xlsx"
OUT_JSON = OUT_DIR / "固定N5_候补时间序对比.json"


def _row_dict(r: pd.Series) -> dict:
    return {c: r.get(c) for c in r.index}


def _bd5_event(row: dict):
    info = resolve_fill(pd.Series(row), MODE_BD5)
    fill_px, fill_t, trig = info
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


def sim(
    univ: pd.DataFrame,
    smap: dict,
    scheme: str,
    *,
    rank_mode: str = "strength",
    seed: int | None = None,
) -> dict:
    """scheme: baseline | backup_chrono | lookahead_backup"""
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
                rows = [_row_dict(r) for _, r in ranked.iterrows()]
                target = equity_pre / float(Seff)

                if scheme == "baseline":
                    # only reserved top Seff; each buys if/when it breaks (order by their break time among reserved)
                    events = []
                    for row in rows[:Seff]:
                        ev = _bd5_event(row)
                        if ev is None:
                            skips.append(
                                {
                                    "scheme": scheme,
                                    "选股日": sel_day,
                                    "买入日": d,
                                    "代码": row["代码"],
                                    "skip_reason": "未跌破/不可成交",
                                }
                            )
                        else:
                            events.append(ev)
                    events.sort(key=lambda e: (e["ts"], e["code"]))
                    chosen = events  # may be < Seff; no substitute

                elif scheme == "backup_chrono":
                    # full pool race by break time — no look-ahead
                    events = []
                    for row in rows:
                        ev = _bd5_event(row)
                        if ev is not None:
                            events.append(ev)
                    events.sort(key=lambda e: (e["ts"], e["code"]))
                    chosen = events[:Seff]
                    # reserved top that never made the chrono cut → informational skip
                    chosen_codes = {e["code"] for e in chosen}
                    for row in rows[:Seff]:
                        if row["代码"] not in chosen_codes:
                            skips.append(
                                {
                                    "scheme": scheme,
                                    "选股日": sel_day,
                                    "买入日": d,
                                    "代码": row["代码"],
                                    "skip_reason": "未进时间序名额(破得晚/未破)",
                                }
                            )

                elif scheme == "lookahead_backup":
                    # OLD biased: walk rank, take who eventually breaks (ignores time competition)
                    chosen = []
                    for row in rows:
                        if len(chosen) >= Seff:
                            break
                        ev = _bd5_event(row)
                        if ev is not None:
                            chosen.append(ev)
                    chosen_codes = {e["code"] for e in chosen}
                    for row in rows[:Seff]:
                        if row["代码"] not in chosen_codes:
                            skips.append(
                                {
                                    "scheme": scheme,
                                    "选股日": sel_day,
                                    "买入日": d,
                                    "代码": row["代码"],
                                    "skip_reason": "lookahead未纳入",
                                }
                            )
                else:
                    raise ValueError(scheme)

                for ev in chosen:
                    spend = min(target, cash)
                    if spend < 1 or cash < spend * 0.99:
                        skips.append(
                            {
                                "scheme": scheme,
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
                            "scheme": scheme,
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
                            "bd5_ts": ev["ts"],
                            "ret_pct": ev["ret_pct"],
                            "open_ret_pct": row.get("open_ret_pct", row.get("收益率pct")),
                            "pnl": pnl,
                        }
                    )

        equity = cash + sum(p["cost"] for p in held) + sum(p["pnl"] for p in held)
        eq_curve.append({"date": d, "equity": equity, "scheme": scheme})

    fills_df = pd.DataFrame(fills)
    curve = pd.DataFrame(eq_curve)
    final = float(curve["equity"].iloc[-1]) if len(curve) else CAPITAL0
    n = len(fills_df)
    return {
        "scheme": scheme,
        "rank_mode": rank_mode,
        "ret_pct": (final / CAPITAL0 - 1.0) * 100.0,
        "final": final,
        "n_fill": n,
        "n_skip": len(skips),
        "mean_ret_pct": float(fills_df["ret_pct"].mean()) if n else float("nan"),
        "winrate_pct": float((fills_df["ret_pct"] > 0).mean() * 100) if n else float("nan"),
        "max_dd_pct": max_dd(curve["equity"]) if len(curve) else float("nan"),
        "mean_spend": float(fills_df["spend"].mean()) if n else float("nan"),
        "_fills": fills_df,
        "_curve": curve,
        "_skips": pd.DataFrame(skips),
    }


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    print("load+scan…")
    df, smap = load_universe()
    univ = enrich_universe_scans(df)

    schemes = ("baseline", "backup_chrono", "lookahead_backup")
    strength = {}
    fills_all, curves_all = [], []
    summary_rows = []

    for sch in schemes:
        print(f"strength {sch}…")
        st = sim(univ, smap, sch, rank_mode="strength")
        strength[sch] = st
        print(
            f"  ret={st['ret_pct']:+.2f}% fills={st['n_fill']} skip={st['n_skip']} "
            f"mean={st['mean_ret_pct']:.2f}% win={st['winrate_pct']:.1f}% "
            f"dd={st['max_dd_pct']:.2f}%"
        )
        summary_rows.append(
            {
                "scheme": sch,
                "rank": "strength",
                "组合收益pct": round(st["ret_pct"], 4),
                "vs_baseline_pp": round(st["ret_pct"] - strength["baseline"]["ret_pct"], 4)
                if sch != "baseline" and "baseline" in strength
                else 0.0,
                "成交": st["n_fill"],
                "跳过": st["n_skip"],
                "笔均": round(st["mean_ret_pct"], 4) if st["n_fill"] else None,
                "胜率": round(st["winrate_pct"], 2) if st["n_fill"] else None,
                "回撤": round(st["max_dd_pct"], 4),
            }
        )
        if len(st["_fills"]):
            fills_all.append(st["_fills"])
        if len(st["_curve"]):
            curves_all.append(st["_curve"])

    # fix vs_baseline after all strength runs
    base_ret = strength["baseline"]["ret_pct"]
    for r in summary_rows:
        r["vs_baseline_pp"] = round(r["组合收益pct"] - base_ret, 4)

    # random: baseline & backup_chrono
    # note: backup_chrono ignore rank → all seeds identical; still run to confirm
    print(f"random seeds={N_SEEDS} (baseline vs backup_chrono)…")
    rand_base, rand_chrono = [], []
    deltas = []
    seed_rows = []
    for s in range(N_SEEDS):
        b = sim(univ, smap, "baseline", rank_mode="random", seed=s)
        c = sim(univ, smap, "backup_chrono", rank_mode="random", seed=s)
        rand_base.append(b["ret_pct"])
        rand_chrono.append(c["ret_pct"])
        deltas.append(c["ret_pct"] - b["ret_pct"])
        seed_rows.append(
            {
                "seed": s,
                "baseline": b["ret_pct"],
                "backup_chrono": c["ret_pct"],
                "chrono-baseline_pp": c["ret_pct"] - b["ret_pct"],
            }
        )
        if (s + 1) % 20 == 0:
            print(f"  seed {s + 1}/{N_SEEDS}")

    rb = _rand_stats(rand_base)
    rc = _rand_stats(rand_chrono)
    rd = _rand_stats(deltas)
    beat = float((np.asarray(deltas) > 0).mean() * 100)
    # uniqueness of chrono path under random
    chrono_unique = len(set(round(x, 6) for x in rand_chrono))

    print(
        f"  random baseline: mean={rb['mean']:+.2f}% p10={rb['p10']:+.2f}% "
        f"| strength={strength['baseline']['ret_pct']:+.2f}%"
    )
    print(
        f"  random backup_chrono: mean={rc['mean']:+.2f}% p10={rc['p10']:+.2f}% "
        f"unique_rets={chrono_unique} | strength={strength['backup_chrono']['ret_pct']:+.2f}%"
    )
    print(
        f"  chrono-baseline: mean={rd['mean']:+.2f}pp p10={rd['p10']:+.2f}pp "
        f"chrono更优占比={beat:.0f}%"
    )

    rand_summary = pd.DataFrame(
        [
            {
                "scheme": "baseline",
                "强度序": round(strength["baseline"]["ret_pct"], 4),
                "随机均值": round(rb["mean"], 4),
                "随机p10": round(rb["p10"], 4),
                "随机p50": round(rb["p50"], 4),
                "随机>0": round(rb["gt0_pct"], 2),
            },
            {
                "scheme": "backup_chrono",
                "强度序": round(strength["backup_chrono"]["ret_pct"], 4),
                "随机均值": round(rc["mean"], 4),
                "随机p10": round(rc["p10"], 4),
                "随机p50": round(rc["p50"], 4),
                "随机>0": round(rc["gt0_pct"], 2),
                "备注": f"排序不影响成交; unique_rets={chrono_unique}",
            },
            {
                "scheme": "lookahead_backup(有偏对照)",
                "强度序": round(strength["lookahead_backup"]["ret_pct"], 4),
                "随机均值": None,
                "随机p10": None,
                "随机p50": None,
                "随机>0": None,
                "备注": "旧未来函数逻辑，仅对照",
            },
        ]
    )

    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        pd.DataFrame(summary_rows).to_excel(w, sheet_name="强度序汇总", index=False)
        rand_summary.to_excel(w, sheet_name="随机敏感性", index=False)
        pd.DataFrame(seed_rows).to_excel(w, sheet_name="随机各seed", index=False)
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
        "strength": {
            sch: {k: v for k, v in strength[sch].items() if not k.startswith("_")}
            for sch in schemes
        },
        "summary": summary_rows,
        "random": {
            "baseline": rb,
            "backup_chrono": rc,
            "chrono_minus_baseline": {**rd, "beats_pct": beat},
            "chrono_unique_rets": chrono_unique,
        },
        "curves": {
            sch: strength[sch]["_curve"][["date", "equity"]].to_dict(orient="records")
            for sch in schemes
        },
    }
    OUT_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT_XLSX}")
    print(pd.DataFrame(summary_rows).to_string(index=False))
    print(rand_summary.to_string(index=False))


if __name__ == "__main__":
    main()
