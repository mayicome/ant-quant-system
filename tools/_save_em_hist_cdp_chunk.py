# -*- coding: utf-8 -*-
"""从 stdin 读入 CDP __emRun 返回的 JSON，写入 _hist_cache。"""
from __future__ import annotations

import json
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.backfill_em_board_rank_from_hist import ingest_json_batch  # noqa: E402

CACHE = os.path.join(ROOT, "data", "eastmoney_board_rank", "_hist_cache")


def main() -> None:
    raw = sys.stdin.read()
    payload = json.loads(raw)
    kind = str(payload.get("kind") or "industry")
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"_chunk_{kind}_{payload.get('idx')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    n = ingest_json_batch(path, kind)
    print(
        f"idx={payload.get('idx')}/{payload.get('total')} "
        f"ok={payload.get('ok')} fail={payload.get('fail')} "
        f"ingested={n} done={payload.get('done')}"
    )


if __name__ == "__main__":
    main()
