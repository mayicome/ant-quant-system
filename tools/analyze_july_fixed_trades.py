"""拆解 7 月修正后保留成交的结构。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from analyze_param_combo_returns import PARAM_COLS, _exclude_st_and_688, norm_tri  # noqa: E402

PATH = ROOT / "history_data" / "回测七月" / "各日选股收益汇总_7月_全部涨停后1-2日修正后.xlsx"


def _col(df: pd.DataFrame, hint: str) -> str:
    return next(c for c in df.columns if hint in str(c))


def _buy_period(t: object) -> str:
    s = str(t or "").strip()
    if not s or s.lower() == "nan":
        return "未知"
    if s.startswith("09:30") or s.startswith("09:31"):
        return "09:30-09:31"
    if s.startswith("09:3") or s.startswith("09:4"):
        return "09:32-09:49"
    if s.startswith("09:5") or s.startswith("10:"):
        return "09:50-10:59"
    if s.startswith("11:"):
        return "11:00-11:30"
    if s.startswith("13:") or s.startswith("14:"):
        return "13:00-15:00"
    return "其他"


def main() -> None:
    df = pd.read_excel(PATH, sheet_name=0)
    df = _exclude_st_and_688(df)
    for c in PARAM_COLS:
        df[c] = df[c].map(norm_tri)

    ret_col = _col(df, "pct") if "pct" in _col(df, "pct").lower() else _col(df, "收益率")
    code_col = _col(df, "股票代码")
    name_col = _col(df, "股票名称")
    date_col = _col(df, "选股日")
    buy_col = _col(df, "买入时间")
    day_col = next((c for c in df.columns if "选股日为涨停后" in str(c)), None)

    df["_buy_period"] = df[buy_col].map(_buy_period)
    df["_sel_date"] = pd.to_datetime(df[date_col]).dt.strftime("%Y-%m-%d")

    print("=" * 64)
    print(f"7月修正后保留成交: {len(df)} 笔（剔 ST/688）")
    print(f"平均 {df[ret_col].mean():+.2f}%  中位 {df[ret_col].median():+.2f}%  胜率 {(df[ret_col] > 0).mean() * 100:.1f}%")
    print(f"合计 {df[ret_col].sum():+.2f}%")
    print()

    # 按买入时段
    print("【按买入时段】")
    g = df.groupby("_buy_period", sort=False)
    for period, sub in g:
        print(
            f"  {period:12s} {len(sub):2d}笔  "
            f"均{sub[ret_col].mean():+6.2f}%  胜率{(sub[ret_col]>0).mean()*100:4.0f}%"
        )
    print()

    # 按选股日
    print("【按选股日】")
    for d, sub in df.groupby("_sel_date", sort=True):
        wins = (sub[ret_col] > 0).sum()
        print(f"  {d}  {len(sub)}笔  均{sub[ret_col].mean():+.2f}%  赢{wins}/{len(sub)}")
    print()

    # 按7参核心条件
    print("【按选股条件组合】")
    core_cols = [
        ("无旧高", "REQUIRE_OLD_HIGH", "False"),
        ("非二连板", "REJECT_PRIOR_LIMIT_UP", "False"),
        ("有下影", "REQUIRE_LOWER_SHADOW", "True"),
        ("破布林", "REQUIRE_BOLL_BREAK", "True"),
        ("MA支撑", "REQUIRE_MA_SUPPORT_AFTER", "True"),
    ]
    for label, col, val in core_cols:
        sub = df[df[col] == val]
        other = df[df[col] != val]
        print(
            f"  {label}({val}): {len(sub)}笔均{sub[ret_col].mean():+.2f}% | "
            f"不满足{len(other)}笔均{other[ret_col].mean():+.2f}%" if len(other) else
            f"  {label}({val}): {len(sub)}笔均{sub[ret_col].mean():+.2f}%"
        )

    user_mask = pd.Series(True, index=df.index)
    user_rule = {
        "REQUIRE_PRIOR_LU_IN_L": "False",
        "REQUIRE_OLD_HIGH": "False",
        "REJECT_PRIOR_LIMIT_UP": "False",
        "REQUIRE_OBVIOUS_NEW_HIGH": "False",
        "REQUIRE_LOWER_SHADOW": "True",
        "REQUIRE_BOLL_BREAK": "True",
        "REQUIRE_MA_SUPPORT_AFTER": "True",
    }
    for k, v in user_rule.items():
        user_mask &= df[k] == v
    print()
    print(f"【你的7参全对齐】{user_mask.sum()} 笔")
    print()

    # 真突破三项通过率
    tb_cols = [c for c in df.columns if "真突破" in str(c) and "通过" in str(c)]
    if tb_cols:
        print("【真突破通过情况】")
        all_pass = df[tb_cols].apply(lambda r: all(str(x).strip() in ("是", "True", "1", "yes") for x in r), axis=1)
        print(f"  三项全过: {all_pass.sum()}笔  均{df.loc[all_pass, ret_col].mean():+.2f}%")
        print(f"  非全过: {(~all_pass).sum()}笔  均{df.loc[~all_pass, ret_col].mean():+.2f}%")
        print()

    # 逐笔明细
    print("【逐笔明细】")
    show_cols = [date_col, code_col, name_col, buy_col, ret_col] + PARAM_COLS[:4]
    if day_col:
        show_cols.append(day_col)
    sub = df.sort_values([date_col, buy_col])
    for _, r in sub.iterrows():
        d = str(r[date_col])[:10]
        code = str(r[code_col]).replace(".0", "").zfill(6)
        name = str(r[name_col])[:8]
        bt = str(r[buy_col])
        ret = r[ret_col]
        flags = "/".join(
            x[0]
            for x in [
                ("旧", r["REQUIRE_OLD_HIGH"] == "True"),
                ("连", r["REJECT_PRIOR_LIMIT_UP"] == "True"),
                ("下", r["REQUIRE_LOWER_SHADOW"] == "True"),
                ("布", r["REQUIRE_BOLL_BREAK"] == "True"),
            ]
        )
        print(f"  {d} {code} {name:8s} {bt:8s} {ret:+6.2f}%  [{flags}]")


if __name__ == "__main__":
    main()
