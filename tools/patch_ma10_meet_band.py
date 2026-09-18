# -*- coding: utf-8 -*-
"""将马总次日MA10 结果表中的「满足条件」就地改写为当前 RANK_MEET 区间（默认[5,38]）。

不重跑选股/回测：用表内分项条件 + 行业/概念名次重算 meet，并更新相关列。

用法:
  python tools/patch_ma10_meet_band.py
  python tools/patch_ma10_meet_band.py --lo 5 --hi 38
"""
from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "history_data" / "马总选股逻辑"
RULE_SRC = ROOT / "tools" / "_rule_src_ma_zong_next_day_ma10.py"


def _load_rule():
    spec = importlib.util.spec_from_file_location("ma10_rule_src", RULE_SRC)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _as_bool(s: pd.Series) -> pd.Series:
    out: List[bool] = []
    for v in s:
        if isinstance(v, bool):
            out.append(v)
        elif pd.isna(v):
            out.append(False)
        elif str(v).strip().lower() in ("1", "true", "yes", "y", "是"):
            out.append(True)
        elif str(v).strip().lower() in ("0", "false", "no", "n", "否"):
            out.append(False)
        else:
            try:
                out.append(bool(int(float(v))))
            except Exception:
                out.append(False)
    return pd.Series(out, index=s.index)


def _rank_ok(series: pd.Series, lo: int, hi: int) -> pd.Series:
    x = pd.to_numeric(series, errors="coerce")
    return (x >= float(lo)) & (x <= float(hi))


def _board_actual(row: pd.Series) -> str:
    parts = []
    ir = row.get("所属行业最高排名名次")
    iname = row.get("所属行业最高排名名称") or ""
    if ir not in ("", None) and not (isinstance(ir, float) and pd.isna(ir)):
        parts.append("行业%s#%s" % (iname, ir))
    cr = row.get("所属概念最高排名名次")
    cname = row.get("所属概念最高排名名称") or ""
    if cr not in ("", None) and not (isinstance(cr, float) and pd.isna(cr)):
        parts.append("概念%s#%s" % (cname, cr))
    return "、".join(parts) if parts else "无所属行业/概念可匹配东财涨幅榜"


def _rebuild_fail_reason(
    row: pd.Series,
    *,
    cond_rank: bool,
    cond_prior: bool,
    cond_ma: bool,
    cond_boll: bool,
    lo: int,
    hi: int,
) -> str:
    reasons = []
    if not cond_rank:
        reasons.append(
            "行业/概念排名不满足，要求名次∈[%d,%d]（行业或概念任一），实际%s"
            % (lo, hi, _board_actual(row))
        )
    if not cond_prior:
        reasons.append("前10日无大涨不满足")
    if not cond_ma:
        reasons.append("收盘站上MA5且MA20不满足")
    if not cond_boll:
        reasons.append("收盘站上布林上轨不满足")
    return "；".join(reasons)


def patch_frame(df: pd.DataFrame, *, lo: int, hi: int) -> tuple[pd.DataFrame, dict]:
    need = [
        "条件_前10日无大涨",
        "条件_收盘站上MA5且MA20",
        "条件_收盘站上布林上轨",
        "所属行业最高排名名次",
        "所属概念最高排名名次",
    ]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError("缺列: %s" % missing)

    out = df.copy()
    prior = _as_bool(out["条件_前10日无大涨"])
    ma = _as_bool(out["条件_收盘站上MA5且MA20"])
    boll = _as_bool(out["条件_收盘站上布林上轨"])
    rank = _rank_ok(out["所属行业最高排名名次"], lo, hi) | _rank_ok(
        out["所属概念最高排名名次"], lo, hi
    )
    meet = prior & ma & boll & rank

    # drop old band-named columns
    drop_cols = [
        c
        for c in out.columns
        if str(c).startswith("条件_行业或概念排名[")
        or str(c) == "条件_行业或概念中热[9,32]"
    ]
    out = out.drop(columns=drop_cols, errors="ignore")

    band_col = "条件_行业或概念排名[%d,%d]" % (lo, hi)
    out[band_col] = rank.astype(bool)
    out["条件_行业或概念中热[9,32]"] = rank.astype(bool)  # 兼容旧列名
    out["满足条件中热名次下限"] = int(lo)
    out["满足条件中热名次上限"] = int(hi)
    out["满足条件"] = meet.astype(bool)

    if "不满足的原因" in out.columns:
        reasons = []
        for i in out.index:
            if bool(meet.loc[i]):
                reasons.append("")
            else:
                reasons.append(
                    _rebuild_fail_reason(
                        out.loc[i],
                        cond_rank=bool(rank.loc[i]),
                        cond_prior=bool(prior.loc[i]),
                        cond_ma=bool(ma.loc[i]),
                        cond_boll=bool(boll.loc[i]),
                        lo=lo,
                        hi=hi,
                    )
                )
        out["不满足的原因"] = reasons

    # put new band col near other 条件_ columns if possible
    stats = {
        "n": int(len(out)),
        "n_meet": int(meet.sum()),
        "meet_pct": round(100.0 * float(meet.mean()), 1) if len(out) else 0.0,
    }
    return out, stats


def _save(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".xls":
        # xlwt 对大表慢且列类型受限；改写为同名 xlsx，旧 xls 备份后删除
        xlsx = path.with_suffix(".xlsx")
        bak = path.with_suffix(path.suffix + ".bak_pre_538")
        if not bak.exists():
            shutil.copy2(path, bak)
        df.to_excel(xlsx, index=False, engine="openpyxl")
        path.unlink(missing_ok=True)
        return xlsx
    # xlsx: backup once
    bak = path.with_name(path.stem + ".bak_pre_538.xlsx")
    if not bak.exists() and path.exists():
        shutil.copy2(path, bak)
    df.to_excel(path, index=False, engine="openpyxl")
    return path


def discover_targets() -> List[Path]:
    out: List[Path] = []
    for p in sorted(DIR.glob("选股结果_马总选股逻辑-次日MA10_*.xls*")):
        if ".bak_" in p.name or p.name.startswith("~$"):
            continue
        out.append(p)
    for p in sorted(DIR.glob("各日选股收益汇总_日线-ma10-sell_half*-单点_按票*.xlsx")):
        if ".bak_" in p.name or p.name.startswith("~$"):
            continue
        # 只改根目录现行结果，不改 hold2/hold8 等归档子目录
        if p.parent != DIR:
            continue
        out.append(p)
    # dedupe by resolve
    seen = set()
    uniq = []
    for p in out:
        k = p.resolve()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(p)
    return uniq


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="就地改写马总MA10结果满足条件区间")
    ap.add_argument("--lo", type=int, default=None)
    ap.add_argument("--hi", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)

    rule = _load_rule()
    lo = int(args.lo if args.lo is not None else rule.RANK_MEET_LO)
    hi = int(args.hi if args.hi is not None else rule.RANK_MEET_HI)
    print("band=[%d,%d] dir=%s" % (lo, hi, DIR))

    targets = discover_targets()
    if not targets:
        print("no targets")
        return 1

    for p in targets:
        try:
            df = pd.read_excel(p)
        except Exception as e:
            print("SKIP read fail", p.name, e)
            continue
        if "满足条件" not in df.columns:
            print("SKIP no 满足条件", p.name)
            continue
        try:
            patched, stats = patch_frame(df, lo=lo, hi=hi)
        except Exception as e:
            print("SKIP patch fail", p.name, e)
            continue
        print(
            "%s -> meet %d/%d (%.1f%%)"
            % (p.name, stats["n_meet"], stats["n"], stats["meet_pct"])
        )
        if args.dry_run:
            continue
        out_path = _save(patched, p)
        if out_path != p:
            print("  wrote", out_path.name, "(from", p.name + ")")
        else:
            print("  wrote", out_path.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
