# -*- coding: utf-8 -*-
"""扫描软条件：2024/2025/2026 多段不翻车 + 回撤相对收益可接受。"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "history_data" / "马总选股逻辑"
OUT = ROOT / "data" / "_tmp_mz_stable_scan.json"

MIN_N_YEAR = 40
# 不翻车：三年票均都 > FLOOR（允许略负一点算「没翻成大亏」用 0 更严）
YEAR_MEAN_FLOOR = 0.0
# 相对全池：三年 lift 都 > 0（可选叠加）
REQUIRE_BEAT_POOL = True
# 回撤相对收益：累计>0 且 |最大回撤| <= DD_MULT * 累计
DD_MULT = 1.5
# 至少开买日
MIN_DAYS = 60


def ab(s: pd.Series) -> pd.Series:
    return s.map(
        lambda x: True
        if x is True or str(x).strip().lower() in ("true", "1", "是", "真")
        else (
            False
            if x is False or str(x).strip().lower() in ("false", "0", "否", "假", "")
            else np.nan
        )
    )


def code6(x) -> str:
    s = str(x or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    return s.zfill(6) if s.isdigit() else s


def is_growth(c: str) -> bool:
    return str(c).startswith(("300", "301", "688", "689", "8", "4", "920"))


def load() -> pd.DataFrame:
    parts = []
    for folder in (BASE / "2024", BASE / "最终"):
        hits = list(folder.glob("*按票_已完成_收盘上MA10_latest.xlsx"))
        if not hits:
            continue
        df = pd.read_excel(hits[0])
        buy = pd.to_datetime(df.get("买入日"), errors="coerce")
        code_col = "代码" if "代码" in df.columns else "股票代码"
        df = df.copy()
        df["_buy"] = buy
        df["_ret"] = pd.to_numeric(df["收益率pct"], errors="coerce")
        df["_y"] = buy.dt.year
        df["_code"] = df[code_col].map(code6)
        parts.append(df)
    out = pd.concat(parts, ignore_index=True)
    out = out.dropna(subset=["_buy", "_ret", "_y"])
    out = out[out["_y"].isin([2024, 2025, 2026])].copy()
    return out


def build_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    masks: dict[str, pd.Series] = {}
    soft_true = [
        "条件_前10日无大涨",
        "条件_收盘站上MA5且MA20",
        "条件_收盘站上布林上轨",
        "条件_行业或概念排名[5,38]",
        "条件_行业或概念排名达标",
        "条件_流通市值<80亿",
        "条件_当日涨停",
        "满足条件",
    ]
    for c in soft_true:
        if c in df.columns:
            masks[c] = ab(df[c]) == True  # noqa: E712

    masks["非无大涨(有大涨)"] = ab(df["条件_前10日无大涨"]) == False  # noqa: E712
    masks["市值>=80亿"] = ab(df["条件_流通市值<80亿"]) == False  # noqa: E712
    masks["非当日涨停"] = ab(df["条件_当日涨停"]) == False  # noqa: E712

    if "最佳板块排名" in df.columns:
        rk = pd.to_numeric(df["最佳板块排名"], errors="coerce")
        for lo, hi, name in [
            (5, 20, "最佳排名5-20"),
            (5, 15, "最佳排名5-15"),
            (5, 10, "最佳排名5-10"),
            (8, 25, "最佳排名8-25"),
            (5, 38, "最佳排名5-38"),
            (11, 30, "最佳排名11-30"),
            (1, 20, "最佳排名1-20"),
        ]:
            masks[name] = (rk >= lo) & (rk <= hi)

    mvcol = "流通市值_亿_选股日" if "流通市值_亿_选股日" in df.columns else "流通市值_亿"
    if mvcol in df.columns:
        mv = pd.to_numeric(df[mvcol], errors="coerce")
        masks["市值<50亿"] = mv < 50
        masks["市值<40亿"] = mv < 40
        masks["市值<30亿"] = mv < 30
        masks["市值30-80"] = (mv >= 30) & (mv < 80)
        masks["市值50-120"] = (mv >= 50) & (mv < 120)
        masks["市值>=50亿"] = mv >= 50

    mx = pd.to_numeric(df.get("前十个交易日最高涨幅"), errors="coerce")
    for main, gth, lab in [(5.0, 10.0, "5/10"), (7.5, 15.0, "7.5/15"), (6.0, 12.0, "6/12")]:
        thr = df["_code"].map(lambda c: gth if is_growth(c) else main)
        masks[f"无大涨{lab}"] = mx.notna() & (mx < thr)
        masks[f"有大涨{lab}"] = mx.notna() & (mx >= thr)

    return masks


def path_stats(sub: pd.DataFrame) -> dict:
    """开买日票均曲线：累计与最大回撤(pp)。"""
    tmp = sub.copy()
    tmp["_day"] = tmp["_buy"].dt.normalize()
    day = tmp.groupby("_day", as_index=False)["_ret"].mean().sort_values("_day")
    if day.empty:
        return {"cum": 0.0, "max_dd": 0.0, "n_days": 0, "day_mean": None}
    cum = day["_ret"].cumsum()
    peak = cum.cummax()
    dd = float((cum - peak).min())
    return {
        "cum": float(cum.iloc[-1]),
        "max_dd": dd,
        "n_days": int(len(day)),
        "day_mean": float(day["_ret"].mean()),
        "day_win": float((day["_ret"] > 0).mean() * 100),
    }


def eval_mask(df: pd.DataFrame, mask: pd.Series, base_by_y: dict) -> dict | None:
    years = {}
    means = []
    lifts = []
    for y in (2024, 2025, 2026):
        g = df[df["_y"] == y]
        sub = g.loc[mask.reindex(g.index, fill_value=False)]
        r = sub["_ret"]
        n = int(r.notna().sum())
        if n < MIN_N_YEAR:
            return None
        mean = float(r.mean())
        win = float((r > 0).mean() * 100)
        base = base_by_y[y]
        lift = mean - base
        years[str(y)] = {
            "n": n,
            "mean": round(mean, 4),
            "win": round(win, 2),
            "base": round(base, 4),
            "lift": round(lift, 4),
        }
        means.append(mean)
        lifts.append(lift)

    if min(means) < YEAR_MEAN_FLOOR:
        return None
    if REQUIRE_BEAT_POOL and min(lifts) <= 0:
        return None

    sub_all = df.loc[mask.reindex(df.index, fill_value=False)]
    ps = path_stats(sub_all)
    if ps["n_days"] < MIN_DAYS:
        return None
    cum = ps["cum"]
    mdd = ps["max_dd"]
    if cum <= 0:
        return None
    if abs(mdd) > DD_MULT * cum:
        return None

    return {
        "years": years,
        "min_mean": round(min(means), 4),
        "avg_mean": round(float(np.mean(means)), 4),
        "min_lift": round(min(lifts), 4),
        "n": int(len(sub_all)),
        "cum_pp": round(cum, 2),
        "max_dd_pp": round(mdd, 2),
        "dd_over_cum": round(abs(mdd) / cum, 3),
        "n_days": ps["n_days"],
        "day_mean": round(ps["day_mean"], 4) if ps["day_mean"] is not None else None,
        "day_win": round(ps["day_win"], 2) if ps["day_win"] is not None else None,
    }


def conflicts(parts: tuple[str, ...]) -> bool:
    s = set(parts)
    pairs = [
        {"条件_前10日无大涨", "非无大涨(有大涨)"},
        {"无大涨5/10", "有大涨5/10"},
        {"无大涨7.5/15", "有大涨7.5/15"},
        {"无大涨6/12", "有大涨6/12"},
        {"条件_流通市值<80亿", "市值>=80亿"},
        {"条件_当日涨停", "非当日涨停"},
        {"市值<50亿", "市值>=50亿"},
        {"市值<30亿", "市值>=50亿"},
    ]
    for a, b in pairs:
        if a in s and b in s:
            return True
    ranks = [p for p in parts if p.startswith("最佳排名")]
    if len(ranks) > 1:
        return True
    mvs = [p for p in parts if p.startswith("市值")]
    if len(mvs) > 1:
        return True
    bigs = [p for p in parts if "大涨" in p]
    if len(bigs) > 1:
        return True
    return False


def main() -> None:
    global YEAR_MEAN_FLOOR, REQUIRE_BEAT_POOL, DD_MULT

    df = load()
    print("loaded", len(df), df["_y"].value_counts().to_dict())
    base_by_y = {y: float(df.loc[df["_y"] == y, "_ret"].mean()) for y in (2024, 2025, 2026)}
    print("base", {k: round(v, 4) for k, v in base_by_y.items()})

    masks = build_masks(df)
    # coverage filter
    usable = []
    for k, m in masks.items():
        c = float(m.mean())
        if 0.04 <= c <= 0.85:
            usable.append(k)
    print("usable atoms", len(usable))

    # baselines
    for name in ("满足条件", "最佳排名5-20", "市值<50亿"):
        if name in masks:
            ev = eval_mask(df, masks[name], base_by_y)
            print("baseline", name, ev)

    # relax ladder if strict empty
    settings = [
        {"floor": 0.0, "beat": True, "dd": 1.5, "tag": "严:三年票均>0且跑赢全池且|DD|<=1.5*累计"},
        {"floor": 0.0, "beat": True, "dd": 2.5, "tag": "中:三年>0且跑赢全池且|DD|<=2.5*累计"},
        {"floor": 0.0, "beat": False, "dd": 2.5, "tag": "宽:三年票均>0且|DD|<=2.5*累计"},
        {"floor": -0.3, "beat": True, "dd": 2.5, "tag": "更宽:三年>-0.3%且跑赢全池且|DD|<=2.5*累计"},
    ]

    all_out = {}
    for st in settings:
        YEAR_MEAN_FLOOR = st["floor"]
        REQUIRE_BEAT_POOL = st["beat"]
        DD_MULT = st["dd"]
        hits = []
        for r in (1, 2, 3):
            for parts in itertools.combinations(usable, r):
                if conflicts(parts):
                    continue
                mask = masks[parts[0]].copy()
                for p in parts[1:]:
                    mask &= masks[p]
                ev = eval_mask(df, mask, base_by_y)
                if not ev:
                    continue
                hits.append({"parts": list(parts), "k": r, **ev})
        hits.sort(
            key=lambda x: (
                -x["min_mean"],
                x["dd_over_cum"],
                -x["cum_pp"],
                -x["n"],
            )
        )
        all_out[st["tag"]] = {
            "n_hits": len(hits),
            "top": hits[:25],
        }
        print(f"\n=== {st['tag']} | hits={len(hits)} ===")
        for h in hits[:12]:
            print(
                f"minMean={h['min_mean']:+.3f} cum={h['cum_pp']:+.1f} "
                f"DD={h['max_dd_pp']:.1f} ratio={h['dd_over_cum']:.2f} "
                f"n={h['n']} {' ∧ '.join(h['parts'])}"
            )
            for y in ("2024", "2025", "2026"):
                yy = h["years"][y]
                print(
                    f"    {y}: n={yy['n']} mean={yy['mean']:+.3f} "
                    f"lift={yy['lift']:+.3f} win={yy['win']:.1f}"
                )

    OUT.write_text(json.dumps(all_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
