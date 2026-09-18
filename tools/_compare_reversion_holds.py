# -*- coding: utf-8 -*-
import json
from pathlib import Path

import numpy as np
import pandas as pd

root = Path("history_data/reversion_strategy")


def load(name):
    d = root / name
    return {
        "nav": pd.read_csv(d / "nav.csv", parse_dates=["decision_date"]),
        "tr": pd.read_csv(d / "trades.csv"),
        "ic": pd.read_csv(d / "ic_summary.csv"),
        "prem": pd.read_csv(d / "premium_summary.csv"),
        "dec": pd.read_csv(d / "deciles.csv"),
        "gates": pd.read_csv(d / "gates.csv"),
        "cfg": json.loads((d / "config.json").read_text(encoding="utf-8")),
        "summary": (d / "summary.txt").read_text(encoding="utf-8"),
    }


def ann_stats(nav, hold):
    s = nav["nav"].astype(float)
    r = s.pct_change().dropna()
    total = float(s.iloc[-1] / s.iloc[0] - 1)
    n = len(r)
    ppy = 52.0 / max(1, hold)
    ann = (1 + total) ** (ppy / max(n, 1)) - 1 if n else np.nan
    vol = float(r.std(ddof=1) * np.sqrt(ppy)) if len(r) > 1 else np.nan
    sharpe = ann / vol if vol and vol > 1e-12 else np.nan
    dd = float(((s - s.cummax()) / s.cummax()).min())
    cash = (nav["cash"] / nav["nav"]).mean()
    return {
        "total": total,
        "ann": ann,
        "vol": vol,
        "sharpe": sharpe,
        "dd": dd,
        "n": n,
        "hold_mean": float(nav.n_hold.mean()),
        "cash_mean": float(cash),
        "to_mean": float(nav.turnover_used.mean()),
        "to_med": float(nav.turnover_used.median()),
        "to_gt30": float((nav.turnover_used > 0.3).mean()),
        "nav0": float(s.iloc[0]),
        "nav1": float(s.iloc[-1]),
    }


rows = []
detail = {}
for name in ["hold1", "hold2", "hold3"]:
    x = load(name)
    h = int(x["cfg"].get("hold_weeks", int(name[-1])))
    st = ann_stats(x["nav"], h)
    gates = x["gates"]
    gp = gates.set_index("gate")["pass"].to_dict() if "pass" in gates.columns else {}
    gv = gates.set_index("gate")["value"].to_dict() if "gate" in gates.columns else {}
    nav = x["nav"].copy()
    nav["y"] = nav.decision_date.dt.year
    yearly = {int(y): float(g.nav.iloc[-1] / g.nav.iloc[0] - 1) for y, g in nav.groupby("y")}
    main = x["ic"][x["ic"].factor.isin(["REV5", "RSI14", "BIAS20"])]
    prem = x["prem"]
    stops = int((x["tr"].side == "stop_sell").sum())
    buys = int((x["tr"].side == "buy").sum())
    dec = x["dec"]
    spread = (
        float(dec.tail(3).mean_ret.mean() - dec.head(3).mean_ret.mean())
        if len(dec) >= 6
        else np.nan
    )
    rows.append(
        {
            "run": name,
            "hold": h,
            **st,
            "yearly": yearly,
            "ic_abs_mean": float(main.abs_ic_mean.mean()),
            "icir_abs": float(main.icir.mean()),
            "prem_t_abs": float(prem.t_nw.abs().mean()),
            "t": {r.factor: float(r.t_nw) for _, r in prem.iterrows()},
            "mu": {r.factor: float(r["mean"]) for _, r in prem.iterrows()},
            "spread": spread,
            "stops": stops,
            "buys": buys,
            "gp": gp,
            "gv": gv,
            "ic": x["ic"],
            "dec": dec,
        }
    )
    detail[name] = x

print("======== PERF ========")
hdr = "run hold  total    ann  sharpe   maxdd  to_med  >0.3  n_hold  cash   periods"
print(hdr)
for r in rows:
    print(
        f"{r['run']:5} {r['hold']:2d}  {r['total']:+6.1%} {r['ann']:+6.1%} "
        f"{r['sharpe']:7.2f} {r['dd']:7.1%} {r['to_med']:6.3f} {r['to_gt30']:5.0%} "
        f"{r['hold_mean']:6.1f} {r['cash_mean']:5.1%}  {r['n']:3d}"
    )

print("\n======== YEARLY ========")
for r in rows:
    ys = " ".join(f"{y}:{v:+.1%}" for y, v in sorted(r["yearly"].items()))
    print(r["run"], ys)

print("\n======== FACTOR ========")
for r in rows:
    print(
        f"{r['run']}: main|IC|={r['ic_abs_mean']:.4f} ICIR={r['icir_abs']:.3f} "
        f"all|t|={r['prem_t_abs']:.2f} decile_spread={r['spread']:.4f}"
    )
    print("  t ", {k: round(v, 2) for k, v in r["t"].items()})
    print("  mu", {k: round(v, 5) for k, v in r["mu"].items()})
    print(r["ic"].to_string(index=False))

print("\n======== GATES ========")
keys = ["main_|IC|_mean", "main_ICIR_abs", "main_|t|_mean", "ann_return", "max_dd", "sharpe", "buy_fail_rate", "sell_fail_rate"]
for r in rows:
    bits = []
    for k in keys:
        p = r["gp"].get(k)
        v = r["gv"].get(k)
        try:
            vs = f"{float(v):.4f}"
        except Exception:
            vs = str(v)
        bits.append(f"{k}:{p}({vs})")
    print(r["run"], " | ".join(bits))

print("\n======== STOPS / DECILES ========")
for r in rows:
    print(f"{r['run']}: stops={r['stops']} buys={r['buys']} ratio={r['stops']/max(r['buys'],1):.2%}")
    print(r["dec"].to_string(index=False))
