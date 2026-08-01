# -*- coding: utf-8 -*-
"""延迟激活集中调度：不依赖图表分页，运行中任务 tick 驱动观察与到点激活。"""
from __future__ import annotations

from typing import Iterator, Tuple

from PyQt5.QtCore import QObject, pyqtSignal

from core.rule_activation import process_activation_for_task
from utils.logger import Logger


class RuleActivationManager(QObject):
    """对所有运行中任务中带 activation 的单点卖/突破卖规则做 tick 观察与激活。"""

    rules_updated = pyqtSignal(str)  # stock_code

    def __init__(self, task_manager):
        super().__init__()
        self.task_manager = task_manager
        self.logger = Logger()
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
            if process_activation_for_task(task, tick_data):
                self._persist_task(task)
                sc = str(task.get("stock_code") or stock_code)
                for r in (task.get("params") or {}).get("rules") or []:
                    if not isinstance(r, dict):
                        continue
                    act = r.get("activation")
                    if not isinstance(act, dict) or not act.get("resolved"):
                        continue
                    rname = r.get("name", "未命名")
                    if act.get("activated"):
                        self.logger.info(
                            f"[{sc}] 延迟激活：规则「{rname}」已激活 "
                            f"(activate_at={act.get('activate_at')}, reached={act.get('reached')})"
                        )
                    else:
                        self.logger.info(
                            f"[{sc}] 延迟激活：规则「{rname}」已跳过 "
                            f"(activate_at={act.get('activate_at')}, reached={act.get('reached')})"
                        )
                self.rules_updated.emit(sc)
