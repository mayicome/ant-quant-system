"""按 7 个 REQUIRE_* 条件组合统计平均收益率。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PARAM_COLS = [
    "REQUIRE_PRIOR_LU_IN_L",
    "REQUIRE_OLD_HIGH",
    "REJECT_PRIOR_LIMIT_UP",
    "REQUIRE_OBVIOUS_NEW_HIGH",
    "REQUIRE_LOWER_SHADOW",
    "REQUIRE_BOLL_BREAK",
    "REQUIRE_MA_SUPPORT_AFTER",
]


def norm_tri(v: object) -> str:
    if pd.isna(v):
        return "None"
    if isinstance(v, str):
        s = v.strip()
        low = s.lower()
        if low in ("true", "1", "1.0", "yes"):
            return "True"
        if low in ("false", "0", "0.0", "no"):
            return "False"
        if low in ("none", "nan", ""):
            return "None"
        return s
    try:
        fv = float(v)  # type: ignore[arg-type]
        if fv == 1.0:
            return "True"
        if fv == 0.0:
            return "False"
    except (TypeError, ValueError):
        pass
    if v is True:
        return "True"
    if v is False:
        return "False"
    return str(v)


def combo_label(combo: tuple[str, ...]) -> str:
    return " | ".join(f"{c}={v}" for c, v in zip(PARAM_COLS, combo))


def _norm_code6(v: object) -> str:
    s = str(v).replace(".0", "").strip()
    if s.isdigit():
        return s.zfill(6)
    return s


def _exclude_st_and_688(df: pd.DataFrame) -> pd.DataFrame:
    """剔除 ST/*ST 与科创板 688/689。"""
    code_col = next((c for c in df.columns if "股票代码" in str(c)), None)
    name_col = next((c for c in df.columns if "股票名称" in str(c)), None)
    num_col = "代码" if "代码" in df.columns else None

    mask = pd.Series(False, index=df.index)
    if code_col:
        codes = df[code_col].map(_norm_code6)
        mask |= codes.str.startswith(("688", "689"))
    if num_col:
        codes = df[num_col].map(_norm_code6)
        mask |= codes.str.startswith(("688", "689"))
    if name_col:
        mask |= df[name_col].astype(str).str.contains(r"ST", case=False, na=False)
    return df.loc[~mask].copy()


def main() -> None:
    path = ROOT / "history_data" / "回测七月" / "各日选股收益汇总_6月数据全的_全部涨停后1-2日.xlsx"
    df = pd.read_excel(path, sheet_name=0)
    raw_n = len(df)
    df = _exclude_st_and_688(df)

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
            中位收益率pct=(ret_col, "median"),
            胜率pct=(ret_col, lambda s: float((s > 0).mean() * 100)),
            累计收益率pct=(ret_col, "sum"),
        )
        .sort_values("平均收益率pct", ascending=False)
    )
    g["组合"] = g["combo"].map(combo_label)

    print(f"文件: {path.name}")
    print(f"原始行数: {raw_n}，剔除 ST/688 后: {len(df)}，有效成交笔数: {len(sub)}，不同组合数: {len(g)}")
    print(f"全样本平均收益率: {sub[ret_col].mean():.2f}%")
    print()

    print("=== 按 7 参数组合排名（全部）===")
    for _, row in g.iterrows():
        print(
            f"笔数={int(row['笔数']):2d}  "
            f"平均={row['平均收益率pct']:7.2f}%  "
            f"中位={row['中位收益率pct']:7.2f}%  "
            f"胜率={row['胜率pct']:5.1f}%  "
            f"| {row['组合']}"
        )

    print()
    print("=== 样本>=3 笔的组合 ===")
    g3 = g[g["笔数"] >= 3]
    if g3.empty:
        print("(无)")
    else:
        for _, row in g3.iterrows():
            print(
                f"笔数={int(row['笔数']):2d}  "
                f"平均={row['平均收益率pct']:7.2f}%  "
                f"胜率={row['胜率pct']:5.1f}%  "
                f"| {row['组合']}"
            )

    # 单参数边际效应
    print()
    print("=== 单参数边际（控制其余条件混合后的粗看）===")
    for c in PARAM_COLS:
        sg = (
            sub.groupby(c, as_index=False)
            .agg(笔数=(ret_col, "count"), 平均收益率pct=(ret_col, "mean"))
            .sort_values("平均收益率pct", ascending=False)
        )
        print(f"\n{c}:")
        for _, row in sg.iterrows():
            print(f"  {row[c]:5s}  n={int(row['笔数']):2d}  avg={row['平均收益率pct']:7.2f}%")

    out = path.with_name(path.stem + "_参数组合分析_剔ST688.csv")
    export = g.drop(columns=["combo"])
    export.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n已导出: {out}")


if __name__ == "__main__":
    main()
