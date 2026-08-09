# -*- coding: utf-8 -*-
"""Random-sort sweep for native 新规则 215 · clip(2,4) · 100k sequential sim.

Among each day's filled Cond123 (开盘夹档) buys: shuffle by seed, take first Seff.
Optional: same among full day's selection∩fills (all branches).

Does NOT change production defaults (EXPORT_ELIG_WEIGHT untouched).

Usage:
  set PYTHONIOENCODING=utf-8
  python tools/_sweep_random_sort_clip2_4.py
  python tools/_sweep_random_sort_clip2_4.py --n-seeds 500
"""
from __future__ import annotations

import argparse
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
from sector_stock_filter import EXPORT_ELIG_WEIGHT  # noqa: E402

ROOT = Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门")
OUT_XLSX = ROOT / "clip2_4_随机排序扫描.xlsx"
CAPITAL0 = 100_000.0
L_, U_ = 2, 4
DEFAULT_W = int(EXPORT_ELIG_WEIGHT)  # expect 8; reference only
REF_W0 = 7.72
REF_W8 = 1.78
REF_FILE_ORDER = 8.75

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
    n_cond = int((df["分支"] == "开盘夹档").sum())
    print(
        f"universe buys={len(buy)} in_pool={len(df)} Cond123={n_cond} "
        f"out={int((~m['in_pool']).sum())} S-days={len(smap)} "
        f"prod_w={DEFAULT_W}(未改)"
    )
    return df, smap


def pick_candidates(g: pd.DataFrame, mode: str) -> pd.DataFrame:
    """mode: 'cond123' = 开盘夹档 only; 'all_fills' = full selection∩fills that day."""
    if mode == "cond123":
        return g[g["分支"] == "开盘夹档"].copy()
    if mode == "all_fills":
        return g.copy()
    raise ValueError(f"unknown mode={mode}")


def sim_one(df: pd.DataFrame, smap: dict, seed: int, mode: str) -> dict:
    cash = float(CAPITAL0)
    held, fills, skips = [], [], []
    eq_curve = []
    all_days = [d for d in tdays if "2026-07-01" <= d <= "2026-08-05"]
    by_day = {d: g for d, g in df.groupby("买入日")}
    rng = np.random.default_rng(seed)
    day_i = 0

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
                cands = pick_candidates(g, mode)
                if cands.empty:
                    continue
                # per (day, sel_day) sub-seed for reproducibility across modes
                day_i += 1
                sub = np.random.default_rng(int(rng.integers(0, 2**31 - 1)))
                perm = sub.permutation(len(cands))
                g2 = cands.iloc[perm].head(Seff).reset_index(drop=True)
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
                            "seed": seed,
                            "mode": mode,
                            "选股日": sel_day,
                            "买入日": d,
                            "代码": r["代码"],
                            "spend": spend,
                            "ret_pct": float(r["收益率pct"]),
                            "pnl": pnl,
                            "S": S,
                            "Seff": Seff,
                            "分支": r["分支"],
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
        "seed": seed,
        "取票池": "Cond123开盘夹档" if mode == "cond123" else "全日选股∩成交",
        "mode": mode,
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
        "_ret_pct": ret_pct,
    }


def dist_stats(rets: pd.Series, label: str) -> dict:
    r = rets.astype(float)
    return {
        "取票池": label,
        "n_seeds": int(len(r)),
        "mean": round(float(r.mean()), 4),
        "median": round(float(r.median()), 4),
        "std": round(float(r.std(ddof=1)), 4) if len(r) > 1 else 0.0,
        "min": round(float(r.min()), 4),
        "max": round(float(r.max()), 4),
        "pct_positive": round(float((r > 0).mean() * 100), 2),
        "p5": round(float(r.quantile(0.05)), 4),
        "p25": round(float(r.quantile(0.25)), 4),
        "p75": round(float(r.quantile(0.75)), 4),
        "p95": round(float(r.quantile(0.95)), 4),
        "vs_w0_Δpp": round(float(r.mean()) - REF_W0, 4),
        "vs_w8_Δpp": round(float(r.mean()) - REF_W8, 4),
        "vs_file_Δpp": round(float(r.mean()) - REF_FILE_ORDER, 4),
        "frac_gt_w0": round(float((r > REF_W0).mean() * 100), 2),
        "frac_gt_w8": round(float((r > REF_W8).mean() * 100), 2),
        "frac_gt_file": round(float((r > REF_FILE_ORDER).mean() * 100), 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-seeds", type=int, default=500, help="number of random seeds (default 500)")
    ap.add_argument("--seed0", type=int, default=0, help="first seed (inclusive)")
    ap.add_argument("--no-all-fills", action="store_true", help="skip optional all_fills mode")
    args = ap.parse_args()

    n_seeds = int(args.n_seeds)
    seed0 = int(args.seed0)
    seeds = list(range(seed0, seed0 + n_seeds))
    modes = ["cond123"] if args.no_all_fills else ["cond123", "all_fills"]

    df, smap = load_universe()
    rows = []
    print(
        f"===== random-sort · clip({L_},{U_}) · capital={CAPITAL0:.0f} · "
        f"seeds={n_seeds} ({seed0}..{seed0 + n_seeds - 1}) ====="
    )
    print(f"参照: w=0≈+{REF_W0}%  w={DEFAULT_W}≈+{REF_W8}%  file-order≈+{REF_FILE_ORDER}%")
    print(f"modes={modes}  生产默认未改 EXPORT_ELIG_WEIGHT={DEFAULT_W}")

    for mode in modes:
        label = "Cond123" if mode == "cond123" else "全日∩成交"
        print(f"\n--- mode={mode} ({label}) ---")
        for i, seed in enumerate(seeds):
            r = sim_one(df, smap, seed, mode)
            ret = r.pop("_ret_pct")
            rows.append(r)
            if (i + 1) % 50 == 0 or i == 0 or i == n_seeds - 1:
                print(f"  [{i + 1:4d}/{n_seeds}] seed={seed}  收益={ret:+7.2f}%  成交={r['成交笔数']}")

    per_seed = pd.DataFrame(rows)
    dist_rows = []
    for mode in modes:
        sub = per_seed[per_seed["mode"] == mode]
        label = "Cond123开盘夹档" if mode == "cond123" else "全日选股∩成交"
        dist_rows.append(dist_stats(sub["收益%"], label))
    dist = pd.DataFrame(dist_rows)

    # concise verdict
    c = dist[dist["取票池"] == "Cond123开盘夹档"].iloc[0]
    mean_c = float(c["mean"])
    all_same = (
        len(modes) > 1
        and abs(float(dist.iloc[0]["mean"]) - float(dist.iloc[1]["mean"])) < 1e-9
        and abs(float(dist.iloc[0]["std"]) - float(dist.iloc[1]["std"])) < 1e-9
    )
    if mean_c > REF_W8 and float(c["frac_gt_w8"]) >= 50 and abs(mean_c - REF_W0) < 1.5:
        verdict = (
            "排序几乎无增量：随机均值≈最优强度分(w=0)，且远高于默认w=8；"
            "边主要来自 Cond123 过滤 + clip(2,4) 仓位，而非强度分排序。"
        )
    elif mean_c > REF_W8 and float(c["frac_gt_w8"]) >= 50:
        verdict = (
            "排序影响有限：随机均值高于默认强度分(w=8)，说明 Cond123 过滤 + clip(2,4) 仓位"
            "是主要边；强度分排序未稳定优于乱序。"
        )
    elif REF_W0 > float(c["p75"]) and REF_W0 - mean_c > 2.0:
        verdict = (
            "排序有一定作用：最优强度分(w=0)明显高于随机分布中心，说明在 Cond123 池内"
            "仍有可挖的排序边；但 clip 过滤本身已贡献大部分正向期望。"
        )
    else:
        verdict = (
            "排序边不清晰：随机分布覆盖参照点，边主要来自 Cond123 过滤 + clip(2,4) 仓位，"
            "而非稳定的强度分排序优势。"
        )

    note = pd.DataFrame(
        [
            {"项": "方法", "值": "native 新规则215 · clip(2,4) · 10万顺序回放 · 日内随机打乱取前Seff"},
            {"项": "主实验", "值": "当日已成交 Cond123(开盘夹档) 中 shuffle → head(Seff)"},
            {
                "项": "可选实验",
                "值": (
                    "全日选股∩成交 shuffle→Seff；本批 in_pool 全为开盘夹档，与 Cond123 结果相同"
                    if all_same
                    else "当日选股∩全部成交(含高开回踩等) 中 shuffle → head(Seff)"
                ),
            },
            {"项": "Seff", "值": "clip(S,2,4)；S=当日选股池只数；无 max_per_tag"},
            {"项": "种子数", "值": f"{n_seeds} (seed={seed0}..{seed0 + n_seeds - 1})"},
            {"项": "参照w0", "值": f"+{REF_W0}%（Elig权重扫描纯RS）"},
            {"项": "参照w8", "值": f"+{REF_W8}%（默认 EXPORT_ELIG_WEIGHT={DEFAULT_W}）"},
            {"项": "参照file-order", "值": f"+{REF_FILE_ORDER}%（旧导出行序时代）"},
            {"项": "Cond123随机均值", "值": f"{c['mean']:+.4f}%  median={c['median']:+.4f}%  std={c['std']:.4f}"},
            {"项": "结论", "值": verdict},
            {"项": "生产默认", "值": f"未改 EXPORT_ELIG_WEIGHT={DEFAULT_W}"},
            {"项": "生成时间", "值": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
            {"项": "买入", "值": BUY.name},
            {"项": "汇总", "值": NATIVE_SUM.name},
            {"项": "选股", "值": SEL.name},
        ]
    )

    ref = pd.DataFrame(
        [
            {"参照": "Elig w=0 (纯RS)", "收益%": REF_W0},
            {"参照": f"Elig w={DEFAULT_W} (默认)", "收益%": REF_W8},
            {"参照": "file-order 时代", "收益%": REF_FILE_ORDER},
            {
                "参照": "随机 Cond123 均值",
                "收益%": float(c["mean"]),
            },
        ]
    )

    def _write(path: Path) -> Path:
        try:
            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                dist.to_excel(writer, sheet_name="分布统计", index=False)
                per_seed.drop(columns=["mode"], errors="ignore").to_excel(
                    writer, sheet_name="各seed收益", index=False
                )
                ref.to_excel(writer, sheet_name="参照对比", index=False)
                note.to_excel(writer, sheet_name="说明", index=False)
            return path
        except PermissionError:
            alt = path.with_name(path.stem + "_tmp" + path.suffix)
            with pd.ExcelWriter(alt, engine="openpyxl") as writer:
                dist.to_excel(writer, sheet_name="分布统计", index=False)
                per_seed.drop(columns=["mode"], errors="ignore").to_excel(
                    writer, sheet_name="各seed收益", index=False
                )
                ref.to_excel(writer, sheet_name="参照对比", index=False)
                note.to_excel(writer, sheet_name="说明", index=False)
            print(f"[WARN] locked {path.name}, wrote {alt.name}")
            return alt

    out = _write(OUT_XLSX)
    meta = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_seeds": n_seeds,
        "seed0": seed0,
        "modes": modes,
        "clip": f"clip({L_},{U_})",
        "capital0": CAPITAL0,
        "refs": {"w0": REF_W0, "w8": REF_W8, "file_order": REF_FILE_ORDER},
        "dist": dist_rows,
        "verdict": verdict,
        "EXPORT_ELIG_WEIGHT_prod": DEFAULT_W,
        "output": out.name,
    }
    meta_path = out.with_suffix(".json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("===== 分布统计 =====")
    cols = [
        "取票池",
        "n_seeds",
        "mean",
        "median",
        "std",
        "min",
        "max",
        "pct_positive",
        "p5",
        "p25",
        "p75",
        "p95",
        "frac_gt_w0",
        "frac_gt_w8",
        "frac_gt_file",
    ]
    print(dist[cols].to_string(index=False))
    print()
    print(
        f"参照: w0=+{REF_W0}%  w{DEFAULT_W}=+{REF_W8}%  file-order=+{REF_FILE_ORDER}%"
    )
    print(f"结论: {verdict}")
    print(f"导出: {out}")
    print(f"生产默认未改: EXPORT_ELIG_WEIGHT={DEFAULT_W}")


if __name__ == "__main__":
    main()
