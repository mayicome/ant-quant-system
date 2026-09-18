# -*- coding: utf-8 -*-
"""回测编排：仅在有 tick 的交易日回放；盘中用 tick 聚 1m bar。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from event_tick_mvp.config import MvpConfig
from event_tick_mvp.data import (
    ProjectDailyStore,
    list_tick_days,
    load_industry_map,
    load_name_map,
    load_ticks_1m,
    load_universe_codes,
    replay_codes,
)
from event_tick_mvp.layer2_event import build_watch_list, filter_universe
from event_tick_mvp.layer3_filter import filter_to_buy_monitor
from event_tick_mvp.layer4_lifecycle import (
    apply_fill,
    eod_update_positions,
    fill_from_intent,
    mark_to_market_nav,
    on_bar_for_positions,
    try_open_buy,
)
from event_tick_mvp.metrics import BacktestMetrics, compute_metrics
from event_tick_mvp.types import AccountState, FillRecord, WatchItem


@dataclass
class BacktestResult:
    fills: List[FillRecord] = field(default_factory=list)
    daily_nav: List[Dict] = field(default_factory=list)
    watch_sizes: List[Dict] = field(default_factory=list)
    metrics: Optional[BacktestMetrics] = None
    notes: List[str] = field(default_factory=list)
    tick_days: List[date] = field(default_factory=list)
    board_posthoc: Optional[Dict] = None


def _mark_prices_from_bars(
    bars_by_code: Dict[str, pd.DataFrame],
) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for c, df in bars_by_code.items():
        if df is None or df.empty or "close" not in df.columns:
            continue
        out[c] = float(df["close"].iloc[-1])
    return out


def _iter_merged_timeline(
    bars_by_code: Dict[str, pd.DataFrame],
) -> List[Tuple[datetime, Dict[str, dict]]]:
    """合并多标的 1m bar → [(ts, {code: rowdict})]。"""
    by_ts: Dict[datetime, Dict[str, dict]] = {}
    for code, df in bars_by_code.items():
        if df is None or df.empty or "datetime" not in df.columns:
            continue
        # 尽量向量化：只取需要的列
        cols = [c for c in ("datetime", "open", "high", "low", "close", "ask1", "bid1", "amount") if c in df.columns]
        sub = df.loc[:, cols]
        for row in sub.itertuples(index=False):
            ts = getattr(row, "datetime")
            if isinstance(ts, pd.Timestamp):
                ts = ts.to_pydatetime()
            bucket = by_ts.get(ts)
            if bucket is None:
                bucket = {}
                by_ts[ts] = bucket
            bucket[code] = row._asdict() if hasattr(row, "_asdict") else {
                k: getattr(row, k) for k in cols
            }
    return sorted(by_ts.items(), key=lambda x: x[0])


class MvpEngine:
    def __init__(self, cfg: Optional[MvpConfig] = None):
        self.cfg = cfg or MvpConfig()
        self.daily_store = ProjectDailyStore()
        self.industry_map = load_industry_map()
        self.names = load_name_map()
        self.account = AccountState(
            cash=float(self.cfg.initial_cash), nav=float(self.cfg.initial_cash)
        )
        self.fills: List[FillRecord] = []
        self.pending_watch: List[WatchItem] = []
        # 递延：code -> (intent, expire_date, kind)
        self.defer_buys: Dict[str, Tuple] = {}
        self.defer_sells: Dict[str, Tuple] = {}
        self.daily_nav: List[Dict] = []
        self.watch_sizes: List[Dict] = []
        self._board_tagger = None
        if bool(self.cfg.tag_board_rank_posthoc):
            from event_tick_mvp.board_posthoc import BoardRankTagger

            self._board_tagger = BoardRankTagger()

    def _universe(self) -> List[str]:
        raw = load_universe_codes()
        return filter_universe(
            raw,
            names=self.names,
            min_listed_days=int(self.cfg.list_min_trading_days),
        )

    def _record_fill(self, fill: FillRecord) -> None:
        if self._board_tagger is not None and fill.side == "buy":
            try:
                self._board_tagger.tag_buy_fill(fill)
            except Exception:
                pass
        self.fills.append(fill)

    def build_watch_for_day(self, as_of: date, universe: Sequence[str]) -> List[WatchItem]:
        watch = build_watch_list(
            as_of,
            universe,
            self.daily_store.load_daily,
            cfg=self.cfg,
            industry_map=self.industry_map,
        )
        self.pending_watch = watch
        self.watch_sizes.append({"date": as_of.isoformat(), "n": len(watch)})
        return watch

    def _load_bars(self, codes: Sequence[str], trade_date: date) -> Dict[str, pd.DataFrame]:
        import os
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from utils.tick_data_cache import tick_cache_path, tick_cache_path_legacy_pkl

        cand: List[str] = []
        for c in codes:
            c6 = str(c).split(".")[0].zfill(6)[-6:]
            if os.path.isfile(tick_cache_path(c6, trade_date)) or os.path.isfile(
                tick_cache_path_legacy_pkl(c6, trade_date)
            ):
                cand.append(c6)

        out: Dict[str, pd.DataFrame] = {}
        if not cand:
            return out

        def _one(code: str) -> Tuple[str, Optional[pd.DataFrame]]:
            try:
                df = load_ticks_1m(code, trade_date)
            except Exception:
                return code, None
            return code, df

        # I/O 密集：线程池加速 parquet 读取
        workers = min(16, max(4, (os.cpu_count() or 8)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_one, c) for c in cand]
            for fut in as_completed(futs):
                code, df = fut.result()
                if df is not None and not df.empty:
                    out[code] = df
        return out

    def run_day(
        self,
        trade_date: date,
        *,
        universe: Sequence[str],
        defer_expire: Optional[date] = None,
    ) -> None:
        # 使用昨日收盘产出的 watch（pending_watch）；若空则仅管理持仓
        if defer_expire is None:
            defer_expire = trade_date
        watch = list(self.pending_watch)
        pos_codes = self.account.position_codes()
        # 阶段1：仅对有 tick 的 watch 做廉价开盘额过滤 → buy_monitor
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import os

        from event_tick_mvp.data import open_window_amount_from_ticks
        from utils.tick_data_cache import tick_cache_file_ready

        watch_with_tick = [
            w
            for w in watch
            if tick_cache_file_ready(w.code, trade_date)
        ]
        amt_map: Dict[str, float] = {}
        if watch_with_tick:
            workers = min(16, max(4, (os.cpu_count() or 8)))

            def _amt(code: str) -> Tuple[str, float]:
                return code, open_window_amount_from_ticks(
                    code, trade_date, int(self.cfg.open_amt_window_min)
                )

            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = [ex.submit(_amt, w.code) for w in watch_with_tick]
                for fut in as_completed(futs):
                    c, a = fut.result()
                    amt_map[c] = a

        # 构造伪 bars：仅 amount_cum 一列供 L3 使用
        lite_bars: Dict[str, pd.DataFrame] = {}
        for c, a in amt_map.items():
            # open_window_amount 优先读 amount_cum 末值
            ts0 = datetime.combine(trade_date, datetime.strptime("09:30", "%H:%M").time())
            ts1 = ts0 + timedelta(minutes=int(self.cfg.open_amt_window_min) - 1)
            lite_bars[c] = pd.DataFrame(
                {
                    "datetime": [ts0, ts1],
                    "amount_cum": [0.0, float(a)],
                    "close": [0.0, 0.0],
                }
            )

        daily_by_code: Dict[str, pd.DataFrame] = {}
        for w in watch_with_tick:
            daily_by_code[w.code] = self.daily_store.load_daily(w.code, trade_date)

        buy_monitor = filter_to_buy_monitor(
            watch_with_tick,
            trade_date=trade_date,
            account=self.account,
            cfg=self.cfg,
            bars_by_code=lite_bars,
            daily_by_code=daily_by_code,
            names=self.names,
        )
        # 现金最多约 1/max_weight 仓；max_buy_replay>0 时按超跌深度截断（0=全候选基线）
        max_replay = int(getattr(self.cfg, "max_buy_replay", 0) or 0)
        if max_replay > 0 and len(buy_monitor) > max_replay:
            buy_monitor = sorted(
                buy_monitor, key=lambda w: (float(w.drawdown), float(w.volume_ratio))
            )[:max_replay]
        monitor_map = {w.code: w for w in buy_monitor}

        # 阶段2：仅对 buy_monitor ∪ 持仓 ∪ 递延 加载完整 1m
        need_codes = replay_codes(
            list(monitor_map.keys())
            + list(self.defer_buys.keys())
            + list(self.defer_sells.keys()),
            pos_codes,
        )
        bars_by_code = self._load_bars(need_codes, trade_date) if need_codes else {}

        bought_today: set = set()
        window_end = datetime.combine(trade_date, datetime.strptime("09:30", "%H:%M").time()) + timedelta(
            minutes=int(self.cfg.open_amt_window_min)
        )

        timeline = _iter_merged_timeline(bars_by_code)
        last_prices: Dict[str, float] = {}

        for ts, bucket in timeline:
            # 更新最新价
            for code, row in bucket.items():
                last_prices[code] = float(row.get("close") or 0.0)

            allow_new_buy = ts >= window_end

            # 递延卖优先
            for code in list(self.defer_sells.keys()):
                intent, expire_d, _ = self.defer_sells[code]
                if trade_date > expire_d:
                    self.defer_sells.pop(code, None)
                    continue
                row = bucket.get(code)
                if not row:
                    continue
                px = float(row.get("close") or 0.0)
                bid1 = row.get("bid1")
                if bid1 is not None and float(bid1) <= 0:
                    continue
                intent2 = intent
                intent2.deferred = False
                intent2.created_at = ts
                intent2.reason = str((intent.meta or {}).get("exit_reason") or intent.reason)
                fill = fill_from_intent(intent2, price=px, cfg=self.cfg)
                if fill:
                    apply_fill(self.account, fill)
                    fill.meta["deferred"] = True
                    self._record_fill(fill)
                    self.defer_sells.pop(code, None)

            # 递延买
            for code in list(self.defer_buys.keys()):
                if code in bought_today or code in self.account.positions:
                    self.defer_buys.pop(code, None)
                    continue
                intent, expire_d, item = self.defer_buys[code]
                if trade_date > expire_d:
                    self.defer_buys.pop(code, None)
                    continue
                if not allow_new_buy:
                    continue
                row = bucket.get(code)
                if not row:
                    continue
                px = float(row.get("close") or 0.0)
                ask1 = row.get("ask1")
                intent2 = try_open_buy(
                    item,
                    last_price=px,
                    ts=ts,
                    account=self.account,
                    cfg=self.cfg,
                    ask1=float(ask1) if ask1 is not None else None,
                    name=self.names.get(code, ""),
                )
                if intent2 is None or intent2.deferred:
                    continue
                fill = fill_from_intent(intent2, price=px, cfg=self.cfg)
                if fill:
                    apply_fill(
                        self.account,
                        fill,
                        industry=str((intent2.meta or {}).get("industry") or ""),
                    )
                    self._record_fill(fill)
                    bought_today.add(code)
                    self.defer_buys.pop(code, None)

            # 新开仓
            if allow_new_buy:
                for code, row in bucket.items():
                    if code in bought_today or code in self.account.positions:
                        continue
                    item = monitor_map.get(code)
                    if item is None:
                        continue
                    px = float(row.get("close") or 0.0)
                    ask1 = row.get("ask1")
                    intent = try_open_buy(
                        item,
                        last_price=px,
                        ts=ts,
                        account=self.account,
                        cfg=self.cfg,
                        ask1=float(ask1) if ask1 is not None else None,
                        name=self.names.get(code, ""),
                    )
                    if intent is None:
                        continue
                    if intent.deferred:
                        self.defer_buys[code] = (intent, defer_expire, item)
                        continue
                    fill = fill_from_intent(intent, price=px, cfg=self.cfg)
                    if fill:
                        apply_fill(
                            self.account,
                            fill,
                            industry=str(
                                (intent.meta or {}).get("industry") or item.industry
                            ),
                        )
                        self._record_fill(fill)
                        bought_today.add(code)

            # 持仓卖出
            bid1_map = {
                c: float(r["bid1"])
                for c, r in bucket.items()
                if r.get("bid1") is not None
            }
            sell_intents = on_bar_for_positions(
                self.account,
                last_prices=last_prices,
                trade_date=trade_date,
                ts=ts,
                cfg=self.cfg,
                bid1_by_code=bid1_map,
                names=self.names,
            )
            for intent in sell_intents:
                if intent.code in self.defer_sells:
                    continue
                px = float(last_prices.get(intent.code) or 0.0)
                if intent.deferred:
                    self.defer_sells[intent.code] = (intent, defer_expire, "sell")
                    continue
                fill = fill_from_intent(intent, price=px, cfg=self.cfg)
                if fill:
                    apply_fill(self.account, fill)
                    self._record_fill(fill)

        if not last_prices:
            last_prices = _mark_prices_from_bars(bars_by_code)
        eod_update_positions(self.account, trade_date=trade_date, last_prices=last_prices)
        nav = mark_to_market_nav(self.account, last_prices)
        self.daily_nav.append(
            {
                "date": trade_date.isoformat(),
                "nav": nav,
                "cash": float(self.account.cash),
                "n_pos": len(self.account.positions),
                "n_monitor": len(buy_monitor),
                "n_watch_tick": len(watch_with_tick),
            }
        )

        # 当日收盘后跑第 2 层，供下一 tick 日使用
        self.build_watch_for_day(trade_date, universe)

    def finalize(self) -> BacktestResult:
        m = compute_metrics(self.fills)
        # 简单组合指标
        if len(self.daily_nav) >= 2:
            navs = [float(x["nav"]) for x in self.daily_nav]
            rets = []
            for i in range(1, len(navs)):
                if navs[i - 1] > 0:
                    rets.append(navs[i] / navs[i - 1] - 1.0)
            if rets:
                import math
                import statistics

                mean_r = statistics.mean(rets)
                std_r = statistics.pstdev(rets) if len(rets) > 1 else 0.0
                days = len(rets)
                m.ann_return = (navs[-1] / navs[0]) ** (252.0 / max(days, 1)) - 1.0 if navs[0] > 0 else None
                peak = navs[0]
                max_dd = 0.0
                for v in navs:
                    peak = max(peak, v)
                    if peak > 0:
                        max_dd = min(max_dd, v / peak - 1.0)
                m.max_dd = max_dd
                if std_r > 1e-12:
                    m.sharpe = (mean_r / std_r) * math.sqrt(252.0)
                if m.max_dd and m.max_dd < 0 and m.ann_return is not None:
                    m.calmar = m.ann_return / abs(m.max_dd)
        # 盈亏比粗算：配对同一 code 的买→卖
        buy_px: Dict[str, FillRecord] = {}
        wins = []
        losses = []
        for f in self.fills:
            if f.side == "buy":
                buy_px[f.code] = f
            elif f.side == "sell" and f.code in buy_px:
                b = buy_px.pop(f.code)
                pnl = (f.price - b.price) / b.price if b.price else 0.0
                if pnl >= 0:
                    wins.append(pnl)
                else:
                    losses.append(pnl)
        if wins or losses:
            m.win_rate = len(wins) / max(len(wins) + len(losses), 1)
        if wins and losses:
            m.payoff_ratio = (sum(wins) / len(wins)) / abs(sum(losses) / len(losses))
        elif wins and not losses:
            m.payoff_ratio = float("inf")
        board_posthoc = None
        if bool(self.cfg.tag_board_rank_posthoc):
            try:
                from event_tick_mvp.board_posthoc import summarize_board_posthoc

                board_posthoc = summarize_board_posthoc(self.fills)
            except Exception as e:
                board_posthoc = {"error": str(e)}
        return BacktestResult(
            fills=list(self.fills),
            daily_nav=list(self.daily_nav),
            watch_sizes=list(self.watch_sizes),
            metrics=m,
            board_posthoc=board_posthoc,
        )


def run_on_tick_days(
    *,
    cfg: Optional[MvpConfig] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
    max_days: Optional[int] = None,
    min_tick_files: int = 1000,
    progress: bool = True,
) -> BacktestResult:
    """仅在本地 data/ticks 有数据的日期上回测。

    默认 min_tick_files=1000：跳过早期稀疏落盘日（每天仅几十只），
    当前数据约从 2026-06-29 起才有全日覆盖。
    """
    cfg = cfg or MvpConfig()
    days = list_tick_days(start, end, min_files=int(min_tick_files or 0))
    if max_days and len(days) > max_days:
        days = days[: max_days]
    eng = MvpEngine(cfg)
    uni = eng._universe()
    notes = [
        f"tick_days={len(days)} {days[0] if days else None}..{days[-1] if days else None}",
        f"min_tick_files={min_tick_files}",
        f"universe={len(uni)}",
        f"bar_freq={cfg.bar_freq} (ticks aggregated to 1m)",
        f"max_buy_replay={int(cfg.max_buy_replay)} (0=full candidates)",
        f"board_rank_posthoc={bool(cfg.tag_board_rank_posthoc)}",
    ]
    if not days:
        res = eng.finalize()
        res.notes = notes + ["no tick days found"]
        return res

    # 首日前一自然日粗略：用首日之前的日线生成初始 watch（不要求有 tick）
    first = days[0]
    pre = first - timedelta(days=1)
    # 找有日线的最近日：直接用 first 前若干自然日尝试
    for back in range(1, 10):
        pre = first - timedelta(days=back)
        if pre.weekday() < 5:
            break
    if progress:
        print(f"[event_tick_mvp] seed watch as_of~{pre} universe={len(uni)}", flush=True)
    eng.build_watch_for_day(pre, uni)
    if progress:
        print(f"[event_tick_mvp] seed watch size={len(eng.pending_watch)}", flush=True)

    for i, d in enumerate(days):
        if progress:
            print(
                f"[event_tick_mvp] {i+1}/{len(days)} {d} "
                f"watch={len(eng.pending_watch)} pos={len(eng.account.positions)} "
                f"nav={eng.account.nav:.0f}",
                flush=True,
            )
        next_day = days[i + 1] if i + 1 < len(days) else d
        eng.run_day(d, universe=uni, defer_expire=next_day)

    res = eng.finalize()
    res.notes = notes
    res.tick_days = days
    return res
