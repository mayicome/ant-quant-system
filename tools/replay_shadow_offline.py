# -*- coding: utf-8 -*-
"""
周末 / QMT 维护期离线回放 shadow 真突破逻辑。

用法（项目根目录）:
  python tools/replay_shadow_offline.py --code 300321.SZ --date 2026-07-03
  python tools/replay_shadow_offline.py --rules data/rules_armed.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "qmt_builtin"))
sys.path.insert(0, str(ROOT / "strategy_generator_app"))

from repo_path import ensure_paths

ensure_paths()

from qmt_builtin.ant_rules_io import default_paths, load_rules_armed, save_json_atomic  # noqa: E402
from qmt_builtin.ant_tick_runner import ShadowTickRunner  # noqa: E402
from strategy_generator_app.backtest.data_provider import load_tick_data_for_date  # noqa: E402


def _parse_date(s: str) -> date:
    s = s.strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    return datetime.strptime(s, "%Y-%m-%d").date()


def replay_one(code: str, trade_d: date, runner: ShadowTickRunner) -> list:
    df = load_tick_data_for_date(code.split(".")[0], trade_d)
    if df is None or df.empty:
        print(f"[offline] no tick data for {code} {trade_d}")
        return []
    if "datetime" not in df.columns and "time" in df.columns:
        import pandas as pd

        df = df.copy()
        df["datetime"] = pd.to_datetime(df["time"], unit="ms")
    df = df.sort_values("datetime")

    all_events = []
    run_start = time(9, 25)
    run_end = time(15, 0)
    for _, row in df.iterrows():
        ts = row.get("datetime")
        if hasattr(ts, "time"):
            t = ts.time()
            if t < run_start or t > run_end:
                continue
        row_d = dict(row)
        if hasattr(ts, "timestamp"):
            row_d["time"] = int(ts.timestamp() * 1000)
        evs = runner.on_row(code.upper(), row_d)
        for ev in evs:
            print(
                f"[offline] {ev.get('tick_time')} {ev.get('type')} "
                f"trig={ev.get('trigger_price')} {ev.get('msg')}"
            )
            if ev.get("detail"):
                print(f"          {ev.get('detail')}")
        all_events.extend(evs)
    return all_events


def main() -> None:
    parser = argparse.ArgumentParser(description="离线回放 QMT shadow 真突破")
    parser.add_argument("--rules", default="", help="rules_armed.json 路径")
    parser.add_argument("--code", default="", help="单票回放，如 300321.SZ")
    parser.add_argument("--date", default="", help="交易日 YYYY-MM-DD")
    parser.add_argument("--write-results", action="store_true", help="写入 data/results.json")
    args = parser.parse_args()

    rules_path, results_path = default_paths(str(ROOT))
    if args.rules:
        rules_path = args.rules
    rules = load_rules_armed(rules_path)
    if args.date:
        rules["trade_date"] = args.date.replace("-", "")

    runner = ShadowTickRunner(rules, mode="shadow_offline")
    tasks = rules.get("tasks") or []
    if not tasks:
        print(f"[offline] no tasks in {rules_path}")
        return

    if args.code and args.date:
        trade_d = _parse_date(args.date)
        code = args.code.upper()
        replay_one(code, trade_d, runner)
        return

    # 按 rules 中每任务的 metadata.replay_date 或 rules.trade_date 回放
    for task in tasks:
        code = str(task.get("stock_code") or "").upper()
        meta = task.get("metadata") or {}
        d_raw = str(meta.get("replay_date") or rules.get("trade_date") or "")
        if not code or not d_raw:
            print(f"[offline] skip task {task.get('task_id')} — missing code/date")
            continue
        if len(d_raw) == 8 and d_raw.isdigit():
            trade_d = _parse_date(f"{d_raw[:4]}-{d_raw[4:6]}-{d_raw[6:8]}")
        else:
            trade_d = _parse_date(d_raw)
        print(f"[offline] replay {code} {trade_d}")
        replay_one(code, trade_d, runner)

    if args.write_results:
        out = runner.snapshot_results()
        save_json_atomic(results_path, out)
        print(f"[offline] wrote {results_path}")


if __name__ == "__main__":
    main()
