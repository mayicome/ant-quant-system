# -*- coding: utf-8 -*-
"""用成份股日线等权涨跌幅补齐缺失的东财板块日 K（hist 不可用时）。

优先使用 ``_hist_cache`` 中已有的真实 push2his 日 K；
仅对缺失板块，用成份股 ``data/daily_cache`` 等权平均涨跌幅合成。

注意：合成涨跌幅 ≠ 东财官方板块指数涨跌幅（权重/样本不同），仅作排名近似。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.backfill_em_board_rank_from_hist import (  # noqa: E402
    CACHE_DIR,
    OUT_DIR,
    _BOARD_OUT_COLS,
    _klines_to_df,
    _load_hist_cache,
    _save_csv,
    _save_hist_df,
    _ymd,
)

DAILY_CACHE = os.path.join(ROOT, "data", "daily_cache")
CONS_DIR = os.path.join(CACHE_DIR, "_cons")


def _norm_stem(code6: str) -> Optional[str]:
    s = "".join(ch for ch in str(code6) if ch.isdigit())
    if len(s) < 6:
        return None
    s = s[-6:]
    if s.startswith(("6", "9")):
        return f"{s}.SH"
    if s.startswith(("0", "3")):
        return f"{s}.SZ"
    if s.startswith(("4", "8")):
        return f"{s}.BJ"
    return f"{s}.SZ"


def _load_board_meta(kind: str) -> pd.DataFrame:
    path = os.path.join(OUT_DIR, f"{kind}_rank_2026-08-03.csv")
    df = pd.read_csv(path, dtype=str)
    out = df[["板块名称", "板块代码"]].copy()
    out["板块代码"] = out["板块代码"].astype(str).str.strip().str.upper()
    out["板块名称"] = out["板块名称"].astype(str).str.strip()
    return out.drop_duplicates("板块代码")


def _pending_without_hist(kind: str, start: date, end: date) -> List[Tuple[str, str]]:
    meta = _load_board_meta(kind)
    pending = []
    for _, row in meta.iterrows():
        code = str(row["板块代码"])
        name = str(row["板块名称"])
        cached = _load_hist_cache(kind, code)
        if cached is not None and not cached.empty:
            dmin = str(cached["日期"].min())[:10]
            dmax = str(cached["日期"].max())[:10]
            if dmin <= _ymd(start) and dmax >= _ymd(end):
                continue
        pending.append((code, name))
    return pending


def ingest_cons_batch(path: str, kind: str) -> int:
    """摄入成份股批次 JSON: {kind, items:[{code, members:[code6,...]}]}"""
    os.makedirs(CONS_DIR, exist_ok=True)
    payload = json.load(open(path, encoding="utf-8"))
    if isinstance(payload, dict):
        kind = str(payload.get("kind") or kind)
        items = payload.get("items") or []
    else:
        items = payload
    n = 0
    for item in items:
        code = str(item.get("code") or "").strip().upper()
        members = item.get("members") or item.get("codes") or []
        if not code.startswith("BK"):
            continue
        members = [str(x).zfill(6)[-6:] for x in members if str(x).strip()]
        out = os.path.join(CONS_DIR, f"{kind}_{code}.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"code": code, "members": members}, f, ensure_ascii=False)
        n += 1
    print(f"[ok] cons ingested {n} from {path}")
    return n


def _load_cons(kind: str, code: str) -> List[str]:
    path = os.path.join(CONS_DIR, f"{kind}_{code}.json")
    if not os.path.isfile(path):
        return []
    payload = json.load(open(path, encoding="utf-8"))
    return list(payload.get("members") or [])


def _load_stock_pct_panel(stems: Set[str], start: date, end: date) -> pd.DataFrame:
    """返回 index=date, columns=stem 的涨跌幅(%)面板。"""
    start_s = _ymd(start)
    end_s = _ymd(end)
    # 需要多一天算首日涨跌幅
    widen_start = (start - timedelta(days=10)).strftime("%Y-%m-%d")
    frames = []
    for stem in stems:
        path = os.path.join(DAILY_CACHE, f"{stem}.csv")
        if not os.path.isfile(path):
            continue
        try:
            df = pd.read_csv(path, usecols=["date", "close"])
        except Exception:
            continue
        if df.empty:
            continue
        df["date"] = df["date"].astype(str).str[:10]
        df = df[(df["date"] >= widen_start) & (df["date"] <= end_s)].copy()
        if len(df) < 2:
            continue
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["close"]).sort_values("date")
        df["pct"] = df["close"].pct_change() * 100.0
        s = df.set_index("date")["pct"].rename(stem)
        frames.append(s)
    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames, axis=1).sort_index()
    panel = panel.loc[(panel.index >= start_s) & (panel.index <= end_s)]
    return panel


def synthesize_hist_from_cons(
    kind: str,
    code: str,
    name: str,
    panel: pd.DataFrame,
) -> Optional[pd.DataFrame]:
    members = _load_cons(kind, code)
    stems = []
    for m in members:
        st = _norm_stem(m)
        if st and st in panel.columns:
            stems.append(st)
    if not stems:
        return None
    sub = panel[stems]
    # 等权：对各股当日涨跌幅取均值（至少 1 只有效）
    avg = sub.mean(axis=1, skipna=True)
    avg = avg.dropna()
    if avg.empty:
        return None
    rows = []
    for d, pct in avg.items():
        rows.append(
            {
                "日期": str(d)[:10],
                "开盘": pd.NA,
                "收盘": pd.NA,
                "最高": pd.NA,
                "最低": pd.NA,
                "成交量": pd.NA,
                "成交额": pd.NA,
                "振幅": pd.NA,
                "涨跌幅": float(pct),
                "涨跌额": pd.NA,
                "换手率": pd.NA,
            }
        )
    df = pd.DataFrame(rows)
    # 标记为合成，便于排查
    jpath = os.path.join(CACHE_DIR, f"{kind}_{code}.synth.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(
            {
                "code": code,
                "name": name,
                "source": "equal_weight_daily_cache",
                "members_used": len(stems),
                "members_total": len(members),
                "rows": len(df),
            },
            f,
            ensure_ascii=False,
        )
    _save_hist_df(df, kind, code)
    return df


def fill_missing_with_synth(kind: str, start: date, end: date) -> int:
    pending = _pending_without_hist(kind, start, end)
    print(f"[info] {kind} missing hist: {len(pending)}")
    # collect all member stems
    all_stems: Set[str] = set()
    pending_ok = []
    for code, name in pending:
        members = _load_cons(kind, code)
        if not members:
            continue
        pending_ok.append((code, name, members))
        for m in members:
            st = _norm_stem(m)
            if st:
                all_stems.add(st)
    print(f"[info] {kind} with cons: {len(pending_ok)} stems={len(all_stems)}")
    panel = _load_stock_pct_panel(all_stems, start, end)
    print(f"[info] pct panel shape={panel.shape}")
    n = 0
    for code, name, _members in pending_ok:
        df = synthesize_hist_from_cons(kind, code, name, panel)
        if df is not None and not df.empty:
            n += 1
    print(f"[done] {kind} synthesized={n}")
    return n


def export_pending_cons(kind: str, start: date, end: date) -> str:
    pending = _pending_without_hist(kind, start, end)
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"_pending_cons_{kind}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "kind": kind,
                "pending": [{"板块代码": c, "板块名称": n} for c, n in pending],
            },
            f,
            ensure_ascii=False,
        )
    print(f"[ok] pending cons {kind}: {len(pending)} -> {path}")
    return path


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", default="both", choices=("both", "industry", "concept"))
    parser.add_argument("--start", default="2026-06-09")
    parser.add_argument("--end", default="2026-07-31")
    parser.add_argument("--ingest-cons", default="")
    parser.add_argument("--export-pending-cons", action="store_true")
    parser.add_argument("--fill-synth", action="store_true")
    args = parser.parse_args()
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    kinds = ("industry", "concept") if args.kind == "both" else (args.kind,)

    if args.ingest_cons:
        peek = json.load(open(args.ingest_cons, encoding="utf-8"))
        kind = str(peek.get("kind") or (args.kind if args.kind != "both" else ""))
        if not kind:
            raise SystemExit("need kind")
        ingest_cons_batch(args.ingest_cons, kind)
        return

    if args.export_pending_cons:
        for kind in kinds:
            export_pending_cons(kind, start, end)
        return

    if args.fill_synth:
        for kind in kinds:
            fill_missing_with_synth(kind, start, end)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
