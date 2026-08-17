# -*- coding: utf-8 -*-
"""用新半仓逻辑重跑 002982 接续卖出，打印买卖流水。"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(r"d:\蚂蚁量化系统")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "strategy_generator_app"))

from strategy_generator_app.backtest.engine import run_backtest_segmented

BATCH = ROOT / "history_data" / "马总选股逻辑" / (
    "batch_backtest_export_买：马总逻辑1-涨停后跌破MA5_10_20各1_3_7-1弹性.json"
)
SELL_JSON = (
    ROOT / "strategy_generator_app" / "config" / "strategies" / "strategy_5a1fa73f.json"
)
STATE = ROOT / "data" / "ma_zong1_sell_filled_legs.json"
CODE = "002982"


def main() -> None:
    batch = json.loads(BATCH.read_text(encoding="utf-8"))
    seg0 = (batch.get("segments") or [None])[0]
    if not seg0:
        raise SystemExit("no segment")
    fills = [
        f
        for f in (seg0.get("buy_fills") or [])
        if str(f.get("code") or "").zfill(6)[-6:] == CODE
    ]
    if not fills:
        raise SystemExit("no buy fills")

    sell = json.loads(SELL_JSON.read_text(encoding="utf-8"))
    sp = dict(sell.get("strategy_params") or {})
    sp["_filled_legs"] = []
    sp["min_order_amount"] = 5000.0
    # 与原 7-1 弹性卖接续一致：持有约 10 交易日兜底
    sp["sell_hold_trading_days"] = 10
    sp["scheduled_clear_on_sell_day"] = 10
    sp["entry_window_trading_days"] = 4

    start_d = date(2026, 7, 3)
    end_d = date(2026, 7, 27)

    # 注入：窗前买入作初始可卖；窗内按日注入
    pre_vol = 0
    pre_cost = 0.0
    scheduled = []
    for f in fills:
        bd = date.fromisoformat(str(f["date"])[:10])
        vol = int(f["volume"])
        px = float(f["price"])
        if bd < start_d:
            pre_vol += vol
            pre_cost += vol * px
        else:
            scheduled.append(
                {
                    "code": CODE,
                    "date": bd.isoformat(),
                    "volume": vol,
                    "price": px,
                    "side": "buy",
                }
            )
    init_pos = {}
    if pre_vol > 0:
        init_pos[CODE] = {
            "volume": pre_vol,
            "cost": round(pre_cost / pre_vol, 4),
            "available": pre_vol,
            "entry_date": "2026-07-02",
        }

    # 避免污染/被污染实盘腿状态
    bak = None
    if STATE.exists():
        bak = STATE.with_suffix(".json.bak_002982_rerun")
        shutil.copy2(STATE, bak)
        STATE.write_text(
            json.dumps({"legs": [], "updated_at": "rerun"}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    try:
        result = run_backtest_segmented(
            segments=[
                {
                    "name": sell.get("name") or "sell",
                    "strategy_code": sell.get("strategy_code") or "",
                    "strategy_params": sp,
                    "strategy_generation_time": "09:25",
                    "strategy_run_start_time": "09:30",
                    "strategy_run_end_time": "14:57",
                }
            ],
            stock_codes_6=[CODE],
            start_date=start_d,
            end_date=end_d,
            initial_cash=1_000_000.0,
            get_stock_name=lambda c: "湘佳股份",
            use_tick_level=True,
            initial_positions=init_pos or None,
            scheduled_buy_fills=scheduled,
            first_buy_date_hints={CODE: date(2026, 7, 2)},
        )
    finally:
        if bak and bak.exists():
            shutil.copy2(bak, STATE)
            bak.unlink(missing_ok=True)

    trades = result.get("trades") or []
    # 合成买入行（注入不成交记录在 trades 里可能没有 buy）
    rows = []
    for f in fills:
        rows.append(
            {
                "date": str(f["date"])[:10],
                "time": "",
                "side": "买入",
                "price": float(f["price"]),
                "volume": int(f["volume"]),
                "amount": float(f.get("amount") or float(f["price"]) * int(f["volume"])),
                "rule": "买入腿(注入)",
                "leg": "",
                "pos_after": "",
            }
        )
    for t in trades:
        if str(t.get("code") or "").zfill(6)[-6:] != CODE:
            continue
        side = str(t.get("side") or "").lower()
        rows.append(
            {
                "date": str(t.get("date") or "")[:10],
                "time": str(t.get("time") or ""),
                "side": "买入" if side == "buy" else "卖出",
                "price": float(t.get("price") or 0),
                "volume": int(t.get("volume") or 0),
                "amount": float(t.get("amount") or 0),
                "rule": str(t.get("rule_name") or t.get("name") or t.get("rule_type") or ""),
                "leg": str(t.get("leg_key") or ""),
                "pos_after": t.get("position_after", ""),
            }
        )
    rows.sort(key=lambda r: (r["date"], r["time"] or "00:00:00", 0 if r["side"] == "买入" else 1))

    print(f"002982 湘佳股份 · 新半仓逻辑重跑  {start_d} → {end_d}")
    print(f"卖出成交笔数: {sum(1 for r in rows if r['side']=='卖出')}")
    print()
    print(
        f"{'日期':<12}{'时间':<10}{'方向':<4}{'价格':>8}{'数量':>8}{'金额':>10}  {'交易后':>6}  规则"
    )
    print("-" * 100)
    buy_v = sell_v = 0
    for r in rows:
        if r["side"] == "买入":
            buy_v += r["volume"]
        else:
            sell_v += r["volume"]
        pa = r["pos_after"]
        pa_s = "" if pa == "" or pa is None else str(int(pa))
        print(
            f"{r['date']:<12}{(r['time'] or '-'):<10}{r['side']:<4}"
            f"{r['price']:>8.2f}{r['volume']:>8}{r['amount']:>10.1f}  {pa_s:>6}  "
            f"{r['rule']} {r['leg']}"
        )
    print("-" * 100)
    print(f"买入合计 {buy_v}  卖出合计 {sell_v}  差额 {buy_v - sell_v}")
    pos = (result.get("positions") or {}).get(CODE) or {}
    print(f"期末持仓 volume={pos.get('volume')} available={pos.get('available')}")


if __name__ == "__main__":
    main()
