# -*- coding: utf-8 -*-
"""7月：持有1-4日日线清仓拟合 × 满足条件 True/False。"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategy_generator_app.trading_calendar import (
    backtest_window_from_selection_day,
    next_trading_day_after,
    trading_day_window_from_start,
)
from utils.daily_cache_reader import load_daily_from_cache

BATCH = (
    ROOT
    / "history_data"
    / "马总选股逻辑"
    / "batch_backtest_export_买：马总逻辑1-涨停后跌破MA5_10_20各1_3_20260811_063803.json"
)
SUMMARY = ROOT / "history_data" / "马总选股逻辑" / "各日选股收益汇总-7月.xlsx"
OUT = ROOT / "history_data" / "马总选股逻辑" / "hold_1_4_meet_true_false_7月.json"
FROM_T1 = True
HOLDS = (1, 2, 3, 4)

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


def _code6(k) -> str:
    s = str(k or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def chained_window(seg: dict, hold_n: int) -> Tuple[Optional[date], Optional[date]]:
    sel = _parse_d(seg.get("batch_selection_date"))
    br = seg.get("backtest_range") or {}
    prior_start = _parse_d(br.get("start"))
    prior_end = _parse_d(br.get("end")) or _parse_d(br.get("last_equity_date"))
    if not sel or prior_start is None or prior_end is None:
        return None, None
    s_req, e_req, msg = backtest_window_from_selection_day(
        sel, start_next_trading_day=FROM_T1, hold_trading_days=hold_n
    )
    if s_req is None:
        return None, None
    if s_req <= prior_end:
        n_after = next_trading_day_after(prior_start)
        if n_after is None:
            return None, None
        ns = s_req if s_req > n_after else n_after
        s_eff, e_eff, w2 = trading_day_window_from_start(ns, hold_n)
        return s_eff, e_eff
    return s_req, e_req


def series(code: str) -> Optional[pd.DataFrame]:
    if code in _CACHE:
        return _CACHE[code]
    df = load_daily_from_cache(code)
    if df is None or getattr(df, "empty", True):
        _CACHE[code] = None
        return None
    dd = df[["date", "close"]].copy()
    dd["date"] = dd["date"].map(_parse_d)
    dd["close"] = pd.to_numeric(dd["close"], errors="coerce")
    dd = dd.dropna(subset=["date", "close"]).sort_values("date")
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


def summarize(rets: List[float], buys: List[float], pnls: List[float]) -> dict:
    n = len(rets)
    if n == 0:
        return {"n": 0, "ret_mean": None, "wr": None, "pnl_wan": 0.0, "ret_on_buy": None}
    buy = sum(buys)
    pnl = sum(pnls)
    n_win = sum(1 for x in rets if x > 0)
    return {
        "n": n,
        "ret_mean": round(sum(rets) / n, 2),
        "ret_med": round(float(pd.Series(rets).median()), 2),
        "wr": round(n_win / n * 100, 1),
        "n_win": n_win,
        "n_lose": n - n_win,
        "pnl_wan": round(pnl / 1e4, 2),
        "ret_on_buy": round(pnl / buy * 100, 2) if buy else None,
    }


def main() -> None:
    print("load meet map", flush=True)
    sm = pd.read_excel(SUMMARY)
    sm["选股日"] = pd.to_datetime(sm["选股日"]).dt.strftime("%Y-%m-%d")
    sm["代码6"] = sm["代码"].map(_code6)
    meet_map: Dict[Tuple[str, str], bool] = {}
    for _, r in sm.iterrows():
        meet_map[(str(r["选股日"]), str(r["代码6"]))] = bool(r["满足条件"])

    print("load batch", flush=True)
    batch = json.loads(BATCH.read_text(encoding="utf-8"))
    segs = batch.get("segments") or []
    codes = set()
    for seg in segs:
        for k in (seg.get("positions") or {}):
            codes.add(_code6(k))
    print("preload", len(codes), flush=True)
    for i, c in enumerate(sorted(codes)):
        series(c)
        if (i + 1) % 100 == 0:
            print(" ", i + 1, flush=True)

    out = {
        "method": "daily_close_mtm × 满足条件",
        "batch": BATCH.name,
        "summary": SUMMARY.name,
        "holds": {},
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }

    for hold_n in HOLDS:
        buckets = {
            "True": {"rets": [], "buys": [], "pnls": []},
            "False": {"rets": [], "buys": [], "pnls": []},
            "unknown": {"rets": [], "buys": [], "pnls": []},
        }
        for seg in segs:
            sel = str(seg.get("batch_selection_date") or "")
            start_d, end_d = chained_window(seg, hold_n)
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
                px = close_on(c, end_d)
                if px is None or px <= 0:
                    continue
                buy = vol * cost
                mtm = vol * px
                pnl = mtm - buy
                ret = pnl / buy * 100.0
                key = meet_map.get((sel, c))
                tag = "True" if key is True else ("False" if key is False else "unknown")
                buckets[tag]["rets"].append(ret)
                buckets[tag]["buys"].append(buy)
                buckets[tag]["pnls"].append(pnl)

        hold_out = {}
        for tag in ("True", "False", "unknown"):
            b = buckets[tag]
            hold_out[tag] = summarize(b["rets"], b["buys"], b["pnls"])
        # all
        all_rets = buckets["True"]["rets"] + buckets["False"]["rets"] + buckets["unknown"]["rets"]
        all_buys = buckets["True"]["buys"] + buckets["False"]["buys"] + buckets["unknown"]["buys"]
        all_pnls = buckets["True"]["pnls"] + buckets["False"]["pnls"] + buckets["unknown"]["pnls"]
        hold_out["all"] = summarize(all_rets, all_buys, all_pnls)
        out["holds"][str(hold_n)] = hold_out
        print(
            f"hold={hold_n}",
            "True",
            hold_out["True"],
            "False",
            hold_out["False"],
            flush=True,
        )

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
