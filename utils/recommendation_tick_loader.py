# -*- coding: utf-8 -*-
"""
推荐值（弹性买卖）tick 加载：

1. 优先读本地 data/ticks/{YYYYMMDD}/{code6}.parquet|pkl
2. 缺失时经 data_sync_request 请求大 QMT（download_history_data）落盘后再读
3. 默认不走 miniQMT xtquant.xtdata（allow_xtdata_fallback=False）

盘中：当日未收盘前不请求「今日」按需同步（全日 tick 尚未齐），只读已有落盘；
历史日（T-1..）有缓存即可正常计算。
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd


def code6_from_stock(stock_code: str) -> str:
    raw = str(stock_code or "").strip().upper()
    if "." in raw:
        raw = raw.split(".", 1)[0]
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return ""
    return digits[:6].zfill(6) if len(digits) >= 6 else digits.zfill(6)


def filter_session_ticks(tick_df: pd.DataFrame) -> pd.DataFrame:
    """与推荐值计算器历史口径一致：连续竞价 9:30–11:30、13:00–15:00。"""
    if tick_df is None or len(tick_df) == 0 or "datetime" not in tick_df.columns:
        return tick_df
    dt = tick_df["datetime"]
    trading_mask = (
        ((dt.dt.hour == 9) & (dt.dt.minute >= 30))
        | (dt.dt.hour == 10)
        | ((dt.dt.hour == 11) & (dt.dt.minute <= 30))
        | ((dt.dt.hour >= 13) & (dt.dt.hour < 15))
    )
    out = tick_df[trading_mask]
    if "lastPrice" in out.columns:
        out = out[out["lastPrice"] > 0]
    return out


def _session_closed_for_full_day(day: date, now: Optional[datetime] = None) -> bool:
    """收盘后（含盘后约 15:05）才认为当日可能有完整 tick 可按需同步。"""
    now = now or datetime.now()
    if day < now.date():
        return True
    if day > now.date():
        return False
    return now.time() >= time(15, 5)


def _miss_hint(code6: str, day: date, *, skipped_ondemand: bool = False) -> str:
    ymd = day.strftime("%Y%m%d")
    if skipped_ondemand:
        return (
            f"本地无 data/ticks/{ymd}/{code6}.parquet；"
            f"盘中不按需同步未收盘日 {day.isoformat()}（请用已落盘的 T-1.. 历史日）"
        )
    try:
        from utils.data_sync_request import use_on_demand_qmt_sync
        from utils.qmt_execution_config import get_qmt_mode
    except ImportError:
        return (
            f"本地无 data/ticks/{ymd}/{code6}.parquet；"
            "且无法请求大 QMT 按需同步（请确认 qmt_mode=builtin/standalone 且模型交易已启动）"
        )
    mode = get_qmt_mode()
    if use_on_demand_qmt_sync():
        return (
            f"本地无 data/ticks/{ymd}/{code6}.parquet，"
            f"大 QMT 按需同步未在超时内落盘（qmt_mode={mode}）；"
            "请确认模型交易已启动并已绑定 download_history_data。"
            "不使用 miniQMT xtdata。"
        )
    return (
        f"本地无 data/ticks/{ymd}/{code6}.parquet，"
        f"当前 qmt_mode={mode} 未启用大 QMT 按需同步；"
        "请改为 builtin/standalone，或先跑 tick 全量同步落盘。不使用 miniQMT xtdata。"
    )


def load_ticks_for_trading_days(
    stock_code: str,
    trading_days: Iterable[date],
    *,
    allow_xtdata_fallback: bool = False,
    allow_on_demand: bool = True,
    use_memory_cache: bool = True,
    on_demand_timeout_sec: float = 20.0,
) -> Tuple[Dict[date, pd.DataFrame], List[str]]:
    """
    按交易日加载 tick，供推荐值买卖计算器共享。

    先纯本地读；仅当本地 0 天且允许按需时，才对已收盘缺失日请求大 QMT。
    盘中有 T-1.. 落盘即可立即计算，不因个别缺失日阻塞。

    Returns:
        (tick_data_by_day, messages)  messages 为可读提示（含失败原因）
    """
    from utils.tick_data_cache import load_tick_data, tick_cache_path

    code6 = code6_from_stock(stock_code)
    tick_data: Dict[date, pd.DataFrame] = {}
    messages: List[str] = []
    days = list(trading_days or [])
    if not code6:
        messages.append(f"无效股票代码: {stock_code!r}")
        return tick_data, messages
    if not days:
        messages.append("未指定交易日")
        return tick_data, messages

    def _ingest(trading_day: date, raw: Any, day_s: str) -> None:
        if raw is None or len(raw) == 0:
            return
        tick_df = raw if isinstance(raw, pd.DataFrame) else pd.DataFrame(raw)
        if "datetime" not in tick_df.columns and "time" in tick_df.columns:
            tick_df = tick_df.sort_values("time")
        elif "datetime" in tick_df.columns:
            tick_df = tick_df.sort_values("datetime")
        tick_df = filter_session_ticks(tick_df)
        if len(tick_df) > 0:
            tick_data[trading_day] = tick_df
            print(f"  [ok] 成功加载 {len(tick_df)} 条tick数据")
        else:
            msg = f"{day_s} 过滤连续竞价时段后无有效 tick"
            print(f"  [warn] {msg}")
            messages.append(msg)

    print(f"\n开始加载 {len(days)} 个交易日的tick数据（本地缓存优先）...")
    missing_closed: List[date] = []
    for i, trading_day in enumerate(days, 1):
        day_s = trading_day.strftime("%Y-%m-%d")
        path = tick_cache_path(code6, trading_day)
        print(f"[{i}/{len(days)}] 加载 {day_s} <- {path}")
        try:
            raw = load_tick_data(
                code6,
                trading_day,
                use_memory_cache=use_memory_cache,
                allow_xtdata_fallback=False,
                allow_on_demand=False,
            )
            if raw is None or len(raw) == 0:
                if _session_closed_for_full_day(trading_day):
                    missing_closed.append(trading_day)
                hint = _miss_hint(
                    code6,
                    trading_day,
                    skipped_ondemand=not _session_closed_for_full_day(trading_day),
                )
                print(f"  [warn] {day_s} 本地无数据")
                messages.append(hint)
                continue
            _ingest(trading_day, raw, day_s)
        except Exception as e:
            msg = f"加载 {day_s} 失败: {e}"
            print(f"  [fail] {msg}")
            messages.append(msg)

    if (
        allow_on_demand
        and not tick_data
        and missing_closed
        and not allow_xtdata_fallback
    ):
        print(
            f"本地 0 天，尝试大 QMT 按需同步 {len(missing_closed)} 个已收盘日"
            f"（timeout={on_demand_timeout_sec}s/日）..."
        )
        for trading_day in missing_closed:
            day_s = trading_day.strftime("%Y-%m-%d")
            try:
                raw = load_tick_data(
                    code6,
                    trading_day,
                    use_memory_cache=use_memory_cache,
                    allow_xtdata_fallback=False,
                    allow_on_demand=True,
                    on_demand_timeout_sec=on_demand_timeout_sec,
                )
                if raw is None or len(raw) == 0:
                    print(f"  [warn] {day_s} 按需同步后仍无数据")
                    continue
                print(f"[{day_s}] 按需同步命中")
                _ingest(trading_day, raw, day_s)
            except Exception as e:
                msg = f"按需同步 {day_s} 失败: {e}"
                print(f"  [fail] {msg}")
                messages.append(msg)
    elif allow_xtdata_fallback and not tick_data:
        # 显式打开时才走 xtdata（推荐值默认关闭）
        for trading_day in days:
            if trading_day in tick_data:
                continue
            day_s = trading_day.strftime("%Y-%m-%d")
            try:
                raw = load_tick_data(
                    code6,
                    trading_day,
                    use_memory_cache=use_memory_cache,
                    allow_xtdata_fallback=True,
                    allow_on_demand=False,
                )
                _ingest(trading_day, raw, day_s)
            except Exception as e:
                messages.append(f"xtdata 回退 {day_s} 失败: {e}")

    print(f"\n总共加载了 {len(tick_data)} 个交易日的数据")
    if not tick_data and messages:
        print("错误：推荐值无法计算 — " + messages[0])
    return tick_data, messages
