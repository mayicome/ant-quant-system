# -*- coding: utf-8 -*-
"""Ma Zong factor hunt on long-horizon per-stock backtest."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"d:\蚂蚁量化系统")
PATH = (
    ROOT
    / "history_data"
    / "马总选股逻辑"
    / "各日选股收益汇总_日线-ma10-sell_half-单点_按票_已完成_收盘上MA10_latest.xlsx"
)
OUT = ROOT / "history_data" / "马总选股逻辑" / "_tmp_factor_hunt.json"


def as_bool(s: pd.Series) -> pd.Series:
    def _one(x):
        if isinstance(x, (bool, np.bool_)):
            return bool(x)
        if x is None:
            return False
        try:
            if x != x:
                return False
        except Exception:
            pass
        t = str(x).strip().lower()
        if t in ("1", "true", "yes", "y", "是", "真"):
            return True
        if t in ("0", "false", "no", "n", "否", "假", "", "nan", "none"):
            return False
        try:
            return bool(float(t))
        except Exception:
            return False

    return s.map(_one).astype(bool)


def stats(ret: pd.Series) -> dict:
    a = pd.to_numeric(ret, errors="coerce").dropna()
    if a.empty:
        return {"n": 0}
    return {
        "n": int(len(a)),
        "mean": float(a.mean()),
        "median": float(a.median()),
        "win": float((a > 0).mean() * 100),
        "p25": float(a.quantile(0.25)),
        "p75": float(a.quantile(0.75)),
        "sum": float(a.sum()),
    }


def binary_edge(df: pd.DataFrame, mask: pd.Series, label: str) -> dict:
    m = mask.fillna(False).astype(bool)
    t = stats(df.loc[m, "收益率pct"])
    f = stats(df.loc[~m, "收益率pct"])
    edge = (t.get("mean") or 0) - (f.get("mean") or 0) if t.get("n") and f.get("n") else None
    return {"label": label, "true": t, "false": f, "edge_mean": edge}


def quintile_table(df: pd.DataFrame, col: str, q: int = 5) -> list:
    x = pd.to_numeric(df[col], errors="coerce")
    r = pd.to_numeric(df["收益率pct"], errors="coerce")
    ok = x.notna() & r.notna()
    if ok.sum() < 100:
        return []
    try:
        bins = pd.qcut(x[ok], q=q, duplicates="drop")
    except ValueError:
        return []
    rows = []
    for i, (name, idx) in enumerate(bins.groupby(bins).groups.items()):
        sub = r.loc[list(idx)]
        xs = x.loc[list(idx)]
        st = stats(sub)
        st["bucket"] = str(name)
        st["x_mean"] = float(xs.mean())
        st["x_lo"] = float(xs.min())
        st["x_hi"] = float(xs.max())
        st["i"] = i
        rows.append(st)
    return rows


def main() -> None:
    df = pd.read_excel(PATH)
    df["选股日"] = pd.to_datetime(df["选股日"], errors="coerce")
    df = df[df["收益率pct"].notna() & df["选股日"].notna()].copy()

    # condition flags
    c_prior = as_bool(df["条件_前10日无大涨"]) if "条件_前10日无大涨" in df.columns else False
    c_ma = as_bool(df["条件_收盘站上MA5且MA20"]) if "条件_收盘站上MA5且MA20" in df.columns else False
    c_boll = as_bool(df["条件_收盘站上布林上轨"]) if "条件_收盘站上布林上轨" in df.columns else False
    c_board = as_bool(df["条件_行业或概念排名达标"]) if "条件_行业或概念排名达标" in df.columns else False
    c_mv = as_bool(df["条件_流通市值<80亿"]) if "条件_流通市值<80亿" in df.columns else False
    c_old = as_bool(df["满足条件"]) if "满足条件" in df.columns else (c_prior & c_ma & c_boll & c_board & c_mv)
    c_disp = c_prior & c_ma & c_boll & ((~c_board) | (~c_mv))

    # continuous helpers
    for col in [
        "流通市值_亿",
        "最佳板块排名",
        "所属行业最高排名名次",
        "所属概念最高排名名次",
        "主力净流入_万元",
        "选股日为涨停后第几日",
        "近5日RS",
        "近10日RS",
        "近20日RS",
        "%b",
        "收盘价",
        "布林上轨",
        "MA5",
        "MA10",
        "MA20",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # derived
    if "收盘价" in df.columns and "布林上轨" in df.columns:
        df["收盘相对布林上轨pct"] = (df["收盘价"] / df["布林上轨"] - 1.0) * 100.0
    if "收盘价" in df.columns and "MA10" in df.columns:
        df["收盘相对MA10pct"] = (df["收盘价"] / df["MA10"] - 1.0) * 100.0
    if "收盘价" in df.columns and "MA5" in df.columns:
        df["收盘相对MA5pct"] = (df["收盘价"] / df["MA5"] - 1.0) * 100.0
    if "MA5" in df.columns and "MA10" in df.columns:
        df["MA5减MA10pct"] = (df["MA5"] / df["MA10"] - 1.0) * 100.0

    # best rank prefer 最佳板块排名 else min of industry/concept
    if "最佳板块排名" in df.columns:
        df["板块名次"] = df["最佳板块排名"]
    else:
        parts = []
        if "所属行业最高排名名次" in df.columns:
            parts.append(df["所属行业最高排名名次"])
        if "所属概念最高排名名次" in df.columns:
            parts.append(df["所属概念最高排名名次"])
        if parts:
            df["板块名次"] = pd.concat(parts, axis=1).min(axis=1)

    binary = [
        binary_edge(df, c_prior, "前10日无大涨"),
        binary_edge(df, c_ma, "收盘>MA5且MA20"),
        binary_edge(df, c_boll, "收盘>布林上轨"),
        binary_edge(df, c_board, "板块达标(行32/概8)"),
        binary_edge(df, c_mv, "流通市值<80亿"),
        binary_edge(df, c_old, "旧满足(五项全真)"),
        binary_edge(df, c_disp, "现展示满足(三项∧板块|市值缺)"),
    ]

    # more binary cuts on continuous
    extra_bin = []
    if "流通市值_亿" in df.columns:
        for thr in [50, 80, 100, 120, 150, 200, 300]:
            extra_bin.append(binary_edge(df, df["流通市值_亿"] < thr, f"市值<{thr}亿"))
            extra_bin.append(binary_edge(df, df["流通市值_亿"] >= thr, f"市值>={thr}亿"))
    if "板块名次" in df.columns:
        for thr in [8, 16, 32, 50, 80, 100]:
            extra_bin.append(binary_edge(df, df["板块名次"] <= thr, f"板块名次<={thr}"))
            extra_bin.append(binary_edge(df, df["板块名次"] > thr, f"板块名次>{thr}"))
    if "选股日为涨停后第几日" in df.columns:
        for d in [0, 1, 2, 3, 5]:
            extra_bin.append(
                binary_edge(df, df["选股日为涨停后第几日"] == d, f"涨停后第{d}日")
            )
            extra_bin.append(
                binary_edge(df, df["选股日为涨停后第几日"] <= d, f"涨停后≤{d}日")
            )
    if "主力净流入_万元" in df.columns:
        for thr in [0, 1000, 3000, 5000, 10000]:
            extra_bin.append(
                binary_edge(df, df["主力净流入_万元"] >= thr, f"净流入>={thr}万")
            )
    if "近5日RS" in df.columns:
        for thr in [0, 5, 10, 13.5, 20]:
            extra_bin.append(binary_edge(df, df["近5日RS"] > thr, f"RS5>{thr}%"))
    if "近10日RS" in df.columns:
        for thr in [0, 3, 6, 10]:
            extra_bin.append(binary_edge(df, df["近10日RS"] > thr, f"RS10>{thr}%"))
    if "近20日RS" in df.columns:
        for thr in [10, 20, 25, 40]:
            extra_bin.append(binary_edge(df, df["近20日RS"] < thr, f"RS20<{thr}%"))
            extra_bin.append(binary_edge(df, df["近20日RS"] > thr, f"RS20>{thr}%"))

    # combos inspired by soft factors + findings
    combos = []
    base3 = c_prior & c_ma & c_boll
    combos.append(binary_edge(df, base3, "三项技术(无大涨∧均线∧布林)"))
    combos.append(binary_edge(df, base3 & c_board & c_mv, "三项+板块+市值(旧)"))
    combos.append(binary_edge(df, base3 & ((~c_board) | (~c_mv)), "三项+(缺板块|缺市值)现展示"))
    combos.append(binary_edge(df, base3 & (~c_board) & (~c_mv), "三项+板块与市值都不过"))
    combos.append(binary_edge(df, base3 & c_board & (~c_mv), "三项+板块过市值不过"))
    combos.append(binary_edge(df, base3 & (~c_board) & c_mv, "三项+市值过板块不过"))
    combos.append(binary_edge(df, c_ma & c_boll, "仅均线∧布林"))
    combos.append(binary_edge(df, c_prior & c_ma, "仅无大涨∧均线"))
    combos.append(binary_edge(df, c_prior & c_boll, "仅无大涨∧布林"))
    combos.append(binary_edge(df, c_boll, "仅布林"))
    combos.append(binary_edge(df, c_ma, "仅均线"))
    combos.append(binary_edge(df, c_prior, "仅无大涨"))

    if "流通市值_亿" in df.columns:
        combos.append(binary_edge(df, base3 & (df["流通市值_亿"] < 50), "三项+市值<50"))
        combos.append(binary_edge(df, base3 & (df["流通市值_亿"] < 80), "三项+市值<80"))
        combos.append(
            binary_edge(df, base3 & (df["流通市值_亿"] >= 80) & (df["流通市值_亿"] < 150), "三项+市值80-150")
        )
        combos.append(binary_edge(df, base3 & (df["流通市值_亿"] >= 150), "三项+市值>=150"))
        combos.append(binary_edge(df, c_ma & c_boll & (df["流通市值_亿"] < 80), "均线布林+市值<80"))
        combos.append(binary_edge(df, c_boll & (df["流通市值_亿"] < 80), "布林+市值<80"))
    if "板块名次" in df.columns:
        combos.append(binary_edge(df, base3 & (df["板块名次"] <= 32), "三项+板块≤32"))
        combos.append(binary_edge(df, base3 & (df["板块名次"] > 32), "三项+板块>32"))
        combos.append(binary_edge(df, base3 & (df["板块名次"] <= 8), "三项+板块≤8"))
        combos.append(binary_edge(df, c_boll & (df["板块名次"] > 32), "布林+板块>32"))
        combos.append(binary_edge(df, c_boll & (df["板块名次"] <= 32), "布林+板块≤32"))
    if "选股日为涨停后第几日" in df.columns:
        combos.append(
            binary_edge(df, base3 & (df["选股日为涨停后第几日"] <= 1), "三项+涨停后≤1日")
        )
        combos.append(
            binary_edge(df, base3 & (df["选股日为涨停后第几日"] >= 2), "三项+涨停后≥2日")
        )
        combos.append(
            binary_edge(df, c_boll & (df["选股日为涨停后第几日"] == 0), "布林+当日涨停")
        )
        combos.append(
            binary_edge(df, c_boll & (df["选股日为涨停后第几日"] >= 1), "布林+涨停后≥1日")
        )

    # search: rank binary edges by abs edge among n>=500 both sides
    ranked = []
    for item in binary + extra_bin + combos:
        t, f = item["true"], item["false"]
        if t.get("n", 0) >= 300 and f.get("n", 0) >= 300 and item["edge_mean"] is not None:
            ranked.append(item)
    ranked.sort(key=lambda x: abs(x["edge_mean"]), reverse=True)

    # preferred: positive edge on TRUE side with decent n
    preferred = [
        x
        for x in ranked
        if x["edge_mean"] is not None
        and x["edge_mean"] > 0
        and x["true"].get("n", 0) >= 500
        and x["true"].get("mean", -999) > (stats(df["收益率pct"]).get("mean") or 0)
    ]
    preferred.sort(key=lambda x: (x["edge_mean"], x["true"]["mean"]), reverse=True)

    quints = {}
    for col in [
        "流通市值_亿",
        "板块名次",
        "主力净流入_万元",
        "选股日为涨停后第几日",
        "近5日RS",
        "近10日RS",
        "近20日RS",
        "收盘相对布林上轨pct",
        "收盘相对MA10pct",
        "收盘相对MA5pct",
        "MA5减MA10pct",
    ]:
        if col in df.columns:
            quints[col] = quintile_table(df, col)

    # month stability for top candidates
    df["ym"] = df["选股日"].dt.to_period("M").astype(str)
    top_labels = [x["label"] for x in preferred[:8]]
    # rebuild masks by label for month table
    mask_map = {
        "前10日无大涨": c_prior,
        "收盘>MA5且MA20": c_ma,
        "收盘>布林上轨": c_boll,
        "板块达标(行32/概8)": c_board,
        "流通市值<80亿": c_mv,
        "旧满足(五项全真)": c_old,
        "现展示满足(三项∧板块|市值缺)": c_disp,
        "三项技术(无大涨∧均线∧布林)": base3,
        "三项+板块+市值(旧)": base3 & c_board & c_mv,
        "三项+(缺板块|缺市值)现展示": c_disp,
        "三项+板块与市值都不过": base3 & (~c_board) & (~c_mv),
        "三项+板块过市值不过": base3 & c_board & (~c_mv),
        "三项+市值过板块不过": base3 & (~c_board) & c_mv,
        "仅均线∧布林": c_ma & c_boll,
        "仅无大涨∧均线": c_prior & c_ma,
        "仅无大涨∧布林": c_prior & c_boll,
        "仅布林": c_boll,
        "仅均线": c_ma,
        "仅无大涨": c_prior,
    }
    if "流通市值_亿" in df.columns:
        mask_map["三项+市值<50"] = base3 & (df["流通市值_亿"] < 50)
        mask_map["三项+市值<80"] = base3 & (df["流通市值_亿"] < 80)
        mask_map["三项+市值80-150"] = base3 & (df["流通市值_亿"] >= 80) & (df["流通市值_亿"] < 150)
        mask_map["三项+市值>=150"] = base3 & (df["流通市值_亿"] >= 150)
        mask_map["均线布林+市值<80"] = c_ma & c_boll & (df["流通市值_亿"] < 80)
        mask_map["布林+市值<80"] = c_boll & (df["流通市值_亿"] < 80)
    if "板块名次" in df.columns:
        mask_map["三项+板块≤32"] = base3 & (df["板块名次"] <= 32)
        mask_map["三项+板块>32"] = base3 & (df["板块名次"] > 32)
        mask_map["三项+板块≤8"] = base3 & (df["板块名次"] <= 8)
        mask_map["布林+板块>32"] = c_boll & (df["板块名次"] > 32)
        mask_map["布林+板块≤32"] = c_boll & (df["板块名次"] <= 32)
    if "选股日为涨停后第几日" in df.columns:
        mask_map["三项+涨停后≤1日"] = base3 & (df["选股日为涨停后第几日"] <= 1)
        mask_map["三项+涨停后≥2日"] = base3 & (df["选股日为涨停后第几日"] >= 2)
        mask_map["布林+当日涨停"] = c_boll & (df["选股日为涨停后第几日"] == 0)
        mask_map["布林+涨停后≥1日"] = c_boll & (df["选股日为涨停后第几日"] >= 1)
    if "近5日RS" in df.columns:
        for thr in [0, 5, 10, 13.5, 20]:
            mask_map[f"RS5>{thr}%"] = df["近5日RS"] > thr
    if "近20日RS" in df.columns:
        for thr in [10, 20, 25, 40]:
            mask_map[f"RS20<{thr}%"] = df["近20日RS"] < thr
            mask_map[f"RS20>{thr}%"] = df["近20日RS"] > thr

    month_edges = {}
    for lab in top_labels:
        m = mask_map.get(lab)
        if m is None:
            continue
        rows = []
        for ym, g in df.groupby("ym"):
            mm = m.loc[g.index]
            if mm.sum() < 20 or (~mm).sum() < 20:
                continue
            tmean = float(g.loc[mm, "收益率pct"].mean())
            fmean = float(g.loc[~mm, "收益率pct"].mean())
            rows.append(
                {
                    "ym": ym,
                    "edge": tmean - fmean,
                    "t_mean": tmean,
                    "f_mean": fmean,
                    "t_n": int(mm.sum()),
                }
            )
        if rows:
            edges = [r["edge"] for r in rows]
            month_edges[lab] = {
                "months": rows,
                "pos_month_pct": 100.0 * sum(1 for e in edges if e > 0) / len(edges),
                "mean_edge": float(np.mean(edges)),
            }

    # propose best simple rules for monitor
    proposals = []
    for lab in [
        "旧满足(五项全真)",
        "现展示满足(三项∧板块|市值缺)",
        "三项技术(无大涨∧均线∧布林)",
        "仅均线∧布林",
        "仅布林",
        "三项+板块+市值(旧)",
        "三项+市值<80",
        "三项+板块>32",
        "三项+板块≤32",
        "布林+市值<80",
        "均线布林+市值<80",
        "三项+涨停后≥2日",
        "三项+涨停后≤1日",
        "布林+涨停后≥1日",
    ]:
        item = next((x for x in binary + combos if x["label"] == lab), None)
        if item and item["true"].get("n", 0) >= 200:
            proposals.append(item)

    # also add top preferred not already in list
    seen = {p["label"] for p in proposals}
    for x in preferred[:12]:
        if x["label"] not in seen:
            proposals.append(x)
            seen.add(x["label"])

    out = {
        "meta": {
            "path": str(PATH),
            "n": int(len(df)),
            "date_min": str(df["选股日"].min().date()),
            "date_max": str(df["选股日"].max().date()),
            "baseline": stats(df["收益率pct"]),
            "cols_sample": [c for c in df.columns if "条件" in str(c) or c in (
                "流通市值_亿", "最佳板块排名", "近5日RS", "近10日RS", "近20日RS",
                "主力净流入_万元", "选股日为涨停后第几日", "布林上轨", "满足条件"
            )],
        },
        "binary_core": binary,
        "combos": combos,
        "top_abs_edge": ranked[:20],
        "top_positive_preferred": preferred[:15],
        "quintiles": quints,
        "month_edges": month_edges,
        "proposals": proposals,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"meta": out["meta"], "top": preferred[:10], "core": binary}, ensure_ascii=False, indent=2))
    print("saved", OUT)


if __name__ == "__main__":
    main()
