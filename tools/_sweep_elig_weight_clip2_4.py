# -*- coding: utf-8 -*-
"""Sweep EXPORT_ELIG_WEIGHT 0..15 on native 新规则 215 · clip(2,4) · 100k sequential sim.

Does NOT change production EXPORT_ELIG_WEIGHT; only overrides elig_weight per run.

score = 合格榜内序位 * w + 合格榜标签内RS排名 (tiebreak elig, RS, code)
Seff = clip(S, 2, 4); top Seff by score among filled buys (no max_per_tag)

Usage:
  set PYTHONIOENCODING=utf-8
  python tools/_sweep_elig_weight_clip2_4.py
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
OUT_XLSX = ROOT / "clip2_4_Elig权重0to15扫描.xlsx"
CAPITAL0 = 100_000.0
L_, U_ = 2, 4
WEIGHTS = list(range(0, 16))
DEFAULT_W = int(EXPORT_ELIG_WEIGHT)  # expect 8

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
        f"S-days={len(smap)} default_w={DEFAULT_W}"
    )
    return df, smap


def sim_one(df: pd.DataFrame, smap: dict, w: int) -> dict:
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
                Seff = clip_n(S, L_, U_)
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
                            "w": w,
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
        eq_curve.append({"date": d, "equity": equity})

    fills_df = pd.DataFrame(fills)
    curve = pd.DataFrame(eq_curve)
    final = float(curve["equity"].iloc[-1]) if len(curve) else CAPITAL0
    ret_pct = (final / CAPITAL0 - 1.0) * 100.0
    n_fill = int(len(fills_df))
    return {
        "Elig权重w": w,
        "是否默认": "是(当前默认)" if w == DEFAULT_W else ("纯标签内RS" if w == 0 else ""),
        "期末权益": round(final, 2),
        "收益%": round(ret_pct, 4),
        "成交笔数": n_fill,
        "跳过笔数": int(len(skips)),
        "均ret%": round(float(fills_df["ret_pct"].mean()), 4) if n_fill else float("nan"),
        "中位ret%": round(float(fills_df["ret_pct"].median()), 4) if n_fill else float("nan"),
        "胜率%": round(float((fills_df["ret_pct"] > 0).mean() * 100), 2) if n_fill else float("nan"),
        "最大回撤%": round(max_dd(curve["equity"]), 4) if len(curve) else float("nan"),
        "盈亏合计": round(float(fills_df["pnl"].sum()), 2) if n_fill else 0.0,
        "均仓位": round(float(fills_df["spend"].mean()), 2) if n_fill else float("nan"),
        "交易日数": int(fills_df["选股日"].nunique()) if n_fill else 0,
        "日均成交": round(float(fills_df.groupby("选股日").size().mean()), 4) if n_fill else 0.0,
        "_fills": fills_df,
        "_curve": curve,
        "_ret_pct": ret_pct,
    }


def describe_curve(summary: pd.DataFrame) -> dict:
    rets = summary["收益%"].astype(float)
    best_i = int(rets.idxmax())
    worst_i = int(rets.idxmin())
    best_w = int(summary.loc[best_i, "Elig权重w"])
    worst_w = int(summary.loc[worst_i, "Elig权重w"])
    diffs = rets.diff().dropna()
    mono_up = bool((diffs >= -1e-9).all())
    mono_down = bool((diffs <= 1e-9).all())
    # peak: unique max then falls both sides, or plateau then drop
    peak_ws = summary.loc[rets == rets.max(), "Elig权重w"].astype(int).tolist()
    shape = "单调递增" if mono_up else ("单调递减" if mono_down else "非单调")
    if not mono_up and not mono_down:
        if len(peak_ws) == 1 and 0 < peak_ws[0] < 15:
            shape = f"单峰(峰≈w={peak_ws[0]})"
        elif len(peak_ws) > 1:
            shape = f"平台峰(w={peak_ws[0]}..{peak_ws[-1]})"
        else:
            shape = "非单调(端点最优)" if peak_ws[0] in (0, 15) else "非单调"
    def_row = summary.loc[summary["Elig权重w"] == DEFAULT_W].iloc[0]
    return {
        "best_w": best_w,
        "best_ret": float(summary.loc[best_i, "收益%"]),
        "worst_w": worst_w,
        "worst_ret": float(summary.loc[worst_i, "收益%"]),
        "default_w": DEFAULT_W,
        "default_ret": float(def_row["收益%"]),
        "w0_ret": float(summary.loc[summary["Elig权重w"] == 0, "收益%"].iloc[0]),
        "shape": shape,
        "peak_ws": peak_ws,
        "ret_range_pp": float(rets.max() - rets.min()),
        "n_unique_rets": int(rets.nunique()),
    }


def main() -> None:
    df, smap = load_universe()
    rows = []
    fills_by_w = {}
    print(f"===== sweep w=0..15 · clip({L_},{U_}) · capital={CAPITAL0:.0f} =====")
    print(f"score = Elig×w + 标签内RS；w=0=纯RS；w={DEFAULT_W}=当前默认")
    for w in WEIGHTS:
        r = sim_one(df, smap, w)
        fills_by_w[w] = r.pop("_fills")
        r.pop("_curve")
        ret = r.pop("_ret_pct")
        rows.append(r)
        mark = " ←默认" if w == DEFAULT_W else (" ←纯RS" if w == 0 else "")
        print(
            f"w={w:2d}  收益={ret:+7.2f}%  成交={r['成交笔数']:3d}  "
            f"均ret={r['均ret%']:+6.2f}%  胜率={r['胜率%']:5.1f}%  "
            f"maxDD={r['最大回撤%']:7.2f}%{mark}"
        )

    summary = pd.DataFrame(rows)
    curve_info = describe_curve(summary)
    summary["相对默认Δpp"] = (
        summary["收益%"] - float(summary.loc[summary["Elig权重w"] == DEFAULT_W, "收益%"].iloc[0])
    ).round(4)

    # pick-set overlap vs default w
    def_keys = set()
    if DEFAULT_W in fills_by_w and len(fills_by_w[DEFAULT_W]):
        fd = fills_by_w[DEFAULT_W]
        def_keys = set(zip(fd["选股日"], fd["代码"]))
    overlap_rows = []
    for w in WEIGHTS:
        fw = fills_by_w[w]
        keys = set(zip(fw["选股日"], fw["代码"])) if len(fw) else set()
        inter = keys & def_keys
        union = keys | def_keys
        overlap_rows.append(
            {
                "Elig权重w": w,
                "成交笔数": len(keys),
                "与默认w交集": len(inter),
                "仅本w": len(keys - def_keys),
                "仅默认": len(def_keys - keys),
                "Jaccard": round(len(inter) / len(union), 4) if union else float("nan"),
            }
        )
    overlap = pd.DataFrame(overlap_rows)

    note = pd.DataFrame(
        [
            {"项": "方法", "值": "native 新规则215 · clip(2,4) · 10万顺序回放"},
            {"项": "score", "值": "合格榜内序位×w + 合格榜标签内RS排名（tie: elig, RS, code）"},
            {"项": "Seff", "值": "clip(S,2,4)；S=当日选股池只数；无 max_per_tag"},
            {"项": "生产默认", "值": f"EXPORT_ELIG_WEIGHT={DEFAULT_W}（本扫描未改生产默认）"},
            {"项": "w=0含义", "值": "纯标签内RS（Elig 不进 score）"},
            {"项": "最优w", "值": f"w={curve_info['best_w']} 收益={curve_info['best_ret']:+.4f}%"},
            {"项": "最差w", "值": f"w={curve_info['worst_w']} 收益={curve_info['worst_ret']:+.4f}%"},
            {"项": "默认w收益", "值": f"w={curve_info['default_w']} 收益={curve_info['default_ret']:+.4f}%"},
            {"项": "曲线形态", "值": curve_info["shape"]},
            {"项": "收益跨度pp", "值": f"{curve_info['ret_range_pp']:.4f}"},
            {"项": "不同收益档数", "值": str(curve_info["n_unique_rets"])},
            {"项": "生成时间", "值": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
            {"项": "买入", "值": BUY.name},
            {"项": "汇总", "值": NATIVE_SUM.name},
            {"项": "选股", "值": SEL.name},
        ]
    )

    def _write(path: Path) -> Path:
        try:
            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                summary.to_excel(writer, sheet_name="扫描汇总", index=False)
                note.to_excel(writer, sheet_name="说明", index=False)
                overlap.to_excel(writer, sheet_name="相对默认取票重叠", index=False)
            return path
        except PermissionError:
            alt = path.with_name(path.stem + "_tmp" + path.suffix)
            with pd.ExcelWriter(alt, engine="openpyxl") as writer:
                summary.to_excel(writer, sheet_name="扫描汇总", index=False)
                note.to_excel(writer, sheet_name="说明", index=False)
                overlap.to_excel(writer, sheet_name="相对默认取票重叠", index=False)
            print(f"[WARN] locked {path.name}, wrote {alt.name}")
            return alt

    out = _write(OUT_XLSX)
    meta = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "EXPORT_ELIG_WEIGHT_prod": DEFAULT_W,
        "weights": WEIGHTS,
        "clip": f"clip({L_},{U_})",
        "capital0": CAPITAL0,
        "curve": curve_info,
        "output": out.name,
    }
    meta_path = out.with_suffix(".json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("===== 扫描结论 =====")
    print(summary[["Elig权重w", "是否默认", "收益%", "成交笔数", "均ret%", "胜率%", "最大回撤%", "相对默认Δpp"]].to_string(index=False))
    print()
    print(
        f"最优 w={curve_info['best_w']} ({curve_info['best_ret']:+.2f}%)  "
        f"最差 w={curve_info['worst_w']} ({curve_info['worst_ret']:+.2f}%)  "
        f"默认 w={curve_info['default_w']} ({curve_info['default_ret']:+.2f}%)  "
        f"w0纯RS={curve_info['w0_ret']:+.2f}%"
    )
    print(f"曲线形态: {curve_info['shape']}  跨度={curve_info['ret_range_pp']:.2f}pp  不同收益档={curve_info['n_unique_rets']}")
    print(f"导出: {out}")
    print(f"生产默认未改: EXPORT_ELIG_WEIGHT={DEFAULT_W}")


if __name__ == "__main__":
    main()
