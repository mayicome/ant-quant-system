# -*- coding: utf-8 -*-
"""Analyze early vs late picks within short-window repeat selection clusters."""
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

base = Path(r"d:\蚂蚁量化系统\history_data\布林%b回落选股")
f_ret = base / "各日选股收益汇总_日线-bb_pctb-bb_pctb_sell-单点_按票_已完成_latest.xlsx"
f_sel = base / "选股结果_布林%b回落选股_2025-01-02_2026-08-14.xls"

ret = pd.read_excel(f_ret)
sel = pd.read_excel(f_sel)

ret["选股日"] = pd.to_datetime(ret["选股日"])
ret["代码"] = ret["代码"].astype(str).str.zfill(6)
sel["选股日"] = pd.to_datetime(sel["选股日"])
sel["股票代码"] = sel["股票代码"].astype(str).str.zfill(6)

# Use selection calendar as trading-day index proxy
all_days = np.array(sorted(sel["选股日"].unique()))
day_to_idx = {pd.Timestamp(d): i for i, d in enumerate(all_days)}


def trading_gap(d1, d2) -> int:
    i1 = day_to_idx.get(pd.Timestamp(d1))
    i2 = day_to_idx.get(pd.Timestamp(d2))
    if i1 is None or i2 is None:
        return int(abs((pd.Timestamp(d2) - pd.Timestamp(d1)).days))
    return abs(i2 - i1)


def cluster_episodes(df, code_col, date_col, gap_tdays=5):
    episodes = []
    for _, g in df.sort_values([code_col, date_col]).groupby(code_col, sort=False):
        rows = g.to_dict("records")
        cur = [rows[0]]
        for r in rows[1:]:
            if trading_gap(cur[-1][date_col], r[date_col]) <= gap_tdays:
                cur.append(r)
            else:
                episodes.append(cur)
                cur = [r]
        episodes.append(cur)
    return episodes


def stats(arr):
    a = np.asarray(arr, dtype=float)
    a = a[~np.isnan(a)]
    if len(a) == 0:
        return {"n": 0}
    return {
        "n": int(len(a)),
        "mean": float(np.mean(a)),
        "median": float(np.median(a)),
        "win_rate": float(np.mean(a > 0) * 100),
        "p25": float(np.percentile(a, 25)),
        "p75": float(np.percentile(a, 75)),
    }


sel_freq = {}
for gap in [3, 5, 7]:
    eps = cluster_episodes(sel, "股票代码", "选股日", gap_tdays=gap)
    sizes = [len(e) for e in eps]
    multi = [e for e in eps if len(e) >= 2]
    consec_pairs = 0
    for e in multi:
        for a, b in zip(e, e[1:]):
            if trading_gap(a["选股日"], b["选股日"]) == 1:
                consec_pairs += 1
    sel_freq[str(gap)] = {
        "episodes": len(eps),
        "multi": len(multi),
        "multi_pct": 100 * len(multi) / len(eps),
        "multi_picks": sum(len(e) for e in multi),
        "multi_picks_pct": 100 * sum(len(e) for e in multi) / len(sel),
        "size_hist": {str(k): v for k, v in sorted(Counter(sizes).items())},
        "consec_adj_pairs": consec_pairs,
    }

GAP = 5
eps = cluster_episodes(ret, "代码", "选股日", gap_tdays=GAP)
multi = [e for e in eps if len(e) >= 2]
single = [e for e in eps if len(e) == 1]

early_rets = [e[0]["收益率pct"] for e in multi]
late_rets = [e[-1]["收益率pct"] for e in multi]
pos2 = [e[1]["收益率pct"] for e in multi if len(e) >= 2]
pos3p = [r["收益率pct"] for e in multi for r in e[2:]]

early_half, late_half = [], []
for e in multi:
    rets = [r["收益率pct"] for r in e]
    mid = len(rets) / 2
    for i, v in enumerate(rets):
        (early_half if i < mid else late_half).append(v)

deltas = [e[-1]["收益率pct"] - e[0]["收益率pct"] for e in multi]
early_better = sum(1 for e in multi if e[0]["收益率pct"] > e[-1]["收益率pct"])
late_better = sum(1 for e in multi if e[-1]["收益率pct"] > e[0]["收益率pct"])
tie = len(multi) - early_better - late_better

spans = [trading_gap(e[0]["选股日"], e[-1]["选股日"]) for e in multi]
consec_eps = [
    e
    for e in multi
    if all(trading_gap(a["选股日"], b["选股日"]) == 1 for a, b in zip(e, e[1:]))
]

gap_sens = {}
for g in [2, 3, 5, 7, 10]:
    eps_g = cluster_episodes(ret, "代码", "选股日", gap_tdays=g)
    multi_g = [e for e in eps_g if len(e) >= 2]
    if not multi_g:
        continue
    er = [e[0]["收益率pct"] for e in multi_g]
    lr = [e[-1]["收益率pct"] for e in multi_g]
    gap_sens[str(g)] = {
        "n_multi": len(multi_g),
        "early_mean": float(np.mean(er)),
        "late_mean": float(np.mean(lr)),
        "early_med": float(np.median(er)),
        "late_med": float(np.median(lr)),
        "early_win": float(np.mean(np.array(er) > 0) * 100),
        "late_win": float(np.mean(np.array(lr) > 0) * 100),
        "early_better_pct": 100
        * sum(1 for e in multi_g if e[0]["收益率pct"] > e[-1]["收益率pct"])
        / len(multi_g),
    }

by_offset = {}
for e in multi:
    for i, r in enumerate(e):
        by_offset.setdefault(i, []).append(r["收益率pct"])

examples = []
for e in sorted(multi, key=lambda x: -len(x))[:10]:
    examples.append(
        {
            "code": e[0]["代码"],
            "name": e[0].get("股票名称"),
            "n": len(e),
            "dates": [str(pd.Timestamp(r["选股日"]).date()) for r in e],
            "rets": [round(float(r["收益率pct"]), 3) for r in e],
            "pctb": [
                round(float(r["%b"]), 4) if pd.notna(r["%b"]) else None for r in e
            ],
        }
    )

out = {
    "meta": {
        "ret_rows": int(len(ret)),
        "sel_rows": int(len(sel)),
        "gap_tdays": GAP,
        "date_range": [
            str(ret["选股日"].min().date()),
            str(ret["选股日"].max().date()),
        ],
    },
    "sel_freq": sel_freq,
    "episode": {
        "n_episodes": len(eps),
        "n_multi": len(multi),
        "n_single": len(single),
        "multi_pct_episodes": 100 * len(multi) / len(eps),
        "multi_picks": sum(len(e) for e in multi),
        "multi_picks_pct": 100 * sum(len(e) for e in multi) / len(ret),
        "size_hist": {str(k): v for k, v in sorted(Counter(len(e) for e in eps).items())},
        "span_mean": float(np.mean(spans)) if spans else None,
        "span_median": float(np.median(spans)) if spans else None,
        "n_fully_consecutive": len(consec_eps),
    },
    "returns": {
        "all": stats(ret["收益率pct"].tolist()),
        "single_only": stats([e[0]["收益率pct"] for e in single]),
        "multi_all_picks": stats([r["收益率pct"] for e in multi for r in e]),
        "early_first": stats(early_rets),
        "late_last": stats(late_rets),
        "pos2": stats(pos2),
        "pos3plus": stats(pos3p),
        "early_half": stats(early_half),
        "late_half": stats(late_half),
        "first_vs_last": {
            "early_better_n": early_better,
            "late_better_n": late_better,
            "tie_n": tie,
            "early_better_pct": 100 * early_better / len(multi),
            "mean_delta_late_minus_early": float(np.mean(deltas)),
            "median_delta_late_minus_early": float(np.median(deltas)),
        },
        "consec_early": stats([e[0]["收益率pct"] for e in consec_eps]),
        "consec_late": stats([e[-1]["收益率pct"] for e in consec_eps]),
    },
    "pctb": {
        "early_mean": float(np.nanmean([e[0]["%b"] for e in multi])),
        "late_mean": float(np.nanmean([e[-1]["%b"] for e in multi])),
        "early_median": float(np.nanmedian([e[0]["%b"] for e in multi])),
        "late_median": float(np.nanmedian([e[-1]["%b"] for e in multi])),
    },
    "gap_sensitivity": gap_sens,
    "by_offset": {str(k): stats(v) for k, v in sorted(by_offset.items()) if k <= 6},
    "paired": {
        "mean_early_minus_late": float(
            np.mean([e[0]["收益率pct"] - e[-1]["收益率pct"] for e in multi])
        ),
        "median_early_minus_late": float(
            np.median([e[0]["收益率pct"] - e[-1]["收益率pct"] for e in multi])
        ),
    },
    "examples": examples,
}

out_path = base / "_tmp_early_late_analysis.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False, indent=2))
print("saved", out_path)
