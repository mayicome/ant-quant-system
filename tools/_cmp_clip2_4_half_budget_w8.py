# -*- coding: utf-8 -*-
"""Compare full-equity vs half-budget clip(2,4) · w=8 · native 新规则 215 · 100k.

Primary:
  A) full equity/Seff  — spend=min(equity/Seff, cash)  [baseline recent compares]
  B) half-budget       — daily buy budget = 0.5 * equity (before buys that day);
                         spend=min(budget/Seff, cash); buy up to Seff in score order

Optional quick check:
  C) half-budget + random rank among Cond123 fills (100 seeds mean)

Does NOT change production defaults (EXPORT_ELIG_WEIGHT / clip_U untouched).

Usage:
  set PYTHONIOENCODING=utf-8
  python tools/_cmp_clip2_4_half_budget_w8.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import akshare as ak
import numpy as np
import pandas as pd

_ROOT_PKG = Path(__file__).resolve().parents[1]
if str(_ROOT_PKG) not in sys.path:
    sys.path.insert(0, str(_ROOT_PKG))
from sector_stock_filter import EXPORT_ELIG_WEIGHT, clip_strength_sort_key  # noqa: E402

ROOT = Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门")
OUT_XLSX = ROOT / "clip2_4_半仓_w8_对比.xlsx"
OUT_CSV_SUMMARY = ROOT / "clip2_4_半仓_w8_汇总.csv"
OUT_CSV_DAILY = ROOT / "clip2_4_半仓_w8_按日成交.csv"
OUT_CSV_FILLS = ROOT / "clip2_4_半仓_w8_成交明细.csv"
OUT_JSON = ROOT / "clip2_4_半仓_w8_对比.json"
CAPITAL0 = 100_000.0
W = 8
L_, U_ = 2, 4
HALF_FRAC = 0.5
N_RANDOM_SEEDS = 100
DEFAULT_W = int(EXPORT_ELIG_WEIGHT)

BUY = ROOT / "回测成交明细_新规则.csv"
SELL = ROOT / "回测成交明细_新规则_卖出.csv"
NATIVE_SUM = ROOT / "各日选股收益汇总_新规则.xlsx"
SEL = ROOT / (
    "选股结果_东财热门-besttest-无涨停均线差0.5to2-MA空头排列_2026-07-01_2026-07-31.xls"
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


def clip_n(S: int, L: int, U: int) -> int:
    return int(max(L, min(U, S)))


def max_dd(equity: pd.Series) -> float:
    if equity.empty:
        return float("nan")
    peak = equity.cummax()
    return float((equity / peak - 1.0).min() * 100.0)


def load_universe() -> tuple[pd.DataFrame, dict]:
    sel = pd.read_excel(SEL)
    sel["选股日"] = pd.to_datetime(sel["选股日"]).dt.strftime("%Y-%m-%d")
    sel["股票代码"] = sel["股票代码"].map(code6)
    sel = sel[sel["选股日"].str.startswith("2026-07")].copy()
    sel["池序"] = sel.groupby("选股日").cumcount()
    order = sel.set_index(["选股日", "股票代码"])["池序"].to_dict()
    smap = sel.groupby("选股日").size().to_dict()
    strength = {
        (r["选股日"], r["股票代码"]): (r.get("合格榜内序位"), r.get("合格榜标签内RS排名"))
        for _, r in sel.iterrows()
    }

    native = pd.read_excel(NATIVE_SUM)
    native["代码"] = native["代码"].map(code6)
    native["选股日"] = pd.to_datetime(native["选股日"]).dt.strftime("%Y-%m-%d")
    native["end_date"] = pd.to_datetime(native["end_date"]).dt.strftime("%Y-%m-%d")
    native["收益率pct"] = pd.to_numeric(native["收益率pct"], errors="coerce")
    keep_cols = ["选股日", "代码", "收益率pct", "end_date"]
    for c in ("合格榜内序位", "合格榜标签内RS排名"):
        if c in native.columns:
            keep_cols.append(c)
    native = native[keep_cols].drop_duplicates(["选股日", "代码"])

    buy = pd.read_csv(BUY, encoding="utf-8-sig")
    buy = buy.drop(columns=[c for c in ("start_date", "end_date") if c in buy.columns])
    buy["代码"] = buy["代码"].map(code6)
    buy["选股日"] = pd.to_datetime(buy["选股日"]).dt.strftime("%Y-%m-%d")
    buy["买入日"] = pd.to_datetime(buy["日期"]).dt.strftime("%Y-%m-%d")
    tip = buy["触发信息"].astype(str)
    buy["分支"] = np.where(
        tip.str.contains("开盘买入"),
        "开盘夹档",
        np.where(tip.str.contains("单点买入"), "高开回踩", "其他"),
    )
    buy["t"] = buy["时间"].astype(str) if "时间" in buy.columns else ""

    sell = pd.read_csv(SELL, encoding="utf-8-sig")
    sell["代码"] = sell["代码"].map(code6)
    sell["选股日"] = pd.to_datetime(sell["选股日"]).dt.strftime("%Y-%m-%d")
    kb = set(zip(buy["选股日"], buy["代码"]))
    kn = set(zip(native["选股日"], native["代码"]))
    if kb != kn:
        raise SystemExit(f"FAIL: buy/summary keys misaligned |buy|={len(kb)} |nat|={len(kn)}")

    m = buy.merge(native, on=["选股日", "代码"], how="left")
    if m["收益率pct"].isna().any() or m["end_date"].isna().any():
        n_miss = int((m["收益率pct"].isna() | m["end_date"].isna()).sum())
        raise SystemExit(f"FAIL: {n_miss} keys missing native returns/end_date")

    m["池序"] = m.apply(lambda r: order.get((r["选股日"], r["代码"]), 9999), axis=1)
    m["in_pool"] = m["池序"] < 9999
    if "合格榜内序位" not in m.columns:
        m["合格榜内序位"] = np.nan
    if "合格榜标签内RS排名" not in m.columns:
        m["合格榜标签内RS排名"] = np.nan
    for i, r in m.iterrows():
        key = (r["选股日"], r["代码"])
        if key not in strength:
            continue
        elig, rs = strength[key]
        if pd.isna(r.get("合格榜内序位")):
            m.at[i, "合格榜内序位"] = elig
        if pd.isna(r.get("合格榜标签内RS排名")):
            m.at[i, "合格榜标签内RS排名"] = rs

    df = m[m["in_pool"]].copy()
    print(
        f"universe buys={len(buy)} in_pool={len(df)} out={int((~m['in_pool']).sum())} "
        f"S-days={len(smap)} w={W} (prod EXPORT_ELIG_WEIGHT={DEFAULT_W})"
    )
    return df, smap


def rank_opens_w8(g: pd.DataFrame, w: int) -> pd.DataFrame:
    opens = g[g["分支"] == "开盘夹档"].copy()
    if opens.empty:
        return opens
    opens["_sk"] = opens.apply(
        lambda r: clip_strength_sort_key(
            r.get("合格榜内序位"),
            r.get("合格榜标签内RS排名"),
            r.get("代码"),
            elig_weight=w,
        ),
        axis=1,
    )
    return opens.sort_values(["_sk", "t", "代码"]).drop(columns=["_sk"])


def rank_opens_random(g: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    opens = g[g["分支"] == "开盘夹档"].copy()
    if opens.empty:
        return opens
    perm = rng.permutation(len(opens))
    return opens.iloc[perm].reset_index(drop=True)


def sim_one(
    df: pd.DataFrame,
    smap: dict,
    *,
    mode: str,
    budget_frac: float,
    w: int = W,
    seed: int | None = None,
) -> dict:
    """mode: 'w8' | 'random'. budget_frac=1.0 full equity; 0.5 half-budget."""
    cash = float(CAPITAL0)
    held, fills, skips = [], [], []
    eq_curve = []
    daily_rows = []
    all_days = [d for d in tdays if "2026-07-01" <= d <= "2026-08-05"]
    by_day = {d: g for d, g in df.groupby("买入日")}
    rng = np.random.default_rng(seed) if seed is not None else None
    label = (
        f"{'半仓' if budget_frac < 1 else '全仓'}_"
        f"{'随机' if mode == 'random' else f'w{w}'}"
        f"_clip({L_},{U_})"
    )

    for d in all_days:
        still = []
        for p in held:
            if p["release_day"] == d:
                cash += p["cost"] + p["pnl"]
            else:
                still.append(p)
        held = still

        # equity / daily budget snapshot BEFORE any buys that day
        locked_cost0 = sum(p["cost"] for p in held)
        equity_pre = cash + locked_cost0
        day_budget = float(budget_frac) * equity_pre
        day_budget_left = day_budget
        day_fills = 0
        day_skips = 0
        day_spend = 0.0

        if d in by_day:
            for sel_day, g in by_day[d].groupby("选股日", sort=False):
                S = int(smap.get(sel_day, 0))
                if S <= 0:
                    continue
                Seff = clip_n(S, L_, U_)
                if mode == "w8":
                    opens = rank_opens_w8(g, w)
                elif mode == "random":
                    assert rng is not None
                    opens = rank_opens_random(g, rng)
                else:
                    raise ValueError(mode)
                if opens.empty:
                    continue
                g2 = opens.head(Seff).reset_index(drop=True)
                # per-name target from day budget / Seff (fixed for the day slot count)
                target = day_budget / float(Seff)
                for _, r in g2.iterrows():
                    spend = min(target, cash, day_budget_left)
                    if spend < 1 or cash < spend * 0.99:
                        skips.append(1)
                        day_skips += 1
                        continue
                    cash -= spend
                    day_budget_left -= spend
                    day_spend += spend
                    day_fills += 1
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
                            "方案": label,
                            "mode": mode,
                            "budget_frac": budget_frac,
                            "选股日": sel_day,
                            "买入日": d,
                            "代码": r["代码"],
                            "spend": spend,
                            "ret_pct": float(r["收益率pct"]),
                            "pnl": pnl,
                            "S": S,
                            "Seff": Seff,
                            "equity_pre": equity_pre,
                            "day_budget": day_budget,
                            "合格榜内序位": r.get("合格榜内序位", np.nan),
                            "合格榜标签内RS排名": r.get("合格榜标签内RS排名", np.nan),
                        }
                    )

        locked_cost = sum(p["cost"] for p in held)
        locked_pnl = sum(p["pnl"] for p in held)
        equity = cash + locked_cost + locked_pnl
        eq_curve.append(
            {
                "date": d,
                "equity": equity,
                "cash": cash,
                "方案": label,
                "equity_pre": equity_pre,
                "day_budget": day_budget,
                "day_spend": day_spend,
                "成交笔数": day_fills,
                "跳过笔数": day_skips,
            }
        )
        if day_fills or day_skips or (d in by_day):
            # one row per 选股日 that had candidates that day
            if d in by_day:
                for sel_day, g in by_day[d].groupby("选股日", sort=False):
                    n_day = int(
                        sum(
                            1
                            for f in fills
                            if f["买入日"] == d and f["选股日"] == sel_day
                        )
                    )
                    daily_rows.append(
                        {
                            "方案": label,
                            "选股日": sel_day,
                            "买入日": d,
                            "成交笔数": n_day,
                            "S": int(smap.get(sel_day, 0)),
                            "Seff": clip_n(int(smap.get(sel_day, 0)), L_, U_),
                            "equity_pre": round(equity_pre, 2),
                            "day_budget": round(day_budget, 2),
                            "cash_after_release": round(
                                cash + day_spend, 2
                            ),  # approx pre-buy cash after releases
                        }
                    )

    fills_df = pd.DataFrame(fills)
    curve = pd.DataFrame(eq_curve)
    daily_df = pd.DataFrame(daily_rows)
    final = float(curve["equity"].iloc[-1]) if len(curve) else CAPITAL0
    ret_pct = (final / CAPITAL0 - 1.0) * 100.0
    n_fill = int(len(fills_df))
    return {
        "方案": label,
        "mode": mode,
        "budget_frac": budget_frac,
        "期末权益": round(final, 2),
        "收益%": round(ret_pct, 4),
        "成交笔数": n_fill,
        "跳过笔数": int(len(skips)),
        "均ret%": round(float(fills_df["ret_pct"].mean()), 4) if n_fill else float("nan"),
        "胜率%": round(float((fills_df["ret_pct"] > 0).mean() * 100), 2) if n_fill else float("nan"),
        "最大回撤%": round(max_dd(curve["equity"]), 4) if len(curve) else float("nan"),
        "日均成交": round(float(fills_df.groupby("选股日").size().mean()), 4) if n_fill else 0.0,
        "交易日数": int(fills_df["选股日"].nunique()) if n_fill else 0,
        "均仓位": round(float(fills_df["spend"].mean()), 2) if n_fill else float("nan"),
        "_fills": fills_df,
        "_curve": curve,
        "_daily": daily_df,
        "_ret_pct": ret_pct,
    }


def main() -> None:
    if W != DEFAULT_W:
        print(f"NOTE: sim w={W} != prod EXPORT_ELIG_WEIGHT={DEFAULT_W} (prod untouched)")
    df, smap = load_universe()

    print(
        f"===== clip(2,4) · w={W} · capital={CAPITAL0:.0f} · "
        f"full vs half({HALF_FRAC}) ====="
    )
    print(f"score = Elig×{W}+标签内RS；无 max_per_tag；S=全日选股池")

    r_full = sim_one(df, smap, mode="w8", budget_frac=1.0, w=W)
    r_half = sim_one(df, smap, mode="w8", budget_frac=HALF_FRAC, w=W)

    # optional random half-budget mean
    rand_rets = []
    rand_fills_n = []
    rand_skips = []
    rand_mean_ret = []
    rand_wr = []
    rand_dd = []
    print(f"----- optional: half-budget random rank ×{N_RANDOM_SEEDS} seeds -----")
    for s in range(N_RANDOM_SEEDS):
        rr = sim_one(df, smap, mode="random", budget_frac=HALF_FRAC, seed=s)
        rand_rets.append(rr["_ret_pct"])
        rand_fills_n.append(rr["成交笔数"])
        rand_skips.append(rr["跳过笔数"])
        rand_mean_ret.append(rr["均ret%"])
        rand_wr.append(rr["胜率%"])
        rand_dd.append(rr["最大回撤%"])
    r_rand_mean = {
        "方案": f"半仓_随机均值_clip({L_},{U_})",
        "mode": "random_mean",
        "budget_frac": HALF_FRAC,
        "期末权益": round(CAPITAL0 * (1 + float(np.mean(rand_rets)) / 100.0), 2),
        "收益%": round(float(np.mean(rand_rets)), 4),
        "成交笔数": round(float(np.mean(rand_fills_n)), 2),
        "跳过笔数": round(float(np.mean(rand_skips)), 2),
        "均ret%": round(float(np.nanmean(rand_mean_ret)), 4),
        "胜率%": round(float(np.nanmean(rand_wr)), 2),
        "最大回撤%": round(float(np.nanmean(rand_dd)), 4),
        "日均成交": float("nan"),
        "交易日数": float("nan"),
        "均仓位": float("nan"),
        "收益%_std": round(float(np.std(rand_rets)), 4),
        "收益%_p10": round(float(np.percentile(rand_rets, 10)), 4),
        "收益%_p90": round(float(np.percentile(rand_rets, 90)), 4),
        "n_seeds": N_RANDOM_SEEDS,
    }

    def strip_private(r: dict) -> dict:
        out = {k: v for k, v in r.items() if not k.startswith("_")}
        return out

    rows = [strip_private(r_full), strip_private(r_half), r_rand_mean]
    summary = pd.DataFrame(rows)

    # side-by-side print
    print("\n===== 并排对比 =====")
    hdr = (
        f"{'方案':<28} {'收益%':>8} {'成交':>6} {'跳过':>6} "
        f"{'均ret%':>8} {'胜率%':>7} {'maxDD%':>8}"
    )
    print(hdr)
    for r in (r_full, r_half):
        print(
            f"{r['方案']:<28} {r['_ret_pct']:+8.2f} {r['成交笔数']:6d} {r['跳过笔数']:6d} "
            f"{r['均ret%']:+8.2f} {r['胜率%']:7.1f} {r['最大回撤%']:8.2f}"
        )
    print(
        f"{r_rand_mean['方案']:<28} {r_rand_mean['收益%']:+8.2f} "
        f"{r_rand_mean['成交笔数']:6.1f} {r_rand_mean['跳过笔数']:6.1f} "
        f"{r_rand_mean['均ret%']:+8.2f} {r_rand_mean['胜率%']:7.1f} "
        f"{r_rand_mean['最大回撤%']:8.2f}  "
        f"(std={r_rand_mean['收益%_std']:.2f} "
        f"p10={r_rand_mean['收益%_p10']:+.2f} p90={r_rand_mean['收益%_p90']:+.2f})"
    )

    # daily buy counts side-by-side
    d_full = r_full["_daily"].rename(columns={"成交笔数": "成交_全仓"})[
        ["选股日", "买入日", "S", "Seff", "成交_全仓"]
    ]
    d_half = r_half["_daily"].rename(
        columns={
            "成交笔数": "成交_半仓",
            "equity_pre": "半仓_equity_pre",
            "day_budget": "半仓_day_budget",
        }
    )[["选股日", "成交_半仓", "半仓_equity_pre", "半仓_day_budget"]]
    daily_cmp = d_full.merge(d_half, on="选股日", how="outer").sort_values("选股日")
    daily_cmp["成交_全仓"] = daily_cmp["成交_全仓"].fillna(0).astype(int)
    daily_cmp["成交_半仓"] = daily_cmp["成交_半仓"].fillna(0).astype(int)

    print("\n===== 按选股日成交笔数 =====")
    print(
        f"{'选股日':<12} {'S':>4} {'Seff':>4} {'全仓成交':>8} {'半仓成交':>8} "
        f"{'半仓预算':>10}"
    )
    for _, row in daily_cmp.iterrows():
        bud = row.get("半仓_day_budget", float("nan"))
        bud_s = f"{bud:,.0f}" if pd.notna(bud) else "-"
        print(
            f"{row['选股日']:<12} {int(row['S']) if pd.notna(row['S']) else 0:4d} "
            f"{int(row['Seff']) if pd.notna(row['Seff']) else 0:4d} "
            f"{int(row['成交_全仓']):8d} {int(row['成交_半仓']):8d} {bud_s:>10}"
        )

    d_full_ret = r_full["_ret_pct"]
    d_half_ret = r_half["_ret_pct"]
    delta = d_half_ret - d_full_ret
    # check day-after-big-day: consecutive days where full has 0 but half has >0
    zero_full_pos_half = int(
        ((daily_cmp["成交_全仓"] == 0) & (daily_cmp["成交_半仓"] > 0)).sum()
    )
    verdict = (
        f"半仓 clip(2,4) w=8 收益 {d_half_ret:+.2f}% vs 全仓 {d_full_ret:+.2f}% "
        f"（Δ{delta:+.2f}pp）；成交 {r_half['成交笔数']} vs {r_full['成交笔数']}，"
        f"跳过 {r_half['跳过笔数']} vs {r_full['跳过笔数']}；"
        f"全仓为0而半仓仍有成交的选股日={zero_full_pos_half}天。"
        f"随机半仓均值 {r_rand_mean['收益%']:+.2f}% "
        f"（±{r_rand_mean['收益%_std']:.2f}）。"
    )
    if d_half_ret > d_full_ret:
        verdict += " 半仓因保留次日现金、分散进场日，本期收益优于全仓。"
    elif d_half_ret < d_full_ret:
        verdict += " 半仓本期收益低于全仓（仓位减半削弱单日暴露）。"
    else:
        verdict += " 半仓与全仓收益接近。"
    print(f"\n结论: {verdict}")

    fills_df = pd.concat(
        [r_full["_fills"], r_half["_fills"]], ignore_index=True
    )
    curve_df = pd.concat(
        [r_full["_curve"], r_half["_curve"]], ignore_index=True
    )

    meta = pd.DataFrame(
        [
            {"项": "方法", "值": "native 新规则215 · clip(2,4) · w=8 · 10万顺序回放"},
            {"项": "score", "值": f"Elig×{W}+标签内RS（无 max_per_tag）"},
            {"项": "Seff", "值": f"clip(S,{L_},{U_})；S=当日选股池只数"},
            {
                "项": "全仓",
                "值": "当日预算=1.0×equity(买前)；spend=min(budget/Seff,cash)",
            },
            {
                "项": "半仓",
                "值": f"当日预算={HALF_FRAC}×equity(买前)；spend=min(budget/Seff,cash)",
            },
            {
                "项": "资金回笼",
                "值": "持仓于 end_date 次一交易日释放 cost+pnl",
            },
            {
                "项": "可选对照",
                "值": f"半仓+随机排序 Cond123 取前Seff · {N_RANDOM_SEEDS} seeds 均值",
            },
            {"项": "结论", "值": verdict},
            {"项": "Δpp(半-全)", "值": f"{delta:+.4f}"},
            {"项": "生产默认", "值": f"EXPORT_ELIG_WEIGHT={DEFAULT_W}（本对比未改）"},
            {"项": "选股", "值": SEL.name},
            {"项": "生成时间", "值": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
        ]
    )

    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="汇总", index=False)
        daily_cmp.to_excel(w, sheet_name="按日成交", index=False)
        meta.to_excel(w, sheet_name="说明", index=False)
        if len(fills_df):
            fills_df.to_excel(w, sheet_name="成交明细", index=False)
        if len(curve_df):
            curve_df.to_excel(w, sheet_name="权益曲线", index=False)

    summary.to_csv(OUT_CSV_SUMMARY, index=False, encoding="utf-8-sig")
    daily_cmp.to_csv(OUT_CSV_DAILY, index=False, encoding="utf-8-sig")
    fills_df.to_csv(OUT_CSV_FILLS, index=False, encoding="utf-8-sig")

    payload = {
        "w": W,
        "clip": [L_, U_],
        "half_frac": HALF_FRAC,
        "full": strip_private(r_full),
        "half": strip_private(r_half),
        "half_random_mean": r_rand_mean,
        "delta_pp_half_minus_full": round(delta, 4),
        "days_full0_half_gt0": zero_full_pos_half,
        "verdict": verdict,
        "EXPORT_ELIG_WEIGHT_prod": DEFAULT_W,
        "out_xlsx": str(OUT_XLSX),
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nexported {OUT_XLSX.name}")
    print(f"  + {OUT_CSV_SUMMARY.name} / {OUT_CSV_DAILY.name} / {OUT_CSV_FILLS.name}")
    print(f"生产默认未改: EXPORT_ELIG_WEIGHT={DEFAULT_W}")


if __name__ == "__main__":
    main()
