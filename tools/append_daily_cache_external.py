# -*- coding: utf-8 -*-
"""盘后日线：写入 data/daily_cache。

只在文件末尾追加目标交易日，不改已有行。
当天这一根优先用 QMT 行情页导出；表里凑不齐的再向东财、腾讯、同花顺要。
成交量单位是手，与现有缓存一致。

用法：
  python tools/append_daily_cache_external.py
  python tools/append_daily_cache_external.py --date 2026-10-14
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.external_daily_bars import fetch_daily_bars  # noqa: E402

CACHE = ROOT / "data" / "daily_cache"
MANIFEST = CACHE / "manifest.json"
_PRINT = threading.Lock()


def _last_date(path: Path) -> str:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 240))
            tail = f.read().decode("utf-8", "replace").strip().splitlines()
    except OSError:
        return ""
    if not tail:
        return ""
    return tail[-1].split(",")[0][:10]


def _fmt_px(value: float) -> str:
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _fmt_vol(value: float) -> str:
    return str(int(round(float(value))))


def _fmt_amt(value) -> str:
    if value is None:
        return ""
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text


def _append(path: Path, bar: dict) -> None:
    line = ",".join(
        [
            bar["date"],
            _fmt_px(bar["open"]),
            _fmt_px(bar["high"]),
            _fmt_px(bar["low"]),
            _fmt_px(bar["close"]),
            _fmt_vol(bar["volume"]),
            _fmt_amt(bar.get("amount")),
        ]
    )
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        data += b"\n"
    data += (line + "\n").encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _one(path: Path, day: str, quote_bars: dict) -> str:
    code = path.stem
    last = _last_date(path)
    if last == day:
        return "have"
    quoted = quote_bars.get(code)
    if quoted and float(quoted.get("volume") or 0) > 0:
        if _last_date(path) == day:
            return "have"
        _append(path, quoted)
        return "ok:qmt_quote"
    bars, source = fetch_daily_bars(code, day, day)
    hit = None
    for bar in bars:
        if str(bar.get("date") or "")[:10] == day and float(bar.get("volume") or 0) > 0:
            hit = bar
            break
    if hit is None:
        return "miss:" + source
    if _last_date(path) == day:
        return "have"
    _append(path, hit)
    return "ok:" + source


def _update_manifest(day: str, ok: int, miss: int, source: str) -> None:
    if not MANIFEST.is_file():
        return
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    data["sync_trade_date"] = day
    data["status"] = "completed"
    data["ok_count"] = ok
    data["fail_count"] = miss
    data["finished_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    data["trigger"] = "external_kline"
    data["primary_source"] = source or "eastmoney"
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_trade_day(day: str, prev: str, workers: int = 16) -> int:
    """把 prev 收尾的缓存补到 day。已是 day 的文件跳过。成功返回 0。"""
    day = day[:10]
    prev = prev[:10]
    already = 0
    targets = []
    for path in CACHE.glob("*.csv"):
        last = _last_date(path)
        if last == day:
            already += 1
        elif last == prev:
            targets.append(path)
    from utils.qmt_hsj_quote import load_bars

    quote_bars = load_bars(day)
    print(
        f"[日线] 目标日={day} 上一交易日={prev} "
        f"待补={len(targets)} 已有={already} 行情表={len(quote_bars)}",
        flush=True,
    )
    if not targets:
        if already > 0:
            print(f"[日线] {day} 已在缓存中，共 {already} 只", flush=True)
            return 0
        print("[日线] 没有可追加的文件", flush=True)
        return 1
    ok = miss = have = 0
    sources = {}
    started = time.time()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(_one, path, day, quote_bars): path for path in targets}
        done = 0
        for fut in as_completed(futures):
            done += 1
            try:
                result = fut.result()
            except Exception as exc:
                result = "miss:" + type(exc).__name__
            if result == "have":
                have += 1
            elif result.startswith("ok:"):
                ok += 1
                sources[result[3:]] = sources.get(result[3:], 0) + 1
            else:
                miss += 1
            if done % 400 == 0 or done == len(targets):
                with _PRINT:
                    print(
                        f"进度 {done}/{len(targets)} 写入={ok} 已有={have} 无行情={miss} "
                        f"用时={int(time.time() - started)}s",
                        flush=True,
                    )
    universe = already + len(targets)
    covered = already + ok + have
    success = universe > 0 and covered >= int(universe * 0.8)
    if success:
        top = max(sources, key=sources.get) if sources else ""
        _update_manifest(day, ok + already, miss, top)
    print(f"完成 写入={ok} 已有={have + already} 无行情={miss} 来源={sources}", flush=True)
    return 0 if success else 1


def main() -> int:
    from utils.post_market_pipeline import resolve_asof_day
    from utils.trading_day import previous_tradeday

    ap = argparse.ArgumentParser(description="盘后日线：行情导出为主，缺的再向东财、腾讯、同花顺要")
    ap.add_argument("--date", default="", help="目标交易日 YYYY-MM-DD，默认按收盘时间决定")
    ap.add_argument("--prev", default="", help="只处理末日等于这一天的文件，默认目标日的上一交易日")
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()
    if args.date:
        day = datetime.strptime(args.date[:10], "%Y-%m-%d").date()
    else:
        day = resolve_asof_day()
    if args.prev:
        prev = args.prev[:10]
    else:
        prev = previous_tradeday(day).isoformat()
    return append_trade_day(day.isoformat(), prev, workers=max(1, args.workers))


if __name__ == "__main__":
    raise SystemExit(main())
