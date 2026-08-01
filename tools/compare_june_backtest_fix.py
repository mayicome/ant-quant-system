"""对比 6 月回测修正前后，并复核此前分析结论。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from analyze_param_combo_returns import PARAM_COLS, _exclude_st_and_688, norm_tri  # noqa: E402

BASE = ROOT / "history_data" / "回测七月"
OLD = BASE / "各日选股收益汇总_6月_全部涨停后1-2日判断5日线上方不能有20日和30日线.xlsx"
OLD2 = BASE / "各日选股收益汇总_6月_全部涨停后1-2日.xlsx"
NEW = BASE / "各日选股收益汇总_6月_全部涨停后1-2日修正后.xlsx"

USER_7 = {
    "REQUIRE_PRIOR_LU_IN_L": "False",
    "REQUIRE_OLD_HIGH": "False",
    "REJECT_PRIOR_LIMIT_UP": "False",
    "REQUIRE_OBVIOUS_NEW_HIGH": "False",
    "REQUIRE_LOWER_SHADOW": "True",
    "REQUIRE_BOLL_BREAK": "True",
    "REQUIRE_MA_SUPPORT_AFTER": "True",
}


def _load(path: Path):
    df = pd.read_excel(path, sheet_name=0)
    code_col = next(c for c in df.columns if "股票" in str(c) and "代码" in str(c))
    date_col = next(c for c in df.columns if "选股日" in str(c))
    ret_col = next(c for c in df.columns if "pct" in str(c).lower() or "收益率" in str(c))
    name_col = next((c for c in df.columns if "股票" in str(c) and "名称" in str(c)), None)
    buy_col = next((c for c in df.columns if "买入" in str(c) and "时间" in str(c)), None)
    df["_key"] = (
        pd.to_datetime(df[date_col]).dt.strftime("%Y-%m-%d")
        + "_"
        + df[code_col].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    )
    if name_col:
        df["_name"] = df[name_col].astype(str)
    if buy_col:
        df["_buy"] = df[buy_col].astype(str)
    return df, ret_col, date_col, code_col


def _mask(df: pd.DataFrame, rules: dict) -> pd.Series:
    m = pd.Series(True, index=df.index)
    for k, v in rules.items():
        m &= df[k].map(norm_tri) == v
    return m


def _tier_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "Tier1_无旧高且非二连板": (df["REQUIRE_OLD_HIGH"] == "False")
        & (df["REJECT_PRIOR_LIMIT_UP"] == "False"),
        "Tier3_Tier1+下影+布林": (df["REQUIRE_OLD_HIGH"] == "False")
        & (df["REJECT_PRIOR_LIMIT_UP"] == "False")
        & (df["REQUIRE_LOWER_SHADOW"] == "True")
        & (df["REQUIRE_BOLL_BREAK"] == "True"),
        "你的7参全对齐": _mask(df, USER_7),
    }


def _stats(df: pd.DataFrame, ret_col: str, label: str) -> None:
    d = _exclude_st_and_688(df.copy())
    for c in PARAM_COLS:
        d[c] = d[c].map(norm_tri)
    print(f"\n【{label}】剔ST/688: {len(d)}笔  均{d[ret_col].mean():+.2f}%  胜率{(d[ret_col]>0).mean()*100:.1f}%  合计{d[ret_col].sum():+.2f}%")
    for name, m in _tier_masks(d).items():
        s = d[m]
        if len(s):
            print(f"  {name}: {len(s)}笔 均{s[ret_col].mean():+.2f}%  胜率{(s[ret_col]>0).mean()*100:.1f}%")
        else:
            print(f"  {name}: 0笔")


def _diff(old: pd.DataFrame, new: pd.DataFrame, ret_col: str, date_col: str) -> None:
    removed = set(old["_key"]) - set(new["_key"])
    added = set(new["_key"]) - set(old["_key"])
    print("\n" + "=" * 60)
    print(f"全量: 修正前 {len(old)} 笔 → 修正后 {len(new)} 笔")
    print(f"消失 {len(removed)} 笔  新增 {len(added)} 笔")
    common = set(old["_key"]) & set(new["_key"])
    chg = []
    for k in common:
        ro = float(old.loc[old["_key"] == k, ret_col].iloc[0])
        rn = float(new.loc[new["_key"] == k, ret_col].iloc[0])
        if abs(ro - rn) > 0.01:
            chg.append((k, ro, rn))
    print(f"同笔收益变化: {len(chg)} 笔")

    if removed:
        print("\n消失的成交:")
        sub = old[old["_key"].isin(removed)].sort_values(date_col)
        for _, r in sub.iterrows():
            bt = r.get("_buy", "")
            print(f"  {r['_key']} {r.get('_name','')[:8]:8s} {r[ret_col]:+.2f}%  买{bt}")

    if added:
        print("\n新增的成交:")
        sub = new[new["_key"].isin(added)].sort_values(date_col)
        for _, r in sub.iterrows():
            bt = r.get("_buy", "")
            print(f"  {r['_key']} {r.get('_name','')[:8]:8s} {r[ret_col]:+.2f}%  买{bt}")

    # 此前结论对照
    print("\n" + "=" * 60)
    print("【此前结论 vs 修正后】")
    old_n = _exclude_st_and_688(old.copy())
    new_n = _exclude_st_and_688(new.copy())
    for c in PARAM_COLS:
        old_n[c] = old_n[c].map(norm_tri)
        new_n[c] = new_n[c].map(norm_tri)

    prior = {
        "全量50笔均-1.08%(另一文件)": None,
        "Tier1_24笔+2.06%": ("Tier1_无旧高且非二连板", 24, 2.06),
        "你的7参_7笔+0.21%": ("你的7参全对齐", 7, 0.21),
        "Tier3+MA约20笔": ("Tier3_Tier1+下影+布林", None, None),
    }
    for desc, spec in [
        ("Tier1（无旧高+非二连板）", "Tier1_无旧高且非二连板", 24, 2.06),
        ("你的7参全对齐", "你的7参全对齐", 7, 0.21),
        ("Tier3（+下影+布林）", "Tier3_Tier1+下影+布林", None, None),
    ]:
        _, tier, old_n_trades, old_avg = spec[0], spec[1], spec[2], spec[3]
        m_old = _tier_masks(old_n)[tier]
        m_new = _tier_masks(new_n)[tier]
        so, sn = old_n[m_old], new_n[m_new]
        print(f"\n{desc}")
        print(f"  修正前: {len(so)}笔 均{so[ret_col].mean():+.2f}%  (曾述: {old_n_trades}笔 ~{old_avg:+.2f}%)")
        print(f"  修正后: {len(sn)}笔 均{sn[ret_col].mean():+.2f}%  胜率{(sn[ret_col]>0).mean()*100:.0f}%" if len(sn) else f"  修正后: 0笔")
        if len(so) or len(sn):
            ko = set(so["_key"]) if len(so) else set()
            kn = set(sn["_key"]) if len(sn) else set()
            print(f"  消失: {len(ko-kn)}  新增: {len(kn-ko)}")


def main() -> None:
    if not NEW.exists():
        print("修正后文件不存在:", NEW)
        return
    old_path = OLD if OLD.exists() else OLD2
    old, ret_col, date_col, _ = _load(old_path)
    new, _, _, _ = _load(NEW)
    print(f"对比文件:\n  旧: {old_path.name}\n  新: {NEW.name}")
    _stats(old, ret_col, "修正前")
    _stats(new, ret_col, "修正后")
    _diff(old, new, ret_col, date_col)


if __name__ == "__main__":
    main()
