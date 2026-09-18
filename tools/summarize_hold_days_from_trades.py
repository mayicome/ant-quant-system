# -*- coding: utf-8 -*-
"""从既有买卖成交明细，近似生成持仓 1～N 日的选股收益汇总（不重跑回测）。

口径（用户约定）：
- 买入全部保留；同一选股日+代码若有第二腿（买入笔数>=2），持仓结束日改为
  从末笔买入日（第二腿）次日起算持仓 N 日，否则仍从首买日次日起算；
- 只保留锚定结束日（含）以内、且不早于该腿买入日的卖出；
- 若该票在结束日前已卖完 → 用真实卖出算收益；
- 若仍有剩余 → 用结束日收盘价盯市。

资金无穷大假设下，不做组合资金再分配。
"""
from __future__ import annotations

import argparse
import csv
import sys
import tempfile
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.merge_backtest_trades_by_selection import (  # noqa: E402
    _build_prices_by_mark_date,
    _code6_from_row,
    _int_vol,
    _norm_sel_key,
    _num,
    _parse_row_date,
    _read_rows,
    _sel_from_row,
    aggregate,
    apply_buy_day_ma5_ref_fields,
    apply_hold_end_date_from_buy,
    apply_ma_fields_from_daily_cache,
    apply_mark_and_returns,
    apply_selection_file_fields,
)


def _side_norm(r: dict) -> str:
    s = str(r.get("方向") or r.get("side") or "").strip()
    if "买" in s:
        return "买入"
    if "卖" in s:
        return "卖出"
    return s


def _hold_end(buy_d: date, hold_n: int) -> Optional[date]:
    """买入次日=持有第1日 → 结束日=含买入日共 hold_n+1 个交易日的末日。"""
    try:
        from strategy_generator_app.trading_calendar import trading_day_window_from_start
    except ImportError:
        from trading_calendar import trading_day_window_from_start  # type: ignore

    n = max(1, int(hold_n))
    _s, end_d, _msg = trading_day_window_from_start(buy_d, n + 1)
    return end_d


def _buy_sel_key(r: dict) -> str:
    return _norm_sel_key(_sel_from_row(r, fallback_trade_date=True))


def _hold_anchor_by_group(
    buy_rows: List[dict],
) -> Dict[Tuple[str, str], date]:
    """同选股日+代码：单腿→首买日；有第二腿（>=2笔）→末笔买入日。"""
    first: Dict[Tuple[str, str], date] = {}
    last: Dict[Tuple[str, str], date] = {}
    n_buys: Dict[Tuple[str, str], int] = defaultdict(int)
    for r in buy_rows:
        if _side_norm(r) != "买入":
            continue
        code = _code6_from_row(r)
        bd = _parse_row_date(r.get("日期") or r.get("date"))
        vol = _int_vol(r.get("数量") or r.get("volume"))
        if not code or bd is None or vol <= 0:
            continue
        sel = _buy_sel_key(r)
        if not sel:
            continue
        k = (sel, code)
        n_buys[k] += 1
        if k not in first or bd < first[k]:
            first[k] = bd
        if k not in last or bd >= last[k]:
            last[k] = bd
    out: Dict[Tuple[str, str], date] = {}
    for k, d0 in first.items():
        if n_buys.get(k, 0) >= 2:
            out[k] = last[k]
        else:
            out[k] = d0
    return out


def filter_buys_within_hold(
    buy_rows: List[dict],
    hold_n: int,
) -> Tuple[List[dict], dict]:
    """保留全部买入（有第二腿时由结束日延后覆盖，不再剔后腿）。

    仍返回 stats，便于旧脚本兼容；hold_n 未使用。
    """
    _ = hold_n  # 口径改为延后结束日，不再按首买窗丢弃后腿
    n = sum(1 for r in buy_rows if _side_norm(r) == "买入")
    stats = {
        "buy_in": n,
        "buy_kept": n,
        "buy_dropped": 0,
        "buy_dropped_on_end": 0,
        "buy_dropped_after_end": 0,
        "groups_trimmed": 0,
        "anchor_rule": "last_buy_if_multi_leg",
    }
    return list(buy_rows), stats


def filter_sells_within_hold(
    buy_rows: List[dict],
    sell_rows: List[dict],
    hold_n: int,
) -> Tuple[List[dict], dict]:
    """按代码 FIFO 腿，丢弃超过「选股锚定结束日」的卖出；可拆薄单笔数量/金额。

    锚定：单腿=首买日；有第二腿=末笔买入日（从该日次日起算 hold_n）。
    """
    anchor_by = _hold_anchor_by_group(buy_rows)

    lots_by_code: Dict[str, List[dict]] = defaultdict(list)
    for r in buy_rows:
        if _side_norm(r) != "买入":
            continue
        code = _code6_from_row(r)
        bd = _parse_row_date(r.get("日期") or r.get("date"))
        vol = _int_vol(r.get("数量") or r.get("volume"))
        if not code or bd is None or vol <= 0:
            continue
        sel = _buy_sel_key(r)
        anchor = anchor_by.get((sel, code), bd)
        end_d = _hold_end(anchor, hold_n)
        if end_d is None:
            continue
        lots_by_code[code].append(
            {
                "buy_d": bd,
                "end_d": end_d,
                "left": int(vol),
            }
        )
    for code in lots_by_code:
        lots_by_code[code].sort(key=lambda x: x["buy_d"])

    sells = [r for r in sell_rows if _side_norm(r) == "卖出"]
    sells.sort(
        key=lambda r: (
            str(_parse_row_date(r.get("日期") or r.get("date")) or ""),
            str(r.get("时间") or r.get("time") or ""),
            str(r.get("腿键") or r.get("规则名") or ""),
        )
    )

    out: List[dict] = []
    stats = {
        "sell_in": 0,
        "sell_kept_full": 0,
        "sell_kept_partial": 0,
        "sell_dropped": 0,
        "vol_in": 0,
        "vol_kept": 0,
    }
    for r in sells:
        code = _code6_from_row(r)
        sell_d = _parse_row_date(r.get("日期") or r.get("date"))
        vol = _int_vol(r.get("数量") or r.get("volume"))
        amt = _num(r.get("金额") or r.get("amount"))
        if not code or sell_d is None or vol <= 0:
            continue
        stats["sell_in"] += 1
        stats["vol_in"] += vol
        lots = lots_by_code.get(code) or []
        take = 0
        for lot in lots:
            if take >= vol:
                break
            # 卖出不得早于该腿买入日（避免把更早卖出误配到后续买入）
            if sell_d < lot["buy_d"]:
                continue
            if sell_d > lot["end_d"]:
                continue
            if lot["left"] <= 0:
                continue
            got = min(lot["left"], vol - take)
            lot["left"] -= got
            take += got
        if take <= 0:
            stats["sell_dropped"] += 1
            continue
        nr = dict(r)
        if take < vol:
            stats["sell_kept_partial"] += 1
            # 按数量比例拆金额
            if vol > 0 and amt:
                nr["金额"] = round(float(amt) * take / vol, 2)
            nr["数量"] = int(take)
        else:
            stats["sell_kept_full"] += 1
        stats["vol_kept"] += take
        out.append(nr)
    return out, stats


def _write_csv(rows: List[dict], path: Path) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    # union keys preserving first-seen order
    keys: List[str] = []
    seen = set()
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                keys.append(k)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in keys})


def summarize_one(
    buy_path: Path,
    sell_rows_filtered: List[dict],
    hold_n: int,
    selection_path: Optional[Path] = None,
    buy_rows_filtered: Optional[List[dict]] = None,
) -> Tuple[List[dict], dict]:
    with tempfile.TemporaryDirectory() as td:
        sell_p = Path(td) / f"sell_hold{hold_n}.csv"
        _write_csv(sell_rows_filtered, sell_p)
        if buy_rows_filtered is not None:
            buy_p = Path(td) / f"buy_hold{hold_n}.csv"
            _write_csv(buy_rows_filtered, buy_p)
        else:
            buy_p = buy_path
        rows = aggregate(buy_p, sell_p)
    end_warns = apply_hold_end_date_from_buy(rows, hold_from_next_day=hold_n)
    prices_by_mark, price_warn = _build_prices_by_mark_date(
        rows,
        mark_n=hold_n,
        use_nth_trading_day=False,
        use_last_available=False,
    )
    apply_mark_and_returns(
        rows,
        prices_by_mark,
        price_warn,
        mark_n=hold_n,
        use_nth_trading_day=False,
        use_last_available=False,
    )
    if selection_path is not None and selection_path.is_file():
        try:
            apply_selection_file_fields(rows, selection_path)
        except Exception:
            pass
    try:
        apply_ma_fields_from_daily_cache(rows)
    except Exception:
        pass
    try:
        apply_buy_day_ma5_ref_fields(rows)
    except Exception:
        pass
    meta = {
        "hold_n": hold_n,
        "n_rows": len(rows),
        "n_open": sum(1 for r in rows if int(r.get("剩余持仓数量") or 0) > 0),
        "n_cleared": sum(1 for r in rows if int(r.get("剩余持仓数量") or 0) <= 0),
        "end_warns": len(end_warns),
        "price_warn": price_warn or "",
    }
    # 汇总收益
    rets = []
    buy_amts = []
    pnls = []
    for r in rows:
        ba = float(r.get("买入金额合计") or 0)
        if ba <= 0:
            continue
        buy_amts.append(ba)
        try:
            rp = float(r.get("收益率pct") or 0)
        except (TypeError, ValueError):
            rp = 0.0
        rets.append(rp)
        # pnl ≈ 买金额 * 收益率/100
        pnls.append(ba * rp / 100.0)
    meta["n_with_buy"] = len(buy_amts)
    meta["mean_ret_pct"] = round(sum(rets) / len(rets), 4) if rets else None
    meta["med_ret_pct"] = round(sorted(rets)[len(rets) // 2], 4) if rets else None
    meta["total_buy"] = round(sum(buy_amts), 2) if buy_amts else 0.0
    meta["total_pnl"] = round(sum(pnls), 2) if pnls else 0.0
    meta["total_ret_pct"] = (
        round(100.0 * meta["total_pnl"] / meta["total_buy"], 4)
        if meta["total_buy"]
        else None
    )
    meta["win_rate"] = (
        round(100.0 * sum(1 for x in rets if x > 0) / len(rets), 2) if rets else None
    )
    return rows, meta


def main() -> int:
    ap = argparse.ArgumentParser(description="持仓1～N日近似收益汇总（截断卖出+到期盯市）")
    ap.add_argument(
        "--buy",
        default=str(ROOT / "history_data" / "马总盘后新" / "回测成交明细_612-828_买入.csv"),
    )
    ap.add_argument(
        "--sell",
        default=str(ROOT / "history_data" / "马总盘后新" / "回测成交明细_612-828_卖出.csv"),
    )
    ap.add_argument(
        "--out",
        default=str(
            ROOT / "history_data" / "马总盘后新" / "各日选股收益汇总_612-828_持仓1to9.xlsx"
        ),
    )
    ap.add_argument("--min-hold", type=int, default=1)
    ap.add_argument("--max-hold", type=int, default=9)
    ap.add_argument(
        "--also-10",
        action="store_true",
        help="额外输出持仓10日（同口径截断，便于对照原10日回测）",
    )
    ap.add_argument(
        "--selection",
        default="",
        help="可选选股结果文件（xls/xlsx/csv），回填选股日相关字段",
    )
    args = ap.parse_args()

    buy_p = Path(args.buy)
    sell_p = Path(args.sell)
    out_p = Path(args.out)
    sel_p = Path(args.selection) if str(args.selection or "").strip() else None
    if not buy_p.is_file() or not sell_p.is_file():
        print("找不到买入/卖出文件", file=sys.stderr)
        return 1
    if sel_p is not None and not sel_p.is_file():
        print(f"找不到选股文件: {sel_p}", file=sys.stderr)
        return 1

    buy_rows = _read_rows(buy_p)
    sell_rows = _read_rows(sell_p)
    holds = list(range(max(1, args.min_hold), max(1, args.max_hold) + 1))
    if args.also_10 and 10 not in holds:
        holds.append(10)

    try:
        import pandas as pd
    except ImportError:
        print("需要 pandas 写 xlsx", file=sys.stderr)
        return 1

    overview = []
    sheets: Dict[str, Any] = {}
    out_p.parent.mkdir(parents=True, exist_ok=True)

    for n in holds:
        print(f"[hold={n}] 过滤卖出（多腿按末笔买入锚定结束日）…", flush=True)
        filtered, st = filter_sells_within_hold(buy_rows, sell_rows, n)
        print(
            f"[hold={n}] sells kept {st['sell_kept_full']}+partial{st['sell_kept_partial']}"
            f" / in{st['sell_in']}；vol {st['vol_kept']}/{st['vol_in']}",
            flush=True,
        )
        print(f"[hold={n}] 汇总+盯市…", flush=True)
        rows, meta = summarize_one(buy_p, filtered, n, selection_path=sel_p)
        meta.update(st)
        overview.append(meta)
        df = pd.DataFrame(rows)
        front = [
            "选股日",
            "代码",
            "股票名称",
            "买入日",
            "末笔买入日",
            "持有交易日数",
            "计划持仓结束日",
            "end_date",
            "盯市日期",
            "盯市类型",
        ]
        cols = list(df.columns)
        ordered = [c for c in front if c in cols] + [c for c in cols if c not in front]
        if ordered:
            df = df[ordered]
        sheets[f"持仓{n}日"] = df
        print(
            f"[hold={n}] rows={meta['n_rows']} cleared={meta['n_cleared']} open={meta['n_open']} "
            f"mean={meta['mean_ret_pct']}% total_ret={meta['total_ret_pct']}%",
            flush=True,
        )

    ov = pd.DataFrame(overview)
    prefer = [
        "hold_n",
        "n_rows",
        "n_with_buy",
        "n_cleared",
        "n_open",
        "mean_ret_pct",
        "med_ret_pct",
        "total_ret_pct",
        "win_rate",
        "total_buy",
        "total_pnl",
        "sell_kept_full",
        "sell_kept_partial",
        "sell_dropped",
        "vol_kept",
        "vol_in",
        "end_warns",
        "price_warn",
    ]
    ov_cols = [c for c in prefer if c in ov.columns] + [
        c for c in ov.columns if c not in prefer
    ]
    if ov_cols:
        ov = ov[ov_cols]
    sheets["概览"] = ov

    with pd.ExcelWriter(out_p, engine="openpyxl") as w:
        for name, df in sheets.items():
            df.to_excel(w, sheet_name=name[:31], index=False)
    print("wrote", out_p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
