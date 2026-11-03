# -*- coding: utf-8 -*-
"""延迟激活集中调度：不依赖图表分页，运行中任务 tick 驱动观察与到点激活。

另：未决议 clear_branch 在 T 点后用墙钟补一次结算——贴涨停时价不变不再发 tick，
否则分支永不 resolved、清仓腿进不了武装。
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterator, Optional, Set, Tuple

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from core.rule_activation import _parse_hms, process_activation_for_task
from core.clear_branch import (
    clear_branch_is_resolved,
    has_clear_branch,
    process_clear_branch_for_task,
)
from utils.logger import Logger


class RuleActivationManager(QObject):
    """对所有运行中任务中带 activation 的单点卖/突破卖规则做 tick 观察与激活。"""

    rules_updated = pyqtSignal(str)  # stock_code

    def __init__(self, task_manager):
        super().__init__()
        self.task_manager = task_manager
        self.logger = Logger()
        self._tick_connected = False
        # 墙钟补结算：价不变无 tick 时，到点仍能决议 clear_branch
        self._wallclock_missing_logged: Set[str] = set()
        self._wallclock_timer = QTimer(self)
        self._wallclock_timer.setInterval(1000)
        self._wallclock_timer.timeout.connect(self._on_wallclock_clear_branch)
        self._wallclock_timer.start()

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

    def _iter_running_tasks(self) -> Iterator[Tuple[str, dict]]:
        running = getattr(self.task_manager, "running_tasks", {}) or {}
        tasks = getattr(self.task_manager, "tasks", {}) or {}
        for task_id in list(running.keys()):
            task = tasks.get(task_id)
            if task:
                yield task_id, task

    def _persist_task(self, task: dict) -> None:
        task_id = task.get("task_id")
        if task_id and task_id in self.task_manager.tasks:
            params = self.task_manager.tasks[task_id].get("params") or {}
            if isinstance(params, dict):
                params["rules"] = (task.get("params") or {}).get("rules", params.get("rules"))
                self.task_manager.tasks[task_id]["params"] = params
        try:
            self.task_manager.save_tasks(list(self.task_manager.tasks.values()))
        except Exception as e:
            self.logger.warning(f"保存延迟激活规则状态失败: {e}")

    def _log_and_sync_after_change(
        self, task: dict, stock_code: str, *, source: str = ""
    ) -> None:
        sc = str(task.get("stock_code") or stock_code)
        src_tag = f" source={source}" if source else ""
        for r in (task.get("params") or {}).get("rules") or []:
            if not isinstance(r, dict):
                continue
            act = r.get("activation")
            if isinstance(act, dict) and act.get("resolved"):
                rname = r.get("name", "未命名")
                if act.get("activated"):
                    self.logger.info(
                        f"[{sc}] 延迟激活：规则「{rname}」已激活 "
                        f"(activate_at={act.get('activate_at')}, reached={act.get('reached')})"
                        f"{src_tag}"
                    )
                else:
                    self.logger.info(
                        f"[{sc}] 延迟激活：规则「{rname}」已跳过 "
                        f"(activate_at={act.get('activate_at')}, reached={act.get('reached')})"
                        f"{src_tag}"
                    )
            cb = r.get("clear_branch")
            if isinstance(cb, dict) and cb.get("resolved"):
                rname = r.get("name", "未命名")
                self.logger.info(
                    f"[{sc}] 清仓T点分支：规则「{rname}」"
                    f"choice={cb.get('choice')} sealed={cb.get('sealed_at_T')} "
                    f"at={r.get('clear_branch_at')}{src_tag}"
                )
        try:
            sync = getattr(self.task_manager, "_sync_rules_armed_if_builtin", None)
            if callable(sync):
                sync()
        except Exception as e:
            self.logger.debug(f"清仓分支后同步武装失败: {e}")
        self.rules_updated.emit(sc)

    def on_tick_data(self, tick_data: dict) -> None:
        if not tick_data:
            return
        stock_code = str(tick_data.get("stock_code") or "")
        if not stock_code:
            return
        for _task_id, task in self._iter_running_tasks():
            if str(task.get("stock_code") or "") != stock_code:
                continue
            params = task.get("params") if isinstance(task.get("params"), dict) else {}
            if params.get("task_paused", False):
                continue
            changed = False
            if process_activation_for_task(task, tick_data):
                changed = True
            if process_clear_branch_for_task(task, tick_data):
                changed = True
            if changed:
                self._persist_task(task)
                self._log_and_sync_after_change(task, stock_code)

    def _pending_due_clear_branch_rules(self, task: dict, now_t) -> list:
        """运行中任务里：有 clear_branch_at、未 resolved、本地时刻已到点的规则。"""
        params = task.get("params") if isinstance(task.get("params"), dict) else {}
        rules = params.get("rules")
        if not isinstance(rules, list):
            return []
        due = []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            if not rule.get("enabled", True):
                continue
            if rule.get("executed", False):
                continue
            if not has_clear_branch(rule):
                continue
            if clear_branch_is_resolved(rule):
                continue
            activate_at = _parse_hms(rule.get("clear_branch_at"))
            # 无法解析的 clear_branch_at：与 tick 路径一致，视为应立即决议
            if activate_at is not None and now_t < activate_at:
                continue
            due.append(rule)
        return due

    def _build_tick_from_price_cache(self, stock_code: str) -> Optional[dict]:
        """用 task_manager 行情缓存构造与东财 tick 同结构的快照（含墙钟 time）。"""
        tm = self.task_manager
        try:
            price = float((getattr(tm, "latest_prices", {}) or {}).get(stock_code) or 0)
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0:
            return None
        try:
            last_close = float(
                (getattr(tm, "pre_close_prices", {}) or {}).get(stock_code) or 0
            )
        except (TypeError, ValueError):
            last_close = 0.0
        open_px = hi_px = lo_px = 0.0
        # 若 BuiltinPricePoller 仍挂着盘中高低，一并带上（与正常 tick 一致）
        poller = (
            getattr(tm, "builtin_price_poller", None)
            or getattr(tm, "_builtin_price_poller", None)
            or getattr(tm, "_price_poller", None)
        )
        if poller is None:
            qmt = getattr(tm, "qmt_adapter", None) or getattr(tm, "broker_gateway", None)
            if qmt is not None:
                poller = (
                    getattr(qmt, "_builtin_price_poller", None)
                    or getattr(qmt, "builtin_price_poller", None)
                )
        if poller is not None:
            iday = (getattr(poller, "_last_intraday", {}) or {}).get(stock_code) or {}
            try:
                open_px = float(iday.get("open") or 0)
                hi_px = float(iday.get("high") or 0)
                lo_px = float(iday.get("low") or 0)
                if last_close <= 0:
                    last_close = float(iday.get("last_close") or 0)
            except (TypeError, ValueError):
                pass
        return {
            "stock_code": stock_code,
            "lastPrice": price,
            "lastClose": last_close,
            "open": open_px,
            "high": hi_px,
            "low": lo_px,
            # 与 builtin_price_feed._build_tick_data 一致：东财路径亦不带卖一
            "askPrice": [],
            "bidPrice": [],
            "askVol": [],
            "bidVol": [],
            "time": datetime.now(),
            "wallclock_clear_branch": True,
        }

    def _on_wallclock_clear_branch(self) -> None:
        """每秒扫描：到点仍未决议的 clear_branch，用缓存价补一次结算。"""
        now_t = datetime.now().time()
        for task_id, task in self._iter_running_tasks():
            params = task.get("params") if isinstance(task.get("params"), dict) else {}
            if params.get("task_paused", False):
                continue
            due_rules = self._pending_due_clear_branch_rules(task, now_t)
            if not due_rules:
                continue
            stock_code = str(task.get("stock_code") or "")
            if not stock_code:
                continue
            tick_data = self._build_tick_from_price_cache(stock_code)
            if tick_data is None:
                for rule in due_rules:
                    key = f"{task_id}|{rule.get('id') or rule.get('name')}|no_price"
                    if key in self._wallclock_missing_logged:
                        continue
                    self._wallclock_missing_logged.add(key)
                    self.logger.warning(
                        f"[{stock_code}] 清仓T点分支补结算跳过：无缓存最新价 "
                        f"(rule={rule.get('name')}, at={rule.get('clear_branch_at')}, "
                        f"source=wallclock)"
                    )
                continue
            changed = process_clear_branch_for_task(task, tick_data)
            if changed:
                self._persist_task(task)
                self._log_and_sync_after_change(
                    task, stock_code, source="wallclock/补结算"
                )
                continue
            # 有价但仍未决议：通常缺涨停价/昨收，只打一次
            for rule in due_rules:
                if clear_branch_is_resolved(rule):
                    continue
                key = f"{task_id}|{rule.get('id') or rule.get('name')}|no_settle"
                if key in self._wallclock_missing_logged:
                    continue
                self._wallclock_missing_logged.add(key)
                try:
                    lu = float(rule.get("limit_up") or 0)
                except (TypeError, ValueError):
                    lu = 0.0
                self.logger.warning(
                    f"[{stock_code}] 清仓T点分支补结算未生效：缺涨停价或其它字段 "
                    f"(rule={rule.get('name')}, cached_last={tick_data.get('lastPrice')}, "
                    f"cached_lastClose={tick_data.get('lastClose')}, rule_limit_up={lu}, "
                    f"at={rule.get('clear_branch_at')}, source=wallclock)"
                )
