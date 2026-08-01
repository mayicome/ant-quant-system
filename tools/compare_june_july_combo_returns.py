"""对比 6 月 / 7 月回测汇总的 7 参数组合收益。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from analyze_param_combo_returns import (  # noqa: E402
    PARAM_COLS,
    _exclude_st_and_688,
    combo_label,
    norm_tri,
)

FILES = {
    "6月(5日线过滤)": ROOT
    / "history_data"
    / "回测七月"
    / "各日选股收益汇总_6月_全部涨停后1-2日判断5日线上方不能有20日和30日线.xlsx",
    "7月": ROOT
    / "history_data"
    / "回测七月"
    / "各日选股收益汇总_7月_全部涨停后1-2日.xlsx",
}


def analyze(path: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    df = _exclude_st_and_688(pd.read_excel(path, sheet_name=0))
    ret_col = next(c for c in df.columns if "pct" in str(c).lower() or "收益率" in str(c))
    sub = df[df[ret_col].notna()].copy()
    for c in PARAM_COLS:
        sub[c] = sub[c].map(norm_tri)
    sub["combo"] = list(zip(*(sub[c] for c in PARAM_COLS)))

    g = (
        sub.groupby("combo", as_index=False)
        .agg(
            笔数=(ret_col, "count"),
            平均收益率pct=(ret_col, "mean"),
            胜率pct=(ret_col, lambda s: float((s > 0).mean() * 100)),
        )
        .sort_values("平均收益率pct", ascending=False)
    )
    g["组合"] = g["combo"].map(combo_label)

    singles = []
    for c in PARAM_COLS:
        sg = (
            sub.groupby(c, as_index=False)
            .agg(笔数=(ret_col, "count"), 平均收益率pct=(ret_col, "mean"))
            .sort_values("平均收益率pct", ascending=False)
        )
        sg["参数"] = c
        singles.append(sg)
    single_df = pd.concat(singles, ignore_index=True)

    tiers = []
    tier_defs = [
        ("Tier1_无旧高且非二连板", (sub["REQUIRE_OLD_HIGH"] == "False") & (sub["REJECT_PRIOR_LIMIT_UP"] == "False")),
        (
            "Tier2_+有下影",
            (sub["REQUIRE_OLD_HIGH"] == "False")
            & (sub["REJECT_PRIOR_LIMIT_UP"] == "False")
            & (sub["REQUIRE_LOWER_SHADOW"] == "True"),
        ),
        (
            "Tier3_+破布林",
            (sub["REQUIRE_OLD_HIGH"] == "False")
            & (sub["REJECT_PRIOR_LIMIT_UP"] == "False")
            & (sub["REQUIRE_LOWER_SHADOW"] == "True")
            & (sub["REQUIRE_BOLL_BREAK"] == "True"),
        ),
    ]
    for name, m in tier_defs:
        t = sub[m]
        tiers.append(
            {
                "层级": name,
                "笔数": len(t),
                "平均收益率pct": t[ret_col].mean() if len(t) else None,
                "胜率pct": float((t[ret_col] > 0).mean() * 100) if len(t) else None,
            }
        )
    tier_df = pd.DataFrame(tiers)
    return g, single_df, tier_df


def main() -> None:
    results = {}
    for label, path in FILES.items():
        g, single, tier = analyze(path)
        results[label] = (g, single, tier, path)

    print("=" * 70)
    for label, (g, single, tier, path) in results.items():
        sub_n = int(g["笔数"].sum()) if not g.empty else 0
        print(f"\n【{label}】{path.name}")
        print(f"  成交 {sub_n} 笔，组合 {len(g)} 种，全样本平均 {g['笔数'].mul(g['平均收益率pct']).sum()/max(sub_n,1):.2f}%")
        pos = g[g["平均收益率pct"] > 0]
        print(f"  正收益组合: {len(pos)} / {len(g)} 种")
        print("  --- 样本>=2 且平均>0 ---")
        hit = g[(g["笔数"] >= 2) & (g["平均收益率pct"] > 0)]
        if hit.empty:
            print("  (无)")
        else:
            for _, r in hit.iterrows():
                print(f"    n={int(r['笔数']):2d} avg={r['平均收益率pct']:+7.2f}% win={r['胜率pct']:5.1f}% | {r['组合'][:90]}")
        print("  --- Tier 避雷 ---")
        for _, r in tier.iterrows():
            if r["笔数"]:
                print(f"    {r['层级']}: {int(r['笔数'])}笔 avg={r['平均收益率pct']:+.2f}% win={r['胜率pct']:.1f}%")

    print("\n" + "=" * 70)
    print("【6月 vs 7月 单参数对比】")
    _, s6, _, _ = results["6月(5日线过滤)"]
    _, s7, _, _ = results["7月"]
    for c in PARAM_COLS:
        print(f"\n{c}:")
        for val in ["False", "True", "None"]:
            r6 = s6[(s6["参数"] == c) & (s6[c] == val)]
            r7 = s7[(s7["参数"] == c) & (s7[c] == val)]
            a6 = f"{r6['平均收益率pct'].iloc[0]:+.2f}%(n={int(r6['笔数'].iloc[0])})" if len(r6) else "-"
            a7 = f"{r7['平均收益率pct'].iloc[0]:+.2f}%(n={int(r7['笔数'].iloc[0])})" if len(r7) else "-"
            print(f"  {val:5s}  6月{a6:18s}  7月{a7}")

    # merge combo comparison
    g6 = results["6月(5日线过滤)"][0][["combo", "笔数", "平均收益率pct", "胜率pct"]].rename(
        columns={"笔数": "6月笔数", "平均收益率pct": "6月平均%", "胜率pct": "6月胜率%"}
    )
    g7 = results["7月"][0][["combo", "笔数", "平均收益率pct", "胜率pct"]].rename(
        columns={"笔数": "7月笔数", "平均收益率pct": "7月平均%", "胜率pct": "7月胜率%"}
    )
    merged = g6.merge(g7, on="combo", how="outer")
    merged["组合"] = merged["combo"].map(combo_label)
    merged = merged.sort_values("7月平均%", ascending=False, na_position="last")
    out = FILES["7月"].parent / "6月7月_参数组合对比.csv"
    merged.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n已导出对比表: {out}")


if __name__ == "__main__":
    main()
