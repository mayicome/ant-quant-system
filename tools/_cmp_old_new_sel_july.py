# -*- coding: utf-8 -*-
"""Compare old vs new C selection order under capital-constrained July replay."""
import pandas as pd
import numpy as np
from pathlib import Path
import akshare as ak

hot = Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门")
old_sel_p = hot / "选股结果_C.xls"
new_sel_p = Path(r"d:\蚂蚁量化系统\history_data\选股结果_东财热门_2026-07-01_2026-07-31新规则.xls")


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


def load_sel(path):
    df = pd.read_excel(path)
    df["选股日"] = pd.to_datetime(df["选股日"]).dt.strftime("%Y-%m-%d")
    df["股票代码"] = df["股票代码"].map(code6)
    df = df[df["选股日"].str.startswith("2026-07")].copy()
    df["池序"] = df.groupby("选股日").cumcount()
    order = df.set_index(["选股日", "股票代码"])["池序"].to_dict()
    smap = df.groupby("选股日").size().to_dict()
    sets = {d: set(g["股票代码"]) for d, g in df.groupby("选股日")}
    return df, order, smap, sets


def load_trades(name, order):
    df = pd.read_excel(hot / name)
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
    df = df[df["选股日"].str.startswith("2026-07")].copy()
    df["池序"] = df.apply(lambda r: order.get((r["选股日"], r["代码"]), 9999), axis=1)
    # drop trades not in selection pool for that day
    df["in_pool"] = df["池序"] < 9999
    return df


def clip_n(S, L, U):
    return int(max(L, min(U, S)))


def keep_live_slots(g, Seff):
    opens = g[g["分支"] == "开盘夹档"].sort_values(["池序", "t", "代码"])
    gaps = g[g["分支"] == "高开回踩"].sort_values(["池序", "t", "代码"])
    kept_o = opens.head(Seff)
    remain = Seff - len(kept_o)
    kept_g = gaps.head(remain) if remain > 0 else gaps.iloc[0:0]
    kept = pd.concat([kept_o, kept_g], ignore_index=True)
    kept["prio"] = np.where(kept["分支"] == "开盘夹档", 0, 1)
    return kept.sort_values(["prio", "t", "池序", "代码"]).reset_index(drop=True)


def sim(trades, smap, L, U, capital0=100_000.0, label=""):
    # only in-pool trades
    df = trades[trades["in_pool"]].copy()
    cash = float(capital0)
    held, fills, skips = [], [], []
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
                S = int(smap.get(sel_day, 0))
                if S <= 0:
                    continue
                Seff = clip_n(S, L, U)
                g2 = keep_live_slots(g, Seff)
                for _, r in g2.iterrows():
                    locked_cost = sum(p["cost"] for p in held)
                    equity_now = cash + locked_cost
                    target = equity_now / float(Seff)
                    spend = min(target, cash)
                    if spend < 1 or cash < spend * 0.99:
                        skips.append(
                            {
                                "选股日": sel_day,
                                "代码": r["代码"],
                                "分支": r["分支"],
                                "池序": int(r["池序"]),
                            }
                        )
                        continue
                    cash -= spend
                    ret = float(r["收益率pct"]) / 100.0
                    pnl = spend * ret
                    held.append(
                        {
                            "release_day": next_td(r["end_date"], 1),
                            "cost": spend,
                            "pnl": pnl,
                        }
                    )
                    fills.append(
                        {
                            "选股日": sel_day,
                            "代码": r["代码"],
                            "分支": r["分支"],
                            "池序": int(r["池序"]),
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

    final = cash + sum(p["cost"] + p["pnl"] for p in held)
    return {
        "label": label,
        "final": final,
        "ret": (final / capital0 - 1) * 100,
        "n_fill": len(fills),
        "n_skip": len(skips),
        "fills": pd.DataFrame(fills),
        "skips": pd.DataFrame(skips),
    }


def compare_pools(old_df, new_df, old_sets, new_sets, old_order, new_order):
    days = sorted(set(old_sets) | set(new_sets))
    rows = []
    for d in days:
        oset, nset = old_sets.get(d, set()), new_sets.get(d, set())
        only_o = sorted(oset - nset)
        only_n = sorted(nset - oset)
        both = oset & nset
        # rank changes for intersection
        rank_chg = []
        for c in both:
            ro, rn = old_order[(d, c)], new_order[(d, c)]
            if ro != rn:
                rank_chg.append((c, ro, rn, rn - ro))
        rank_chg.sort(key=lambda x: abs(x[3]), reverse=True)
        rows.append(
            {
                "选股日": d,
                "Sold": len(oset),
                "Snew": len(nset),
                "相同": len(both),
                "仅旧": len(only_o),
                "仅新": len(only_n),
                "序变数": len(rank_chg),
                "仅旧码": ",".join(only_o[:8]),
                "仅新码": ",".join(only_n[:8]),
                "最大序变": (
                    f"{rank_chg[0][0]}:{rank_chg[0][1]}→{rank_chg[0][2]}"
                    if rank_chg
                    else ""
                ),
            }
        )
    return pd.DataFrame(rows)


def main():
    old_df, old_order, old_smap, old_sets = load_sel(old_sel_p)
    new_df, new_order, new_smap, new_sets = load_sel(new_sel_p)

    print("===== 选股池差异（按日）=====")
    cmp = compare_pools(old_df, new_df, old_sets, new_sets, old_order, new_order)
    print(cmp.to_string(index=False))
    print(
        f"\n合计: 日数={len(cmp)} 有成分差天数={(cmp['仅旧']+cmp['仅新']>0).sum()} "
        f"有序变天数={(cmp['序变数']>0).sum()} "
        f"旧总票={len(old_df)} 新总票={len(new_df)}"
    )

    # which trade fills change pool membership / order materially
    print("\n===== 占资回放：旧池序 vs 新池序（同成交源，10万）=====")
    print(f"{'模式':<28} {'旧池序':>12} {'新池序':>12} {'Δ收益pp':>8}")
    summaries = []
    for tname, tfile in [
        ("C仅开盘", "各日选股收益汇总_C.xlsx"),
        ("C开盘+回踩", "各日选股收益汇总_C带回踩.xlsx"),
    ]:
        for L, U in [(2, 4), (2, 6)]:
            told = load_trades(tfile, old_order)
            tnew = load_trades(tfile, new_order)
            # out of pool counts
            o_out = int((~told["in_pool"]).sum())
            n_out = int((~tnew["in_pool"]).sum())
            ro = sim(told, old_smap, L, U, 100_000, f"{tname} clip({L},{U}) 旧")
            rn = sim(tnew, new_smap, L, U, 100_000, f"{tname} clip({L},{U}) 新")
            dpp = rn["ret"] - ro["ret"]
            tag = f"{tname} clip({L},{U})"
            print(
                f"{tag:<28} "
                f"{ro['ret']:+6.2f}%/{ro['n_fill']}笔 "
                f"{rn['ret']:+6.2f}%/{rn['n_fill']}笔 "
                f"{dpp:+6.2f}"
            )
            if o_out or n_out:
                print(f"  成交不在当日池: 旧序视角={o_out} 新序视角={n_out}")
            summaries.append((ro, rn, dpp, o_out, n_out))

            # fill set differences for both mode
            if tname.startswith("C开盘"):
                fo = set(zip(ro["fills"]["选股日"], ro["fills"]["代码"])) if len(ro["fills"]) else set()
                fn = set(zip(rn["fills"]["选股日"], rn["fills"]["代码"])) if len(rn["fills"]) else set()
                only_o = sorted(fo - fn)
                only_n = sorted(fn - fo)
                if only_o or only_n:
                    print(f"  成交差 仅旧={only_o[:12]} 仅新={only_n[:12]}")


if __name__ == "__main__":
    main()
