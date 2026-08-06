# -*- coding: utf-8 -*-
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools._synth_em_board_hist_from_cons import ingest_cons_batch  # noqa: E402

LOG_DIR = r"C:\Users\Administrator\.cursor\browser-logs"


def latest() -> str:
    files = glob.glob(os.path.join(LOG_DIR, "cdp-response-Runtime.evaluate-*.json"))
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]


def extract(obj):
    val = obj
    if isinstance(val, dict) and "result" in val:
        val = val["result"]
    if isinstance(val, dict) and "value" in val:
        val = val["value"]
    if isinstance(val, str):
        return json.loads(val)
    return val


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else latest()
    payload = extract(json.load(open(path, encoding="utf-8")))
    kind = str(payload.get("kind") or "industry")
    acc = payload.get("acc") or {}
    items = [{"code": c, "members": m} for c, m in acc.items()]
    out = {
        "kind": kind,
        "items": items,
    }
    tmp = os.path.join(
        ROOT,
        "data",
        "eastmoney_board_rank",
        "_hist_cache",
        f"_cons_chunk_{kind}.json",
    )
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    n = ingest_cons_batch(tmp, kind)
    print(f"ingested_cons={n} from {os.path.basename(path)}")


if __name__ == "__main__":
    main()
