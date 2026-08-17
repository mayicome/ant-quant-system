"""
任务图表视图 - 显示所有任务的图形化界面
完全替代之前的表格任务列表
"""

import sys
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QScrollArea, QHBoxLayout,
                             QPushButton, QLineEdit, QLabel, QMessageBox,
                             QDialog, QFormLayout, QComboBox, QDoubleSpinBox,
                             QRadioButton, QButtonGroup, QSizePolicy, QGridLayout, QMenu,
                             QApplication)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QKeyEvent
from ui.stock_chart_widget import StockChartWidget
from utils.logger import Logger
import time


class TasksChartsView(QWidget):
    """任务图表视图 - 显示所有任务的图形化界面"""
    
    def __init__(self, task_manager, qmt_adapter, parent=None):
        super().__init__(parent)
        self.task_manager = task_manager
        self.qmt_adapter = qmt_adapter
        self.logger = Logger()
        
        # 存储图表组件 {stock_code: chart_widget}
        self.chart_widgets = {}
        
        # 图表组件缓存：存储所有已创建的图表组件（包括不在当前页的）
        # {stock_code: {'chart': StockChartWidget, 'container': QWidget, 'task': dict}}
        self._chart_cache = {}
        
        # 标记是否已经执行过启动时的任务暂停检查（只在程序启动时执行一次）
        self._has_checked_startup_pause = False
        
        # 列数设置，默认单列
        self.columns = 1
        
        # 分页设置
        # 1列=1只, 2列=2x2=4只, 3列=3x3=9只, 4列=4x4=16只
        self.tasks_per_page = {
            1: 1,    # 1列：1只
            2: 4,    # 2列：2x2=4只
            3: 9,    # 3列：3x3=9只
            4: 16    # 4列：4x4=16只
        }
        self.current_page = 0
        self.all_tasks = []
        
        # 防止并发加载的标志
        self._loading_tasks = False
        
        # 异步加载队列：存储待加载数据的图表
        self._pending_charts = []  # [(chart, qmt_adapter), ...]
        self._load_timer = QTimer()
        self._load_timer.timeout.connect(self._load_next_chart)
        self._load_timer.setSingleShot(True)  # 单次触发
        
        # 全屏状态
        self.is_fullscreen = False
        self.fullscreen_toolbar = None  # 保存全屏前的工具栏引用
        self.fullscreen_original_widgets = {}  # 保存全屏前需要隐藏的组件
        self.fullscreen_chart_widget = None  # 全屏时显示的图表组件
        self.fullscreen_exit_btn = None  # 全屏退出按钮
        
        # 连接tick数据信号
        if self.qmt_adapter:
            self.qmt_adapter.tick_data_signal.connect(self.on_tick_data)
        if self.task_manager and hasattr(self.task_manager, "scheduled_clear_manager"):
            self.task_manager.scheduled_clear_manager.rules_updated.connect(
                self._on_scheduled_clear_rules_updated
            )
        if self.task_manager and hasattr(self.task_manager, "rule_activation_manager"):
            self.task_manager.rule_activation_manager.rules_updated.connect(
                self._on_rule_activation_rules_updated
            )
        
        self.init_ui()
        self.load_tasks()

    def _on_rule_activation_rules_updated(self, stock_code: str):
        """延迟激活状态变更后刷新图表规则与显示。"""
        try:
            chart_data = self._chart_cache.get(stock_code) or self.chart_widgets.get(stock_code)
            if not chart_data or "chart" not in chart_data:
                return
            chart = chart_data["chart"]
            task = chart_data.get("task") or getattr(chart, "task", None)
            if task and self.task_manager:
                task_id = task.get("task_id")
                fresh = self.task_manager.tasks.get(task_id) if task_id else None
                if fresh:
                    task = fresh
                    chart_data["task"] = fresh
            if task:
                rules = (task.get("params") or {}).get("rules", [])
                chart.rules = rules
                chart.task = task
            if hasattr(chart, "update_chart"):
                chart.update_chart()
        except Exception as e:
            self.logger.debug(f"刷新延迟激活图表失败 {stock_code}: {e}")

    def _on_scheduled_clear_rules_updated(self, stock_code: str):
        """集中调度执行/跳过后刷新图表节点颜色。"""
        try:
            chart_data = self._chart_cache.get(stock_code) or self.chart_widgets.get(stock_code)
            if not chart_data or "chart" not in chart_data:
                return
            chart = chart_data["chart"]
            task = chart_data.get("task") or getattr(chart, "task", None)
            if task:
                rules = (task.get("params") or {}).get("rules", [])
                chart.rules = rules
            if hasattr(chart, "update_chart"):
                chart.update_chart()
        except Exception as e:
            self.logger.debug(f"刷新定时清仓图表失败 {stock_code}: {e}")
    
    def handle_early_order_cancel_error(self, order_id, error_msg):
        """将撤单失败回调转发到对应股票图表，提前单无法撤销时结束任务。"""
        handled_any = False
        charts = list(getattr(self, 'chart_widgets', {}).values())
        charts.extend(getattr(self, '_chart_cache', {}).values())
        seen = set()
        for chart_data in charts:
            if not isinstance(chart_data, dict) or 'chart' not in chart_data:
                continue
            chart = chart_data['chart']
            chart_key = id(chart)
            if chart_key in seen:
                continue
            seen.add(chart_key)
            if hasattr(chart, 'handle_early_order_cancel_error'):
                if chart.handle_early_order_cancel_error(order_id, error_msg):
                    handled_any = True
        return handled_any

    def handle_early_order_status_from_callback(self, stock_code, order_sysid, order_status, order_price, order_type):
        """将提前下单委托回报（撤单/成交）转发到对应股票图表。"""
        handled_any = False
        charts = list(getattr(self, 'chart_widgets', {}).values())
        charts.extend(getattr(self, '_chart_cache', {}).values())
        seen = set()
        for chart_data in charts:
            if not isinstance(chart_data, dict) or 'chart' not in chart_data:
                continue
            chart = chart_data['chart']
            if getattr(chart, 'stock_code', None) != stock_code:
                continue
            chart_key = id(chart)
            if chart_key in seen:
                continue
            seen.add(chart_key)
            if hasattr(chart, 'handle_early_order_status_from_callback'):
                if chart.handle_early_order_status_from_callback(
                    order_sysid, order_status, order_price, order_type
                ):
                    handled_any = True
        return handled_any

    def mark_early_manual_cancel_pending(self, stock_code, order_sysid) -> bool:
        """订单列表人工撤单前：标记对应提前挂单（回报后结束为黑节点）。"""
        code = str(stock_code or "").strip()
        oid = str(order_sysid or "").strip()
        if not code or not oid:
            return False
        charts = list(getattr(self, "chart_widgets", {}).values())
        charts.extend(getattr(self, "_chart_cache", {}).values())
        seen = set()
        hit = False
        for chart_data in charts:
            chart = None
            if isinstance(chart_data, dict):
                chart = chart_data.get("chart")
            elif chart_data is not None:
                chart = chart_data
            if chart is None:
                continue
            sc = str(getattr(chart, "stock_code", "") or "")
            if sc.split(".")[0] != code.split(".")[0]:
                continue
            ck = id(chart)
            if ck in seen:
                continue
            seen.add(ck)
            if hasattr(chart, "_mark_early_manual_cancel_pending"):
                if chart._mark_early_manual_cancel_pending(oid):
                    hit = True
        return hit

    def finalize_early_manual_cancel_if_pending(self, stock_code, order_sysid) -> bool:
        """撤单接口已成功：若已打人工撤单标记，立即黑节点结束（不等回调）。"""
        code = str(stock_code or "").strip()
        oid = str(order_sysid or "").strip()
        if not code:
            return False
        charts = list(getattr(self, "chart_widgets", {}).values())
        charts.extend(getattr(self, "_chart_cache", {}).values())
        seen = set()
        hit = False
        for chart_data in charts:
            chart = None
            if isinstance(chart_data, dict):
                chart = chart_data.get("chart")
            elif chart_data is not None:
                chart = chart_data
            if chart is None:
                continue
            sc = str(getattr(chart, "stock_code", "") or "")
            if sc.split(".")[0] != code.split(".")[0]:
                continue
            ck = id(chart)
            if ck in seen:
                continue
            seen.add(ck)
            for rule in getattr(chart, "rules", []) or []:
                if not isinstance(rule, dict) or not rule.get("early_manual_cancel_pending"):
                    continue
                if hasattr(chart, "_finalize_early_order_manual_cancelled"):
                    chart._finalize_early_order_manual_cancelled(rule, oid)
                    hit = True
        return hit

    def update_early_order_id(self, stock_code, order_sysid, order_price, order_type):
        """更新提前下单规则的真实订单ID（order_sysid）"""
        # 找到对应股票的图表组件
        if stock_code in self.chart_widgets:
            chart_data = self.chart_widgets[stock_code]
            # chart_widgets 存储的是字典 {'chart': StockChartWidget, 'container': ..., 'task': ...}
            if isinstance(chart_data, dict) and 'chart' in chart_data:
                chart_widget = chart_data['chart']
                if hasattr(chart_widget, '_update_early_order_id_from_callback'):
                    chart_widget._update_early_order_id_from_callback(order_sysid, order_price, order_type)
            elif hasattr(chart_data, '_update_early_order_id_from_callback'):
                # 如果直接是 StockChartWidget 对象（向后兼容）
                chart_data._update_early_order_id_from_callback(order_sysid, order_price, order_type)
            else:
                self.logger.warning(f"[订单ID更新] chart_data格式不正确或缺少方法: {type(chart_data)}")
        else:
            # 检查该股票是否有任务（可能在别的页面，这是正常的）
            has_task = False
            if self.task_manager and hasattr(self.task_manager, 'tasks'):
                for task_id, task in self.task_manager.tasks.items():
                    if isinstance(task, dict) and task.get('stock_code') == stock_code:
                        has_task = True
                        break
            
            # 如果股票有任务但不在当前页面（分页导致的），这是正常情况，不记录日志
            # 只有当股票完全没有任务时，才可能是异常情况（已删除的任务或错误订单），但也不记录日志避免刷屏
            # 因为程序启动时会查询所有历史订单，很多订单可能对应已删除的任务
            pass  # 静默处理，不记录日志

    def apply_builtin_order_feedback(self, stock_code, task_id, order_rec, order_id=""):
        """大 QMT passorder 成功后回写图表规则为已执行。"""
        code = str(stock_code or "").strip().upper()
        code6 = code.split(".")[0] if code else ""
        chart_widget = None
        candidates = []
        for mapping in (
            getattr(self, "chart_widgets", {}) or {},
            getattr(self, "_chart_cache", {}) or {},
        ):
            for key, chart_data in mapping.items():
                ku = str(key or "").strip().upper()
                if ku == code or ku.split(".")[0] == code6 or code6 and ku.endswith(code6):
                    candidates.append(chart_data)
        for chart_data in candidates:
            if isinstance(chart_data, dict) and chart_data.get("chart") is not None:
                chart_widget = chart_data["chart"]
                break
            if chart_data is not None and hasattr(chart_data, "apply_builtin_order_feedback"):
                chart_widget = chart_data
                break
        if chart_widget is not None and hasattr(chart_widget, "apply_builtin_order_feedback"):
            return bool(
                chart_widget.apply_builtin_order_feedback(task_id, order_rec, order_id)
            )
        # 当前页没有图表：直接改 task_manager 中的规则并保存，避免错过回写
        return self._apply_builtin_order_feedback_to_task(code, task_id, order_rec, order_id)

    def apply_builtin_early_state(self, task_id, state):
        """大 QMT early_states → 图表提前挂单展示。"""
        tid = str(task_id or "").strip()
        rule_id = tid.split(":")[-1].strip() if tid else ""
        parent_id = tid.split(":", 1)[0].strip() if ":" in tid else ""
        if not rule_id:
            return False
        for mapping in (
            getattr(self, "chart_widgets", {}) or {},
            getattr(self, "_chart_cache", {}) or {},
        ):
            for _key, chart_data in mapping.items():
                chart = None
                if isinstance(chart_data, dict):
                    chart = chart_data.get("chart")
                elif hasattr(chart_data, "apply_builtin_early_state"):
                    chart = chart_data
                if chart is None:
                    continue
                if parent_id and str(getattr(chart, "task_id", "") or "") not in (
                    parent_id,
                    "",
                ):
                    # 允许按规则 id 匹配
                    pass
                if hasattr(chart, "apply_builtin_early_state"):
                    if chart.apply_builtin_early_state(tid, state):
                        return True
        return self._apply_early_state_to_task_rules(tid, state)

    def _apply_early_state_to_task_rules(self, task_id, state):
        tm = getattr(self, "task_manager", None)
        if tm is None or not getattr(tm, "tasks", None):
            return False
        tid = str(task_id or "").strip()
        rule_id = tid.split(":")[-1].strip() if tid else ""
        if not rule_id:
            return False
        st = state or {}
        for task in tm.tasks.values():
            if not isinstance(task, dict):
                continue
            params = task.get("params") or {}
            if isinstance(params, str):
                try:
                    import json as _json

                    params = _json.loads(params) if params else {}
                except Exception:
                    params = {}
            rules = params.get("rules") if isinstance(params, dict) else None
            if not isinstance(rules, list):
                continue
            for r in rules:
                if not isinstance(r, dict) or str(r.get("id") or "") != rule_id:
                    continue
                if r.get("executed"):
                    return True
                uid = str(st.get("user_order_id") or "").strip() or "BUILTIN_EARLY"
                r["early_order"] = True
                r["early_order_id"] = uid  # 图表黄色节点依赖此字段
                r["early_order_price"] = float(st.get("price") or r.get("price") or 0)
                vol = int(st.get("volume") or 0)
                if vol > 0:
                    r["early_order_submit_volume"] = vol
                params["rules"] = rules
                task["params"] = params
                try:
                    tm.save_tasks(list(tm.tasks.values()))
                except Exception:
                    pass
                return True
        return False

    def apply_builtin_elastic_state(self, task_id, state):
        """大 QMT 弹性跟踪状态 → 图表节点变红 / 动态目标线。"""
        tid = str(task_id or "").strip()
        rule_id = tid.split(":")[-1].strip() if tid else ""
        parent_id = tid.split(":", 1)[0].strip() if ":" in tid else ""
        if not rule_id:
            return False
        # 先找对应图表
        for mapping in (
            getattr(self, "chart_widgets", {}) or {},
            getattr(self, "_chart_cache", {}) or {},
        ):
            for _key, chart_data in mapping.items():
                chart_widget = None
                task = None
                if isinstance(chart_data, dict):
                    chart_widget = chart_data.get("chart")
                    task = chart_data.get("task")
                elif chart_data is not None:
                    chart_widget = chart_data
                    task = getattr(chart_data, "task", None)
                if chart_widget is None:
                    continue
                if parent_id and task and str(task.get("task_id") or "") != parent_id:
                    # 仍允许按规则 id 匹配
                    pass
                if hasattr(chart_widget, "apply_builtin_elastic_state"):
                    if chart_widget.apply_builtin_elastic_state(tid, state):
                        return True
        # 无可见图表：直接改任务
        try:
            from brokers.builtin_price_feed import BuiltinPricePoller

            helper = BuiltinPricePoller.__new__(BuiltinPricePoller)
            helper.task_manager = self.task_manager
            helper.logger = getattr(self, "logger", None)
            return bool(helper._apply_elastic_state_to_task(tid, state))
        except Exception:
            return False

    def _apply_builtin_order_feedback_to_task(self, stock_code, task_id, order_rec, order_id=""):
        tm = getattr(self, "task_manager", None)
        if tm is None or not getattr(tm, "tasks", None):
            return False
        from datetime import datetime

        tid = str(task_id or "").strip()
        rule_id = tid.split(":")[-1].strip() if tid else ""
        parent_id = tid.split(":", 1)[0].strip() if ":" in tid else ""
        code6 = str(stock_code or "").split(".")[0]
        order_rec = order_rec or {}
        for task in tm.tasks.values():
            if not isinstance(task, dict):
                continue
            sc = str(task.get("stock_code") or "").strip().upper()
            if sc.split(".")[0] != code6:
                continue
            if parent_id and str(task.get("task_id") or "") != parent_id:
                # parent 不匹配时仍允许按规则 id 搜
                pass
            params = task.get("params") or {}
            if isinstance(params, str):
                try:
                    import json as _json
                    params = _json.loads(params) if params else {}
                except Exception:
                    params = {}
            rules = params.get("rules") if isinstance(params, dict) else None
            if not isinstance(rules, list):
                continue
            hit = None
            for r in rules:
                if isinstance(r, dict) and str(r.get("id") or "") == rule_id:
                    hit = r
                    break
            if hit is None:
                continue
            rtype = str(hit.get("type") or "")
            if rtype == "scheduled_clear":
                if hit.get("scheduled_clear_executed"):
                    continue
            elif hit.get("executed"):
                # 已执行仍补真突破明细
                try:
                    from ui.stock_chart_widget import StockChartWidget

                    helper = StockChartWidget.__new__(StockChartWidget)
                    helper.stock_code = stock_code
                    if helper._apply_true_breakthrough_from_builtin_event(hit, tid, order_rec):
                        if isinstance(params, dict):
                            task["params"] = params
                        try:
                            tm.save_tasks(list(tm.tasks.values()))
                        except Exception:
                            try:
                                tm.save_tasks()
                            except Exception:
                                pass
                except Exception:
                    pass
                continue
            if str(order_rec.get("event_type") or "") == "early_confirm" or (
                bool(order_rec.get("early_order"))
                and str(order_rec.get("status") or "").lower() == "filled"
            ):
                hit["early_order"] = False
                hit["early_order_id"] = None
                hit["early_order_price"] = None
                hit.pop("early_order_submit_price", None)
                hit.pop("early_order_submit_volume", None)
                hit.pop("early_order_cancel_pending", None)
            px = float(order_rec.get("price") or hit.get("price") or 0)
            vol = int(order_rec.get("volume") or hit.get("volume") or 0)
            at = str(order_rec.get("at") or "")
            try:
                exec_time = datetime.strptime(at[:19], "%Y-%m-%dT%H:%M:%S") if at else datetime.now()
            except Exception:
                exec_time = datetime.now()
            if rtype == "scheduled_clear":
                st = str(order_rec.get("status") or "").strip().lower()
                attempted = st not in ("skipped", "")
                hit["scheduled_clear_executed"] = True
                hit["scheduled_clear_order_attempted"] = attempted
                hit["pending_tick_execution"] = False
                hit["executed_time"] = exec_time.strftime("%Y-%m-%d %H:%M:%S")
                hit["executed_price"] = px
                hit["executed_volume"] = vol
                if attempted:
                    hit["order_id"] = str(order_id or tid or "PO_BUILTIN")
                hit.pop("executed_reason", None)
            elif rtype in ("grid_buy", "grid_sell"):
                gi = order_rec.get("grid_index")
                try:
                    gi = int(gi) if gi is not None else None
                except (TypeError, ValueError):
                    gi = None
                if gi is None:
                    continue
                if "executed_grids" not in hit or not isinstance(hit.get("executed_grids"), list):
                    hit["executed_grids"] = []
                if gi not in hit["executed_grids"]:
                    hit["executed_grids"].append(gi)
                if "executed_grid_prices" not in hit or not isinstance(
                    hit.get("executed_grid_prices"), dict
                ):
                    hit["executed_grid_prices"] = {}
                if "executed_grid_volumes" not in hit or not isinstance(
                    hit.get("executed_grid_volumes"), dict
                ):
                    hit["executed_grid_volumes"] = {}
                hit["executed_grid_prices"][str(gi)] = px
                hit["executed_grid_volumes"][str(gi)] = vol
                hit["order_id"] = str(order_id or tid or "PO_BUILTIN")
                num_grids = int(hit.get("num_grids") or 2)
                all_done = len(set(int(x) for x in hit["executed_grids"])) >= num_grids + 1
                if all_done:
                    hit["executed"] = True
                    hit["executed_time"] = exec_time.strftime("%Y-%m-%d %H:%M:%S")
                    hit["executed_price"] = px
                    hit["executed_volume"] = vol
                    hit.pop("executed_reason", None)
            else:
                hit["executed"] = True
                hit["executed_time"] = exec_time.strftime("%Y-%m-%d %H:%M:%S")
                hit["executed_price"] = px
                hit["executed_volume"] = vol
                hit["order_id"] = str(order_id or tid or "PO_BUILTIN")
                # 禁买跳过 / 本笔低于最小买入：保留非正常结束原因，供图表黑/深灰区分
                try:
                    from ui.stock_chart_widget import StockChartWidget

                    skip_reason = StockChartWidget._builtin_order_skip_reason(
                        order_rec, hit.get("order_id")
                    )
                except Exception:
                    skip_reason = ""
                if skip_reason:
                    hit["executed_reason"] = skip_reason
                    if skip_reason == "buy_block_window":
                        hit["order_id"] = "SKIPPED_BUY_WINDOW"
                    elif skip_reason == "order_below_min":
                        hit["order_id"] = "SKIPPED_MIN_BUY"
                else:
                    hit.pop("executed_reason", None)
                try:
                    from ui.stock_chart_widget import StockChartWidget

                    helper = StockChartWidget.__new__(StockChartWidget)
                    helper.stock_code = stock_code
                    helper._apply_true_breakthrough_from_builtin_event(hit, tid, order_rec)
                except Exception:
                    pass
                if rtype in ("night_buy", "night_sell"):
                    hit["night_market_pending"] = False
                    hit["night_market_order_id"] = str(order_id or tid or "PO_BUILTIN")
                ep = str(order_rec.get("executed_endpoint") or "").strip()
                if ep in ("low", "high"):
                    hit["executed_endpoint"] = ep
                # 实盘建仓日：首次买入成交写入；卖出后无仓则清除
                try:
                    from utils.position_entry_dates import note_fill_from_order

                    note_fill_from_order(
                        stock_code=code6 or stock_code,
                        rule=hit,
                        order_rec=order_rec,
                        skip_reason=str(skip_reason or ""),
                    )
                except Exception:
                    pass
                if not skip_reason:
                    try:
                        from utils.filled_legs import note_from_rule_fill

                        note_from_rule_fill(
                            stock_code=code6 or stock_code,
                            rule=hit,
                            order_rec=order_rec,
                        )
                    except Exception:
                        pass
            params["rules"] = rules
            task["params"] = params
            try:
                tm.save_tasks(list(tm.tasks.values()))
            except Exception:
                pass
            try:
                from core.execution_record_manager import ExecutionRecordManager

                ExecutionRecordManager().record_from_builtin_order(
                    order_rec,
                    order_id=str(order_id or tid or ""),
                    stock_name=str(task.get("stock_name") or task.get("name") or ""),
                    rule=hit,
                )
            except Exception:
                pass
            if getattr(self, "logger", None):
                self.logger.info(
                    f"[{stock_code}] [builtin] 无可见图表，已直接回写任务规则: {rule_id}"
                )
            return True
        return False

    def update_night_market_rule_from_order(self, stock_code, order_sysid, order_status, order_price, order_type):
        """根据订单回报更新夜市委托规则状态"""
        # 找到对应股票的图表组件
        if stock_code in self.chart_widgets:
            chart_data = self.chart_widgets[stock_code]
            # chart_widgets 存储的是字典 {'chart': StockChartWidget, 'container': ..., 'task': ...}
            if isinstance(chart_data, dict) and 'chart' in chart_data:
                chart_widget = chart_data['chart']
                if hasattr(chart_widget, '_update_night_market_rule_from_order'):
                    chart_widget._update_night_market_rule_from_order(order_sysid, order_status, order_price, order_type)
            elif hasattr(chart_data, '_update_night_market_rule_from_order'):
                # 如果直接是 StockChartWidget 对象（向后兼容）
                chart_data._update_night_market_rule_from_order(order_sysid, order_status, order_price, order_type)
        
    def init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # 顶部工具栏
        toolbar = self.create_toolbar()
        layout.addWidget(toolbar)
        
        # 创建滚动区域
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        # 最小高度将在 load_tasks 中按列数调整；此处给较小下限，避免挤占主窗口
        self.scroll_area.setMinimumHeight(200)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        # 主容器 - 存放所有图表
        self.charts_container = QWidget()
        self.charts_layout = QVBoxLayout()  # 这将是外层布局
        self.charts_layout.setAlignment(Qt.AlignTop)
        
        # 创建网格布局用于多列显示
        self.grid_layout = QGridLayout()
        self.grid_layout.setSpacing(10)
        
        # 设置网格布局的列宽比例，确保平铺显示
        # 这里先不设置，在 load_tasks 中根据列数动态设置
        self.grid_layout.setColumnMinimumWidth(0, 1)  # 初始只设置一列
        
        self.charts_container.setLayout(self.grid_layout)
        
        self.scroll_area.setWidget(self.charts_container)
        # stretch=1 让滚动区占满窗口剩余高度，3/4 列才能按视口高度平铺一页
        layout.addWidget(self.scroll_area, 1)
        
        # 检查是否已经有布局，避免重复设置导致警告
        if self.layout() is None:
            self.setLayout(layout)
        else:
            self.logger.debug("init_ui: 布局已存在，跳过重复设置")
        
        # 设置样式
        self.setStyleSheet("""
            QLabel {
                font-family: "Microsoft YaHei";
                font-size: 10pt;
            }
            QPushButton {
                font-family: "Microsoft YaHei";
                font-size: 10pt;
                padding: 5px 15px;
                border-radius: 5px;
                background-color: #4CAF50;
                color: white;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:pressed {
                background-color: #3d8b40;
            }
        """)
        
        # 设置焦点策略以接收键盘事件
        self.setFocusPolicy(Qt.StrongFocus)
        
    def create_toolbar(self):
        """创建工具栏"""
        toolbar = QWidget()
        toolbar_layout = QHBoxLayout()
        
        # 添加任务按钮
        add_task_btn = QPushButton("➕ 添加股票")
        add_task_btn.clicked.connect(self.show_add_task_dialog)
        toolbar_layout.addWidget(add_task_btn)
        
        toolbar_layout.addSpacing(20)
        
        # 列数选择
        column_label = QLabel("每行:")
        column_label.setStyleSheet("color: #666;")
        toolbar_layout.addWidget(column_label)
        
        # 创建单选框组
        self.column_button_group = QButtonGroup()
        self.column_radios = {}  # 存储单选按钮引用，方便后续更新
        for cols in [1, 2, 3, 4]:
            radio = QRadioButton(f"{cols}列")
            radio.setChecked(cols == 1)  # 默认选中单列
            radio.setMinimumWidth(70)  # 设置最小宽度，确保文字完整显示
            radio.setFixedWidth(70)  # 设置固定宽度，确保按钮宽度一致
            # 设置字体，确保文字清晰显示
            font = radio.font()
            font.setPointSize(9)
            radio.setFont(font)
            radio.toggled.connect(lambda checked, c=cols: self.on_column_toggled(checked, c))
            self.column_button_group.addButton(radio, cols)
            self.column_radios[cols] = radio  # 保存引用
            toolbar_layout.addWidget(radio)
        
        toolbar_layout.addSpacing(20)
        
        # 添加分页按钮
        self.prev_btn = QPushButton("◄ 上一页")
        self.prev_btn.clicked.connect(self.prev_page)
        self.prev_btn.setEnabled(False)
        toolbar_layout.addWidget(self.prev_btn)
        
        self.next_btn = QPushButton("下一页 ►")
        self.next_btn.clicked.connect(self.next_page)
        self.next_btn.setEnabled(False)
        toolbar_layout.addWidget(self.next_btn)
        
        toolbar_layout.addStretch()
        
        # 暂停本页全部正在运行的任务
        self.pause_all_btn = QPushButton("全部暂停")
        self.pause_all_btn.setToolTip("暂停当前页所有正在运行的任务")
        self.pause_all_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff9800;
                color: white;
                padding: 5px 15px;
            }
            QPushButton:hover {
                background-color: #fb8c00;
            }
            QPushButton:pressed {
                background-color: #ef6c00;
            }
        """)
        self.pause_all_btn.clicked.connect(self.pause_all_page_tasks)
        toolbar_layout.addWidget(self.pause_all_btn)
        
        toolbar_layout.addSpacing(10)
        
        # 查看执行记录按钮
        records_btn = QPushButton("📋 执行记录")
        records_btn.setToolTip("查看任务执行记录")
        records_btn.setStyleSheet("background-color: #2196F3; color: white; padding: 5px 15px;")
        records_btn.clicked.connect(self.show_execution_records)
        toolbar_layout.addWidget(records_btn)
        
        toolbar_layout.addSpacing(10)
        
        # 重新加载任务按钮（从文件刷新，用于策略生成系统写入新任务后同步）
        reload_btn = QPushButton("🔄 重新加载任务")
        reload_btn.setToolTip("从任务文件重新加载（策略生成系统写入新任务后请点击此按钮）")
        reload_btn.setStyleSheet("background-color: #FF9800; color: white; padding: 5px 15px;")
        reload_btn.clicked.connect(self._on_reload_tasks_from_file)
        toolbar_layout.addWidget(reload_btn)
        
        toolbar_layout.addSpacing(10)
        
        # 状态信息
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet("color: #666;")
        toolbar_layout.addWidget(self.status_label)
        
        toolbar_layout.addSpacing(10)
        
        # 全屏按钮（只在1列时显示）
        self.fullscreen_btn = QPushButton("⛶ 全屏")
        self.fullscreen_btn.setToolTip("全屏显示图表（按ESC退出）")
        self.fullscreen_btn.setStyleSheet("""
            QPushButton {
                background-color: #9C27B0;
                color: white;
                padding: 5px 15px;
            }
            QPushButton:hover {
                background-color: #7B1FA2;
            }
        """)
        self.fullscreen_btn.clicked.connect(self.toggle_fullscreen)
        toolbar_layout.addWidget(self.fullscreen_btn)
        self.fullscreen_btn.setVisible(True)  # 所有列数都可以全屏
        
        toolbar.setLayout(toolbar_layout)
        
        # 设置工具栏样式
        toolbar.setStyleSheet("""
            QWidget {
                background-color: #f5f5f5;
                padding: 10px;
                border-bottom: 1px solid #ddd;
            }
            QPushButton {
                background-color: #4CAF50;
                color: white;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:pressed {
                background-color: #3d8b40;
            }
            QPushButton:disabled {
                background-color: #ccc;
                color: #999;
                border: none;
            }
        """)
        
        return toolbar
        
    def _on_reload_tasks_from_file(self):
        """从任务文件重新加载（策略生成系统写入新任务后调用，避免关闭主程序时覆盖）"""
        if not hasattr(self, 'task_manager') or not self.task_manager:
            return
        try:
            self.task_manager.load_tasks(force_reload=True)
            self.load_tasks()
            # 若有因重新加载被置为暂停的任务，弹窗提示（与程序启动时一致）
            paused_list = getattr(self.task_manager, '_reload_paused_task_names', []) or []
            if paused_list:
                from PyQt5.QtWidgets import QMessageBox
                from PyQt5.QtCore import QTimer
                def show_reload_paused_dialog():
                    msg = (
                        "因重新加载任务后检测到任务内容（规则/参数等）与运行前不一致，"
                        "以下原正在运行的任务已置为暂停，请确认规则后手动启动：\n\n"
                    )
                    msg += "\n".join([f"  • {name}" for name in paused_list])
                    msg += "\n\n请在需要时手动启动这些任务。"
                    parent = self.window() or QApplication.activeWindow()
                    try:
                        QMessageBox.warning(parent, "任务状态提示", msg, QMessageBox.Ok)
                    except Exception as e:
                        self.logger.error(f"显示重新加载暂停提示失败: {e}", exc_info=True)
                    self.task_manager._reload_paused_task_names = []
                QTimer.singleShot(500, show_reload_paused_dialog)
        except Exception as e:
            self.logger.error(f"重新加载任务失败: {e}", exc_info=True)
        
    def load_tasks(self):
        """加载所有任务并创建图表"""
        try:
            import time as _time
            _t0 = _time.time()
            # 防止并发加载：如果正在加载中，跳过重复调用
            if self._loading_tasks:
                self.logger.debug("正在加载任务，跳过重复调用")
                return
            
            self._loading_tasks = True
            
            # 性能优化：禁用 UI 更新，避免布局调整过程中的多次重绘
            self.setUpdatesEnabled(False)

            # 与切列重排相同：避免布局调整时误触启动/暂停
            run_snap = self._snapshot_chart_run_states()
            self._block_chart_toggle_signals(True)
            
            # 清空现有图表
            self.clear_charts()
            
            # 只在第一次加载时从文件加载任务，后续切换页面不再重新加载
            # （避免重新加载文件时丢失运行状态）
            if not self._has_checked_startup_pause:
                # 第一次加载时，确保任务管理器已加载任务
                if hasattr(self.task_manager, 'load_tasks'):
                    self.task_manager.load_tasks()
            else:
                # 如果已经初始化过，但当前任务列表为空，重新加载一次（可能刚添加了新任务）
                current_tasks = self.task_manager.tasks if hasattr(self.task_manager, 'tasks') else {}
                if not current_tasks:
                    # 重新加载任务管理器，确保获取最新保存的任务
                    if hasattr(self.task_manager, 'load_tasks'):
                        self.task_manager.load_tasks()
            
            # 获取所有任务
            tasks = self.task_manager.tasks if hasattr(self.task_manager, 'tasks') else {}
            # 本次重新加载时新增的股票代码集合（6 位），用于区分显示
            new_stock_codes = set(getattr(self.task_manager, '_newly_loaded_stock_codes', None) or [])
            
            def _norm_sc(sc):
                s = (sc or "").strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
                s = "".join(c for c in s if c.isdigit())
                return s.zfill(6) if len(s) >= 6 else (s[:6].zfill(6) if s else "")
            
            # 自动计算所有任务股票的弹性买入推荐值
            try:
                from utils.recommendation_service import get_recommendation_service
                recommendation_service = get_recommendation_service()
                
                # 收集所有唯一的股票代码
                stock_codes = set()
                for task in tasks.values():
                    stock_code = task.get('stock_code')
                    if stock_code:
                        stock_codes.add(stock_code)
                
                # 注释掉自动计算，改为按需计算（用户打开规则对话框时再计算）
                # 这样可以避免UI卡顿
                # 对每个股票代码，检查是否需要计算买入推荐值
                # from datetime import date
                # today = date.today()
                # 
                # for stock_code in stock_codes:
                #     buy_rec = recommendation_service.get_buy_recommendations(stock_code)
                #     if not buy_rec or (buy_rec.get('last_update') and (today - buy_rec['last_update']).days > 1):
                #         # 异步计算，不阻塞UI
                #         recommendation_service.calculate_buy_recommendations_async(stock_code)
            except Exception as e:
                self.logger.error(f"自动计算买入推荐值失败: {str(e)}", exc_info=True)
            
            # 全屏按钮在所有列数下都显示
            if hasattr(self, 'fullscreen_btn'):
                self.fullscreen_btn.setVisible(True)
            
            # 检查并修复正在运行的任务（仅在程序启动时执行一次）
            paused_tasks = []
            if not self._has_checked_startup_pause:
                # 只在第一次加载时检查并暂停正在运行的任务（程序启动时）
                for task_id, task in tasks.items():
                    params = task.get('params', {})
                    task_running = params.get('task_running', False)
                    
                    if task_running:
                        # 将正在运行的任务改为暂停状态
                        params['task_running'] = False
                        params['task_paused'] = True
                        task['params'] = params
                        
                        stock_code = task.get('stock_code', '')
                        stock_name = task.get('stock_name', '未知')
                        paused_tasks.append(f"{stock_name} ({stock_code})")
                        # 为避免控制台刷屏：仅在最后做一次汇总输出
                
                # 标记已经执行过启动检查
                self._has_checked_startup_pause = True
                
                # 保存暂停任务列表到实例变量，供延迟函数使用
                self.paused_tasks_on_startup = paused_tasks.copy() if paused_tasks else []
            
            # 如果有任务被暂停，保存任务并提示用户（仅在启动时）
            if paused_tasks:
                self.logger.info(f"检测到 {len(paused_tasks)} 个正在运行的任务，已暂停：{paused_tasks}")
                
                # 保存任务状态（传入任务列表，不是字典）
                if hasattr(self.task_manager, 'save_tasks'):
                    # 将字典转换为列表，确保格式正确
                    all_tasks_list = list(tasks.values())
                    self.logger.info(f"准备保存 {len(all_tasks_list)} 个任务")
                    self.task_manager.save_tasks(all_tasks_list)
                    self.logger.info(f"任务状态已保存，当前任务管理器中有 {len(self.task_manager.tasks)} 个任务")
                
                # 延迟显示提示对话框，确保UI已经初始化完成
                from PyQt5.QtWidgets import QMessageBox
                # 勿在 load_tasks 内再 import QTimer，否则会把 QTimer 视为局部变量，
                # 当 paused_tasks 为空时会导致后面 QTimer.singleShot 报 UnboundLocalError
                def show_warning_dialog():
                    # 从实例变量获取暂停任务列表
                    paused_list = getattr(self, 'paused_tasks_on_startup', [])
                    
                    if not paused_list:
                        self.logger.warning("延迟函数执行时，暂停任务列表为空")
                        return
                    
                    message = "为安全起见，以下任务已被暂停，请手工启动：\n\n"
                    message += "\n".join([f"  • {task_name}" for task_name in paused_list])
                    message += "\n\n请在需要时手动启动这些任务。"
                    
                    # 尝试多种方式获取父窗口
                    parent_window = None
                    try:
                        parent_window = self.window()
                        if not parent_window or not parent_window.isVisible():
                            parent_window = QApplication.activeWindow()
                        if not parent_window:
                            parent_window = None
                    except Exception as e:
                        self.logger.warning(f"获取父窗口失败：{str(e)}")
                        parent_window = None
                    
                    try:
                        msg_box = QMessageBox.warning(
                            parent_window,
                            "任务状态提示",
                            message,
                            QMessageBox.Ok
                        )
                    except Exception as e:
                        self.logger.error(f"显示警告对话框失败：{str(e)}", exc_info=True)
                
                # 延迟显示对话框，确保UI已完全初始化（增加到1.5秒）
                QTimer.singleShot(1500, show_warning_dialog)
            
            if not tasks:
                # 显示提示
                no_task_label = QLabel("暂无任务，请点击【添加任务】按钮创建新任务")
                no_task_label.setAlignment(Qt.AlignCenter)
                no_task_label.setStyleSheet("color: #999; font-size: 12pt; padding: 50px;")
                self.grid_layout.addWidget(no_task_label, 0, 0)
                self.status_label.setText("无任务")
                # 重置加载标志，允许后续重新加载
                self._loading_tasks = False
                try:
                    self._restore_chart_run_states(run_snap)
                except Exception:
                    pass
                try:
                    self._block_chart_toggle_signals(False)
                except Exception:
                    pass
                # 性能优化：重新启用 UI 更新
                self.setUpdatesEnabled(True)
                return
            
            # 保存所有任务到列表，并按order_index排序（如果存在）
            self.all_tasks = list(tasks.items())
            
            # 确保所有任务都有order_index，并按order_index排序
            for idx, (task_id, task) in enumerate(self.all_tasks):
                if 'order_index' not in task:
                    task['order_index'] = idx
            
            # 按order_index排序
            self.all_tasks.sort(key=lambda x: x[1].get('order_index', 0))
            
            # 计算分页
            items_per_page = self.tasks_per_page.get(self.columns, 1)
            total_pages = (len(self.all_tasks) + items_per_page - 1) // items_per_page if items_per_page > 0 else 1
            
            # 确保当前页不超出范围
            if self.current_page >= total_pages:
                self.current_page = max(0, total_pages - 1)
            
            # 获取当前页的任务
            start_idx = self.current_page * items_per_page
            end_idx = start_idx + items_per_page
            current_page_tasks = self.all_tasks[start_idx:end_idx]
            
            # 设置网格布局的列拉伸比例
            # 先清除之前的列设置
            for col_idx in range(10):  # 假设最多10列
                self.grid_layout.setColumnStretch(col_idx, 0)
            
            # 根据当前列数设置列拉伸，并为每列设置最小宽度（避免从1列切到多列时第二列被压成0宽导致前两格空白）
            for col_idx in range(self.columns):
                self.grid_layout.setColumnStretch(col_idx, 1)  # 每列等宽
                self.grid_layout.setColumnMinimumWidth(col_idx, 100)
            # 清除多余列的最小宽度（避免残留）
            for col_idx in range(self.columns, 10):
                self.grid_layout.setColumnMinimumWidth(col_idx, 0)
            
            # 为当前页的任务创建或重用图表
            row = 0
            col = 0
            max_row = 0  # 记录实际使用的最大行号
            chart_containers_to_show = []  # 收集需要显示的容器，最后一次性显示
            # 本页已放置的 stock_code：同一页若重复出现同一股票，不能复用同一容器（否则会移走导致空单元格）
            placed_stock_codes_this_page = set()
            for task_id, task in current_page_tasks:
                stock_code = task.get('stock_code', '')
                # 确保stock_code是字符串类型
                stock_code = str(stock_code) if stock_code is not None else ''
                stock_name = self._resolve_task_stock_name(
                    stock_code, task.get('stock_name', ''), task=task
                )
                is_new_task = _norm_sc(stock_code) in new_stock_codes
                
                if not stock_code:
                    continue
                
                # 检查图表是否已在缓存中且本页尚未用过该容器（同一页重复股票需新建，避免同一控件被 addWidget 两次导致空单元格）
                if stock_code in self._chart_cache and stock_code not in placed_stock_codes_this_page:
                    # 重用已存在的图表组件
                    cached_data = self._chart_cache[stock_code]
                    chart = cached_data['chart']
                    chart_container = cached_data['container']
                    
                    # 先隐藏图表，等所有图表都添加到布局后再统一显示（避免逐个显示的效果）
                    if chart_container:
                        chart_container.hide()
                    if chart:
                        chart.hide()
                    
                    # 收集需要显示的容器
                    chart_containers_to_show.append((chart_container, chart))
                    
                    # 检查图表是否已加载数据（如果price_data或time_data为空，说明还没加载）
                    has_data = hasattr(chart, 'price_data') and chart.price_data and len(chart.price_data) > 0
                    
                    # 更新列数设置
                    chart.current_columns = self.columns
                    
                    # 根据列数动态控制控件显示（只在单列时显示）
                    show_controls = self.columns == 1
                    if hasattr(chart, 'set_controls_visible'):
                        chart.set_controls_visible(show_controls)
                    
                    # 根据列数调整图表的最小高度
                    if self.columns >= 4:
                        chart.setMinimumHeight(100)
                        chart.canvas.setMinimumHeight(120)
                    elif self.columns == 3:
                        chart.setMinimumHeight(120)
                        chart.canvas.setMinimumHeight(150)
                    elif self.columns == 2:
                        chart.setMinimumHeight(140)
                        chart.canvas.setMinimumHeight(180)
                    else:  # 1列：设置更大的最小高度，充分利用空间
                        chart.setMinimumHeight(300)
                        chart.canvas.setMinimumHeight(400)
                    
                    # 更新任务数据和规则（可能已变化）；补齐曾为「未知」的名称
                    chart.task = task
                    chart.task_id = task_id
                    if stock_name and stock_name not in ("未知", "未知名称"):
                        chart.stock_name = stock_name
                        try:
                            for child in chart_container.findChildren(QLabel):
                                t = child.text() if child is not None else ""
                                if stock_code.split(".")[0] in t and ("未知" in t or "未知名称" in t):
                                    short = stock_code.split(".")[0]
                                    child.setText(f"{stock_name} ({short})")
                        except Exception:
                            pass
                    params = task.get('params', {})
                    rules = params.get('rules', [])
                    chart.set_rules(rules)
                    
                    # 更新任务运行状态：优先图表实况 / running_tasks，避免切列后被陈旧 params 刷成已暂停
                    task_running, task_paused = self._resolve_task_run_state(task_id, task, chart)
                    chart.set_task_status(task_running, task_paused)
                    
                    # 重绘：只在 1 列强制，避免 2 列切换时卡顿
                    need_force_redraw = (self.columns == 1)
                    try:
                        if need_force_redraw:
                            chart.update_chart()
                    except Exception:
                        pass
                    
                    # 更新缓存中的任务数据
                    cached_data['task'] = task
                    
                    # 如果容器已存在，直接添加到布局并更新按钮状态
                    chart_container = cached_data['container']
                    
                    # 根据是否为本次新增任务设置容器边框样式与“新”角标显隐（区分新增与现有任务）
                    if 'new_badge' in cached_data:
                        cached_data['new_badge'].setVisible(is_new_task)
                    if is_new_task:
                        chart_container.setStyleSheet("""
                            QWidget {
                                background-color: white;
                                border: 2px solid #FF9800;
                                border-radius: 8px;
                                margin: 5px;
                            }
                            QPushButton {
                                background-color: #f44336;
                                color: white;
                                padding: 5px 10px;
                                border-radius: 3px;
                            }
                            QPushButton:hover {
                                background-color: #da190b;
                            }
                        """)
                    else:
                        chart_container.setStyleSheet("""
                            QWidget {
                                background-color: white;
                                border: 1px solid #ddd;
                                border-radius: 8px;
                                margin: 5px;
                            }
                            QPushButton {
                                background-color: #f44336;
                                color: white;
                                padding: 5px 10px;
                                border-radius: 3px;
                            }
                            QPushButton:hover {
                                background-color: #da190b;
                            }
                        """)
                    
                    # 更新移动按钮的状态（检查是否可以上移/下移/置顶/置底）
                    current_idx = next((idx for idx, (tid, t) in enumerate(self.all_tasks) if t.get('stock_code') == stock_code), -1)
                    # 查找容器中的移动按钮并更新状态
                    for widget in chart_container.findChildren(QPushButton):
                        tooltip = widget.toolTip()
                        if tooltip == "置顶任务（移到最前面）" or tooltip == "上移任务":
                            widget.setEnabled(current_idx > 0)
                        elif tooltip == "下移任务" or tooltip == "置底任务（移到最后面）":
                            widget.setEnabled(current_idx < len(self.all_tasks) - 1 and current_idx >= 0)
                    
                    # 如果图表还没有加载数据，重新添加到待加载队列
                    if not has_data:
                        self._pending_charts.append((chart, self.qmt_adapter))
                else:
                    # 创建新的图表组件（只在单列时显示控件）
                    show_controls = self.columns == 1
                    chart = StockChartWidget(stock_code, stock_name, show_controls=show_controls)
                    
                    # 设置当前列数，用于判断是否显示标签和调整最小尺寸
                    chart.current_columns = self.columns
                    
                    # 根据列数调整图表的最小高度（多列时需要更小的最小高度）
                    if self.columns >= 4:
                        # 4列：大幅减小最小高度
                        chart.setMinimumHeight(100)
                        chart.canvas.setMinimumHeight(120)
                    elif self.columns == 3:
                        # 3列：中等减小最小高度
                        chart.setMinimumHeight(120)
                        chart.canvas.setMinimumHeight(150)
                    elif self.columns == 2:
                        # 2列：稍微减小最小高度
                        chart.setMinimumHeight(140)
                        chart.canvas.setMinimumHeight(180)
                    else:  # 1列：设置更大的最小高度，充分利用空间
                        chart.setMinimumHeight(300)
                        chart.canvas.setMinimumHeight(400)
                    
                    # 设置task和task_manager，用于拖动结束后保存
                    chart.task = task
                    chart.task_id = task_id
                    chart.task_manager = self.task_manager
                    
                    # 设置任务数据和规则
                    params = task.get('params', {})
                    
                    # 传递规则列表给图表
                    rules = params.get('rules', [])
                    chart.set_rules(rules)
                    
                    # 传递任务运行状态给图表：优先实况，避免 UI 显示滞后/被陈旧 params 覆盖
                    task_running, task_paused = self._resolve_task_run_state(task_id, task, chart)
                    chart.set_task_status(task_running, task_paused)
                    
                    # 先显示空图表框架：仅 1 列时强制重绘以避免切 2 列卡顿
                    if self.columns == 1:
                        try:
                            chart.update_chart()
                        except:
                            pass  # 如果失败，后续加载数据时会自动显示
                    
                    # 延迟加载市场数据（异步分批加载，避免一次性加载导致卡顿）
                    # 先显示空图表框架，数据在后台逐步加载
                    self._pending_charts.append((chart, self.qmt_adapter))
                    
                    # 创建图表容器
                    chart_container = QWidget()
                    chart_layout = QVBoxLayout()
                    chart_layout.setContentsMargins(8, 8, 8, 8)  # 恢复原来的边距
                    
                    # 图表标题栏
                    header_layout = QHBoxLayout()
                    header_layout.setSpacing(2)  # 恢复原来的间距
                    header_layout.setContentsMargins(0, 0, 0, 0)  # 减小边距
                    
                    # 删除按钮（放在股票名称前面）
                    delete_btn = QPushButton("×")
                    delete_btn.setToolTip(f"删除任务: {stock_code}")
                    delete_btn.setStyleSheet("""
                        QPushButton {
                            background-color: transparent;
                            color: #999;
                            border: none;
                            font-size: 20px;
                            font-weight: bold;
                            padding: 0px;
                            border-radius: 3px;
                        }
                        QPushButton:hover {
                            background-color: #ffebee;
                            color: #f44336;
                        }
                        QPushButton:pressed {
                            background-color: #ffcdd2;
                        }
                    """)
                    delete_btn.setFixedSize(26, 26)  # 恢复原来的按钮尺寸
                    delete_btn.clicked.connect(lambda checked, code=stock_code: self.delete_task(code))
                    
                    # 去掉股票代码的后缀（.SH、.SZ等）
                    # 确保stock_code是字符串类型
                    stock_code = str(stock_code) if stock_code is not None else ''
                    stock_code_short = stock_code.split('.')[0] if '.' in stock_code else stock_code
                    task_name_label = QLabel(f"{stock_name} ({stock_code_short})")
                    task_name_label.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))  # 恢复原来的字体
                    task_name_label.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Preferred)  # 允许压缩
                    
                    header_layout.addWidget(delete_btn)
                    header_layout.addSpacing(2)  # 恢复原来的间距
                    new_badge = QLabel("新")
                    new_badge.setStyleSheet("background-color: #FF9800; color: white; font-size: 10px; font-weight: bold; padding: 1px 4px; border-radius: 3px;")
                    new_badge.setToolTip("本次重新加载时新增的任务")
                    new_badge.setVisible(is_new_task)
                    header_layout.addWidget(new_badge)
                    header_layout.addSpacing(4)
                    header_layout.addWidget(task_name_label)
                    
                    # 添加图表的状态控制按钮到标题栏（按钮已合并状态显示和操作）
                    if hasattr(chart, 'toggle_btn'):
                        header_layout.addSpacing(1)  # 在股票名称和状态按钮之间添加小间距
                        header_layout.addWidget(chart.toggle_btn)
                    elif hasattr(chart, 'start_btn'):  # 兼容旧代码
                        header_layout.addSpacing(1)
                        header_layout.addWidget(chart.start_btn)
                    
                    header_layout.addStretch()
                    
                    # 统一的按钮样式（所有箭头按钮使用相同样式）
                    arrow_button_style = """
                        QPushButton {
                            background-color: #e3f2fd;
                            color: #1976d2;
                            border: 1px solid #90caf9;
                            font-size: 16px;
                            font-weight: bold;
                            padding: 0px;
                            border-radius: 3px;
                        }
                        QPushButton:hover {
                            background-color: #bbdefb;
                            color: #0d47a1;
                            border: 1px solid #64b5f6;
                        }
                        QPushButton:pressed {
                            background-color: #90caf9;
                            color: #0d47a1;
                        }
                        QPushButton:disabled {
                            background-color: #f5f5f5;
                            color: #ccc;
                            border: 1px solid #e0e0e0;
                        }
                    """
                    
                    # 添加置顶按钮（放在标题栏右侧，第一个）
                    # 使用 ⇈ (双向上箭头) 符号，更简洁统一
                    move_to_top_btn = QPushButton("⇈")
                    move_to_top_btn.setToolTip("置顶任务（移到最前面）")
                    move_to_top_btn.setStyleSheet(arrow_button_style)
                    move_to_top_btn.setFixedSize(26, 26)  # 恢复原来的按钮尺寸
                    move_to_top_btn.clicked.connect(lambda checked, code=stock_code: self.move_task_to_top(code))
                    
                    # 添加上移/下移按钮（放在标题栏右侧）
                    move_up_btn = QPushButton("▲")
                    move_up_btn.setToolTip("上移任务")
                    move_up_btn.setStyleSheet(arrow_button_style)
                    move_up_btn.setFixedSize(26, 26)  # 恢复原来的按钮尺寸
                    move_up_btn.clicked.connect(lambda checked, code=stock_code: self.move_task_up(code))
                    
                    move_down_btn = QPushButton("▼")
                    move_down_btn.setToolTip("下移任务")
                    move_down_btn.setStyleSheet(arrow_button_style)
                    move_down_btn.setFixedSize(26, 26)  # 恢复原来的按钮尺寸
                    move_down_btn.clicked.connect(lambda checked, code=stock_code: self.move_task_down(code))
                    
                    # 添加置底按钮
                    move_to_bottom_btn = QPushButton("⇊")
                    move_to_bottom_btn.setToolTip("置底任务（移到最后面）")
                    move_to_bottom_btn.setStyleSheet(arrow_button_style)
                    move_to_bottom_btn.setFixedSize(26, 26)  # 恢复原来的按钮尺寸
                    move_to_bottom_btn.clicked.connect(lambda checked, code=stock_code: self.move_task_to_bottom(code))
                    
                    # 检查是否可以上移/下移/置顶/置底，并设置按钮状态
                    current_idx = next((idx for idx, (tid, t) in enumerate(self.all_tasks) if t.get('stock_code') == stock_code), -1)
                    move_to_top_btn.setEnabled(current_idx > 0)
                    move_up_btn.setEnabled(current_idx > 0)
                    move_down_btn.setEnabled(current_idx < len(self.all_tasks) - 1 and current_idx >= 0)
                    move_to_bottom_btn.setEnabled(current_idx < len(self.all_tasks) - 1 and current_idx >= 0)
                    
                    header_layout.addWidget(move_to_top_btn)
                    header_layout.addSpacing(1)  # 恢复原来的按钮间距
                    header_layout.addWidget(move_up_btn)
                    header_layout.addSpacing(1)  # 恢复原来的按钮间距
                    header_layout.addWidget(move_down_btn)
                    header_layout.addSpacing(1)  # 恢复原来的按钮间距
                    header_layout.addWidget(move_to_bottom_btn)
                    
                    chart_layout.addLayout(header_layout)
                    chart_layout.addWidget(chart)
                    
                    chart_container.setLayout(chart_layout)
                    
                    # 根据是否为本次新增任务设置容器样式（区分新增与现有任务）
                    if is_new_task:
                        chart_container.setStyleSheet("""
                            QWidget {
                                background-color: white;
                                border: 2px solid #FF9800;
                                border-radius: 8px;
                                margin: 5px;
                            }
                            QPushButton {
                                background-color: #f44336;
                                color: white;
                                padding: 5px 10px;
                                border-radius: 3px;
                            }
                            QPushButton:hover {
                                background-color: #da190b;
                            }
                        """)
                    else:
                        chart_container.setStyleSheet("""
                            QWidget {
                                background-color: white;
                                border: 1px solid #ddd;
                                border-radius: 8px;
                                margin: 5px;
                            }
                            QPushButton {
                                background-color: #f44336;
                                color: white;
                                padding: 5px 10px;
                                border-radius: 3px;
                            }
                            QPushButton:hover {
                                background-color: #da190b;
                            }
                        """)
                    
                    # 设置容器的尺寸策略，确保图表能够拉伸
                    chart_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                    
                    # 保存到缓存（含“新”角标引用，复用时可根据 is_new_task 显隐）
                    self._chart_cache[stock_code] = {
                        'chart': chart,
                        'container': chart_container,
                        'task': task,
                        'new_badge': new_badge
                    }
                    
                    # 新创建的图表也需要先隐藏，等所有图表都添加到布局后再统一显示
                    chart_container.hide()
                    chart.hide()
                    chart_containers_to_show.append((chart_container, chart))
                
                # 添加到网格布局（根据列数设置）- 此时图表是隐藏的
                self.grid_layout.addWidget(chart_container, row, col)
                placed_stock_codes_this_page.add(stock_code)
                
                # 记录最大行号
                max_row = max(max_row, row)
                
                # 更新行列位置
                col += 1
                if col >= self.columns:
                    col = 0
                    row += 1
                
                # 存储引用（用于当前页）：同页多任务同股票时只保留最后一个，避免覆盖
                self.chart_widgets[stock_code] = {
                    'chart': chart,
                    'container': chart_container,
                    'task': task
                }
            
            # 性能优化：先设置所有布局参数，再显示图表，避免中间状态的尺寸调整
            
            # 设置行拉伸比例，确保同一行的所有图表等高
            # 先清除之前的行设置
            for row_idx in range(20):  # 假设最多20行
                self.grid_layout.setRowStretch(row_idx, 0)
            
            # 为实际使用的每一行设置相同的拉伸比例
            if max_row >= 0:  # 如果有图表
                for row_idx in range(max_row + 1):
                    self.grid_layout.setRowStretch(row_idx, 1)  # 每行等高
            
            # 根据列数调整滚动区域的最小高度（3/4 列由视口动态决定图表高度，滚动区本身占满剩余空间即可）
            if self.columns >= 4:
                self.scroll_area.setMinimumHeight(280)
            elif self.columns == 3:
                self.scroll_area.setMinimumHeight(280)
            else:
                self.scroll_area.setMinimumHeight(600)
            
            # 从1列切到多列时强制触发布局重算，避免前两格因布局未更新而显示空白
            self.charts_container.updateGeometry()
            self.scroll_area.updateGeometry()
            
            # 所有布局参数已设置完毕，现在一次性显示所有图表（避免中间尺寸调整）
            for chart_container, chart in chart_containers_to_show:
                if chart_container:
                    chart_container.show()
                if chart:
                    chart.show()
            
            # 显示分页信息
            # 标记加载完成
            self._loading_tasks = False
            page_info = f"第 {self.current_page + 1}/{total_pages} 页"
            # 显示总任务数，而不是当前页任务数
            self.status_label.setText(f"{len(self.all_tasks)} 个任务 - {page_info}")
            
            # 更新分页按钮状态
            self.prev_btn.setEnabled(self.current_page > 0)
            self.next_btn.setEnabled(self.current_page < total_pages - 1)
            
            try:
                self._restore_chart_run_states(run_snap)
            except Exception:
                pass
            try:
                self._block_chart_toggle_signals(False)
            except Exception:
                pass
            # 性能优化：所有布局修改完成后，重新启用 UI 更新
            self.setUpdatesEnabled(True)
            
            # 启动异步加载队列（如果有待加载的图表）
            if self._pending_charts:
                # 延迟50ms后开始加载第一个图表，给界面渲染留出时间
                self._load_timer.start(50)
            
            # 3/4 列：视口尺寸就绪后再按窗口高度均分每格高度（避免一页出现纵向滚动条）
            if self.columns in (3, 4):
                QTimer.singleShot(0, self._apply_chart_heights_from_viewport)
            
            elapsed = _time.time() - _t0
            if elapsed > 0.2:
                self.logger.warning(
                    f"[性能监控] load_tasks 耗时: {elapsed:.3f}s columns={self.columns} page={self.current_page} "
                    f"pending_charts={len(self._pending_charts) if hasattr(self, '_pending_charts') else 0}"
                )
            
        except Exception as e:
            import traceback
            self.logger.error(f"加载任务失败: {str(e)}\n{traceback.format_exc()}")
            # 确保即使出错也重置加载标志
            self._loading_tasks = False
            try:
                self._restore_chart_run_states(locals().get('run_snap') or {})
            except Exception:
                pass
            try:
                self._block_chart_toggle_signals(False)
            except Exception:
                pass
            # 性能优化：确保异常时也重新启用 UI 更新
            self.setUpdatesEnabled(True)
    
    def _load_next_chart(self):
        """分批加载图表数据，避免一次性加载导致卡顿"""
        if not self._pending_charts:
            return
        
        # 每次加载一个图表（可以根据需要调整批量大小）
        chart, qmt_adapter = self._pending_charts.pop(0)
        
        try:
            # 加载市场数据
            chart.load_market_data(qmt_adapter)
        except Exception as e:
            self.logger.error(f"加载图表数据失败: {str(e)}")
        
        # 如果还有待加载的图表，继续加载下一个
        # 根据待加载数量调整延迟时间：剩余越多，延迟越短（加快加载）
        if self._pending_charts:
            # 根据剩余数量动态调整延迟：1-2个时延迟100ms，3-5个时延迟50ms，更多时延迟30ms
            remaining = len(self._pending_charts)
            if remaining <= 2:
                delay = 100
            elif remaining <= 5:
                delay = 50
            else:
                delay = 30
            self._load_timer.start(delay)
        
    def clear_charts(self):
        """清空所有图表（优化：只从布局中移除，保留在缓存中）"""
        # 价格改变信号已移除，无需断开连接
        
        # 停止加载定时器并清空待加载队列（避免加载已不在当前页的图表）
        if self._load_timer.isActive():
            self._load_timer.stop()
        self._pending_charts.clear()
            
        # 只从网格布局中移除，但不删除图表组件（保留在缓存中）
        hide_count = 0
        while self.grid_layout.count():
            child = self.grid_layout.takeAt(0)
            if child.widget():
                widget = child.widget()
                # 先隐藏组件，避免在setParent(None)后显示为独立窗口
                widget.hide()
                hide_count += 1
                # 只从布局中移除，不删除组件
                widget.setParent(None)
        
        # 清空当前显示的图表引用（但保留在缓存中）
        self.chart_widgets.clear()
    
    def _apply_chart_heights_from_viewport(self):
        """
        3/4 列时按当前滚动视口高度均分每行高度，使一页内尽量不出现纵向滚动条。
        依赖 scroll_area 已 stretch 占满父布局剩余高度。
        """
        if self.columns not in (3, 4):
            return
        try:
            vp = self.scroll_area.viewport()
            h = int(vp.height())
            if h < 120:
                # 尚未完成布局时用本控件高度估算
                h = max(400, int(self.height()) - 120)
            # 按本页实际行数均分视口高度，勿固定为 3/4 行（例如 3 列 5 只股只有 2 行）
            items_per_page = self.tasks_per_page.get(self.columns, 1)
            start_idx = self.current_page * items_per_page
            end_idx = start_idx + items_per_page
            page_slice = self.all_tasks[start_idx:end_idx] if self.all_tasks else []
            n = sum(
                1 for _, t in page_slice
                if str(t.get('stock_code', '') or '').strip()
            )
            row_count = max(1, (n + self.columns - 1) // self.columns) if self.columns else 1
            spacing = self.grid_layout.verticalSpacing()
            if spacing < 0:
                spacing = 10
            margin = 8
            inner = max(0, h - margin - (row_count - 1) * spacing)
            cell_h = max(72, inner // row_count)
            chart_h = max(40, int(cell_h * 0.30))
            canvas_h = max(50, cell_h - chart_h - 6)
            canvas_h = min(canvas_h, cell_h - chart_h - 4)
            for cached in self._chart_cache.values():
                if not isinstance(cached, dict):
                    continue
                chart = cached.get('chart')
                if not chart:
                    continue
                chart.setMinimumHeight(chart_h)
                if hasattr(chart, 'canvas') and chart.canvas is not None:
                    chart.canvas.setMinimumHeight(canvas_h)
        except Exception:
            pass
    
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, 'columns', 1) in (3, 4):
            QTimer.singleShot(0, self._apply_chart_heights_from_viewport)
    
    def stop_all_chart_timers(self):
        """停止所有图表的定时器（用于程序退出时清理）"""
        try:
            # 停止当前显示的图表定时器
            for stock_code, chart_data in self.chart_widgets.items():
                try:
                    chart = chart_data.get('chart')
                    if chart and hasattr(chart, 'scheduled_clear_timer'):
                        if chart.scheduled_clear_timer and chart.scheduled_clear_timer.isActive():
                            chart.scheduled_clear_timer.stop()
                            try:
                                chart.scheduled_clear_timer.timeout.disconnect()
                            except (RuntimeError, TypeError):
                                pass
                            chart.scheduled_clear_timer = None
                except Exception as e:
                    self.logger.error(f"停止图表{stock_code}定时器失败: {str(e)}")
            
            # 停止缓存中所有图表的定时器
            for stock_code, cached_data in self._chart_cache.items():
                try:
                    chart = cached_data.get('chart')
                    if chart and hasattr(chart, 'scheduled_clear_timer'):
                        if chart.scheduled_clear_timer and chart.scheduled_clear_timer.isActive():
                            chart.scheduled_clear_timer.stop()
                            try:
                                chart.scheduled_clear_timer.timeout.disconnect()
                            except (RuntimeError, TypeError):
                                pass
                            chart.scheduled_clear_timer = None
                except Exception as e:
                    self.logger.error(f"停止缓存图表{stock_code}定时器失败: {str(e)}")
        except Exception as e:
            self.logger.error(f"停止所有图表定时器失败: {str(e)}")
    
    def on_column_toggled(self, checked, columns):
        """列数单选按钮切换事件"""
        if checked:
            # 降级为 DEBUG：用户操作日志用于排查，不希望实盘/截图时刷屏
            # utils/logger.py 的 debug 被重写为“无条件转发”，这里绕过该实现
            self.logger.logger.debug(f"[用户操作] 切换列数: {columns}列")
            # 只有在新按钮被选中时才执行切换
            # 先立即更新所有单选按钮的选中状态，确保原按钮立即取消选中
            for cols in [1, 2, 3, 4]:
                if cols != columns and hasattr(self, 'column_radios') and cols in self.column_radios:
                    self.column_radios[cols].setChecked(False)
            
            # 然后再执行列数改变逻辑
            self.on_column_changed(columns)
    
    def on_column_changed(self, columns):
        """列数改变"""
        # 记录列切换前的列数，用于决定是否需要强制重绘图表
        self._prev_columns = getattr(self, 'columns', None)
        self.columns = columns
        self.current_page = 0  # 重置到第一页
        
        # 全屏按钮在所有列数下都显示
        if hasattr(self, 'fullscreen_btn'):
            self.fullscreen_btn.setVisible(True)
        
        # 注意：不再自动退出全屏，允许在任何列数下保持全屏状态
        
        # 用 QTimer.singleShot 延迟执行 load_tasks，让 UI 先刷新（按钮状态立即更新）
        # 优先走快速重排，只有在缓存不足时才回退到 load_tasks
        QTimer.singleShot(0, self._load_page_fast)
    
    def switch_to_single_column_and_show_stock(self, stock_code):
        """切换到1列布局并显示指定股票
        
        Args:
            stock_code: 要显示的股票代码
        """
        try:
            # 如果已经在1列布局，且该股票在当前页面，则不需要切换
            if self.columns == 1 and stock_code in self.chart_widgets:
                # 滚动到该图表
                chart_data = self.chart_widgets[stock_code]
                # 确保图表可见
                if 'container' in chart_data and chart_data['container']:
                    chart_data['container'].show()
                    chart_data['container'].raise_()
                if 'chart' in chart_data and chart_data['chart']:
                    chart_data['chart'].show()
                # 滚动到该容器
                if 'container' in chart_data and chart_data['container']:
                    self.scroll_area.ensureWidgetVisible(chart_data['container'])
                return
            
            # 保存当前全屏状态（在全屏模式下切换列数时需要保持全屏）
            was_fullscreen = hasattr(self, 'is_fullscreen') and self.is_fullscreen
            
            # 在所有任务中查找该股票的索引位置（先查找，避免切换列数后找不到）
            target_page = 0
            if hasattr(self, 'all_tasks') and self.all_tasks:
                for idx, (task_id, task) in enumerate(self.all_tasks):
                    if task.get('stock_code') == stock_code:
                        # 1列时每页1个任务
                        target_page = idx
                        break
            
            # 切换到1列布局
            if self.columns != 1:
                self.columns = 1
                # 更新单选框状态（先暂时断开信号，避免触发重复调用）
                if hasattr(self, 'column_button_group'):
                    button = self.column_button_group.button(1)
                    if button:
                        # 暂时断开信号，避免触发on_column_toggled
                        button.blockSignals(True)
                        button.setChecked(True)
                        button.blockSignals(False)
            
            # 切换到目标页面
            self.current_page = target_page
            
            # 重新加载任务（会按照新列数和页面显示）
            self.load_tasks()
            
            # 如果之前是全屏模式，确保保持全屏状态
            if was_fullscreen:
                # 延迟恢复全屏状态，确保 load_tasks() 完成
                from PyQt5.QtCore import QTimer
                def restore_fullscreen():
                    if hasattr(self, 'is_fullscreen') and not self.is_fullscreen:
                        # 如果全屏状态丢失，重新进入全屏
                        if hasattr(self, 'enter_fullscreen'):
                            self.enter_fullscreen()
                QTimer.singleShot(100, restore_fullscreen)
            
            # 滚动到该图表（延迟执行，确保加载完成）
            from PyQt5.QtCore import QTimer
            def scroll_to_chart():
                if stock_code in self.chart_widgets:
                    chart_data = self.chart_widgets[stock_code]
                    # 确保图表可见
                    if 'container' in chart_data and chart_data['container']:
                        chart_data['container'].show()
                        chart_data['container'].raise_()
                        # 滚动到该容器
                        self.scroll_area.ensureWidgetVisible(chart_data['container'])
                    if 'chart' in chart_data and chart_data['chart']:
                        chart_data['chart'].show()
            QTimer.singleShot(200, scroll_to_chart)  # 增加延迟，确保load_tasks完成
            
        except Exception as e:
            self.logger.error(f"切换到单列显示股票失败: {str(e)}", exc_info=True)
    
    def prev_page(self):
        """上一页"""
        # 降级为 DEBUG：用户操作日志用于排查，不希望实盘/截图时刷屏
        self.logger.logger.debug("[用户操作] 上一页")
        if self.current_page > 0:
            self.current_page -= 1
            self._load_page_fast()
    
    def next_page(self):
        """下一页"""
        # 降级为 DEBUG：用户操作日志用于排查，不希望实盘/截图时刷屏
        self.logger.logger.debug("[用户操作] 下一页")
        items_per_page = self.tasks_per_page.get(self.columns, 1)
        total_pages = (len(self.all_tasks) + items_per_page - 1) // items_per_page if items_per_page > 0 else 1
        
        if self.current_page < total_pages - 1:
            self.current_page += 1
            self._load_page_fast()
    
    def _load_page_fast(self):
        """快速加载页面（如果图表都在缓存中则快速切换，否则完整加载）"""
        try:
            import time as _time
            _t0 = _time.time()
            # 计算当前页的任务
            items_per_page = self.tasks_per_page.get(self.columns, 1)
            start_idx = self.current_page * items_per_page
            end_idx = start_idx + items_per_page
            current_page_tasks = self.all_tasks[start_idx:end_idx]
            
            # 检查是否所有图表都在缓存中
            all_cached = True
            for task_id, task in current_page_tasks:
                stock_code = str(task.get('stock_code', ''))
                if stock_code and stock_code not in self._chart_cache:
                    all_cached = False
                    break
            
            if all_cached:
                # 所有图表都在缓存中，使用快速重排
                self._rearrange_charts_fast()
            else:
                # 有些图表不在缓存中，需要完整加载
                self.load_tasks()
            
            elapsed = _time.time() - _t0
            if elapsed > 0.3:
                self.logger.warning(
                    f"[性能监控] _load_page_fast 耗时: {elapsed:.3f}s all_cached={all_cached} "
                    f"columns={self.columns} page={self.current_page}"
                )
        except Exception as e:
            self.logger.error(f"快速加载页面失败: {str(e)}", exc_info=True)
            # 失败时回退到完整加载
            self.load_tasks()
    
    def move_task_to_top(self, stock_code):
        """将任务移到最前面（置顶）"""
        try:
            # 找到任务在列表中的位置
            current_idx = next((idx for idx, (tid, t) in enumerate(self.all_tasks) if t.get('stock_code') == stock_code), -1)
            
            if current_idx <= 0:
                return  # 已经在最前面，无需置顶
            
            current_task_id, current_task = self.all_tasks[current_idx]
            
            # 获取最小的order_index
            min_order = min([t.get('order_index', idx) for idx, (tid, t) in enumerate(self.all_tasks)], default=0)
            
            # 将当前任务之前的所有任务的order_index加1
            for idx in range(current_idx):
                task_id, task = self.all_tasks[idx]
                old_order = task.get('order_index', idx)
                task['order_index'] = old_order + 1
                self.task_manager.tasks[task_id] = task
            
            # 将当前任务的order_index设为最小-1（确保在最前面）
            current_task['order_index'] = min_order - 1
            
            # 更新任务管理器中的任务
            self.task_manager.tasks[current_task_id] = current_task
            
            # 轻量级重排（不重新创建图表）
            self._rearrange_charts_fast()
            
            # 延迟保存任务（避免阻塞 UI）
            QTimer.singleShot(100, lambda: self._save_tasks_async())
            
            self.logger.info(f"任务 {stock_code} 已置顶")
        except Exception as e:
            self.logger.error(f"置顶任务失败: {str(e)}", exc_info=True)
            QMessageBox.warning(self, "错误", f"置顶任务失败: {str(e)}")
    
    def move_task_up(self, stock_code):
        """将任务上移一位"""
        try:
            # 找到任务在列表中的位置
            current_idx = next((idx for idx, (tid, t) in enumerate(self.all_tasks) if t.get('stock_code') == stock_code), -1)
            
            if current_idx <= 0:
                return  # 已经在最前面，无法上移
            
            # 与前一个任务交换order_index
            prev_idx = current_idx - 1
            current_task_id, current_task = self.all_tasks[current_idx]
            prev_task_id, prev_task = self.all_tasks[prev_idx]
            
            # 交换order_index
            current_order = current_task.get('order_index', current_idx)
            prev_order = prev_task.get('order_index', prev_idx)
            
            current_task['order_index'] = prev_order
            prev_task['order_index'] = current_order
            
            # 更新任务管理器中的任务
            self.task_manager.tasks[current_task_id] = current_task
            self.task_manager.tasks[prev_task_id] = prev_task
            
            # 轻量级重排（不重新创建图表）
            self._rearrange_charts_fast()
            
            # 延迟保存任务（避免阻塞 UI）
            QTimer.singleShot(100, lambda: self._save_tasks_async())
            
            self.logger.info(f"任务 {stock_code} 已上移")
        except Exception as e:
            self.logger.error(f"上移任务失败: {str(e)}", exc_info=True)
            QMessageBox.warning(self, "错误", f"上移任务失败: {str(e)}")
    
    def move_task_down(self, stock_code):
        """将任务下移一位"""
        try:
            # 找到任务在列表中的位置
            current_idx = next((idx for idx, (tid, t) in enumerate(self.all_tasks) if t.get('stock_code') == stock_code), -1)
            
            if current_idx < 0 or current_idx >= len(self.all_tasks) - 1:
                return  # 已经在最后面，无法下移
            
            # 与下一个任务交换order_index
            next_idx = current_idx + 1
            current_task_id, current_task = self.all_tasks[current_idx]
            next_task_id, next_task = self.all_tasks[next_idx]
            
            # 交换order_index
            current_order = current_task.get('order_index', current_idx)
            next_order = next_task.get('order_index', next_idx)
            
            current_task['order_index'] = next_order
            next_task['order_index'] = current_order
            
            # 更新任务管理器中的任务
            self.task_manager.tasks[current_task_id] = current_task
            self.task_manager.tasks[next_task_id] = next_task
            
            # 轻量级重排（不重新创建图表）
            self._rearrange_charts_fast()
            
            # 延迟保存任务（避免阻塞 UI）
            QTimer.singleShot(100, lambda: self._save_tasks_async())
            
            self.logger.info(f"任务 {stock_code} 已下移")
        except Exception as e:
            self.logger.error(f"下移任务失败: {str(e)}", exc_info=True)
            QMessageBox.warning(self, "错误", f"下移任务失败: {str(e)}")
    
    def move_task_to_bottom(self, stock_code):
        """将任务移到最后面（置底）"""
        try:
            # 找到任务在列表中的位置
            current_idx = next((idx for idx, (tid, t) in enumerate(self.all_tasks) if t.get('stock_code') == stock_code), -1)
            
            if current_idx < 0 or current_idx >= len(self.all_tasks) - 1:
                return  # 已经在最后面，无需置底
            
            current_task_id, current_task = self.all_tasks[current_idx]
            
            # 获取最大的order_index
            max_order = max([t.get('order_index', idx) for idx, (tid, t) in enumerate(self.all_tasks)], default=0)
            
            # 将当前任务之后的所有任务的order_index减1
            for idx in range(current_idx + 1, len(self.all_tasks)):
                task_id, task = self.all_tasks[idx]
                old_order = task.get('order_index', idx)
                task['order_index'] = old_order - 1
                self.task_manager.tasks[task_id] = task
            
            # 将当前任务的order_index设为最大+1（确保在最后面）
            current_task['order_index'] = max_order + 1
            
            # 更新任务管理器中的任务
            self.task_manager.tasks[current_task_id] = current_task
            
            # 轻量级重排（不重新创建图表）
            self._rearrange_charts_fast()
            
            # 延迟保存任务（避免阻塞 UI）
            QTimer.singleShot(100, lambda: self._save_tasks_async())
            
            self.logger.info(f"任务 {stock_code} 已置底")
        except Exception as e:
            self.logger.error(f"置底任务失败: {str(e)}", exc_info=True)
            QMessageBox.warning(self, "错误", f"置底任务失败: {str(e)}")
    
    def _rearrange_charts_fast(self):
        """轻量级重排图表位置（不重新创建图表，只调整布局位置）"""
        run_snap = {}
        try:
            import time as _time
            _t0 = _time.time()
            layout_changed = getattr(self, '_last_rearrange_columns', None) != self.columns
            # 性能优化：禁用 UI 更新
            self.setUpdatesEnabled(False)

            # 切列时第 4 列控件曾被先压成 0 宽，易误触「启动/暂停」；先快照并屏蔽按钮信号
            run_snap = self._snapshot_chart_run_states()
            self._block_chart_toggle_signals(True)
            
            # 重新排序 all_tasks
            self.all_tasks.sort(key=lambda x: x[1].get('order_index', 0))
            
            # 计算当前页的任务
            items_per_page = self.tasks_per_page.get(self.columns, 1)
            start_idx = self.current_page * items_per_page
            end_idx = start_idx + items_per_page
            current_page_tasks = self.all_tasks[start_idx:end_idx]

            # 清空当前页的图表引用（需要先记住旧可见股票，便于只隐藏差异）
            old_visible_stock_codes = set(self.chart_widgets.keys())
            self.chart_widgets.clear()
            
            # 当前页目标股票集合（用于仅隐藏“旧页但不在新页”的控件）
            target_stock_codes = set()
            for _, task in current_page_tasks:
                sc = str(task.get('stock_code', '') or '').strip()
                if sc:
                    target_stock_codes.add(sc)

            # 关键：先从 grid 卸下旧控件，再改列 stretch/最小宽。
            # 旧逻辑先把第 4 列 minWidth 置 0，控件仍停在 col=3，会被压扁并可能误触暂停。
            self._detach_grid_widgets(old_visible_stock_codes | target_stock_codes)

            if layout_changed:
                for col_idx in range(10):
                    self.grid_layout.setColumnStretch(col_idx, 0)
                    self.grid_layout.setColumnMinimumWidth(col_idx, 0)
                for col_idx in range(self.columns):
                    self.grid_layout.setColumnStretch(col_idx, 1)
                    self.grid_layout.setColumnMinimumWidth(col_idx, 100)

                if self.columns >= 4:
                    self.scroll_area.setMinimumHeight(280)
                elif self.columns == 3:
                    self.scroll_area.setMinimumHeight(280)
                else:
                    self.scroll_area.setMinimumHeight(600)
                self._last_rearrange_columns = self.columns
            
            # 停止当前的加载定时器，清空待加载队列（将根据新页面重新填充）
            if self._load_timer.isActive():
                self._load_timer.stop()
            self._pending_charts.clear()
            
            # 按新顺序重新添加到布局
            row = 0
            col = 0
            max_row = -1  # 本页实际最大行号，用于 setRowStretch 与 load_tasks 一致
            charts_need_data = []  # 收集需要加载数据的图表
            for task_id, task in current_page_tasks:
                stock_code = str(task.get('stock_code', ''))
                if not stock_code:
                    continue
                
                # 从缓存中获取容器
                if stock_code in self._chart_cache:
                    cached_data = self._chart_cache[stock_code]
                    chart_container = cached_data.get('container')
                    chart = cached_data.get('chart')
                    if chart_container:
                        self.grid_layout.addWidget(chart_container, row, col)
                        max_row = max(max_row, row)
                        # 确保容器和图表可见
                        chart_container.show()
                        if chart:
                            chart.show()
                            # 同步当前列数并刷新显示，避免从多列切回1列时标签不显示（只有坐标轴和节点）
                            chart.current_columns = self.columns
                            if hasattr(chart, 'set_controls_visible'):
                                chart.set_controls_visible(self.columns == 1)
                            # 只在 1 列时强制重绘；2 列尽量避免 Matplotlib 重绘以降低切列卡顿
                            need_force_redraw = layout_changed and (self.columns == 1)
                            try:
                                if need_force_redraw:
                                    chart.update_chart()
                            except Exception:
                                pass
                            # 检查图表是否已有数据，没有则加入待加载队列
                            has_data = hasattr(chart, 'price_data') and chart.price_data and len(chart.price_data) > 0
                            if not has_data:
                                charts_need_data.append((chart, self.qmt_adapter))
                        
                        # 更新 chart_widgets 引用
                        self.chart_widgets[stock_code] = {
                            'chart': chart,
                            'container': chart_container,
                            'task': task
                        }
                        
                        # 更新移动按钮状态（缓存按钮引用，避免重复 findChildren）
                        current_idx = next(
                            (idx for idx, (tid, t) in enumerate(self.all_tasks) if t.get('stock_code') == stock_code),
                            -1
                        )
                        move_buttons = cached_data.get('move_buttons')
                        if not move_buttons:
                            move_buttons = {}
                            for widget in chart_container.findChildren(QPushButton):
                                tooltip = widget.toolTip()
                                if tooltip in ("置顶任务（移到最前面）", "上移任务", "下移任务", "置底任务（移到最后面）"):
                                    move_buttons[tooltip] = widget
                            cached_data['move_buttons'] = move_buttons

                        up_enabled = current_idx > 0
                        down_enabled = (current_idx < len(self.all_tasks) - 1 and current_idx >= 0)
                        if "置顶任务（移到最前面）" in move_buttons:
                            move_buttons["置顶任务（移到最前面）"].setEnabled(up_enabled)
                        if "上移任务" in move_buttons:
                            move_buttons["上移任务"].setEnabled(up_enabled)
                        if "下移任务" in move_buttons:
                            move_buttons["下移任务"].setEnabled(down_enabled)
                        if "置底任务（移到最后面）" in move_buttons:
                            move_buttons["置底任务（移到最后面）"].setEnabled(down_enabled)
                        
                        col += 1
                        if col >= self.columns:
                            col = 0
                            row += 1
            
            # 只隐藏当前页之外的旧控件，避免全量 hide 导致重排更慢
            for sc in old_visible_stock_codes - target_stock_codes:
                cached = self._chart_cache.get(sc)
                if not cached:
                    continue
                chart_container = cached.get('container') if isinstance(cached, dict) else None
                chart = cached.get('chart') if isinstance(cached, dict) else None
                if chart_container:
                    chart_container.hide()
                if chart:
                    chart.hide()
            
            # 每行等高：与 load_tasks 相同。快速重排（尤其从 1 列切到多列）若只把 stretch 清零不恢复，会出现第一行极矮、末行占满剩余高度。
            for row_idx in range(20):
                self.grid_layout.setRowStretch(row_idx, 0)
            if max_row >= 0:
                for row_idx in range(max_row + 1):
                    self.grid_layout.setRowStretch(row_idx, 1)
            
            # 将需要加载数据的图表加入队列
            if charts_need_data:
                self._pending_charts.extend(charts_need_data)
                # 启动加载定时器
                self._load_timer.start(50)
            
            # 更新分页信息和按钮状态
            total_pages = (len(self.all_tasks) + items_per_page - 1) // items_per_page if items_per_page > 0 else 1
            page_info = f"第 {self.current_page + 1}/{total_pages} 页"
            self.status_label.setText(f"{len(self.all_tasks)} 个任务 - {page_info}")
            
            # 更新分页按钮状态
            self.prev_btn.setEnabled(self.current_page > 0)
            self.next_btn.setEnabled(self.current_page < total_pages - 1)
            
        finally:
            try:
                self._restore_chart_run_states(run_snap)
            except Exception as e:
                self.logger.warning(f"切列后恢复任务运行态失败: {e}")
            try:
                self._block_chart_toggle_signals(False)
            except Exception:
                pass
            # 性能优化：重新启用 UI 更新
            self.setUpdatesEnabled(True)
            if getattr(self, 'columns', 1) in (3, 4):
                QTimer.singleShot(0, self._apply_chart_heights_from_viewport)
            try:
                elapsed = _time.time() - _t0
                if elapsed > 0.2:
                    self.logger.warning(
                        f"[性能监控] _rearrange_charts_fast 耗时: {elapsed:.3f}s columns={self.columns} page={self.current_page} "
                        f"pending_charts={len(self._pending_charts) if hasattr(self, '_pending_charts') else 0}"
                    )
            except Exception:
                pass

            # 触发一次几何重算，仅在列数变化时需要
            try:
                if locals().get('layout_changed'):
                    if hasattr(self, 'charts_container') and self.charts_container:
                        self.charts_container.updateGeometry()
                    self.scroll_area.updateGeometry()
            except Exception:
                pass
    
    def _save_tasks_async(self):
        """异步保存任务顺序到文件"""
        try:
            all_tasks_list = [task for _, task in sorted(self.all_tasks, key=lambda x: x[1].get('order_index', 0))]
            self.task_manager.save_tasks(all_tasks_list)
        except Exception as e:
            self.logger.error(f"保存任务顺序失败: {str(e)}", exc_info=True)
        
    def show_add_task_dialog(self):
        """显示添加任务对话框"""
        dialog = AddTaskDialog(self)
        
        if dialog.exec_() == QDialog.Accepted:
            try:
                stock_code = dialog.stock_code_edit.text().strip()
                stock_name = dialog.stock_name_edit.text().strip()
                
                # 如果只输入了股票名称，尝试通过名称查询股票代码
                if not stock_code and stock_name:
                    stock_code = dialog._get_stock_code_by_name(stock_name)
                    if not stock_code:
                        QMessageBox.warning(self, "警告", f"未找到名称为\"{stock_name}\"的股票，请检查输入")
                        return
                
                if not stock_code:
                    QMessageBox.warning(self, "警告", "请输入股票代码或股票名称")
                    return
                
                # 如果输入的是6位数字代码，自动补充后缀
                stock_code = self._get_full_stock_code(stock_code)
                
                # 如果没有股票名称，尝试通过代码查询名称
                if not stock_name:
                    stock_name = dialog._get_stock_name(stock_code)
                    if not stock_name:
                        stock_name = "未知"
                
                # 检查是否已经存在该股票的任务
                if hasattr(self, 'task_manager') and self.task_manager:
                    existing_tasks = self.task_manager.tasks if hasattr(self.task_manager, 'tasks') else {}
                    for task_id, task in existing_tasks.items():
                        if isinstance(task, dict):
                            existing_stock_code = task.get('stock_code', '')
                            if existing_stock_code == stock_code:
                                # 获取已存在任务的股票名称
                                existing_stock_name = task.get('stock_name', '未知')
                                reply = QMessageBox.question(
                                    self, 
                                    "任务已存在", 
                                    f"该股票已存在任务！\n\n"
                                    f"股票代码: {stock_code}\n"
                                    f"股票名称: {existing_stock_name}\n\n"
                                    f"一只股票只能有一个任务列表。\n\n"
                                    f"是否跳转到该任务？",
                                    QMessageBox.Yes | QMessageBox.No
                                )
                                
                                # 如果用户选择跳转，则打开该任务的图表
                                if reply == QMessageBox.Yes:
                                    self.edit_task_chart(stock_code)
                                
                                return
                
                # 创建新任务
                import uuid
                from datetime import datetime
                
                task_id = str(uuid.uuid4())
                
                # 计算新任务的order_index（插入到当前页第一个位置）
                items_per_page = self.tasks_per_page.get(self.columns, 1)
                insert_pos = self.current_page * items_per_page
                # 如果已有任务，使用最大order_index+1；否则使用插入位置的索引
                if hasattr(self, 'all_tasks') and self.all_tasks:
                    max_order = max([t.get('order_index', idx) for idx, (tid, t) in enumerate(self.all_tasks)], default=-1)
                    new_order_index = insert_pos if insert_pos <= max_order else max_order + 1
                else:
                    new_order_index = insert_pos
                
                task_data = {
                    'stock_code': stock_code,
                    'stock_name': stock_name,
                    'init_volume': 0,
                    'volume': 0,
                    'init_cost': 0,
                    'buy_date': datetime.now().strftime('%Y-%m-%d'),
                    'hold_days': 0,
                    'base_price': 0,
                    'strategy': '规则任务',
                    'status': '未运行',
                    'task_id': task_id,
                    'create_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'order_index': new_order_index,  # 添加排序索引
                    'params': {
                        'rules': [],  # 使用新的规则列表结构
                        # 保留旧的策略参数以兼容策略执行
                        'up_threshold': 5.0,
                        'down_threshold': 3.0,
                        'sell_times': 3,
                        'clear_time': '00:00:00',
                        'cycle_times': 0
                    }
                }
                
                # 添加到任务管理器
                self.task_manager.tasks[task_id] = task_data
                self.task_manager.task_params[task_id] = task_data['params']
                
                # 重新获取任务列表（确保包含新任务）
                all_tasks_dict = self.task_manager.tasks
                
                # 将任务字典转换为列表，并按原顺序排序
                # 先获取当前任务列表的顺序
                current_tasks_list = list(all_tasks_dict.items())
                
                # 计算插入位置：插入到当前页第一个任务的位置，这样新任务会显示在当前页
                items_per_page = self.tasks_per_page.get(self.columns, 1)
                # 计算当前页第一个任务的全局索引位置
                insert_pos = self.current_page * items_per_page
                
                # 确保插入位置不超出列表长度
                if insert_pos > len(current_tasks_list):
                    insert_pos = len(current_tasks_list)
                
                # 将新任务插入到指定位置
                new_task_item = (task_id, task_data)
                
                # 移除新任务（如果已经存在）
                current_tasks_list = [(tid, t) for tid, t in current_tasks_list if tid != task_id]
                
                # 插入到指定位置
                current_tasks_list.insert(insert_pos, new_task_item)
                
                # 按照新的顺序重新组织任务管理器中的任务
                # 注意：Python 3.7+ 字典保持插入顺序，我们需要重新构建
                # 但这里我们用列表保存顺序，然后按顺序保存
                
                # 保存到文件（按新顺序）
                self.task_manager.save_tasks([task for _, task in current_tasks_list])
                
                # 重新加载任务管理器中的任务（确保包含新任务）
                self.task_manager.load_tasks()
                
                self.logger.info(f"任务已创建: {stock_code}，插入到位置 {insert_pos}（当前页第1个位置）")
                
                # 重新加载（会按照保存的顺序加载）
                # 强制重新加载任务管理器，确保新任务被读取
                if hasattr(self.task_manager, 'load_tasks'):
                    self.task_manager.load_tasks()
                
                # 添加股票到订阅列表，确保能获取实时价格数据
                if hasattr(self, 'qmt_adapter') and self.qmt_adapter:
                    try:
                        if self.qmt_adapter.ensure_subscribed(stock_code):
                            subscribed = self.qmt_adapter.get_subscribed_codes()
                            self.logger.info(
                                f"已将股票 {stock_code} 添加到订阅列表，当前订阅数量: {len(subscribed)}"
                            )
                    except Exception as e:
                        self.logger.warning(f"添加股票 {stock_code} 到订阅列表失败: {str(e)}")
                
                # 重置加载标志，确保能重新加载
                self._loading_tasks = False
                self.load_tasks()
                
                # 立即打开这个新任务的图表，让用户设置交易规则
                self.edit_task_chart(stock_code)
                
            except Exception as e:
                self.logger.error(f"创建任务失败: {str(e)}", exc_info=True)
                QMessageBox.critical(self, "错误", f"创建任务失败: {str(e)}")
                
    def edit_task_chart(self, stock_code):
        """编辑任务图表 - 聚焦并提示设置交易规则"""
        # 如果股票已在当前页面显示，直接跳转
        if stock_code in self.chart_widgets:
            chart_data = self.chart_widgets[stock_code]
            chart = chart_data['chart']
            
            # 滚动到该图表
            chart_data['container'].raise_()
            
            # 提示用户可以使用规则工具
            QMessageBox.information(
                self,
                "设置交易规则",
                "📊 现在可以使用工具栏的规则按钮设置交易策略：\n\n"
                "💚 单点买入/卖出：点击按钮，在图表上点击设置价格和数量\n"
                "📦 笼子买入/卖出：点击按钮，拖动创建价格区间\n"
                "🔃 弹性买入/弹性卖出：价格突破后自动回调买卖\n"
                "⊞ 网格买入/卖出：拖动创建网格，支持批量交易\n\n"
                "✅ 所有规则均可拖动调整，右键可禁用/删除"
            )
        else:
            # 股票不在当前页面，需要找到它所在的页面
            # 在所有任务中查找该股票
            if hasattr(self, 'all_tasks') and self.all_tasks:
                for idx, (task_id, task) in enumerate(self.all_tasks):
                    if task.get('stock_code') == stock_code:
                        # 计算该任务在哪一页
                        items_per_page = self.tasks_per_page.get(self.columns, 1)
                        target_page = idx // items_per_page
                        
                        # 切换到目标页面
                        self.current_page = target_page
                        self._load_page_fast()
                        
                        # 再次尝试跳转（此时应该在当前页面了）
                        if stock_code in self.chart_widgets:
                            chart_data = self.chart_widgets[stock_code]
                            
                            # 滚动到该图表
                            chart_data['container'].raise_()
                            
                            # 提示用户可以使用规则工具
                            QMessageBox.information(
                                self,
                                "设置交易规则",
                                "📊 现在可以使用工具栏的规则按钮设置交易策略：\n\n"
                                "💚 单点买入/卖出：点击按钮，在图表上点击设置价格和数量\n"
                                "📦 笼子买入/卖出：点击按钮，拖动创建价格区间\n"
                                "🔃 弹性买入/弹性卖出：价格突破后自动回调买卖\n"
                                "⊞ 网格买入/卖出：拖动创建网格，支持批量交易\n\n"
                                "✅ 所有规则均可拖动调整，右键可禁用/删除"
                            )
                        return
                
                # 如果没找到
                self.logger.warning(f"未找到股票 {stock_code} 的任务")
            else:
                self.logger.warning(f"任务列表为空，无法跳转到 {stock_code}")
            
    def delete_task(self, stock_code):
        """删除任务"""
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Question)
        msg.setWindowTitle("确认删除")
        msg.setText(f"确定要删除 {stock_code} 的任务吗？")
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.Yes)
        yes_btn = msg.button(QMessageBox.Yes)
        no_btn = msg.button(QMessageBox.No)
        if yes_btn is not None:
            yes_btn.setText("Yes")
            # 默认/焦点按钮：深蓝高亮，与 No 明显区分
            yes_btn.setStyleSheet(
                "QPushButton {"
                "  background-color: #1976D2; color: white; font-weight: bold;"
                "  border: 2px solid #0D47A1; border-radius: 4px; padding: 6px 18px;"
                "  min-width: 72px;"
                "}"
                "QPushButton:focus, QPushButton:default {"
                "  background-color: #1565C0;"
                "  border: 3px solid #FF9800;"
                "  outline: none;"
                "}"
                "QPushButton:hover { background-color: #1E88E5; }"
                "QPushButton:pressed { background-color: #0D47A1; }"
            )
            yes_btn.setDefault(True)
            yes_btn.setAutoDefault(True)
        if no_btn is not None:
            no_btn.setText("No")
            no_btn.setStyleSheet(
                "QPushButton {"
                "  background-color: #EEEEEE; color: #424242;"
                "  border: 1px solid #BDBDBD; border-radius: 4px; padding: 6px 18px;"
                "  min-width: 72px;"
                "}"
                "QPushButton:focus {"
                "  background-color: #E0E0E0;"
                "  border: 3px solid #FF9800;"
                "  outline: none;"
                "}"
                "QPushButton:hover { background-color: #E0E0E0; }"
                "QPushButton:pressed { background-color: #BDBDBD; }"
            )
            no_btn.setDefault(False)
            no_btn.setAutoDefault(False)
        # 弹出后把焦点放到 Yes，回车即可确认
        if yes_btn is not None:
            QTimer.singleShot(0, yes_btn.setFocus)
        reply = msg.exec_()
        if reply == QMessageBox.Yes:
            try:
                # 性能优化：禁用 UI 更新
                self.setUpdatesEnabled(False)
                
                task_id_to_delete = None
                for task_id, task in list(self.task_manager.tasks.items()):
                    if task.get('stock_code') == stock_code:
                        task_id_to_delete = task_id
                        break

                if not task_id_to_delete:
                    self.logger.warning(f"未找到股票 {stock_code} 的任务")
                    self.setUpdatesEnabled(True)
                    return

                if task_id_to_delete in self.task_manager.running_tasks:
                    self.task_manager.stop_task(task_id_to_delete)

                self.task_manager.delete_task(task_id_to_delete)
                
                # 从 all_tasks 列表中移除
                self.all_tasks = [(tid, t) for tid, t in self.all_tasks if t.get('stock_code') != stock_code]
                
                # 清理缓存中的图表组件
                if stock_code in self._chart_cache:
                    cached_data = self._chart_cache[stock_code]
                    # 先从布局中移除
                    if 'container' in cached_data and cached_data['container']:
                        cached_data['container'].setParent(None)
                    # 删除图表组件
                    if 'chart' in cached_data:
                        cached_data['chart'].deleteLater()
                    if 'container' in cached_data:
                        cached_data['container'].deleteLater()
                    del self._chart_cache[stock_code]
                
                # 从当前显示的图表中移除
                if stock_code in self.chart_widgets:
                    del self.chart_widgets[stock_code]

                # 删除后若本页名额空缺，用后续页任务补齐；页码越界则回退到最后一页
                items_per_page = self.tasks_per_page.get(self.columns, 1)
                if self.all_tasks and items_per_page > 0:
                    total_pages = (len(self.all_tasks) + items_per_page - 1) // items_per_page
                    if self.current_page >= total_pages:
                        self.current_page = max(0, total_pages - 1)
                else:
                    self.current_page = 0

                # 用 _load_page_fast：后续页图表可能不在缓存，仅 rearrange 补不上来
                self._load_page_fast()
                
                # 性能优化：重新启用 UI 更新
                self.setUpdatesEnabled(True)
                
                # 延迟保存任务（避免阻塞 UI）
                QTimer.singleShot(100, lambda: self._save_tasks_async())
                
                # 清理股票订阅（如果股票不在持仓、任务或其他监控中，则取消订阅）
                if hasattr(self, 'ext') and self.ext:
                    if hasattr(self.ext, '_cleanup_stock_subscription'):
                        self.ext._cleanup_stock_subscription(stock_code)
                
                self.logger.info(f"任务已删除: {stock_code}")
                
            except Exception as e:
                self.setUpdatesEnabled(True)
                self.logger.error(f"删除任务失败: {str(e)}", exc_info=True)
                QMessageBox.critical(self, "错误", f"删除任务失败: {str(e)}")
                
    def on_chart_price_changed(self, stock_code, price_type, new_price):
        """图表价格改变 - 自动保存"""
        try:
            if stock_code in self.chart_widgets:
                chart_data = self.chart_widgets[stock_code]
                task = chart_data['task']
                
                # 更新任务参数
                if 'params' not in task:
                    task['params'] = {}
                    
                task['params'][f'{price_type}_price'] = new_price
                
                # 立即保存
                all_tasks = list(self.task_manager.tasks.values())
                self.task_manager.save_tasks(all_tasks)
                
                self.logger.info(f"价格已保存: {stock_code}, {price_type}={new_price}")
                
                # 更新状态
                self.status_label.setText(f"已更新 {stock_code} 的{price_type}点")
                
        except Exception as e:
            self.logger.error(f"保存价格失败: {str(e)}", exc_info=True)
            
    def update_all_charts_price(self):
        """更新所有图表的实时价格"""
        try:
            from datetime import datetime, time as dt_time
            
            # 判断是否在交易时段
            now = datetime.now()
            current_time = now.time()
            is_trading_time = False
            if (dt_time(9, 30) <= current_time <= dt_time(11, 30)) or \
               (dt_time(13, 0) <= current_time <= dt_time(15, 0)):
                is_trading_time = True
            
            for stock_code, chart_data in self.chart_widgets.items():
                chart = chart_data['chart']
                
                # 从QMT适配器的任务管理器获取实时价格
                if self.qmt_adapter and hasattr(self.qmt_adapter, 'task_manager') and self.qmt_adapter.task_manager:
                    if hasattr(self.qmt_adapter.task_manager, 'latest_prices'):
                        latest_price = self.qmt_adapter.task_manager.latest_prices.get(stock_code)
                        if latest_price:
                            chart.update_current_price(latest_price)
                        
        except Exception as e:
            # 静默失败，不影响UI
            pass
    
    def on_tick_data(self, tick_data):
        """
        接收并分发tick数据到对应的图表
        tick_data格式: {
            'stock_code': str,
            'lastPrice': float,
            'lastClose': float,
            'askPrice': list,
            'bidPrice': list,
            'askVol': list,
            'bidVol': list,
            'time': datetime
        }
        """
        import time as _time
        _t0 = _time.time()
        try:
            stock_code = tick_data.get('stock_code', '')
            if not stock_code:
                return
            
            # 查找对应的图表（同时查缓存，确保非当前页的图表如定时清仓也能收到 tick 并执行）
            chart_data = self._chart_cache.get(stock_code) or self.chart_widgets.get(stock_code)
            if chart_data and 'chart' in chart_data:
                chart = chart_data['chart']
                # 分发tick数据到图表
                chart.on_tick_data(tick_data)
            elapsed = _time.time() - _t0
            # 与 stock_chart 一致：仅对明显卡顿记 WARNING（多股同屏正常重绘常 0.25~0.4s）
            if elapsed > 0.5:
                self.logger.warning(f"[性能监控] tasks_charts_view.on_tick_data {stock_code} 耗时: {elapsed:.3f}秒")
        except Exception as e:
            if _time.time() - _t0 > 0.5:
                self.logger.warning(f"[性能监控] tasks_charts_view.on_tick_data 异常耗时: {_time.time() - _t0:.3f}秒")
            self.logger.error(f"分发tick数据失败 {stock_code}: {str(e)}", exc_info=True)
    
    def show_execution_records(self):
        """显示执行记录对话框"""
        try:
            from ui.execution_records_dialog import ExecutionRecordsDialog
            
            dialog = ExecutionRecordsDialog(self)
            dialog.exec_()
        except Exception as e:
            self.logger.error(f"打开执行记录对话框失败: {str(e)}", exc_info=True)
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(self, "错误", f"打开执行记录对话框失败：{str(e)}")
    
    def _resolve_task_run_state(self, task_id, task, chart=None):
        """解析任务运行/暂停显示状态。

        优先信任：图表组件当前态、TaskManager.running_tasks；
        再回退 params/status。避免切列重排后用陈旧 params 把「运行中」刷成「已暂停」。
        """
        params = task.get('params') if isinstance(task.get('params'), dict) else {}
        in_tm = bool(
            task_id and task_id in getattr(self.task_manager, 'running_tasks', {})
        )
        live_running = bool(
            chart is not None
            and getattr(chart, 'task_running', False)
            and not getattr(chart, 'task_paused', False)
        )
        live_paused = bool(
            chart is not None
            and getattr(chart, 'task_paused', False)
            and not getattr(chart, 'task_running', False)
        )
        if live_running or in_tm or task.get('status') == '运行中' or bool(params.get('task_running')):
            return True, False
        if live_paused or bool(params.get('task_paused')):
            return False, True
        return False, False

    def _block_chart_toggle_signals(self, blocked: bool) -> None:
        """切列/翻页重排时屏蔽启动暂停按钮，防止布局挤压导致误触 pause_task。"""
        cache = getattr(self, '_chart_cache', None) or {}
        for cached in cache.values():
            if not isinstance(cached, dict):
                continue
            chart = cached.get('chart')
            if not chart:
                continue
            btn = getattr(chart, 'toggle_btn', None)
            if btn is not None:
                try:
                    btn.blockSignals(blocked)
                except Exception:
                    pass

    def _snapshot_chart_run_states(self) -> dict:
        """重排前快照各图运行态，供重排后恢复。"""
        snap = {}
        cache = getattr(self, '_chart_cache', None) or {}
        for stock_code, cached in cache.items():
            if not isinstance(cached, dict):
                continue
            chart = cached.get('chart')
            if not chart:
                continue
            snap[str(stock_code)] = (
                bool(getattr(chart, 'task_running', False)),
                bool(getattr(chart, 'task_paused', False)),
            )
        return snap

    def _restore_chart_run_states(self, snap: dict) -> None:
        """重排后按快照恢复运行态；若误触暂停则拉回运行中。

        注意：快照为「未运行」时，不得覆盖 TaskManager 里已在跑的任务
        （预约重载：先 start_all 再 load_tasks 时，旧图快照全是未运行）。
        """
        if not snap:
            return
        cache = getattr(self, '_chart_cache', None) or {}
        running_tasks = getattr(self.task_manager, 'running_tasks', {}) if self.task_manager else {}
        for stock_code, (was_running, was_paused) in snap.items():
            cached = cache.get(stock_code)
            if not isinstance(cached, dict):
                continue
            chart = cached.get('chart')
            if not chart:
                continue
            task_id = getattr(chart, 'task_id', None)
            in_tm = bool(task_id and task_id in running_tasks)

            # 重排前是运行中，重排后若变成暂停/未运行 → 视为误触，恢复 UI（并尽量重启 TM）
            if was_running and not was_paused:
                now_running = bool(getattr(chart, 'task_running', False)) and not bool(
                    getattr(chart, 'task_paused', False)
                )
                if now_running:
                    continue
                if self.task_manager and task_id:
                    if not in_tm:
                        try:
                            # 误触 pause 会 stop_task；尝试重新启动
                            self.task_manager.start_task(task_id)
                        except Exception as e:
                            self.logger.warning(
                                f"切列后恢复运行失败 {stock_code}: {e}"
                            )
                try:
                    chart.set_task_status(True, False)
                    if getattr(chart, 'task', None) and isinstance(chart.task.get('params'), dict):
                        chart.task['params']['task_running'] = True
                        chart.task['params']['task_paused'] = False
                except Exception:
                    pass
            else:
                # 快照未运行，但 TM 已在跑 / 组件已标运行 → 保留运行态，勿刷回未运行
                if in_tm or (
                    bool(getattr(chart, 'task_running', False))
                    and not bool(getattr(chart, 'task_paused', False))
                ):
                    try:
                        chart.set_task_status(True, False)
                        if getattr(chart, 'task', None) and isinstance(chart.task.get('params'), dict):
                            chart.task['params']['task_running'] = True
                            chart.task['params']['task_paused'] = False
                    except Exception:
                        pass
                    continue
                try:
                    chart.set_task_status(was_running, was_paused)
                except Exception:
                    pass

    def sync_charts_with_running_tasks(self) -> int:
        """按 TaskManager.running_tasks 同步当前缓存/本页图表的运行态。返回同步为运行中的数量。"""
        if not self.task_manager:
            return 0
        running_tasks = getattr(self.task_manager, 'running_tasks', {}) or {}
        synced = 0
        seen = set()
        for source in (
            getattr(self, 'chart_widgets', None) or {},
            getattr(self, '_chart_cache', None) or {},
        ):
            for stock_code, chart_data in list(source.items()):
                if stock_code in seen:
                    continue
                chart = chart_data.get('chart') if isinstance(chart_data, dict) else chart_data
                if not chart:
                    continue
                seen.add(stock_code)
                task_id = getattr(chart, 'task_id', None)
                task = getattr(chart, 'task', None)
                if not isinstance(task, dict):
                    task = self.task_manager.tasks.get(task_id) if task_id else None
                try:
                    running, paused = self._resolve_task_run_state(task_id, task or {}, chart)
                    chart.set_task_status(running, paused)
                    if running and not paused:
                        synced += 1
                        if isinstance(getattr(chart, 'task', None), dict):
                            params = chart.task.get('params')
                            if isinstance(params, dict):
                                params['task_running'] = True
                                params['task_paused'] = False
                except Exception as e:
                    self.logger.warning(f"同步图表运行态失败 {stock_code}: {e}")
        return synced

    def _detach_grid_widgets(self, stock_codes) -> None:
        """从 grid 卸下容器（先卸再改列宽，避免第 4 列被压成 0 宽时误触按钮）。"""
        for sc in stock_codes:
            cached = (getattr(self, '_chart_cache', None) or {}).get(sc)
            if not isinstance(cached, dict):
                continue
            container = cached.get('container')
            if container is None:
                continue
            try:
                self.grid_layout.removeWidget(container)
            except Exception:
                pass
            try:
                container.hide()
            except Exception:
                pass

    def pause_all_page_tasks(self):
        """暂停当前页所有正在运行的任务"""
        try:
            paused = []
            for stock_code, chart_data in list(self.chart_widgets.items()):
                chart = chart_data.get('chart') if isinstance(chart_data, dict) else chart_data
                if not chart:
                    continue
                if getattr(chart, 'task_running', False) and not getattr(chart, 'task_paused', False):
                    try:
                        chart.pause_task()
                        name = getattr(chart, 'stock_name', '') or stock_code
                        paused.append(f"{name} ({stock_code})")
                    except Exception as e:
                        self.logger.error(f"暂停任务失败 {stock_code}: {e}", exc_info=True)

            if paused:
                self.logger.info(f"全部暂停：已暂停本页 {len(paused)} 个任务：{paused}")
                self.status_label.setText(f"已暂停本页 {len(paused)} 个任务")
            else:
                self.status_label.setText("本页没有正在运行的任务")
        except Exception as e:
            self.logger.error(f"全部暂停失败: {e}", exc_info=True)
            QMessageBox.critical(self, "错误", f"全部暂停失败：{str(e)}")

    def _get_full_stock_code(self, stock_code):
        """获取完整的股票代码，如果是6位数字则自动补充后缀"""
        stock_code = stock_code.strip()
        if len(stock_code) == 6 and stock_code.isdigit():
            if stock_code.startswith(('0', '1', '3')):
                return f"{stock_code}.SZ"
            elif stock_code.startswith(('5', '6')):
                return f"{stock_code}.SH"
            elif stock_code.startswith(('4', '8')):
                return f"{stock_code}.BJ"
            elif stock_code.startswith('920'):
                return f"{stock_code}.BJ"
        return stock_code
    
    def toggle_fullscreen(self):
        """切换全屏状态"""
        if self.is_fullscreen:
            self.exit_fullscreen()
        else:
            self.enter_fullscreen()
    
    def enter_fullscreen(self):
        """进入全屏模式 - 让任务图表视图占据整个程序窗口"""
        if self.is_fullscreen:
            return  # 已经在全屏状态
        
        # 获取主窗口
        main_window = self.window()
        if not main_window:
            return
        
        # 获取 MainWindowExt 实例
        main_window_ext = None
        if hasattr(main_window, 'ext'):
            main_window_ext = main_window.ext
        elif hasattr(main_window, 'ui') and hasattr(main_window.ui, 'ext'):
            main_window_ext = main_window.ui.ext
        
        if not main_window_ext:
            self.logger.warning("无法找到 MainWindowExt 实例，无法进入全屏模式")
            return
        
        # 保存需要隐藏的组件
        self.fullscreen_original_widgets = {
            'menu_bar': main_window.menuBar() if hasattr(main_window, 'menuBar') else None,
            'status_bar': main_window.statusBar() if hasattr(main_window, 'statusBar') else None,
            'splitter_3': None,  # 最外层分割器
            'splitter_2': None,  # 右侧分割器（持仓+实时信息）
            'splitter': None,     # 左侧分割器（任务+订单详情）
            'textEdit': None,     # 订单详情/日志
            'tableWidget': None,  # 持仓列表
        }
        
        # 保存组件的可见状态
        if hasattr(main_window_ext, 'splitter_3'):
            self.fullscreen_original_widgets['splitter_3'] = main_window_ext.splitter_3
            self.fullscreen_original_widgets['splitter_3_visible'] = main_window_ext.splitter_3.isVisible()
        
        if hasattr(main_window_ext, 'splitter_2'):
            self.fullscreen_original_widgets['splitter_2'] = main_window_ext.splitter_2
            self.fullscreen_original_widgets['splitter_2_visible'] = main_window_ext.splitter_2.isVisible()
        
        if hasattr(main_window_ext, 'splitter'):
            self.fullscreen_original_widgets['splitter'] = main_window_ext.splitter
            self.fullscreen_original_widgets['splitter_visible'] = main_window_ext.splitter.isVisible()
        
        if hasattr(main_window_ext, 'textEdit'):
            self.fullscreen_original_widgets['textEdit'] = main_window_ext.textEdit
            self.fullscreen_original_widgets['textEdit_visible'] = main_window_ext.textEdit.isVisible()
        
        if hasattr(main_window_ext, 'tableWidget'):
            self.fullscreen_original_widgets['tableWidget'] = main_window_ext.tableWidget
            self.fullscreen_original_widgets['tableWidget_visible'] = main_window_ext.tableWidget.isVisible()
        
        # 隐藏菜单栏和状态栏
        if self.fullscreen_original_widgets['menu_bar']:
            self.fullscreen_original_widgets['menu_bar'].setVisible(False)
        if self.fullscreen_original_widgets['status_bar']:
            self.fullscreen_original_widgets['status_bar'].setVisible(False)
        
        # 隐藏其他组件（持仓列表、订单列表、日志等）
        if self.fullscreen_original_widgets['splitter_2']:
            self.fullscreen_original_widgets['splitter_2'].setVisible(False)
        if self.fullscreen_original_widgets['textEdit']:
            self.fullscreen_original_widgets['textEdit'].setVisible(False)
        if self.fullscreen_original_widgets['tableWidget']:
            self.fullscreen_original_widgets['tableWidget'].setVisible(False)
        
        # 让任务图表视图占据整个 splitter_3 的空间
        if self.fullscreen_original_widgets['splitter_3']:
            splitter_3 = self.fullscreen_original_widgets['splitter_3']
            # 设置 splitter_3 的大小，让左侧（任务图表）占据全部空间
            splitter_3.setSizes([10000, 0])  # 左侧占满，右侧为0（已隐藏）
        
        # 让任务图表视图在 splitter 中占据全部空间
        if self.fullscreen_original_widgets['splitter']:
            splitter = self.fullscreen_original_widgets['splitter']
            # 设置 splitter 的大小，让上方（任务图表）占据全部空间
            splitter.setSizes([10000, 0])  # 上方占满，下方为0（已隐藏）
        
        self.is_fullscreen = True
        
        # 设置焦点以接收键盘事件
        self.setFocus()
        
        # 更新全屏按钮文本
        if hasattr(self, 'fullscreen_btn'):
            self.fullscreen_btn.setText("退出全屏")
    
    def exit_fullscreen(self):
        """退出全屏模式 - 恢复原始布局"""
        if not self.is_fullscreen:
            return
        
        # 恢复菜单栏和状态栏
        if self.fullscreen_original_widgets.get('menu_bar'):
            self.fullscreen_original_widgets['menu_bar'].setVisible(True)
        if self.fullscreen_original_widgets.get('status_bar'):
            self.fullscreen_original_widgets['status_bar'].setVisible(True)
        
        # 恢复其他组件的可见状态
        if self.fullscreen_original_widgets.get('splitter_2'):
            splitter_2 = self.fullscreen_original_widgets['splitter_2']
            splitter_2.setVisible(self.fullscreen_original_widgets.get('splitter_2_visible', True))
        
        if self.fullscreen_original_widgets.get('textEdit'):
            textEdit = self.fullscreen_original_widgets['textEdit']
            textEdit.setVisible(self.fullscreen_original_widgets.get('textEdit_visible', True))
        
        if self.fullscreen_original_widgets.get('tableWidget'):
            tableWidget = self.fullscreen_original_widgets['tableWidget']
            tableWidget.setVisible(self.fullscreen_original_widgets.get('tableWidget_visible', True))
        
        # 恢复 splitter_3 的大小比例
        if self.fullscreen_original_widgets.get('splitter_3'):
            splitter_3 = self.fullscreen_original_widgets['splitter_3']
            splitter_3.setSizes([600, 400])  # 恢复原始比例
        
        # 恢复 splitter 的大小比例
        if self.fullscreen_original_widgets.get('splitter'):
            splitter = self.fullscreen_original_widgets['splitter']
            splitter.setSizes([600, 400])  # 恢复原始比例
        
        self.is_fullscreen = False
        self.fullscreen_chart_widget = None
        self.fullscreen_exit_btn = None
        
        # 更新全屏按钮文本
        if hasattr(self, 'fullscreen_btn'):
            self.fullscreen_btn.setText("⛶ 全屏")
    
    def keyPressEvent(self, event):
        """处理按键事件"""
        if event.key() == Qt.Key_Escape and self.is_fullscreen:
            self.exit_fullscreen()
            event.accept()
        else:
            super().keyPressEvent(event)
    
    def update_charts_position_and_cash(self, updated_asset, updated_positions):
        """更新所有图表的持仓和余额信息
        
        性能优化：只更新属性，不立即重绘图表。
        图表会在下一次 tick 数据到来时自然刷新，无需在此处强制重绘。
        这样可以将此函数的耗时从 0.5-1 秒降到几乎为 0。
        """
        try:
            # 更新可用余额（所有图表共享）
            available_cash = 0
            if updated_asset and isinstance(updated_asset, dict) and 'cash' in updated_asset:
                try:
                    available_cash = float(updated_asset['cash'])
                except (ValueError, TypeError):
                    available_cash = 0
            
            # 更新每个图表的持仓和余额（只更新属性，不重绘）
            for stock_code, chart_data in self.chart_widgets.items():
                chart = chart_data['chart']
                
                # 更新可用余额（所有图表共享同一个余额）
                chart.available_cash = available_cash
                
                # 更新持仓量（每个股票不同）
                if updated_positions and stock_code in updated_positions:
                    position_info = updated_positions[stock_code]
                    volume = position_info.get('volume', 0)
                    can_use_volume = position_info.get('can_use_volume', 0)
                    
                    # 判断QMT返回的can_use_volume含义
                    if can_use_volume < 0:
                        # can_use_volume为负数，可能表示冻结数量（已委托）
                        # 实际可用 = 总持仓 - |冻结数量|
                        actual_available = volume + can_use_volume  # volume + (-已委托) = 可用
                        chart.position_volume = max(0, int(actual_available))
                    elif 'can_use_volume' in position_info:
                        # can_use_volume为正数，直接使用
                        chart.position_volume = int(can_use_volume)
                    else:
                        # 没有can_use_volume字段，使用volume
                        chart.position_volume = int(volume)
                else:
                    # 如果没有该股票的持仓信息，设置为0
                    chart.position_volume = 0
                
                # 性能优化：去掉 chart.update_chart() 调用
                # 图表会在下一次 tick 更新时自然刷新，无需在此处强制重绘
        
        except Exception as e:
            self.logger.error(f"更新图表持仓和余额失败: {str(e)}", exc_info=True)

    def _resolve_task_stock_name(self, stock_code, stored_name="", *, task=None):
        """任务里若存了「未知/未知名称」，回查 StockInfoManager（与订单列表同路径）。"""
        name = str(stored_name or "").strip()
        if name and name not in ("未知", "未知名称"):
            return name
        try:
            from utils.stock_info_manager import get_stock_name

            resolved = str(get_stock_name(stock_code) or "").strip()
        except Exception:
            resolved = ""
        if resolved and resolved not in ("未知", "未知名称"):
            if isinstance(task, dict):
                task["stock_name"] = resolved
                try:
                    tm = getattr(self, "task_manager", None)
                    tid = str(task.get("task_id") or "")
                    if tm is not None and tid and tid in getattr(tm, "tasks", {}):
                        if isinstance(tm.tasks[tid], dict):
                            tm.tasks[tid]["stock_name"] = resolved
                except Exception:
                    pass
            return resolved
        return name or "未知"


class AddTaskDialog(QDialog):
    """添加任务对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_view = parent  # 保存父视图引用
        self.stock_code_edit = None
        self.stock_name_edit = None
        self.init_ui()
        
    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle("添加股票")
        
        layout = QFormLayout()
        layout.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)  # 字段保持提示大小，不自动拉伸
        
        # 股票代码
        self.stock_code_edit = QLineEdit()
        self.stock_code_edit.setPlaceholderText("例如: 000001 或 000001.SZ")
        self.stock_code_edit.setMaxLength(15)  # 限制最大长度
        self.stock_code_edit.setFixedWidth(180)  # 固定宽度
        self.stock_code_edit.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)  # 固定宽度，不拉伸
        self.stock_code_edit.textChanged.connect(self.on_stock_code_changed)
        layout.addRow("股票代码:", self.stock_code_edit)
        
        # 股票名称
        self.stock_name_edit = QLineEdit()
        self.stock_name_edit.setPlaceholderText("例如: 平安银行")
        self.stock_name_edit.setMaxLength(20)  # 限制最大长度
        self.stock_name_edit.setFixedWidth(180)  # 固定宽度
        self.stock_name_edit.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)  # 固定宽度，不拉伸
        layout.addRow("股票名称:", self.stock_name_edit)
        
        # 按钮
        button_layout = QHBoxLayout()
        
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)
        
        layout.addRow(button_layout)
        
        self.setLayout(layout)
    
    def on_stock_code_changed(self, text):
        """股票代码变化时的处理"""
        text = text.strip()
        if not text:
            self.stock_name_edit.clear()
            return
        
        # 如果是6位数字，自动补充后缀并查询名称
        if len(text) == 6 and text.isdigit():
            # 补充后缀
            full_code = self._get_full_stock_code(text)
            # 查询股票名称
            stock_name = self._get_stock_name(full_code)
            if stock_name:
                self.stock_name_edit.setText(stock_name)
            else:
                self.stock_name_edit.clear()
        elif len(text) >= 6 and '.' in text:
            # 已经有完整代码格式，直接查询
            stock_name = self._get_stock_name(text)
            if stock_name:
                self.stock_name_edit.setText(stock_name)
            else:
                self.stock_name_edit.clear()
    
    def _get_full_stock_code(self, stock_code):
        """获取完整的股票代码，如果是6位数字则自动补充后缀"""
        stock_code = stock_code.strip()
        if len(stock_code) == 6 and stock_code.isdigit():
            if stock_code.startswith(('0', '1', '3')):
                return f"{stock_code}.SZ"
            elif stock_code.startswith(('5', '6')):
                return f"{stock_code}.SH"
            elif stock_code.startswith(('4', '8')):
                return f"{stock_code}.BJ"
            elif stock_code.startswith('920'):
                return f"{stock_code}.BJ"
        return stock_code

    def _resolve_task_stock_name(self, stock_code, stored_name="", *, task=None):
        """任务里若存了「未知/未知名称」，回查 StockInfoManager（与订单列表同路径）。"""
        name = str(stored_name or "").strip()
        if name and name not in ("未知", "未知名称"):
            return name
        resolved = self._get_stock_name(stock_code) or ""
        if resolved and resolved not in ("未知", "未知名称"):
            if isinstance(task, dict):
                task["stock_name"] = resolved
                try:
                    tm = getattr(self, "task_manager", None)
                    if tm is not None and hasattr(tm, "tasks"):
                        tid = str(task.get("task_id") or "")
                        if tid and tid in tm.tasks and isinstance(tm.tasks[tid], dict):
                            tm.tasks[tid]["stock_name"] = resolved
                except Exception:
                    pass
            return resolved
        return name or "未知"

    def _get_stock_name(self, stock_code):
        """获取股票名称（统一走 StockInfoManager：CSV 缓存 + QMT InstrumentName）。"""
        try:
            from utils.stock_info_manager import get_stock_name

            name = get_stock_name(stock_code)
            if name and name not in ("未知名称", "未知"):
                return name
            return None
        except Exception as e:
            print(f"获取股票名称失败: {e}")
            return None
    
    def _get_stock_code_by_name(self, stock_name):
        """通过股票名称查询股票代码"""
        try:
            stock_name = stock_name.strip()
            if not stock_name:
                return None
            
            # 1. 优先使用项目中的股票信息管理器
            try:
                from utils.stock_info_manager import get_stock_info_manager
                stock_manager = get_stock_info_manager()
                # 获取所有股票信息（从缓存中）
                stock_manager._load_stock_info()
                stock_cache = stock_manager._stock_info_cache
                
                if stock_cache:
                    # 遍历所有股票，查找名称匹配的
                    for code, info in stock_cache.items():
                        if isinstance(info, dict):
                            name = info.get('证券简称', '')
                            if name == stock_name:
                                return code
                            # 也支持模糊匹配（包含关系）
                            if stock_name in name or name in stock_name:
                                return code
            except ImportError:
                pass
            except Exception as e:
                print(f"通过股票名称查询代码失败: {e}")
            
            # 2. 尝试使用QMT获取股票信息
            try:
                import xtquant.xtdata as xtdata
                # 获取所有股票代码列表
                all_stocks = xtdata.get_stock_list_in_sector('沪深A股')
                if all_stocks:
                    for code in all_stocks:
                        try:
                            stock_info = xtdata.get_instrument_detail(code)
                            if stock_info and 'InstrumentName' in stock_info:
                                name = stock_info['InstrumentName']
                                if name == stock_name or stock_name in name or name in stock_name:
                                    return code
                        except:
                            continue
            except:
                pass
            
            return None
            
        except Exception as e:
            print(f"通过股票名称查询代码失败: {e}")
            return None


if __name__ == '__main__':
    from PyQt5.QtWidgets import QApplication
    
    app = QApplication(sys.argv)
    
    # 测试时需要提供task_manager和qmt_adapter
    # widget = TasksChartsView(None, None)
    # widget.show()
    # sys.exit(app.exec_())
    
    print("这是任务图表视图组件")
    print("请在主窗口中集成使用")

