"""查询指定 7 参数组合在 6/7 月的表现。"""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from analyze_param_combo_returns import PARAM_COLS, _exclude_st_and_688, norm_tri  # noqa: E402

TARGET = {
    "REQUIRE_PRIOR_LU_IN_L": "False",
    "REQUIRE_OLD_HIGH": "False",
    "REJECT_PRIOR_LIMIT_UP": "False",
    "REQUIRE_OBVIOUS_NEW_HIGH": "False",
    "REQUIRE_LOWER_SHADOW": "True",
    "REQUIRE_BOLL_BREAK": "True",
    "REQUIRE_MA_SUPPORT_AFTER": "True",
}


def query(label: str, path: Path) -> None:
    df = _exclude_st_and_688(pd.read_excel(path, sheet_name=0))
    ret = next(c for c in df.columns if "pct" in str(c).lower() or "收益率" in str(c))
    code_col = next(c for c in df.columns if "股票" in str(c) and "代码" in str(c))
    name_col = next(c for c in df.columns if "股票" in str(c) and "名称" in str(c))
    date_col = next(c for c in df.columns if "选股日" in str(c))
    for c in PARAM_COLS:
        df[c] = df[c].map(norm_tri)
    mask = pd.Series(True, index=df.index)
    for k, v in TARGET.items():
        mask &= df[k] == v
    sub = df[mask]
    print(f"\n【{label}】{path.name}")
    print(f"成交 {len(sub)} 笔")
    if sub.empty:
        return
    print(f"平均 {sub[ret].mean():+.2f}%  中位 {sub[ret].median():+.2f}%  胜率 {(sub[ret]>0).mean()*100:.1f}%")
    for _, r in sub.sort_values(date_col).iterrows():
        d = str(r[date_col])[:10]
        code = str(r[code_col]).replace(".0", "").zfill(6)
        name = str(r.get(name_col, ""))[:10]
        print(f"  {d} {code} {name:10s} {r[ret]:+.2f}%")


def main() -> None:
    base = ROOT / "history_data" / "回测七月"
    query("7月", base / "各日选股收益汇总_7月_全部涨停后1-2日.xlsx")
    query(
        "6月",
        base / "各日选股收益汇总_6月_全部涨停后1-2日判断5日线上方不能有20日和30日线.xlsx",
    )


if __name__ == "__main__":
    main()
