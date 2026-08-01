"""复盘 300321 在 2026-07-03 突破 tick 的真突破判定（回测 vs 盘口）。"""
from __future__ import annotations

import sys
from datetime import date, datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "strategy_generator_app"))
from repo_path import ensure_paths

ensure_paths()

from strategy_generator_app.backtest.data_provider import load_tick_data_for_date  # noqa: E402
from strategy_generator_app.backtest.true_breakthrough import (  # noqa: E402
    evaluate_true_breakthrough_tick_with_detail,
    infer_tick_vol_to_shares_multiplier,
    is_breakthrough_buy_price_cross_tick,
    is_breakthrough_break_below_trigger_tick,
    max_cond1_breakthrough_volume_from_recent,
    per_tick_trade_volumes_list,
    round_price_like_display,
)


def main() -> None:
    code = "300321"
    trade_d = date(2026, 7, 3)
    # 汇总表买入 54522/900≈60.58；触发价需从策略 MA5 推断，先扫 09:30-10:00 上穿
    df = load_tick_data_for_date(code, trade_d)
    if df is None or df.empty:
        print("无 tick")
        return
    if "datetime" not in df.columns and "time" in df.columns:
        import pandas as pd

        df = df.copy()
        df["datetime"] = pd.to_datetime(df["time"], unit="ms")
    df = df.sort_values("datetime")
    vm = infer_tick_vol_to_shares_multiplier(df)
    rows = [r for _, r in df.iterrows()]
    vols = per_tick_trade_volumes_list(rows, vm)

    # 回测汇总：09:35:15 成交；用该时刻附近 lastPrice 反推 trigger≈60.58 前一刻上穿
    # 扫描所有上穿事件（trigger 取常见 MA5 附近：从 09:35 成交反推 trigger 在 60.4~60.7）
    candidates = []
    prefix_sum = 0.0
    prefix_cnt = 0
    prev_row = None
    prev_lp = None
    recent_rows: list = []
    recent_vols: list = []
    break_below = False
    run_start = time(9, 30)
    run_end = time(15, 0)

    for i, row in enumerate(rows):
        ts = row.get("datetime")
        if hasattr(ts, "time"):
            t = ts.time()
        else:
            continue
        if t < run_start or t > run_end:
            prev_lp = float(row.get("lastPrice") or 0)
            if prev_lp > 0:
                prefix_sum += float(vols[i] or 0)
                prefix_cnt += 1
            prev_row = dict(row)
            recent_rows = (recent_rows + [dict(row)])[-5:]
            recent_vols = (recent_vols + [vols[i]])[-10:]
            continue

        lp = float(row.get("lastPrice") or 0)
        row_d = dict(row)
        v_break = vols[i] if i < len(vols) else None

        # 试探多个 trigger（MA5 常见在 60.4-60.8）
        for trig in [60.45, 60.50, 60.55, 60.58, 60.60, 60.65]:
            crossed = is_breakthrough_buy_price_cross_tick(code, lp, trig, prev_lp)
            if crossed:
                if break_below or True:  # config break_below=1
                    bb = break_below
                avg_before = (prefix_sum / prefix_cnt) if prefix_cnt > 0 else None
                v_cond1 = max_cond1_breakthrough_volume_from_recent(recent_vols, v_break)
                ratio_window = (recent_rows + [row_d])[-5:]
                ok, msg, detail, metrics = evaluate_true_breakthrough_tick_with_detail(
                    code,
                    row_d,
                    prev_row,
                    vm,
                    avg_before,
                    v_break,
                    ratio_window,
                    v_break_cond1=v_cond1,
                    recent_vols=recent_vols,
                )
                candidates.append(
                    {
                        "time": str(ts)[11:19],
                        "trigger": trig,
                        "last": lp,
                        "passed": ok,
                        "detail": detail,
                        "r1": metrics.get("ratio_cond1"),
                        "r2": metrics.get("ask_bid_ratio_cond2"),
                        "r3": metrics.get("ratio_cond3"),
                        "break_below": bb,
                    }
                )
        if is_breakthrough_break_below_trigger_tick(code, lp, 60.58, prev_lp):
            break_below = True
        if lp > 0:
            prefix_sum += float(v_break or 0)
            prefix_cnt += 1
        prev_row = row_d
        prev_lp = lp
        recent_rows = (recent_rows + [row_d])[-5:]
        recent_vols = (recent_vols + [v_break])[-10:]

    # 09:35:10~09:35:20 附近 tick
    print(f"tick rows {len(rows)} vol_mul={vm}")
    near = df[(df["datetime"].dt.time >= time(9, 35, 0)) & (df["datetime"].dt.time <= time(9, 36, 0))]
    print(f"09:35-09:36 ticks: {len(near)}")
    for _, r in near.head(8).iterrows():
        print(" ", r["datetime"], float(r.get("lastPrice") or 0))

    print("\n上穿扫描(含 break_below 状态):")
    seen = set()
    for c in candidates:
        key = (c["time"], c["trigger"])
        if key in seen:
            continue
        seen.add(key)
        if c["time"].startswith("09:3"):
            print(
                f"  {c['time']} trig={c['trigger']} last={c['last']:.2f} "
                f"bb={c['break_below']} pass={c['passed']} "
                f"①{c['r1']} ②{c['r2']} ③{c['r3']}"
            )
            if c["detail"]:
                print("   ", c["detail"][:120])


if __name__ == "__main__":
    main()
