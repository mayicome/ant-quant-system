# -*- coding: utf-8 -*-
"""实盘智能卖出执行器：挂限价单、改价、Phase B 强平。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from core.smart_sell import (
    PHASE_CLOSING,
    PHASE_FORCE,
    current_tranche_volume,
    init_session,
    is_session_complete,
    should_requote,
    should_use_smart_sell,
    smart_sell_mode_label_cn,
    smart_sell_order_strategy_name,
    update_session_tick,
)
from core.trading_config import non_early_order_sell_smart_sell_enabled


class SmartSellRunner:
    """挂在 StockChartWidget 上，处理单只股票的智能卖出会话。"""

    def __init__(self, widget):
        self.widget = widget

    @property
    def logger(self):
        return self.widget.logger

    @property
    def stock_code(self) -> str:
        return getattr(self.widget, "stock_code", "") or ""

    def _smart_sell_enabled(self) -> bool:
        return non_early_order_sell_smart_sell_enabled(default=False)

    def _get_qmt(self):
        tm = getattr(self.widget, "task_manager", None)
        if tm and hasattr(tm, "qmt_adapter"):
            return tm.qmt_adapter
        return None

    def _limit_prices(self) -> tuple:
        up = float(getattr(self.widget, "limit_up_price", 0) or 0)
        down = float(getattr(self.widget, "limit_down_price", 0) or 0)
        return up, down

    def _pre_close(self) -> float:
        return float(getattr(self.widget, "pre_close_price", 0) or getattr(self.widget, "yesterday_close", 0) or 0)

    def _position_cost(self) -> float:
        cost = float(getattr(self.widget, "position_cost", 0) or 0)
        if cost > 0:
            return cost
        qmt = self._get_qmt()
        if qmt and hasattr(qmt, "get_stock_position"):
            try:
                pos = qmt.get_stock_position(self.stock_code)
                if isinstance(pos, dict):
                    return float(pos.get("open_price") or 0)
            except Exception:
                pass
        return 0.0

    def try_intercept_execute_trade(self, rule: dict, trade_info: dict, tick_data: dict) -> bool:
        """若已启动智能卖出则返回 True，调用方应跳过常规 _execute_trade。"""
        if trade_info.get("type") != "sell":
            return False
        rule_type = (rule.get("type") or "").strip()
        is_early = bool(trade_info.get("early_order") or rule.get("early_order"))
        session = rule.get("smart_sell") or {}
        if session.get("active"):
            return True
        if not should_use_smart_sell(
            rule_type,
            enabled=self._smart_sell_enabled(),
            is_early_order=is_early,
        ):
            return False
        return self._start_session(rule, trade_info, tick_data)

    def on_tick(self, tick_data: dict) -> None:
        if not tick_data:
            return
        for rule in getattr(self.widget, "rules", []) or []:
            session = rule.get("smart_sell") or {}
            if session.get("active"):
                self._process_rule_tick(rule, tick_data, session)

    def _start_session(self, rule: dict, trade_info: dict, tick_data: dict) -> bool:
        volume = int(trade_info.get("volume") or rule.get("volume") or 0)
        if volume <= 0:
            return False
        p_ref = float(trade_info.get("price") or rule.get("price") or tick_data.get("lastPrice") or 0)
        if p_ref <= 0:
            return False
        from core.smart_sell import resolve_smart_sell_p_ref

        rule_type = (rule.get("type") or "").strip()
        last_px = float(tick_data.get("lastPrice") or p_ref)
        intent_px = float(rule.get("price") or trade_info.get("price") or 0)
        p_ref = resolve_smart_sell_p_ref(rule_type, intent_px, last_px)
        limit_up, limit_down = self._limit_prices()
        trigger_info = trade_info.get("reason") or rule.get("name") or "智能卖出"
        tick_dt = self._tick_dt(tick_data)
        session = init_session(
            self.stock_code,
            p_ref,
            volume,
            limit_up=limit_up,
            limit_down=limit_down,
            trigger_info=str(trigger_info),
            rule_type=(rule.get("type") or "").strip(),
            scheduled_clear_time=str(
                rule.get("scheduled_clear_time") or trade_info.get("scheduled_clear_time") or ""
            ),
            cost_price=self._position_cost(),
            session_start_dt=tick_dt,
        )
        session["initial_position"] = int(getattr(self.widget, "position_volume", 0) or 0)
        session["order_id"] = None
        session["force_attempted"] = False
        session["closing_attempted"] = False
        rule["smart_sell"] = session
        rule["smart_sell_active"] = True
        self.logger.info(
            f"[{self.stock_code}] 智能卖出启动: {trigger_info}, P_ref={p_ref:.4f}, "
            f"量={volume}, 拆档={session.get('tranches')}"
        )
        self._process_rule_tick(rule, tick_data, session)
        self.widget._save_rules()
        return True

    def _tick_dt(self, tick_data: dict):
        t = tick_data.get("time")
        if isinstance(t, datetime):
            return t
        return datetime.now()

    def _process_rule_tick(self, rule: dict, tick_data: dict, session: dict) -> None:
        if not session.get("active"):
            return
        tick_dt = self._tick_dt(tick_data)
        phase, quote, mode = update_session_tick(
            session,
            tick_data,
            tick_dt,
            pre_close=self._pre_close(),
        )
        vol = current_tranche_volume(session)
        if vol <= 0 and is_session_complete(session):
            self._finish_rule(rule, session)
            return

        if phase == PHASE_FORCE:
            self._force_sell(rule, session, tick_data, vol)
            return
        if phase == PHASE_CLOSING:
            self._closing_sell(rule, session, tick_data, vol, quote)
            return

        if not should_requote(session, quote, tick_dt, float(session.get("strength") or 0)):
            self._detect_position_fill(rule, session)
            if is_session_complete(session):
                self._finish_rule(rule, session)
            return

        cancel_result, filled_vol = _cancel_smart_sell_order(
            self._get_qmt(), session, self.stock_code, self.logger, vol
        )
        if cancel_result == "failed":
            self._detect_position_fill(rule, session)
            if is_session_complete(session):
                self._finish_rule(rule, session)
            return
        if cancel_result == "filled":
            self._detect_position_fill(rule, session, explicit_fill=filled_vol)
            if is_session_complete(session):
                self._finish_rule(rule, session)
            return
        self._place_limit_sell(rule, session, quote, vol, tick_data, mode)
        session["last_requote_dt"] = tick_dt
        session["last_requote_strength"] = float(session.get("strength") or 0)
        session["requote_count"] = int(session.get("requote_count") or 0) + 1
        self._detect_position_fill(rule, session)
        if is_session_complete(session):
            self._finish_rule(rule, session)

    def _place_limit_sell(
        self,
        rule: dict,
        session: dict,
        quote: float,
        volume: int,
        tick_data: dict,
        mode: str,
    ) -> None:
        qmt = self._get_qmt()
        if not qmt or volume <= 0:
            return
        rule_type = (rule.get("type") or "").strip()
        reason = smart_sell_order_strategy_name(mode, rule_type=rule_type)
        mode_cn = smart_sell_mode_label_cn(mode)
        try:
            order_id = qmt.trade(
                stock_code=self.stock_code,
                order_type="sell",
                price=quote,
                volume=volume,
                strategy_name=reason,
            )
            if order_id and str(order_id) not in ("-1", "0", ""):
                session["order_id"] = str(order_id)
                session["last_quote"] = quote
                self.logger.info(
                    f"[{self.stock_code}] 智能卖出挂单: {volume}股 @ {quote:.4f} ({mode_cn}) 订单={order_id}"
                )
                self.widget._play_trade_sound()
            else:
                self.logger.warning(f"[{self.stock_code}] 智能卖出挂单失败: order_id={order_id}")
        except Exception as e:
            self.logger.error(f"[{self.stock_code}] 智能卖出挂单异常: {e}", exc_info=True)

    def _cancel_open_order(self, session: dict) -> Tuple[str, int]:
        vol = current_tranche_volume(session)
        return _cancel_smart_sell_order(
            self._get_qmt(), session, self.stock_code, self.logger, vol
        )

    def _force_sell(self, rule: dict, session: dict, tick_data: dict, volume: int) -> None:
        if volume <= 0:
            if is_session_complete(session):
                self._finish_rule(rule, session)
            return
        if session.get("force_attempted"):
            self._detect_position_fill(rule, session)
            if is_session_complete(session):
                self._finish_rule(rule, session)
            return
        cancel_result, filled_vol = self._cancel_open_order(session)
        if cancel_result == "failed":
            self._detect_position_fill(rule, session)
            if is_session_complete(session):
                self._finish_rule(rule, session)
            return
        if cancel_result == "filled":
            self._detect_position_fill(rule, session, explicit_fill=filled_vol)
            if is_session_complete(session):
                self._finish_rule(rule, session)
            return
        from core.smart_sell import compute_quote

        session["phase"] = PHASE_FORCE
        quote = compute_quote(self.stock_code, session, tick_data, "WEAK")
        qmt = self._get_qmt()
        if not qmt:
            return
        rule_type = (rule.get("type") or "").strip()
        strategy_name = smart_sell_order_strategy_name(
            "WEAK",
            rule_type=rule_type,
            phase_tag="强平",
        )
        try:
            order_id = qmt.trade(
                stock_code=self.stock_code,
                order_type="sell",
                price=quote,
                volume=volume,
                strategy_name=strategy_name,
            )
            session["force_attempted"] = True
            session["order_id"] = str(order_id) if order_id else None
            session["last_quote"] = quote
            self.logger.info(
                f"[{self.stock_code}] 智能卖出 Phase B 强平: {volume}股 @ {quote:.4f} 订单={order_id}"
            )
            self.widget._play_trade_sound()
        except Exception as e:
            self.logger.error(f"[{self.stock_code}] 智能卖出强平失败: {e}", exc_info=True)

    def _closing_sell(self, rule: dict, session: dict, tick_data: dict, volume: int, quote: float) -> None:
        if volume <= 0:
            if is_session_complete(session):
                self._finish_rule(rule, session)
            return
        if session.get("closing_attempted"):
            self._detect_position_fill(rule, session)
            if is_session_complete(session):
                self._finish_rule(rule, session)
            return
        cancel_result, filled_vol = self._cancel_open_order(session)
        if cancel_result in ("failed", "filled"):
            if cancel_result == "filled":
                self._detect_position_fill(rule, session, explicit_fill=filled_vol)
            else:
                self._detect_position_fill(rule, session)
            if is_session_complete(session):
                self._finish_rule(rule, session)
            return
        qmt = self._get_qmt()
        if not qmt:
            return
        rule_type = (rule.get("type") or "").strip()
        strategy_name = smart_sell_order_strategy_name(
            session.get("mode") or "WEAK",
            rule_type=rule_type,
            phase_tag="收盘竞价",
        )
        try:
            order_id = qmt.trade(
                stock_code=self.stock_code,
                order_type="sell",
                price=quote,
                volume=volume,
                strategy_name=strategy_name,
            )
            session["closing_attempted"] = True
            session["order_id"] = str(order_id) if order_id else None
            session["last_quote"] = quote
            self.logger.info(
                f"[{self.stock_code}] 智能卖出收盘竞价: {volume}股 @ {quote:.4f} 订单={order_id}"
            )
        except Exception as e:
            self.logger.error(f"[{self.stock_code}] 智能卖出收盘竞价失败: {e}", exc_info=True)

    def _detect_position_fill(self, rule: dict, session: dict, explicit_fill: int = 0) -> None:
        quote = float(session.get("last_quote") or session.get("p_ref") or 0)
        if explicit_fill > 0:
            _record_fill_increment(session, explicit_fill, quote)
        initial = int(session.get("initial_position") or 0)
        current = int(getattr(self.widget, "position_volume", 0) or 0)
        target_filled = int(session.get("filled_volume") or 0)
        total_planned = sum(int(x) for x in (session.get("tranches") or []))
        sold = max(0, initial - current)
        new_fill = sold - target_filled
        if new_fill > 0:
            _record_fill_increment(session, new_fill, quote)
            self.logger.info(
                f"[{self.stock_code}] 智能卖出成交检测: +{new_fill}股 @ ~{quote:.4f}, "
                f"累计 {session.get('filled_volume')}/{total_planned}"
            )
        elif explicit_fill <= 0 and initial > 0 and current <= 0 and target_filled < initial:
            remainder = initial - target_filled
            _record_fill_increment(session, remainder, quote)
            self.logger.info(
                f"[{self.stock_code}] 智能卖出成交检测(持仓已清): +{remainder}股 @ ~{quote:.4f}, "
                f"累计 {session.get('filled_volume')}/{total_planned}"
            )
        if int(session.get("tranche_remaining") or 0) <= 0 and not is_session_complete(session):
            session["order_id"] = None
            session["force_attempted"] = False
            session["closing_attempted"] = False

    def _finish_rule(self, rule: dict, session: dict) -> None:
        from core.smart_sell import average_fill_price

        self._cancel_open_order(session)
        session["active"] = False
        rule["smart_sell_active"] = False
        avg_px = average_fill_price(session)
        filled = int(session.get("filled_volume") or 0)
        rule["executed"] = True
        rule["executed_price"] = avg_px if avg_px > 0 else float(session.get("p_ref") or 0)
        rule["executed_volume"] = filled
        rule["executed_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rule["executed_reason"] = "smart_sell"
        if rule.get("type") == "scheduled_clear":
            rule["scheduled_clear_executed"] = True
            rule["scheduled_clear_order_attempted"] = True
        self.logger.info(
            f"[{self.stock_code}] 智能卖出完成: {rule.get('name', '')} "
            f"{filled}股 均价={rule['executed_price']:.4f}"
        )
        self.widget._save_rules()
        try:
            self.widget.update_chart()
        except Exception:
            pass


def try_start_smart_sell_for_scheduled_clear(
    manager,
    task: dict,
    rule: dict,
    tick_data: dict,
) -> bool:
    """定时清仓集中调度入口：若启用智能卖出则启动会话并返回 True。"""
    if not non_early_order_sell_smart_sell_enabled(default=False):
        return False
    stock_code = str(task.get("stock_code") or "")
    chart = None
    try:
        mw = getattr(manager.task_manager, "main_window", None)
        ext = getattr(mw, "ext", None) if mw else None
        tcv = getattr(ext, "tasks_charts_view", None) if ext else None
        if tcv and hasattr(tcv, "get_chart_widget"):
            chart = tcv.get_chart_widget(stock_code)
    except Exception:
        chart = None

    if chart and hasattr(chart, "smart_sell_runner"):
        volume = int(rule.get("volume") or 0)
        trigger_price = float(rule.get("price") or tick_data.get("lastPrice") or 0)
        trade_info = {
            "type": "sell",
            "price": trigger_price,
            "volume": volume,
            "reason": f"定时清仓-{rule.get('name', '')}",
        }
        started = chart.smart_sell_runner._start_session(rule, trade_info, tick_data)
        if started:
            rule["scheduled_clear_order_attempted"] = True
            rule["pending_tick_execution"] = False
            manager._persist_task_rules(task)
        return started

    return _start_standalone_scheduled_clear_smart_sell(manager, task, rule, tick_data)


def _start_standalone_scheduled_clear_smart_sell(
    manager,
    task: dict,
    rule: dict,
    tick_data: dict,
) -> bool:
    stock_code = str(task.get("stock_code") or "")
    volume = int(rule.get("volume") or 0)
    intent_px = float(rule.get("price") or tick_data.get("lastPrice") or 0)
    last_px = float(tick_data.get("lastPrice") or intent_px)
    from core.smart_sell import resolve_smart_sell_p_ref

    p_ref = resolve_smart_sell_p_ref("scheduled_clear", intent_px, last_px)
    if volume <= 0 or p_ref <= 0:
        return False
    prev_close = float(tick_data.get("lastClose") or 0)
    limit_up, limit_down = 0.0, 0.0
    if prev_close > 0:
        from core.scheduled_clear_manager import calculate_limit_prices

        limit_up, limit_down = calculate_limit_prices(
            stock_code, prev_close, task.get("stock_name", "")
        )
    cost_price = 0.0
    if hasattr(manager, "_get_position_cost"):
        cost_price = float(manager._get_position_cost(stock_code) or 0)
    tick_dt = tick_data.get("time")
    if not isinstance(tick_dt, datetime):
        tick_dt = datetime.now()
    session = init_session(
        stock_code,
        p_ref,
        volume,
        limit_up=limit_up,
        limit_down=limit_down,
        trigger_info=f"定时清仓-{rule.get('name', '')}",
        rule_type="scheduled_clear",
        scheduled_clear_time=str(rule.get("scheduled_clear_time") or ""),
        cost_price=cost_price,
        session_start_dt=tick_dt,
    )
    session["standalone"] = True
    session["initial_position"] = manager._get_position_volume(stock_code)
    session["order_id"] = None
    rule["smart_sell"] = session
    rule["smart_sell_active"] = True
    rule["scheduled_clear_order_attempted"] = True
    rule["pending_tick_execution"] = False
    manager._persist_task_rules(task)
    process_standalone_smart_sell_tick(manager, task, rule, tick_data)
    return True


def process_standalone_smart_sell_tick(
    manager,
    task: dict,
    rule: dict,
    tick_data: dict,
) -> None:
    session = rule.get("smart_sell") or {}
    if not session.get("active") or not session.get("standalone"):
        return
    stock_code = str(task.get("stock_code") or "")
    logger = manager.logger
    if _try_finish_standalone_if_sold_out(manager, task, rule, session, stock_code, logger):
        return

    tick_dt = tick_data.get("time")
    if not isinstance(tick_dt, datetime):
        tick_dt = datetime.now()
    prev_close = float(tick_data.get("lastClose") or 0)
    phase, quote, mode = update_session_tick(session, tick_data, tick_dt, pre_close=prev_close)
    vol = current_tranche_volume(session)
    qmt = getattr(manager.task_manager, "qmt_adapter", None)

    if vol <= 0 and is_session_complete(session):
        _finish_standalone_rule(manager, task, rule, session)
        return

    available = manager._get_position_volume(stock_code)
    if available <= 0:
        if _try_finish_standalone_if_sold_out(manager, task, rule, session, stock_code, logger):
            return
        logger.info(f"[{stock_code}] 智能卖出(独立) 无可用持仓，结束会话")
        _finish_standalone_rule(manager, task, rule, session)
        return
    vol = min(vol, available)

    if phase == PHASE_FORCE:
        if session.get("force_attempted"):
            _detect_standalone_fill(manager, task, rule, session, stock_code)
            if is_session_complete(session) or _try_finish_standalone_if_sold_out(
                manager, task, rule, session, stock_code, logger
            ):
                if session.get("active"):
                    _finish_standalone_rule(manager, task, rule, session)
            return
        cancel_result, filled_vol = _cancel_smart_sell_order(qmt, session, stock_code, logger, vol)
        if cancel_result == "failed":
            _detect_standalone_fill(manager, task, rule, session, stock_code)
            manager._persist_task_rules(task)
            return
        if cancel_result == "filled":
            _detect_standalone_fill(manager, task, rule, session, stock_code, explicit_fill=filled_vol)
            if is_session_complete(session) or _try_finish_standalone_if_sold_out(
                manager, task, rule, session, stock_code, logger
            ):
                if session.get("active"):
                    _finish_standalone_rule(manager, task, rule, session)
            else:
                manager._persist_task_rules(task)
            return
        from core.smart_sell import compute_quote

        session["phase"] = PHASE_FORCE
        fq = compute_quote(stock_code, session, tick_data, "WEAK")
        rule_type = (rule.get("type") or "").strip()
        strategy_name = smart_sell_order_strategy_name(
            "WEAK",
            rule_type=rule_type,
            phase_tag="强平",
        )
        try:
            oid = qmt.trade(
                stock_code=stock_code,
                order_type="sell",
                price=fq,
                volume=vol,
                strategy_name=strategy_name,
            )
            session["force_attempted"] = True
            session["order_id"] = str(oid) if oid else None
            session["last_quote"] = fq
            logger.info(f"[{stock_code}] 智能卖出(独立) Phase B: {vol}股 @ {fq:.4f}")
            if _reject_insufficient_position(qmt, session, stock_code, vol, logger):
                _try_finish_standalone_if_sold_out(manager, task, rule, session, stock_code, logger)
        except Exception as e:
            logger.error(f"[{stock_code}] 智能卖出(独立)强平失败: {e}")
        manager._persist_task_rules(task)
        return

    if not should_requote(session, quote, tick_dt, float(session.get("strength") or 0)):
        _detect_standalone_fill(manager, task, rule, session, stock_code)
        if is_session_complete(session) or _try_finish_standalone_if_sold_out(
            manager, task, rule, session, stock_code, logger
        ):
            if session.get("active"):
                _finish_standalone_rule(manager, task, rule, session)
        return

    if session.get("order_id"):
        cancel_result, filled_vol = _cancel_smart_sell_order(qmt, session, stock_code, logger, vol)
        if cancel_result == "failed":
            _detect_standalone_fill(manager, task, rule, session, stock_code)
            manager._persist_task_rules(task)
            return
        if cancel_result == "filled":
            _detect_standalone_fill(manager, task, rule, session, stock_code, explicit_fill=filled_vol)
            if is_session_complete(session) or _try_finish_standalone_if_sold_out(
                manager, task, rule, session, stock_code, logger
            ):
                if session.get("active"):
                    _finish_standalone_rule(manager, task, rule, session)
            else:
                manager._persist_task_rules(task)
            return
    rule_type = (rule.get("type") or "").strip()
    strategy_name = smart_sell_order_strategy_name(
        mode, rule_type=rule_type
    )
    try:
        oid = qmt.trade(
            stock_code=stock_code,
            order_type="sell",
            price=quote,
            volume=vol,
            strategy_name=strategy_name,
        )
        session["order_id"] = str(oid) if oid else None
        session["last_quote"] = quote
        session["last_requote_dt"] = tick_dt
        session["last_requote_strength"] = float(session.get("strength") or 0)
        session["requote_count"] = int(session.get("requote_count") or 0) + 1
        logger.info(
            f"[{stock_code}] 智能卖出(独立)挂单: {vol}股 @ {quote:.4f} "
            f"({smart_sell_mode_label_cn(mode)})"
        )
        if _reject_insufficient_position(qmt, session, stock_code, vol, logger):
            _try_finish_standalone_if_sold_out(manager, task, rule, session, stock_code, logger)
    except Exception as e:
        logger.error(f"[{stock_code}] 智能卖出(独立)挂单失败: {e}")
    _detect_standalone_fill(manager, task, rule, session, stock_code)
    if is_session_complete(session) or _try_finish_standalone_if_sold_out(
        manager, task, rule, session, stock_code, logger
    ):
        if session.get("active"):
            _finish_standalone_rule(manager, task, rule, session)
    manager._persist_task_rules(task)


def _record_fill_increment(session: dict, fill_vol: int, quote: float) -> None:
    from core.smart_sell import record_fill

    fill_vol = int(fill_vol)
    if fill_vol <= 0:
        return
    initial = int(session.get("initial_position") or 0)
    target = int(session.get("filled_volume") or 0)
    planned = sum(int(x) for x in (session.get("tranches") or []))
    cap = initial if initial > 0 else planned
    if cap > 0:
        fill_vol = min(fill_vol, max(0, cap - target))
    if fill_vol > 0:
        record_fill(session, fill_vol, quote)


def _reject_insufficient_position(qmt, session: dict, stock_code: str, volume: int, logger) -> bool:
    """下单后若委托被柜台拒单(可用不足)，返回 True。"""
    oid = session.get("order_id")
    if not oid or not qmt:
        return False
    inspect = getattr(qmt, "inspect_order_for_cancel", None)
    if not inspect:
        return False
    price = float(session.get("last_quote") or session.get("p_ref") or 0)
    status, _sysid, _traded = inspect(
        stock_code,
        oid,
        price=price,
        volume=volume,
        is_sell=True,
    )
    if status == "gone":
        logger.info(f"[{stock_code}] 智能卖出(独立) 委托已结束(可能拒单): order={oid}")
        session["order_id"] = None
        return True
    return False


def _try_finish_standalone_if_sold_out(
    manager,
    task: dict,
    rule: dict,
    session: dict,
    stock_code: str,
    logger,
) -> bool:
    if not session.get("active"):
        return False
    initial = int(session.get("initial_position") or 0)
    filled = int(session.get("filled_volume") or 0)
    planned = sum(int(x) for x in (session.get("tranches") or []))
    current = manager._get_position_volume(stock_code)
    quote = float(session.get("last_quote") or session.get("p_ref") or 0)

    if initial > 0 and current <= 0 and filled < initial:
        _record_fill_increment(session, initial - filled, quote)
        filled = int(session.get("filled_volume") or 0)
        logger.info(
            f"[{stock_code}] 智能卖出(独立) 持仓已清空，补记成交 {filled}股"
        )

    if planned > 0 and filled >= planned:
        _finish_standalone_rule(manager, task, rule, session)
        return True
    if initial > 0 and current <= 0 and filled > 0:
        _finish_standalone_rule(manager, task, rule, session)
        return True
    return False


def _cancel_smart_sell_order(
    qmt,
    session: dict,
    stock_code: str,
    logger,
    volume: int,
) -> Tuple[str, int]:
    """
    撤销智能卖出在途委托。返回 (status, fill_volume)，status 为 none | cancelled | filled | failed。
    撤单前解析 order_sysid；撤单失败或委托仍有效时不清理 order_id，避免重复下单。
    """
    oid = session.get("order_id")
    if not oid or not qmt:
        return "none", 0

    price = float(session.get("last_quote") or session.get("p_ref") or 0)
    inspect = getattr(qmt, "inspect_order_for_cancel", None)
    status, sysid, traded = ("unknown", None, 0)
    if inspect:
        status, sysid, traded = inspect(
            stock_code,
            oid,
            price=price,
            volume=volume,
            is_sell=True,
        )

    if status == "filled":
        if sysid:
            session["order_sysid"] = sysid
        fill_vol = int(traded or volume or 0)
        logger.info(
            f"[{stock_code}] 智能卖出撤单跳过: 委托已成交 (sysid={sysid or oid}, vol={fill_vol})"
        )
        session["order_id"] = None
        return "filled", fill_vol
    if status == "gone":
        logger.info(f"[{stock_code}] 智能卖出撤单跳过: 委托已结束 (sysid={sysid or oid})")
        session["order_id"] = None
        return "cancelled", 0

    cancel_id = str(sysid or oid).strip()
    if sysid and str(oid).strip() != cancel_id:
        logger.info(f"[{stock_code}] 智能卖出撤单ID解析: {oid} -> {cancel_id}")

    try:
        ok = qmt.cancel_order(cancel_id, stock_code=stock_code)
    except Exception as e:
        logger.warning(f"[{stock_code}] 智能卖出撤单异常: {e}")
        return "failed", 0

    if not ok:
        logger.warning(f"[{stock_code}] 智能卖出撤单请求失败，保留在途委托 {cancel_id}")
        return "failed", 0

    if inspect:
        status2, _sysid2, traded2 = inspect(
            stock_code,
            cancel_id,
            price=price,
            volume=volume,
            is_sell=True,
        )
        if status2 == "cancelable":
            logger.warning(
                f"[{stock_code}] 智能卖出撤单后委托仍有效 {cancel_id}，暂缓改价重挂"
            )
            return "failed", 0
        if status2 == "filled":
            session["order_id"] = None
            return "filled", int(traded2 or volume or 0)

    session["order_id"] = None
    return "cancelled", 0


def _cancel_standalone_order(qmt, session: dict, stock_code: str, logger) -> None:
    vol = current_tranche_volume(session)
    _cancel_smart_sell_order(qmt, session, stock_code, logger, vol)


def _detect_standalone_fill(
    manager,
    task: dict,
    rule: dict,
    session: dict,
    stock_code: str,
    explicit_fill: int = 0,
) -> None:
    quote = float(session.get("last_quote") or session.get("p_ref") or 0)
    if explicit_fill > 0:
        _record_fill_increment(session, explicit_fill, quote)
    initial = int(session.get("initial_position") or 0)
    current = manager._get_position_volume(stock_code)
    target = int(session.get("filled_volume") or 0)
    sold = max(0, initial - current)
    new_fill = sold - target
    if new_fill > 0:
        _record_fill_increment(session, new_fill, quote)
    elif explicit_fill <= 0 and initial > 0 and current <= 0 and target < initial:
        _record_fill_increment(session, initial - target, quote)


def _finish_standalone_rule(manager, task: dict, rule: dict, session: dict) -> None:
    from core.smart_sell import average_fill_price

    stock_code = str(task.get("stock_code") or "")
    qmt = getattr(manager.task_manager, "qmt_adapter", None)
    _cancel_standalone_order(qmt, session, stock_code, manager.logger)
    session["active"] = False
    rule["smart_sell_active"] = False
    rule["scheduled_clear_executed"] = True
    rule["scheduled_clear_order_attempted"] = True
    rule["pending_tick_execution"] = False
    rule["executed"] = True
    rule["executed_volume"] = int(session.get("filled_volume") or 0)
    rule["executed_price"] = average_fill_price(session) or float(session.get("p_ref") or 0)
    manager.logger.info(
        f"[{stock_code}] 智能卖出(独立)完成: {rule.get('name', '')} "
        f"{rule['executed_volume']}股 均价={rule['executed_price']:.4f}"
    )
    manager._persist_task_rules(task)
    manager._play_trade_sound()
