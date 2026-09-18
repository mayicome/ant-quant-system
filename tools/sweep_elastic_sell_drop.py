# -*- coding: utf-8 -*-
"""固定买入成交，扫描弹性卖 drop_percent。

不重跑买入：从买入 CSV 注入仓位/成交，只换回落比例重放卖出。

模式：
  1) 两腿共用：``--drops 1,1.5,2,2.5,3``
  2) 分腿：开盘涨幅腿固定 + 近涨停腿扫描
     ``--open-drop 1.5 --lu-drops 1,1.5,2,2.5,3``
     或反过来：``--lu-drop 1.5 --open-drops 1,1.5,2``

策略参数：
  ``drop_percent`` 共用默认；``open_drop_percent`` / ``lu_drop_percent`` 分腿覆盖。
  涨停清仓 drop=0 不变。

口径与 UI「下一轮接续」/ ``--sell-hold`` 一致：
  买入次日 = 持有第 1 日；第 N 日无条件清仓。
  引擎序号含买入日 → 注入 scheduled_clear = N+1。

用法:
  python tools/sweep_elastic_sell_drop.py --drops 1,1.5,2,2.5,3 --hold 10
  python tools/sweep_elastic_sell_drop.py --open-drop 1.5 --lu-drops 1,1.5,2,2.5,3 --hold 10
  python tools/sweep_elastic_sell_drop.py --fill-mode same_day_ohlc --max-buys 20
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STRAT_DIR = ROOT / "strategy_generator_app" / "config" / "strategies"
DEFAULT_BUY = ROOT / "history_data" / "马总盘后新" / "回测成交明细_612-828_买入.csv"
DEFAULT_STRATEGY = "strategy_5a1fa73f"
DEFAULT_OUT = ROOT / "history_data" / "马总盘后新" / "elastic_drop_sweep"
FILLED_LEGS_PATH = ROOT / "data" / "ma_zong1_sell_filled_legs.json"
INITIAL_CASH = 100_000_000.0


def _norm_code6(v: Any) -> str:
    s = str(v or "").strip()
    if not s or s.lower() in ("nan", "none"):
        return ""
    if "." in s:
        s = s.split(".", 1)[0]
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)[-6:]


def _parse_d(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip().replace("/", "-")
    if not s or s.lower() in ("nan", "none"):
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    # 2026-7-1 / 2026/7/1
    parts = s.replace(".", "-").split("-")
    if len(parts) >= 3:
        try:
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2][:2])
            return date(y, m, d)
        except (TypeError, ValueError):
            return None
    return None


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _int_vol(v: Any) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _side_is_buy(row: dict) -> bool:
    s = str(row.get("方向") or row.get("side") or "").strip().lower()
    return s in ("买入", "buy", "b")


def _load_strategy(sid: str) -> Tuple[str, str, Dict[str, Any]]:
    p = STRAT_DIR / f"{sid}.json"
    if not p.is_file():
        raise FileNotFoundError(f"策略不存在: {p}")
    d = json.loads(p.read_text(encoding="utf-8"))
    return (
        str(d.get("name") or sid),
        str(d.get("strategy_code") or ""),
        dict(d.get("strategy_params") or {}),
    )


def _resolve_sell_id(spec: str) -> str:
    key = str(spec or "").strip()
    if not key:
        return DEFAULT_STRATEGY
    aliases = {
        "elastic": DEFAULT_STRATEGY,
        "弹性": DEFAULT_STRATEGY,
        "5a1fa73f": DEFAULT_STRATEGY,
        DEFAULT_STRATEGY: DEFAULT_STRATEGY,
    }
    if key in aliases:
        return aliases[key]
    cand = key if key.startswith("strategy_") else f"strategy_{key}"
    if (STRAT_DIR / f"{cand}.json").is_file():
        return cand
    want = key.replace(" ", "")
    for p in STRAT_DIR.glob("strategy_*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        name = str(d.get("name") or "")
        if "卖" not in name:
            continue
        if want in name.replace(" ", "") or ("弹性半仓" in name and "弹性" in want):
            return str(d.get("id") or p.stem)
    raise SystemExit(f"未知卖出策略: {spec!r}（默认 {DEFAULT_STRATEGY}）")


def _read_buy_rows(path: Path) -> List[dict]:
    from tools.merge_backtest_trades_by_selection import _read_rows

    rows = _read_rows(path)
    return [r for r in rows if _side_is_buy(r)]


def _fills_from_buy_rows(rows: List[dict]) -> List[dict]:
    out: List[dict] = []
    for r in rows:
        code = _norm_code6(r.get("代码") or r.get("code") or r.get("stock_code"))
        bd = _parse_d(r.get("日期") or r.get("date") or r.get("trade_date"))
        vol = _int_vol(r.get("数量") or r.get("volume"))
        price = _num(r.get("价格") or r.get("price"))
        if not code or bd is None or vol <= 0 or price <= 0:
            continue
        amount = _num(r.get("金额") or r.get("amount"))
        if amount <= 0:
            amount = round(price * vol, 2)
        commission = _num(r.get("佣金") or r.get("commission"))
        tm = str(r.get("时间") or r.get("time") or "").strip() or "09:30:00"
        row = {
            "code": code,
            "date": bd.isoformat(),
            "time": tm,
            "volume": vol,
            "price": price,
            "amount": round(amount, 2),
            "commission": round(commission, 2),
            "side": "buy",
        }
        rn = str(r.get("规则名") or r.get("rule_name") or "").strip()
        if rn:
            row["rule_name"] = rn
        lk = str(r.get("腿键") or r.get("leg_key") or "").strip()
        if lk:
            row["leg_key"] = lk
        sel = _parse_d(r.get("选股日") or r.get("selection_date"))
        if sel is not None:
            row["选股日"] = sel.isoformat()
        out.append(row)
    return out


def _first_buy_dates(fills: List[dict]) -> Dict[str, date]:
    out: Dict[str, date] = {}
    for f in fills:
        code = _norm_code6(f.get("code"))
        d = _parse_d(f.get("date"))
        if not code or d is None:
            continue
        prev = out.get(code)
        if prev is None or d < prev:
            out[code] = d
    return out


def _sell_window(
    first_buys: Dict[str, date], hold_n: int
) -> Tuple[Optional[date], Optional[date], str]:
    """买入次日起持有 hold_n 日；全局窗 = 最早卖出日起 ～ 最晚清仓日。"""
    from strategy_generator_app.trading_calendar import (
        next_trading_day_after,
        trading_day_window_from_start,
    )

    if not first_buys:
        return None, None, "无买入成交"
    starts: List[date] = []
    ends: List[date] = []
    for c, fb in first_buys.items():
        ns = next_trading_day_after(fb)
        if ns is None:
            return None, None, f"无法计算 {c} 买入次日"
        _s, e_hold, wmsg = trading_day_window_from_start(ns, hold_n)
        if e_hold is None:
            return None, None, wmsg or f"{c} 持有窗口不足"
        starts.append(ns)
        ends.append(e_hold)
    return (
        min(starts),
        max(ends),
        f"按首次买入次日接续：{min(starts)}～{max(ends)}（{len(first_buys)}只，持有{hold_n}日）",
    )


def _prepare_injection(
    fills: List[dict],
    start_d: date,
    first_buys: Dict[str, date],
    *,
    base_cash: float = INITIAL_CASH,
) -> Tuple[float, Dict[str, Dict[str, Any]], List[dict]]:
    """窗前买入 → initial_positions；窗内/当日 → scheduled；现金加回待注入金额。"""
    pre_vol: Dict[str, int] = {}
    pre_cost: Dict[str, float] = {}
    scheduled: List[dict] = []
    cash_add = 0.0
    for f in fills:
        code = _norm_code6(f.get("code"))
        bd = _parse_d(f.get("date"))
        vol = _int_vol(f.get("volume"))
        price = _num(f.get("price"))
        amount = _num(f.get("amount"))
        if amount <= 0 and price > 0:
            amount = price * vol
        commission = _num(f.get("commission"))
        if not code or bd is None or vol <= 0 or price <= 0:
            continue
        row = dict(f)
        row["code"] = code
        row["date"] = bd.isoformat()
        row["amount"] = round(amount, 2)
        if bd < start_d:
            pre_vol[code] = int(pre_vol.get(code) or 0) + vol
            pre_cost[code] = float(pre_cost.get(code) or 0) + float(amount)
            row["blotter_only"] = True
            scheduled.append(row)
        else:
            scheduled.append(row)
            cash_add += float(amount) + float(commission)

    init_pos: Dict[str, Dict[str, Any]] = {}
    for code, vol in pre_vol.items():
        if vol <= 0:
            continue
        cost_amt = float(pre_cost.get(code) or 0)
        ed = first_buys.get(code)
        init_pos[code] = {
            "volume": int(vol),
            "cost": round(cost_amt / vol, 4) if vol else 0.0,
            **({"entry_date": ed.isoformat()} if ed is not None else {}),
        }
    return float(base_cash) + cash_add, init_pos, scheduled


def _clear_filled_legs_file() -> None:
    try:
        FILLED_LEGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        FILLED_LEGS_PATH.write_text(
            json.dumps(
                {
                    "legs": [],
                    "note": "cleared_for_drop_sweep",
                    "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def _get_name(code: str) -> str:
    try:
        from utils.stock_info_manager import get_stock_name

        return (get_stock_name(code) or "").strip()
    except Exception:
        try:
            from strategy_generator_app.account_provider import get_stock_name as _gn

            return (_gn(code) or "").strip()
        except Exception:
            return ""


def _trade_to_csv_row(t: dict) -> dict:
    code = _norm_code6(t.get("code") or t.get("stock_code"))
    side = str(t.get("side") or "").lower()
    side_zh = "买入" if side == "buy" else ("卖出" if side == "sell" else side)
    return {
        "日期": str(t.get("date") or ""),
        "时间": str(t.get("time") or ""),
        "代码": code,
        "股票名称": str(t.get("stock_name") or _get_name(code) or ""),
        "方向": side_zh,
        "价格": t.get("price"),
        "数量": t.get("volume"),
        "金额": t.get("amount"),
        "佣金": t.get("commission"),
        "交易后持仓": t.get("position_after"),
        "规则名": t.get("rule_name") or "",
        "腿键": t.get("leg_key") or "",
        "触发信息": t.get("trigger_info") or "",
    }


def _write_csv(path: Path, rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
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


def _parse_drops(s: str) -> List[float]:
    out: List[float] = []
    for part in str(s or "").replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        out.append(float(part))
    if not out:
        raise SystemExit("drop 列表为空")
    seen = set()
    uniq: List[float] = []
    for x in out:
        key = round(x, 6)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(x)
    return uniq


def _fmt_drop(x: float) -> str:
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return s.replace(".", "p")


def _build_drop_cases(args: argparse.Namespace) -> Tuple[str, List[Tuple[float, float]]]:
    """返回 (mode, [(open_drop, lu_drop), ...])。

    mode: shared | open_fixed | lu_fixed
    """
    has_open_fix = args.open_drop is not None
    has_lu_fix = args.lu_drop is not None
    has_open_list = str(args.open_drops or "").strip() != ""
    has_lu_list = str(args.lu_drops or "").strip() != ""

    if has_open_fix and has_lu_list:
        open_d = float(args.open_drop)
        return "open_fixed", [(open_d, x) for x in _parse_drops(args.lu_drops)]
    if has_lu_fix and has_open_list:
        lu_d = float(args.lu_drop)
        return "lu_fixed", [(x, lu_d) for x in _parse_drops(args.open_drops)]
    if has_open_fix or has_lu_fix or has_open_list or has_lu_list:
        raise SystemExit(
            "分腿扫描请用：--open-drop 1.5 --lu-drops 1,1.5,2  "
            "或 --lu-drop 1.5 --open-drops 1,1.5,2"
        )
    shared = _parse_drops(args.drops)
    return "shared", [(x, x) for x in shared]


def run_one_drop(
    *,
    open_drop: float,
    lu_drop: float,
    sell_code: str,
    sell_params0: Dict[str, Any],
    codes: List[str],
    start_d: date,
    end_d: date,
    icash: float,
    init_pos: Dict[str, Dict[str, Any]],
    scheduled: List[dict],
    first_buys: Dict[str, date],
    engine_hold_n: int,
    sell_hold: int,
    fill_mode: str,
    use_tick: bool,
    clear_ticks: bool,
    progress=None,
    drop_when_lu_gt_open: Optional[float] = None,
    drop_when_lu_lt_open: Optional[float] = None,
    drop_when_lu_eq_open: Optional[float] = None,
) -> Dict[str, Any]:
    _clear_filled_legs_file()
    params = dict(sell_params0)
    params["drop_percent"] = float(open_drop)
    params["open_drop_percent"] = float(open_drop)
    params["lu_drop_percent"] = float(lu_drop)
    # 可选：按「近涨停触发价 vs 开盘腿」选当日两腿共用 drop
    for k in (
        "drop_percent_when_lu_gt_open",
        "drop_percent_when_lu_lt_open",
        "drop_percent_when_lu_eq_open",
    ):
        params.pop(k, None)
    if drop_when_lu_gt_open is not None:
        params["drop_percent_when_lu_gt_open"] = float(drop_when_lu_gt_open)
    if drop_when_lu_lt_open is not None:
        params["drop_percent_when_lu_lt_open"] = float(drop_when_lu_lt_open)
    if drop_when_lu_eq_open is not None:
        params["drop_percent_when_lu_eq_open"] = float(drop_when_lu_eq_open)
    params["scheduled_clear_on_sell_day"] = int(engine_hold_n)
    params["sell_hold_trading_days"] = int(engine_hold_n)
    params["entry_window_trading_days"] = int(engine_hold_n)
    params["sell_hold_from_next_day"] = int(sell_hold)
    params["_filled_legs"] = []

    from strategy_generator_app.backtest.engine import run_backtest
    from strategy_generator_app.backtest import compute_metrics

    result = run_backtest(
        strategy_code=sell_code,
        strategy_params=params,
        stock_codes_6=codes,
        start_date=start_d,
        end_date=end_d,
        initial_cash=float(icash),
        get_stock_name=_get_name,
        use_engine_form=False,
        use_tick_level=bool(use_tick),
        fill_mode=str(fill_mode),
        strategy_generation_time="09:25",
        strategy_run_start_time="09:30",
        strategy_run_end_time="15:00",
        initial_positions=init_pos or None,
        scheduled_buy_fills=scheduled,
        first_buy_date_hints=first_buys,
        progress=progress,
        clear_ticks_on_finish=bool(clear_ticks),
    )
    trades = list(result.get("trades") or [])
    sell_trades = [t for t in trades if str(t.get("side") or "").lower() == "sell"]
    buy_injected = [t for t in trades if str(t.get("side") or "").lower() == "buy"]
    metrics = result.get("metrics")
    if not metrics:
        try:
            metrics = compute_metrics(
                result.get("equity_curve") or [],
                trades,
                initial_cash=float(icash),
            )
        except Exception:
            metrics = {}
    return {
        "open_drop": open_drop,
        "lu_drop": lu_drop,
        "result": result,
        "metrics": metrics or {},
        "trades": trades,
        "sell_trades": sell_trades,
        "buy_injected": buy_injected,
    }


def _summarize_hold(
    buy_path: Path, sell_rows: List[dict], hold_n: int, selection: Optional[Path]
) -> Tuple[List[dict], dict]:
    from tools.summarize_hold_days_from_trades import filter_sells_within_hold, summarize_one

    buy_rows = _read_buy_rows(buy_path)
    filtered, st = filter_sells_within_hold(buy_rows, sell_rows, hold_n)
    rows, meta = summarize_one(buy_path, filtered, hold_n, selection_path=selection)
    meta.update(st)
    return rows, meta


def _case_tag(open_drop: float, lu_drop: float, mode: str) -> str:
    if mode == "shared":
        return f"drop{_fmt_drop(open_drop)}"
    return f"open{_fmt_drop(open_drop)}_lu{_fmt_drop(lu_drop)}"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="固定买入 CSV，扫描弹性卖 drop%%（共用或分腿）"
    )
    ap.add_argument("--buy", default=str(DEFAULT_BUY), help="买入成交明细 CSV")
    ap.add_argument(
        "--strategy",
        default=DEFAULT_STRATEGY,
        help=f"弹性卖策略 id/别名（默认 {DEFAULT_STRATEGY}）",
    )
    ap.add_argument(
        "--drops",
        default="1,1.5,2,2.5,3",
        help="两腿共用 drop%%（默认 1,1.5,2,2.5,3；分腿模式时忽略）",
    )
    ap.add_argument(
        "--open-drop",
        type=float,
        default=None,
        help="开盘涨幅腿固定 drop%%（配合 --lu-drops）",
    )
    ap.add_argument(
        "--lu-drops",
        default="",
        help="近涨停腿扫描列表，如 1,1.5,2,2.5,3（配合 --open-drop）",
    )
    ap.add_argument(
        "--lu-drop",
        type=float,
        default=None,
        help="近涨停腿固定 drop%%（配合 --open-drops）",
    )
    ap.add_argument(
        "--open-drops",
        default="",
        help="开盘涨幅腿扫描列表（配合 --lu-drop）",
    )
    ap.add_argument(
        "--hold",
        type=int,
        default=10,
        help="持有交易日数：买入次日=第1日，第 N 日强清（默认 10）",
    )
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT), help="输出目录")
    ap.add_argument(
        "--fill-mode",
        default="tick",
        choices=("tick", "same_day_ohlc"),
        help="撮合模式（默认 tick；冒烟可用 same_day_ohlc）",
    )
    ap.add_argument(
        "--max-buys",
        type=int,
        default=0,
        help="仅取前 N 笔买入（0=全部；冒烟用）",
    )
    ap.add_argument(
        "--no-summarize",
        action="store_true",
        help="不跑持仓盯市汇总（仅导出卖出明细+引擎 metrics）",
    )
    ap.add_argument(
        "--selection",
        default="",
        help="可选选股文件，汇总时回填字段",
    )
    ap.add_argument("--cash", type=float, default=INITIAL_CASH, help="初始现金基数")
    args = ap.parse_args()

    buy_path = Path(args.buy)
    if not buy_path.is_absolute():
        buy_path = ROOT / buy_path
    if not buy_path.is_file():
        print(f"找不到买入文件: {buy_path}", file=sys.stderr)
        return 1

    mode, cases = _build_drop_cases(args)

    sid = _resolve_sell_id(args.strategy)
    sell_name, sell_code, sell_params0 = _load_strategy(sid)
    if mode != "shared" and "open_drop_percent" not in sell_code:
        print(
            "策略代码未含 open_drop_percent，请先运行 "
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
    if mode == "shared":
        print(f"模式: 两腿共用  drops={[c[0] for c in cases]}  fill_mode={fill_mode}")
    elif mode == "open_fixed":
        print(
            f"模式: 开盘腿固定={cases[0][0]}  近涨停扫描={[c[1] for c in cases]}  "
            f"fill_mode={fill_mode}"
        )
    else:
        print(
            f"模式: 近涨停固定={cases[0][1]}  开盘腿扫描={[c[0] for c in cases]}  "
            f"fill_mode={fill_mode}"
        )
    print(f"输出: {out_dir}")

    overview: List[dict] = []
    do_sum = not bool(args.no_summarize)

    for i, (open_drop, lu_drop) in enumerate(cases):
        ctag = _case_tag(open_drop, lu_drop, mode)
        if mode == "shared":
            tag = f"[{i + 1}/{len(cases)}] drop={open_drop}%"
        else:
            tag = f"[{i + 1}/{len(cases)}] open={open_drop}% lu={lu_drop}%"
        print(f"\n{tag} 开始…", flush=True)
        clear_ticks = i == len(cases) - 1

        def _progress(msg: str, pct: Optional[int] = None) -> None:
            if pct is None:
                print(f"  {msg}", flush=True)
            else:
                print(f"  [{pct:3d}%] {msg}", flush=True)

        try:
            one = run_one_drop(
                open_drop=open_drop,
                lu_drop=lu_drop,
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
            )
        except Exception as ex:
            print(f"{tag} 失败: {ex}", file=sys.stderr)
            overview.append(
                {
                    "open_drop_percent": open_drop,
                    "lu_drop_percent": lu_drop,
                    "drop_percent": open_drop if mode == "shared" else None,
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
        row = {
            "mode": mode,
            "open_drop_percent": open_drop,
            "lu_drop_percent": lu_drop,
            "drop_percent": open_drop if mode == "shared" else None,
            "sell_trades": len(one["sell_trades"]),
            "buy_injected": len(one["buy_injected"]),
            "total_return_pct": m.get("total_return_pct", m.get("总收益率")),
            "final_equity": m.get("final_equity", m.get("期末权益")),
            "sell_csv": str(sell_path.name),
            "trades_csv": str(all_path.name),
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

    ov_name = "overview_legs" if mode != "shared" else "overview_drops"
    ov_path = out_dir / f"{ov_name}_{stamp}.csv"
    _write_csv(ov_path, overview)
    try:
        import pandas as pd

        xlsx_path = out_dir / f"{ov_name}_{stamp}.xlsx"
        pd.DataFrame(overview).to_excel(xlsx_path, index=False)
        print(f"\n对照表: {xlsx_path}")
    except Exception:
        print(f"\n对照表: {ov_path}")

    print("\n=== drop% 对照 ===")
    if mode == "shared":
        hdr = f"{'drop%':>8} {'卖笔':>6} {'盯市总收益%':>12} {'均收益%':>10} {'胜率%':>8}"
        print(hdr)
        for r in overview:
            if r.get("error"):
                print(f"{r.get('open_drop_percent'):>8}  ERROR {r.get('error')}")
                continue
            print(
                f"{r.get('open_drop_percent'):>8} {r.get('sell_trades') or 0:>6} "
                f"{str(r.get('sum_total_ret_pct') if do_sum else r.get('total_return_pct')):>12} "
                f"{str(r.get('sum_mean_ret_pct') or '-'):>10} "
                f"{str(r.get('sum_win_rate') or '-'):>8}"
            )
    else:
        hdr = (
            f"{'open%':>8} {'lu%':>8} {'卖笔':>6} "
            f"{'盯市总收益%':>12} {'均收益%':>10} {'胜率%':>8}"
        )
        print(hdr)
        for r in overview:
            if r.get("error"):
                print(
                    f"{r.get('open_drop_percent'):>8} {r.get('lu_drop_percent'):>8}  "
                    f"ERROR {r.get('error')}"
                )
                continue
            print(
                f"{r.get('open_drop_percent'):>8} {r.get('lu_drop_percent'):>8} "
                f"{r.get('sell_trades') or 0:>6} "
                f"{str(r.get('sum_total_ret_pct') if do_sum else r.get('total_return_pct')):>12} "
                f"{str(r.get('sum_mean_ret_pct') or '-'):>10} "
                f"{str(r.get('sum_win_rate') or '-'):>8}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
