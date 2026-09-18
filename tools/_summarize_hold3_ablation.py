# -*- coding: utf-8 -*-
import pandas as pd
from pathlib import Path

root = Path("history_data/reversion_strategy/hold3_ablation")
print(pd.read_csv(root / "ablation_summary.csv").to_string(index=False))
print()
for tag in [
    "h3_base",
    "h3_drop_rsi14",
    "h3_drop_pos_prem",
    "h3_rev5_vshrk",
    "h3_nostop",
    "h3_drop_rsi14_nostop",
    "h3_rev5_vshrk_nostop",
]:
    d = root / tag
    nav = pd.read_csv(d / "nav.csv", parse_dates=["decision_date"])
    prem = pd.read_csv(d / "premium_summary.csv")
    gates = pd.read_csv(d / "gates.csv").set_index("gate")
    tr = pd.read_csv(d / "trades.csv")
    nav["y"] = nav.decision_date.dt.year
    ys = " ".join(
        f"{y}:{g.nav.iloc[-1] / g.nav.iloc[0] - 1:+.1%}" for y, g in nav.groupby("y")
    )
    stops = int((tr.side == "stop_sell").sum())
    buys = int((tr.side == "buy").sum())
    print(f"==== {tag} ====")
    print("prem t", {r.factor: round(float(r.t_nw), 2) for _, r in prem.iterrows()})
    for k in ["ann_return", "max_dd", "sharpe", "main_|t|_mean"]:
        if k in gates.index:
            print(f"  {k}: pass={gates.loc[k, 'pass']} val={float(gates.loc[k, 'value']):.4f}")
    print(f"  yearly {ys}")
    print(f"  stops={stops} buys={buys} ratio={stops / max(buys, 1):.1%}")
