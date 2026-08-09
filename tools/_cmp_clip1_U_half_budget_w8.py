# -*- coding: utf-8 -*-
"""Compare full-equity vs half-budget for clip(1,U) · U∈{1,2} · w=8.

Same setup as tools/_cmp_clip2_U_half_budget_w8.py:
  - Native 215 新规则 + selection MA空头
  - w=8 strength score, no max_per_tag
  - 100k sequential
  - Full: daily budget = 1.0*equity; spend=min(budget/Seff, cash); up to Seff
  - Half: daily budget = 0.5*equity; spend=min(budget/Seff, cash); up to Seff

Does NOT change production defaults.

Usage:
  set PYTHONIOENCODING=utf-8
  python tools/_cmp_clip1_U_half_budget_w8.py
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
OUT_XLSX = ROOT / "clip1_U_半仓_w8_对比.xlsx"
OUT_CSV_SUMMARY = ROOT / "clip1_U_半仓_w8_汇总.csv"
OUT_CSV_FILLS = ROOT / "clip1_U_半仓_w8_成交明细.csv"
OUT_JSON = ROOT / "clip1_U_半仓_w8_对比.json"
CAPITAL0 = 100_000.0
W = 8
L_ = 1
US = (1, 2)
HALF_FRAC = 0.5
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


def sim_one(
    df: pd.DataFrame,
    smap: dict,
    *,
    U: int,
    budget_frac: float,
    w: int = W,
) -> dict:
    """budget_frac=1.0 full equity; 0.5 half-budget. Seff=clip(S,L_,U)."""
    cash = float(CAPITAL0)
    held, fills, skips = [], [], []
    eq_curve = []
    all_days = [d for d in tdays if "2026-07-01" <= d <= "2026-08-05"]
    by_day = {d: g for d, g in df.groupby("买入日")}
    label = f"{'半仓' if budget_frac < 1 else '全仓'}_w{w}_clip({L_},{U})"

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
                Seff = clip_n(S, L_, U)
                opens = rank_opens_w8(g, w)
                if opens.empty:
                    continue
                g2 = opens.head(Seff).reset_index(drop=True)
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
                            "clip_U": U,
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
                "clip_U": U,
                "budget_frac": budget_frac,
                "equity_pre": equity_pre,
                "day_budget": day_budget,
                "day_spend": day_spend,
                "成交笔数": day_fills,
                "跳过笔数": day_skips,
            }
        )

    fills_df = pd.DataFrame(fills)
    curve = pd.DataFrame(eq_curve)
    final = float(curve["equity"].iloc[-1]) if len(curve) else CAPITAL0
    ret_pct = (final / CAPITAL0 - 1.0) * 100.0
    n_fill = int(len(fills_df))
    return {
        "方案": label,
        "clip": f"({L_},{U})",
        "clip_U": U,
        "budget": "半仓" if budget_frac < 1 else "全仓",
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
        "_ret_pct": ret_pct,
    }


def strip_private(r: dict) -> dict:
    return {k: v for k, v in r.items() if not k.startswith("_")}


def main() -> None:
    if W != DEFAULT_W:
        print(f"NOTE: sim w={W} != prod EXPORT_ELIG_WEIGHT={DEFAULT_W} (prod untouched)")
    df, smap = load_universe()

    print(
        f"===== clip(1,U) U={list(US)} · w={W} · capital={CAPITAL0:.0f} · "
        f"full vs half({HALF_FRAC}) ====="
    )
    print(f"score = Elig×{W}+标签内RS；无 max_per_tag；S=全日选股池")

    results: list[dict] = []
    for U in US:
        for frac, tag in ((1.0, "全仓"), (HALF_FRAC, "半仓")):
            print(f"----- clip(1,{U}) {tag} -----")
            r = sim_one(df, smap, U=U, budget_frac=frac, w=W)
            results.append(r)

    summary = pd.DataFrame([strip_private(r) for r in results])

    print("\n===== 4行对比表 =====")
    hdr = (
        f"{'clip':<8} {'仓位':<6} {'收益%':>8} {'成交':>6} {'跳过':>6} "
        f"{'均ret%':>8} {'胜率%':>7} {'maxDD%':>8}"
    )
    print(hdr)
    for r in results:
        print(
            f"{r['clip']:<8} {r['budget']:<6} {r['_ret_pct']:+8.2f} {r['成交笔数']:6d} {r['跳过笔数']:6d} "
            f"{r['均ret%']:+8.2f} {r['胜率%']:7.1f} {r['最大回撤%']:8.2f}"
        )

    print("\n===== 半仓 vs 全仓（按 U） =====")
    deltas = {}
    half_better_all = True
    for U in US:
        full = next(r for r in results if r["clip_U"] == U and r["budget_frac"] == 1.0)
        half = next(r for r in results if r["clip_U"] == U and r["budget_frac"] == HALF_FRAC)
        delta = half["_ret_pct"] - full["_ret_pct"]
        deltas[U] = delta
        better = half["_ret_pct"] > full["_ret_pct"]
        if not better:
            half_better_all = False
        mark = "半>全" if better else ("半≈全" if abs(delta) < 1e-9 else "半<全")
        print(
            f"  clip(1,{U}): 半仓 {half['_ret_pct']:+.2f}% vs 全仓 {full['_ret_pct']:+.2f}% "
            f"（Δ{delta:+.2f}pp）→ {mark}"
        )

    if half_better_all:
        verdict = (
            f"本期样本上，半仓在 U=1/2 均优于全仓"
            f"（Δpp: " + ", ".join(f"U={u} {deltas[u]:+.2f}" for u in US) + "）。"
        )
    else:
        worse = [u for u in US if deltas[u] <= 0]
        verdict = (
            f"半仓并非对所有 U 都更好：U={worse} 半仓未优于全仓；"
            f"Δpp: " + ", ".join(f"U={u} {deltas[u]:+.2f}" for u in US) + "。"
        )
    print(f"\n结论: {verdict}")

    fills_df = pd.concat([r["_fills"] for r in results], ignore_index=True)
    curve_df = pd.concat([r["_curve"] for r in results], ignore_index=True)

    pivot_rows = []
    for U in US:
        full = next(r for r in results if r["clip_U"] == U and r["budget_frac"] == 1.0)
        half = next(r for r in results if r["clip_U"] == U and r["budget_frac"] == HALF_FRAC)
        pivot_rows.append(
            {
                "clip": f"({L_},{U})",
                "全仓_收益%": full["收益%"],
                "半仓_收益%": half["收益%"],
                "Δpp(半-全)": round(half["_ret_pct"] - full["_ret_pct"], 4),
                "全仓_成交": full["成交笔数"],
                "半仓_成交": half["成交笔数"],
                "全仓_跳过": full["跳过笔数"],
                "半仓_跳过": half["跳过笔数"],
                "全仓_均ret%": full["均ret%"],
                "半仓_均ret%": half["均ret%"],
                "全仓_胜率%": full["胜率%"],
                "半仓_胜率%": half["胜率%"],
                "全仓_maxDD%": full["最大回撤%"],
                "半仓_maxDD%": half["最大回撤%"],
            }
        )
    pivot = pd.DataFrame(pivot_rows)

    meta = pd.DataFrame(
        [
            {"项": "方法", "值": "native 新规则215 · clip(1,U) U=1/2 · w=8 · 10万顺序回放"},
            {"项": "score", "值": f"Elig×{W}+标签内RS（无 max_per_tag）"},
            {"项": "Seff", "值": f"clip(S,{L_},U)；S=当日选股池只数"},
            {
                "项": "全仓",
                "值": "当日预算=1.0×equity(买前)；spend=min(budget/Seff,cash)",
            },
            {
                "项": "半仓",
                "值": f"当日预算={HALF_FRAC}×equity(买前)；spend=min(budget/Seff,cash)",
            },
            {"项": "资金回笼", "值": "持仓于 end_date 次一交易日释放 cost+pnl"},
            {"项": "结论", "值": verdict},
            {
                "项": "Δpp按U",
                "值": ", ".join(f"U={u}:{deltas[u]:+.4f}" for u in US),
            },
            {"项": "半仓是否U全优", "值": "是" if half_better_all else "否"},
            {"项": "生产默认", "值": f"EXPORT_ELIG_WEIGHT={DEFAULT_W}（本对比未改）"},
            {"项": "选股", "值": SEL.name},
            {"项": "生成时间", "值": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
        ]
    )

    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="汇总", index=False)
        pivot.to_excel(w, sheet_name="半全并排", index=False)
        meta.to_excel(w, sheet_name="说明", index=False)
        if len(fills_df):
            fills_df.to_excel(w, sheet_name="成交明细", index=False)
        if len(curve_df):
            curve_df.to_excel(w, sheet_name="权益曲线", index=False)

    summary.to_csv(OUT_CSV_SUMMARY, index=False, encoding="utf-8-sig")
    fills_df.to_csv(OUT_CSV_FILLS, index=False, encoding="utf-8-sig")

    payload = {
        "w": W,
        "L": L_,
        "Us": list(US),
        "half_frac": HALF_FRAC,
        "rows": [strip_private(r) for r in results],
        "deltas_pp_half_minus_full": {str(u): round(deltas[u], 4) for u in US},
        "half_better_all_U": half_better_all,
        "verdict": verdict,
        "EXPORT_ELIG_WEIGHT_prod": DEFAULT_W,
        "out_xlsx": str(OUT_XLSX),
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nexported {OUT_XLSX.name}")
    print(f"  + {OUT_CSV_SUMMARY.name} / {OUT_CSV_FILLS.name}")
    print(f"生产默认未改: EXPORT_ELIG_WEIGHT={DEFAULT_W}")


if __name__ == "__main__":
    main()
