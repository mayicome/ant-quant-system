"""查询用户推荐的 7 参数选股组合在 6/7 月回测成交表现。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from analyze_param_combo_returns import PARAM_COLS, _exclude_st_and_688, norm_tri  # noqa: E402

# 规则开关 -> Excel 每股实际条件列（REJECT=True 表示排除二连板 => 行内须 REJECT=False）
TRADE_MASK = {
    "REQUIRE_OLD_HIGH": "False",
    "REJECT_PRIOR_LIMIT_UP": "False",
    "REQUIRE_LOWER_SHADOW": "True",
    "REQUIRE_BOLL_BREAK": "True",
    "REQUIRE_MA_SUPPORT_AFTER": "True",
}


def _mask(df: pd.DataFrame) -> pd.Series:
    m = pd.Series(True, index=df.index)
    for k, v in TRADE_MASK.items():
        m &= df[k] == v
    return m


def _summarize(label: str, path: Path) -> None:
    df = _exclude_st_and_688(pd.read_excel(path, sheet_name=0))
    for c in PARAM_COLS:
        df[c] = df[c].map(norm_tri)
    ret = next(c for c in df.columns if "pct" in str(c).lower() or "收益率" in str(c))
    code_col = next(c for c in df.columns if "股票" in str(c) and "代码" in str(c))
    name_col = next(c for c in df.columns if "股票" in str(c) and "名称" in str(c))
    date_col = next(c for c in df.columns if "选股日" in str(c))
    sub = df[_mask(df)]
    print(f"\n【{label}】{path.name}")
    print(f"成交 {len(sub)} 笔")
    if sub.empty:
        return
    print(
        f"平均 {sub[ret].mean():+.2f}%  中位 {sub[ret].median():+.2f}%  "
        f"胜率 {(sub[ret] > 0).mean() * 100:.1f}%"
    )
    for _, r in sub.sort_values(date_col).iterrows():
        d = str(r[date_col])[:10]
        code = str(r[code_col]).replace(".0", "").zfill(6)
        name = str(r.get(name_col, ""))[:10]
        print(f"  {d} {code} {name:10s} {r[ret]:+.2f}%")


def _pool_count(sel_path: Path) -> None:
    if not sel_path.exists():
        return
    sel = _exclude_st_and_688(pd.read_excel(sel_path))
    for c in PARAM_COLS:
        sel[c] = sel[c].map(norm_tri)
    date_col = next(c for c in sel.columns if "选股日" in str(c))
    sub = sel[_mask(sel)]
    days = sub[date_col].nunique()
    print(f"\n【选股池】{sel_path.name}")
    print(f"符合组合 {len(sub)} 只 / {days} 个交易日")


def main() -> None:
    base = ROOT / "history_data" / "回测七月"
    _summarize(
        "7月成交(旧回测汇总)",
        base / "各日选股收益汇总_7月_全部涨停后1-2日.xlsx",
    )
    _summarize(
        "6月成交",
        base / "各日选股收益汇总_6月_全部涨停后1-2日判断5日线上方不能有20日和30日线.xlsx",
    )
    _pool_count(base / "选股结果_涨停的第P到N天_7-01_7-08.xls")


if __name__ == "__main__":
    main()
