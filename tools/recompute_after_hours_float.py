#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用已有 detail 补流通股并重算三榜（不重下 tick）。

用法：
  python tools/recompute_after_hours_float.py
  python tools/recompute_after_hours_float.py --days 20260714,20260715,20260716
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _after_vol_to_shares(
    after_vol: float, after_amount: float = 0.0, last_price: float = 0.0
) -> float:
    if after_vol is None or after_vol <= 0:
        return 0.0
    vol = float(after_vol)
    try:
        amt = float(after_amount or 0)
        px = float(last_price or 0)
    except Exception:
        amt, px = 0.0, 0.0
    if amt > 0 and px > 0 and vol > 0:
        ratio = (amt / vol) / px
        if ratio >= 10.0:
            return vol * 100.0
        return vol
    return vol * 100.0


def _pct(v: Any) -> str:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        return f"{100 * float(v):.3f}%"
    except Exception:
        return ""


def _num(v: Any) -> str:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        return f"{float(v):.3f}"
    except Exception:
        return ""


def _wan(v: Any) -> str:
    try:
        x = float(v)
        return f"{x / 10000.0:.2f}" if x > 0 else ""
    except Exception:
        return ""


def _normalize_detail(df: pd.DataFrame) -> pd.DataFrame:
    colmap = {
        "代码": "code",
        "名称": "name",
        "全天量": "day_vol",
        "全天额": "day_amount",
        "收盘竞价量": "close_auc_vol",
        "收盘竞价额": "close_auc_amount",
        "盘后量": "after_vol",
        "盘后额": "after_amount",
        "盘后占全天": "after_vs_day",
        "盘后相对竞价": "after_vs_close_auc",
        "流通股万股": "float_shares_wan",
        "盘后占流通": "after_vs_float",
    }
    df = df.rename(columns={k: v for k, v in colmap.items() if k in df.columns})
    for c in (
        "day_vol",
        "day_amount",
        "close_auc_vol",
        "close_auc_amount",
        "after_vol",
        "after_amount",
        "after_vs_day",
        "after_vs_close_auc",
        "after_vs_float",
        "float_shares",
    ):
        if c in df.columns:
            # 百分比字符串 → 小数
            if c in ("after_vs_day", "after_vs_float"):
                s = df[c].astype(str).str.replace("%", "", regex=False)
                num = pd.to_numeric(s, errors="coerce")
                # 若原值带 %，已是百分数口径（如 1.046），需 /100
                has_pct = df[c].astype(str).str.contains("%", na=False)
                df[c] = num.where(~has_pct, num / 100.0)
            else:
                df[c] = pd.to_numeric(df[c], errors="coerce")
    if "float_shares_wan" in df.columns and "float_shares" not in df.columns:
        wan = pd.to_numeric(df["float_shares_wan"], errors="coerce")
        df["float_shares"] = wan * 10000.0
    return df


def recompute_day(day: str, top_n: int = 10, instrument_meta=None) -> Path:
    out_dir = ROOT / "data" / "after_hours_rank" / day
    src = out_dir / "detail.csv"
    if not src.is_file():
        raise FileNotFoundError(str(src))

    df = pd.read_csv(src, encoding="utf-8-sig")
    df = _normalize_detail(df)
    print(f"[{day}] loaded {len(df)} rows")

    codes = df["code"].astype(str).tolist()
    names: List[str] = []
    floats: List[float] = []
    for i, code in enumerate(codes):
        n, fs = instrument_meta(code)
        old = df.at[i, "name"] if "name" in df.columns else ""
        if pd.isna(old) or str(old).strip() in ("", "nan"):
            names.append(n or "")
        else:
            names.append(str(old))
        floats.append(float(fs or 0))
        if (i + 1) % 1000 == 0:
            print(f"[{day}] meta {i + 1}/{len(codes)}")

    df["name"] = names
    df["float_shares"] = floats

    after_vs_float: List[Optional[float]] = []
    for _, row in df.iterrows():
        av = float(row.get("after_vol") or 0)
        aa = float(row.get("after_amount") or 0)
        fs = float(row.get("float_shares") or 0)
        if av > 0 and fs > 0:
            after_vs_float.append(_after_vol_to_shares(av, aa) / fs)
        else:
            after_vs_float.append(None)
    df["after_vs_float"] = after_vs_float

    def _safe_div(a, b):
        try:
            a = float(a)
            b = float(b)
            return a / b if b > 0 else None
        except Exception:
            return None

    # 保证占比列存在
    if "after_vs_day" not in df.columns:
        df["after_vs_day"] = [
            _safe_div(r.after_vol, r.day_vol) for r in df.itertuples()
        ]
    else:
        # 空值补算
        for i, row in df.iterrows():
            if pd.isna(row.get("after_vs_day")):
                df.at[i, "after_vs_day"] = _safe_div(row.get("after_vol"), row.get("day_vol"))
    if "after_vs_close_auc" not in df.columns:
        df["after_vs_close_auc"] = [
            _safe_div(r.after_vol, r.close_auc_vol) for r in df.itertuples()
        ]
    else:
        for i, row in df.iterrows():
            if pd.isna(row.get("after_vs_close_auc")):
                df.at[i, "after_vs_close_auc"] = _safe_div(
                    row.get("after_vol"), row.get("close_auc_vol")
                )

    ranked_day = (
        df[df["after_vol"].fillna(0) > 0]
        .sort_values("after_vs_day", ascending=False)
        .head(top_n)
    )
    ranked_auc = (
        df[(df["after_vol"].fillna(0) > 0) & (df["close_auc_vol"].fillna(0) > 0)]
        .sort_values("after_vs_close_auc", ascending=False)
        .head(top_n)
    )
    ranked_float = (
        df[
            (df["after_vol"].fillna(0) > 0)
            & (df["float_shares"].fillna(0) > 0)
            & df["after_vs_float"].notna()
        ]
        .sort_values("after_vs_float", ascending=False)
        .head(top_n)
    )

    day_list = ranked_day.to_dict("records")
    auc_list = ranked_auc.to_dict("records")
    float_list = ranked_float.to_dict("records")
    day_codes = {str(r["code"]) for r in day_list}
    auc_codes = {str(r["code"]) for r in auc_list}
    float_codes = {str(r["code"]) for r in float_list}

    by_code: Dict[str, Dict[str, Any]] = {}
    for r in day_list + auc_list + float_list:
        c = str(r["code"])
        if c not in by_code:
            by_code[c] = dict(r)
        else:
            cur = by_code[c]
            for k, v in r.items():
                if cur.get(k) in (None, "") or (
                    isinstance(cur.get(k), float) and pd.isna(cur.get(k))
                ):
                    if v is not None and not (isinstance(v, float) and pd.isna(v)):
                        cur[k] = v

    merged = []
    for c, r in by_code.items():
        tags = []
        if c in day_codes:
            tags.append("盘后占全天")
        if c in auc_codes:
            tags.append("盘后相对竞价")
        if c in float_codes:
            tags.append("盘后占流通")
        merged.append(
            {
                "代码": c,
                "名称": r.get("name", ""),
                "盘后量": r.get("after_vol", ""),
                "全天量": r.get("day_vol", ""),
                "收盘竞价量": r.get("close_auc_vol", ""),
                "流通股万股": _wan(r.get("float_shares")),
                "盘后占全天": _pct(r.get("after_vs_day")),
                "盘后相对竞价": _num(r.get("after_vs_close_auc")),
                "盘后占流通": _pct(r.get("after_vs_float")),
                "入选": "+".join(tags),
            }
        )

    def _sk(row):
        tags = str(row.get("入选") or "")
        n = tags.count("+") + (1 if tags else 0)
        try:
            fp = float(str(row.get("盘后占流通") or "").replace("%", "") or 0)
        except Exception:
            fp = 0.0
        try:
            dp = float(str(row.get("盘后占全天") or "").replace("%", "") or 0)
        except Exception:
            dp = 0.0
        return (-n, -fp, -dp)

    merged.sort(key=_sk)

    detail_cn = pd.DataFrame(
        {
            "代码": df["code"],
            "名称": df["name"],
            "全天量": df["day_vol"],
            "全天额": df["day_amount"],
            "收盘竞价量": df["close_auc_vol"],
            "收盘竞价额": df["close_auc_amount"],
            "盘后量": df["after_vol"],
            "盘后额": df["after_amount"],
            "流通股万股": [_wan(v) for v in df["float_shares"]],
            "盘后占全天": [_pct(v) for v in df["after_vs_day"]],
            "盘后相对竞价": [_num(v) for v in df["after_vs_close_auc"]],
            "盘后占流通": [_pct(v) for v in df["after_vs_float"]],
        }
    )

    bak = out_dir / f"detail_backup_before_float_{day}.csv"
    if not bak.exists():
        shutil.copy2(src, bak)
        print(f"[{day}] backup -> {bak.name}")

    detail_path = out_dir / "detail.csv"
    top_path = out_dir / "top10.csv"
    detail_cn.to_csv(detail_path, index=False, encoding="utf-8-sig")
    pd.DataFrame(merged).to_csv(top_path, index=False, encoding="utf-8-sig")

    for legacy in ("top10_after_vs_day.csv", "top10_after_vs_close_auc.csv"):
        lp = out_dir / legacy
        if lp.is_file():
            try:
                lp.unlink()
            except Exception:
                pass

    n_float = int((df["float_shares"] > 0).sum())
    n_ratio = int(df["after_vs_float"].notna().sum())
    print(
        f"[{day}] done detail={detail_path.name} top10={len(merged)} "
        f"float>0={n_float} after_vs_float={n_ratio}"
    )
    return out_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="补流通股并重算盘后三榜")
    parser.add_argument(
        "--days",
        default="",
        help="逗号分隔 YYYYMMDD；默认处理 after_hours_rank 下全部目录",
    )
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument(
        "--skip",
        default="",
        help="跳过的日期，逗号分隔",
    )
    args = parser.parse_args()

    import xtquant.xtdata as xtdata

    xtdata.enable_hello = False
    # 探测连接
    _ = xtdata.get_data_dir()

    from tools.after_hours_volume_rank import _instrument_meta

    base = ROOT / "data" / "after_hours_rank"
    if args.days.strip():
        days = [d.strip() for d in args.days.split(",") if d.strip()]
    else:
        days = sorted(
            p.name for p in base.iterdir() if p.is_dir() and p.name.isdigit()
        )

    skip = {d.strip() for d in args.skip.split(",") if d.strip()}
    days = [d for d in days if d not in skip]
    if not days:
        print("no days")
        return 1

    for day in days:
        try:
            recompute_day(day, top_n=max(1, int(args.top or 10)), instrument_meta=_instrument_meta)
        except Exception as e:
            print(f"[{day}] FAILED: {e}")
            import traceback

            traceback.print_exc()
            return 1
    print("all done:", ", ".join(days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
