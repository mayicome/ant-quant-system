# -*- coding: utf-8 -*-
"""开盘腿固定 drop；近涨停腿按与开盘腿触发价关系分档扫描。

- 开盘涨幅腿：始终 ``--open-drop``（默认 2）
- 近涨停腿：能比价时
    - 近涨停触发价 > 开盘腿触发价 → ``drop_gt``
    - 否则 → ``drop_lt``；相等可用 ``--drop-eq``
  缺一侧触发价时近涨停腿回退 ``open-drop``

网格默认：drop_gt × drop_lt ∈ {1,1.5,2,2.5,3}²（25 组）。
固定买入 CSV，只重放卖出；默认 ``--hold 10``。

用法:
  python tools/sweep_elastic_sell_drop_lu_vs_open.py --open-drop 2
  python tools/sweep_elastic_sell_drop_lu_vs_open.py --open-drop 1.5 --drops-gt 1,1.5,2,2.5,3 --drops-lt 1,1.5,2,2.5,3
  python tools/sweep_elastic_sell_drop_lu_vs_open.py --hold 10 --fill-mode same_day_ohlc --max-buys 20
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.sweep_elastic_sell_drop import (  # noqa: E402
    DEFAULT_BUY,
    DEFAULT_STRATEGY,
    INITIAL_CASH,
    _fills_from_buy_rows,
    _first_buy_dates,
    _fmt_drop,
    _load_strategy,
    _norm_code6,
    _parse_drops,
    _prepare_injection,
    _read_buy_rows,
    _resolve_sell_id,
    _sell_window,
    _summarize_hold,
    _trade_to_csv_row,
    _write_csv,
    run_one_drop,
)

DEFAULT_OUT = ROOT / "history_data" / "马总盘后新" / "elastic_drop_sweep_lu_vs_open"
DEFAULT_GRID = "1,1.5,2,2.5,3"


def _grid_cases(
    drops_gt: List[float], drops_lt: List[float]
) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for g in drops_gt:
        for t in drops_lt:
            out.append((g, t))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="开盘腿固定 drop；近涨停腿按 LU vs OPEN 触发价分档网格扫描"
    )
    ap.add_argument("--buy", default=str(DEFAULT_BUY), help="买入成交明细 CSV")
    ap.add_argument("--strategy", default=DEFAULT_STRATEGY, help="弹性卖策略 id")
    ap.add_argument(
        "--open-drop",
        type=float,
        default=2.0,
        help="开盘涨幅腿固定 drop%%（默认 2）",
    )
    ap.add_argument(
        "--drops-gt",
        default=DEFAULT_GRID,
        help="近涨停>开盘腿 时近涨停腿 drop%% 列表（默认 1,1.5,2,2.5,3）",
    )
    ap.add_argument(
        "--drops-lt",
        default=DEFAULT_GRID,
        help="近涨停<=开盘腿 时近涨停腿 drop%% 列表（默认 1,1.5,2,2.5,3）",
    )
    ap.add_argument(
        "--drop-eq",
        type=float,
        default=None,
        help="两触发价相等时近涨停腿 drop%%（默认用当日 drops-gt）",
    )
    ap.add_argument(
        "--hold",
        type=int,
        default=10,
        help="持有交易日数：买入次日=第1日（默认 10）",
    )
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT), help="输出目录")
    ap.add_argument(
        "--fill-mode",
        default="tick",
        choices=("tick", "same_day_ohlc"),
        help="撮合模式（默认 tick；冒烟用 same_day_ohlc）",
    )
    ap.add_argument("--max-buys", type=int, default=0, help="仅前 N 笔买入（0=全部）")
    ap.add_argument("--no-summarize", action="store_true", help="跳过持仓盯市汇总")
    ap.add_argument("--selection", default="", help="可选选股文件")
    ap.add_argument("--cash", type=float, default=INITIAL_CASH)
    args = ap.parse_args()

    buy_path = Path(args.buy)
    if not buy_path.is_absolute():
        buy_path = ROOT / buy_path
    if not buy_path.is_file():
        print(f"找不到买入文件: {buy_path}", file=sys.stderr)
        return 1

    open_drop = float(args.open_drop)
    drops_gt = _parse_drops(args.drops_gt)
    drops_lt = _parse_drops(args.drops_lt)
    cases = _grid_cases(drops_gt, drops_lt)
    if not cases:
        print("网格为空", file=sys.stderr)
        return 1

    sid = _resolve_sell_id(args.strategy)
    sell_name, sell_code, sell_params0 = _load_strategy(sid)
    if "drop_percent_when_lu_gt_open" not in sell_code:
        print(
            "策略未含关系分档参数，请先运行: "
            "python tools/install_ma_zong1_sell_strategy.py",
            file=sys.stderr,
        )
        return 1

    sell_hold = max(1, int(args.hold))
    engine_hold_n = sell_hold + 1
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    sel_path = Path(args.selection) if str(args.selection or "").strip() else None
    if sel_path is not None and not sel_path.is_absolute():
        sel_path = ROOT / sel_path
    if sel_path is not None and not sel_path.is_file():
        print(f"找不到选股文件: {sel_path}", file=sys.stderr)
        return 1

    fill_mode = str(args.fill_mode)
    use_tick = fill_mode == "tick"
    base_cash = float(args.cash)

    buy_rows = _read_buy_rows(buy_path)
    if int(args.max_buys or 0) > 0:
        buy_rows = buy_rows[: int(args.max_buys)]
    fills = _fills_from_buy_rows(buy_rows)
    if not fills:
        print("买入 CSV 无有效买入成交", file=sys.stderr)
        return 1
    first_buys = _first_buy_dates(fills)
    start_d, end_d, wnote = _sell_window(first_buys, sell_hold)
    if start_d is None or end_d is None:
        print(f"卖出窗口失败: {wnote}", file=sys.stderr)
        return 1
    icash, init_pos, scheduled = _prepare_injection(
        fills, start_d, first_buys, base_cash=base_cash
    )
    codes = sorted(
        set(list(init_pos.keys()) + [_norm_code6(f.get("code")) for f in fills])
    )
    codes = [c for c in codes if c]

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"买文件: {buy_path}")
    print(f"卖策略: {sell_name} ({sid})")
    print(f"买入笔数: {len(fills)}  标的: {len(codes)}")
    print(f"持有: {sell_hold}日（次日=第1；引擎N={engine_hold_n}）")
    print(f"窗口: {start_d}～{end_d}  | {wnote}")
    print(
        f"模式: 开盘腿固定 open_drop={open_drop}% ; "
        f"LU>OPEN → lu drops_gt={drops_gt} ; LU<=OPEN → lu drops_lt={drops_lt} "
        f"（{len(cases)} 组） fill_mode={fill_mode}"
    )
    if args.drop_eq is not None:
        print(f"相等档近涨停 drop_eq={args.drop_eq}")
    print(f"输出: {out_dir}")

    overview: List[dict] = []
    do_sum = not bool(args.no_summarize)

    for i, (drop_gt, drop_lt) in enumerate(cases):
        drop_eq = float(args.drop_eq) if args.drop_eq is not None else float(drop_gt)
        tag = (
            f"[{i + 1}/{len(cases)}] open={open_drop}% "
            f"lu_gt={drop_gt}% lu_lt={drop_lt}%"
        )
        print(f"\n{tag} 开始…", flush=True)
        clear_ticks = i == len(cases) - 1
        ctag = (
            f"open{_fmt_drop(open_drop)}_gt{_fmt_drop(drop_gt)}_lt{_fmt_drop(drop_lt)}"
        )

        def _progress(msg: str, pct: Optional[int] = None) -> None:
            if pct is None:
                print(f"  {msg}", flush=True)
            else:
                print(f"  [{pct:3d}%] {msg}", flush=True)

        try:
            one = run_one_drop(
                open_drop=open_drop,
                lu_drop=open_drop,
                sell_code=sell_code,
                sell_params0=sell_params0,
                codes=codes,
                start_d=start_d,
                end_d=end_d,
                icash=icash,
                init_pos=init_pos,
                scheduled=scheduled,
                first_buys=first_buys,
                engine_hold_n=engine_hold_n,
                sell_hold=sell_hold,
                fill_mode=fill_mode,
                use_tick=use_tick,
                clear_ticks=clear_ticks,
                progress=_progress if len(codes) <= 30 else None,
                drop_when_lu_gt_open=float(drop_gt),
                drop_when_lu_lt_open=float(drop_lt),
                drop_when_lu_eq_open=float(drop_eq),
            )
        except Exception as ex:
            print(f"{tag} 失败: {ex}", file=sys.stderr)
            overview.append(
                {
                    "open_drop_percent": open_drop,
                    "drop_when_lu_gt_open": drop_gt,
                    "drop_when_lu_lt_open": drop_lt,
                    "drop_when_lu_eq_open": drop_eq,
                    "error": str(ex),
                }
            )
            continue

        sell_csv_rows = [_trade_to_csv_row(t) for t in one["sell_trades"]]
        all_csv_rows = [_trade_to_csv_row(t) for t in one["trades"]]
        sell_path = out_dir / f"卖出_{ctag}_{stamp}.csv"
        all_path = out_dir / f"成交_{ctag}_{stamp}.csv"
        _write_csv(sell_path, sell_csv_rows)
        _write_csv(all_path, all_csv_rows)

        m = one["metrics"] or {}
        row: Dict[str, Any] = {
            "open_drop_percent": open_drop,
            "drop_when_lu_gt_open": drop_gt,
            "drop_when_lu_lt_open": drop_lt,
            "drop_when_lu_eq_open": drop_eq,
            "sell_trades": len(one["sell_trades"]),
            "buy_injected": len(one["buy_injected"]),
            "total_return_pct": m.get("total_return_pct", m.get("总收益率")),
            "final_equity": m.get("final_equity", m.get("期末权益")),
            "sell_csv": sell_path.name,
            "trades_csv": all_path.name,
        }

        if do_sum:
            print(f"{tag} 持仓{sell_hold}日盯市汇总…", flush=True)
            try:
                sum_rows, meta = _summarize_hold(
                    buy_path, sell_csv_rows, sell_hold, sel_path
                )
                sum_path = out_dir / f"汇总_hold{sell_hold}_{ctag}_{stamp}.xlsx"
                try:
                    import pandas as pd

                    pd.DataFrame(sum_rows).to_excel(sum_path, index=False)
                    row["summary_xlsx"] = sum_path.name
                except Exception as e:
                    row["summary_xlsx_error"] = str(e)
                row.update(
                    {
                        "sum_n_rows": meta.get("n_rows"),
                        "sum_n_cleared": meta.get("n_cleared"),
                        "sum_n_open": meta.get("n_open"),
                        "sum_mean_ret_pct": meta.get("mean_ret_pct"),
                        "sum_med_ret_pct": meta.get("med_ret_pct"),
                        "sum_total_ret_pct": meta.get("total_ret_pct"),
                        "sum_total_pnl": meta.get("total_pnl"),
                        "sum_total_buy": meta.get("total_buy"),
                        "sum_win_rate": meta.get("win_rate"),
                    }
                )
                print(
                    f"{tag} 卖{len(one['sell_trades'])}笔 | "
                    f"盯市总收益 {meta.get('total_ret_pct')}% "
                    f"均 {meta.get('mean_ret_pct')}% 胜率 {meta.get('win_rate')}%",
                    flush=True,
                )
            except Exception as e:
                row["summarize_error"] = str(e)
                print(f"{tag} 汇总失败: {e}", file=sys.stderr)
        else:
            print(f"{tag} 卖出 {len(one['sell_trades'])} 笔 → {sell_path.name}", flush=True)

        overview.append(row)

    ov_path = out_dir / f"overview_lu_vs_open_{stamp}.csv"
    _write_csv(ov_path, overview)
    try:
        import pandas as pd

        xlsx_path = out_dir / f"overview_lu_vs_open_{stamp}.xlsx"
        df = pd.DataFrame(overview)
        if do_sum and "sum_total_ret_pct" in df.columns and not df.empty:
            pivot = df.pivot_table(
                index="drop_when_lu_gt_open",
                columns="drop_when_lu_lt_open",
                values="sum_total_ret_pct",
                aggfunc="first",
            )
            with pd.ExcelWriter(xlsx_path, engine="openpyxl") as w:
                df.to_excel(w, index=False, sheet_name="明细")
                pivot.to_excel(w, sheet_name="总收益透视_gt行_lt列")
        else:
            df.to_excel(xlsx_path, index=False)
        print(f"\n对照表: {xlsx_path}")
    except Exception:
        print(f"\n对照表: {ov_path}")

    scored = [r for r in overview if r.get("sum_total_ret_pct") is not None]
    scored.sort(key=lambda r: float(r["sum_total_ret_pct"]), reverse=True)
    print(
        f"\n=== 开盘腿固定 {open_drop}% | LU>OPEN → lu_gt × LU<=OPEN → lu_lt ==="
    )
    hdr = (
        f"{'open%':>6} {'lu_gt%':>7} {'lu_lt%':>7} {'卖笔':>6} "
        f"{'盯市总收益%':>12} {'均收益%':>10} {'胜率%':>8}"
    )
    print(hdr)
    show = scored if scored else overview
    for r in show:
        if r.get("error"):
            print(
                f"{r.get('open_drop_percent'):>6} {r.get('drop_when_lu_gt_open'):>7} "
                f"{r.get('drop_when_lu_lt_open'):>7}  ERROR {r.get('error')}"
            )
            continue
        print(
            f"{r.get('open_drop_percent'):>6} {r.get('drop_when_lu_gt_open'):>7} "
            f"{r.get('drop_when_lu_lt_open'):>7} "
            f"{r.get('sell_trades') or 0:>6} "
            f"{str(r.get('sum_total_ret_pct') if do_sum else r.get('total_return_pct')):>12} "
            f"{str(r.get('sum_mean_ret_pct') or '-'):>10} "
            f"{str(r.get('sum_win_rate') or '-'):>8}"
        )
    if scored:
        best = scored[0]
        print(
            f"\n最优（盯市总收益）: open={best['open_drop_percent']}% "
            f"lu_gt={best['drop_when_lu_gt_open']}% "
            f"lu_lt={best['drop_when_lu_lt_open']}% → {best['sum_total_ret_pct']}%"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
