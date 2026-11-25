# -*- coding: utf-8 -*-
"""盘后日线目标日完整性：扫描缺失空洞 → 外源补齐 → 失败告警。

覆盖两类问题（QMT 分片易出现）：
  1) 末日仍停在上一交易日（未追加）
  2) 中间缺目标日，但后续交易日已写入（空洞）
"""
from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from utils.daily_cache_reader import CACHE_DIR, merge_and_save_daily_cache
from utils.trading_day import previous_tradeday

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(CACHE_DIR)


@dataclass
class DayCoverageReport:
    day: str
    prev: str
    scanned: int = 0
    has_day: int = 0
    missing: List[str] = field(default_factory=list)
    skipped_stale: int = 0
    by_market: Dict[str, int] = field(default_factory=dict)
    by_prefix3: List[Tuple[str, int]] = field(default_factory=list)
    consecutive_runs: List[Tuple[str, str, int]] = field(default_factory=list)

    @property
    def missing_count(self) -> int:
        return len(self.missing)


@dataclass
class RepairResult:
    ok: int = 0
    fail: int = 0
    skip_empty: int = 0
    empty_codes: List[str] = field(default_factory=list)
    fail_samples: List[str] = field(default_factory=list)
    still_missing: List[str] = field(default_factory=list)


def _iso(d: date | str) -> str:
    if isinstance(d, date):
        return d.strftime("%Y-%m-%d")
    return str(d or "")[:10]


def _etf_stem_set() -> set:
    out: set = set()
    path = ROOT / "data" / "etf_universe.json"
    if not path.is_file():
        return out
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return out
    for raw in data.get("codes") or []:
        c = str(raw or "").strip().upper()
        if not c:
            continue
        if "." not in c:
            c6 = "".join(ch for ch in c if ch.isdigit())[-6:].zfill(6)
            if len(c6) != 6:
                continue
            if c6.startswith(("5", "6", "9")):
                c = c6 + ".SH"
            elif c6.startswith(("0", "1", "2", "3")):
                c = c6 + ".SZ"
            else:
                c = c6 + ".BJ"
        out.add(c.split("/")[-1])
    return out


def _last_date(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 320))
            tail = handle.read().decode("utf-8", "replace").strip().splitlines()
    except OSError:
        return ""
    if not tail:
        return ""
    return tail[-1].split(",")[0][:10]


def _file_has_day(path: Path, day: str) -> bool:
    needle = (day[:10] + ",").encode("ascii", "ignore")
    if not needle:
        return False
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return needle in data


def _pattern_stats(missing: Sequence[str]) -> Tuple[Dict[str, int], List[Tuple[str, int]], List[Tuple[str, str, int]]]:
    markets = Counter(x.split(".")[-1] if "." in x else "?" for x in missing)
    p3 = Counter(x[:3] for x in missing)
    codes = sorted(int(x.split(".")[0]) for x in missing if x.split(".")[0].isdigit())
    runs: List[Tuple[str, str, int]] = []
    if codes:
        start = prev = codes[0]
        for c in codes[1:]:
            if c == prev + 1:
                prev = c
                continue
            runs.append((f"{start:06d}", f"{prev:06d}", prev - start + 1))
            start = prev = c
        runs.append((f"{start:06d}", f"{prev:06d}", prev - start + 1))
        runs.sort(key=lambda r: -r[2])
    return dict(markets), p3.most_common(12), runs[:12]


def scan_missing_for_day(
    day: date | str,
    *,
    stocks_only: bool = True,
    prev: Optional[date | str] = None,
) -> DayCoverageReport:
    """扫描 daily_cache：有上一交易日却缺目标日的股票。"""
    day_s = _iso(day)
    if prev is None:
        prev_d = previous_tradeday(datetime.strptime(day_s, "%Y-%m-%d").date())
        prev_s = prev_d.strftime("%Y-%m-%d")
    else:
        prev_s = _iso(prev)

    etf = _etf_stem_set() if stocks_only else set()
    missing: List[str] = []
    has_day = 0
    scanned = 0
    skipped_stale = 0

    if not CACHE.is_dir():
        return DayCoverageReport(day=day_s, prev=prev_s)

    for path in CACHE.glob("*.csv"):
        stem = path.stem.strip().upper()
        if stocks_only and stem in etf:
            continue
        scanned += 1
        last = _last_date(path)
        if last == day_s:
            has_day += 1
            continue
        if last == prev_s:
            missing.append(stem)
            continue
        if last and last > day_s:
            if _file_has_day(path, day_s):
                has_day += 1
            else:
                # 可能是空洞，也可能无 prev（新上市后第一段）；有 prev 才算缺口
                if _file_has_day(path, prev_s):
                    missing.append(stem)
                else:
                    skipped_stale += 1
            continue
        skipped_stale += 1

    by_mkt, by_p3, runs = _pattern_stats(missing)
    return DayCoverageReport(
        day=day_s,
        prev=prev_s,
        scanned=scanned,
        has_day=has_day,
        missing=sorted(missing),
        skipped_stale=skipped_stale,
        by_market=by_mkt,
        by_prefix3=by_p3,
        consecutive_runs=runs,
    )


def _fetch_one_bar(code: str, day: str) -> Tuple[str, Optional[Dict[str, Any]], str]:
    from utils.external_daily_bars import fetch_daily_bars

    bars, source = fetch_daily_bars(code, day, day)
    for bar in bars or []:
        if str(bar.get("date") or "")[:10] == day and float(bar.get("volume") or 0) > 0:
            return code, bar, source
    return code, None, source or "empty"


def repair_missing_bars(
    day: date | str,
    codes: Sequence[str],
    *,
    workers: int = 8,
) -> RepairResult:
    """用东财→腾讯→同花顺补缺失目标日，写入 daily_cache。"""
    day_s = _iso(day)
    result = RepairResult()
    codes = [str(c).strip().upper() for c in codes if str(c).strip()]
    if not codes:
        return result

    workers = max(1, min(int(workers or 8), 16))
    empty_set: set = set()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_fetch_one_bar, code, day_s): code for code in codes}
        done = 0
        total = len(futs)
        for fut in as_completed(futs):
            code = futs[fut]
            done += 1
            try:
                c, bar, src = fut.result()
            except Exception as e:
                result.fail += 1
                result.fail_samples.append(f"{code}:{type(e).__name__}")
                continue
            if bar is None:
                result.skip_empty += 1
                empty_set.add(c)
                continue
            extra = pd.DataFrame(
                [
                    {
                        "date": day_s,
                        "open": float(bar["open"]),
                        "high": float(bar["high"]),
                        "low": float(bar["low"]),
                        "close": float(bar["close"]),
                        "volume": float(bar.get("volume") or 0),
                        "amount": bar.get("amount"),
                    }
                ]
            )
            if merge_and_save_daily_cache(c, extra):
                result.ok += 1
            else:
                result.fail += 1
                result.fail_samples.append(f"{c}:merge:{src}")
            if done % 50 == 0 or done == total:
                print(
                    f"[日线补齐] {done}/{total} ok={result.ok} empty={result.skip_empty} "
                    f"fail={result.fail}",
                    flush=True,
                )

    result.empty_codes = sorted(empty_set)
    for code in codes:
        path = CACHE / f"{code}.csv"
        if path.is_file() and _file_has_day(path, day_s):
            continue
        result.still_missing.append(code)
    return result


def summarize_report(rep: DayCoverageReport, repair: Optional[RepairResult] = None) -> str:
    parts = [
        f"day={rep.day}",
        f"scanned={rep.scanned}",
        f"has={rep.has_day}",
        f"missing={rep.missing_count}",
    ]
    if rep.by_market:
        parts.append("mkt=" + ",".join(f"{k}:{v}" for k, v in sorted(rep.by_market.items())))
    if rep.by_prefix3:
        top = ",".join(f"{a}×{b}" for a, b in rep.by_prefix3[:5])
        parts.append(f"p3={top}")
    if rep.consecutive_runs:
        r0 = rep.consecutive_runs[0]
        parts.append(f"run={r0[0]}-{r0[1]}({r0[2]})")
    if repair is not None:
        parts.append(
            f"repair ok={repair.ok} empty={repair.skip_empty} "
            f"fail={repair.fail} left={len(repair.still_missing)}"
        )
    return " ".join(parts)


def _hard_fail_codes(
    still_missing: Sequence[str],
    empty_codes: Optional[Sequence[str]] = None,
) -> List[str]:
    """仍缺且不是外源 empty（停牌）的，视为硬失败。"""
    empty = set(empty_codes or [])
    return [c for c in still_missing if c not in empty]


def _format_code_list(codes: Sequence[str], *, limit: int = 400) -> str:
    """告警正文用的代码列表；过长截断并注明余量。"""
    items = [str(c).strip() for c in codes if str(c).strip()]
    if not items:
        return "-"
    if len(items) <= limit:
        return ", ".join(items)
    shown = ", ".join(items[:limit])
    return f"{shown} …（另有 {len(items) - limit} 只未列出，共 {len(items)}）"


def verify_and_repair_trade_day(
    day: date | str,
    *,
    repair: bool = True,
    notify: bool = True,
    workers: int = 8,
    max_missing_ok: int = 0,
) -> Tuple[bool, str, DayCoverageReport, Optional[RepairResult]]:
    """检验目标日覆盖；可选外源补齐；补后仍有硬缺口则门禁失败。

    第一步扫描只要发现空洞，无论补后是否还有硬缺口，都发 Server酱（列出缺失代码，供人工审核）。
    max_missing_ok: 允许残留的硬缺口上限（默认 0）。
    外源也拉不到的（停牌等）不计入硬缺口。
    """
    day_s = _iso(day)
    rep = scan_missing_for_day(day_s)
    repair_res: Optional[RepairResult] = None

    if rep.missing_count <= 0:
        detail = summarize_report(rep) + "（目标日覆盖完整）"
        return True, detail, rep, None

    print(
        f"[日线检验] 发现缺失 {rep.missing_count} 只 "
        f"mkt={rep.by_market} 样例={rep.missing[:8]}",
        flush=True,
    )
    scanned_missing = list(rep.missing)

    if repair:
        print(f"[日线补齐] 外源补 {rep.missing_count} 只 workers={workers}", flush=True)
        repair_res = repair_missing_bars(day_s, rep.missing, workers=workers)
        rep2 = scan_missing_for_day(day_s)
        hard = _hard_fail_codes(rep2.missing, repair_res.empty_codes)
        detail = summarize_report(rep2, repair_res)
        ok = len(hard) <= max_missing_ok
        if ok:
            if hard:
                detail += f" 残留硬缺口={len(hard)}≤容差"
            else:
                detail += "（已补齐或仅剩停牌空窗）"
        else:
            detail += f" 硬缺口={len(hard)} 样例={hard[:15]}"
        if notify:
            _alert_daily_cache_holes(
                day_s,
                scan_rep=rep,
                after_rep=rep2,
                repair=repair_res,
                hard=hard,
                scanned_missing=scanned_missing,
                gate_ok=ok,
            )
        return ok, detail, rep2, repair_res

    # 不补齐：有缺失即按容差判定；仍告警供人工审核
    detail = summarize_report(rep) + f" 样例={rep.missing[:12]}"
    ok = rep.missing_count <= max_missing_ok
    if notify:
        _alert_daily_cache_holes(
            day_s,
            scan_rep=rep,
            after_rep=None,
            repair=None,
            hard=scanned_missing,
            scanned_missing=scanned_missing,
            gate_ok=ok,
        )
    return ok, detail, rep, None


def _alert_daily_cache_holes(
    day: str,
    *,
    scan_rep: DayCoverageReport,
    after_rep: Optional[DayCoverageReport],
    repair: Optional[RepairResult],
    hard: Sequence[str],
    scanned_missing: Sequence[str],
    gate_ok: bool,
) -> None:
    """初扫有空洞即推送：列出缺失代码，便于人工审核（与门禁是否通过无关）。"""
    try:
        from utils.server_chan_notify import notify_alert
    except Exception:
        return
    d8 = day.replace("-", "")[:8]
    runs = ""
    if scan_rep.consecutive_runs:
        runs = "；连续号段：" + ", ".join(
            f"{a}-{b}({n})" for a, b, n in scan_rep.consecutive_runs[:5]
        )
    repair_txt = "未补齐"
    if repair is not None:
        repair_txt = (
            f"ok={repair.ok} empty={repair.skip_empty} fail={repair.fail} "
            f"left={len(repair.still_missing)}"
        )
    left_n = after_rep.missing_count if after_rep is not None else len(scanned_missing)
    gate_txt = "通过" if gate_ok else "失败（硬缺口）"
    content = (
        f"**日期**：{day}\n"
        f"**初扫**：scanned={scan_rep.scanned} has={scan_rep.has_day} "
        f"missing={scan_rep.missing_count}{runs}\n"
        f"**市场分布**：{scan_rep.by_market or '-'}\n"
        f"**前缀**：{scan_rep.by_prefix3[:8] or '-'}\n"
        f"**初扫缺失代码（请人工审核）**：{_format_code_list(scanned_missing)}\n"
        f"**补齐**：{repair_txt}\n"
        f"**补后仍缺**：{left_n}\n"
        f"**硬缺口**：{len(hard)} {_format_code_list(hard, limit=200)}\n"
        f"**门禁**：{gate_txt}\n"
        f"**说明**：QMT/外源同步后扫到目标日空洞即告警；"
        f"已尝试东财/腾讯/同花顺补齐。停牌外源 empty 不计硬失败，但仍请核对上表代码。\n"
        f"**时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    title = f"盘后批跑告警 {day} · 日线空洞{scan_rep.missing_count}只需审核"
    if not gate_ok:
        title = f"盘后批跑告警 {day} · 日线硬缺口门禁失败"
    notify_alert(
        title=title,
        content=content,
        alert_key=f"daily_cache_holes_{d8}",
        cooldown_sec=0,
    )


def coverage_ok_for_check(
    asof: date,
    *,
    max_missing_ok: int = 20,
    max_run_ok: int = 4,
) -> Tuple[bool, str]:
    """供齐全度面板：只检验不补齐、不推送。

    少量散点缺失多为停牌/无行情，不挡门禁；
    连续号段（分片中断典型特征）或缺失过多则判不合格。
    """
    rep = scan_missing_for_day(asof)
    longest = rep.consecutive_runs[0][2] if rep.consecutive_runs else 0
    detail = summarize_report(rep)
    if rep.missing_count and rep.missing_count <= max_missing_ok and longest <= max_run_ok:
        return True, detail + f" 散点缺失可忽略(≤{max_missing_ok},run≤{max_run_ok})"
    if rep.missing_count <= 0:
        return True, detail
    return False, detail + f" 样例={rep.missing[:10]}"
