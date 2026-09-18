# -*- coding: utf-8 -*-
"""提高无大涨阈值 5/10 → 7.5/15，对比 2024 与 最终。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "_tmp_mz_raise_bigmove_thr.json"

FILES = {
    "2024": ROOT
    / "history_data"
    / "马总选股逻辑"
    / "各日选股收益汇总_日线-ma10-sell_half-单点_按票_已完成_收盘上MA10_latest.xlsx",
    "最终": ROOT
    / "history_data"
    / "马总选股逻辑"
    / "最终"
    / "各日选股收益汇总_日线-ma10-sell_half-单点_按票_已完成_收盘上MA10_latest.xlsx",
}


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


def is_growth(code) -> bool:
    c = code6(code)
    return c.startswith(("300", "301", "688", "689", "8", "4", "920"))


def thr_pct(code, main: float, growth: float) -> float:
    return growth if is_growth(code) else main


def met(r: pd.Series) -> dict:
    r = pd.to_numeric(r, errors="coerce").dropna()
    n = len(r)
    if not n:
        return {"n": 0}
    return {
        "n": n,
        "mean": round(float(r.mean()), 4),
        "median": round(float(r.median()), 4),
        "win": round(float((r > 0).mean() * 100), 2),
        "sum": round(float(r.sum()), 2),
    }


def show(label: str, r: pd.Series, base_mean: float) -> dict:
    m = met(r)
    if m["n"]:
        m["delta"] = round(m["mean"] - base_mean, 4)
        print(
            f"  {label}: n={m['n']} 票均={m['mean']:+.3f}% "
            f"胜率={m['win']:.1f}% Δ={m['delta']:+.3f}"
        )
    else:
        print(f"  {label}: empty")
    m["label"] = label
    return m


def load(path: Path, year_filter: str | None) -> pd.DataFrame:
    df = pd.read_excel(path)
    buy = pd.to_datetime(df.get("买入日"), errors="coerce")
    df = df.copy()
    df["_buy"] = buy
    df["_ret"] = pd.to_numeric(df["收益率pct"], errors="coerce")
    code_col = "代码" if "代码" in df.columns else "股票代码"
    df["_code"] = df[code_col].map(code6)
    # 最高涨幅列可能是百分数
    mx = pd.to_numeric(df.get("前十个交易日最高涨幅"), errors="coerce")
    df["_max_prior_pct"] = mx
    if year_filter == "2024":
        df = df[(buy >= "2024-01-01") & (buy <= "2024-12-31")].copy()
    return df


def no_big(df: pd.DataFrame, main: float, growth: float) -> pd.Series:
    """用最高单日涨幅近似：max < thr（与 all(day < thr) 等价）。"""
    thr = df["_code"].map(lambda c: thr_pct(c, main, growth))
    mx = df["_max_prior_pct"]
    # 缺数据 → False（与规则 prior_rets 空则无大涨=False 一致）
    return mx.notna() & (mx < thr)


def analyze(name: str, df: pd.DataFrame) -> dict:
    print(f"\n======== {name} n={len(df)} "
          f"{df['_buy'].min()} -> {df['_buy'].max()} ========")
    ret = df["_ret"]
    base = met(ret)
    bm = base["mean"]
    print("BASE", base)

    ma = ab(df["条件_收盘站上MA5且MA20"]) == True  # noqa: E712
    boll = ab(df["条件_收盘站上布林上轨"]) == True  # noqa: E712
    rank = ab(df["条件_行业或概念排名[5,38]"]) == True  # noqa: E712
    meet_old = ab(df["满足条件"]) == True  # noqa: E712
    nobig_old_col = ab(df["条件_前10日无大涨"]) == True  # noqa: E712

    nobig_510 = no_big(df, 5.0, 10.0)
    nobig_7515 = no_big(df, 7.5, 15.0)

    # 校验：重算5/10应接近原列
    agree = (nobig_510 == nobig_old_col).mean() * 100
    print(f"  重算5/10 vs 原列一致率={agree:.2f}% "
          f"(原True={int(nobig_old_col.sum())} 重算={int(nobig_510.sum())})")

    rows = []
    for lab, mask in [
        ("全池", ret.notna()),
        ("原满足条件(5/10)", meet_old),
        ("无大涨5/10", nobig_510),
        ("无大涨7.5/15", nobig_7515),
        ("新满足=无大涨7.5/15∧MA∧布林∧[5,38]", nobig_7515 & ma & boll & rank),
        ("旧结构重算=无大涨5/10∧MA∧布林∧[5,38]", nobig_510 & ma & boll & rank),
        ("仅放宽多出来的: 新满足且非旧满足", (nobig_7515 & ma & boll & rank) & ~meet_old),
    ]:
        rows.append(show(lab, ret[mask], bm))

    # 边际：阈值从5提到7.5后新纳入的「无大涨」票
    newly_nobig = nobig_7515 & ~nobig_510
    rows.append(show("仅因放宽变为无大涨的票", ret[newly_nobig], bm))
    rows.append(
        show(
            "放宽后新进满足的票",
            ret[(nobig_7515 & ma & boll & rank) & ~(nobig_510 & ma & boll & rank)],
            bm,
        )
    )

    return {
        "base": base,
        "agree_pct_vs_col": round(agree, 2),
        "rows": rows,
        "n_nobig_510": int(nobig_510.sum()),
        "n_nobig_7515": int(nobig_7515.sum()),
        "n_meet_old": int(meet_old.sum()),
        "n_meet_new": int((nobig_7515 & ma & boll & rank).sum()),
    }


def main() -> None:
    # 2024 用主目录 latest（可能含非2024，需滤）；最终用最终目录
    out = {}
    df24 = load(FILES["2024"], "2024")
    # 若主目录已是纯2024也可；另尝试若2024文件几乎无数据则提示
    out["2024"] = analyze("2024", df24)
    out["最终"] = analyze("最终", load(FILES["最终"], None))

    # 综合评分：两段都优于「原满足」且优于全池？
    summary = []
    for k in ("2024", "最终"):
        rows = {r["label"]: r for r in out[k]["rows"] if r.get("n")}
        base_m = out[k]["base"]["mean"]
        old = rows.get("原满足条件(5/10)", {})
        new = rows.get("新满足=无大涨7.5/15∧MA∧布林∧[5,38]", {})
        summary.append(
            {
                "sample": k,
                "base_mean": base_m,
                "old_meet_mean": old.get("mean"),
                "new_meet_mean": new.get("mean"),
                "new_vs_old": None
                if old.get("mean") is None or new.get("mean") is None
                else round(new["mean"] - old["mean"], 4),
                "new_vs_base": None
                if new.get("mean") is None
                else round(new["mean"] - base_m, 4),
                "old_vs_base": None
                if old.get("mean") is None
                else round(old["mean"] - base_m, 4),
            }
        )
    out["summary"] = summary
    print("\n======== SUMMARY ========")
    for s in summary:
        print(s)

    both_better_than_old = all(
        (s["new_vs_old"] is not None and s["new_vs_old"] > 0) for s in summary
    )
    both_better_than_base = all(
        (s["new_vs_base"] is not None and s["new_vs_base"] > 0) for s in summary
    )
    out["verdict"] = {
        "new_meet_better_than_old_on_both": both_better_than_old,
        "new_meet_better_than_pool_on_both": both_better_than_base,
    }
    print("verdict", out["verdict"])
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
