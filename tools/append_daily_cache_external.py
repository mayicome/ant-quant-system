# -*- coding: utf-8 -*-
"""盘后日线：写入 data/daily_cache。

默认只在文件末尾追加目标交易日，不改历史。
但会对「目标日已存在」与「上一交易日」做多源对照覆盖，避免盘中半截 K 锁死。

取源：
  - 新追加：东财 -> 腾讯 -> 同花顺（单源即可，与旧逻辑一致）
  - 覆盖/回补：优先两源以上一致才改写；仅单源时只修半截K形态，源互斥则跳过

用法：
  python tools/append_daily_cache_external.py
  python tools/append_daily_cache_external.py --date 2026-10-16
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
from typing import Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.external_daily_bars import (  # noqa: E402
    fetch_daily_bars,
    fetch_day_bar_consensus,
)

CACHE = ROOT / "data" / "daily_cache"
MANIFEST = CACHE / "manifest.json"
_PRINT = threading.Lock()
_PX_REL = 0.002
_PX_ABS = 0.015


def _last_date(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 240))
            tail = handle.read().decode("utf-8", "replace").strip().splitlines()
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


def _bar_line(bar: dict) -> str:
    return ",".join(
        [
            str(bar["date"])[:10],
            _fmt_px(bar["open"]),
            _fmt_px(bar["high"]),
            _fmt_px(bar["low"]),
            _fmt_px(bar["close"]),
            _fmt_vol(bar["volume"]),
            _fmt_amt(bar.get("amount")),
        ]
    )


def _append(path: Path, bar: dict) -> None:
    line = _bar_line(bar)
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        data += b"\n"
    data += (line + "\n").encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _read_rows(path: Path) -> Dict[str, dict]:
    rows: Dict[str, dict] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        if not line.strip() or line.lower().startswith("date,"):
            continue
        parts = line.split(",")
        if len(parts) < 6:
            continue
        d = parts[0][:10]
        try:
            rows[d] = {
                "date": d,
                "open": float(parts[1]),
                "high": float(parts[2]),
                "low": float(parts[3]),
                "close": float(parts[4]),
                "volume": float(parts[5]),
                "amount": float(parts[6]) if len(parts) > 6 and parts[6] != "" else None,
            }
        except (TypeError, ValueError):
            continue
    return rows


def _write_rows(path: Path, rows: Dict[str, dict]) -> None:
    buf = ["date,open,high,low,close,volume,amount\n"]
    for d in sorted(rows.keys()):
        buf.append(_bar_line(rows[d]) + "\n")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes("".join(buf).encode("utf-8"))
    tmp.replace(path)


def _px_bad(a: float, b: float) -> bool:
    if a <= 0 or b <= 0:
        return True
    return abs(a - b) / max(b, 1e-9) > _PX_REL and abs(a - b) > _PX_ABS


def _ohlc_differs(local: dict, ext: dict) -> bool:
    return any(_px_bad(float(local[k]), float(ext[k])) for k in ("open", "high", "low", "close"))


def _trusted_overwrite_bar(code: str, day: str, local=None):
    """Overwrite bar policy.

    Prefer >=2 sources agreeing on OHLC.
    Solo source: only overwrite when local looks like incomplete EOD vs that source.
    """
    from utils.external_daily_bars import looks_like_incomplete_eod

    bar, src = fetch_day_bar_consensus(code, day, min_agree=2)
    if bar is None:
        return None, "skip:" + src
    if "|solo" not in src:
        return bar, src
    if local is not None and looks_like_incomplete_eod(local, bar):
        return bar, src + "|incomplete"
    return None, "skip_solo:" + src


def _one_append(path: Path, day: str) -> str:
    """末尾为 prev 时追加 day（单源即可）。"""
    code = path.stem
    if _last_date(path) == day:
        return "have"
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


def _one_overwrite(path: Path, day: str) -> str:
    """If day exists locally, overwrite when trusted external bar differs."""
    code = path.stem
    rows = _read_rows(path)
    local = rows.get(day)
    if local is None:
        return "absent"
    bar, src = _trusted_overwrite_bar(code, day, local=local)
    if bar is None:
        return src
    if not _ohlc_differs(local, bar):
        return "same:" + src
    rows[day] = {
        "date": day,
        "open": float(bar["open"]),
        "high": float(bar["high"]),
        "low": float(bar["low"]),
        "close": float(bar["close"]),
        "volume": float(bar.get("volume") or 0),
        "amount": bar.get("amount"),
    }
    _write_rows(path, rows)
    return "fix:" + src


def _update_manifest(day: str, ok: int, miss: int, source: str) -> None:
    if not MANIFEST.is_file():
        return
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    data["sync_trade_date"] = day
    data["status"] = "completed"
    data["ok_count"] = ok
    data["fail_count"] = miss
    data["skip_count"] = 0
    data["finished_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    data["trigger"] = "external_kline"
    data["primary_source"] = source or "eastmoney"
    data["primary_path"] = "eastmoney/tencent/ths+consensus_overwrite"
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _pool_map(paths, fn, day: str, label: str, workers: int) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    sources: Dict[str, int] = {}
    started = time.time()
    if not paths:
        return {"n": 0}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fn, path, day): path for path in paths}
        done = 0
        for fut in as_completed(futures):
            done += 1
            try:
                result = fut.result()
            except Exception as exc:
                result = "miss:" + type(exc).__name__
            key = result.split(":", 1)[0]
            counts[key] = counts.get(key, 0) + 1
            if result.startswith(("ok:", "fix:", "same:")) and ":" in result:
                src = result.split(":", 1)[1]
                sources[src] = sources.get(src, 0) + 1
            if done % 400 == 0 or done == len(paths):
                with _PRINT:
                    print(
                        "[%s] 进度 %d/%d %s 用时=%ds"
                        % (label, done, len(paths), counts, int(time.time() - started)),
                        flush=True,
                    )
    counts["_sources"] = sources  # type: ignore
    counts["n"] = len(paths)
    return counts


def reconcile_trade_day(day: str, workers: int = 16) -> int:
    """规则3：回补/校正上一交易日——凡本地已有该日的，多源一致则覆盖错价。"""
    day = day[:10]
    targets = []
    for path in CACHE.glob("*.csv"):
        rows = _read_rows(path)
        if day in rows:
            targets.append(path)
    print("[回补] 交易日=%s 本地已有=%d" % (day, len(targets)), flush=True)
    if not targets:
        return 0
    counts = _pool_map(targets, _one_overwrite, day, "回补", workers)
    print(
        "[回补] 完成 覆盖=%d 一致=%d 跳过单源=%d 跳过其它=%d 源=%s"
        % (
            counts.get("fix", 0),
            counts.get("same", 0),
            counts.get("skip_solo", 0),
            counts.get("skip", 0) + counts.get("miss", 0) + counts.get("disagree", 0),
            counts.get("_sources", {}),
        ),
        flush=True,
    )
    return 0


def append_trade_day(day: str, prev: str, workers: int = 16) -> int:
    """规则2：末尾为 prev 的追加 day；已是 day 的再多源对照覆盖。"""
    day = day[:10]
    prev = prev[:10]
    already = []
    targets = []
    for path in CACHE.glob("*.csv"):
        last = _last_date(path)
        if last == day:
            already.append(path)
        elif last == prev:
            targets.append(path)
    print(
        "[追加] 目标日=%s 上一交易日=%s 待追加=%d 已有待校对=%d"
        % (day, prev, len(targets), len(already)),
        flush=True,
    )

    ok = miss = 0
    sources: Dict[str, int] = {}
    if targets:
        counts = _pool_map(targets, _one_append, day, "追加", workers)
        ok = counts.get("ok", 0)
        miss = counts.get("miss", 0) + counts.get("have", 0)  # have race ~0
        # recount properly
        miss = counts.get("miss", 0)
        have_race = counts.get("have", 0)
        sources = counts.get("_sources", {}) or {}
        print(
            "[追加] 写入=%d 并发已有=%d 拉不到=%d 源=%s"
            % (ok, have_race, miss, sources),
            flush=True,
        )

    fixed = same = skipped = 0
    if already:
        # 规则2：目标日已存在也可覆盖
        counts2 = _pool_map(already, _one_overwrite, day, "校对", workers)
        fixed = counts2.get("fix", 0)
        same = counts2.get("same", 0)
        skipped = (
            counts2.get("skip_solo", 0)
            + counts2.get("skip", 0)
            + counts2.get("miss", 0)
        )
        for k, v in (counts2.get("_sources", {}) or {}).items():
            sources[k] = sources.get(k, 0) + v
        print(
            "[校对] 覆盖=%d 一致=%d 跳过=%d"
            % (fixed, same, skipped),
            flush=True,
        )

    universe = len(already) + len(targets)
    covered = len(already) + ok
    # 追加阶段已有 day 的也算覆盖
    success = universe > 0 and covered >= int(universe * 0.8)
    if success:
        top = max(sources, key=sources.get) if sources else ""
        _update_manifest(day, covered, miss, top)
    print(
        "结果 新写=%d 已有校对覆盖=%d 拉不到=%d 源=%s"
        % (ok, fixed, miss, sources),
        flush=True,
    )
    if not targets and not already:
        print("[追加] 没有可处理的文件", flush=True)
        return 1
    return 0 if success else 1


def main() -> int:
    try:
        from utils.http_ipv4 import install_prefer_ipv4
        install_prefer_ipv4()
    except Exception:
        pass
    from utils.post_market_pipeline import resolve_asof_day
    from utils.trading_day import previous_tradeday

    ap = argparse.ArgumentParser(
        description="盘后日线：东财/腾讯/同花顺追加；多源一致才覆盖错价；并回补上一交易日"
    )
    ap.add_argument("--date", default="", help="目标交易日 YYYY-MM-DD，默认按盘后时点推断")
    ap.add_argument("--prev", default="", help="只处理末日等于该日的文件；默认目标日的上一交易日")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--skip-etf", action="store_true", help="跳过 ETF 日同步；默认每天刷新 ETF 列表并补缺")
    ap.add_argument("--skip-reconcile", action="store_true", help="跳过上一交易日多源回补")
    args = ap.parse_args()
    if args.date:
        day = datetime.strptime(args.date[:10], "%Y-%m-%d").date()
    else:
        day = resolve_asof_day()
    if args.prev:
        prev = args.prev[:10]
    else:
        prev = previous_tradeday(day).isoformat()

    etf_rc = 0
    if not args.skip_etf:
        try:
            from tools.sync_etf_daily_cache import sync_etf_daily_cache

            print("[日线] 先同步 ETF 截至 " + day.isoformat(), flush=True)
            etf_rc = int(sync_etf_daily_cache(day.isoformat(), refresh_universe=True) or 0)
        except Exception as exc:
            etf_rc = 1
            print("[日线] ETF 同步异常: %s" % (exc,), flush=True)

    # 规则3：先回补上一交易日
    if not args.skip_reconcile:
        reconcile_trade_day(prev, workers=max(1, args.workers))

    ashare_rc = append_trade_day(day.isoformat(), prev, workers=max(1, args.workers))
    if ashare_rc == 0:
        return 0
    return 1 if etf_rc != 0 else ashare_rc


if __name__ == "__main__":
    raise SystemExit(main())
