"""放宽 7 参数条件：看成交笔数 vs 平均收益权衡。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from analyze_param_combo_returns import PARAM_COLS, _exclude_st_and_688, norm_tri  # noqa: E402

PATH = (
    ROOT
    / "history_data"
    / "回测七月"
    / "各日选股收益汇总_6月_全部涨停后1-2日判断5日线上方不能有20日和30日线.xlsx"
)


def stats(sub: pd.DataFrame, ret_col: str, label: str) -> None:
    n = len(sub)
    if n == 0:
        print(f"{label}: 0 笔")
        return
    avg = sub[ret_col].mean()
    win = (sub[ret_col] > 0).mean() * 100
    print(f"{label}: {n} 笔, 平均 {avg:+.2f}%, 胜率 {win:.1f}%")


def main() -> None:
    df = pd.read_excel(PATH, sheet_name=0)
    df = _exclude_st_and_688(df)
    ret_col = next(c for c in df.columns if "pct" in str(c).lower() or "收益率" in str(c))
    sub = df[df[ret_col].notna()].copy()
    for c in PARAM_COLS:
        sub[c] = sub[c].map(norm_tri)

    print(f"数据: {PATH.name}")
    print(f"全样本 {len(sub)} 笔, 平均 {sub[ret_col].mean():+.2f}%")
    print()

    # 正收益组合统计
    sub["combo"] = list(zip(*(sub[c] for c in PARAM_COLS)))
    g = (
        sub.groupby("combo")
        .agg(n=(ret_col, "count"), avg=(ret_col, "mean"))
        .reset_index()
    )
    pos = g[g["avg"] > 0]
    print(f"正收益组合: 共 {len(pos)} / {len(g)} 种")
    for min_n in (1, 2, 3, 5):
        c = len(pos[pos["n"] >= min_n])
        print(f"  其中样本>={min_n}笔: {c} 种")
    print()

    print("=== 逐条放宽：只加一条「避雷」条件 ===")
    stats(sub, ret_col, "基线(全部)")
    # 单条件避雷
    masks = {
        "排除 OLD_HIGH=True(有旧高)": sub["REQUIRE_OLD_HIGH"] != "True",
        "排除 REJECT=True(二连板第二板)": sub["REJECT_PRIOR_LIMIT_UP"] != "True",
        "排除 LOWER_SHADOW=False(无下影)": sub["REQUIRE_LOWER_SHADOW"] != "False",
        "只要 BOLL_BREAK=True(破布林)": sub["REQUIRE_BOLL_BREAK"] == "True",
        "只要 PRIOR_LU=False(L内无前置涨停)": sub["REQUIRE_PRIOR_LU_IN_L"] == "False",
    }
    for label, m in masks.items():
        stats(sub[m], ret_col, label)

    print()
    print("=== 组合放宽（越多笔越好，尽量保持正收益）===")
    tiers = [
        ("Tier1 核心避雷", (sub["REQUIRE_OLD_HIGH"] != "True") & (sub["REJECT_PRIOR_LIMIT_UP"] != "True")),
        (
            "Tier2 +有下影",
            (sub["REQUIRE_OLD_HIGH"] != "True")
            & (sub["REJECT_PRIOR_LIMIT_UP"] != "True")
            & (sub["REQUIRE_LOWER_SHADOW"] != "False"),
        ),
        (
            "Tier3 +破布林",
            (sub["REQUIRE_OLD_HIGH"] != "True")
            & (sub["REJECT_PRIOR_LIMIT_UP"] != "True")
            & (sub["REQUIRE_LOWER_SHADOW"] != "False")
            & (sub["REQUIRE_BOLL_BREAK"] == "True"),
        ),
        (
            "Tier4 原最优7参(全满足)",
            (sub["REQUIRE_PRIOR_LU_IN_L"] == "False")
            & (sub["REQUIRE_OLD_HIGH"] == "False")
            & (sub["REJECT_PRIOR_LIMIT_UP"] == "False")
            & (sub["REQUIRE_OBVIOUS_NEW_HIGH"] == "False")
            & (sub["REQUIRE_LOWER_SHADOW"] == "True")
            & (sub["REQUIRE_BOLL_BREAK"] == "True")
            & (sub["REQUIRE_MA_SUPPORT_AFTER"] == "True"),
        ),
        (
            "Tier4b 放宽PRIOR_LU(允许True)",
            (sub["REQUIRE_OLD_HIGH"] == "False")
            & (sub["REJECT_PRIOR_LIMIT_UP"] == "False")
            & (sub["REQUIRE_OBVIOUS_NEW_HIGH"] == "False")
            & (sub["REQUIRE_LOWER_SHADOW"] == "True")
            & (sub["REQUIRE_BOLL_BREAK"] == "True")
            & (sub["REQUIRE_MA_SUPPORT_AFTER"] == "True"),
        ),
    ]
    for label, m in tiers:
        stats(sub[m], ret_col, label)

    print()
    print("=== 选股规则建议（对应 Tier，可多选）===")
    print("规则配置建议（在「涨停的第P到N天」规则顶部）:")
    print("  必选避雷: REQUIRE_OLD_HIGH=False, REJECT_PRIOR_LIMIT_UP=True")
    print("  建议加:   REQUIRE_LOWER_SHADOW=True, REQUIRE_BOLL_BREAK=True")
    print("  可不卡:   REQUIRE_PRIOR_LU_IN_L=None  (放宽后 19笔 vs 7笔, 见 Tier4b)")
    print("  买入侧:   保留「5日线上方无20/30日线」")


if __name__ == "__main__":
    main()
