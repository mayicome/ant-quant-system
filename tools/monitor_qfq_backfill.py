# -*- coding: utf-8 -*-
"""监控 qfq 一次性补齐进度，直到 _backfill_done.json 出现。"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROG = ROOT / "data" / "daily_cache_qfq" / "_backfill_progress.json"
DONE = ROOT / "data" / "daily_cache_qfq" / "_backfill_done.json"
LOG = ROOT / "data" / "daily_cache_qfq" / "_backfill_monitor.log"


def snap():
    if DONE.is_file():
        return "DONE", json.loads(DONE.read_text(encoding="utf-8"))
    if PROG.is_file():
        return "RUN", json.loads(PROG.read_text(encoding="utf-8"))
    return "WAIT", {}


def main() -> None:
    print("[monitor] 等待 QMT 策略启动…", flush=True)
    last_line = ""
    while True:
        st, data = snap()
        ts = datetime.now().strftime("%H:%M:%S")
        if st == "WAIT":
            line = f"{ts} 等待 _backfill_progress.json 出现"
        elif st == "RUN":
            n = len(data.get("codes") or [])
            line = (
                f"{ts} {data.get('phase')} {data.get('idx')}/{n} "
                f"ok_cache={data.get('ok_cache')} ok_full={data.get('ok_full')} "
                f"fail={len(data.get('fail') or {})}"
            )
        else:
            line = (
                f"{ts} 完成 ok_cache={data.get('ok_cache')} ok_full={data.get('ok_full')} "
                f"fail={len(data.get('fail') or {})}"
            )
        if line != last_line:
            print(line, flush=True)
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            last_line = line
        if st == "DONE":
            break
        time.sleep(30)
    print("[monitor] 监控结束", flush=True)


if __name__ == "__main__":
    main()
