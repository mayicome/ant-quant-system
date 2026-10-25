# -*- coding: utf-8 -*-
"""马总盘中 · 可配置多时钟落硬门槛候选。

落盘范围：所有满足硬门槛（当时涨幅）的股票；每行带五项软门槛的取值 / 是否满足 / 不满足原因。

用法：
  python tools/dump_ma_zong_intraday_candidates.py
  python tools/dump_ma_zong_intraday_candidates.py --clock 14:40
  python tools/dump_ma_zong_intraday_candidates.py --scheduled --clock 14:40  # 启动器按槽位调用
  python tools/dump_ma_zong_intraday_candidates.py --allow-file  # 盘外手动：允许降为上一交易日文件

配置（data/config.ini）：
  [MaZongIntradayDump]
  enabled = 1
  dump_times = 11:00,14:00,14:30,14:40,14:50,14:55
  grace_minutes = 15
  output_dir = data/ma_zong_intraday_candidates
  min_inflow_wan = 3500
  # 兼容旧键 dump_time = 14:40（单时刻）

输出：
  data/ma_zong_intraday_candidates/YYYY-MM-DD_HHMM.csv
  同名 .jsonl / _meta.json（同一天多槽位各写一份）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_ipv4() -> None:
    try:
        from utils.http_ipv4 import install_prefer_ipv4

        install_prefer_ipv4()
    except Exception as e:
        print("[dump] install_prefer_ipv4 skip:", e)


def _parse_date(s: Optional[str]) -> date:
    if not s:
        return date.today()
    s = str(s).strip()
    if len(s) == 8 and s.isdigit():
        return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
    return date.fromisoformat(s[:10])


def _clock_label(s: str) -> str:
    t = str(s or "14:40").strip().replace(":", "")
    if len(t) >= 4:
        return t[:4]
    return t.zfill(4)


def _is_trading_day(d: date) -> bool:
    try:
        from utils.trading_day import is_tradeday

        return bool(is_tradeday(d))
    except Exception:
        return d.weekday() < 5


def main(argv: Optional[list] = None) -> int:
    _install_ipv4()
    from utils.ma_zong_candidate_dump import (
        flatten_scan_result,
        load_dump_config,
        write_candidate_files,
    )
    from utils.ma_zong_intraday_ctx import is_a_share_intraday_session
    from utils.ma_zong_logic2_scan import scan_logic2_meet_candidates

    cfg = load_dump_config(ROOT)
    ap = argparse.ArgumentParser(description="马总盘中硬门槛候选落盘")
    ap.add_argument(
        "--clock",
        default="",
        help="落盘时钟 HH:MM（默认读 config 首个 dump_times=%s；定时须显式传入）" % cfg["dump_time"],
    )
    ap.add_argument("--date", default="", help="选股日 YYYY-MM-DD；默认今天")
    ap.add_argument(
        "--scheduled",
        action="store_true",
        help="启动器定时模式：非交易日跳过；盘外不写文件；已落过同日同槽位则跳过；须配合 --clock",
    )
    ap.add_argument(
        "--allow-file",
        action="store_true",
        help="盘外允许降为上一交易日本地文件（手动验证用；定时模式默认不允许）",
    )
    ap.add_argument(
        "--force-live",
        action="store_true",
        help="请求 live（盘外仍会被 ctx 降级；定时模式盘外直接跳过）",
    )
    ap.add_argument(
        "--min-inflow-wan",
        type=float,
        default=None,
        help="净流入预筛门槛（万），默认 config/3500",
    )
    ap.add_argument(
        "--out-dir",
        default="",
        help="输出目录，默认 config output_dir",
    )
    args = ap.parse_args(argv)

    if args.scheduled and not str(args.clock or "").strip():
        print("[dump] abort scheduled: --clock required for multi dump_times")
        return 2
    clock = (args.clock or cfg["dump_time"] or (cfg.get("dump_times") or ["14:40"])[0]).strip()
    clock_tag = _clock_label(clock)
    as_of = _parse_date(args.date or None)
    out_dir = Path(args.out_dir or cfg["output_dir"])
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    min_inflow = (
        float(args.min_inflow_wan)
        if args.min_inflow_wan is not None
        else float(cfg["min_inflow_wan"])
    )

    now = datetime.now()
    in_session = is_a_share_intraday_session(now)
    is_td = _is_trading_day(now.date())

    print(
        "[dump] now=%s clock=%s as_of_req=%s trading_day=%s in_session=%s scheduled=%s"
        % (
            now.strftime("%Y-%m-%d %H:%M:%S"),
            clock,
            as_of.isoformat(),
            is_td,
            in_session,
            bool(args.scheduled),
        )
    )

    if args.scheduled and not bool(cfg.get("enabled", True)):
        print("[dump] skip: config MaZongIntradayDump.enabled=0")
        return 0

    if args.scheduled and not is_td:
        print("[dump] skip: not a trading day")
        return 0

    # 定时：盘外不落盘（避免把日终文件当成 14:40）
    if args.scheduled and not in_session:
        print("[dump] skip: scheduled dump requires intraday session (no CSV fallback)")
        return 0

    # 定时：同日同时钟已有文件则跳过
    if args.scheduled:
        stem = "%s_%s" % (now.date().isoformat(), clock_tag)
        exist = out_dir / ("%s.csv" % stem)
        if exist.is_file() and exist.stat().st_size > 0:
            print("[dump] skip: already exists", exist)
            return 0

    allow_file = bool(args.allow_file) or (not args.scheduled and not in_session)
    if args.scheduled:
        allow_file = False

    # force_live:
    #   scheduled / in_session → None (ctx 自动 live)
    #   manual + --force-live → True（盘外会被 demote）
    #   manual off-session without allow_file → still call scan; ctx demotes
    force_live: Optional[bool]
    if args.force_live:
        force_live = True
    elif in_session and as_of == date.today():
        force_live = None  # live
    elif allow_file:
        force_live = False  # explicit file / demote path
    else:
        force_live = True  # request live; if demoted and empty, we error clearly

    if not in_session and not allow_file and not args.scheduled:
        print(
            "[dump] warn: outside session and --allow-file not set; "
            "live may demote/fail. For tonight smoke test use --allow-file."
        )

    print("[dump] scanning … force_live=%s min_inflow_wan=%s" % (force_live, min_inflow))
    scan = scan_logic2_meet_candidates(
        as_of,
        force_live=force_live,
        min_inflow_wan=min_inflow,
        skip_em_hot=True,
        progress=None,
    )

    mode = str(scan.get("mode") or "")
    err = str(scan.get("error") or "").strip()
    stats = scan.get("stats") or {}
    effective_asof = str(scan.get("as_of") or as_of.isoformat())

    print(
        "[dump] scan done mode=%s as_of=%s err=%s stats=%s"
        % (mode, effective_asof, err or "-", json.dumps(stats, ensure_ascii=False))
    )

    if args.scheduled and mode != "live":
        print("[dump] abort scheduled: expected live mode, got", mode)
        return 2

    if not (scan.get("hard_pass_all") or scan.get("candidates")):
        if err:
            print("[dump] no hard-pass rows:", err)
            return 3 if args.scheduled else 1
        print("[dump] no hard-pass rows")
        # still write empty file for the day marker? For scheduled yes (avoid re-fire loops on empty markets)
        # For manual, write empty with meta too so path is clear.

    rows = flatten_scan_result(scan, dump_clock=clock)
    soft_pass = sum(1 for r in rows if r.get("满足全部软门槛") == "是")
    hard_pass = len(rows)

    meta: Dict[str, Any] = {
        "requested_as_of": as_of.isoformat(),
        "effective_as_of": effective_asof,
        "mode": mode,
        "fetched_at": scan.get("fetched_at"),
        "in_session": in_session,
        "scheduled": bool(args.scheduled),
        "allow_file": allow_file,
        "min_inflow_wan": min_inflow,
        "error": err,
        "stats": stats,
        "hard_pass": hard_pass,
        "soft_pass": soft_pass,
        "config_dump_time": cfg["dump_time"],
        "config_dump_times": list(cfg.get("dump_times") or []),
    }
    paths = write_candidate_files(
        rows,
        out_dir=out_dir,
        as_of=effective_asof,
        dump_clock=clock,
        meta=meta,
    )
    print(
        "[dump] wrote hard_pass=%d soft_pass=%d\n  csv=%s\n  jsonl=%s\n  meta=%s"
        % (hard_pass, soft_pass, paths["csv"], paths["jsonl"], paths["meta"])
    )
    if err and hard_pass == 0:
        return 3 if args.scheduled else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
