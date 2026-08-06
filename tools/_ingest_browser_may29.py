# -*- coding: utf-8 -*-
"""Ingest browser-fetched hist batch JSON into _hist_cache and build May29 CSV."""
from __future__ import annotations

import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.backfill_em_board_rank_from_hist import (  # noqa: E402
    OUT_DIR,
    backfill_kind,
    ingest_json_batch,
)


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "data", "eastmoney_board_rank", "_hist_cache", "_browser_may29_batch.json"
    )
    n = ingest_json_batch(path, "industry")
    print(f"[info] ingested {n}")
    n_days, dates = backfill_kind(
        "industry",
        date(2026, 5, 29),
        date(2026, 5, 29),
        buffer_days=0,
        pause_s=0.0,
        force_fetch=False,
        write_csv=True,
        allow_network=False,
    )
    csv_path = os.path.join(OUT_DIR, "industry_rank_2026-05-29.csv")
    print(f"[done] days={n_days} dates={dates} exists={os.path.isfile(csv_path)}")
    if os.path.isfile(csv_path):
        import pandas as pd

        df = pd.read_csv(csv_path)
        print(f"[done] rows={len(df)} top5={df['板块名称'].head(5).tolist()}")


if __name__ == "__main__":
    main()
