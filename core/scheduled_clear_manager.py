"""定时清仓集中调度：不依赖任务图表分页/暂停 UI 状态，重启后仍可执行。"""
from __future__ import annotations

import math
from datetime import datetime, time as dt_time, timedelta
from typing import Any, Dict, Iterator, List, Optional, Tuple

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from core.utils.security_type import SecurityTypeUtil
from utils.logger import Logger


def _next_trading_day_date(after_date):
    from utils.trading_day import is_tradeday

    d = after_date + timedelta(days=1)
    for _ in range(14):
        if is_tradeday(d):
            return d
        d += timedelta(days=1)
    return after_date + timedelta(days=1)


def resolve_scheduled_clear_effective_date(rule: dict) -> datetime.date:
    s = (rule.get("scheduled_clear_effective_date") or "").strip()
    if s:
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            pass
    now = datetime.now()
    today = now.date()
    try:
        from utils.trading_day import is_tradeday

        is_td = is_tradeday(today)
    except ImportError:
        is_td = today.weekday() < 5
    if is_td and now.time() >= dt_time(15, 0):
        return _next_trading_day_date(today)
    if is_td:
        return today
    return _next_trading_day_date(today)


def scheduled_clear_rule_active_today(rule: dict) -> bool:
    return datetime.now().date() == resolve_scheduled_clear_effective_date(rule)


def scheduled_clear_execution_in_progress(rule: dict) -> bool:
    """定时清仓已启动或正在智能卖出中，避免重复触发。"""
    if rule.get("scheduled_clear_executed", False):
        return True
    if rule.get("smart_sell_active", False):
        return True
    session = rule.get("smart_sell") or {}
    if session.get("active"):
        return True
    return False


def _stock_price_round(value: float, precision: int) -> float:
    multiplier = 10 ** precision
    return math.floor(value * multiplier + 0.5) / multiplier


def calculate_limit_prices(stock_code: str, base_price: float, stock_name: str = "") -> Tuple[float, float]:
    from utils.limit_ratio import get_limit_ratio

    limit_ratio = get_limit_ratio(stock_code, stock_name)
    precision = 3 if SecurityTypeUtil.is_fund(stock_code) else 2
    limit_up = _stock_price_round(base_price * (1 + limit_ratio), precision)
    limit_down = _stock_price_round(base_price * (1 - limit_ratio), precision)
    return limit_up, limit_down


class ScheduledClearManager(QObject):
    """对所有运行中任务的 scheduled_clear 规则做统一时间检查与 tick 触发执行。"""

    rules_updated = pyqtSignal(str)  # stock_code

    def __init__(self, task_manager):
        super().__init__()
        self.task_manager = task_manager
        self.logger = Logger()
        self._last_check_log_time: Dict[str, datetime] = {}
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check_all_times)
        self._timer.start(1000)
        self._tick_connected = False

    def connect_tick_signal(self, qmt_adapter) -> None:
        if not qmt_adapter or not hasattr(qmt_adapter, "tick_data_signal"):
            return
        if self._tick_connected:
            try:
                qmt_adapter.tick_data_signal.disconnect(self.on_tick_data)
            except Exception:
                pass
        qmt_adapter.tick_data_signal.connect(self.on_tick_data)
        self._tick_connected = True

    def _iter_running_scheduled_rules(self) -> Iterator[Tuple[str, dict, str, dict]]:
        running = getattr(self.task_manager, "running_tasks", {}) or {}
        tasks = getattr(self.task_manager, "tasks", {}) or {}
        for task_id in list(running.keys()):
            task = tasks.get(task_id)
            if not task:
                continue
            stock_code = str(task.get("stock_code") or "")
            params = task.get("params") if isinstance(task.get("params"), dict) else {}
            rules = params.get("rules") or []
            if not isinstance(rules, list):
                continue
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                if rule.get("type") != "scheduled_clear":
                    continue
                if not rule.get("enabled", True):
                    continue
                yield task_id, task, stock_code, rule

    def _persist_task_rules(self, task: dict) -> None:
        task_id = task.get("task_id")
        if task_id and task_id in self.task_manager.tasks:
            params = self.task_manager.tasks[task_id].get("params") or {}
            if isinstance(params, dict):
                params["rules"] = task.get("params", {}).get("rules", params.get("rules"))
                self.task_manager.tasks[task_id]["params"] = params
        try:
            self.task_manager.save_tasks(list(self.task_manager.tasks.values()))
        except Exception as e:
            self.logger.warning(f"保存定时清仓规则状态失败: {e}")
        stock_code = str(task.get("stock_code") or "")
        if stock_code:
            self.rules_updated.emit(stock_code)

    def _check_all_times(self) -> None:
        try:
            from utils.qmt_execution_config import use_builtin_order_execution

            if use_builtin_order_execution():
                return
        except Exception:
            pass
        now = datetime.now()
        current_time = now.time()
        for _task_id, task, stock_code, rule in self._iter_running_scheduled_rules():
            if scheduled_clear_execution_in_progress(rule):
                continue
            if not scheduled_clear_rule_active_today(rule):
                if rule.get("pending_tick_execution", False):
                    rule["pending_tick_execution"] = False
                    self._persist_task_rules(task)
                continue

            time_str = rule.get("scheduled_clear_time", "14:56:00")
            try:
                hour, minute, second = map(int, time_str.split(":"))
                rule_time = dt_time(hour, minute, second)
            except Exception:
                rule_time = dt_time(14, 56, 0)

            trigger_price = float(rule.get("price", 0) or 0)
            volume = int(rule.get("volume", 0) or 0)
            if trigger_price <= 0 or volume <= 0:
                continue

            if current_time >= rule_time:
                rule_name = rule.get("name", "未命名")
                time_passed_seconds = (
                    current_time.hour * 3600 + current_time.minute * 60 + current_time.second
                    - (rule_time.hour * 3600 + rule_time.minute * 60 + rule_time.second)
                )
                if time_passed_seconds > 300:
                    if not rule.get("scheduled_clear_executed", False):
                        self.logger.info(
                            f"[{stock_code}] ⏰ [集中调度] 定时清仓规则「{rule_name}」时间已过 "
                            f"(目标: {rule_time.strftime('%H:%M:%S')}, 当前: {current_time.strftime('%H:%M:%S')}, "
                            f"已过 {int(time_passed_seconds / 60)} 分钟)，标记为已错过"
                        )
                        rule["pending_tick_execution"] = False
                        rule["scheduled_clear_executed"] = True
                        rule["scheduled_clear_order_attempted"] = False
                        self._persist_task_rules(task)
                    continue

                if not rule.get("pending_tick_execution", False):
                    self.logger.info(
                        f"[{stock_code}] ⏰ [集中调度] 定时清仓规则「{rule_name}」时间已到！"
                        f" 当前: {current_time.strftime('%H:%M:%S')}, 目标: {rule_time.strftime('%H:%M:%S')}, "
                        f"等待 tick 判断价格 (触发价: {trigger_price:.2f}元)"
                    )
                    rule["pending_tick_execution"] = True
                    self._persist_task_rules(task)
            else:
                time_diff = (
                    rule_time.hour * 3600 + rule_time.minute * 60 + rule_time.second
                    - (current_time.hour * 3600 + current_time.minute * 60 + current_time.second)
                )
                if 0 < time_diff <= 60:
                    rule_id = str(rule.get("id", "unknown"))
                    last_log = self._last_check_log_time.get(rule_id)
                    if last_log is None or (now - last_log).total_seconds() >= 10:
                        self.logger.info(
                            f"[{stock_code}] [集中调度] 定时清仓「{rule.get('name', '未命名')}」检查中: "
                            f"当前 {current_time.strftime('%H:%M:%S')}, 目标 {rule_time.strftime('%H:%M:%S')}, "
                            f"还有 {int(time_diff)} 秒"
                        )
                        self._last_check_log_time[rule_id] = now

    def on_tick_data(self, tick_data: dict) -> None:
        try:
            from utils.qmt_execution_config import use_builtin_order_execution

            if use_builtin_order_execution():
                return
        except Exception:
            pass
        if not tick_data:
            return
        stock_code = str(tick_data.get("stock_code") or "")
        if not stock_code:
            return
        current_price = float(tick_data.get("lastPrice") or 0)

        for _task_id, task, sc, rule in self._iter_running_scheduled_rules():
            if sc != stock_code:
                continue
            session = rule.get("smart_sell") or {}
            if session.get("active") and session.get("standalone"):
                from ui.smart_sell_runner import process_standalone_smart_sell_tick

                process_standalone_smart_sell_tick(self, task, rule, tick_data)
                continue
            if session.get("active"):
                continue
            if scheduled_clear_execution_in_progress(rule):
                continue
            if not rule.get("pending_tick_execution", False):
                continue
            if rule.get("scheduled_clear_executed", False):
                continue
            if not scheduled_clear_rule_active_today(rule):
                rule["pending_tick_execution"] = False
                self._persist_task_rules(task)
                continue

            rule_name = rule.get("name", "未命名")
            trigger_price = float(rule.get("price", 0) or 0)

            if current_price > 0 and current_price < trigger_price:
                self.logger.info(
                    f"[{stock_code}] ✅ [集中调度] 执行定时清仓「{rule_name}」"
                    f" (现价: {current_price:.2f} < 触发价: {trigger_price:.2f})"
                )
                rule["pending_tick_execution"] = False
                rule["scheduled_clear_order_attempted"] = True
                rule["smart_sell_active"] = True
                session = rule.get("smart_sell") or {}
                if session.get("active"):
                    from ui.smart_sell_runner import process_standalone_smart_sell_tick

                    process_standalone_smart_sell_tick(self, task, rule, tick_data)
                    self._persist_task_rules(task)
                    continue
                from ui.smart_sell_runner import try_start_smart_sell_for_scheduled_clear

                if try_start_smart_sell_for_scheduled_clear(self, task, rule, tick_data):
                    self._persist_task_rules(task)
                    continue
                rule["smart_sell_active"] = False
                self._execute_rule(task, rule, tick_data, current_price)
            elif current_price > 0:
                self.logger.warning(
                    f"[{stock_code}] ⚠️ [集中调度] 定时清仓「{rule_name}」价格不满足 "
                    f"(现价: {current_price:.2f} >= 触发价: {trigger_price:.2f})，取消执行"
                )
                rule["pending_tick_execution"] = False
                rule["scheduled_clear_executed"] = True
                self._persist_task_rules(task)

    def _get_position_volume(self, stock_code: str) -> int:
        qmt = getattr(self.task_manager, "qmt_adapter", None)
        if not qmt or not hasattr(qmt, "get_stock_position"):
            return 0
        try:
            position = qmt.get_stock_position(stock_code)
            if not position or not isinstance(position, dict):
                return 0
            volume = int(position.get("volume", 0) or 0)
            can_use_volume = position.get("can_use_volume", 0)
            if can_use_volume is not None and int(can_use_volume) < 0:
                return max(0, volume + int(can_use_volume))
            if "can_use_volume" in position:
                return max(0, int(can_use_volume or 0))
            return max(0, volume)
        except Exception as e:
            self.logger.error(f"[{stock_code}] 获取持仓失败: {e}")
            return 0

    def _get_position_cost(self, stock_code: str) -> float:
        qmt = getattr(self.task_manager, "qmt_adapter", None)
        if not qmt or not hasattr(qmt, "get_stock_position"):
            return 0.0
        try:
            position = qmt.get_stock_position(stock_code)
            if not position or not isinstance(position, dict):
                return 0.0
            return float(position.get("open_price") or 0)
        except Exception as e:
            self.logger.error(f"[{stock_code}] 获取持仓成本失败: {e}")
            return 0.0

    def _execute_rule(
        self,
        task: dict,
        rule: dict,
        tick_data: dict,
        current_price: float,
    ) -> None:
        stock_code = str(task.get("stock_code") or "")
        rule_name = rule.get("name", "未命名")
        qmt = getattr(self.task_manager, "qmt_adapter", None)
        if not qmt:
            self.logger.error(f"[{stock_code}] 定时清仓失败：无 QMT 适配器")
            rule["scheduled_clear_executed"] = True
            self._persist_task_rules(task)
            return

        position_volume = self._get_position_volume(stock_code)
        if position_volume <= 0:
            self.logger.warning(f"[{stock_code}] 定时清仓「{rule_name}」失败：无可用持仓")
            rule["scheduled_clear_executed"] = True
            self._persist_task_rules(task)
            return

        volume = int(rule.get("volume", 0) or 0)
        sell_volume = min(volume, position_volume)
        precision = 3 if SecurityTypeUtil.is_fund(stock_code) else 2
        slippage = 0.001 if precision == 3 else 0.01

        prev_close = float(tick_data.get("lastClose") or 0)
        if prev_close <= 0:
            prev_close = float(
                (getattr(self.task_manager, "pre_close_prices", {}) or {}).get(stock_code, 0) or 0
            )
        limit_down_price = 0.0
        if prev_close > 0:
            _, limit_down_price = calculate_limit_prices(
                stock_code, prev_close, task.get("stock_name", "")
            )

        sell_price = current_price
        bid_list = tick_data.get("bidPrice") or []
        base_price = None
        try:
            if isinstance(bid_list, (list, tuple)) and len(bid_list) > 0:
                bp0 = float(bid_list[0] or 0)
                if bp0 > 0:
                    base_price = bp0
        except Exception:
            base_price = None
        if base_price is None:
            base_price = current_price
        sell_price = round(base_price - slippage, precision)
        if sell_price <= 0:
            sell_price = round(current_price, precision)
        if limit_down_price > 0 and (current_price <= limit_down_price or sell_price < limit_down_price):
            sell_price = round(limit_down_price, precision)

        try:
            from core.smart_sell import direct_sell_order_strategy_name

            order_id = qmt.trade(
                stock_code=stock_code,
                order_type="sell",
                price=sell_price,
                volume=sell_volume,
                strategy_name=direct_sell_order_strategy_name("scheduled_clear"),
            )
            rule["scheduled_clear_order_attempted"] = True
            if order_id and str(order_id) not in ("-1", "0", ""):
                self.logger.info(
                    f"[{stock_code}] ✅ 定时清仓下单成功: {sell_volume}股 @ {sell_price:.{precision}f} (订单: {order_id})"
                )
                self._play_trade_sound()
            else:
                self.logger.warning(
                    f"[{stock_code}] ⚠️ 定时清仓下单返回无效订单号: {order_id}"
                )
            rule["scheduled_clear_executed"] = True
            self._persist_task_rules(task)
        except Exception as e:
            self.logger.error(f"[{stock_code}] 定时清仓执行失败: {e}", exc_info=True)
            rule["scheduled_clear_order_attempted"] = True
            rule["scheduled_clear_executed"] = True
            self._persist_task_rules(task)

    def _play_trade_sound(self) -> None:
        try:
            mw = getattr(self.task_manager, "main_window", None)
            ext = getattr(mw, "ext", None) if mw else None
            if ext and hasattr(ext, "play_trade_sound"):
                ext.play_trade_sound()
        except Exception:
            pass
