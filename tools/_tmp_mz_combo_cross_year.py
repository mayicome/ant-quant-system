# -*- coding: utf-8 -*-
"""扫描软条件组合：要求 2024、2025、2026 均优于当年全池。"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "_tmp_mz_combo_cross_year.json"

PATH_2024 = (
    ROOT
    / "history_data"
    / "马总选股逻辑"
    / "各日选股收益汇总_日线-ma10-sell_half-单点_按票_已完成_收盘上MA10_latest.xlsx"
)
PATH_FINAL = (
    ROOT
    / "history_data"
    / "马总选股逻辑"
    / "最终"
    / "各日选股收益汇总_日线-ma10-sell_half-单点_按票_已完成_收盘上MA10_latest.xlsx"
)

# 可开关的原子条件（True 方向）；另加若干 False 方向别名
ATOM_TRUE = [
    "条件_前10日无大涨",
    "条件_收盘站上MA5且MA20",
    "条件_收盘站上布林上轨",
    "条件_行业或概念排名[5,38]",
    "条件_行业或概念排名达标",
    "条件_流通市值<80亿",
    "条件_当日涨停",
]
# 取 False 的原子（名称用于展示）
ATOM_FALSE = [
    ("非无大涨(有大涨)", "条件_前10日无大涨"),
    ("市值>=80亿", "条件_流通市值<80亿"),
    ("非当日涨停", "条件_当日涨停"),
]

MIN_N_YEAR = 40  # 每年最少样本，太少不算稳


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
    if s.isdigit():
        return s.zfill(6)
    return s


def load_all() -> pd.DataFrame:
    frames = []
    for path, tag in ((PATH_2024, "main"), (PATH_FINAL, "final")):
        df = pd.read_excel(path)
        buy = pd.to_datetime(df.get("买入日"), errors="coerce")
        df = df.copy()
        df["_buy"] = buy
        df["_ret"] = pd.to_numeric(df["收益率pct"], errors="coerce")
        code_col = "代码" if "代码" in df.columns else "股票代码"
        df["_code"] = df[code_col].map(code6)
        df["_src"] = tag
        if tag == "main":
            df = df[(buy >= "2024-01-01") & (buy <= "2024-12-31")]
        else:
            df = df[(buy >= "2025-01-01") & (buy <= "2026-12-31")]
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out["_y"] = out["_buy"].dt.year
    return out


def met(r: pd.Series) -> dict:
    r = pd.to_numeric(r, errors="coerce").dropna()
    n = len(r)
    if not n:
        return {"n": 0, "mean": None, "win": None}
    return {
        "n": n,
        "mean": float(r.mean()),
        "median": float(r.median()),
        "win": float((r > 0).mean() * 100),
    }


def build_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    masks: dict[str, pd.Series] = {}
    for col in ATOM_TRUE:
        if col not in df.columns:
            continue
        masks[col] = ab(df[col]) == True  # noqa: E712
    for name, col in ATOM_FALSE:
        if col not in df.columns:
            continue
        masks[name] = ab(df[col]) == False  # noqa: E712

    # 排名数值桶
    if "最佳板块排名" in df.columns:
        rk = pd.to_numeric(df["最佳板块排名"], errors="coerce")
        for lo, hi, name in [
            (1, 10, "最佳排名1-10"),
            (5, 10, "最佳排名5-10"),
            (5, 20, "最佳排名5-20"),
            (5, 38, "最佳排名5-38"),
            (11, 38, "最佳排名11-38"),
            (1, 38, "最佳排名1-38"),
        ]:
            masks[name] = (rk >= lo) & (rk <= hi)

    # 市值桶
    mvcol = "流通市值_亿_选股日" if "流通市值_亿_选股日" in df.columns else "流通市值_亿"
    if mvcol in df.columns:
        mv = pd.to_numeric(df[mvcol], errors="coerce")
        masks["市值<50亿"] = mv < 50
        masks["市值<80亿_数值"] = mv < 80
        masks["市值>=50亿"] = mv >= 50
        masks["市值>=80亿_数值"] = mv >= 80
        masks["市值50-150"] = (mv >= 50) & (mv < 150)

    # 无大涨阈值变体（用最高涨幅列）
    mx = pd.to_numeric(df.get("前十个交易日最高涨幅"), errors="coerce")

    def growth(c):
        return str(c).startswith(("300", "301", "688", "689", "8", "4", "920"))

    for main, gth, lab in [
        (5.0, 10.0, "无大涨5/10"),
        (7.5, 15.0, "无大涨7.5/15"),
        (4.0, 8.0, "无大涨4/8"),
        (6.0, 12.0, "无大涨6/12"),
    ]:
        thr = df["_code"].map(lambda c: gth if growth(c) else main)
        masks[lab] = mx.notna() & (mx < thr)
        masks[lab.replace("无大涨", "有大涨")] = mx.notna() & (mx >= thr)

    return masks


def eval_mask(df: pd.DataFrame, mask: pd.Series) -> dict | None:
    years = {}
    ok = True
    lifts = []
    for y in (2024, 2025, 2026):
        g = df[df["_y"] == y]
        base = met(g["_ret"])
        sub = met(g.loc[mask.reindex(g.index, fill_value=False), "_ret"])
        if sub["n"] < MIN_N_YEAR or base["mean"] is None or sub["mean"] is None:
            ok = False
            years[str(y)] = {"n": sub.get("n", 0), "ok": False}
            continue
        lift = sub["mean"] - base["mean"]
        years[str(y)] = {
            "n": sub["n"],
            "mean": round(sub["mean"], 4),
            "win": round(sub["win"], 2),
            "base": round(base["mean"], 4),
            "lift": round(lift, 4),
            "win_lift": round(sub["win"] - base["win"], 2),
            "ok": lift > 0,
        }
        if lift <= 0:
            ok = False
        lifts.append(lift)
    if not ok or len(lifts) < 3:
        return None
    return {
        "years": years,
        "min_lift": round(min(lifts), 4),
        "avg_lift": round(float(np.mean(lifts)), 4),
        "sum_n": int(sum(years[str(y)]["n"] for y in (2024, 2025, 2026))),
    }


def main() -> None:
    df = load_all()
    print("loaded", len(df), df["_y"].value_counts().to_dict())
    for y in (2024, 2025, 2026):
        g = df[df["_y"] == y]
        print(y, "base", met(g["_ret"]))

    masks = build_masks(df)
    keys = list(masks.keys())
    print("atoms", len(keys), keys)

    # 基线：原满足
    meet = ab(df["满足条件"]) == True  # noqa: E712
    print("原满足 cross?", eval_mask(df, meet))

    hits = []

    # 1) 单条件
    for k in keys:
        r = eval_mask(df, masks[k])
        if r:
            hits.append({"parts": [k], "k": 1, **r})

    # 2) 2～3 元 AND（限制：互斥对不组合）
    conflict_pairs = {
        frozenset({"条件_前10日无大涨", "非无大涨(有大涨)"}),
        frozenset({"无大涨5/10", "有大涨5/10"}),
        frozenset({"无大涨7.5/15", "有大涨7.5/15"}),
        frozenset({"无大涨4/8", "有大涨4/8"}),
        frozenset({"无大涨6/12", "有大涨6/12"}),
        frozenset({"条件_流通市值<80亿", "市值>=80亿"}),
        frozenset({"条件_当日涨停", "非当日涨停"}),
    }
    # 同类无大涨只留一个
    big_family = [k for k in keys if "大涨" in k]

    def conflicts(parts: tuple[str, ...]) -> bool:
        s = set(parts)
        for cp in conflict_pairs:
            if cp <= s:
                return True
        # 多个无大涨/有大涨变体
        bf = [p for p in parts if p in big_family]
        if len(bf) > 1:
            return True
        # 多个排名数值桶
        rb = [p for p in parts if p.startswith("最佳排名")]
        if len(rb) > 1:
            return True
        mvb = [p for p in parts if p.startswith("市值")]
        if len(mvb) > 1:
            return True
        # 排名[5,38] 与 最佳排名5-38 近似冗余可允许
        return False

    # 优先原子：去掉几乎全 True 的（覆盖率过高噪声）
    cover = {k: float(masks[k].mean()) for k in keys}
    usable = [k for k in keys if 0.05 <= cover[k] <= 0.92]
    print("usable", len(usable), {k: round(cover[k], 3) for k in usable})

    for r in (2, 3):
        for parts in itertools.combinations(usable, r):
            if conflicts(parts):
                continue
            mask = masks[parts[0]].copy()
            for p in parts[1:]:
                mask &= masks[p]
            ev = eval_mask(df, mask)
            if ev:
                hits.append({"parts": list(parts), "k": r, **ev})

    hits.sort(key=lambda x: (-x["min_lift"], -x["avg_lift"], -x["sum_n"]))
    print(f"\n共 {len(hits)} 组三年全优于全池")
    print("=== TOP 30 by min_lift ===")
    for h in hits[:30]:
        print(
            f"minΔ={h['min_lift']:+.3f} avgΔ={h['avg_lift']:+.3f} n={h['sum_n']} "
            f"{' ∧ '.join(h['parts'])}"
        )
        for y in ("2024", "2025", "2026"):
            yy = h["years"][y]
            print(
                f"    {y}: n={yy['n']} mean={yy['mean']:+.3f} "
                f"base={yy['base']:+.3f} lift={yy['lift']:+.3f} win={yy['win']:.1f}"
            )

    # 也报告：三年都好但 min_lift 很小的；以及「接近」——两年好一年略差
    near = []
    for r in (1, 2, 3):
        for parts in itertools.combinations(usable, r):
            if conflicts(parts):
                continue
            mask = masks[parts[0]].copy()
            for p in parts[1:]:
                mask &= masks[p]
            years = {}
            lifts = []
            n_ok = 0
            for y in (2024, 2025, 2026):
                g = df[df["_y"] == y]
                base = met(g["_ret"])
                sub = met(g.loc[mask.reindex(g.index, fill_value=False), "_ret"])
                if sub["n"] < MIN_N_YEAR:
                    break
                lift = sub["mean"] - base["mean"]
                years[str(y)] = {
                    "n": sub["n"],
                    "mean": round(sub["mean"], 4),
                    "lift": round(lift, 4),
                    "win": round(sub["win"], 2),
                }
                lifts.append(lift)
                if lift > 0:
                    n_ok += 1
            else:
                if n_ok == 2 and min(lifts) > -0.3:
                    near.append(
                        {
                            "parts": list(parts),
                            "k": r,
                            "years": years,
                            "min_lift": round(min(lifts), 4),
                            "avg_lift": round(float(np.mean(lifts)), 4),
                            "n_ok_years": n_ok,
                        }
                    )
    near.sort(key=lambda x: (-x["avg_lift"], -x["min_lift"]))
    print(f"\n接近(2年正、第3年>-0.3%) top10:")
    for h in near[:10]:
        print(h["parts"], "min", h["min_lift"], "avg", h["avg_lift"], h["years"])

    payload = {
        "min_n_year": MIN_N_YEAR,
        "n_hits": len(hits),
        "top": hits[:50],
        "near": near[:20],
        "meet_eval": eval_mask(df, meet),
        "base_by_year": {
            str(y): met(df.loc[df["_y"] == y, "_ret"]) for y in (2024, 2025, 2026)
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
