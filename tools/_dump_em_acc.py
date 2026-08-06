# -*- coding: utf-8 -*-
"""Dump window.__EM_ACC slice JSON from stdin into hist cache."""
from __future__ import annotations

import json
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.backfill_em_board_rank_from_hist import _klines_to_df, _save_hist_df  # noqa: E402


def main() -> None:
    payload = json.loads(sys.stdin.read())
    kind = str(payload.get("kind") or "industry")
    acc = payload.get("acc") or {}
    n = 0
    for code, klines in acc.items():
        code = str(code).strip().upper()
        if not code.startswith("BK"):
            continue
        df = _klines_to_df(klines or [])
        if df.empty:
            continue
        _save_hist_df(df, kind, code)
        n += 1
    print(f"saved={n} kind={kind}")


if __name__ == "__main__":
    main()
