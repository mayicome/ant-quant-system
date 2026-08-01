"""从 6 月选股文件按 Tier1/Tier2 筛选，并与回测成交对比。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from analyze_param_combo_returns import PARAM_COLS, _exclude_st_and_688, norm_tri  # noqa: E402

SEL_PATH = ROOT / "history_data" / "回测七月" / "选股结果_涨停的第P到N天_全_6-01_6-30.xls"
BT_PATH = (
    ROOT
    / "history_data"
    / "回测七月"
    / "各日选股收益汇总_6月_全部涨停后1-2日判断5日线上方不能有20日和30日线.xlsx"
)
OUT_DIR = ROOT / "history_data" / "回测七月"


def _col(df: pd.DataFrame, hint: str) -> str:
    return next(c for c in df.columns if hint in str(c))


def _norm_df(df: pd.DataFrame) -> pd.DataFrame:
    for c in PARAM_COLS:
        if c in df.columns:
            df[c] = df[c].map(norm_tri)
    return df


def tier_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "全量": pd.Series(True, index=df.index),
        "Tier1_无旧高且非二连板": (df["REQUIRE_OLD_HIGH"] == "False")
        & (df["REJECT_PRIOR_LIMIT_UP"] == "False"),
        "Tier2_Tier1+有下影": (df["REQUIRE_OLD_HIGH"] == "False")
        & (df["REJECT_PRIOR_LIMIT_UP"] == "False")
        & (df["REQUIRE_LOWER_SHADOW"] == "True"),
        "Tier3_Tier2+破布林": (df["REQUIRE_OLD_HIGH"] == "False")
        & (df["REJECT_PRIOR_LIMIT_UP"] == "False")
        & (df["REQUIRE_LOWER_SHADOW"] == "True")
        & (df["REQUIRE_BOLL_BREAK"] == "True"),
    }


def main() -> None:
    sel = pd.read_excel(SEL_PATH)
    date_col = _col(sel, "选股日")
    code_col = _col(sel, "股票代码")
    name_col = _col(sel, "股票名称")
    sel = _exclude_st_and_688(sel)
    sel = _norm_df(sel)
    sel["_code6"] = sel[code_col].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    sel["_sel_date"] = pd.to_datetime(sel[date_col]).dt.strftime("%Y-%m-%d")

    masks = tier_masks(sel)
    print(f"选股文件: {SEL_PATH.name}")
    print(f"剔 ST/688 后: {len(sel)} 条，选股日 {sel['_sel_date'].nunique()} 天\n")

    print("=== 各 Tier 选股规模 ===")
    for name, m in masks.items():
        sub = sel[m]
        per_day = sub.groupby("_sel_date").size()
        print(
            f"{name}: {len(sub)} 条，日均 {per_day.mean():.1f} 只"
            f"（{per_day.min()}~{per_day.max()}/日）"
        )

    # 回测成交对比
    bt = pd.read_excel(BT_PATH, sheet_name=0)
    bt = _exclude_st_and_688(bt)
    bt = _norm_df(bt)
    ret_col = _col(bt, "pct") if "pct" in str(_col(bt, "pct")).lower() else _col(bt, "收益率")
    bt_code = _col(bt, "股票代码") if any("股票代码" in str(c) for c in bt.columns) else "代码"
    bt_date = _col(bt, "选股日")
    bt["_code6"] = bt[bt_code].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    bt["_sel_date"] = pd.to_datetime(bt[bt_date]).dt.strftime("%Y-%m-%d")

    # merge selection attrs into trades
    key_cols = ["_sel_date", "_code6"] + PARAM_COLS
    sel_key = sel[key_cols].drop_duplicates()
    bt_m = bt.merge(sel_key, on=["_sel_date", "_code6"], how="left", suffixes=("", "_sel"))

    print("\n=== 回测成交（50笔）在各 Tier 中的归属 ===")
    bt_masks = tier_masks(bt_m.fillna({"REQUIRE_OLD_HIGH": "None", "REJECT_PRIOR_LIMIT_UP": "None"}))
    for name, m in bt_masks.items():
        sub = bt_m[m]
        if len(sub) == 0:
            print(f"{name}: 0 笔成交")
            continue
        avg = sub[ret_col].mean()
        win = (sub[ret_col] > 0).mean() * 100
        print(f"{name}: {len(sub)} 笔成交, 平均 {avg:+.2f}%, 胜率 {win:.1f}%")

    # 导出 Tier1 / Tier2 选股文件
    for tier_name in ("Tier1_无旧高且非二连板", "Tier2_Tier1+有下影", "Tier3_Tier2+破布林"):
        out = sel[masks[tier_name]].copy()
        fname = f"选股结果_涨停P到N天_6月_{tier_name}.xls"
        out_path = OUT_DIR / fname
        out.to_excel(out_path, index=False)
        print(f"\n已导出: {out_path.name} ({len(out)} 条)")

    # 成交明细对照表
    compare_rows = []
    for _, row in bt_m.iterrows():
        tier_hit = []
        for tname, m in bt_masks.items():
            if tname == "全量":
                continue
            if m.loc[row.name]:
                tier_hit.append(tname.replace("Tier", "T").split("_")[0])
        compare_rows.append(
            {
                "选股日": row["_sel_date"],
                "代码": row["_code6"],
                "名称": row.get(name_col, row.get(_col(bt, "股票名称"), "")),
                "收益率pct": row[ret_col],
                "命中Tier": ",".join(tier_hit) if tier_hit else "-",
                **{c: row.get(c, "") for c in PARAM_COLS},
            }
        )
    cmp_df = pd.DataFrame(compare_rows).sort_values(["选股日", "收益率pct"], ascending=[True, False])
    cmp_path = OUT_DIR / "6月回测成交_Tier对照表.csv"
    cmp_df.to_csv(cmp_path, index=False, encoding="utf-8-sig")
    print(f"已导出成交对照: {cmp_path.name}")

    print("\n=== 成交明细（按收益排序，含 Tier 归属）===")
    for _, r in cmp_df.iterrows():
        sign = "+" if r["收益率pct"] >= 0 else ""
        print(
            f"{r['选股日']} {r['代码']} {str(r['名称'])[:6]:6s} "
            f"{sign}{r['收益率pct']:.2f}%  [{r['命中Tier']}]"
        )


if __name__ == "__main__":
    main()
