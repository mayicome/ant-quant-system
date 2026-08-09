# -*- coding: utf-8 -*-
"""Validate MA5<MA10<MA20 vs MA5>MA10>MA20 on July open-clip anytag/besttest."""
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门")
OUT = BASE / "MA排列_开盘夹档_anytag_besttest_验证.xlsx"


def load(name: str) -> pd.DataFrame:
    df = pd.read_excel(BASE / name, sheet_name=0)
    for c in [
        "MA5",
        "MA10",
        "MA20",
        "收益率pct",
        "均线差占比",
        "最近10个交易日内的涨停板数量",
        "开盘相对买入日MA5_pct",
    ]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[df["收益率pct"].notna()].copy()
    has = df["MA5"].notna() & df["MA10"].notna() & df["MA20"].notna()
    # literal A-filter: MA5 < MA10 < MA20 (= 空头排列 in TA naming)
    df["ma_lt"] = has & (df["MA5"] < df["MA10"]) & (df["MA10"] < df["MA20"])
    # opposite: MA5 > MA10 > MA20 (= 多头排列)
    df["ma_gt"] = has & (df["MA5"] > df["MA10"]) & (df["MA10"] > df["MA20"])
    df["ma_ok"] = has
    return df


def stats(s, label=""):
    s = pd.to_numeric(s, errors="coerce").dropna()
    n = len(s)
    if n == 0:
        return dict(label=label, n=0, mean=np.nan, med=np.nan, win=np.nan, sumret=np.nan)
    return dict(
        label=label,
        n=n,
        mean=float(s.mean()),
        med=float(s.median()),
        win=float((s > 0).mean() * 100),
        sumret=float(s.sum()),
    )


def fmt(d):
    if d["n"] == 0:
        return f"{d['label']}: n=0"
    return (
        f"{d['label']}: n={d['n']}  mean={d['mean']:+.3f}%  med={d['med']:+.3f}%  "
        f"win={d['win']:.1f}%  sum={d['sumret']:+.1f}%"
    )


def compare_flag(df, flag_col, flag_name):
    rows = []
    parts = [
        stats(df["收益率pct"], "ALL"),
        stats(df.loc[df[flag_col], "收益率pct"], f"{flag_name}=Y"),
        stats(df.loc[~df[flag_col] & df["ma_ok"], "收益率pct"], f"{flag_name}=N_hasMA"),
        stats(df.loc[~df[flag_col], "收益率pct"], f"{flag_name}=N_all"),
    ]
    for d in parts:
        rows.append(d)
        print("  ", fmt(d))
    y, n = parts[1], parts[2]
    if y["n"] and n["n"]:
        print(
            f"   Δmean(Y-N_hasMA)={y['mean'] - n['mean']:+.3f}pp  "
            f"Δwin={y['win'] - n['win']:+.1f}pp"
        )
    return rows


def main():
    datasets = [
        ("anytag", "开盘夹档", "开盘夹档_各日选股收益汇总.xlsx"),
        ("anytag", "Cond1", "开盘夹档_条件一_无涨停_均线差0.5to2_各日选股收益汇总.xlsx"),
        (
            "anytag",
            "Cond1+2",
            "开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_各日选股收益汇总.xlsx",
        ),
        ("besttest", "开盘夹档", "besttest_开盘夹档_各日选股收益汇总.xlsx"),
        (
            "besttest",
            "开盘夹档+开盘MA5",
            "besttest_开盘夹档_各日选股收益汇总_含开盘相对MA5.xlsx",
        ),
        (
            "besttest",
            "Cond1",
            "besttest_开盘夹档_条件一_无涨停_均线差0.5to2_各日选股收益汇总.xlsx",
        ),
        (
            "besttest",
            "Cond1+2",
            "besttest_开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_各日选股收益汇总.xlsx",
        ),
    ]

    summary_rows = []
    for src, layer, fname in datasets:
        if not (BASE / fname).exists():
            print("MISSING", fname)
            continue
        df = load(fname)
        n_miss = int((~df["ma_ok"]).sum())
        share_lt = float(df["ma_lt"].mean() * 100)
        share_gt = float(df["ma_gt"].mean() * 100)
        print("=" * 70)
        print(
            f"{src} | {layer} | {fname} | n={len(df)} ma_missing={n_miss} "
            f"lt%={share_lt:.1f} gt%={share_gt:.1f}"
        )
        print("  --- MA5<MA10<MA20 (literal / 空头排列) ---")
        for d in compare_flag(df, "ma_lt", "ma_lt"):
            summary_rows.append({**d, "src": src, "layer": layer, "flag": "ma_lt"})
        print("  --- MA5>MA10>MA20 (多头排列) ---")
        for d in compare_flag(df, "ma_gt", "ma_gt"):
            summary_rows.append({**d, "src": src, "layer": layer, "flag": "ma_gt"})

        # compact key row for verdict sheet
        for flag, col in [("MA5<MA10<MA20", "ma_lt"), ("MA5>MA10>MA20", "ma_gt")]:
            y = stats(df.loc[df[col], "收益率pct"], "Y")
            n = stats(df.loc[~df[col] & df["ma_ok"], "收益率pct"], "N")
            a = stats(df["收益率pct"], "ALL")
            summary_rows.append(
                {
                    "src": src,
                    "layer": layer,
                    "flag": flag,
                    "label": "VERDICT",
                    "n_all": a["n"],
                    "mean_all": a["mean"],
                    "win_all": a["win"],
                    "n_Y": y["n"],
                    "mean_Y": y["mean"],
                    "med_Y": y["med"],
                    "win_Y": y["win"],
                    "n_N": n["n"],
                    "mean_N": n["mean"],
                    "med_N": n["med"],
                    "win_N": n["win"],
                    "d_mean": (y["mean"] - n["mean"]) if y["n"] and n["n"] else np.nan,
                    "d_win": (y["win"] - n["win"]) if y["n"] and n["n"] else np.nan,
                    "share_Y": y["n"] / a["n"] * 100 if a["n"] else np.nan,
                }
            )

    print()
    print("=" * 70)
    print("INCREMENTAL rebuild Cond1/Cond1+2 from full open-clip")
    incr_rows = []
    for src, fname in [
        ("anytag", "开盘夹档_各日选股收益汇总.xlsx"),
        ("besttest", "besttest_开盘夹档_各日选股收益汇总_含开盘相对MA5.xlsx"),
    ]:
        # anytag open-clip may lack 开盘相对 — use Cond1+2 export for that layer;
        # for incremental on full, try join open_ma from Cond1+2 file keys if missing
        df = load(fname)
        if "开盘相对买入日MA5_pct" not in df.columns or df["开盘相对买入日MA5_pct"].isna().all():
            alt = BASE / (
                "开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_各日选股收益汇总.xlsx"
                if src == "anytag"
                else "besttest_开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_各日选股收益汇总.xlsx"
            )
            # Prefer a fuller open_ma source if exists
            open_src = BASE / (
                "开盘夹档_各日选股收益汇总.xlsx"
                if src == "anytag"
                else "besttest_开盘夹档_各日选股收益汇总_含开盘相对MA5.xlsx"
            )
            # For anytag: check if市值回填 has open_ma
            cand = [
                BASE / "开盘夹档_各日选股收益汇总_市值回填.xlsx",
                alt,
            ]
            open_col = None
            for cpath in cand:
                if not cpath.exists():
                    continue
                t = pd.read_excel(cpath, sheet_name=0, nrows=2)
                if "开盘相对买入日MA5_pct" in t.columns:
                    full = pd.read_excel(cpath, sheet_name=0)
                    full["代码"] = full["代码"].map(
                        lambda v: str(int(float(v))).zfill(6) if pd.notna(v) else ""
                    )
                    full["选股日"] = pd.to_datetime(full["选股日"]).dt.strftime("%Y-%m-%d")
                    df["代码6"] = df["代码"].map(
                        lambda v: str(int(float(v))).zfill(6) if pd.notna(v) else ""
                    )
                    df["选股日"] = pd.to_datetime(df["选股日"]).dt.strftime("%Y-%m-%d")
                    m = full.set_index(["选股日", "代码"])["开盘相对买入日MA5_pct"]
                    df["开盘相对买入日MA5_pct"] = [
                        m.get((d, c), np.nan) for d, c in zip(df["选股日"], df["代码6"])
                    ]
                    open_col = "joined"
                    print(f"  {src}: joined 开盘相对MA5 from {cpath.name}")
                    break
            if open_col is None and src == "anytag":
                # compute Cond1 only; Cond1+2 from prebuilt export
                print(f"  {src}: no open_ma on full open-clip; Cond1+2 via export only")

        gap = df["均线差占比"]
        print(
            f"\n{src} gap: min={gap.min()} max={gap.max()} median={gap.median()} "
            f"n={len(df)}"
        )
        zt = df["最近10个交易日内的涨停板数量"]
        cond1 = (zt.fillna(-1) == 0) & gap.between(0.005, 0.02)
        has_open = "开盘相对买入日MA5_pct" in df.columns
        cond2 = (
            df["开盘相对买入日MA5_pct"].between(0, 2) if has_open else pd.Series(False, index=df.index)
        )
        print(f"  Cond1 n={int(cond1.sum())}  Cond1+2 n={int((cond1 & cond2).sum())} has_open={has_open}")

        layers = [
            ("open-clip", df["ma_ok"]),
            ("Cond1", cond1 & df["ma_ok"]),
        ]
        if has_open and (cond1 & cond2).any():
            layers.append(("Cond1+2", cond1 & cond2 & df["ma_ok"]))

        for name, mask in layers:
            sub = df[mask]
            a = stats(sub["收益率pct"], "ALL")
            y_lt = stats(sub.loc[sub["ma_lt"], "收益率pct"], "ma_lt=Y")
            n_lt = stats(sub.loc[~sub["ma_lt"], "收益率pct"], "ma_lt=N")
            y_gt = stats(sub.loc[sub["ma_gt"], "收益率pct"], "ma_gt=Y")
            n_gt = stats(sub.loc[~sub["ma_gt"], "收益率pct"], "ma_gt=N")
            print(f"  [{name}]")
            for d in [a, y_lt, n_lt, y_gt, n_gt]:
                print("   ", fmt(d))
            if y_lt["n"] and n_lt["n"]:
                print(
                    f"    Δmean lt={y_lt['mean'] - n_lt['mean']:+.3f}  "
                    f"Δwin={y_lt['win'] - n_lt['win']:+.1f}"
                )
            if y_gt["n"] and n_gt["n"]:
                print(
                    f"    Δmean gt={y_gt['mean'] - n_gt['mean']:+.3f}  "
                    f"Δwin={y_gt['win'] - n_gt['win']:+.1f}"
                )
            if name == "Cond1+2":
                print(
                    f"    INCREMENTAL +ma_lt: mean {a['mean']:+.3f}->{y_lt['mean']:+.3f} "
                    f"(Δ{y_lt['mean'] - a['mean']:+.3f}); win {a['win']:.1f}->{y_lt['win']:.1f}; "
                    f"n {a['n']}->{y_lt['n']}"
                )
                print(
                    f"    INCREMENTAL +ma_gt: mean {a['mean']:+.3f}->{y_gt['mean']:+.3f} "
                    f"(Δ{y_gt['mean'] - a['mean']:+.3f}); win {a['win']:.1f}->{y_gt['win']:.1f}; "
                    f"n {a['n']}->{y_gt['n']}"
                )
            incr_rows.append(
                {
                    "src": src,
                    "layer": name,
                    "n_all": a["n"],
                    "mean_all": a["mean"],
                    "win_all": a["win"],
                    "n_lt": y_lt["n"],
                    "mean_lt": y_lt["mean"],
                    "win_lt": y_lt["win"],
                    "n_not_lt": n_lt["n"],
                    "mean_not_lt": n_lt["mean"],
                    "win_not_lt": n_lt["win"],
                    "d_mean_lt": (y_lt["mean"] - n_lt["mean"]) if y_lt["n"] and n_lt["n"] else np.nan,
                    "n_gt": y_gt["n"],
                    "mean_gt": y_gt["mean"],
                    "win_gt": y_gt["win"],
                    "n_not_gt": n_gt["n"],
                    "mean_not_gt": n_gt["mean"],
                    "win_not_gt": n_gt["win"],
                    "d_mean_gt": (y_gt["mean"] - n_gt["mean"]) if y_gt["n"] and n_gt["n"] else np.nan,
                }
            )

        # Also report prebuilt Cond1+2 export incremental for anytag if open_ma missing
        if src == "anytag" and not (has_open and (cond1 & cond2).any()):
            sub = load(
                "开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_各日选股收益汇总.xlsx"
            )
            a = stats(sub["收益率pct"], "ALL")
            y_lt = stats(sub.loc[sub["ma_lt"], "收益率pct"], "ma_lt=Y")
            n_lt = stats(sub.loc[~sub["ma_lt"] & sub["ma_ok"], "收益率pct"], "ma_lt=N")
            y_gt = stats(sub.loc[sub["ma_gt"], "收益率pct"], "ma_gt=Y")
            print("  [Cond1+2 export]")
            for d in [a, y_lt, n_lt, y_gt]:
                print("   ", fmt(d))
            print(
                f"    INCREMENTAL +ma_lt: mean {a['mean']:+.3f}->{y_lt['mean']:+.3f} "
                f"(Δ{y_lt['mean'] - a['mean']:+.3f}); win {a['win']:.1f}->{y_lt['win']:.1f}; "
                f"n {a['n']}->{y_lt['n']}"
            )
            print(
                f"    INCREMENTAL +ma_gt: mean {a['mean']:+.3f}->{y_gt['mean']:+.3f} "
                f"(Δ{y_gt['mean'] - a['mean']:+.3f}); win {a['win']:.1f}->{y_gt['win']:.1f}; "
                f"n {a['n']}->{y_gt['n']}"
            )
            incr_rows.append(
                {
                    "src": src,
                    "layer": "Cond1+2_export",
                    "n_all": a["n"],
                    "mean_all": a["mean"],
                    "win_all": a["win"],
                    "n_lt": y_lt["n"],
                    "mean_lt": y_lt["mean"],
                    "win_lt": y_lt["win"],
                    "n_not_lt": n_lt["n"],
                    "mean_not_lt": n_lt["mean"],
                    "win_not_lt": n_lt["win"],
                    "d_mean_lt": (y_lt["mean"] - n_lt["mean"]) if y_lt["n"] and n_lt["n"] else np.nan,
                    "n_gt": y_gt["n"],
                    "mean_gt": y_gt["mean"],
                    "win_gt": y_gt["win"],
                    "n_not_gt": np.nan,
                    "mean_not_gt": np.nan,
                    "win_not_gt": np.nan,
                    "d_mean_gt": np.nan,
                }
            )

    # Verdict table only
    verd = [r for r in summary_rows if r.get("label") == "VERDICT"]
    verd_df = pd.DataFrame(verd)
    incr_df = pd.DataFrame(incr_rows)
    with pd.ExcelWriter(OUT, engine="openpyxl") as w:
        verd_df.to_excel(w, sheet_name="verdict", index=False)
        incr_df.to_excel(w, sheet_name="incremental", index=False)
    print()
    print("Wrote", OUT)
    print()
    print("===== VERDICT TABLE =====")
    cols = [
        "src",
        "layer",
        "flag",
        "n_all",
        "mean_all",
        "win_all",
        "n_Y",
        "mean_Y",
        "win_Y",
        "n_N",
        "mean_N",
        "win_N",
        "d_mean",
        "d_win",
        "share_Y",
    ]
    print(verd_df[cols].to_string(index=False))
    print()
    print(incr_df.to_string(index=False))


if __name__ == "__main__":
    main()
