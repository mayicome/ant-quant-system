# -*- coding: utf-8 -*-
"""将完整 ETF 池日线写入 data/daily_cache（串行，可补缺口）。

新建 CSV 固定从 2025-01-01 起（不拉更早）；已有文件只追加缺口到目标日。
拉数时在东财/腾讯/同花顺中选历史最长的一份，避免同花顺短窗截断。

用法：
  python tools/sync_etf_daily_cache.py --date 2026-10-26
  python tools/sync_etf_daily_cache.py --limit 5
  python tools/sync_etf_daily_cache.py --refresh-universe
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.etf_universe import load_etf_universe  # noqa: E402
from utils.external_daily_bars import (  # noqa: E402
    fetch_daily_bars,
    fetch_eastmoney,
    fetch_tencent,
    fetch_ths,
)

CACHE = ROOT / "data" / "daily_cache"
MANIFEST = CACHE / "manifest.json"
HEADER = "date,open,high,low,close,volume,amount\n"
# 新建 ETF CSV 固定从 2025-01-01 起，不拉更早历史
BACKFILL_START_FIXED = "2025-01-01"


def _backfill_start() -> str:
    return BACKFILL_START_FIXED


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


def _next_day(iso: str) -> str:
    d = datetime.strptime(iso[:10], "%Y-%m-%d").date()
    return (d + timedelta(days=1)).isoformat()


def _filter_bars(bars: List[dict], start: str, end: str) -> List[dict]:
    out = []
    seen = set()
    for bar in bars:
        day = str(bar.get("date") or "")[:10]
        if not day or day < start[:10] or day > end[:10]:
            continue
        if float(bar.get("volume") or 0) <= 0:
            continue
        if day in seen:
            continue
        seen.add(day)
        out.append(bar)
    out.sort(key=lambda b: str(b["date"])[:10])
    return out


def _fetch_best(code: str, start: str, end: str) -> Tuple[List[dict], str]:
    """在东财/腾讯/同花顺中选有效根数最多的一份。"""
    start_ymd = start.replace("-", "")
    end_ymd = end.replace("-", "")
    candidates = []
    for name, fn in (
        ("eastmoney", fetch_eastmoney),
        ("tencent", fetch_tencent),
        ("ths", fetch_ths),
    ):
        try:
            rows = fn(code, start_ymd, end_ymd)
        except Exception:
            rows = []
        rows = _filter_bars(rows or [], start, end)
        if rows:
            candidates.append((len(rows), name, rows))
    if candidates:
        candidates.sort(reverse=True)
        _n, name, rows = candidates[0]
        return rows, name
    bars, src = fetch_daily_bars(code, start_ymd, end_ymd)
    return _filter_bars(bars, start, end), src


def _write_new(path: Path, bars: List[dict]) -> None:
    body = HEADER + "".join(_bar_line(b) + "\n" for b in bars)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


def _append_bars(path: Path, bars: List[dict]) -> None:
    if not bars:
        return
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        data += b"\n"
    data += ("".join(_bar_line(b) + "\n" for b in bars)).encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _sync_one(
    code: str,
    target: str,
    backfill_start: str,
) -> Tuple[str, str]:
    """返回 (status, detail)。status: already|created|gap|miss|empty"""
    path = CACHE / f"{code}.csv"
    target = target[:10]
    if path.is_file():
        last = _last_date(path)
        if last == target:
            return "already", last
        if last and last > target:
            return "already", last
        if last:
            start = _next_day(last)
        else:
            start = backfill_start
        if start > target:
            return "already", last or ""
        # 短缺口用综合拉取即可；长缺口也走最长源
        if start <= backfill_start or (last and last < backfill_start):
            bars, source = _fetch_best(code, backfill_start, target)
            # 末段从 last 之后追加，避免重写（若需全量可另做 refill）
            bars = [b for b in bars if str(b["date"])[:10] >= start]
        else:
            bars, source = _fetch_best(code, start, target)
        if not bars:
            return "miss", source or "empty"
        if not last:
            _write_new(path, bars)
            return "created", source
        _append_bars(path, bars)
        return "gap", source
    bars, source = _fetch_best(code, backfill_start, target)
    if not bars:
        return "miss", source or "empty"
    _write_new(path, bars)
    return "created", source


def sync_etf_daily_cache(
    day: str,
    *,
    refresh_universe: bool = False,
    limit: int = 0,
    success_ratio: float = 0.8,
    progress_every: int = 50,
) -> int:
    """同步 ETF 日线到 day。返回 0 表示达到成功阈值。"""
    day = day[:10]
    CACHE.mkdir(parents=True, exist_ok=True)
    items = load_etf_universe(refresh=refresh_universe)
    if limit and limit > 0:
        items = items[: int(limit)]
    backfill_start = _backfill_start()
    print(
        "[ETF] 目标日=%s 池=%d backfill_start=%s 串行拉取"
        % (day, len(items), backfill_start),
        flush=True,
    )
    if not items:
        print("[ETF] 空池", flush=True)
        return 1

    counts: Dict[str, int] = {
        "already": 0,
        "created": 0,
        "gap": 0,
        "miss": 0,
        "empty": 0,
    }
    miss_codes: List[str] = []
    sources: Dict[str, int] = {}
    started = time.time()

    for i, it in enumerate(items, 1):
        code = it["code"]
        try:
            status, detail = _sync_one(code, day, backfill_start)
        except Exception as exc:
            status, detail = "miss", type(exc).__name__
        if status not in counts:
            status = "miss"
        counts[status] += 1
        if status == "miss":
            miss_codes.append(code)
        elif detail and status in ("created", "gap"):
            sources[detail] = sources.get(detail, 0) + 1
        if progress_every > 0 and (i % progress_every == 0 or i == len(items)):
            print(
                "ETF进度 %d/%d 新建=%d 补缺=%d 已有=%d 未命中=%d 耗时=%ds"
                % (
                    i,
                    len(items),
                    counts["created"],
                    counts["gap"],
                    counts["already"],
                    counts["miss"],
                    int(time.time() - started),
                ),
                flush=True,
            )

    at_target = 0
    for it in items:
        path = CACHE / f"{it['code']}.csv"
        if path.is_file() and _last_date(path) == day:
            at_target += 1
    ratio = at_target / float(len(items)) if items else 0.0
    ok = ratio >= float(success_ratio)
    print(
        "ETF完成 池=%d 新建=%d 补缺=%d 已有=%d 未命中=%d 末日=%s占比=%.1f%% 源=%s"
        % (
            len(items),
            counts["created"],
            counts["gap"],
            counts["already"],
            counts["miss"],
            day,
            ratio * 100.0,
            sources,
        ),
        flush=True,
    )
    if miss_codes:
        sample = miss_codes[:20]
        print("[ETF] miss样本 %s%s" % (sample, "..." if len(miss_codes) > 20 else ""), flush=True)
    return 0 if ok else 1


def main() -> int:
    from utils.post_market_pipeline import resolve_asof_day

    ap = argparse.ArgumentParser(description="同步完整 ETF 日线进 daily_cache")
    ap.add_argument("--date", default="", help="目标交易日 YYYY-MM-DD")
    ap.add_argument("--limit", type=int, default=0, help="仅处理前 N 只（冒烟）")
    ap.add_argument("--refresh-universe", action="store_true", help="强制重拉 ETF 池")
    ap.add_argument("--success-ratio", type=float, default=0.8)
    args = ap.parse_args()
    if args.date:
        day = datetime.strptime(args.date[:10], "%Y-%m-%d").date().isoformat()
    else:
        day = resolve_asof_day().isoformat()
    return sync_etf_daily_cache(
        day,
        refresh_universe=bool(args.refresh_universe),
        limit=int(args.limit or 0),
        success_ratio=float(args.success_ratio),
    )


if __name__ == "__main__":
    raise SystemExit(main())
