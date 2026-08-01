"""对比 7 月回测修正前后汇总差异。"""
from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
OLD = ROOT / "history_data" / "回测七月" / "各日选股收益汇总_7月_全部涨停后1-2日.xlsx"
NEW = ROOT / "history_data" / "回测七月" / "各日选股收益汇总_7月_全部涨停后1-2日修正后.xlsx"


def _load(path: Path) -> tuple[pd.DataFrame, str, str, str]:
    df = pd.read_excel(path, sheet_name=0)
    code_col = next(c for c in df.columns if "股票" in str(c) and "代码" in str(c))
    date_col = next(c for c in df.columns if "选股日" in str(c))
    ret_col = next(c for c in df.columns if "pct" in str(c).lower() or "收益率" in str(c))
    name_col = next((c for c in df.columns if "股票" in str(c) and "名称" in str(c)), None)
    codes = df[code_col].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    dates = pd.to_datetime(df[date_col]).dt.strftime("%Y-%m-%d")
    df["_key"] = dates + "_" + codes
    df["_name"] = df[name_col].astype(str) if name_col else ""
    return df, code_col, date_col, ret_col


def main() -> None:
    old, code_col, date_col, ret_col = _load(OLD)
    new, _, _, _ = _load(NEW)

    print("=" * 60)
    print(f"修正前: {len(old)} 笔  修正后: {len(new)} 笔  减少: {len(old) - len(new)} 笔")
    print(
        f"修正前 平均 {old[ret_col].mean():+.2f}%  合计 {old[ret_col].sum():+.2f}%\n"
        f"修正后 平均 {new[ret_col].mean():+.2f}%  合计 {new[ret_col].sum():+.2f}%"
    )

    removed = set(old["_key"]) - set(new["_key"])
    added = set(new["_key"]) - set(old["_key"])
    print(f"\n消失成交: {len(removed)} 笔  新增成交: {len(added)} 笔")

    if removed:
        print("\n【修正后消失的成交】")
        sub = old[old["_key"].isin(removed)].sort_values(date_col)
        for _, r in sub.iterrows():
            print(
                f"  {str(r[date_col])[:10]} {r['_key'].split('_')[1]} "
                f"{r['_name'][:10]:10s} {r[ret_col]:+.2f}%"
            )

    common = set(old["_key"]) & set(new["_key"])
    changed = []
    for k in common:
        ro = float(old.loc[old["_key"] == k, ret_col].iloc[0])
        rn = float(new.loc[new["_key"] == k, ret_col].iloc[0])
        if abs(ro - rn) > 0.01:
            changed.append((k, ro, rn, rn - ro))
    print(f"\n【同笔收益变化】{len(changed)} 笔")
    for k, ro, rn, d in sorted(changed, key=lambda x: abs(x[3]), reverse=True):
        print(f"  {k}  {ro:+.2f}% -> {rn:+.2f}%  ({d:+.2f}%)")

    # 按用户 7 参组合
    from analyze_param_combo_returns import PARAM_COLS, _exclude_st_and_688, norm_tri  # noqa: E402

    combo = {
        "REQUIRE_PRIOR_LU_IN_L": "False",
        "REQUIRE_OLD_HIGH": "False",
        "REJECT_PRIOR_LIMIT_UP": "False",
        "REQUIRE_OBVIOUS_NEW_HIGH": "False",
        "REQUIRE_LOWER_SHADOW": "True",
        "REQUIRE_BOLL_BREAK": "True",
        "REQUIRE_MA_SUPPORT_AFTER": "True",
    }

    def _combo_stats(df: pd.DataFrame, label: str) -> None:
        d = _exclude_st_and_688(df.copy())
        for c in PARAM_COLS:
            d[c] = d[c].map(norm_tri)
        m = pd.Series(True, index=d.index)
        for k, v in combo.items():
            m &= d[k] == v
        s = d[m]
        print(
            f"\n【你的7参】{label}: {len(s)} 笔"
            + (f"  平均 {s[ret_col].mean():+.2f}%" if len(s) else "")
        )

    _combo_stats(old, "修正前")
    _combo_stats(new, "修正后")


if __name__ == "__main__":
    main()
