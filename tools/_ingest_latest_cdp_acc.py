# -*- coding: utf-8 -*-
"""Parse latest CDP Runtime.evaluate dump file and save __EM_ACC into hist cache."""
from __future__ import annotations

import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.backfill_em_board_rank_from_hist import _klines_to_df, _save_hist_df  # noqa: E402

LOG_DIR = r"C:\Users\Administrator\.cursor\browser-logs"


def latest_cdp_file() -> str:
    files = glob.glob(os.path.join(LOG_DIR, "cdp-response-Runtime.evaluate-*.json"))
    if not files:
        raise SystemExit("no cdp dump files")
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]


def extract_payload(obj):
    val = obj
    # common shapes
    if isinstance(val, dict) and "result" in val:
        val = val["result"]
    if isinstance(val, dict) and "value" in val:
        val = val["value"]
    if isinstance(val, str):
        return json.loads(val)
    return val


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else latest_cdp_file()
    obj = json.load(open(path, encoding="utf-8"))
    payload = extract_payload(obj)
    kind = str(payload.get("kind") or "industry")
    acc = payload.get("acc") or {}
    n = 0
    for code, klines in acc.items():
        code = str(code).strip().upper()
        df = _klines_to_df(klines or [])
        if df.empty:
            continue
        _save_hist_df(df, kind, code)
        n += 1
    print(f"file={os.path.basename(path)} kind={kind} saved={n} acc={len(acc)}")


if __name__ == "__main__":
    main()
