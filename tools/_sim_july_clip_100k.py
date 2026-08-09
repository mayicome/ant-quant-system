# -*- coding: utf-8 -*-
"""July C backtest — live-aligned logic:
- Seff = clip(S, L, U)
- Slot truncate: all open-band first (pool order), then pullbacks (pool order) into remaining slots
- Each fill: spend = min(equity/Seff, cash); equity = cash + sum(position costs) [no future PnL]
- Execute: opens first, then pullbacks by time
"""
import pandas as pd
import numpy as np
from pathlib import Path
import akshare as ak

root = Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门")


def code6(v):
    if pd.isna(v):
        return ""
    try:
        return str(int(float(v))).zfill(6)
    except Exception:
        return ""


cal = ak.tool_trade_date_hist_sina()
cal["trade_date"] = pd.to_datetime(cal["trade_date"]).dt.strftime("%Y-%m-%d")
tdays = cal[(cal["trade_date"] >= "2026-07-01") & (cal["trade_date"] <= "2026-08-10")][
    "trade_date"
].tolist()
idx = {d: i for i, d in enumerate(tdays)}


def next_td(d, n=1):
    return tdays[idx[d] + n]


sel = pd.read_excel(root / "选股结果_C.xls")
sel["选股日"] = pd.to_datetime(sel["选股日"]).dt.strftime("%Y-%m-%d")
sel["股票代码"] = sel["股票代码"].map(code6)
sel["池序"] = sel.groupby("选股日").cumcount()
order = sel.set_index(["选股日", "股票代码"])["池序"].to_dict()
Smap = sel.groupby("选股日").size().to_dict()


def load(name):
    df = pd.read_excel(root / name)
    df["代码"] = df["代码"].map(code6)
    df["选股日"] = pd.to_datetime(df["选股日"]).dt.strftime("%Y-%m-%d")
    df["买入日"] = pd.to_datetime(df["买入日"]).dt.strftime("%Y-%m-%d")
    df["end_date"] = pd.to_datetime(df["end_date"]).dt.strftime("%Y-%m-%d")
    df["收益率pct"] = pd.to_numeric(df["收益率pct"], errors="coerce")
    tip = df["触发信息"].astype(str)
    df["分支"] = np.where(
        tip.str.contains("开盘买入"),
        "开盘夹档",
        np.where(tip.str.contains("单点买入"), "高开回踩", "其他"),
    )
    tcol = "买入时间" if "买入时间" in df.columns else None
    df["t"] = df[tcol].astype(str) if tcol else ""
    df["池序"] = df.apply(lambda r: order.get((r["选股日"], r["代码"]), 9999), axis=1)
    return df


def july(df):
    return df[df["选股日"].str.startswith("2026-07")].copy()


C_open = july(load("各日选股收益汇总_C.xlsx"))
C_both = july(load("各日选股收益汇总_C带回踩.xlsx"))


def clip_n(S, L, U):
    return int(max(L, min(U, S)))


def keep_live_slots(g: pd.DataFrame, Seff: int) -> pd.DataFrame:
    """Match strategy_b98f343e: opens first by pool order, then pullbacks into remaining slots."""
    opens = g[g["分支"] == "开盘夹档"].sort_values(["池序", "t", "代码"])
    gaps = g[g["分支"] == "高开回踩"].sort_values(["池序", "t", "代码"])
    kept_o = opens.head(Seff)
    remain = Seff - len(kept_o)
    kept_g = gaps.head(remain) if remain > 0 else gaps.iloc[0:0]
    kept = pd.concat([kept_o, kept_g], ignore_index=True)
    kept["prio"] = np.where(kept["分支"] == "开盘夹档", 0, 1)
    return kept.sort_values(["prio", "t", "池序", "代码"]).reset_index(drop=True)


def sim(trades, L, U, capital0=100_000.0, label=""):
    df = trades.copy()
    cash = float(capital0)
    held = []  # cost, pnl, release_day
    fills = []
    skips = []
    eq_curve = []

    all_days = [d for d in tdays if "2026-07-01" <= d <= "2026-08-05"]
    by_day = {d: g for d, g in df.groupby("买入日")}

    for d in all_days:
        still = []
        for p in held:
            if p["release_day"] == d:
                cash += p["cost"] + p["pnl"]
            else:
                still.append(p)
        held = still

        if d in by_day:
            for sel_day, g in by_day[d].groupby("选股日", sort=False):
                S = int(Smap.get(sel_day, len(g)))
                Seff = clip_n(S, L, U)
                g2 = keep_live_slots(g, Seff)

                for _, r in g2.iterrows():
                    # sizing equity: cash + position costs only (no future trade PnL)
                    locked_cost = sum(p["cost"] for p in held)
                    equity_now = cash + locked_cost
                    target = equity_now / float(Seff)
                    spend = min(target, cash)
                    if spend < 1 or cash < spend * 0.99:
                        skips.append(
                            {
                                "选股日": sel_day,
                                "买入日": d,
                                "代码": r["代码"],
                                "分支": r["分支"],
                                "池序": int(r["池序"]),
                                "原因": "没钱",
                                "target": target,
                                "cash": cash,
                            }
                        )
                        continue
                    cash -= spend
                    ret = float(r["收益率pct"]) / 100.0
                    pnl = spend * ret
                    rel = next_td(r["end_date"], 1)
                    held.append({"release_day": rel, "cost": spend, "pnl": pnl})
                    fills.append(
                        {
                            "选股日": sel_day,
                            "买入日": d,
                            "代码": r["代码"],
                            "分支": r["分支"],
                            "池序": int(r["池序"]),
                            "S": S,
                            "Seff": Seff,
                            "spend": spend,
                            "ret_pct": float(r["收益率pct"]),
                            "pnl": pnl,
                            "equity_at_buy": equity_now,
                            "cash_after": cash,
                        }
                    )

        locked_cost = sum(p["cost"] for p in held)
        locked_pnl = sum(p["pnl"] for p in held)
        equity = cash + locked_cost + locked_pnl
        eq_curve.append(
            {
                "date": d,
                "cash": cash,
                "locked_cost": locked_cost,
                "equity": equity,
                "n_held": len(held),
            }
        )

    final = eq_curve[-1]["equity"] if eq_curve else capital0
    return {
        "label": label,
        "L": L,
        "U": U,
        "final": final,
        "ret": (final / capital0 - 1) * 100,
        "n_fill": len(fills),
        "n_skip": len(skips),
        "fills": pd.DataFrame(fills),
        "skips": pd.DataFrame(skips),
        "curve": pd.DataFrame(eq_curve),
    }


def day_rows(r, all_sel):
    f = r["fills"]
    s = r["skips"] if len(r["skips"]) else pd.DataFrame()
    rows = []
    for d in all_sel:
        S = Smap[d]
        Seff = clip_n(S, r["L"], r["U"])
        fd = f[f["选股日"] == d] if len(f) else f
        n = len(fd)
        spend = float(fd["spend"].sum()) if n else 0.0
        pnl = float(fd["pnl"].sum()) if n else 0.0
        n_skip = int((s["选股日"] == d).sum()) if len(s) else 0
        rows.append(
            {
                "选股日": d,
                "S": S,
                "Seff": Seff,
                "成交": n,
                "开": int((fd["分支"] == "开盘夹档").sum()) if n else 0,
                "回": int((fd["分支"] == "高开回踩").sum()) if n else 0,
                "跳": n_skip,
                "花费": spend,
                "盈亏": pnl,
                "日收益%": (pnl / spend * 100) if spend > 0 else 0.0,
            }
        )
    return pd.DataFrame(rows)


def main():
    results = []
    for name, tr in [("C仅开盘", C_open), ("C开盘+回踩", C_both)]:
        for L, U in [(2, 4), (2, 6)]:
            r = sim(tr, L, U, 100_000, f"{name} clip({L},{U})")
            results.append(r)
            print(f"=== {r['label']} ===")
            print(
                f"期末 {r['final']:.0f}  收益 {r['ret']:+.2f}%  成交 {r['n_fill']}  跳过 {r['n_skip']}"
            )
            if len(r["fills"]):
                f = r["fills"]
                print(
                    f"  开盘 {(f['分支']=='开盘夹档').sum()}  回踩 {(f['分支']=='高开回踩').sum()}"
                    f"  均ret {f['ret_pct'].mean():+.2f}%  均spend {f['spend'].mean():.0f}"
                )
            if len(r["skips"]):
                print("  跳过:")
                print(r["skips"][["选股日", "代码", "分支", "池序"]].to_string(index=False))
            print()

    print("===== SUMMARY 10万 · 新逻辑 =====")
    print(f"{'模式':<22} {'期末':>10} {'收益%':>8} {'成交':>6} {'跳过':>6}")
    for r in results:
        print(
            f"{r['label']:<22} {r['final']/10000:8.2f}万 {r['ret']:+7.2f} {r['n_fill']:6d} {r['n_skip']:6d}"
        )

    # daily for C+pullback both clips
    all_sel = sorted(d for d in Smap if d.startswith("2026-07"))
    r4 = next(x for x in results if x["label"] == "C开盘+回踩 clip(2,4)")
    r6 = next(x for x in results if x["label"] == "C开盘+回踩 clip(2,6)")
    t4, t6 = day_rows(r4, all_sel), day_rows(r6, all_sel)
    cmp = t4[["选股日", "S"]].copy()
    cmp["Seff4"] = t4["Seff"]
    cmp["Seff6"] = t6["Seff"]
    cmp["成交4"] = (
        t4["成交"].astype(str) + "(开" + t4["开"].astype(str) + "回" + t4["回"].astype(str) + ")"
    )
    cmp["成交6"] = (
        t6["成交"].astype(str) + "(开" + t6["开"].astype(str) + "回" + t6["回"].astype(str) + ")"
    )
    cmp["跳4"] = t4["跳"]
    cmp["跳6"] = t6["跳"]
    cmp["日收益4%"] = t4["日收益%"].round(2)
    cmp["日收益6%"] = t6["日收益%"].round(2)
    cmp["盈亏4"] = t4["盈亏"].round(0)
    cmp["盈亏6"] = t6["盈亏"].round(0)
    cmp["Δ盈亏"] = (t6["盈亏"] - t4["盈亏"]).round(0)
    print("\n===== C开盘+回踩 按日 clip(2,4) vs (2,6) =====")
    print(cmp.to_string(index=False))


if __name__ == "__main__":
    main()
