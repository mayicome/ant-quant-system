# -*- coding: utf-8 -*-
"""用东财日 K 对账并修补 data/daily_cache 近端残缺 bar。

场景：盘中/未定稿 K 被写入后，次日增量只补新日期，历史错 bar 永久冻结
（如 600770 的 2026-09-07 收盘 5.23 vs 真值 5.59）。

用法：
  python tools/repair_daily_cache_tail_vs_em.py --dry-run
  python tools/repair_daily_cache_tail_vs_em.py
  python tools/repair_daily_cache_tail_vs_em.py --days 10 --workers 16
  python tools/repair_daily_cache_tail_vs_em.py --codes 600770.SH,600737.SH
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fetch_daily_full_from_em import (  # noqa: E402
    DAILY_CACHE,
    OUT_COLS,
    _session,
    fetch_hist_em,
)

CLOSE_EPS = 0.02
CLOSE_PCT = 0.003
VOL_RATIO = 0.97
AMT_RATIO = 0.97
HIGH_EPS = 0.02


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _bar_mismatch(local: Dict[str, Any], em: Dict[str, Any]) -> Optional[str]:
    c_l, c_e = _f(local.get("close")), _f(em.get("close"))
    if c_e <= 0 or c_l <= 0:
        return "bad_close"
    tol = max(CLOSE_EPS, abs(c_e) * CLOSE_PCT)
    if abs(c_l - c_e) > tol:
        return "close"
    v_l, v_e = _f(local.get("volume")), _f(em.get("volume"))
    if v_e > 0 and v_l < v_e * VOL_RATIO:
        return "volume"
    a_l, a_e = _f(local.get("amount")), _f(em.get("amount"))
    if a_e > 0 and a_l > 0 and a_l < a_e * AMT_RATIO:
        return "amount"
    h_l, h_e = _f(local.get("high")), _f(em.get("high"))
    if h_e > 0 and h_l > 0 and h_l + HIGH_EPS < h_e:
        return "high"
    o_l, o_e = _f(local.get("open")), _f(em.get("open"))
    if o_e > 0 and abs(o_l - o_e) > tol:
        return "open"
    l_l, l_e = _f(local.get("low")), _f(em.get("low"))
    if l_e > 0 and abs(l_l - l_e) > tol:
        return "low"
    return None


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(OUT_COLS))
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in OUT_COLS})
    for attempt in range(8):
        try:
            tmp.replace(path)
            return
        except OSError:
            if attempt >= 7:
                raise
            time.sleep(0.05 * (attempt + 1))


def _repair_one(
    code: str,
    *,
    start: str,
    end: str,
    dry_run: bool,
    session,
) -> Tuple[str, int, List[str]]:
    path = DAILY_CACHE / f"{code}.csv"
    if not path.is_file():
        return code, 0, []
    try:
        rows = _read_csv(path)
    except Exception as e:
        return code, 0, ["read_err:%s" % e]
    if not rows:
        return code, 0, []
    by_d = {str(r.get("date") or "")[:10]: r for r in rows}
    try:
        em = fetch_hist_em(
            code, start=start, end=end, adjust="none", session=session, timeout=25.0
        )
    except Exception as e:
        return code, 0, ["em_err:%s" % type(e).__name__]
    if em is None or em.empty:
        return code, 0, ["em_empty"]

    patched = 0
    notes: List[str] = []
    for _, er in em.iterrows():
        d = er["date"]
        if hasattr(d, "strftime"):
            ds = d.strftime("%Y-%m-%d")
        else:
            ds = str(d)[:10]
        local = by_d.get(ds)
        if not local:
            continue
        em_bar = {
            "date": ds,
            "open": float(er["open"]),
            "high": float(er["high"]),
            "low": float(er["low"]),
            "close": float(er["close"]),
            "volume": float(er["volume"]),
            "amount": float(er["amount"]) if er.get("amount") == er.get("amount") else "",
        }
        reason = _bar_mismatch(local, em_bar)
        if not reason:
            continue
        notes.append(
            "%s %s L(c=%.4f v=%.0f) EM(c=%.4f v=%.0f)"
            % (
                ds,
                reason,
                _f(local.get("close")),
                _f(local.get("volume")),
                em_bar["close"],
                em_bar["volume"],
            )
        )
        if not dry_run:
            local["open"] = em_bar["open"]
            local["high"] = em_bar["high"]
            local["low"] = em_bar["low"]
            local["close"] = em_bar["close"]
            local["volume"] = em_bar["volume"]
            if em_bar["amount"] != "":
                local["amount"] = em_bar["amount"]
        patched += 1

    if patched and not dry_run:
        # keep original row order
        out_rows = []
        for r in rows:
            d = str(r.get("date") or "")[:10]
            out_rows.append(by_d.get(d) or r)
        _write_csv(path, out_rows)
    return code, patched, notes


def main() -> int:
    ap = argparse.ArgumentParser(description="Repair daily_cache tail bars vs Eastmoney")
    ap.add_argument("--days", type=int, default=10, help="lookback calendar days")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--codes", type=str, default="", help="comma codes; default=all csv")
    ap.add_argument(
        "--end",
        type=str,
        default="",
        help="YYYY-MM-DD or YYYYMMDD; default=today",
    )
    args = ap.parse_args()

    if not DAILY_CACHE.is_dir():
        print("missing", DAILY_CACHE)
        return 1

    if args.end:
        end_d = datetime.strptime(args.end.replace("-", "")[:8], "%Y%m%d").date()
    else:
        end_d = date.today()
    start_d = end_d - timedelta(days=max(1, int(args.days)))
    start_s = start_d.strftime("%Y%m%d")
    end_s = end_d.strftime("%Y%m%d")

    if args.codes.strip():
        codes = [
            c.strip().upper()
            for c in args.codes.replace("，", ",").split(",")
            if c.strip()
        ]
    else:
        codes = sorted(p.stem.upper() for p in DAILY_CACHE.glob("*.csv") if "." in p.stem)

    print(
        "repair daily_cache vs EM  start=%s end=%s codes=%d dry_run=%s workers=%d"
        % (start_s, end_s, len(codes), args.dry_run, args.workers)
    )

    sess = _session()
    fixed_codes = 0
    fixed_bars = 0
    em_err = 0
    samples: List[str] = []
    t0 = time.time()

    def _job(code: str):
        # each worker gets its own session to avoid sharing
        return _repair_one(
            code, start=start_s, end=end_s, dry_run=args.dry_run, session=_session()
        )

    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as ex:
        futs = {ex.submit(_job, c): c for c in codes}
        done = 0
        for fut in as_completed(futs):
            done += 1
            code, n, notes = fut.result()
            if notes and notes[0].startswith(("em_err", "em_empty", "read_err")):
                em_err += 1
            if n > 0:
                fixed_codes += 1
                fixed_bars += n
                if len(samples) < 40:
                    samples.append("%s | %s" % (code, " ; ".join(notes[:3])))
            if done % 200 == 0 or done == len(codes):
                print(
                    "progress %d/%d fixed_codes=%d fixed_bars=%d em_err=%d elapsed=%.0fs"
                    % (done, len(codes), fixed_codes, fixed_bars, em_err, time.time() - t0)
                )

    print(
        "DONE fixed_codes=%d fixed_bars=%d em_err=%d dry_run=%s elapsed=%.1fs"
        % (fixed_codes, fixed_bars, em_err, args.dry_run, time.time() - t0)
    )
    for s in samples:
        print(" ", s)
    if len(samples) < fixed_codes:
        print("  ... +%d more codes" % (fixed_codes - len(samples)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
