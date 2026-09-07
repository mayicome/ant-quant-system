# -*- coding: utf-8 -*-
"""按第二版硬条件缩池：过滤布林%b选股结果与回测汇总（不重跑回测）。

条件：
  MA10归一斜率 >= -0.004
  流通市值 < 80 亿
  最佳板块排名 ∈[1,50] 或 所属概念最高排名 ∈[1,50]

用法:
  python tools/filter_bb_pctb_shrink_v2.py
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "history_data" / "布林%b回落选股"
BAK = DIR / "备份-全量未缩池"

import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SLOPE_MIN = -0.004
MV_MAX = 80.0
RANK_LO = 1
RANK_HI = 50


def _rank_ok(s: pd.Series) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    return (x >= float(RANK_LO)) & (x <= float(RANK_HI))


def mask_rows(df: pd.DataFrame) -> pd.Series:
    sn = pd.to_numeric(df["MA10归一化斜率"], errors="coerce")
    mv = pd.to_numeric(df["流通市值_亿"], errors="coerce")
    best = df["最佳板块排名"] if "最佳板块排名" in df.columns else pd.Series(pd.NA, index=df.index)
    con = (
        df["所属概念最高排名名次"]
        if "所属概念最高排名名次" in df.columns
        else pd.Series(pd.NA, index=df.index)
    )
    return (sn >= SLOPE_MIN) & (mv < MV_MAX) & (_rank_ok(best) | _rank_ok(con))


def _keys(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    code_col = "股票代码" if "股票代码" in out.columns else ("代码" if "代码" in out.columns else None)
    if code_col is None:
        raise KeyError("无股票代码/代码列")
    out["_code"] = out[code_col].astype(str).str.strip().str.zfill(6)
    out["_sel"] = pd.to_datetime(out["选股日"], errors="coerce").dt.strftime("%Y-%m-%d")
    return out


def _backup(path: Path) -> None:
    BAK.mkdir(parents=True, exist_ok=True)
    dest = BAK / path.name
    if not dest.exists():
        shutil.copy2(path, dest)
        print(f"  backup -> {dest.name}")


def filter_selection() -> set[tuple[str, str]]:
    src_full = DIR / "选股结果_布林%b回落选股_全条件_2025-01-02_2026-08-14.xls"
    src_main = DIR / "选股结果_布林%b回落选股_2025-01-02_2026-08-14.xls"
    # 优先从全量底池筛；若无则从主选股文件筛
    src = src_full if src_full.is_file() else src_main
    if not src.is_file():
        raise FileNotFoundError(f"缺少选股文件: {src}")
    print(f"读选股: {src.name}")
    df = pd.read_excel(src)
    before = len(df)
    keep = df.loc[mask_rows(df)].copy()
    # 写入新条件列，方便对照
    keep["条件_MA10斜率达标"] = True
    keep["条件_流通市值<80亿"] = True
    keep["条件_板块或概念排名1to50"] = True
    keep["满足条件"] = True
    keep["MA10_SLOPE_NORM_MIN"] = SLOPE_MIN
    keep["MAX_FLOAT_MV_YI"] = MV_MAX
    keep["BOARD_RANK_LO"] = RANK_LO
    keep["BOARD_RANK_HI"] = RANK_HI

    if src_main.is_file():
        _backup(src_main)
    from sector_stock_filter import save_xls_with_text_code

    save_xls_with_text_code(str(src_main), keep)
    print(f"写选股: {src_main.name}  {before} -> {len(keep)}")

    keyed = _keys(keep)
    return set(zip(keyed["_code"], keyed["_sel"]))


def filter_xlsx(path: Path, keys: set[tuple[str, str]]) -> None:
    if not path.is_file():
        return
    print(f"读汇总: {path.name}")
    df = pd.read_excel(path)
    before = len(df)
    keyed = _keys(df)
    # 若表内已有斜率/市值列，直接按条件筛（与选股一致，避免键缺失）
    if "MA10归一化斜率" in df.columns and "流通市值_亿" in df.columns:
        m = mask_rows(df)
    else:
        m = keyed.apply(lambda r: (r["_code"], r["_sel"]) in keys, axis=1)
    out = df.loc[m].copy()
    _backup(path)
    out.to_excel(path, index=False)
    print(f"写汇总: {path.name}  {before} -> {len(out)}")


def filter_csv(path: Path, keys: set[tuple[str, str]]) -> None:
    if not path.is_file():
        return
    print(f"读成交: {path.name}")
    df = pd.read_csv(path, encoding="utf-8-sig")
    before = len(df)
    keyed = _keys(df)
    m = keyed.apply(lambda r: (r["_code"], r["_sel"]) in keys, axis=1)
    out = df.loc[m].copy()
    _backup(path)
    out.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"写成交: {path.name}  {before} -> {len(out)}")


def rebuild_daily_from_per_stock(per_stock: Path, daily_out: Path) -> None:
    """由按票表重算日汇总（若存在）。"""
    if not per_stock.is_file():
        return
    df = pd.read_excel(per_stock)
    if "收益率pct" not in df.columns or "选股日" not in df.columns:
        return
    df = df.copy()
    df["选股日"] = pd.to_datetime(df["选股日"], errors="coerce")
    df["收益率pct"] = pd.to_numeric(df["收益率pct"], errors="coerce")
    g = df.groupby(df["选股日"].dt.strftime("%Y-%m-%d"), dropna=True)
    rows = []
    for sel, sub in g:
        r = sub["收益率pct"].dropna()
        rows.append(
            {
                "选股日": sel,
                "选股只数": int(len(sub)),
                "有收益样本数": int(len(r)),
                "均收益pct": float(r.mean()) if len(r) else None,
                "中位收益pct": float(r.median()) if len(r) else None,
                "胜率pct": float((r > 0).mean() * 100) if len(r) else None,
            }
        )
    out = pd.DataFrame(rows).sort_values("选股日")
    if daily_out.is_file():
        _backup(daily_out)
    out.to_excel(daily_out, index=False)
    print(f"重写日汇总: {daily_out.name}  rows={len(out)}")


def main() -> int:
    keys = filter_selection()
    print(f"缩池键数 (代码,选股日) = {len(keys)}")

    # 按票汇总：latest + 已完成 latest；时间戳全量副本留在备份目录，不改时间戳原件
    for name in (
        "各日选股收益汇总_日线-bb_pctb-bb_pctb_sell-单点_按票_latest.xlsx",
        "各日选股收益汇总_日线-bb_pctb-bb_pctb_sell-单点_按票_已完成_latest.xlsx",
    ):
        filter_xlsx(DIR / name, keys)

    # 日汇总
    for name in (
        "各日选股收益汇总_日线-bb_pctb-bb_pctb_sell-单点_latest.xlsx",
    ):
        p = DIR / name
        if p.is_file():
            rebuild_daily_from_per_stock(
                DIR / "各日选股收益汇总_日线-bb_pctb-bb_pctb_sell-单点_按票_已完成_latest.xlsx",
                p,
            )

    # 成交明细 latest（与缩池一致，便于监控）
    for name in (
        "回测成交明细_日线-bb_pctb-bb_pctb_sell买入_latest.csv",
        "回测成交明细_日线-bb_pctb-bb_pctb_sell卖出_latest.csv",
    ):
        filter_csv(DIR / name, keys)

    # 统计
    bt = pd.read_excel(DIR / "各日选股收益汇总_日线-bb_pctb-bb_pctb_sell-单点_按票_已完成_latest.xlsx")
    ret = pd.to_numeric(bt["收益率pct"], errors="coerce").dropna()
    print(
        f"缩池后按票: n={len(bt)}  mean={ret.mean():.3f}%  median={ret.median():.3f}%  "
        f"win={(ret > 0).mean() * 100:.1f}%"
    )
    print(f"全量备份目录: {BAK}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
