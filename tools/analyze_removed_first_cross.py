"""分析修正后消失的9笔：首笔上穿时刻与真突破判定。"""
from __future__ import annotations

import sys
from datetime import date, time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "strategy_generator_app"))
from repo_path import ensure_paths

ensure_paths()

from strategy_generator_app.backtest.data_provider import load_tick_data_for_date  # noqa: E402
from strategy_generator_app.backtest.true_breakthrough import (  # noqa: E402
    evaluate_true_breakthrough_tick_with_detail,
    infer_tick_vol_to_shares_multiplier,
    is_breakthrough_buy_price_cross_tick,
    max_cond1_breakthrough_volume_from_recent,
    per_tick_trade_volumes_list,
)

OLD_XLS = ROOT / "history_data" / "回测七月" / "各日选股收益汇总_7月_全部涨停后1-2日.xlsx"


def _first_cross_tb(
    code: str,
    trade_d: date,
    trigger: float,
) -> Optional[Dict[str, Any]]:
    df = load_tick_data_for_date(code, trade_d)
    if df is None or df.empty:
        return None
    if "datetime" not in df.columns:
        df = df.copy()
        df["datetime"] = pd.to_datetime(df["time"], unit="ms")
    df = df.sort_values("datetime")
    rows = [r for _, r in df.iterrows()]
    vm = infer_tick_vol_to_shares_multiplier(df)
    vols = per_tick_trade_volumes_list(rows, vm)

    ps = 0.0
    pc = 0
    prev_lp: Optional[float] = None
    prev_row: Optional[Dict[str, Any]] = None
    rr: List[Dict[str, Any]] = []
    rv: List[Optional[float]] = []
    run_start = time(9, 30)

    for j, row in enumerate(rows):
        ts = row["datetime"]
        t = ts.time()
        lp = float(row.get("lastPrice") or 0)
        if lp <= 0:
            continue
        vb = vols[j] if j < len(vols) else None
        row_d = dict(row)

        if t < run_start:
            if vb is not None:
                ps += float(vb)
                pc += 1
            prev_row = row_d
            prev_lp = lp
            rr = (rr + [row_d])[-5:]
            rv = (rv + [vb])[-10:]
            continue

        crossed = is_breakthrough_buy_price_cross_tick(code, lp, trigger, prev_lp)
        if crossed:
            avg_before = (ps / pc) if pc > 0 else None
            v_cond1 = max_cond1_breakthrough_volume_from_recent(rv, vb)
            ok, _msg, detail, metrics = evaluate_true_breakthrough_tick_with_detail(
                code,
                row_d,
                prev_row,
                vm,
                avg_before,
                vb,
                (rr + [row_d])[-5:],
                v_break_cond1=v_cond1,
                recent_vols=rv,
            )
            return {
                "time": str(ts)[11:19],
                "last": lp,
                "prev": prev_lp,
                "passed": ok,
                "detail": detail,
                "r1": metrics.get("ratio_cond1"),
                "r2": metrics.get("ask_bid_ratio_cond2"),
                "r3": metrics.get("ratio_cond3"),
            }

        if vb is not None:
            ps += float(vb)
            pc += 1
        prev_row = row_d
        prev_lp = lp
        rr = (rr + [row_d])[-5:]
        rv = (rv + [vb])[-10:]
    return None


def main() -> None:
    df = pd.read_excel(OLD_XLS, sheet_name=0)
    code_col = next(c for c in df.columns if "股票" in str(c) and "代码" in str(c))
    date_col = next(c for c in df.columns if "选股日" in str(c))
    buy_col = next(c for c in df.columns if "买入" in str(c) and "时间" in str(c))
    amt_col = next(c for c in df.columns if "买入" in str(c) and "合计" in str(c))
    vol_col = next(c for c in df.columns if "买入" in str(c) and "数量" in str(c))
    ret_col = next(c for c in df.columns if "pct" in str(c).lower())

    new_keys = set()
    new_path = ROOT / "history_data" / "回测七月" / "各日选股收益汇总_7月_全部涨停后1-2日修正后.xlsx"
    nd = pd.read_excel(new_path)
    nk = (
        pd.to_datetime(nd[date_col]).dt.strftime("%Y-%m-%d")
        + "_"
        + nd[code_col].astype(str).str.zfill(6)
    )
    new_keys = set(nk)

    df["_key"] = (
        pd.to_datetime(df[date_col]).dt.strftime("%Y-%m-%d")
        + "_"
        + df[code_col].astype(str).str.zfill(6)
    )
    removed = df[~df["_key"].isin(new_keys)].sort_values(date_col)

    print("修正消失的 9 笔 — 首笔上穿真突破复盘\n")
    open_first = 0
    early_first = 0
    later_old_buy = 0

    for _, r in removed.iterrows():
        code = str(r[code_col]).replace(".0", "").zfill(6)
        d = pd.to_datetime(r[date_col]).date()
        fill = float(r[amt_col]) / float(r[vol_col])
        old_buy = str(r[buy_col])
        # 触发价约在成交价下方 0~0.15 元
        trigger = None
        hit = None
        for delta in [x / 100.0 for x in range(0, 16)]:
            trig = round(fill - delta, 2)
            hit = _first_cross_tb(code, d, trig)
            if hit is not None:
                trigger = trig
                break
        print(f"{d} {code} 旧买入{old_buy} 收益{r[ret_col]:+.2f}%")
        if not hit:
            print("  未找到上穿\n")
            continue
        print(f"  推断触发价≈{trigger:.2f}  首笔上穿={hit['time']} 价{hit['last']:.2f} (前价{hit['prev']})")
        print(f"  首笔真突破={'过' if hit['passed'] else '不过'}  ①{hit['r1']} ②{hit['r2']} ③{hit['r3']}")
        if hit["detail"]:
            print(f"  {hit['detail'][:100]}")
        if hit["time"] <= "09:30:59":
            open_first += 1
            tag = "首笔在开盘1分钟内"
        else:
            early_first += 1
            tag = "首笔在上午更早"
        if old_buy > "09:31:00" or (old_buy > hit["time"]):
            later_old_buy += 1
            tag += "；旧回测更晚才买（第二次机会）"
        print(f"  → {tag}\n")

    print("汇总:")
    print(f"  首笔上穿在 09:30 内: {open_first} 笔")
    print(f"  首笔上穿在 09:31 后: {early_first} 笔")
    print(f"  旧回测买入晚于首笔上穿: {later_old_buy} 笔")


if __name__ == "__main__":
    main()
