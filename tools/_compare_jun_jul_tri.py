# -*- coding: utf-8 -*-
"""6月 vs 7月：不检真突破，7 参数三态扫描对比。"""
from __future__ import annotations

import itertools
from pathlib import Path

import pandas as pd

DIR = Path(r"d:\蚂蚁量化系统\history_data\买入条件弱化分析")
PATH_JUN = DIR / "各日选股收益汇总_6月_不检测真突破.xlsx"
PATH_JUL = DIR / "各日选股收益汇总_7月_不检测真突破.xlsx"

REQUIRE_COLS = [
    "REQUIRE_PRIOR_LU_IN_L",
    "REQUIRE_OLD_HIGH",
    "REQUIRE_OBVIOUS_NEW_HIGH",
    "REQUIRE_LOWER_SHADOW",
    "REQUIRE_BOLL_BREAK",
    "REQUIRE_MA_SUPPORT_AFTER",
]
REJECT_COL = "REJECT_PRIOR_LIMIT_UP"
KEYS = REQUIRE_COLS + [REJECT_COL]
TRI = ("True", "False", "None")

SHORT = {
    "REQUIRE_PRIOR_LU_IN_L": "PRIOR",
    "REQUIRE_OLD_HIGH": "OLD",
    "REQUIRE_OBVIOUS_NEW_HIGH": "NEWH",
    "REQUIRE_LOWER_SHADOW": "SHAD",
    "REQUIRE_BOLL_BREAK": "BOLL",
    "REQUIRE_MA_SUPPORT_AFTER": "MA",
    "REJECT_PRIOR_LIMIT_UP": "REJ2",
}

LIVE = {
    "REQUIRE_PRIOR_LU_IN_L": "False",
    "REQUIRE_OLD_HIGH": "False",
    "REQUIRE_OBVIOUS_NEW_HIGH": "False",
    "REQUIRE_LOWER_SHADOW": "True",
    "REQUIRE_BOLL_BREAK": "True",
    "REQUIRE_MA_SUPPORT_AFTER": "True",
    "REJECT_PRIOR_LIMIT_UP": "True",
}
# 6月稳健 #1 / #2
CFG1 = {
    "REQUIRE_PRIOR_LU_IN_L": "None",
    "REQUIRE_OLD_HIGH": "False",
    "REQUIRE_OBVIOUS_NEW_HIGH": "False",
    "REQUIRE_LOWER_SHADOW": "True",
    "REQUIRE_BOLL_BREAK": "False",
    "REQUIRE_MA_SUPPORT_AFTER": "True",
    "REJECT_PRIOR_LIMIT_UP": "True",
}
CFG2 = {**CFG1, "REQUIRE_MA_SUPPORT_AFTER": "None"}


def norm_tri(v: object) -> str:
    if pd.isna(v):
        return "None"
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "1.0", "yes"):
            return "True"
        if s in ("false", "0", "0.0", "no"):
            return "False"
        if s in ("none", "nan", ""):
            return "None"
        return v.strip()
    if v is True:
        return "True"
    if v is False:
        return "False"
    try:
        fv = float(v)  # type: ignore[arg-type]
        if fv == 1.0:
            return "True"
        if fv == 0.0:
            return "False"
    except (TypeError, ValueError):
        pass
    return str(v)


def exclude_st_688(df: pd.DataFrame) -> pd.DataFrame:
    code_col = next((c for c in df.columns if "股票代码" in str(c)), None)
    name_col = next((c for c in df.columns if "股票名称" in str(c)), None)
    num_col = "代码" if "代码" in df.columns else None
    mask = pd.Series(False, index=df.index)

    def z6(v):
        s = str(v).replace(".0", "").strip()
        return s.zfill(6) if s.isdigit() else s

    if code_col:
        mask |= df[code_col].map(z6).str.startswith(("688", "689"))
    if num_col:
        mask |= df[num_col].map(z6).str.startswith(("688", "689"))
    if name_col:
        mask |= df[name_col].astype(str).str.contains(r"ST", case=False, na=False)
    return df.loc[~mask].copy()


def label(sw: dict[str, str]) -> str:
    return " ".join(f"{SHORT[c]}={sw[c][0]}" for c in KEYS)


def load_trades(path: Path) -> tuple[pd.DataFrame, str, str]:
    df = pd.read_excel(path)
    raw = len(df)
    df = exclude_st_688(df)
    ret_col = next(c for c in df.columns if "pct" in str(c).lower() or "收益率" in str(c))
    dc = next(c for c in df.columns if "选股" in str(c) and "日" in str(c))
    sub = df[df[ret_col].notna()].copy()
    for c in KEYS:
        sub[c] = sub[c].map(norm_tri)
    sub[dc] = pd.to_datetime(sub[dc], errors="coerce")
    print(
        f"  {path.name}: 原始{raw} → 剔ST/688后有效 {len(sub)} 笔，"
        f"选股日 {sub[dc].dt.strftime('%Y-%m-%d').nunique()}，"
        f"均 {sub[ret_col].mean():+.2f}% 胜 {(sub[ret_col]>0).mean()*100:.1f}%"
    )
    return sub, ret_col, dc


def scan(sub: pd.DataFrame, ret_col: str, dc: str) -> pd.DataFrame:
    rows = []
    for vals in itertools.product(TRI, repeat=len(KEYS)):
        sw = dict(zip(KEYS, vals))
        m = pd.Series(True, index=sub.index)
        for c in REQUIRE_COLS:
            flag = sw[c]
            fact = sub[c] == "True"
            if flag == "True":
                m &= fact
            elif flag == "False":
                m &= ~fact
        if sw[REJECT_COL] == "True":
            m &= sub[REJECT_COL] != "True"
        part = sub.loc[m]
        n = len(part)
        if n == 0:
            continue
        rets = part[ret_col].astype(float)
        # 累加回撤
        ordered = part.sort_values(dc)
        cum = ordered[ret_col].astype(float).cumsum()
        dd = (cum - cum.cummax()).min()
        rows.append(
            {
                "n": n,
                "days": ordered[dc].dt.strftime("%Y-%m-%d").nunique(),
                "mean": float(rets.mean()),
                "median": float(rets.median()),
                "win": float((rets > 0).mean() * 100),
                "sum": float(rets.sum()),
                "max_dd": float(dd) if pd.notna(dd) else 0.0,
                "none_n": sum(1 for v in sw.values() if v == "None"),
                "label": label(sw),
                "sw": sw,
            }
        )
    return pd.DataFrame(rows)


def stats_cfg(sub: pd.DataFrame, ret_col: str, dc: str, sw: dict[str, str]) -> dict:
    m = pd.Series(True, index=sub.index)
    for c in REQUIRE_COLS:
        flag = sw[c]
        fact = sub[c] == "True"
        if flag == "True":
            m &= fact
        elif flag == "False":
            m &= ~fact
    if sw[REJECT_COL] == "True":
        m &= sub[REJECT_COL] != "True"
    part = sub.loc[m]
    if part.empty:
        return {"n": 0, "days": 0, "mean": float("nan"), "win": float("nan"), "max_dd": float("nan")}
    rets = part[ret_col].astype(float)
    ordered = part.sort_values(dc)
    cum = ordered[ret_col].astype(float).cumsum()
    dd = float((cum - cum.cummax()).min())
    return {
        "n": len(part),
        "days": ordered[dc].dt.strftime("%Y-%m-%d").nunique(),
        "mean": float(rets.mean()),
        "win": float((rets > 0).mean() * 100),
        "max_dd": dd,
        "sum": float(rets.sum()),
    }


def show_top(title: str, g: pd.DataFrame, top: int = 10) -> None:
    print(f"\n=== {title} ===")
    if g.empty:
        print("(无)")
        return
    for _, r in g.head(top).iterrows():
        print(
            f"n={int(r['n']):3d} 日={int(r['days']):2d} None={int(r['none_n'])}  "
            f"均={r['mean']:+7.2f}% 胜={r['win']:5.1f}% 回撤={r['max_dd']:+6.1f}pp  "
            f"| {r['label']}"
        )


def main() -> None:
    print("## 样本总览")
    print("6月:")
    jun, ret_j, dc_j = load_trades(PATH_JUN)
    print("7月:")
    jul, ret_u, dc_u = load_trades(PATH_JUL)

    print("\n## 固定组合对照（实盘用 / 6月#1 / 6月#2）")
    print(f"{'组合':<8} {'月':<4} {'n':>4} {'日':>3} {'均%':>8} {'胜%':>6} {'回撤pp':>8} {'累计pp':>8}")
    for name, sw in [("实盘用", LIVE), ("6月#1", CFG1), ("6月#2", CFG2)]:
        for mon, sub, ret, dc in (("6月", jun, ret_j, dc_j), ("7月", jul, ret_u, dc_u)):
            s = stats_cfg(sub, ret, dc, sw)
            print(
                f"{name:<8} {mon:<4} {s['n']:4d} {s['days']:3d} "
                f"{s['mean']:+8.2f} {s['win']:6.1f} {s['max_dd']:+8.1f} {s.get('sum', float('nan')):+8.1f}"
            )

    print("\n扫描三态 3^7 …")
    g6 = scan(jun, ret_j, dc_j)
    g7 = scan(jul, ret_u, dc_u)
    print(f"6月非空组合 {len(g6)}，7月非空组合 {len(g7)}")

    stable6 = g6[(g6["n"] >= 15) & (g6["days"] >= 8)].sort_values(
        ["mean", "n"], ascending=[False, False]
    )
    # 7月样本少，放宽
    stable7 = g7[(g7["n"] >= 5) & (g7["days"] >= 3)].sort_values(
        ["mean", "n"], ascending=[False, False]
    )
    show_top("6月稳健 Top10（n≥15 日≥8）", stable6)
    show_top("7月相对稳健 Top10（n≥5 日≥3）", stable7)

    # 两边都「还行」的重叠：用 label 对齐
    print("\n=== 6月稳健 Top20 在 7 月的表现 ===")
    m7 = g7.set_index("label")
    rows = []
    for _, r in stable6.head(20).iterrows():
        lab = r["label"]
        if lab not in m7.index:
            rows.append((lab, r, None))
        else:
            rows.append((lab, r, m7.loc[lab]))
    print(f"{'label':<55} {'6月n':>5} {'6均':>7} {'6撤':>7} {'7月n':>5} {'7均':>7} {'7撤':>7}")
    for lab, r6, r7 in rows:
        if r7 is None:
            print(f"{lab:<55} {int(r6['n']):5d} {r6['mean']:+7.2f} {r6['max_dd']:+7.1f}   —")
        else:
            print(
                f"{lab:<55} {int(r6['n']):5d} {r6['mean']:+7.2f} {r6['max_dd']:+7.1f} "
                f"{int(r7['n']):5d} {r7['mean']:+7.2f} {r7['max_dd']:+7.1f}"
            )

    # 联合：6月均>2 且 7月均>0（若有）
    print("\n=== 联合筛选：6月稳健集中，7月均收益>0 且 n≥3 ===")
    good = []
    for _, r6 in stable6.iterrows():
        lab = r6["label"]
        if lab not in m7.index:
            continue
        r7 = m7.loc[lab]
        if float(r7["mean"]) > 0 and int(r7["n"]) >= 3:
            good.append((r6, r7))
    good.sort(key=lambda x: -(0.5 * x[0]["mean"] + 0.5 * x[1]["mean"]))
    if not good:
        print("(无：7月几乎所有继承自6月稳健的组合均≤0)")
        # 退而求其次：7月回撤不太差
        print("\n=== 退阶：6月稳健 Top20 中 7月回撤最好（max_dd 最大=回撤最小）的 8 个 ===")
        scored = []
        for _, r6 in stable6.head(20).iterrows():
            lab = r6["label"]
            if lab not in m7.index:
                continue
            r7 = m7.loc[lab]
            scored.append((r6, r7))
        scored.sort(key=lambda x: -x[1]["max_dd"])  # less negative better
        for r6, r7 in scored[:8]:
            print(
                f"6均={r6['mean']:+.2f}% 7均={r7['mean']:+.2f}% "
                f"6撤={r6['max_dd']:+.1f} 7撤={r7['max_dd']:+.1f} "
                f"7n={int(r7['n'])} | {r6['label']}"
            )
    else:
        for r6, r7 in good[:10]:
            print(
                f"6均={r6['mean']:+.2f}% 7均={r7['mean']:+.2f}% "
                f"6n={int(r6['n'])} 7n={int(r7['n'])} | {r6['label']}"
            )

    # 7月自身最优完整开关
    print("\n=== 7月相对稳健第1名完整开关 ===")
    if not stable7.empty:
        sw = stable7.iloc[0]["sw"]
        r = stable7.iloc[0]
        print(
            f"n={int(r['n'])} 日={int(r['days'])} 均={r['mean']:+.2f}% "
            f"胜={r['win']:.1f}% 回撤={r['max_dd']:+.1f}pp"
        )
        for c in KEYS:
            print(f"  {c} = {sw[c]}")


if __name__ == "__main__":
    main()
