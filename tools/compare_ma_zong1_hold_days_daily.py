# -*- coding: utf-8 -*-
"""用日线收盘拟合：持有 1/2/3 个交易日后清仓收益（不含盘中卖规则）。"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

print("boot", flush=True)

from strategy_generator_app.trading_calendar import (
    backtest_window_from_selection_day,
    next_trading_day_after,
    trading_day_window_from_start,
)
from utils.daily_cache_reader import load_daily_from_cache

DEFAULT_BATCH = (
    ROOT
    / "history_data"
    / "马总选股逻辑"
    / "batch_backtest_export_买：马总逻辑1-涨停后跌破MA5_10_20各1_3_20260810_190142.json"
)
FROM_T1 = True
HOLDS = (1, 2, 3)

_CACHE: Dict[str, Optional[pd.DataFrame]] = {}


def _parse_d(v) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v).strip()[:10])
    except Exception:
        return None


def _code6(k: str) -> str:
    s = str(k or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    return s.zfill(6) if s.isdigit() else s


def chained_window(seg: dict, hold_n: int) -> Tuple[Optional[date], Optional[date], str]:
    sel = _parse_d(seg.get("batch_selection_date"))
    br = seg.get("backtest_range") or {}
    prior_start = _parse_d(br.get("start"))
    prior_end = _parse_d(br.get("end")) or _parse_d(br.get("last_equity_date"))
    if not sel or prior_start is None or prior_end is None:
        return None, None, "缺区间"
    s_req, e_req, msg = backtest_window_from_selection_day(
        sel, start_next_trading_day=FROM_T1, hold_trading_days=hold_n
    )
    if s_req is None:
        return None, None, msg or "窗口失败"
    if s_req <= prior_end:
        n_after = next_trading_day_after(prior_start)
        if n_after is None:
            return None, None, "无下一交易日"
        ns = s_req if s_req > n_after else n_after
        s_eff, e_eff, w2 = trading_day_window_from_start(ns, hold_n)
        if s_eff is None:
            return None, None, w2 or "不足"
        return s_eff, e_eff, "重叠顺延"
    return s_req, e_req, ""


def series(code: str) -> Optional[pd.DataFrame]:
    if code in _CACHE:
        return _CACHE[code]
    df = load_daily_from_cache(code)
    if df is None or getattr(df, "empty", True):
        _CACHE[code] = None
        return None
    dd = df[["date", "close"]].copy()
    dd["date"] = dd["date"].map(_parse_d)
    dd = dd.dropna(subset=["date"]).sort_values("date")
    dd["close"] = pd.to_numeric(dd["close"], errors="coerce")
    dd = dd.dropna(subset=["close"])
    _CACHE[code] = dd
    return dd


def close_on(code: str, d: date) -> Optional[float]:
    dd = series(code)
    if dd is None or dd.empty:
        return None
    row = dd[dd["date"] == d]
    if row.empty:
        row = dd[dd["date"] <= d].tail(1)
    if row.empty:
        return None
    return float(row.iloc[-1]["close"])


def summarize(rows: List[dict]) -> dict:
    if not rows:
        return {"n": 0}
    rets = [r["ret_pct"] for r in rows]
    buy = sum(r["buy"] for r in rows)
    mtm = sum(r["mtm"] for r in rows)
    pnl = sum(r["pnl"] for r in rows)
    n = len(rows)
    n_win = sum(1 for x in rets if x > 0)
    return {
        "n": n,
        "buy_wan": round(buy / 1e4, 2),
        "mtm_wan": round(mtm / 1e4, 2),
        "pnl_wan": round(pnl / 1e4, 2),
        "ret_mean": round(sum(rets) / n, 2),
        "ret_med": round(float(pd.Series(rets).median()), 2),
        "wr": round(n_win / n * 100, 1),
        "n_win": n_win,
        "n_lose": n - n_win,
        "ret_on_buy": round(pnl / buy * 100, 2) if buy else 0.0,
    }


def main() -> None:
    batch_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BATCH
    if len(sys.argv) > 2:
        out_path = Path(sys.argv[2])
    else:
        stem = "hold_compare_daily_mtm_1_2_3"
        if "063803" in batch_path.name or "7月" in batch_path.name:
            stem = "hold_compare_daily_mtm_1_2_3_7月"
        out_path = ROOT / "history_data" / "马总选股逻辑" / f"{stem}.json"

    print("load batch", batch_path.name, flush=True)
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    segs = batch.get("segments") or []
    # preload codes
    codes = set()
    for seg in segs:
        for k in (seg.get("positions") or {}):
            codes.add(_code6(k))
    print("preload daily", len(codes), flush=True)
    for i, c in enumerate(sorted(codes)):
        series(c)
        if (i + 1) % 50 == 0:
            print("  loaded", i + 1, flush=True)

    out: Dict[str, Any] = {
        "method": "daily_close_mtm_clear_at_hold_end",
        "note": "接续窗口末日收盘清仓拟合；不含盘中弹性卖/涨停卖/1455",
        "batch": batch_path.name,
        "from_t1": FROM_T1,
        "holds": {},
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }

    for hold_n in HOLDS:
        rows: List[dict] = []
        by_sel: Dict[str, List[dict]] = {}
        for seg in segs:
            sel = str(seg.get("batch_selection_date") or "")
            start_d, end_d, note = chained_window(seg, hold_n)
            if start_d is None or end_d is None:
                continue
            for k, p in (seg.get("positions") or {}).items():
                c = _code6(k)
                try:
                    vol = int((p or {}).get("volume") or 0)
                    cost = float((p or {}).get("cost") or 0)
                except Exception:
                    continue
                if vol < 100 or cost <= 0:
                    continue
                buy = vol * cost
                px = close_on(c, end_d)
                miss = px is None or px <= 0
                if miss:
                    continue
                mtm = vol * float(px)
                pnl = mtm - buy
                ret = pnl / buy * 100.0
                r = {
                    "selection_date": sel,
                    "code": c,
                    "buy": buy,
                    "end": str(end_d),
                    "close": float(px),
                    "mtm": mtm,
                    "pnl": pnl,
                    "ret_pct": ret,
                }
                rows.append(r)
                by_sel.setdefault(sel, []).append(r)
        summary = summarize(rows)
        by_day = {k: summarize(v) for k, v in sorted(by_sel.items())}
        out["holds"][str(hold_n)] = {"summary": summary, "by_day": by_day}
        print(f"hold={hold_n}", summary, flush=True)

    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", out_path, flush=True)


if __name__ == "__main__":
    main()
