# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils.eastmoney_board_rank_ctx import load_em_board_hot_map

BASE = os.path.join(ROOT, "data", "eastmoney_board_rank")
DAYS = [
    "2026-05-29",
    "2026-06-01",
    "2026-06-02",
    "2026-06-03",
    "2026-06-04",
    "2026-06-05",
    "2026-06-08",
    "2026-06-09",
    "2026-06-10",
]


def main() -> None:
    for ds in DAYS:
        ind = os.path.isfile(os.path.join(BASE, f"industry_rank_{ds}.csv"))
        ctx = load_em_board_hot_map(ds, arms="today")
        err = str(ctx.get("error") or "")
        pool = len(ctx.get("today_pool_codes") or [])
        prev = ctx.get("prev_date")
        print(
            f"{ds} ind_csv={ind} prev={prev} today_pool={pool} "
            f"err={err[:100]!r}"
        )


if __name__ == "__main__":
    main()
