# -*- coding: utf-8 -*-
"""按新口径重算按票汇总表：近10日涨停次数/距今（不含选股日）。"""
from __future__ import annotations

import sys
import warnings
from datetime import date
from pathlib import Path

warnings.filterwarnings("ignore")

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from utils.daily_cache_reader import load_daily_from_cache  # noqa: E402

# 复用已修正的规则源逻辑
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "ma_zong_logic1", ROOT / "tools" / "_rule_src_ma_zong_logic1.py"
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)

_recent_lu_stats = _mod._recent_lu_stats
_as_date = _mod._as_date
_code6 = _mod._code6

LOOKBACK = 10
COL_CNT = "最近10个交易日内的涨停板数量"
COL_AGO = "最近的涨停板是几日前"

FILES = [
    ROOT
    / "history_data"
    / "马总选股逻辑"
    / "各日选股收益汇总_日线-ma10-单点_按票_20260815_100317.xlsx",
    ROOT
    / "history_data"
    / "马总选股逻辑"
    / "各日选股收益汇总_日线-ma10-单点_按票_20260815_100317_收盘上MA10.xlsx",
]


def _code_for_cache(v) -> str:
    s = str(v or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return _code6(s)


def recompute_df(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    cache: dict[str, pd.DataFrame | None] = {}
    counts_old = pd.to_numeric(df[COL_CNT], errors="coerce")
    agos_old = df[COL_AGO]
    new_cnt = []
    new_ago = []
    miss = 0
    changed = 0
    for i, row in df.iterrows():
        code = _code_for_cache(row.get("代码") or row.get("股票代码"))
        name = str(row.get("股票名称") or "")
        as_of = _as_date(row.get("选股日"))
        if code not in cache:
            cache[code] = load_daily_from_cache(code, through_date=None)
        daily = cache[code]
        if daily is None or daily.empty or as_of is None:
            miss += 1
            new_cnt.append(0)
            new_ago.append("")
            continue
        # 按行 through_date 切片，避免跨日污染；cache 存全量
        sub = daily[daily["date"] <= as_of]
        cnt, ago = _recent_lu_stats(code, name, sub, as_of, LOOKBACK)
        new_cnt.append(int(cnt))
        new_ago.append("" if ago == "" else int(ago))
        old_c = counts_old.loc[i]
        old_a = agos_old.loc[i]
        try:
            old_a_n = int(old_a) if pd.notna(old_a) and str(old_a).strip() != "" else None
        except Exception:
            old_a_n = None
        if (pd.isna(old_c) or int(old_c) != int(cnt)) or (
            old_a_n != (None if ago == "" else int(ago))
        ):
            changed += 1

    out = df.copy()
    out[COL_CNT] = new_cnt
    out[COL_AGO] = new_ago
    stats = {
        "n": len(out),
        "miss": miss,
        "changed": changed,
        "has_lu": int((out[COL_CNT] > 0).sum()),
        "no_lu": int((out[COL_CNT] == 0).sum()),
        "cnt_mean": float(out[COL_CNT].mean()),
        "ago_vc": out.loc[out[COL_CNT] > 0, COL_AGO].value_counts().head(12).to_dict(),
        "cnt_vc": out[COL_CNT].value_counts().sort_index().head(12).to_dict(),
    }
    return out, stats


def analyze_returns(df: pd.DataFrame) -> None:
    if "收益率pct" not in df.columns:
        return
    d = df.copy()
    d["ret"] = pd.to_numeric(d["收益率pct"], errors="coerce")
    d = d[d["ret"].notna()]
    d["sel"] = pd.to_datetime(d["选股日"]).dt.strftime("%Y-%m-%d")
    d["ret_cs"] = d["ret"] - d.groupby("sel")["ret"].transform("mean")
    d["has"] = pd.to_numeric(d[COL_CNT], errors="coerce").fillna(0) > 0

    def _show(lab, g):
        if len(g) == 0:
            print(f"  {lab}: n=0")
            return
        r, cs = g["ret"], g["ret_cs"]
        print(
            f"  {lab}: n={len(g):4d} mean={r.mean():+.3f} med={r.median():+.3f} "
            f"win={(r > 0).mean() * 100:5.1f}% cs={cs.mean():+.3f}"
        )

    print("=== 是否有涨停（不含选股日）===")
    _show("无涨停", d[~d["has"]])
    _show("有涨停", d[d["has"]])
    print("=== 涨停次数 ===")
    clipped = d[COL_CNT].clip(upper=3).astype(int)
    for k, g in d.groupby(clipped):
        lab = str(k) if k < 3 else ">=3"
        _show(f"次数={lab}", g)
    print("=== 最近涨停距今（有涨停）===")
    g0 = d[d["has"]].copy()
    bands = [
        ("1日", g0[COL_AGO] == 1),
        ("2-3日", g0[COL_AGO].between(2, 3)),
        ("4-5日", g0[COL_AGO].between(4, 5)),
        ("6-10日", g0[COL_AGO].between(6, 10)),
    ]
    for lab, m in bands:
        _show(lab, g0[m])


def main() -> None:
    for path in FILES:
        if not path.is_file():
            print("missing", path)
            continue
        print("\n####", path.name)
        df = pd.read_excel(path)
        out, st = recompute_df(df)
        out.to_excel(path, index=False)
        print(
            f"wrote n={st['n']} miss={st['miss']} changed={st['changed']} "
            f"has_lu={st['has_lu']} no_lu={st['no_lu']} cnt_mean={st['cnt_mean']:.3f}"
        )
        print("cnt_vc", st["cnt_vc"])
        print("ago_vc", st["ago_vc"])
        analyze_returns(out)


if __name__ == "__main__":
    main()
