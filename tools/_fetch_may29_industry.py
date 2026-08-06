# -*- coding: utf-8 -*-
"""Resilient May29 industry hist fetch for boards already in _hist_cache."""
from __future__ import annotations

import os
import random
import sys
import time
from datetime import date
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Do NOT call _clear_proxy_env — NO_PROXY=* breaks 7078.
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

from tools.backfill_em_board_rank_from_hist import (  # noqa: E402
    CACHE_DIR,
    OUT_DIR,
    _fetch_board_hist,
    _list_boards,
    _load_hist_cache,
    _save_hist_df,
    _ymd,
    backfill_kind,
)

TARGET = date(2026, 5, 29)
BEG = "20260520"
END = "20260605"


def needs_may29(kind: str, code: str) -> bool:
    cached = _load_hist_cache(kind, code)
    if cached is None or cached.empty:
        return True
    ds = set(cached["日期"].astype(str).str[:10])
    return "2026-05-29" not in ds


def fetch_one(code: str, *, retries: int = 6) -> bool:
    last = None
    for attempt in range(retries):
        try:
            df = _fetch_board_hist(code, BEG, END)
            if df is None or df.empty:
                last = RuntimeError("empty hist")
            else:
                _save_hist_df(df, "industry", code)
                ok = (df["日期"].astype(str).str[:10] == "2026-05-29").any()
                return bool(ok)
        except Exception as e:  # noqa: BLE001
            last = e
        sleep = 1.2 * (attempt + 1) + random.random() * 0.8
        time.sleep(sleep)
    print(f"[fail] {code}: {last}")
    return False


def main() -> None:
    boards = _list_boards("industry")
    # Prefer boards that already have parquet cache (~206)
    cached_codes = {
        p.name[len("industry_") : -len(".parquet")].upper()
        for p in Path(CACHE_DIR).glob("industry_*.parquet")
    }
    rows = []
    for _, row in boards.iterrows():
        code = str(row["板块代码"]).strip().upper()
        if cached_codes and code not in cached_codes:
            continue
        if needs_may29("industry", code):
            rows.append(code)
    print(f"[info] industry boards needing 2026-05-29: {len(rows)} / cache={len(cached_codes)}")
    ok = 0
    fail = 0
    t0 = time.time()
    for i, code in enumerate(rows, 1):
        if fetch_one(code):
            ok += 1
        else:
            fail += 1
        if i % 20 == 0 or i == len(rows):
            print(
                f"[progress] {i}/{len(rows)} ok={ok} fail={fail} "
                f"elapsed={time.time() - t0:.0f}s"
            )
        time.sleep(0.25 + random.random() * 0.2)

    print(f"[info] fetch done ok={ok} fail={fail}; building CSV via build-only path")
    # Build only May29 from cache (network off)
    n_days, dates = backfill_kind(
        "industry",
        TARGET,
        TARGET,
        buffer_days=5,
        pause_s=0.0,
        force_fetch=False,
        write_csv=True,
        allow_network=False,
    )
    path = os.path.join(OUT_DIR, f"industry_rank_{_ymd(TARGET)}.csv")
    exists = os.path.isfile(path)
    print(f"[done] csv_days={n_days} dates={dates} exists={exists} path={path}")
    if exists:
        import pandas as pd

        df = pd.read_csv(path)
        print(f"[done] rows={len(df)} top3={df['板块名称'].head(3).tolist()}")


if __name__ == "__main__":
    main()
