# -*- coding: utf-8 -*-
"""Compare clip(2,4)/(2,6)/(2,8) · w=8 (Elig×8+标签内RS) · native 新规则 215 · 100k sequential.

Does NOT change production defaults (EXPORT_ELIG_WEIGHT / clip_U untouched).

Data / ranking same as tools/_sweep_elig_weight_clip2_4.py:
  buys/sells/summary = 新规则 native 215
  selection = MA空头排列
  score = 合格榜内序位×8 + 合格榜标签内RS排名 (no max_per_tag)
  Seff = clip(S, L, U); S = daily full selection pool size

Usage:
  set PYTHONIOENCODING=utf-8
  python tools/_cmp_clip2_4_vs_2_8_w8.py
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
OUT_XLSX = ROOT / "clip2_4_2_6_2_8_w8_对比.xlsx"
OUT_JSON = ROOT / "clip2_4_2_6_2_8_w8_对比.json"
# keep prior filename as alias copy target for continuity
OUT_XLSX_LEGACY = ROOT / "clip2_4_vs_2_8_w8_对比.xlsx"
CAPITAL0 = 100_000.0
W = 8
CLIPS = [(2, 4), (2, 6), (2, 8)]
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


def sim_one(df: pd.DataFrame, smap: dict, L: int, U: int, w: int) -> dict:
    cash = float(CAPITAL0)
    held, fills, skips = [], [], []
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
                S = int(smap.get(sel_day, 0))
                if S <= 0:
                    continue
                Seff = clip_n(S, L, U)
                opens = g[g["分支"] == "开盘夹档"].copy()
                opens["_sk"] = opens.apply(
                    lambda r: clip_strength_sort_key(
                        r.get("合格榜内序位"),
                        r.get("合格榜标签内RS排名"),
                        r.get("代码"),
                        elig_weight=w,
                    ),
                    axis=1,
                )
                opens = opens.sort_values(["_sk", "t", "代码"]).drop(columns=["_sk"])
                g2 = opens.head(Seff).reset_index(drop=True)
                for _, r in g2.iterrows():
                    locked_cost = sum(p["cost"] for p in held)
                    equity_now = cash + locked_cost
                    target = equity_now / float(Seff)
                    spend = min(target, cash)
                    if spend < 1 or cash < spend * 0.99:
                        skips.append(1)
                        continue
                    cash -= spend
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
                            "clip": f"clip({L},{U})",
                            "选股日": sel_day,
                            "买入日": d,
                            "代码": r["代码"],
                            "spend": spend,
                            "ret_pct": float(r["收益率pct"]),
                            "pnl": pnl,
                            "S": S,
                            "Seff": Seff,
                            "合格榜内序位": r.get("合格榜内序位", np.nan),
                            "合格榜标签内RS排名": r.get("合格榜标签内RS排名", np.nan),
                        }
                    )

        locked_cost = sum(p["cost"] for p in held)
        locked_pnl = sum(p["pnl"] for p in held)
        equity = cash + locked_cost + locked_pnl
        eq_curve.append({"date": d, "equity": equity, "clip": f"clip({L},{U})"})

    fills_df = pd.DataFrame(fills)
    curve = pd.DataFrame(eq_curve)
    final = float(curve["equity"].iloc[-1]) if len(curve) else CAPITAL0
    ret_pct = (final / CAPITAL0 - 1.0) * 100.0
    n_fill = int(len(fills_df))
    return {
        "clip": f"clip({L},{U})",
        "L": L,
        "U": U,
        "w": w,
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


def main() -> None:
    if W != DEFAULT_W:
        print(f"NOTE: sim w={W} != prod EXPORT_ELIG_WEIGHT={DEFAULT_W} (prod untouched)")
    df, smap = load_universe()
    rows = []
    fills_all = []
    curves_all = []
    print(f"===== w={W} · clip(2,4)/(2,6)/(2,8) · capital={CAPITAL0:.0f} =====")
    print(f"score = Elig×{W} + 标签内RS；无 max_per_tag；S=全日选股池")

    for L, U in CLIPS:
        r = sim_one(df, smap, L, U, W)
        fills_all.append(r.pop("_fills"))
        curves_all.append(r.pop("_curve"))
        ret = r.pop("_ret_pct")
        rows.append(r)
        print(
            f"clip({L},{U})  收益={ret:+7.2f}%  成交={r['成交笔数']:3d}  跳过={r['跳过笔数']:3d}  "
            f"均ret={r['均ret%']:+6.2f}%  胜率={r['胜率%']:5.1f}%  "
            f"maxDD={r['最大回撤%']:7.2f}%  日均={r['日均成交']:.2f}"
        )

    summary = pd.DataFrame(rows)
    by_u = {int(r["U"]): float(r["收益%"]) for _, r in summary.iterrows()}
    r24, r26, r28 = by_u[4], by_u[6], by_u[8]
    d64, d84, d86 = r26 - r24, r28 - r24, r28 - r26
    best_u = max(by_u, key=by_u.get)
    verdict = (
        f"本 July 样本 clip(2,{best_u}) 最佳 "
        f"（相对(2,4): U6 {d64:+.2f}pp / U8 {d84:+.2f}pp；U8−U6={d86:+.2f}pp）"
    )
    print(verdict)

    fills_df = pd.concat(fills_all, ignore_index=True) if fills_all else pd.DataFrame()
    curve_df = pd.concat(curves_all, ignore_index=True) if curves_all else pd.DataFrame()

    # pick-set pairwise overlap
    keys = {}
    for (L, U), fd in zip(CLIPS, fills_all):
        keys[(L, U)] = set(zip(fd["选股日"], fd["代码"])) if len(fd) else set()
    pairs = [((2, 4), (2, 6)), ((2, 4), (2, 8)), ((2, 6), (2, 8))]
    overlap_rows = []
    for a, b in pairs:
        ka, kb = keys[a], keys[b]
        inter = ka & kb
        union = ka | kb
        overlap_rows.append(
            {
                "对比": f"clip{a[0]}{a[1]} vs clip{b[0]}{b[1]}",
                "A笔数": len(ka),
                "B笔数": len(kb),
                "交集": len(inter),
                "仅A": len(ka - kb),
                "仅B": len(kb - ka),
                "Jaccard": round(len(inter) / len(union), 4) if union else float("nan"),
            }
        )
    overlap = pd.DataFrame(overlap_rows)

    meta = pd.DataFrame(
        [
            {"项": "方法", "值": "native 新规则215 · w=8 · 10万顺序回放"},
            {"项": "score", "值": f"Elig×{W}+标签内RS（无 max_per_tag）"},
            {"项": "Seff", "值": "clip(S,L,U)；S=当日选股池只数"},
            {"项": "对比", "值": "clip(2,4) vs clip(2,6) vs clip(2,8)"},
            {"项": "结论", "值": verdict},
            {"项": "Δpp(6-4)", "值": f"{d64:+.4f}"},
            {"项": "Δpp(8-4)", "值": f"{d84:+.4f}"},
            {"项": "Δpp(8-6)", "值": f"{d86:+.4f}"},
            {"项": "生产默认", "值": f"EXPORT_ELIG_WEIGHT={DEFAULT_W}（本对比未改）"},
            {"项": "选股", "值": SEL.name},
            {"项": "生成时间", "值": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
        ]
    )

    for path in (OUT_XLSX, OUT_XLSX_LEGACY):
        with pd.ExcelWriter(path, engine="openpyxl") as w:
            summary.to_excel(w, sheet_name="汇总", index=False)
            overlap.to_excel(w, sheet_name="成交重叠", index=False)
            meta.to_excel(w, sheet_name="说明", index=False)
            if len(fills_df):
                fills_df.to_excel(w, sheet_name="成交明细", index=False)
            if len(curve_df):
                curve_df.to_excel(w, sheet_name="权益曲线", index=False)

    payload = {
        "w": W,
        "clips": rows,
        "delta_pp_U6_minus_U4": round(d64, 4),
        "delta_pp_U8_minus_U4": round(d84, 4),
        "delta_pp_U8_minus_U6": round(d86, 4),
        "best_U": best_u,
        "verdict": verdict,
        "EXPORT_ELIG_WEIGHT_prod": DEFAULT_W,
        "out": str(OUT_XLSX),
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "clip2_4_vs_2_8_w8_对比.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"exported {OUT_XLSX.name} (+ legacy {OUT_XLSX_LEGACY.name})")
    print(f"生产默认未改: EXPORT_ELIG_WEIGHT={DEFAULT_W}")


if __name__ == "__main__":
    main()
