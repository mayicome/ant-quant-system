# -*- coding: utf-8 -*-
"""用公开日线对照 data/daily_cache，结果写到 data/daily_cache_ext。

不改正在使用的 daily_cache 和它的 manifest。盘中当天 K 线未收完，不计入对账，
影子 manifest 的 sync_trade_date 用双方都有的最后一个已完成交易日。

用法：
  python tools/reconcile_external_daily.py
  python tools/reconcile_external_daily.py --codes 000001.SZ,600000.SH
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.daily_cache_reader import load_manifest, to_full_stock_code  # noqa: E402
from utils.external_daily_bars import OUT_COLS, fetch_daily_bars, fetch_tencent, fetch_ths  # noqa: E402

CACHE_DIR = ROOT / "data" / "daily_cache"
SHADOW_DIR = ROOT / "data" / "daily_cache_ext"
DEFAULT_CODES = (
    "000001.SZ",
    "600000.SH",
    "600519.SH",
    "000858.SZ",
    "300364.SZ",
    "002490.SZ",
    "688981.SH",
)

CLOSE_ABS = 0.015
VOLUME_ABS = 1.5
AMOUNT_REL = 0.002


def _read_cache(full_code: str) -> Dict[str, Dict[str, str]]:
    path = CACHE_DIR / f"{full_code}.csv"
    if not path.is_file():
        return {}
    out: Dict[str, Dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            d = str(row.get("date") or "")[:10]
            if d:
                out[d] = row
    return out


def _num(row: Optional[Dict[str, Any]], key: str) -> Optional[float]:
    if not row:
        return None
    raw = row.get(key)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _fmt(value: Optional[float]) -> str:
    if value is None:
        return ""
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return ("%.4f" % value).rstrip("0").rstrip(".")


def _write_shadow(full_code: str, bars: List[Dict[str, Any]]) -> None:
    SHADOW_DIR.mkdir(parents=True, exist_ok=True)
    path = SHADOW_DIR / f"{full_code}.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(OUT_COLS))
        writer.writeheader()
        for bar in bars:
            writer.writerow({col: _fmt(bar.get(col)) if col != "date" else bar["date"] for col in OUT_COLS})


def _compare_one(
    full_code: str,
    bars: List[Dict[str, Any]],
    cache: Dict[str, Dict[str, str]],
    through: date,
) -> Dict[str, Any]:
    ext = {b["date"]: b for b in bars}
    dates = sorted(set(ext) & set(cache))
    scored = [d for d in dates if d <= through.isoformat()]
    today_s = date.today().isoformat()
    max_close = 0.0
    max_vol = 0.0
    max_amt_rel = 0.0
    amt_checked = 0
    bad: List[str] = []
    for d in scored:
        e = ext[d]
        c = cache[d]
        close_diff = abs(float(e["close"]) - float(c["close"]))
        vol_diff = abs(float(e["volume"]) - float(c["volume"]))
        max_close = max(max_close, close_diff)
        max_vol = max(max_vol, vol_diff)
        amt_e = e.get("amount")
        amt_c = _num(c, "amount")
        if amt_e is not None and amt_c is not None:
            rel = abs(float(amt_e) - amt_c) / max(abs(amt_c), 1.0)
            max_amt_rel = max(max_amt_rel, rel)
            amt_checked += 1
            amt_bad = rel > AMOUNT_REL and abs(float(amt_e) - amt_c) > 100
        else:
            amt_bad = False
        if close_diff > CLOSE_ABS or vol_diff > VOLUME_ABS or amt_bad:
            bad.append(d)
    ok = bool(scored) and not bad
    return {
        "code": full_code,
        "source": bars[0]["source"] if bars else "",
        "overlap": len(scored),
        "max_close_diff": round(max_close, 4),
        "max_volume_diff": round(max_vol, 2),
        "max_amount_rel": round(max_amt_rel, 6),
        "amount_checked": amt_checked,
        "mismatch_days": bad[:8],
        "has_today_partial": today_s in ext and today_s > through.isoformat(),
        "ok": ok,
    }


def _bars_agree(left: List[Dict[str, Any]], right: List[Dict[str, Any]], days: List[str]) -> bool:
    a = {b["date"]: b for b in left}
    b = {b["date"]: b for b in right}
    if not days:
        return False
    for d in days:
        if d not in a or d not in b:
            return False
        if abs(float(a[d]["close"]) - float(b[d]["close"])) > CLOSE_ABS:
            return False
        if abs(float(a[d]["volume"]) - float(b[d]["volume"])) > VOLUME_ABS:
            return False
    return True


def _write_manifest(through: date, results: List[Dict[str, Any]]) -> Path:
    SHADOW_DIR.mkdir(parents=True, exist_ok=True)
    ok_n = sum(1 for r in results if r.get("ok"))
    qmt_n = sum(1 for r in results if r.get("qmt_cache_differs"))
    hard_fail = len(results) - ok_n - qmt_n
    payload = {
        "version": 1,
        "status": "completed" if results and hard_fail == 0 else "incomplete",
        "sync_trade_date": through.isoformat(),
        "universe_count": len(results),
        "ok_count": ok_n,
        "qmt_differs_count": qmt_n,
        "fail_count": hard_fail,
        "finished_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "primary_source": "eastmoney",
        "fallback": ["tencent", "ths"],
        "adjust": "none",
        "volume_unit": "手",
        "note": "影子目录，未替换 data/daily_cache",
    }
    path = SHADOW_DIR / "manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="公开日线对照 QMT daily_cache")
    ap.add_argument("--codes", default=",".join(DEFAULT_CODES), help="逗号分隔代码")
    ap.add_argument("--lookback-days", type=int, default=40, help="向前取多少日历日")
    args = ap.parse_args()

    manifest = load_manifest()
    raw_sync = str(manifest.get("sync_trade_date") or "")[:10]
    try:
        through = datetime.strptime(raw_sync, "%Y-%m-%d").date()
    except ValueError:
        through = date.today() - timedelta(days=1)
    start = (through - timedelta(days=int(args.lookback_days))).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")

    codes = [to_full_stock_code(x.strip()) for x in str(args.codes).split(",") if x.strip()]
    results: List[Dict[str, Any]] = []
    print(f"[reconcile] through={through.isoformat()} window={start}..{end} n={len(codes)}")
    for code in codes:
        cache = _read_cache(code)
        if not cache:
            print(f"[skip] {code} 无 daily_cache")
            results.append({"code": code, "ok": False, "source": "", "overlap": 0, "error": "no_cache"})
            continue
        bars, source = fetch_daily_bars(code, start, end)
        if not bars:
            print(f"[fail] {code} 公开源无数据 {source}")
            results.append({"code": code, "ok": False, "source": source, "overlap": 0, "error": source})
            continue
        _write_shadow(code, bars)
        item = _compare_one(code, bars, cache, through)
        results.append(item)
        if not item["ok"] and item["source"] == "eastmoney" and item["mismatch_days"]:
            agreed = False
            disagreed = False
            for fn in (fetch_tencent, fetch_ths):
                try:
                    other = fn(code, start, end)
                except Exception:
                    other = []
                covered = [d for d in item["mismatch_days"] if any(b["date"] == d for b in other)]
                if not covered:
                    continue
                if _bars_agree(bars, other, covered):
                    agreed = True
                    break
                disagreed = True
                break
            majority_match = item["overlap"] >= 10 and len(item["mismatch_days"]) < item["overlap"]
            if agreed or (majority_match and not disagreed):
                item["qmt_cache_differs"] = True
        flag = "OK" if item["ok"] else ("QMT" if item.get("qmt_cache_differs") else "DIFF")
        print(
            f"[{flag}] {code} src={item['source']} overlap={item['overlap']} "
            f"close<={item['max_close_diff']} vol<={item['max_volume_diff']} "
            f"amt_rel<={item['max_amount_rel']} partial_today={item['has_today_partial']}"
        )
        if item["mismatch_days"]:
            print("       mismatch", ",".join(item["mismatch_days"]))

    manifest_path = _write_manifest(through, results)
    ok_n = sum(1 for r in results if r.get("ok"))
    qmt_n = sum(1 for r in results if r.get("qmt_cache_differs"))
    print(
        f"[done] match={ok_n}/{len(results)} qmt_differs={qmt_n} "
        f"shadow={SHADOW_DIR} manifest={manifest_path.name} sync={through.isoformat()}"
    )
    failed = [r for r in results if not r.get("ok") and not r.get("qmt_cache_differs")]
    return 0 if results and not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
