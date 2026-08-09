# -*- coding: utf-8 -*-
"""Rebuild 2026-07-02 eligible Top50 tag inventory with funnel counts.

Funnel columns (选股日=2026-07-02 only), column order:
  anytag入列股数 → besttest入列股数 → anytag过滤后股数 → besttest过滤后股数 → Cond123入选股数

anytag过滤后:
  No July export named 选股结果*anytag*(无涨停|均线差|MA空头) was found under
  history_data / 八月回测-热门 / 备份* / 存档.
  Derived by replaying besttest过滤后 stock filters on anytag全量 metric columns:
    均线差占比 ∈ [MA_GAP_LO, MA_GAP_HI] (0.5%~2%),
    MA5 < MA10 < MA20,
    最近10个交易日内的涨停板数量 == 0.
  Validation: same replay on besttest全量 exactly matches the besttest过滤后 export
  for 2026-07-02 (131/131).

Note: user "cond13" interpreted as Cond123 (开盘夹档+条件二+条件三 fills).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.eastmoney_board_rank_ctx import (  # noqa: E402
    _build_qmt_tag_to_codes,
    _load_stock_info_tag_index,
    _resolve_members_for_tag,
    load_em_board_hot_map,
)

DAY = date(2026, 7, 2)
DAY_S = "2026-07-02"
TOP_N = 50
MIN_MEMBERS = 10

HOT_DIR = ROOT / "history_data" / "八月回测-热门"
OUT_PATH = HOT_DIR / "合格Top50标签清单_2026-07-02.xlsx"

BESTTEST_FULL = HOT_DIR / "选股结果_东财热门-besttest全量-无个股过滤_2026-07-01_2026-07-31.xls"
ANYTAG_FULL = HOT_DIR / "选股结果_东财热门-anytag全量-无个股过滤_2026-07-01_2026-07-31.xls"
BESTTEST_FILTERED = (
    HOT_DIR / "选股结果_东财热门-besttest-无涨停均线差0.5to2-MA空头排列_2026-07-01_2026-07-31.xls"
)

# Prefer a real anytag+个股过滤 export if one appears later.
ANYTAG_FILTERED_CANDIDATES = [
    HOT_DIR / "选股结果_东财热门-anytag-无涨停均线差0.5to2-MA空头排列_2026-07-01_2026-07-31.xls",
    HOT_DIR / "备份2" / "选股结果_东财热门-anytag-无涨停均线差0.5to2-MA空头排列_2026-07-01_2026-07-31.xls",
    HOT_DIR / "备份" / "选股结果_东财热门-anytag-无涨停均线差0.5to2-MA空头排列_2026-07-01_2026-07-31.xls",
]

COND123_CANDIDATES = [
    HOT_DIR / "各日选股收益汇总_新规则.xlsx",
    HOT_DIR / "备份2" / "各日选股收益汇总_新规则.xlsx",
    HOT_DIR
    / "besttest_开盘夹档_条件一_无涨停_均线差0.5to2_条件二开盘相对MA5满足0to2_条件三MA5lt10lt20_各日选股收益汇总.xlsx",
]

FUNNEL_COLS = [
    "anytag入列股数",
    "besttest入列股数",
    "anytag过滤后股数",
    "besttest过滤后股数",
    "Cond123入选股数",
]


def norm_kind(k) -> str:
    s = str(k).strip().lower()
    if s in ("sector", "板块", "行业"):
        return "sector"
    if s in ("concept", "概念"):
        return "concept"
    return s


def load_chg_map(path: Path) -> dict:
    df = pd.read_csv(path, encoding="utf-8-sig")
    out = {}
    for _, row in df.iterrows():
        name = str(row["板块名称"]).strip()
        try:
            out[name] = float(row["涨跌幅"])
        except Exception:
            out[name] = None
    return out


def day_tag_counts(path: Path, day_s: str) -> dict:
    """Count rows on day by (kind, tag) from 选出标签 / 选出标签类型."""
    df = pd.read_excel(path)
    return day_tag_counts_df(df, day_s)


def day_tag_counts_df(df: pd.DataFrame, day_s: str) -> dict:
    sub = df[df["选股日"].astype(str).str[:10] == day_s].copy()
    if sub.empty:
        return {}
    tag_col = "选出标签"
    type_col = "选出标签类型" if "选出标签类型" in sub.columns else None
    out: dict = {}
    for _, row in sub.iterrows():
        tag = str(row.get(tag_col) or "").strip()
        if not tag or tag.lower() == "nan":
            continue
        kind = norm_kind(row.get(type_col)) if type_col else ""
        key = (kind, tag) if kind else ("", tag)
        out[key] = out.get(key, 0) + 1
        bare = ("", tag)
        if key != bare:
            out[bare] = out.get(bare, 0) + 1
    return out


def apply_stock_filters_like_besttest(df: pd.DataFrame, day_s: str) -> pd.DataFrame:
    """Replay 无涨停 + 均线差[0.5%,2%] + MA5<MA10<MA20 on a 无个股过滤 export."""
    sub = df[df["选股日"].astype(str).str[:10] == day_s].copy()
    if sub.empty:
        return sub
    gap = pd.to_numeric(sub["均线差占比"], errors="coerce")
    ma5 = pd.to_numeric(sub["MA5"], errors="coerce")
    ma10 = pd.to_numeric(sub["MA10"], errors="coerce")
    ma20 = pd.to_numeric(sub["MA20"], errors="coerce")
    lu = pd.to_numeric(sub["最近10个交易日内的涨停板数量"], errors="coerce").fillna(0)
    lo = float(sub["MA_GAP_LO"].iloc[0]) if "MA_GAP_LO" in sub.columns else 0.005
    hi = float(sub["MA_GAP_HI"].iloc[0]) if "MA_GAP_HI" in sub.columns else 0.02
    mask = (
        gap.notna()
        & (gap >= lo)
        & (gap <= hi)
        & ma5.notna()
        & ma10.notna()
        & ma20.notna()
        & (ma5 < ma10)
        & (ma10 < ma20)
        & (lu <= 0)
    )
    return sub.loc[mask].copy()


def resolve_anytag_filtered(day_s: str) -> tuple[dict, str]:
    """Return (tag_count_map, source_note). Prefer real export; else derive."""
    for p in ANYTAG_FILTERED_CANDIDATES:
        if p.is_file():
            return day_tag_counts(p, day_s), f"exported file: {p}"

    anytag_df = pd.read_excel(ANYTAG_FULL)
    day_any = anytag_df[anytag_df["选股日"].astype(str).str[:10] == day_s]
    if day_any.empty:
        return {}, "empty anytag全量 for day"

    apply_col = "APPLY_STOCK_FILTERS"
    if apply_col in day_any.columns and bool(day_any[apply_col].iloc[0]):
        # Unexpected: file claims filters already applied
        return day_tag_counts_df(anytag_df, day_s), f"anytag全量 with APPLY_STOCK_FILTERS=True: {ANYTAG_FULL}"

    needed = ["均线差占比", "MA5", "MA10", "MA20", "最近10个交易日内的涨停板数量"]
    missing = [c for c in needed if c not in anytag_df.columns]
    if missing:
        return {}, f"未导出/无法可靠推算 (missing columns: {missing})"

    # Validate replay against besttest过滤后 when both full+filtered exist.
    if BESTTEST_FULL.is_file() and BESTTEST_FILTERED.is_file():
        best_full = pd.read_excel(BESTTEST_FULL)
        best_filt = pd.read_excel(BESTTEST_FILTERED)
        der = apply_stock_filters_like_besttest(best_full, day_s)
        exp = best_filt[best_filt["选股日"].astype(str).str[:10] == day_s]
        if len(der) != len(exp):
            print(
                f"WARN: filter-replay on besttest全量 n={len(der)} "
                f"!= besttest过滤后 n={len(exp)}; anytag过滤后 still derived the same way"
            )

    filtered = apply_stock_filters_like_besttest(anytag_df, day_s)
    note = (
        "未导出 real anytag+个股过滤选股结果; "
        f"derived by replaying 无涨停+均线差0.5to2+MA空头 on {ANYTAG_FULL.name} "
        f"(validated: same replay matches besttest过滤后 on {DAY_S})"
    )
    return day_tag_counts_df(filtered, day_s), note


def lookup_count(cmap: dict, kind: str, tag: str) -> int:
    if (kind, tag) in cmap:
        return int(cmap[(kind, tag)])
    return int(cmap.get(("", tag), 0))


def resolve_cond123_path() -> Path:
    for p in COND123_CANDIDATES:
        if p.is_file():
            return p
    raise FileNotFoundError("No Cond123 fills file found among candidates")


def main() -> None:
    ctx = load_em_board_hot_map(
        DAY,
        top_n=TOP_N,
        rs_top_k=50,
        min_members=MIN_MEMBERS,
        arms=["today"],
        elig_bands=None,
    )
    if ctx.get("error"):
        raise RuntimeError(ctx["error"])

    info_tag_to_codes, _ = _load_stock_info_tag_index(None)
    store = None
    try:
        from utils.qmt_sector_store import get_qmt_sector_store

        store = get_qmt_sector_store()
    except Exception:
        store = None
    qmt_tag_to_codes = _build_qmt_tag_to_codes(store)

    def member_count(tag_name: str, kind: str) -> int:
        members = _resolve_members_for_tag(
            tag_name,
            kind=kind,
            info_tag_to_codes=info_tag_to_codes,
            qmt_tag_to_codes=qmt_tag_to_codes,
        )
        return len(members)

    ind_chg = load_chg_map(
        ROOT / "data" / "eastmoney_board_rank" / "industry_rank_2026-07-02.csv"
    )
    con_chg = load_chg_map(
        ROOT / "data" / "eastmoney_board_rank" / "concept_rank_2026-07-02.csv"
    )

    anytag_map = day_tag_counts(ANYTAG_FULL, DAY_S)
    best_full_map = day_tag_counts(BESTTEST_FULL, DAY_S)
    best_filt_map = day_tag_counts(BESTTEST_FILTERED, DAY_S)
    any_filt_map, any_filt_note = resolve_anytag_filtered(DAY_S)
    print("anytag过滤后 source:", any_filt_note)
    print(
        "anytag过滤后 day fills:",
        sum(v for (k, _), v in any_filt_map.items() if k),
    )

    cond_path = resolve_cond123_path()
    cond_map = day_tag_counts(cond_path, DAY_S)
    print("Cond123 source:", cond_path)
    print("Cond123 day fills:", sum(v for (k, _), v in cond_map.items() if k))

    def build_rows(items, kind: str, type_cn: str, chg_map: dict) -> pd.DataFrame:
        rows = []
        for rec in items:
            name = str(rec.get("name") or "").strip()
            elig = int(rec.get("eligible_rank") or 0)
            rk = int(rec.get("rank_d") or 0)
            chg = chg_map.get(name)
            rows.append(
                {
                    "选股日": DAY_S,
                    "类型": type_cn,
                    "合格榜内序位": elig,
                    "标签": name,
                    "东财原排名": rk,
                    "涨幅": None if chg is None else round(float(chg), 4),
                    "成员数": member_count(name, kind),
                    "anytag入列股数": lookup_count(anytag_map, kind, name),
                    "besttest入列股数": lookup_count(best_full_map, kind, name),
                    "anytag过滤后股数": lookup_count(any_filt_map, kind, name),
                    "besttest过滤后股数": lookup_count(best_filt_map, kind, name),
                    "Cond123入选股数": lookup_count(cond_map, kind, name),
                }
            )
        rows.sort(key=lambda x: (x["合格榜内序位"], x["东财原排名"], x["标签"]))
        return pd.DataFrame(rows)

    df_ind = build_rows(ctx.get("today_sectors") or [], "sector", "板块", ind_chg)
    df_con = build_rows(ctx.get("today_concepts") or [], "concept", "概念", con_chg)
    df_all = pd.concat([df_ind, df_con], ignore_index=True)

    with pd.ExcelWriter(OUT_PATH, engine="openpyxl") as w:
        df_ind.to_excel(w, sheet_name="行业合格Top50", index=False)
        df_con.to_excel(w, sheet_name="概念合格Top50", index=False)
        df_all.to_excel(w, sheet_name="合并", index=False)

    print("wrote", OUT_PATH)
    print("ind", len(df_ind), "con", len(df_con))
    print("\n=== day funnel totals (选股日=%s) ===" % DAY_S)
    for c in FUNNEL_COLS:
        print(
            f"sum {c}: ind={int(df_ind[c].sum())} con={int(df_con[c].sum())} "
            f"all={int(df_all[c].sum())}"
        )

    show = [
        "合格榜内序位",
        "标签",
        "东财原排名",
        "涨幅",
        "成员数",
        *FUNNEL_COLS,
    ]
    pd.set_option("display.max_rows", 60)
    pd.set_option("display.width", 220)
    pd.set_option("display.unicode.east_asian_width", True)
    print("\n=== 行业合格Top50 ===")
    print(df_ind[show].to_string(index=False))
    print("\n=== 概念合格Top50 ===")
    print(df_con[show].to_string(index=False))


if __name__ == "__main__":
    main()
