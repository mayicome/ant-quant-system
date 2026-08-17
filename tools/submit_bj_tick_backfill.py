# -*- coding: utf-8 -*-
"""一次性提交北交所（.BJ）近约 1 个月分时回补队列。

写入 data/tick_full_sync/manual_request.json，由大 QMT「蚂蚁量化规则」
periodic_sync → process_manual_request 用 ContextInfo 执行。

用法：
  python tools/submit_bj_tick_backfill.py --dry-run
  python tools/submit_bj_tick_backfill.py --submit-only --no-require-strategy
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from typing import List

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.dirname(os.path.abspath(__file__))
for p in (_ROOT, _TOOLS):
    if p not in sys.path:
        sys.path.insert(0, p)

from backfill_tick_history import (  # noqa: E402
    _import_runner,
    _trading_days,
    _ymd,
    submit_strategy_backfill,
)


def _build_days(lookback: int, include_today: bool) -> List[str]:
    runner = _import_runner()
    xtdata = None
    try:
        xtdata = runner._load_xtdata()
    except Exception:
        xtdata = None
    today = date.today()
    end = today if include_today else (today - timedelta(days=1))
    start = today - timedelta(days=max(1, int(lookback)))
    days = _trading_days(xtdata, runner, start, end)
    return [_ymd(d) for d in days]


def main() -> int:
    ap = argparse.ArgumentParser(description="提交北交所 tick 近月回补队列")
    ap.add_argument(
        "--lookback",
        type=int,
        default=0,
        help="自然日回看天数（默认用 runner.TICK_RETENTION_CALENDAR_DAYS）",
    )
    ap.add_argument(
        "--include-today",
        action="store_true",
        help="包含今天（默认到昨天）",
    )
    ap.add_argument("--force", action="store_true", help="已有文件也重拉")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写请求")
    ap.add_argument(
        "--submit-only",
        action="store_true",
        help="写入 manual_request 后退出（默认行为）",
    )
    ap.add_argument(
        "--no-require-strategy",
        action="store_true",
        help="不检查策略心跳（定时任务用）",
    )
    ap.add_argument(
        "--merge",
        action="store_true",
        help="与现有 manual_request 日期合并（默认覆盖同后缀任务）",
    )
    args = ap.parse_args()

    runner = _import_runner()
    lookback = int(args.lookback or 0)
    if lookback <= 0:
        lookback = int(getattr(runner, "TICK_RETENTION_CALENDAR_DAYS", 28) or 28)

    days = _build_days(lookback, bool(args.include_today))
    print(
        "[bj_tick] lookback=%d days=%d range=%s..%s"
        % (
            lookback,
            len(days),
            days[0] if days else "-",
            days[-1] if days else "-",
        )
    )
    if not days:
        print("[bj_tick] 无交易日可提交")
        return 1

    if args.dry_run:
        print("[bj_tick] dry-run days=%s" % ",".join(days))
        return 0

    if not args.no_require_strategy:
        from backfill_tick_history import _strategy_alive

        alive, detail = _strategy_alive()
        print("[bj_tick] strategy alive=%s (%s)" % (alive, detail))
        if not alive:
            print("[bj_tick] 策略未在跑；加 --no-require-strategy 可强制写队列")
            return 6

    payload = submit_strategy_backfill(
        days,
        force=bool(args.force),
        limit=0,
        merge=bool(args.merge),
        suffix=".BJ",
        source="submit_bj_tick_backfill",
    )
    meta_path = os.path.join(
        _ROOT, "data", "tick_full_sync", "bj_backfill_submitted.json"
    )
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "submitted_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                    "lookback": lookback,
                    "days": payload.get("days") or days,
                    "suffix": payload.get("suffix"),
                    "force": payload.get("force"),
                    "source": payload.get("source"),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception as e:
        print("[bj_tick] meta write failed: %s" % e)

    print(
        "[bj_tick] submitted suffix=.BJ n_days=%d → data/tick_full_sync/manual_request.json"
        % len(payload.get("days") or [])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
