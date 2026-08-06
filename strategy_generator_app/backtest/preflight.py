# -*- coding: utf-8 -*-
"""回测启动前批量检查/预热日线与 tick 缓存。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:
    from strategy_generator_app.trading_calendar import get_trading_dates_in_range_sorted
except ImportError:
    from trading_calendar import get_trading_dates_in_range_sorted  # type: ignore

try:
    from strategy_generator_app.qmt_mode_config import (
        allow_xtdata_daily_fallback,
        use_on_demand_daily_sync,
    )
except ImportError:
    from qmt_mode_config import (  # type: ignore
        allow_xtdata_daily_fallback,
        use_on_demand_daily_sync,
    )


ProgressFn = Optional[Callable[[str, Optional[int]], None]]


@dataclass
class BacktestPreflightResult:
    ok: bool
    lines: List[str] = field(default_factory=list)
    daily_missing: List[str] = field(default_factory=list)
    tick_missing: List[Tuple[str, str]] = field(default_factory=list)  # (code6, YYYY-MM-DD)
    stats: Dict[str, Any] = field(default_factory=dict)


def backtest_preflight_hint_lines() -> List[str]:
    """回测 Tab 静态说明（随 qmt_mode 变化）。"""
    daily_hint = (
        "缺日线会写入 data/data_sync_requests.json，需大 QMT 模型交易在线处理"
        "（盘中约 1 只/秒、拉至上一交易日）；"
        if use_on_demand_daily_sync()
        else (
            "日线优先 daily_cache，缺失时本机 xtdata 回退；"
            if allow_xtdata_daily_fallback()
            else "日线仅 daily_cache；"
        )
    )
    return [
        "【回测 tick】仅读 data/ticks/日期/代码.parquet（旧 .pkl 可回退）；本地缺失不会向 QMT/xtdata 按需拉取。",
        f"【回测日线】data/daily_cache；{daily_hint}缺 tick 的交易日将无法 tick 级撮合。",
    ]


def _norm_codes(codes: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in codes or []:
        c6 = "".join(ch for ch in str(raw or "") if ch.isdigit())[:6].zfill(6)
        if not c6 or c6 == "000000" or c6 in seen:
            continue
        seen.add(c6)
        out.append(c6)
    return sorted(out)


def _emit_progress(
    progress: ProgressFn,
    msg: str,
    pct: Optional[int] = None,
) -> None:
    if progress:
        progress(msg, pct)
    _pump_ui()


def _intraday_qmt_daily_hint() -> str:
    """盘中大 QMT 按需日线限速消费（含非池），供回测补数。"""
    try:
        from datetime import datetime as _dt

        now = _dt.now()
        if now.weekday() >= 5:
            return ""
        t = now.time()
        if _dt.strptime("09:15", "%H:%M").time() <= t <= _dt.strptime("15:05", "%H:%M").time():
            return "（盘中：按需日线约 1 只/秒、拉至上一交易日；请确认模型交易已运行）"
    except Exception:
        pass
    return ""


def _pending_sync_hint() -> str:
    try:
        from utils.data_sync_request import REQUESTS_PATH, count_pending_sync
    except ImportError:
        from data_sync_request import REQUESTS_PATH, count_pending_sync  # type: ignore

    daily_n, tick_n = count_pending_sync()
    if daily_n or tick_n:
        return (
            f"队列 pending：日线 {daily_n}、tick {tick_n}；"
            f"请求文件 {REQUESTS_PATH}"
        )
    return f"请求文件 {REQUESTS_PATH}（当前无 pending）"


def _pump_ui() -> None:
    try:
        from PyQt5.QtWidgets import QApplication

        QApplication.processEvents()
    except Exception:
        pass


def _tick_pairs_need_wait(
    codes_6: Sequence[str],
    trade_days: Sequence[date],
) -> Tuple[List[Tuple[str, date]], int]:
    """返回 (仍需等待的 code×日, 已判定不可用跳过数)。"""
    try:
        from utils.data_sync_request import _tick_cache_ready, tick_sync_unavailable
    except ImportError:
        from data_sync_request import _tick_cache_ready, tick_sync_unavailable  # type: ignore

    missing: List[Tuple[str, date]] = []
    skipped = 0
    for d in trade_days:
        for c6 in codes_6:
            if _tick_cache_ready(c6, d):
                continue
            if tick_sync_unavailable(c6, d):
                skipped += 1
                continue
            missing.append((c6, d))
    return missing, skipped


def run_backtest_preflight(
    codes_6: Sequence[str],
    start_date: date,
    end_date: date,
    *,
    use_tick_level: bool = True,
    progress: ProgressFn = None,
) -> BacktestPreflightResult:
    """
    回测前批量等待日线落盘，并按需提交/等待 tick。
    不阻断回测：返回仍缺的数据清单，由引擎按日报错。
    """
    codes = _norm_codes(codes_6)
    lines: List[str] = []
    stats: Dict[str, Any] = {
        "codes": len(codes),
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "use_tick_level": use_tick_level,
    }
    if not codes:
        return BacktestPreflightResult(
            ok=False,
            lines=["[preflight] 股票池为空，跳过预热。"],
            stats=stats,
        )

    trade_days = get_trading_dates_in_range_sorted(start_date, end_date)
    stats["trade_days"] = len(trade_days)
    if not trade_days:
        return BacktestPreflightResult(
            ok=False,
            lines=["[preflight] 区间内无交易日，跳过预热。"],
            stats=stats,
        )

    daily_missing: List[str] = []
    _emit_progress(
        progress,
        f"检查日线 daily_cache（{len(codes)} 只，through={end_date}）…",
        5,
    )

    if use_on_demand_daily_sync():
        try:
            from utils.data_sync_request import (
                submit_daily_requests,
                wait_daily_cache_pool,
            )
            from utils.daily_cache_reader import to_full_stock_code
        except ImportError:
            from data_sync_request import (  # type: ignore
                submit_daily_requests,
                wait_daily_cache_pool,
            )
            from daily_cache_reader import to_full_stock_code  # type: ignore

        intraday_hint = _intraday_qmt_daily_hint()
        if intraday_hint:
            lines.append(f"[preflight] {intraday_hint.strip('（）')}")

        fulls = [to_full_stock_code(c) for c in codes]
        submitted = submit_daily_requests(fulls, through_date=end_date)
        if submitted:
            _emit_progress(
                progress,
                f"已向大 QMT 提交日线 {len(submitted)} 只；{_pending_sync_hint()}"
                + (intraday_hint or "（请确认模型交易 shadow 策略在运行）"),
                10,
            )
        else:
            _emit_progress(
                progress,
                f"日线请求已在队列或本地已齐；{_pending_sync_hint()}",
                10,
            )

        def _daily_pool_progress(ready_n: int, total_n: int, _tag: str) -> None:
            pct = 10 + int(35 * ready_n / max(1, total_n))
            _emit_progress(
                progress,
                f"日线 {ready_n}/{total_n} 已就绪；{_pending_sync_hint()}",
                pct,
            )

        ready, daily_missing = wait_daily_cache_pool(
            codes,
            through_date=end_date,
            on_progress=_daily_pool_progress,
        )
        stats["daily_ready"] = len(ready)
        stats["daily_missing"] = len(daily_missing)
        if daily_missing:
            show = ", ".join(daily_missing[:8])
            if len(daily_missing) > 8:
                show += f" …共{len(daily_missing)}只"
            lines.append(
                f"[preflight] 日线仍缺 {len(daily_missing)} 只（{show}）；"
                "可能停牌/新股；请确认大 QMT 已同步。"
            )
        else:
            lines.append(
                f"[preflight] 日线已就绪 {len(ready)} 只（through={end_date}）。"
            )
    else:
        try:
            from utils.data_sync_request import _daily_cache_ready
        except ImportError:
            from data_sync_request import _daily_cache_ready  # type: ignore

        for c6 in codes:
            try:
                from utils.daily_cache_reader import to_full_stock_code

                full = to_full_stock_code(c6)
            except ImportError:
                full = c6
            if not _daily_cache_ready(full, end_date):
                daily_missing.append(c6)
        stats["daily_ready"] = len(codes) - len(daily_missing)
        stats["daily_missing"] = len(daily_missing)
        if daily_missing:
            hint = (
                "可连接 MiniQMT/xtdata 自动补日线。"
                if allow_xtdata_daily_fallback()
                else "请补 daily_cache 或切换 qmt_mode。"
            )
            lines.append(
                f"[preflight] 本地日线缺 {len(daily_missing)} 只（through={end_date}）；{hint}"
            )
        else:
            lines.append(f"[preflight] 本地日线齐全 {len(codes)} 只。")

    tick_missing_pairs: List[Tuple[str, date]] = []
    tick_skipped = 0
    if use_tick_level:
        _emit_progress(
            progress,
            f"检查本地 tick（{len(codes)} 只 × {len(trade_days)} 日）…",
            45,
        )
        tick_need, tick_skipped = _tick_pairs_need_wait(codes, trade_days)
        stats["tick_pairs_need"] = len(tick_need)
        stats["tick_pairs_skipped"] = tick_skipped
        tick_missing_pairs = tick_need
        stats["tick_pairs_missing"] = len(tick_need)
        stats["tick_pairs_skipped"] = tick_skipped
        if not tick_need and tick_skipped == 0:
            lines.append(
                f"[preflight] tick 已齐全（{len(codes)} 只 × {len(trade_days)} 日）。"
            )
        elif tick_skipped and not tick_need:
            lines.append(
                f"[preflight] tick 无缓存 {tick_skipped} 项已标记不可用（如停牌），跳过。"
            )
        elif tick_need:
            sample = ", ".join(
                f"{c6}@{d.isoformat()}" for c6, d in tick_need[:6]
            )
            if len(tick_need) > 6:
                sample += f" …共{len(tick_need)}项"
            lines.append(
                f"[preflight] 本地 tick 缺 {len(tick_need)} 项：{sample}；"
                "回测不会向 QMT/xtdata 按需下载，相关交易日可能无法 tick 撮合。"
            )
        if tick_skipped and tick_need:
            lines.append(
                f"[preflight] tick 另有 {tick_skipped} 项已标记不可用（QMT failed/停牌）。"
            )

    tick_missing_fmt = [
        (c6, d.isoformat()) for c6, d in (tick_missing_pairs or [])
    ]
    ok = not daily_missing and not tick_missing_fmt
    _emit_progress(progress, "预热完成", 100)
    return BacktestPreflightResult(
        ok=ok,
        lines=lines,
        daily_missing=list(daily_missing),
        tick_missing=tick_missing_fmt,
        stats=stats,
    )
