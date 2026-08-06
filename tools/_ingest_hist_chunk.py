# -*- coding: utf-8 -*-
"""Save a browser CDP hist dump chunk into _hist_cache."""
from __future__ import annotations

import json
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.backfill_em_board_rank_from_hist import ingest_json_batch  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: _ingest_hist_chunk.py <chunk.json>")
    path = sys.argv[1]
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    kind = str(payload.get("kind") or "")
    if not kind:
        raise SystemExit("chunk missing kind")
    n = ingest_json_batch(path, kind)
    print(f"INGESTED={n}")


if __name__ == "__main__":
    main()
