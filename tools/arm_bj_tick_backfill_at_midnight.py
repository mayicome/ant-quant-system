# -*- coding: utf-8 -*-
"""等到指定时刻后提交北交所 tick 回补队列（一次性）。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _next_midnight(now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    target = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=5, microsecond=0
    )
    if target <= now:
        target = now + timedelta(seconds=5)
    return target


def main() -> int:
    os.chdir(_ROOT)
    target = _next_midnight()
    sec = max(1, int((target - datetime.now()).total_seconds()))
    print(
        "BJ_TICK_ARM sleep_sec=%d until=%s"
        % (sec, target.isoformat(timespec="seconds")),
        flush=True,
    )
    time.sleep(sec)
    print(
        "BJ_TICK_FIRE at=%s" % datetime.now().isoformat(timespec="seconds"),
        flush=True,
    )
    rc = subprocess.call(
        [
            sys.executable,
            "tools/submit_bj_tick_backfill.py",
            "--submit-only",
            "--no-require-strategy",
        ],
        cwd=_ROOT,
    )
    print("BJ_TICK_SUBMIT_RC %d" % rc, flush=True)
    meta = {
        "fired_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "submit_rc": rc,
    }
    try:
        path = os.path.join(
            _ROOT, "data", "tick_full_sync", "bj_backfill_fired.json"
        )
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("meta err %s" % e, flush=True)
    # Cursor loop sentinel（供 agent 醒来核对）
    print(
        'AGENT_LOOP_WAKE_bj_tick {"prompt":"Confirm BJ tick midnight submit"}',
        flush=True,
    )
    return int(rc)


if __name__ == "__main__":
    raise SystemExit(main())
