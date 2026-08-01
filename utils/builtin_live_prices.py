# -*- coding: utf-8 -*-
"""builtin 实盘：读 results.json 现价，等待大 QMT 订阅推送就绪。"""
from __future__ import annotations

import os
import time
from datetime import datetime, time as dt_time
from typing import Any, Dict, List, Tuple

from utils.ant_rules_io_ext import default_paths, load_results_prices
from utils.strategy_pool_watch import code_6_to_full, codes_6_to_full

DEFAULT_TIMEOUT_SEC = 90.0
DEFAULT_POLL_SEC = 0.5
# 至少有一只有价即可继续；其余缺失股票跳过
DEFAULT_MIN_READY_COUNT = 1
# 满足有价后至少再等这么久，给晚到的行情一点机会
DEFAULT_PARTIAL_MIN_WAIT_SEC = 5.0


def _china_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        try:
            import pytz

            return datetime.now(pytz.timezone("Asia/Shanghai"))
        except Exception:
            return datetime.now()


def _intraday_session_need_open() -> bool:
    """交易时段（约 9:00–15:00）今开盘只能来自实时 tick，不能指望 daily_cache。"""
    now = _china_now()
    try:
        from utils.trading_day import is_tradeday

        if not is_tradeday(now.date()):
            return False
    except Exception:
        if now.weekday() >= 5:
            return False
    t = now.time()
    return dt_time(9, 0) <= t <= dt_time(15, 0)


def _code_ready(snap: Dict[str, Dict[str, Any]], full_code: str) -> bool:
    bucket = snap.get(full_code) or {}
    if float(bucket.get("last_price") or 0) <= 0:
        return False
    if _intraday_session_need_open():
        return float(bucket.get("today_open") or 0) > 0
    return True


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def expected_full_codes(codes_6: List[str]) -> List[str]:
    return codes_6_to_full(codes_6)


def load_live_prices_for_codes(
    codes_6: List[str],
    *,
    root: str | None = None,
) -> Dict[str, Dict[str, float]]:
    """从 results.json 读现价；返回 {code_6: {current, pre_close}}，pre_close 由 KeyPriceCalculator 补。"""
    _, results_path = default_paths(root or _project_root())
    snap = load_results_prices(results_path)
    out: Dict[str, Dict[str, float]] = {}
    for c6 in codes_6 or []:
        c6n = "".join(ch for ch in str(c6 or "") if ch.isdigit())[:6].zfill(6)
        if not c6n or c6n == "000000":
            continue
        full = code_6_to_full(c6n)
        bucket = snap.get(full) or snap.get(full.upper()) or {}
        price = float(bucket.get("last_price") or 0)
        out[c6n] = {
            "current": price,
            "pre_close": 0.0,
            "最新价": price,
            "今开盘": float(bucket.get("today_open") or 0),
            "今日最高": float(bucket.get("today_high") or 0),
            "今日最低": float(bucket.get("today_low") or 0),
        }
    return out


def _pump_ui_events() -> None:
    try:
        from PyQt5.QtWidgets import QApplication

        QApplication.processEvents()
    except Exception:
        pass


def _ready_stats(
    expected: List[str],
    snap: Dict[str, Dict[str, Any]],
    *,
    waited: float,
    results_mtime: str = "",
) -> Tuple[List[str], List[str], Dict[str, Any]]:
    ready_set = {fc for fc in expected if _code_ready(snap, fc)}
    missing = sorted(set(expected) - ready_set)
    no_open: List[str] = []
    if _intraday_session_need_open():
        for fc in expected:
            b = snap.get(fc) or {}
            if float(b.get("last_price") or 0) > 0 and float(b.get("today_open") or 0) <= 0:
                no_open.append(fc)
    stats: Dict[str, Any] = {
        "pool": len(expected),
        "ready": len(ready_set),
        "waited_sec": round(waited, 1),
        "missing_open": no_open[:20],
        "results_mtime": results_mtime,
    }
    return sorted(ready_set), missing, stats


def _ready_enough(ready: int, pool: int, min_ready_count: int) -> bool:
    if pool <= 0:
        return True
    return int(ready) >= max(1, int(min_ready_count))


def wait_results_ready(
    codes_6: List[str],
    *,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    poll_sec: float = DEFAULT_POLL_SEC,
    min_ready_count: int = DEFAULT_MIN_READY_COUNT,
    partial_min_wait_sec: float = DEFAULT_PARTIAL_MIN_WAIT_SEC,
    root: str | None = None,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """
    轮询 results.json，直到股票池行情就绪或超时。

    就绪条件：
    - 全部有价；或
    - 有价数量 >= min_ready_count（默认 1），且至少已等待 partial_min_wait_sec。

    返回 (就绪, 缺失完整代码列表, 统计信息)。
    就绪但缺失非空时，调用方应跳过缺失股票继续生成。
    """
    expected = expected_full_codes(codes_6)
    if not expected:
        return True, [], {"pool": 0, "ready": 0, "waited_sec": 0.0}

    root = root or _project_root()
    rules_path, results_path = default_paths(root)
    results_mtime = ""
    if os.path.isfile(results_path):
        try:
            results_mtime = datetime.fromtimestamp(
                os.path.getmtime(results_path)
            ).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            results_mtime = ""
    start = time.time()
    deadline = start + max(1.0, float(timeout_sec))
    min_wait = max(0.0, float(partial_min_wait_sec))
    need = max(1, int(min_ready_count))
    pool_watch_peak = 0
    pool_watch_empty_hits = 0

    while time.time() < deadline:
        if not os.path.isfile(results_path):
            time.sleep(max(0.2, float(poll_sec)))
            continue
        # 诊断：策略生成写入的 pool 是否仍在（被主程序覆盖则会掉到 0）
        try:
            if os.path.isfile(rules_path):
                from utils.strategy_pool_watch import get_strategy_pool_watch

                pw = get_strategy_pool_watch(root=root)
                pool_watch_peak = max(pool_watch_peak, len(pw))
                if not pw:
                    pool_watch_empty_hits += 1
        except Exception:
            pass
        snap = load_results_prices(results_path)
        waited = time.time() - start
        _ready_list, missing, stats = _ready_stats(
            expected, snap, waited=waited, results_mtime=results_mtime
        )
        stats["pool_watch_peak"] = pool_watch_peak
        stats["pool_watch_empty_hits"] = pool_watch_empty_hits
        ready_n = int(stats["ready"])
        pool_n = int(stats["pool"])
        if ready_n >= pool_n:
            return True, [], stats
        if _ready_enough(ready_n, pool_n, need) and waited >= min_wait:
            stats["partial"] = True
            return True, missing, stats
        _pump_ui_events()
        time.sleep(max(0.2, float(poll_sec)))

    snap = load_results_prices(results_path) if os.path.isfile(results_path) else {}
    waited = time.time() - start
    _ready_list, missing, stats = _ready_stats(
        expected, snap, waited=waited, results_mtime=results_mtime
    )
    stats["pool_watch_peak"] = pool_watch_peak
    stats["pool_watch_empty_hits"] = pool_watch_empty_hits
    if _ready_enough(int(stats["ready"]), int(stats["pool"]), need):
        stats["partial"] = True
        return True, missing, stats
    return False, missing, stats


def overlay_intraday_on_key_points(
    key_points: List[Tuple[str, float]],
    stock_code: str,
    *,
    root: str | None = None,
) -> bool:
    """用 results.json 覆盖/补全 今开盘、今日最高、今日最低（主程序图表初始加载）。"""
    _, results_path = default_paths(root or _project_root())
    snap = load_results_prices(results_path)
    norm = str(stock_code or "").strip().upper()
    bucket = snap.get(norm) or {}
    fields = (
        ("今开盘", "today_open"),
        ("今日最高", "today_high"),
        ("今日最低", "today_low"),
    )
    updated = False
    name_to_idx = {name: i for i, (name, _) in enumerate(key_points)}
    for label, fld in fields:
        val = float(bucket.get(fld) or 0)
        if val <= 0:
            continue
        try:
            from core.utils.security_type import SecurityTypeUtil

            precision = SecurityTypeUtil.get_price_precision(stock_code)
            tick = SecurityTypeUtil.min_price_tick(stock_code)
        except Exception:
            precision = 2
            tick = 0.01
        rounded = round(val, precision)
        if label in name_to_idx:
            idx = name_to_idx[label]
            old = key_points[idx][1]
            if abs(float(old) - rounded) > tick * 0.5:
                key_points[idx] = (label, rounded)
                updated = True
        else:
            key_points.append((label, rounded))
            updated = True
    if updated:
        key_points.sort(
            key=lambda x: x[1] if isinstance(x[1], (int, float)) else float("-inf"),
            reverse=True,
        )
    return updated


def format_not_ready_message(
    missing: List[str],
    stats: Dict[str, Any],
) -> str:
    pool = int(stats.get("pool") or 0)
    ready = int(stats.get("ready") or 0)
    waited = stats.get("waited_sec", "?")
    miss_show = ", ".join(missing[:12])
    if len(missing) > 12:
        miss_show += f", ...（共 {len(missing)} 只）"
    open_miss = stats.get("missing_open") or []
    open_hint = ""
    if open_miss:
        open_hint = (
            f"\n  有现价但缺今开盘: {', '.join(open_miss[:8])}"
            + (" ..." if len(open_miss) > 8 else "")
            + "（交易时段今开来自实时 tick，非 daily_cache）"
        )
    stale_hint = ""
    results_mtime = str(stats.get("results_mtime") or "").strip()
    if results_mtime:
        stale_hint = f"\n  results.json 最后修改: {results_mtime}"
    ratio_pct = ("%.1f" % (100.0 * ready / pool)) if pool > 0 else "0.0"
    race_hint = ""
    pw_peak = int(stats.get("pool_watch_peak") or 0)
    pw_empty = int(stats.get("pool_watch_empty_hits") or 0)
    if ready <= 0 and (pw_peak <= 0 or pw_empty > 0):
        race_hint = (
            "\n  诊断: strategy_pool_watch 在等待期间为空/被冲掉"
            f"（peak={pw_peak}, empty_hits={pw_empty}）；"
            "大 QMT 未订阅股票池，results 不会出现这些现价。"
            "请再点一次「运行」（主程序已改为写 rules 前重读 pool_watch）。"
        )
    return (
        f"[行情未就绪] 已跳过本次生成（可手动重试）\n"
        f"  股票池: {pool} 只 | 有价: {ready} 只（{ratio_pct}%） | 缺失: {len(missing)} 只\n"
        f"  缺失代码: {miss_show or '（无 results.json 或无订阅）'}{open_hint}{stale_hint}{race_hint}\n"
        f"  已等待: {waited}s（至少 {DEFAULT_MIN_READY_COUNT} 只有价才继续）\n"
        f"  请确认: ① 大 QMT 模型交易已启动  ② 内置策略在跑  ③ 再点「运行」"
    )


def format_partial_ready_message(
    missing: List[str],
    stats: Dict[str, Any],
) -> str:
    """有价占比达标，跳过少数缺失股票时的提示。"""
    pool = int(stats.get("pool") or 0)
    ready = int(stats.get("ready") or 0)
    waited = stats.get("waited_sec", "?")
    miss_show = ", ".join(missing[:12])
    if len(missing) > 12:
        miss_show += f", ...（共 {len(missing)} 只）"
    ratio_pct = ("%.1f" % (100.0 * ready / pool)) if pool > 0 else "0.0"
    return (
        f"[行情部分就绪] 有价 {ready}/{pool}（{ratio_pct}%），跳过缺失 {len(missing)} 只后继续生成\n"
        f"  跳过: {miss_show or '—'}\n"
        f"  已等待: {waited}s"
    )
