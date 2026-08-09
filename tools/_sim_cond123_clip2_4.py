# -*- coding: utf-8 -*-
"""Cond123 (开盘夹档) anytag vs besttest — clip(L,U) offline replay.

Matches strategy_generator clip_equity + 开盘夹档产品逻辑 (see strategy_b14481fb /
tools/_sim_july_clip_100k.py):
  - S = 当日选股池只数（全量选股结果，非 Cond123 子集）— 仅用于仓位 sizing
  - Seff = clip(S, L, U) = max(L, min(U, S))
  - 当日最多买 Seff 只：在当日 Cond123 开盘夹档成交中，按强度分取前 Seff
    score = 合格榜内序位×EXPORT_ELIG_WEIGHT + 合格榜标签内RS排名（导出/clip 同序，无 max_per_tag）
  - 每笔 spend = min(equity/Seff, cash)；equity = cash + 锁定成本
  - 资金释放：end_date 次一交易日回笼本金+盈亏

Capital default 100_000（与既有 July clip 回放一致）。

Usage:
  python tools/_sim_cond123_clip2_4.py                    # default: 2,2 / 2,3 / 2,4 / 2,5 + 汇总对比
  python tools/_sim_cond123_clip2_4.py --clips 2,4        # single clip only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import akshare as ak
import numpy as np
import pandas as pd

_ROOT_PKG = Path(__file__).resolve().parents[1]
if str(_ROOT_PKG) not in sys.path:
    sys.path.insert(0, str(_ROOT_PKG))
from sector_stock_filter import EXPORT_ELIG_WEIGHT, clip_strength_sort_key  # noqa: E402

ROOT = Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门")
CAPITAL0 = 100_000.0
DEFAULT_CLIPS = [(2, 2), (2, 3), (2, 4), (2, 5)]
MULTI_SUMMARY_NAME = "Cond123_anytag_vs_besttest_clip2_2_2_3_2_4_2_5_对比.xlsx"

ANY_TRADES = (
    "开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_条件三MA5lt10lt20_各日选股收益汇总.xlsx"
)
BEST_TRADES = (
    "besttest_开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_条件三MA5lt10lt20_各日选股收益汇总.xlsx"
)
ANY_SEL = "选股结果_东财热门-anytag全量-无个股过滤_2026-07-01_2026-07-31.xls"
BEST_SEL = "选股结果_东财热门-besttest全量-无个股过滤_2026-07-01_2026-07-31.xls"


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


def clip_n(S: int, L_: int, U_: int) -> int:
    return int(max(L_, min(U_, S)))


def out_tag(L_: int, U_: int) -> str:
    return f"clip{L_}_{U_}"


def load_sel(path: Path):
    df = pd.read_excel(path)
    df["选股日"] = pd.to_datetime(df["选股日"]).dt.strftime("%Y-%m-%d")
    df["股票代码"] = df["股票代码"].map(code6)
    df = df[df["选股日"].str.startswith("2026-07")].copy()
    df["池序"] = df.groupby("选股日").cumcount()  # 导出行序（强度分）；仅对照用
    order = df.set_index(["选股日", "股票代码"])["池序"].to_dict()
    smap = df.groupby("选股日").size().to_dict()
    strength = {}
    for _, r in df.iterrows():
        strength[(r["选股日"], r["股票代码"])] = (
            r.get("合格榜内序位"),
            r.get("合格榜标签内RS排名"),
        )
    return df, order, smap, strength


def load_trades(path: Path, order: dict, strength: dict | None = None) -> pd.DataFrame:
    df = pd.read_excel(path)
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
    df["in_pool"] = df["池序"] < 9999
    # 强度字段：优先成交行，缺则回填选股池
    if "合格榜内序位" not in df.columns:
        df["合格榜内序位"] = np.nan
    if "合格榜标签内RS排名" not in df.columns:
        df["合格榜标签内RS排名"] = np.nan
    if strength:
        for i, r in df.iterrows():
            key = (r["选股日"], r["代码"])
            if key not in strength:
                continue
            elig, rs = strength[key]
            if pd.isna(r.get("合格榜内序位")):
                df.at[i, "合格榜内序位"] = elig
            if pd.isna(r.get("合格榜标签内RS排名")):
                df.at[i, "合格榜标签内RS排名"] = rs
    df["强度分"] = df.apply(
        lambda r: clip_strength_sort_key(
            r.get("合格榜内序位"),
            r.get("合格榜标签内RS排名"),
            r.get("代码"),
            elig_weight=EXPORT_ELIG_WEIGHT,
        )[0],
        axis=1,
    )
    return df


def keep_open_slots(g: pd.DataFrame, Seff: int) -> pd.DataFrame:
    """开盘夹档：按强度分截断至 Seff（≠ 导出池序 / max_per_tag）。"""
    opens = g[g["分支"] == "开盘夹档"].copy()
    opens["_sk"] = opens.apply(
        lambda r: clip_strength_sort_key(
            r.get("合格榜内序位"),
            r.get("合格榜标签内RS排名"),
            r.get("代码"),
            elig_weight=EXPORT_ELIG_WEIGHT,
        ),
        axis=1,
    )
    opens = opens.sort_values(["_sk", "t", "代码"]).drop(columns=["_sk"])
    return opens.head(Seff).reset_index(drop=True)


def max_dd(equity: pd.Series) -> float:
    if equity.empty:
        return float("nan")
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min() * 100.0)


def sim(trades: pd.DataFrame, smap: dict, L_: int, U_: int, capital0: float, label: str):
    df = trades[trades["in_pool"]].copy()
    cash = float(capital0)
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
                g2 = keep_open_slots(g, Seff)
                for _, r in g2.iterrows():
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
                    held.append(
                        {
                            "release_day": next_td(r["end_date"], 1),
                            "cost": spend,
                            "pnl": pnl,
                        }
                    )
                    fills.append(
                        {
                            "方案": label,
                            "选股日": sel_day,
                            "买入日": d,
                            "代码": r["代码"],
                            "股票名称": r.get("股票名称", ""),
                            "分支": r["分支"],
                            "池序": int(r["池序"]),
                            "强度分": int(r.get("强度分", 0)) if pd.notna(r.get("强度分")) else None,
                            "S": S,
                            "Seff": Seff,
                            "spend": spend,
                            "ret_pct": float(r["收益率pct"]),
                            "pnl": pnl,
                            "equity_at_buy": equity_now,
                            "cash_after": cash,
                            "end_date": r["end_date"],
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
                "cash": cash,
                "locked_cost": locked_cost,
                "equity": equity,
                "n_held": len(held),
            }
        )

    fills_df = pd.DataFrame(fills)
    skips_df = pd.DataFrame(skips)
    curve = pd.DataFrame(eq_curve)
    final = float(curve["equity"].iloc[-1]) if len(curve) else capital0
    ret_pct = (final / capital0 - 1.0) * 100.0

    stats = {
        "label": label,
        "clip": f"clip({L_},{U_})",
        "L": L_,
        "U": U_,
        "capital0": capital0,
        "final": final,
        "ret_pct": ret_pct,
        "n_fill": int(len(fills_df)),
        "n_skip": int(len(skips_df)),
        "n_universe": int(len(df)),
        "n_out_of_pool": int((~trades["in_pool"]).sum()),
        "mean_ret_pct": float(fills_df["ret_pct"].mean()) if len(fills_df) else float("nan"),
        "median_ret_pct": float(fills_df["ret_pct"].median()) if len(fills_df) else float("nan"),
        "winrate_pct": float((fills_df["ret_pct"] > 0).mean() * 100) if len(fills_df) else float("nan"),
        "sum_pnl": float(fills_df["pnl"].sum()) if len(fills_df) else 0.0,
        "mean_spend": float(fills_df["spend"].mean()) if len(fills_df) else float("nan"),
        "max_dd_pct": max_dd(curve["equity"]) if len(curve) else float("nan"),
        "trade_days": int(fills_df["选股日"].nunique()) if len(fills_df) else 0,
        "mean_fills_per_sel_day": float(fills_df.groupby("选股日").size().mean())
        if len(fills_df)
        else 0.0,
        "median_fills_per_sel_day": float(fills_df.groupby("选股日").size().median())
        if len(fills_df)
        else 0.0,
        "max_fills_per_sel_day": int(fills_df.groupby("选股日").size().max())
        if len(fills_df)
        else 0,
    }
    return {
        "stats": stats,
        "fills": fills_df,
        "skips": skips_df,
        "curve": curve,
    }


def day_stats(fills: pd.DataFrame, smap: dict, L_: int, U_: int) -> pd.DataFrame:
    rows = []
    for d in sorted(smap):
        if not d.startswith("2026-07"):
            continue
        S = int(smap[d])
        Seff = clip_n(S, L_, U_)
        fd = fills[fills["选股日"] == d] if len(fills) else fills
        n = len(fd)
        spend = float(fd["spend"].sum()) if n else 0.0
        pnl = float(fd["pnl"].sum()) if n else 0.0
        rows.append(
            {
                "选股日": d,
                "S": S,
                "Seff": Seff,
                "成交": n,
                "花费": spend,
                "盈亏": pnl,
                "日收益%": (pnl / spend * 100) if spend > 0 else 0.0,
                "均ret%": float(fd["ret_pct"].mean()) if n else float("nan"),
                "胜率%": float((fd["ret_pct"] > 0).mean() * 100) if n else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def _safe_write(path: Path, writer) -> bool:
    """Write via callable; skip with warning on PermissionError (file open in Excel)."""
    try:
        writer(path)
        return True
    except PermissionError:
        print(f"[WARN] 文件占用，跳过写入: {path.name}")
        return False


def export_clip(results: dict, L_: int, U_: int) -> Path:
    tag = out_tag(L_, U_)
    for key, r in results.items():
        prefix = f"Cond123_{key}_{tag}"
        _safe_write(
            ROOT / f"{prefix}_成交明细.csv",
            lambda p, fills=r["fills"]: fills.to_csv(p, index=False, encoding="utf-8-sig"),
        )
        _safe_write(
            ROOT / f"{prefix}_按日汇总.xlsx",
            lambda p, day=r["day"]: day.to_excel(p, index=False),
        )
        _safe_write(
            ROOT / f"{prefix}_权益曲线.csv",
            lambda p, curve=r["curve"]: curve.to_csv(p, index=False, encoding="utf-8-sig"),
        )
        skip_path = ROOT / f"{prefix}_跳过.csv"
        if len(r["skips"]):
            _safe_write(
                skip_path,
                lambda p, skips=r["skips"]: skips.to_csv(p, index=False, encoding="utf-8-sig"),
            )
        elif skip_path.exists():
            try:
                skip_path.unlink()
            except PermissionError:
                print(f"[WARN] 文件占用，跳过删除: {skip_path.name}")

    sa, sb = results["anytag"]["stats"], results["besttest"]["stats"]
    keys = [
        "label",
        "clip",
        "n_universe",
        "n_out_of_pool",
        "n_fill",
        "n_skip",
        "trade_days",
        "mean_fills_per_sel_day",
        "median_fills_per_sel_day",
        "max_fills_per_sel_day",
        "mean_ret_pct",
        "median_ret_pct",
        "winrate_pct",
        "mean_spend",
        "sum_pnl",
        "final",
        "ret_pct",
        "max_dd_pct",
    ]
    cmp = pd.DataFrame([{k: st[k] for k in keys} for st in (sa, sb)])
    da = results["anytag"]["day"].rename(
        columns={c: f"{c}_any" for c in results["anytag"]["day"].columns if c != "选股日"}
    )
    db = results["besttest"]["day"].rename(
        columns={c: f"{c}_best" for c in results["besttest"]["day"].columns if c != "选股日"}
    )
    daily = da.merge(db, on="选股日", how="outer").sort_values("选股日")
    daily["Δ盈亏_any-best"] = daily.get("盈亏_any", 0) - daily.get("盈亏_best", 0)

    out_xlsx = ROOT / f"Cond123_anytag_vs_besttest_{tag}_对比.xlsx"

    def _write_xlsx(p: Path):
        with pd.ExcelWriter(p, engine="openpyxl") as w:
            cmp.to_excel(w, sheet_name="汇总对比", index=False)
            daily.to_excel(w, sheet_name="按日对比", index=False)
            results["anytag"]["fills"].to_excel(w, sheet_name="anytag成交", index=False)
            results["besttest"]["fills"].to_excel(w, sheet_name="besttest成交", index=False)
            results["anytag"]["curve"].to_excel(w, sheet_name="anytag权益", index=False)
            results["besttest"]["curve"].to_excel(w, sheet_name="besttest权益", index=False)

    _safe_write(out_xlsx, _write_xlsx)

    meta = {
        "method": "offline_postprocess_clip_equity",
        "note": (
            "基于已有 Cond123 开盘夹档成交回放；非 GUI/QMT 全引擎重跑。"
            f"截断规则=强度分(Elig×{EXPORT_ELIG_WEIGHT}+标签内RS，不做 max_per_tag；"
            f"≠导出行序)；S=当日全量选股池只数；Seff=clip(S,{L_},{U_})；"
            "仓位=equity/Seff。"
        ),
        "L": L_,
        "U": U_,
        "capital0": CAPITAL0,
        "date_range": "2026-07-01..2026-07-31",
        "inputs": {
            "anytag_trades": ANY_TRADES,
            "besttest_trades": BEST_TRADES,
            "anytag_sel": ANY_SEL,
            "besttest_sel": BEST_SEL,
        },
        "stats": {"anytag": sa, "besttest": sb},
        "outputs": {
            "compare_xlsx": str(out_xlsx.name),
            "anytag_prefix": f"Cond123_anytag_{tag}_*",
            "besttest_prefix": f"Cond123_besttest_{tag}_*",
        },
    }
    meta_path = ROOT / f"Cond123_anytag_vs_besttest_{tag}_meta.json"
    _safe_write(
        meta_path,
        lambda p: p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"),
    )
    return out_xlsx


def fmt(st: dict) -> str:
    return (
        f"{st['label']:<10} clip({st['L']},{st['U']}) "
        f"期末={st['final']:.0f} 收益={st['ret_pct']:+.2f}% "
        f"成交={st['n_fill']} 跳过={st['n_skip']} "
        f"均ret={st['mean_ret_pct']:+.2f}% 中位ret={st['median_ret_pct']:+.2f}% "
        f"胜率={st['winrate_pct']:.1f}% 最大回撤={st['max_dd_pct']:.2f}% "
        f"日均笔={st['mean_fills_per_sel_day']:.2f}"
    )


def summary_row(st: dict) -> dict:
    return {
        "clip": st["clip"],
        "方案": st["label"],
        "L": st["L"],
        "U": st["U"],
        "期末权益": round(st["final"], 2),
        "收益%": round(st["ret_pct"], 4),
        "成交笔数": st["n_fill"],
        "跳过笔数": st["n_skip"],
        "均ret%": round(st["mean_ret_pct"], 4),
        "中位ret%": round(st["median_ret_pct"], 4),
        "胜率%": round(st["winrate_pct"], 2),
        "最大回撤%": round(st["max_dd_pct"], 4),
        "日均成交": round(st["mean_fills_per_sel_day"], 4),
        "交易日数": st["trade_days"],
        "日中位成交": round(st["median_fills_per_sel_day"], 2),
        "日最大成交": st["max_fills_per_sel_day"],
        "均仓位": round(st["mean_spend"], 2) if pd.notna(st["mean_spend"]) else None,
        "盈亏合计": round(st["sum_pnl"], 2),
    }


def write_multi_summary(all_stats: list[dict], clips: list[tuple[int, int]]) -> Path:
    rows = [summary_row(st) for st in all_stats]
    summary = pd.DataFrame(rows)

    # wide pivot: metrics × (clip, scheme)
    pivot_metrics = [
        "期末权益",
        "收益%",
        "成交笔数",
        "跳过笔数",
        "均ret%",
        "中位ret%",
        "胜率%",
        "最大回撤%",
        "日均成交",
    ]
    wide_rows = []
    for metric in pivot_metrics:
        row = {"指标": metric}
        for st in all_stats:
            col = f"{st['clip']}_{st['label']}"
            row[col] = summary_row(st)[metric]
        wide_rows.append(row)
    wide = pd.DataFrame(wide_rows)

    # delta besttest - anytag per clip
    delta_rows = []
    by_clip = {}
    for st in all_stats:
        by_clip.setdefault((st["L"], st["U"]), {})[st["label"]] = st
    for L_, U_ in clips:
        pair = by_clip.get((L_, U_), {})
        if "anytag" not in pair or "besttest" not in pair:
            continue
        a, b = pair["anytag"], pair["besttest"]
        delta_rows.append(
            {
                "clip": f"clip({L_},{U_})",
                "Δ收益pp_best-any": round(b["ret_pct"] - a["ret_pct"], 4),
                "Δ成交_best-any": b["n_fill"] - a["n_fill"],
                "Δ跳过_best-any": b["n_skip"] - a["n_skip"],
                "Δ均ret_pp": round(b["mean_ret_pct"] - a["mean_ret_pct"], 4),
                "Δ胜率_pp": round(b["winrate_pct"] - a["winrate_pct"], 2),
                "Δ最大回撤_pp": round(b["max_dd_pct"] - a["max_dd_pct"], 4),
                "Δ日均成交": round(b["mean_fills_per_sel_day"] - a["mean_fills_per_sel_day"], 4),
                "besttest收益%": round(b["ret_pct"], 4),
                "anytag收益%": round(a["ret_pct"], 4),
                "besttest期末": round(b["final"], 2),
                "anytag期末": round(a["final"], 2),
            }
        )
    delta = pd.DataFrame(delta_rows)

    tag_bits = "_".join(f"{L}_{U}" for L, U in clips)
    out_xlsx = ROOT / f"Cond123_anytag_vs_besttest_clip{tag_bits}_对比.xlsx"
    # preferred name when default clip set
    if clips == DEFAULT_CLIPS:
        out_xlsx = ROOT / MULTI_SUMMARY_NAME

    def _write_multi(p: Path):
        with pd.ExcelWriter(p, engine="openpyxl") as w:
            summary.to_excel(w, sheet_name="汇总对比", index=False)
            wide.to_excel(w, sheet_name="宽表指标", index=False)
            delta.to_excel(w, sheet_name="best相对any差额", index=False)

    _safe_write(out_xlsx, _write_multi)

    meta = {
        "method": "offline_postprocess_clip_equity",
        "clips": [f"clip({L},{U})" for L, U in clips],
        "capital0": CAPITAL0,
        "date_range": "2026-07-01..2026-07-31",
        "stats": all_stats,
        "compare_xlsx": out_xlsx.name,
    }
    meta_path = out_xlsx.with_name(out_xlsx.name.replace("对比.xlsx", "meta.json"))
    _safe_write(
        meta_path,
        lambda p: p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"),
    )
    return out_xlsx


def parse_clips(args: argparse.Namespace) -> list[tuple[int, int]]:
    if not args.clips:
        return list(DEFAULT_CLIPS)
    out = []
    for s in args.clips:
        parts = s.replace(" ", "").split(",")
        if len(parts) != 2:
            raise SystemExit(f"bad --clips value: {s!r}; expect L,U e.g. 2,4")
        out.append((int(parts[0]), int(parts[1])))
    return out


def main():
    ap = argparse.ArgumentParser(description="Cond123 clip(L,U) anytag vs besttest offline sim")
    ap.add_argument(
        "--clips",
        nargs="*",
        default=None,
        help="one or more L,U pairs (default: 2,2 2,3 2,4 2,5)",
    )
    args = ap.parse_args()
    clips = parse_clips(args)

    schemes = [
        ("anytag", ANY_SEL, ANY_TRADES),
        ("besttest", BEST_SEL, BEST_TRADES),
    ]
    # load once
    loaded = {}
    for key, sel_name, tr_name in schemes:
        _, order, smap, strength = load_sel(ROOT / sel_name)
        trades = load_trades(ROOT / tr_name, order, strength)
        loaded[key] = (trades, smap)

    all_stats: list[dict] = []
    for L_, U_ in clips:
        results = {}
        for key, (trades, smap) in loaded.items():
            r = sim(trades, smap, L_, U_, CAPITAL0, key)
            r["smap"] = smap
            r["day"] = day_stats(r["fills"], smap, L_, U_)
            results[key] = r
            all_stats.append(r["stats"])

        out_xlsx = export_clip(results, L_, U_)
        sa, sb = results["anytag"]["stats"], results["besttest"]["stats"]
        print(f"===== Cond123 clip({L_},{U_}) · 10万 · 离线回放 =====")
        print(
            f"规则: Seff=clip(S,{L_},{U_}); 按强度分(Elig×{EXPORT_ELIG_WEIGHT}+RS,"
            f"无max_per_tag)最多买 Seff 只开盘夹档; spend=min(equity/Seff,cash)"
        )
        print(fmt(sa))
        print(fmt(sb))
        print(
            f"Δ(any-best) 收益pp={sa['ret_pct']-sb['ret_pct']:+.2f} "
            f"成交差={sa['n_fill']-sb['n_fill']:+d} "
            f"均ret差={sa['mean_ret_pct']-sb['mean_ret_pct']:+.2f}pp "
            f"胜率差={sa['winrate_pct']-sb['winrate_pct']:+.1f}pp"
        )
        print(f"导出: {out_xlsx}")
        print()

    if len(clips) > 1:
        multi = write_multi_summary(all_stats, clips)
        print("===== 多 clip 汇总 =====")
        print(pd.DataFrame([summary_row(st) for st in all_stats]).to_string(index=False))
        print(f"\n汇总对比: {multi}")


if __name__ == "__main__":
    main()
