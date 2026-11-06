# -*- coding: utf-8 -*-
"""盘中买入冲高卖出 · 日线简化回测（无 tick / 无 QMT 探针）。

规则（日线近似，对齐 strategy_33aa1c88）：
- 每笔买入各自开窗：买入次日起持有 N 个卖出交易日（缺省 2）；同代码多日/多笔不合并
- 三档：昨收 +4%/+6%/+8%，当日 high >= 触价则按触价成交约 1/3（leg SURGE4/6/8，跨日等待，按笔独立）
- 该笔持有末日若仍有剩余仓：按当日 close 清仓
- 数据：utils.daily_cache / data_provider.get_daily_ohlc_for_codes；买入：buy_chain_from_csv
"""
from __future__ import annotations

import csv
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

STRATEGY_IDS_INTRADAY_SURGE_SELL = {"strategy_33aa1c88"}
SURGE_GAINS = (("SURGE4", 0.04, "盘中冲高卖出-涨幅4%三分之一"),
               ("SURGE6", 0.06, "盘中冲高卖出-涨幅6%三分之一"),
               ("SURGE8", 0.08, "盘中冲高卖出-涨幅8%三分之一"))
DEFAULT_HOLD_UI = 2  # 买入次日=第1日 … 第2日清仓
SOURCE_LABEL = "日线回测-盘中冲高卖出"


def is_intraday_surge_sell_strategy(strategy_id: str = "", name: str = "") -> bool:
    sid = str(strategy_id or "").strip()
    if sid in STRATEGY_IDS_INTRADAY_SURGE_SELL:
        return True
    n = str(name or "")
    return ("盘中买入冲高卖出" in n) and (("卖" in n) or n.startswith("卖"))


def repo_root_from_here() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def history_sell_dir(root: Optional[str] = None) -> str:
    root = root or repo_root_from_here()
    d = os.path.join(root, "history_data", "马总盘中策略")
    os.makedirs(d, exist_ok=True)
    return d


def _code6(raw: Any) -> str:
    s = str(raw or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    digits = "".join(c for c in s if c.isdigit())
    return digits.zfill(6)[-6:] if digits else ""


def _parse_d(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


def _full_lot(avail: int) -> int:
    avail = int(avail or 0)
    if avail < 100:
        return 0
    return (avail // 100) * 100


def _thirds_absorb_dust(total: int) -> Tuple[int, int, int]:
    total = _full_lot(total)
    if total < 100:
        return 0, 0, 0
    lots = total // 100
    a = (lots // 3) * 100
    b = (lots // 3) * 100
    c = total - a - b
    return a, b, c


def _round_px(px: float, prec: int = 2) -> float:
    try:
        return round(float(px), prec)
    except Exception:
        return 0.0


def _ensure_path():
    root = repo_root_from_here()
    import sys
    if root not in sys.path:
        sys.path.insert(0, root)


def _next_td(d: date) -> Optional[date]:
    _ensure_path()
    try:
        from strategy_generator_app.trading_calendar import next_trading_day_after
    except ImportError:
        from trading_calendar import next_trading_day_after  # type: ignore
    return next_trading_day_after(d)


def _window_from(start: date, hold_n: int) -> Tuple[Optional[date], Optional[date], List[date], str]:
    _ensure_path()
    try:
        from strategy_generator_app.trading_calendar import (
            get_trading_dates_in_range_sorted,
            trading_day_window_from_start,
        )
    except ImportError:
        from trading_calendar import (  # type: ignore
            get_trading_dates_in_range_sorted,
            trading_day_window_from_start,
        )
    s, e, note = trading_day_window_from_start(start, hold_n)
    if s is None or e is None:
        return None, None, [], note or "无窗口"
    lst = get_trading_dates_in_range_sorted(s, e)
    return s, e, lst, note


def _ohlc(code: str, day: date) -> Optional[Dict[str, float]]:
    _ensure_path()
    try:
        from strategy_generator_app.backtest.data_provider import get_daily_ohlc_for_codes
    except ImportError:
        from backtest.data_provider import get_daily_ohlc_for_codes  # type: ignore
    m = get_daily_ohlc_for_codes([code], day)
    return m.get(_code6(code))


def _prev_close(code: str, day: date) -> float:
    """昨收 = 上一交易日收盘（fill_close 优先，否则 close）。"""
    prev = None
    # walk back up to 10 calendar days for prior trading bar
    for i in range(1, 12):
        d = day - timedelta(days=i)
        bar = _ohlc(code, d)
        if bar:
            prev = bar
            break
    if not prev:
        return 0.0
    try:
        return float(prev.get("fill_close") or prev.get("close") or 0)
    except Exception:
        return 0.0


def _fill_vol(f: dict) -> int:
    for k in ("volume", "数量", "vol"):
        if f.get(k) is not None and str(f.get(k)).strip() != "":
            try:
                return int(float(f.get(k)))
            except (TypeError, ValueError):
                continue
    return 0


def _iter_buy_lots(fills: Sequence[dict]) -> List[Dict[str, Any]]:
    """每笔买入一条 lot（不按代码合并）。

    字段：code, buy_date, volume, name, selection_date
    """
    lots: List[Dict[str, Any]] = []
    for f in fills or []:
        c6 = _code6(f.get("code") or f.get("stock_code") or f.get("代码"))
        bd = _parse_d(f.get("date") or f.get("trade_date") or f.get("日期"))
        vol = _fill_vol(f)
        if not c6 or bd is None or vol <= 0:
            continue
        # 仅处理买入方向（若带方向字段）
        side = str(f.get("side") or f.get("方向") or "buy").strip().lower()
        if side in ("sell", "卖出", "s"):
            continue
        sel = (
            _parse_d(f.get("selection_date") or f.get("选股日"))
            or bd
        )
        lots.append(
            {
                "code": c6,
                "buy_date": bd,
                "volume": vol,
                "name": str(f.get("name") or f.get("stock_name") or f.get("股票名称") or ""),
                "selection_date": sel,
            }
        )
    # 稳定顺序：买日、代码
    lots.sort(key=lambda x: (x["buy_date"], x["code"]))
    return lots


def _lot_sellable(buy_date: date, volume: int, day: date) -> int:
    """单笔 T+1：该笔买入日的下一交易日起可卖本笔数量。"""
    ns = _next_td(buy_date)
    if ns is None or day < ns:
        return 0
    return _full_lot(int(volume))


def run_surge_daily_sell_backtest(
    fills: Sequence[dict],
    *,
    hold_ui_days: int = DEFAULT_HOLD_UI,
    surge_gains: Optional[Tuple[float, float, float]] = None,
    root: Optional[str] = None,
    progress=None,
) -> Tuple[List[dict], Dict[str, Any]]:
    """执行日线冲高卖出（每笔买入各自开窗，互不合并）。

    返回 (trade_rows, summary)。
    trade_rows 字段对齐卖出明细 CSV。
    """
    root = root or repo_root_from_here()
    hold_n = max(1, int(hold_ui_days or DEFAULT_HOLD_UI))
    if surge_gains and len(surge_gains) == 3:
        gains = [
            ("SURGE4", float(surge_gains[0]), SURGE_GAINS[0][2]),
            ("SURGE6", float(surge_gains[1]), SURGE_GAINS[1][2]),
            ("SURGE8", float(surge_gains[2]), SURGE_GAINS[2][2]),
        ]
    else:
        gains = [(a, b, c) for a, b, c in SURGE_GAINS]

    lots = _iter_buy_lots(fills)
    trades: List[dict] = []
    skipped = []
    n = len(lots)
    codes_seen = set()
    for i, lot in enumerate(lots):
        c6 = lot["code"]
        buy_d = lot["buy_date"]
        codes_seen.add(c6)
        if progress:
            try:
                progress(
                    "冲高日线 %s@%s (%d/%d)" % (c6, buy_d, i + 1, n),
                    int(100 * i / max(1, n)),
                )
            except Exception:
                pass
        start = _next_td(buy_d)
        if start is None:
            skipped.append(("%s@%s" % (c6, buy_d), "无下一交易日"))
            continue
        s, e, days, note = _window_from(start, hold_n)
        if not days:
            skipped.append(("%s@%s" % (c6, buy_d), note or "无卖出窗口"))
            continue
        base = _full_lot(int(lot["volume"]))
        if base < 100:
            skipped.append(("%s@%s" % (c6, buy_d), "仓位不足一手"))
            continue
        v4, v6, v8 = _thirds_absorb_dust(base)
        pending = {"SURGE4": v4, "SURGE6": v6, "SURGE8": v8}
        remain = base
        sel = lot.get("selection_date") or buy_d
        sel_s = sel.isoformat() if isinstance(sel, date) else str(sel or "")
        name = lot.get("name") or ""
        buy_tag = buy_d.strftime("%Y%m%d")

        for di, day in enumerate(days):
            is_last = di == len(days) - 1
            bar = _ohlc(c6, day)
            if not bar:
                if is_last and remain >= 100:
                    skipped.append(("%s@%s" % (c6, buy_d), "末日缺日线 %s" % day))
                continue
            try:
                high = float(bar.get("high") or 0)
                close = float(bar.get("fill_close") or bar.get("close") or 0)
            except (TypeError, ValueError):
                high, close = 0.0, 0.0
            prev = _prev_close(c6, day)
            if prev <= 0:
                try:
                    prev = float(bar.get("open") or 0)
                except Exception:
                    prev = 0.0
            sellable = min(remain, _lot_sellable(buy_d, base, day))
            if sellable < 100:
                continue

            for key, gain, rule_name in gains:
                tgt = int(pending.get(key) or 0)
                if tgt < 100 or remain < 100:
                    continue
                trig = _round_px(prev * (1.0 + float(gain)), 2)
                if trig <= 0 or high + 1e-9 < trig:
                    continue
                vol = _full_lot(min(tgt, sellable, remain))
                if vol < 100:
                    continue
                pending_left = sum(int(pending[k]) for k, _, _ in gains if k != key)
                if pending_left < 100 and remain - vol < 100 and remain - vol > 0:
                    vol = _full_lot(remain)
                px = float(trig)
                amt = round(px * vol, 2)
                trades.append({
                    "日期": day.isoformat(),
                    "时间": "15:00:00",
                    "代码": c6,
                    "选股日": sel_s,
                    "方向": "卖出",
                    "价格": px,
                    "数量": vol,
                    "金额": amt,
                    "规则名": rule_name,
                    "腿键": "%s:%s:%s" % (c6, buy_tag, key),
                    "触发信息": "买日=%s 昨收=%.4f 触价=%.4f high=%.4f" % (buy_d, prev, trig, high),
                    "来源": SOURCE_LABEL,
                    "股票名称": name,
                })
                pending[key] = max(0, tgt - vol)
                remain -= vol
                sellable -= vol
                if remain < 100 or sellable < 100:
                    break

            if is_last and remain >= 100:
                vol = _full_lot(min(remain, _lot_sellable(buy_d, base, day)))
                if vol >= 100 and close > 0:
                    px = _round_px(close, 2)
                    trades.append({
                        "日期": day.isoformat(),
                        "时间": "15:00:00",
                        "代码": c6,
                        "选股日": sel_s,
                        "方向": "卖出",
                        "价格": px,
                        "数量": vol,
                        "金额": round(px * vol, 2),
                        "规则名": "盘中冲高卖出-第%d日收盘清仓" % hold_n,
                        "腿键": "%s:%s:SCLEAR:%s" % (c6, buy_tag, day.strftime("%Y%m%d")),
                        "触发信息": "买日=%s 收盘清仓 close=%.4f remain=%d" % (buy_d, close, remain),
                        "来源": SOURCE_LABEL,
                        "股票名称": name,
                    })
                    remain -= vol
                elif vol >= 100 and close <= 0:
                    skipped.append(("%s@%s" % (c6, buy_d), "末日无收盘价 %s" % day))

    summary = {
        "n_codes": len(codes_seen),
        "n_lots": n,
        "n_trades": len(trades),
        "n_surge": sum(1 for t in trades if "涨幅" in str(t.get("规则名") or "")),
        "n_clear": sum(1 for t in trades if "收盘清仓" in str(t.get("规则名") or "")),
        "hold_ui_days": hold_n,
        "skipped": skipped[:30],
        "skipped_n": len(skipped),
        "per_lot_window": True,
    }
    return trades, summary


def write_sell_trades_csv(
    trades: Sequence[dict],
    *,
    root: Optional[str] = None,
    out_path: Optional[str] = None,
) -> str:
    root = root or repo_root_from_here()
    if not out_path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(
            history_sell_dir(root),
            "回测成交明细_卖出_盘中冲高卖出_%s.csv" % stamp,
        )
    fields = ["日期", "时间", "代码", "选股日", "方向", "价格", "数量", "金额", "规则名", "腿键", "触发信息", "来源"]
    # optional 股票名称 not in nextday header — keep aligned
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for t in trades:
            w.writerow({k: t.get(k, "") for k in fields})
    return out_path


def run_from_buy_csv(
    buy_csv_path: str,
    *,
    hold_ui_days: int = DEFAULT_HOLD_UI,
    root: Optional[str] = None,
    progress=None,
) -> Tuple[str, List[dict], Dict[str, Any]]:
    _ensure_path()
    try:
        from strategy_generator_app.backtest.buy_chain_from_csv import load_buy_fills_from_trades_csv
    except ImportError:
        from backtest.buy_chain_from_csv import load_buy_fills_from_trades_csv  # type: ignore
    fills, note = load_buy_fills_from_trades_csv(buy_csv_path)
    trades, summary = run_surge_daily_sell_backtest(
        fills, hold_ui_days=hold_ui_days, root=root, progress=progress
    )
    summary["buy_parse_note"] = note
    summary["buy_csv"] = buy_csv_path
    out = write_sell_trades_csv(trades, root=root)
    summary["out_csv"] = out
    return out, trades, summary


def run_from_chain_segment(
    seg: dict,
    *,
    hold_ui_days: int = DEFAULT_HOLD_UI,
    root: Optional[str] = None,
    progress=None,
) -> Tuple[str, List[dict], Dict[str, Any]]:
    fills = list(seg.get("buy_fills") or []) if isinstance(seg, dict) else []
    if not fills and isinstance(seg, dict) and seg.get("source_buy_csv"):
        return run_from_buy_csv(
            str(seg.get("source_buy_csv")),
            hold_ui_days=hold_ui_days,
            root=root,
            progress=progress,
        )
    trades, summary = run_surge_daily_sell_backtest(
        fills, hold_ui_days=hold_ui_days, root=root, progress=progress
    )
    summary["source_buy_csv"] = (seg or {}).get("source_buy_csv")
    out = write_sell_trades_csv(trades, root=root)
    summary["out_csv"] = out
    return out, trades, summary
