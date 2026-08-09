# -*- coding: utf-8 -*-
"""Fixed-N=5: compare three buy triggers vs open-buy baseline.

Pre-open: same Cond123 open-clip picks + amounts (equity / Seff, Seff=min(5,n)).
During buy day (ticks):
  1) first cross above 昨MA10
  2) first cross below 昨MA5
  3) neither → buy at close (last tick <= 14:57)

Union: modes with portfolio ret > open baseline; first-touch among winners.

Usage:
  set PYTHONIOENCODING=utf-8
  python tools/_cmp_fixedN5_buy_triggers.py
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
OUT_DIR = ROOT / "并集拆空头_对比"
OUT_XLSX = OUT_DIR / "固定N5_买入触发三方式对比.xlsx"
OUT_JSON = OUT_DIR / "固定N5_买入触发三方式对比.json"
TICKS_ROOT = _ROOT_PKG / "data" / "ticks"

CAPITAL0 = 100_000.0
N_FIXED = 5
N_SEEDS = 100
W = int(EXPORT_ELIG_WEIGHT)
CLOSE_HM = 1457  # continuous auction end (exclude 14:57 auction)

SEL = ROOT / (
    "选股结果_东财热门-besttest-无涨停均线差0.5to2-MA空头排列_2026-07-01_2026-07-31.xls"
)
TRADES = ROOT / (
    "besttest_开盘夹档_条件一_无涨停_均线差0.5to2_"
    "条件二开盘相对MA5满足0to2_条件三MA5lt10lt20_各日选股收益汇总.xlsx"
)

MODE_OPEN = "开盘买入"
MODE_BRK10 = "突破昨MA10"
MODE_BD5 = "跌破昨MA5"
MODE_CLOSE = "夹档收盘"
MODE_UNION = "优胜并集"


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


def max_dd(equity: pd.Series) -> float:
    if equity.empty:
        return float("nan")
    peak = equity.cummax()
    return float((equity / peak - 1.0).min() * 100.0)


def _hm_from_ts(ts_ms: float) -> int:
    dt = datetime.fromtimestamp(float(ts_ms) / 1000.0)
    return int(dt.hour) * 100 + int(dt.minute)


def _hhmmss(ts_ms: float) -> str:
    dt = datetime.fromtimestamp(float(ts_ms) / 1000.0)
    return dt.strftime("%H:%M:%S")


def load_universe():
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

    tr = pd.read_excel(TRADES)
    tr["代码"] = tr["代码"].map(code6)
    tr["选股日"] = pd.to_datetime(tr["选股日"]).dt.strftime("%Y-%m-%d")
    tr["买入日"] = pd.to_datetime(tr["买入日"]).dt.strftime("%Y-%m-%d")
    tr["end_date"] = pd.to_datetime(tr["end_date"]).dt.strftime("%Y-%m-%d")
    tr["收益率pct"] = pd.to_numeric(tr["收益率pct"], errors="coerce")
    tr["买入成交价"] = pd.to_numeric(tr["买入成交价"], errors="coerce")
    tr["MA5"] = pd.to_numeric(tr.get("MA5"), errors="coerce")
    tr["MA10"] = pd.to_numeric(tr.get("MA10"), errors="coerce")
    tip = tr["触发信息"].astype(str) if "触发信息" in tr.columns else pd.Series([""] * len(tr))
    tr["分支"] = np.where(
        tip.str.contains("开盘买入"),
        "开盘夹档",
        np.where(tip.str.contains("单点买入"), "高开回踩", "其他"),
    )
    tr["t"] = tr["买入时间"].astype(str) if "买入时间" in tr.columns else ""
    tr["池序"] = tr.apply(lambda r: order.get((r["选股日"], r["代码"]), 9999), axis=1)
    tr["in_pool"] = tr["池序"] < 9999
    if "合格榜内序位" not in tr.columns:
        tr["合格榜内序位"] = np.nan
    if "合格榜标签内RS排名" not in tr.columns:
        tr["合格榜标签内RS排名"] = np.nan
    for i, r in tr.iterrows():
        key = (r["选股日"], r["代码"])
        if key not in strength:
            continue
        elig, rs = strength[key]
        if pd.isna(r.get("合格榜内序位")):
            tr.at[i, "合格榜内序位"] = elig
        if pd.isna(r.get("合格榜标签内RS排名")):
            tr.at[i, "合格榜标签内RS排名"] = rs
    df = tr[tr["in_pool"] & (tr["分支"] == "开盘夹档")].copy()
    df["exit_px"] = df["买入成交价"] * (1.0 + df["收益率pct"] / 100.0)
    return df, smap


def rank_strength(g: pd.DataFrame) -> pd.DataFrame:
    opens = g.copy()
    if opens.empty:
        return opens
    opens["_sk"] = opens.apply(
        lambda r: clip_strength_sort_key(
            r.get("合格榜内序位"),
            r.get("合格榜标签内RS排名"),
            r.get("代码"),
            elig_weight=W,
        ),
        axis=1,
    )
    return opens.sort_values(["_sk", "t", "代码"]).drop(columns=["_sk"])


def rank_random(g: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    opens = g.copy()
    if opens.empty:
        return opens
    return opens.iloc[rng.permutation(len(opens))].reset_index(drop=True)


def scan_ticks(code: str, buy_day: str, ma5: float, ma10: float) -> dict:
    """Scan buy-day ticks for MA10 breakout / MA5 breakdown / close fill."""
    out = {
        "tick_ok": False,
        "brk10_ts": None,
        "brk10_px": None,
        "brk10_t": "",
        "bd5_ts": None,
        "bd5_px": None,
        "bd5_t": "",
        "close_px": None,
        "close_t": "",
        "had_brk10": False,
        "had_bd5": False,
    }
    try:
        ma5 = float(ma5)
        ma10 = float(ma10)
    except (TypeError, ValueError):
        return out
    if not (ma5 > 0 and ma10 > 0):
        return out

    path = TICKS_ROOT / buy_day.replace("-", "") / f"{code}.parquet"
    if not path.is_file():
        return out
    try:
        df = pd.read_parquet(path, columns=["time_ts", "lastPrice", "ask1", "open"])
    except Exception:
        try:
            df = pd.read_parquet(path)
        except Exception:
            return out
    if df is None or len(df) == 0:
        return out

    ts = pd.to_numeric(df["time_ts"], errors="coerce")
    last = pd.to_numeric(df["lastPrice"], errors="coerce")
    ask = (
        pd.to_numeric(df["ask1"], errors="coerce")
        if "ask1" in df.columns
        else pd.Series([np.nan] * len(df))
    )
    m = ts.notna() & last.notna() & (last > 0)
    if not bool(m.any()):
        return out
    df = df.loc[m].copy()
    df["_ts"] = ts[m]
    df["_last"] = last[m]
    df["_ask"] = ask[m]
    df["_hm"] = df["_ts"].map(_hm_from_ts)
    df = df[(df["_hm"] >= 930) & (df["_hm"] <= CLOSE_HM)].reset_index(drop=True)
    if df.empty:
        return out

    out["tick_ok"] = True

    def fill_px(row) -> float:
        a = row["_ask"]
        try:
            if a == a and float(a) > 0:
                return float(a)
        except (TypeError, ValueError):
            pass
        return float(row["_last"])

    # init prev from session open if available
    prev = None
    try:
        o0 = float(df.iloc[0]["open"]) if "open" in df.columns else float("nan")
        if o0 == o0 and o0 > 0:
            prev = o0
    except (TypeError, ValueError):
        prev = None

    close_row = None
    for _, row in df.iterrows():
        px = float(row["_last"])
        if prev is not None:
            if (not out["had_brk10"]) and prev <= ma10 and px > ma10:
                out["had_brk10"] = True
                out["brk10_ts"] = float(row["_ts"])
                out["brk10_px"] = fill_px(row)
                out["brk10_t"] = _hhmmss(float(row["_ts"]))
            if (not out["had_bd5"]) and prev >= ma5 and px < ma5:
                out["had_bd5"] = True
                out["bd5_ts"] = float(row["_ts"])
                out["bd5_px"] = fill_px(row)
                out["bd5_t"] = _hhmmss(float(row["_ts"]))
        prev = px
        close_row = row

    if close_row is not None:
        out["close_px"] = fill_px(close_row)
        out["close_t"] = _hhmmss(float(close_row["_ts"]))
    return out


_SCAN_COLS = [
    "tick_ok",
    "had_brk10",
    "had_bd5",
    "brk10_px",
    "brk10_t",
    "brk10_ts",
    "bd5_px",
    "bd5_t",
    "bd5_ts",
    "close_px",
    "close_t",
    "夹档内收盘",
]


def enrich_universe_scans(df: pd.DataFrame) -> pd.DataFrame:
    """Attach tick-scan fields to every open-clip row (cache once for random)."""
    out = df.copy()
    for col in _SCAN_COLS:
        out[col] = None if col not in ("tick_ok", "had_brk10", "had_bd5", "夹档内收盘") else False
    for i, r in out.iterrows():
        scan = scan_ticks(r["代码"], r["买入日"], r.get("MA5"), r.get("MA10"))
        out.at[i, "tick_ok"] = scan["tick_ok"]
        out.at[i, "had_brk10"] = scan["had_brk10"]
        out.at[i, "had_bd5"] = scan["had_bd5"]
        out.at[i, "brk10_px"] = scan["brk10_px"]
        out.at[i, "brk10_t"] = scan["brk10_t"]
        out.at[i, "brk10_ts"] = scan["brk10_ts"]
        out.at[i, "bd5_px"] = scan["bd5_px"]
        out.at[i, "bd5_t"] = scan["bd5_t"]
        out.at[i, "bd5_ts"] = scan["bd5_ts"]
        out.at[i, "close_px"] = scan["close_px"]
        out.at[i, "close_t"] = scan["close_t"]
        out.at[i, "夹档内收盘"] = bool(
            scan["tick_ok"]
            and (not scan["had_brk10"])
            and (not scan["had_bd5"])
            and scan["close_px"] is not None
        )
    out["open_px"] = pd.to_numeric(out["买入成交价"], errors="coerce")
    out["open_ret_pct"] = pd.to_numeric(out["收益率pct"], errors="coerce")
    return out


def build_candidates(
    univ: pd.DataFrame,
    smap: dict,
    *,
    rank_mode: str = "strength",
    seed: int | None = None,
) -> pd.DataFrame:
    """Pre-open N=5 picks; tick fields already on univ."""
    rows = []
    by_buy = {d: g for d, g in univ.groupby("买入日")}
    all_days = [d for d in tdays if "2026-07-01" <= d <= "2026-08-05"]
    rng = np.random.default_rng(seed) if seed is not None else None
    for d in all_days:
        if d not in by_buy:
            continue
        for sel_day, g in by_buy[d].groupby("选股日", sort=False):
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
            picks = ranked.head(Seff).reset_index(drop=True)
            for _, r in picks.iterrows():
                rows.append(
                    {
                        "选股日": sel_day,
                        "买入日": d,
                        "end_date": r["end_date"],
                        "代码": r["代码"],
                        "股票名称": r.get("股票名称", ""),
                        "S": S,
                        "Seff": Seff,
                        "MA5": float(r["MA5"]) if pd.notna(r.get("MA5")) else float("nan"),
                        "MA10": float(r["MA10"]) if pd.notna(r.get("MA10")) else float("nan"),
                        "open_px": float(r["open_px"]) if pd.notna(r.get("open_px")) else float("nan"),
                        "open_ret_pct": float(r["open_ret_pct"])
                        if pd.notna(r.get("open_ret_pct"))
                        else float("nan"),
                        "exit_px": float(r["exit_px"]) if pd.notna(r.get("exit_px")) else float("nan"),
                        "tick_ok": bool(r["tick_ok"]),
                        "had_brk10": bool(r["had_brk10"]),
                        "had_bd5": bool(r["had_bd5"]),
                        "brk10_px": r["brk10_px"],
                        "brk10_t": r["brk10_t"],
                        "brk10_ts": r["brk10_ts"],
                        "bd5_px": r["bd5_px"],
                        "bd5_t": r["bd5_t"],
                        "bd5_ts": r["bd5_ts"],
                        "close_px": r["close_px"],
                        "close_t": r["close_t"],
                        "夹档内收盘": bool(r["夹档内收盘"]),
                    }
                )
    return pd.DataFrame(rows)


def _rand_stats(arr: list[float]) -> dict:
    a = np.asarray(arr, dtype=float)
    return {
        "n": int(len(a)),
        "mean": float(np.mean(a)) if len(a) else float("nan"),
        "p10": float(np.percentile(a, 10)) if len(a) else float("nan"),
        "p50": float(np.percentile(a, 50)) if len(a) else float("nan"),
        "p90": float(np.percentile(a, 90)) if len(a) else float("nan"),
        "gt0_pct": float((a > 0).mean() * 100) if len(a) else float("nan"),
    }


def _ret_from_fill(exit_px: float, fill_px: float) -> float | None:
    if fill_px is None or exit_px is None:
        return None
    try:
        fp = float(fill_px)
        ep = float(exit_px)
    except (TypeError, ValueError):
        return None
    if not (fp > 0 and ep > 0):
        return None
    return (ep / fp - 1.0) * 100.0


def resolve_fill(row: pd.Series, mode: str, winner_modes: set[str] | None = None):
    """Return (fill_px, fill_t, trigger_label) or (None, '', reason)."""
    exit_px = row["exit_px"]
    if mode == MODE_OPEN:
        px = row["open_px"]
        if px == px and float(px) > 0:
            return float(px), "09:30:00", MODE_OPEN
        return None, "", "无开盘价"

    if not bool(row["tick_ok"]):
        return None, "", "缺分时"

    if mode == MODE_BRK10:
        if row["had_brk10"] and row["brk10_px"] is not None:
            return float(row["brk10_px"]), str(row["brk10_t"]), MODE_BRK10
        return None, "", "未突破MA10"

    if mode == MODE_BD5:
        if row["had_bd5"] and row["bd5_px"] is not None:
            return float(row["bd5_px"]), str(row["bd5_t"]), MODE_BD5
        return None, "", "未跌破MA5"

    if mode == MODE_CLOSE:
        if bool(row["夹档内收盘"]) and row["close_px"] is not None:
            return float(row["close_px"]), str(row["close_t"]), MODE_CLOSE
        if row["had_brk10"] or row["had_bd5"]:
            return None, "", "非夹档全日"
        return None, "", "无收盘价"

    if mode == MODE_UNION:
        assert winner_modes is not None
        events = []
        if MODE_BRK10 in winner_modes and row["had_brk10"] and row["brk10_px"] is not None:
            events.append((float(row["brk10_ts"]), float(row["brk10_px"]), str(row["brk10_t"]), MODE_BRK10))
        if MODE_BD5 in winner_modes and row["had_bd5"] and row["bd5_px"] is not None:
            events.append((float(row["bd5_ts"]), float(row["bd5_px"]), str(row["bd5_t"]), MODE_BD5))
        if events:
            events.sort(key=lambda x: x[0])
            _, px, t, lab = events[0]
            return px, t, lab
        if MODE_CLOSE in winner_modes and bool(row["夹档内收盘"]) and row["close_px"] is not None:
            return float(row["close_px"]), str(row["close_t"]), MODE_CLOSE
        return None, "", "并集未覆盖"

    return None, "", "未知模式"


def sim_mode(
    cands: pd.DataFrame,
    mode: str,
    *,
    winner_modes: set[str] | None = None,
) -> dict:
    cash = float(CAPITAL0)
    held, fills, skips = [], [], []
    eq_curve = []
    all_days = [d for d in tdays if "2026-07-01" <= d <= "2026-08-05"]
    by_day = {d: g for d, g in cands.groupby("买入日")}

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

        if d in by_day:
            for sel_day, g in by_day[d].groupby("选股日", sort=False):
                Seff = int(g["Seff"].iloc[0]) if len(g) else 0
                if Seff <= 0:
                    continue
                # amounts fixed pre-open from equity / Seff (same for all reserved slots)
                target = equity_pre / float(Seff)
                for _, r in g.iterrows():
                    fill_px, fill_t, trig_or_reason = resolve_fill(r, mode, winner_modes)
                    if fill_px is None:
                        skips.append(
                            {
                                "mode": mode,
                                "选股日": sel_day,
                                "买入日": d,
                                "代码": r["代码"],
                                "skip_reason": trig_or_reason,
                            }
                        )
                        continue
                    ret_pct = _ret_from_fill(float(r["exit_px"]), fill_px)
                    if ret_pct is None:
                        skips.append(
                            {
                                "mode": mode,
                                "选股日": sel_day,
                                "买入日": d,
                                "代码": r["代码"],
                                "skip_reason": "无法算收益",
                            }
                        )
                        continue
                    spend = min(target, cash)
                    if spend < 1 or cash < spend * 0.99:
                        skips.append(
                            {
                                "mode": mode,
                                "选股日": sel_day,
                                "买入日": d,
                                "代码": r["代码"],
                                "skip_reason": "现金不足",
                            }
                        )
                        continue
                    cash -= spend
                    pnl = spend * (ret_pct / 100.0)
                    held.append(
                        {
                            "release_day": next_td(str(r["end_date"]), 1),
                            "cost": spend,
                            "pnl": pnl,
                        }
                    )
                    fills.append(
                        {
                            "mode": mode,
                            "触发": trig_or_reason,
                            "选股日": sel_day,
                            "买入日": d,
                            "end_date": r["end_date"],
                            "代码": r["代码"],
                            "股票名称": r.get("股票名称", ""),
                            "S": r["S"],
                            "Seff": Seff,
                            "target": target,
                            "spend": spend,
                            "fill_px": fill_px,
                            "fill_t": fill_t,
                            "open_px": r["open_px"],
                            "exit_px": r["exit_px"],
                            "ret_pct": ret_pct,
                            "open_ret_pct": r["open_ret_pct"],
                            "pnl": pnl,
                            "MA5": r["MA5"],
                            "MA10": r["MA10"],
                        }
                    )

        locked_cost = sum(p["cost"] for p in held)
        locked_pnl = sum(p["pnl"] for p in held)
        equity = cash + locked_cost + locked_pnl
        eq_curve.append({"date": d, "equity": equity, "mode": mode})

    fills_df = pd.DataFrame(fills)
    skips_df = pd.DataFrame(skips)
    curve = pd.DataFrame(eq_curve)
    final = float(curve["equity"].iloc[-1]) if len(curve) else CAPITAL0
    ret_pct = (final / CAPITAL0 - 1.0) * 100.0
    n = len(fills_df)
    return {
        "mode": mode,
        "final": final,
        "ret_pct": ret_pct,
        "n_fill": n,
        "n_skip": len(skips_df),
        "mean_ret_pct": float(fills_df["ret_pct"].mean()) if n else float("nan"),
        "winrate_pct": float((fills_df["ret_pct"] > 0).mean() * 100) if n else float("nan"),
        "max_dd_pct": max_dd(curve["equity"]) if len(curve) else float("nan"),
        "mean_spend": float(fills_df["spend"].mean()) if n else float("nan"),
        "trade_days": int(fills_df["选股日"].nunique()) if n else 0,
        "_fills": fills_df,
        "_skips": skips_df,
        "_curve": curve,
    }


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    print("loading universe…")
    df, smap = load_universe()
    print(f"open-clip in_pool rows={len(df)} S-days={len(smap)}")

    print("scanning ticks for full open-clip universe…")
    univ = enrich_universe_scans(df)
    print(
        f"universe tick_ok={int(univ['tick_ok'].sum())}/{len(univ)} "
        f"brk10={int(univ['had_brk10'].sum())} bd5={int(univ['had_bd5'].sum())} "
        f"close_band={int(univ['夹档内收盘'].sum())}"
    )

    print("strength N=5 candidates…")
    cands = build_candidates(univ, smap, rank_mode="strength")
    print(
        f"candidates={len(cands)} tick_ok={int(cands['tick_ok'].sum())} "
        f"brk10={int(cands['had_brk10'].sum())} bd5={int(cands['had_bd5'].sum())} "
        f"close_band={int(cands['夹档内收盘'].sum())}"
    )

    results = {}
    for mode in (MODE_OPEN, MODE_BRK10, MODE_BD5, MODE_CLOSE):
        print(f"sim {mode}…")
        results[mode] = sim_mode(cands, mode)
        st = results[mode]
        print(
            f"  ret={st['ret_pct']:+.2f}% fills={st['n_fill']} skip={st['n_skip']} "
            f"mean={st['mean_ret_pct']:.2f}% win={st['winrate_pct']:.1f}% "
            f"dd={st['max_dd_pct']:.2f}%"
        )

    base_ret = results[MODE_OPEN]["ret_pct"]
    winners = [
        m
        for m in (MODE_BRK10, MODE_BD5, MODE_CLOSE)
        if results[m]["ret_pct"] > base_ret
    ]
    union_note = ""
    if winners:
        union_note = "优于开盘基线: " + " + ".join(winners)
        print(f"union winners: {winners}")
        results[MODE_UNION] = sim_mode(cands, MODE_UNION, winner_modes=set(winners))
    else:
        best = max((MODE_BRK10, MODE_BD5, MODE_CLOSE), key=lambda m: results[m]["ret_pct"])
        union_note = f"无一优于开盘基线；并集不硬凑，最佳单模式={best}"
        print(union_note)
        results[MODE_UNION] = {
            **{k: v for k, v in results[best].items() if not k.startswith("_")},
            "mode": MODE_UNION,
            "degenerate_to": best,
            "_fills": results[best]["_fills"].assign(mode=MODE_UNION, 触发=best)
            if len(results[best]["_fills"])
            else pd.DataFrame(),
            "_skips": results[best]["_skips"].assign(mode=MODE_UNION)
            if len(results[best]["_skips"])
            else pd.DataFrame(),
            "_curve": results[best]["_curve"].assign(mode=MODE_UNION)
            if len(results[best]["_curve"])
            else pd.DataFrame(),
        }

    st_u = results[MODE_UNION]
    print(
        f"sim {MODE_UNION}… ret={st_u['ret_pct']:+.2f}% fills={st_u['n_fill']} "
        f"dd={st_u['max_dd_pct']:.2f}% | {union_note}"
    )

    # --- random shuffle sensitivity (same seed → same picks for all modes) ---
    print(f"random sensitivity seeds={N_SEEDS}…")
    rand_modes = (MODE_OPEN, MODE_BRK10, MODE_BD5, MODE_CLOSE)
    rand_rets = {m: [] for m in rand_modes}
    rand_dds = {m: [] for m in rand_modes}
    rand_delta = []  # bd5 - open
    seed_rows = []
    for s in range(N_SEEDS):
        rc = build_candidates(univ, smap, rank_mode="random", seed=s)
        day_rets = {}
        for mode in rand_modes:
            st = sim_mode(rc, mode)
            # drop heavy frames
            day_rets[mode] = st["ret_pct"]
            rand_rets[mode].append(st["ret_pct"])
            rand_dds[mode].append(st["max_dd_pct"])
        dlt = day_rets[MODE_BD5] - day_rets[MODE_OPEN]
        rand_delta.append(dlt)
        seed_rows.append(
            {
                "seed": s,
                "开盘买入": day_rets[MODE_OPEN],
                "突破昨MA10": day_rets[MODE_BRK10],
                "跌破昨MA5": day_rets[MODE_BD5],
                "夹档收盘": day_rets[MODE_CLOSE],
                "跌破减开盘pp": dlt,
            }
        )
        if (s + 1) % 20 == 0:
            print(f"  seed {s + 1}/{N_SEEDS}")

    rand_summary_rows = []
    for mode in rand_modes:
        rs = _rand_stats(rand_rets[mode])
        ds = _rand_stats(rand_dds[mode])
        strength_ret = results[mode]["ret_pct"]
        rand_summary_rows.append(
            {
                "模式": mode,
                "强度序收益pct": round(strength_ret, 4),
                "随机均值pct": round(rs["mean"], 4),
                "随机p10pct": round(rs["p10"], 4),
                "随机p50pct": round(rs["p50"], 4),
                "随机p90pct": round(rs["p90"], 4),
                "随机>0占比pct": round(rs["gt0_pct"], 2),
                "随机回撤均值pct": round(ds["mean"], 4),
                "随机回撤p10pct": round(ds["p10"], 4),
            }
        )
        print(
            f"  random {mode}: mean={rs['mean']:+.2f}% p10={rs['p10']:+.2f}% "
            f">0={rs['gt0_pct']:.0f}% | strength={strength_ret:+.2f}%"
        )

    delta_stats = _rand_stats(rand_delta)
    bd5_beats_open_pct = float((np.asarray(rand_delta) > 0).mean() * 100)
    print(
        f"  跌破-开盘: mean={delta_stats['mean']:+.2f}pp p10={delta_stats['p10']:+.2f}pp "
        f"跌破>开盘占比={bd5_beats_open_pct:.0f}%"
    )

    mix = {
        "candidates": int(len(cands)),
        "tick_ok": int(cands["tick_ok"].sum()),
        "had_brk10": int(cands["had_brk10"].sum()),
        "had_bd5": int(cands["had_bd5"].sum()),
        "both_events": int((cands["had_brk10"] & cands["had_bd5"]).sum()),
        "夹档内收盘": int(cands["夹档内收盘"].sum()),
        "brk10_only": int((cands["had_brk10"] & ~cands["had_bd5"]).sum()),
        "bd5_only": int((cands["had_bd5"] & ~cands["had_brk10"]).sum()),
    }

    summary_rows = []
    for mode in (MODE_OPEN, MODE_BRK10, MODE_BD5, MODE_CLOSE, MODE_UNION):
        st = results[mode]
        summary_rows.append(
            {
                "模式": mode,
                "组合收益pct": round(st["ret_pct"], 4),
                "vs开盘pp": round(st["ret_pct"] - base_ret, 4),
                "最终权益": round(st["final"], 2),
                "成交笔数": st["n_fill"],
                "跳过": st["n_skip"],
                "笔均收益pct": round(st["mean_ret_pct"], 4) if st["n_fill"] else None,
                "胜率pct": round(st["winrate_pct"], 2) if st["n_fill"] else None,
                "最大回撤pct": round(st["max_dd_pct"], 4)
                if st["n_fill"] or mode == MODE_OPEN
                else None,
                "交易选股日数": st["trade_days"],
                "笔均金额": round(st["mean_spend"], 2) if st["n_fill"] else None,
                "备注": union_note if mode == MODE_UNION else "",
            }
        )
    summary = pd.DataFrame(summary_rows)
    rand_summary = pd.DataFrame(rand_summary_rows)
    seed_df = pd.DataFrame(seed_rows)

    fills_all = pd.concat(
        [results[m]["_fills"] for m in results if len(results[m]["_fills"])],
        ignore_index=True,
    )
    curves_all = pd.concat(
        [results[m]["_curve"] for m in results if len(results[m]["_curve"])],
        ignore_index=True,
    )
    skips_all = pd.concat(
        [
            results[m]["_skips"]
            for m in results
            if len(results[m].get("_skips", pd.DataFrame()))
        ],
        ignore_index=True,
    )

    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        summary.to_excel(w, sheet_name="汇总", index=False)
        rand_summary.to_excel(w, sheet_name="随机敏感性汇总", index=False)
        seed_df.to_excel(w, sheet_name="随机各seed", index=False)
        cands.to_excel(w, sheet_name="入选扫描", index=False)
        fills_all.to_excel(w, sheet_name="成交明细", index=False)
        curves_all.to_excel(w, sheet_name="权益曲线", index=False)
        if len(skips_all):
            skips_all.to_excel(w, sheet_name="跳过明细", index=False)
        pd.DataFrame([mix]).to_excel(w, sheet_name="触发占比", index=False)

    meta = {
        "capital0": CAPITAL0,
        "fixed_n": N_FIXED,
        "n_seeds": N_SEEDS,
        "base_ret_pct": base_ret,
        "winners": winners,
        "union_note": union_note,
        "mix": mix,
        "summary": summary_rows,
        "random_summary": rand_summary_rows,
        "random_bd5_minus_open": {
            **{k: round(v, 4) if isinstance(v, float) else v for k, v in delta_stats.items()},
            "bd5_beats_open_pct": round(bd5_beats_open_pct, 2),
        },
        "curves": {
            m: results[m]["_curve"][["date", "equity"]].to_dict(orient="records")
            for m in (MODE_OPEN, MODE_BRK10, MODE_BD5, MODE_CLOSE, MODE_UNION)
            if len(results[m]["_curve"])
        },
    }
    OUT_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT_XLSX}")
    print(f"wrote {OUT_JSON}")
    print(summary.to_string(index=False))
    print(rand_summary.to_string(index=False))


if __name__ == "__main__":
    main()
