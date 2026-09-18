# -*- coding: utf-8 -*-
"""CLI：在本地 tick 日期范围内回测 MVP。

示例：
  python -m event_tick_mvp.run_mvp
  python -m event_tick_mvp.run_mvp --max-buy-replay 0
  python -m event_tick_mvp.run_mvp --max-days 20
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime


def _parse_day(s: str) -> date:
    return datetime.strptime(s.strip()[:10], "%Y-%m-%d").date()


def main(argv=None) -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)

    from event_tick_mvp.config import MvpConfig, apply_addon_mode, default_scan_grids
    from event_tick_mvp.data import list_tick_days
    from event_tick_mvp.engine import run_on_tick_days

    p = argparse.ArgumentParser(description="短线事件-tick MVP-v1 回测（日期=本地 ticks 目录）")
    p.add_argument("--start", type=str, default="", help="YYYY-MM-DD，默认 tick 最早日")
    p.add_argument("--end", type=str, default="", help="YYYY-MM-DD，默认 tick 最晚日")
    p.add_argument("--max-days", type=int, default=0, help="最多回测多少个 tick 日（0=全部）")
    p.add_argument(
        "--min-tick-files",
        type=int,
        default=1000,
        help="当日 tick 文件数下限（默认 1000，跳过早期稀疏日）",
    )
    p.add_argument(
        "--max-buy-replay",
        type=int,
        default=0,
        help="buy_monitor 回放上限；0=全候选（基线复测默认）",
    )
    p.add_argument(
        "--no-board-posthoc",
        action="store_true",
        help="关闭 T-1 板块名次事后标签",
    )
    p.add_argument("--addon", type=str, default="none")
    p.add_argument("--show-grids", action="store_true")
    p.add_argument("--list-tick-days", action="store_true")
    p.add_argument(
        "--out",
        type=str,
        default="",
        help="结果 JSON 输出路径（默认 history_data/event_tick_mvp/last_run.json）",
    )
    args = p.parse_args(argv)

    if args.show_grids:
        print(json.dumps(default_scan_grids(), ensure_ascii=False, indent=2))
        return 0

    min_files = int(args.min_tick_files)
    days = list_tick_days(min_files=min_files)
    if args.list_tick_days:
        all_days = list_tick_days(min_files=0)
        print(f"all tick dirs: {len(all_days)}")
        if all_days:
            print(f"all range: {all_days[0]} .. {all_days[-1]}")
        print(f"usable (min_files>={min_files}): {len(days)}")
        if days:
            print(f"usable range: {days[0]} .. {days[-1]}")
            for d in days:
                print(d.isoformat())
        return 0

    if not days:
        print(
            f"未找到满足 min_tick_files>={min_files} 的 data/ticks 日期，无法回测。"
            " 可用 --min-tick-files 0 查看全部目录。"
        )
        return 1

    start = _parse_day(args.start) if args.start else days[0]
    end = _parse_day(args.end) if args.end else days[-1]
    max_days = int(args.max_days) if args.max_days else None

    cfg = MvpConfig(bar_freq="1m")
    cfg.max_buy_replay = int(args.max_buy_replay)
    cfg.tag_board_rank_posthoc = not bool(args.no_board_posthoc)
    apply_addon_mode(cfg, args.addon)

    print(
        f"[event_tick_mvp] backtest tick-range clamp "
        f"{start}..{end} max_days={max_days or 'all'} "
        f"min_tick_files={min_files} max_buy_replay={cfg.max_buy_replay} "
        f"board_posthoc={cfg.tag_board_rank_posthoc} addon={args.addon}",
        flush=True,
    )
    res = run_on_tick_days(
        cfg=cfg,
        start=start,
        end=end,
        max_days=max_days,
        min_tick_files=min_files,
        progress=True,
    )
    m = res.metrics
    print("---- notes ----")
    for n in res.notes:
        print(" ", n)
    if m:
        print("---- metrics ----")
        print(f"  trades(sells)={m.n_trades}")
        print(f"  win_rate={m.win_rate}")
        print(f"  payoff_ratio={m.payoff_ratio}")
        print(f"  ann_return={m.ann_return} max_dd={m.max_dd} sharpe={m.sharpe}")
        print(
            f"  exit tp/sl/hold={m.exit_take_profit_pct}/{m.exit_stop_loss_pct}/{m.exit_max_hold_pct}"
        )
        print(f"  mvp_baseline_trades>300? {m.passes_mvp_baseline()}")
    if res.board_posthoc:
        print("---- board posthoc (观察，未改规则) ----")
        bp = res.board_posthoc
        print(f"  pairs={bp.get('n_pairs')} tagged={bp.get('n_tagged')}")
        for title, key in (
            ("industry_rank_t1", "by_industry_rank_t1"),
            ("concept_rank_t1", "by_concept_rank_t1"),
        ):
            grp = bp.get(key) or {}
            if not grp:
                continue
            print(f"  [{title}]")
            for bucket, st in grp.items():
                print(
                    f"    {bucket}: n={st.get('n')} win={st.get('win_rate')} "
                    f"E={st.get('expectancy')} sl%={st.get('stop_loss_pct')}"
                )

    out = args.out.strip()
    if not out:
        out_dir = os.path.join(root, "history_data", "event_tick_mvp")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, "last_run.json")
    else:
        out_dir = os.path.dirname(os.path.abspath(out))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

    def _fill_row(f):
        row = {
            "side": f.side,
            "code": f.code,
            "price": f.price,
            "volume": f.volume,
            "reason": f.reason,
            "ts": f.ts.isoformat(),
            "meta": f.meta or {},
        }
        return row

    payload = {
        "notes": res.notes,
        "tick_days": [d.isoformat() for d in (res.tick_days or [])],
        "n_fills": len(res.fills),
        "daily_nav": res.daily_nav,
        "watch_sizes": res.watch_sizes,
        "metrics": None
        if not m
        else {
            "n_trades": m.n_trades,
            "win_rate": m.win_rate,
            "payoff_ratio": None
            if m.payoff_ratio is None or m.payoff_ratio == float("inf")
            else m.payoff_ratio,
            "ann_return": m.ann_return,
            "max_dd": m.max_dd,
            "sharpe": m.sharpe,
            "calmar": m.calmar,
            "exit_take_profit_pct": m.exit_take_profit_pct,
            "exit_stop_loss_pct": m.exit_stop_loss_pct,
            "exit_max_hold_pct": m.exit_max_hold_pct,
        },
        "board_posthoc": res.board_posthoc,
        "fills": [_fill_row(f) for f in res.fills],
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[event_tick_mvp] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
