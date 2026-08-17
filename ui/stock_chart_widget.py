"""
股票分时图组件 - 显示实时价格、买卖点和分时图
支持拖动横线修改买卖点
"""

import sys
import os
import configparser
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QPushButton, QSpacerItem, 
                             QSizePolicy, QDialog, QDialogButtonBox, QTimeEdit)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QDateTime, QTime
from PyQt5.QtGui import QFont
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
import numpy as np
import matplotlib
import math
from datetime import datetime, timedelta
from typing import Optional
from core.utils.security_type import SecurityTypeUtil
from core.elastic_sell import (
    compute_best_sell_fallback_from_rule,
    load_elastic_confirm_triple,
    resolve_room_blend_start,
    DEFAULT_ROOM_BLEND_AT_DROP_LOW,
)

# 配置中文字体
matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS', 'sans-serif']
matplotlib.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题


class StockChartWidget(QWidget):
    """股票图表组件 - 显示价格位置图和买卖点"""
    
    def __init__(self, stock_code, stock_name, show_controls=True, parent=None):
        super().__init__(parent)
        self.stock_code = stock_code
        self.stock_name = stock_name
        self.current_price = 0.0
        self.position_volume = 0  # 持仓量
        self.position_cost = 0.0  # 持仓成本价（QMT open_price）
        self.available_cash = 0  # 可用余额
        self.time_data = []
        self.price_data = []
        self.volume_data = []
        
        # 存储价格历史（用于绘制折线图）
        self.price_history = []  # [(time, price), ...]
        
        # 存储拖动状态
        self.dragging = None
        self.buy_line = None
        self.sell_line = None
    
        # 任务运行状态
        self.task_running = False  # 任务是否正在运行
        self.task_paused = False   # 任务是否暂停
        
        # 已移除闪烁逻辑，改用固定红色显示以提升性能
        
        # 标签点击相关
        self.label_texts = []
        self.label_click_connected = False
        self.label_order = []  # 存储标签的显示顺序
        
        # 标签悬停相关：保存节点和标签的映射关系
        self.node_label_map = {}  # {(price, volume): list[text_obj]} 同坐标可对应多个标签
        self.label_original_zorder = {}  # {text_obj: original_zorder} 标签的原始zorder
        self.label_original_bbox = {}  # {text_obj: dict} 悬停高亮前还原用
        self.hovered_labels = []  # 当前悬停节点上的标签（可能多个）
        self._hovered_node_key = None  # 当前悬停的节点键，用于避免重复 draw_idle
        
        # 价格提示相关（在添加模式下显示鼠标位置的价格）
        self.price_hint_annotation = None
        self.last_mouse_position = None  # 保存最后已知的鼠标位置 (x, y)
        
        # 双击检测相关
        self._last_click_time = 0
        self._double_click_interval = 0.3  # 双击间隔（秒）
        
        # 交易规则列表
        self.rules = []  # 存储所有交易规则
        
        # 夜市规则定时器相关
        self.night_market_timer = None  # 夜市规则定时器
        self.night_market_rules = []  # 夜市规则列表
        self.night_market_start_time = None  # 夜市任务开始时间
        self.night_market_send_count = 0  # 发送计数（用于控制频率）
        self.night_market_high_freq_mode = False  # 是否处于高频发送模式（每秒10次，持续2秒）
        self.night_market_high_freq_end_time = None  # 高频发送模式结束时间
        self.night_market_end_time = None  # 夜市任务结束时间（下一个交易日9:15:00前）
        
        # 规则添加模式
        self.add_mode = None  # 当前添加模式：'single_buy', 'single_sell', 'cage_buy', 'cage_sell' 等
        self.adding_rule = False  # 是否正在添加规则
        self.temp_rule_start = None  # 临时规则起始点（用于笼子规则）
        
        # 规则拖动相关
        self.drag_mode = None  # 拖动模式：'point', 'low', 'high', 'middle'
        self.drag_start_x = None  # 拖动开始时的X坐标
        self.drag_start_y = None  # 拖动开始时的Y坐标（用于计算相对变化）
        self.drag_start_volume = None  # 拖动开始时的交易量（用于计算相对变化）
        self.drag_start_y_pixel = None  # 拖动开始时的Y屏幕坐标（像素，用于准确计算相对移动）
        
        # 是否显示控件（买卖点编辑器）
        self.show_controls = show_controls
        
        # 当前列数（用于判断是否显示标签）
        self.current_columns = None  # 将在创建后设置
        
        # 昨收盘价格（用于计算涨跌停）
        self.prev_close_price = 0.0
        
        # 关键价格点数据
        self.key_points = []  # [(名称, 价格), ...]
        self.limit_up_price = 0.0
        self.limit_down_price = 0.0
        
        # 已显示的警告记录（用于去重，每个警告最多显示一次）
        self.shown_warnings = set()  # 记录已显示的警告标识

        # 高频提示节流（避免弹性买入/卖出等日志刷屏）
        self._last_throttled_log_time = {}  # {key: datetime}
        
        # 定时清仓相关
        self.scheduled_clear_time = None  # 定时清仓时间 (datetime.time对象，默认14:56:00)
        self.scheduled_clear_price = 0.0  # 定时清仓触发价格（低于此价格才卖出）
        self.scheduled_clear_volume = 0  # 定时清仓数量
        self.scheduled_clear_enabled = False  # 是否启用定时清仓
        self.scheduled_clear_timer = None  # 定时清仓定时器
        self.scheduled_clear_executed = False  # 是否已执行定时清仓（当天）
        self.scheduled_clear_last_date = None  # 上次执行日期（用于每天重置）
        
        # 保存最新的tick数据（用于定时清仓计算滑点）
        self._last_tick_data = None

        # 真突破判定（逐 tick 前缀状态，与回测/intelligentbuy 对齐）
        self._tb_state_date = None
        self._tb_prev_tick_row = None
        self._tb_recent_tick_rows = []
        self._tb_recent_break_vols = []
        self._tb_prefix_sum = 0.0
        self._tb_prefix_cnt = 0
        self._tb_last_cum_volume = None
        self._tb_vol_mul = 100.0
        self._last_tick_price = None
        
        # 初始化定时清仓时间为14:56:00
        from datetime import time as dt_time
        self.scheduled_clear_time = dt_time(14, 56, 0)
        
        from ui.smart_sell_runner import SmartSellRunner
        self.smart_sell_runner = SmartSellRunner(self)

        self.init_ui()
    
    def closeEvent(self, event):
        """窗口关闭时清理资源"""
        try:
            # 停止定时清仓定时器
            if hasattr(self, 'scheduled_clear_timer') and self.scheduled_clear_timer is not None:
                try:
                    if self.scheduled_clear_timer.isActive():
                        self.scheduled_clear_timer.stop()
                    # 断开信号连接
                    try:
                        self.scheduled_clear_timer.timeout.disconnect()
                    except (RuntimeError, TypeError):
                        pass  # 信号可能已被断开
                    self.scheduled_clear_timer = None
                except Exception as e:
                    if hasattr(self, 'logger'):
                        self.logger.error(f"[{self.stock_code}] 停止定时清仓定时器失败: {str(e)}")
            
            # 停止夜市定时器（如果存在）
            if hasattr(self, '_stop_night_market_timer'):
                try:
                    self._stop_night_market_timer()
                except Exception:
                    pass
        except Exception as e:
            if hasattr(self, 'logger'):
                self.logger.error(f"[{self.stock_code}] closeEvent处理失败: {str(e)}")
        
        # 调用父类的closeEvent
        super().closeEvent(event)
        
        # 设置tooltip样式，使用系统默认背景色但确保文字可见
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            # 只在还没有设置tooltip样式时设置，避免重复设置
            current_style = app.styleSheet()
            if "QToolTip" not in current_style:
                app.setStyleSheet(current_style + """
                    QToolTip {
                        background-color: #ffffdc;
                        color: #000000;
                        border: 1px solid #d4d4d4;
                        padding: 3px 5px;
                        font-size: 11px;
                    }
                """)

    def _log_throttled(self, key: str, message: str, level: str = "debug", interval_s: float = 30.0) -> None:
        """
        对高频日志做节流：同一个 key 在 interval_s 秒内只输出一次。
        典型用于“尚未达到条件”类的循环提示，避免刷屏。
        """
        try:
            now = datetime.now()
            last = self._last_throttled_log_time.get(key)
            if last is not None and (now - last).total_seconds() < float(interval_s):
                return
            self._last_throttled_log_time[key] = now
            fn = getattr(self.logger, level, None)
            if not callable(fn):
                fn = self.logger.debug
            fn(message)
        except Exception:
            try:
                self.logger.debug(message)
            except Exception:
                pass
        
    def init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout()
        
        # 创建当前价和距离标签（但不在本组件中显示，会被移动到tasks_charts_view的header中）
        # 当前价格
        self.current_price_label = QLabel("当前价: --")
        self.current_price_label.setStyleSheet("color: #333;")
        
        # 距离买卖点
        self.distance_label = QLabel("距离: --")
        self.distance_label.setStyleSheet("color: #666; font-size: 9pt;")
        
        # 图表设置区域（只在单列时显示）
        self.chart_control_layout = None  # 保存控件布局引用，用于动态显示/隐藏
        if self.show_controls:
            chart_control_layout = QHBoxLayout()
            self.chart_control_layout = chart_control_layout  # 保存引用
            
            # 规则工具栏
            rules_label = QLabel("规则工具:")
            chart_control_layout.addWidget(rules_label)
            
            # 单点买入按钮
            buy_btn = QPushButton("📍买")
            buy_btn.setToolTip("单点买入：点击按钮后在图表上点击添加买入点，价格小于等于设定价时自动买入")
            buy_btn.setCheckable(True)
            buy_btn.setMinimumWidth(60)
            buy_btn.setMinimumHeight(28)
            buy_btn.setStyleSheet("""
                QPushButton {
                    background-color: #4caf50; 
                    color: white; 
                    padding: 5px 10px;
                    border: 2px solid #66bb6a;
                    border-radius: 4px;
                    font-weight: normal;
                }
                QPushButton:checked {
                    background-color: #2e7d32;
                    border: 4px solid #000000;
                    padding: 8px 16px;
                    margin: -2px;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover:!checked {
                    background-color: #66bb6a;
                    border: 2px solid #81c784;
                }
            """)
            buy_btn.clicked.connect(lambda: self.set_add_mode('single_buy'))
            chart_control_layout.addWidget(buy_btn)
            self.buy_tool_btn = buy_btn
            
            # 单点卖出按钮
            sell_btn = QPushButton("📍卖")
            sell_btn.setToolTip("单点卖出：点击按钮后在图表上点击添加卖出点，价格大于等于设定价时自动卖出")
            sell_btn.setCheckable(True)
            sell_btn.setMinimumWidth(60)
            sell_btn.setMinimumHeight(28)
            sell_btn.setStyleSheet("""
                QPushButton {
                    background-color: #f44336; 
                    color: white; 
                    padding: 5px 10px;
                    border: 2px solid #e57373;
                    border-radius: 4px;
                    font-weight: normal;
                }
                QPushButton:checked {
                    background-color: #b71c1c;
                    border: 4px solid #000000;
                    padding: 8px 16px;
                    margin: -2px;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover:!checked {
                    background-color: #e57373;
                    border: 2px solid #ef9a9a;
                }
            """)
            sell_btn.clicked.connect(lambda: self.set_add_mode('single_sell'))
            chart_control_layout.addWidget(sell_btn)
            self.sell_tool_btn = sell_btn
            
            # 突破买入按钮
            breakthrough_buy_btn = QPushButton("⬆️买")
            breakthrough_buy_btn.setToolTip(
                "突破买入：点击后在图上定点；可选普通上穿，或价格带硬pass（监控带+有效下沿+硬上沿MA5）"
            )
            breakthrough_buy_btn.setCheckable(True)
            breakthrough_buy_btn.setMinimumWidth(60)
            breakthrough_buy_btn.setMinimumHeight(28)
            breakthrough_buy_btn.setStyleSheet("""
                QPushButton {
                    background-color: #00bcd4; 
                    color: white; 
                    padding: 5px 10px;
                    border: 2px solid #4dd0e1;
                    border-radius: 4px;
                    font-weight: normal;
                }
                QPushButton:checked {
                    background-color: #0097a7;
                    border: 4px solid #000000;
                    padding: 8px 16px;
                    margin: -2px;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover:!checked {
                    background-color: #4dd0e1;
                    border: 2px solid #80deea;
                }
            """)
            breakthrough_buy_btn.clicked.connect(lambda: self.set_add_mode('breakthrough_buy'))
            chart_control_layout.addWidget(breakthrough_buy_btn)
            self.breakthrough_buy_tool_btn = breakthrough_buy_btn
            
            # 突破卖出按钮
            breakthrough_sell_btn = QPushButton("⬇️卖")
            breakthrough_sell_btn.setToolTip("突破卖出：点击按钮后在图表上点击添加卖出点，价格小于设定价时自动卖出")
            breakthrough_sell_btn.setCheckable(True)
            breakthrough_sell_btn.setMinimumWidth(60)
            breakthrough_sell_btn.setMinimumHeight(28)
            breakthrough_sell_btn.setStyleSheet("""
                QPushButton {
                    background-color: #7b1fa2; 
                    color: white; 
                    padding: 5px 10px;
                    border: 2px solid #9c27b0;
                    border-radius: 4px;
                    font-weight: normal;
                }
                QPushButton:checked {
                    background-color: #6a1b9a;
                    border: 4px solid #000000;
                    padding: 8px 16px;
                    margin: -2px;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover:!checked {
                    background-color: #ba68c8;
                    border: 2px solid #ce93d8;
                }
            """)
            breakthrough_sell_btn.clicked.connect(lambda: self.set_add_mode('breakthrough_sell'))
            chart_control_layout.addWidget(breakthrough_sell_btn)
            self.breakthrough_sell_tool_btn = breakthrough_sell_btn
            
            # 弹性买入按钮
            best_buy_btn = QPushButton("🔃买")
            best_buy_btn.setToolTip("弹性买入：价格跌破触发价后反弹到指定百分比时买入，默认0.3%，可右键编辑")
            best_buy_btn.setCheckable(True)
            best_buy_btn.setMinimumWidth(60)
            best_buy_btn.setMinimumHeight(28)
            best_buy_btn.setStyleSheet("""
                QPushButton {
                    background-color: #26a69a; 
                    color: white; 
                    padding: 5px 10px;
                    border: 2px solid #4db6ac;
                    border-radius: 4px;
                    font-weight: normal;
                }
                QPushButton:checked {
                    background-color: #00695c;
                    border: 4px solid #000000;
                    padding: 8px 16px;
                    margin: -2px;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover:!checked {
                    background-color: #4db6ac;
                    border: 2px solid #80cbc4;
                }
            """)
            best_buy_btn.clicked.connect(lambda: self.set_add_mode('best_buy'))
            chart_control_layout.addWidget(best_buy_btn)
            self.best_buy_tool_btn = best_buy_btn
            
            # 弹性卖出按钮
            best_sell_btn = QPushButton("🔃卖")
            best_sell_btn.setToolTip("弹性卖出：突破触发价后按回落%与过渡起点(pp)跟踪卖出；右键可编辑参数")
            best_sell_btn.setCheckable(True)
            best_sell_btn.setMinimumWidth(60)
            best_sell_btn.setMinimumHeight(28)
            best_sell_btn.setStyleSheet("""
                QPushButton {
                    background-color: #ec407a; 
                    color: white; 
                    padding: 5px 10px;
                    border: 2px solid #f06292;
                    border-radius: 4px;
                    font-weight: normal;
                }
                QPushButton:checked {
                    background-color: #ad1457;
                    border: 4px solid #000000;
                    padding: 8px 16px;
                    margin: -2px;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover:!checked {
                    background-color: #f06292;
                    border: 2px solid #f48fb1;
                }
            """)
            best_sell_btn.clicked.connect(lambda: self.set_add_mode('best_sell'))
            chart_control_layout.addWidget(best_sell_btn)
            self.best_sell_tool_btn = best_sell_btn
            
            # 笼子买入按钮
            cage_buy_btn = QPushButton("📦买")
            cage_buy_btn.setToolTip("笼子买入：拖动创建价格区间，价格达到上下限时自动买入，可拖动节点调整")
            cage_buy_btn.setCheckable(True)
            cage_buy_btn.setMinimumWidth(60)
            cage_buy_btn.setMinimumHeight(28)
            cage_buy_btn.setStyleSheet("""
                QPushButton {
                    background-color: #66bb6a; 
                    color: white; 
                    padding: 5px 10px;
                    border: 2px solid #81c784;
                    border-radius: 4px;
                    font-weight: normal;
                }
                QPushButton:checked {
                    background-color: #388e3c;
                    border: 4px solid #000000;
                    padding: 8px 16px;
                    margin: -2px;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover:!checked {
                    background-color: #81c784;
                    border: 2px solid #a5d6a7;
                }
            """)
            cage_buy_btn.clicked.connect(lambda: self.set_add_mode('cage_buy'))
            chart_control_layout.addWidget(cage_buy_btn)
            self.cage_buy_tool_btn = cage_buy_btn
            
            # 笼子卖出按钮
            cage_sell_btn = QPushButton("📦卖")
            cage_sell_btn.setToolTip("笼子卖出：拖动创建价格区间，价格达到上下限时自动卖出，可拖动节点调整")
            cage_sell_btn.setCheckable(True)
            cage_sell_btn.setMinimumWidth(60)
            cage_sell_btn.setMinimumHeight(28)
            cage_sell_btn.setStyleSheet("""
                QPushButton {
                    background-color: #ef5350; 
                    color: white; 
                    padding: 5px 10px;
                    border: 2px solid #f48fb1;
                    border-radius: 4px;
                    font-weight: normal;
                }
                QPushButton:checked {
                    background-color: #c62828;
                    border: 4px solid #000000;
                    padding: 8px 16px;
                    margin: -2px;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover:!checked {
                    background-color: #f48fb1;
                    border: 2px solid #f8bbd0;
                }
            """)
            cage_sell_btn.clicked.connect(lambda: self.set_add_mode('cage_sell'))
            chart_control_layout.addWidget(cage_sell_btn)
            self.cage_sell_tool_btn = cage_sell_btn
            
            # 网格买入按钮
            grid_buy_btn = QPushButton("⊞买")
            grid_buy_btn.setToolTip("网格买入：拖动创建价格区间，价格从高到低依次触及网格点时买入，默认2格，可右键设置")
            grid_buy_btn.setCheckable(True)
            grid_buy_btn.setMinimumWidth(60)
            grid_buy_btn.setMinimumHeight(28)
            grid_buy_btn.setStyleSheet("""
                QPushButton {
                    background-color: #00897b; 
                    color: white; 
                    padding: 5px 10px;
                    border: 2px solid #26a69a;
                    border-radius: 4px;
                    font-weight: normal;
                }
                QPushButton:checked {
                    background-color: #004d40;
                    border: 4px solid #000000;
                    padding: 8px 16px;
                    margin: -2px;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover:!checked {
                    background-color: #26a69a;
                    border: 2px solid #4db6ac;
                }
            """)
            grid_buy_btn.clicked.connect(lambda: self.set_add_mode('grid_buy'))
            chart_control_layout.addWidget(grid_buy_btn)
            self.grid_buy_tool_btn = grid_buy_btn
            
            # 网格卖出按钮
            grid_sell_btn = QPushButton("⊞卖")
            grid_sell_btn.setToolTip("网格卖出：拖动创建价格区间，价格从低到高依次触及网格点时卖出，默认2格，可右键设置")
            grid_sell_btn.setCheckable(True)
            grid_sell_btn.setMinimumWidth(60)
            grid_sell_btn.setMinimumHeight(28)
            grid_sell_btn.setStyleSheet("""
                QPushButton {
                    background-color: #d32f2f; 
                    color: white; 
                    padding: 5px 10px;
                    border: 2px solid #f44336;
                    border-radius: 4px;
                    font-weight: normal;
                }
                QPushButton:checked {
                    background-color: #b71c1c;
                    border: 4px solid #000000;
                    padding: 8px 16px;
                    margin: -2px;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover:!checked {
                    background-color: #f44336;
                    border: 2px solid #e57373;
                }
            """)
            grid_sell_btn.clicked.connect(lambda: self.set_add_mode('grid_sell'))
            chart_control_layout.addWidget(grid_sell_btn)
            self.grid_sell_tool_btn = grid_sell_btn
            
            # 夜市买入按钮
            night_buy_btn = QPushButton("🌙买")
            night_buy_btn.setToolTip("夜市买入：点击按钮后在图表上点击添加夜市买入点，作为夜市委托单提交")
            night_buy_btn.setCheckable(True)
            night_buy_btn.setMinimumWidth(60)
            night_buy_btn.setMinimumHeight(28)
            night_buy_btn.setStyleSheet("""
                QPushButton {
                    background-color: #5c6bc0; 
                    color: white; 
                    padding: 5px 10px;
                    border: 2px solid #7986cb;
                    border-radius: 4px;
                    font-weight: normal;
                }
                QPushButton:checked {
                    background-color: #283593;
                    border: 4px solid #000000;
                    padding: 8px 16px;
                    margin: -2px;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover:!checked {
                    background-color: #7986cb;
                    border: 2px solid #9fa8da;
                }
            """)
            night_buy_btn.clicked.connect(lambda: self.set_add_mode('night_buy'))
            chart_control_layout.addWidget(night_buy_btn)
            self.night_buy_tool_btn = night_buy_btn
            
            # 夜市卖出按钮
            night_sell_btn = QPushButton("🌙卖")
            night_sell_btn.setToolTip("夜市卖出：点击按钮后在图表上点击添加夜市卖出点，作为夜市委托单提交")
            night_sell_btn.setCheckable(True)
            night_sell_btn.setMinimumWidth(60)
            night_sell_btn.setMinimumHeight(28)
            night_sell_btn.setStyleSheet("""
                QPushButton {
                    background-color: #ab47bc; 
                    color: white; 
                    padding: 5px 10px;
                    border: 2px solid #ba68c8;
                    border-radius: 4px;
                    font-weight: normal;
                }
                QPushButton:checked {
                    background-color: #6a1b9a;
                    border: 4px solid #000000;
                    padding: 8px 16px;
                    margin: -2px;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover:!checked {
                    background-color: #ba68c8;
                    border: 2px solid #ce93d8;
                }
            """)
            night_sell_btn.clicked.connect(lambda: self.set_add_mode('night_sell'))
            chart_control_layout.addWidget(night_sell_btn)
            self.night_sell_tool_btn = night_sell_btn
            
            # 定时清仓按钮
            scheduled_clear_btn = QPushButton("⏰定时清仓")
            scheduled_clear_btn.setToolTip("定时清仓：到达指定时间且价格低于指定价格时自动卖出")
            scheduled_clear_btn.setCheckable(True)
            scheduled_clear_btn.setMinimumWidth(80)
            scheduled_clear_btn.setMinimumHeight(28)
            scheduled_clear_btn.setStyleSheet("""
                QPushButton {
                    background-color: #ff9800; 
                    color: white; 
                    padding: 5px 10px;
                    border: 2px solid #ffb74d;
                    border-radius: 4px;
                    font-weight: normal;
                }
                QPushButton:checked {
                    background-color: #f57c00;
                    border: 4px solid #000000;
                    padding: 8px 16px;
                    margin: -2px;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover:!checked {
                    background-color: #ffb74d;
                    border: 2px solid #ffcc80;
                }
            """)
            scheduled_clear_btn.clicked.connect(self.toggle_scheduled_clear)
            scheduled_clear_btn.setContextMenuPolicy(Qt.CustomContextMenu)
            scheduled_clear_btn.customContextMenuRequested.connect(self.show_scheduled_clear_menu)
            chart_control_layout.addWidget(scheduled_clear_btn)
            self.scheduled_clear_tool_btn = scheduled_clear_btn
            
            # 更多规则按钮
            more_btn = QPushButton("⚙️更多")
            more_btn.setToolTip("更多规则类型")
            more_btn.setCheckable(True)
            more_btn.setMinimumWidth(60)
            more_btn.setMinimumHeight(28)
            more_btn.setStyleSheet("""
                QPushButton {
                    background-color: #607d8b; 
                    color: white; 
                    padding: 5px 10px;
                    border: 2px solid #78909c;
                    border-radius: 4px;
                    font-weight: normal;
                }
                QPushButton:checked {
                    background-color: #37474f;
                    border: 4px solid #000000;
                    padding: 8px 16px;
                    margin: -2px;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover:!checked {
                    background-color: #78909c;
                    border: 2px solid #90a4ae;
                }
            """)
            more_btn.clicked.connect(self.show_more_rules)
            chart_control_layout.addWidget(more_btn)
            self.more_tool_btn = more_btn
            
            chart_control_layout.addStretch()
            
            layout.addLayout(chart_control_layout)
        
        # 创建任务运行控制组件（不添加到布局中，会被tasks_charts_view添加到标题栏）
        # 合并状态显示和操作按钮：按钮显示状态信息，点击执行操作
        self.toggle_btn = QPushButton("🔴 未运行 | 启动")
        self.toggle_btn.setToolTip("启动/暂停任务（点击切换状态）")
        # 未运行状态样式：灰色边框+浅灰背景
        self.toggle_btn.setStyleSheet("font-weight: bold; padding: 2px 12px; background-color: #fafafa; border: 2px solid #999; border-radius: 8px; color: #666; font-size: 11px;")
        self.toggle_btn.clicked.connect(self._toggle_task)
        
        # 保留旧的按钮引用以兼容现有代码（隐藏它们）
        self.start_btn = self.toggle_btn  # 兼容性
        self.pause_btn = self.toggle_btn  # 兼容性
        
        # 创建matplotlib图表 - 只显示价格位置图
        self.figure = Figure(figsize=(10, 4), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        
        # 设置图表大小策略 - 允许自动拉伸
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        # 设置图表最小大小（将在设置列数后根据列数调整）
        self.canvas.setMinimumHeight(200)
        self.canvas.setMinimumWidth(200)
        
        # 创建图表：只显示价格位置图
        self.price_position_ax = self.figure.add_subplot(1, 1, 1)
        
        # 设置图表样式
        self.figure.patch.set_facecolor('#FFFFFF')
        self.price_position_ax.set_facecolor('#FAFAFA')
        
        # 存储引用，用于拖动检测
        self.buy_line_ref = None
        self.sell_line_ref = None
        
        layout.addWidget(self.canvas)
        
        # 设置边距
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)
        
        self.setLayout(layout)
        
        # 设置整个widget的尺寸策略，允许自动拉伸
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
    def _create_control_layout(self):
        """动态创建控件布局（当从多列切换到1列时使用）"""
        if self.chart_control_layout is not None:
            return  # 已经存在，不需要创建
        
        from PyQt5.QtWidgets import QHBoxLayout, QPushButton, QLabel
        from PyQt5.QtCore import Qt
        
        # 获取主布局
        main_layout = self.layout()
        if main_layout is None:
            return
        
        # 创建控件布局
        chart_control_layout = QHBoxLayout()
        self.chart_control_layout = chart_control_layout
        
        # 规则工具栏
        rules_label = QLabel("规则工具:")
        chart_control_layout.addWidget(rules_label)
        
        # 单点买入按钮
        buy_btn = QPushButton("📍买")
        buy_btn.setToolTip("单点买入：点击按钮后在图表上点击添加买入点，价格小于等于设定价时自动买入")
        buy_btn.setCheckable(True)
        buy_btn.setMinimumWidth(60)
        buy_btn.setMinimumHeight(28)
        buy_btn.setStyleSheet("""
            QPushButton {
                background-color: #4caf50; 
                color: white; 
                padding: 5px 10px;
                border: 2px solid #66bb6a;
                border-radius: 4px;
                font-weight: normal;
            }
            QPushButton:checked {
                background-color: #2e7d32;
                border: 4px solid #000000;
                padding: 8px 16px;
                margin: -2px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover:!checked {
                background-color: #66bb6a;
                border: 2px solid #81c784;
            }
        """)
        buy_btn.clicked.connect(lambda: self.set_add_mode('single_buy'))
        chart_control_layout.addWidget(buy_btn)
        self.buy_tool_btn = buy_btn
        
        # 单点卖出按钮
        sell_btn = QPushButton("📍卖")
        sell_btn.setToolTip("单点卖出：点击按钮后在图表上点击添加卖出点，价格大于等于设定价时自动卖出")
        sell_btn.setCheckable(True)
        sell_btn.setMinimumWidth(60)
        sell_btn.setMinimumHeight(28)
        sell_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336; 
                color: white; 
                padding: 5px 10px;
                border: 2px solid #e57373;
                border-radius: 4px;
                font-weight: normal;
            }
            QPushButton:checked {
                background-color: #b71c1c;
                border: 4px solid #000000;
                padding: 8px 16px;
                margin: -2px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover:!checked {
                background-color: #e57373;
                border: 2px solid #ef9a9a;
            }
        """)
        sell_btn.clicked.connect(lambda: self.set_add_mode('single_sell'))
        chart_control_layout.addWidget(sell_btn)
        self.sell_tool_btn = sell_btn
        
        # 突破买入按钮
        breakthrough_buy_btn = QPushButton("⬆️买")
        breakthrough_buy_btn.setToolTip("突破买入：点击后在图上定点；可选普通上穿，或价格带硬pass（监控带+有效下沿+硬上沿MA5）")
        breakthrough_buy_btn.setCheckable(True)
        breakthrough_buy_btn.setMinimumWidth(60)
        breakthrough_buy_btn.setMinimumHeight(28)
        breakthrough_buy_btn.setStyleSheet("""
            QPushButton {
                background-color: #00bcd4; 
                color: white; 
                padding: 5px 10px;
                border: 2px solid #4dd0e1;
                border-radius: 4px;
                font-weight: normal;
            }
            QPushButton:checked {
                background-color: #0097a7;
                border: 4px solid #000000;
                padding: 8px 16px;
                margin: -2px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover:!checked {
                background-color: #4dd0e1;
                border: 2px solid #80deea;
            }
        """)
        breakthrough_buy_btn.clicked.connect(lambda: self.set_add_mode('breakthrough_buy'))
        chart_control_layout.addWidget(breakthrough_buy_btn)
        self.breakthrough_buy_tool_btn = breakthrough_buy_btn
        
        # 突破卖出按钮
        breakthrough_sell_btn = QPushButton("⬇️卖")
        breakthrough_sell_btn.setToolTip("突破卖出：点击按钮后在图表上点击添加卖出点，价格小于设定价时自动卖出")
        breakthrough_sell_btn.setCheckable(True)
        breakthrough_sell_btn.setMinimumWidth(60)
        breakthrough_sell_btn.setMinimumHeight(28)
        breakthrough_sell_btn.setStyleSheet("""
            QPushButton {
                background-color: #7b1fa2; 
                color: white; 
                padding: 5px 10px;
                border: 2px solid #9c27b0;
                border-radius: 4px;
                font-weight: normal;
            }
            QPushButton:checked {
                background-color: #6a1b9a;
                border: 4px solid #000000;
                padding: 8px 16px;
                margin: -2px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover:!checked {
                background-color: #ba68c8;
                border: 2px solid #ce93d8;
            }
        """)
        breakthrough_sell_btn.clicked.connect(lambda: self.set_add_mode('breakthrough_sell'))
        chart_control_layout.addWidget(breakthrough_sell_btn)
        self.breakthrough_sell_tool_btn = breakthrough_sell_btn
        
        # 弹性买入按钮
        best_buy_btn = QPushButton("🔃买")
        best_buy_btn.setToolTip("弹性买入：价格跌破触发价后反弹到指定百分比时买入，默认0.3%，可右键编辑")
        best_buy_btn.setCheckable(True)
        best_buy_btn.setMinimumWidth(60)
        best_buy_btn.setMinimumHeight(28)
        best_buy_btn.setStyleSheet("""
            QPushButton {
                background-color: #26a69a; 
                color: white; 
                padding: 5px 10px;
                border: 2px solid #4db6ac;
                border-radius: 4px;
                font-weight: normal;
            }
            QPushButton:checked {
                background-color: #00695c;
                border: 4px solid #000000;
                padding: 8px 16px;
                margin: -2px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover:!checked {
                background-color: #4db6ac;
                border: 2px solid #80cbc4;
            }
        """)
        best_buy_btn.clicked.connect(lambda: self.set_add_mode('best_buy'))
        chart_control_layout.addWidget(best_buy_btn)
        self.best_buy_tool_btn = best_buy_btn
        
        # 弹性卖出按钮
        best_sell_btn = QPushButton("🔃卖")
        best_sell_btn.setToolTip("弹性卖出：突破触发价后按回落%与过渡起点(pp)跟踪卖出；右键可编辑参数")
        best_sell_btn.setCheckable(True)
        best_sell_btn.setMinimumWidth(60)
        best_sell_btn.setMinimumHeight(28)
        best_sell_btn.setStyleSheet("""
            QPushButton {
                background-color: #ec407a; 
                color: white; 
                padding: 5px 10px;
                border: 2px solid #f06292;
                border-radius: 4px;
                font-weight: normal;
            }
            QPushButton:checked {
                background-color: #ad1457;
                border: 4px solid #000000;
                padding: 8px 16px;
                margin: -2px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover:!checked {
                background-color: #f06292;
                border: 2px solid #f48fb1;
            }
        """)
        best_sell_btn.clicked.connect(lambda: self.set_add_mode('best_sell'))
        chart_control_layout.addWidget(best_sell_btn)
        self.best_sell_tool_btn = best_sell_btn
        
        # 笼子买入按钮
        cage_buy_btn = QPushButton("📦买")
        cage_buy_btn.setToolTip("笼子买入：拖动创建价格区间，价格达到上下限时自动买入，可拖动节点调整")
        cage_buy_btn.setCheckable(True)
        cage_buy_btn.setMinimumWidth(60)
        cage_buy_btn.setMinimumHeight(28)
        cage_buy_btn.setStyleSheet("""
            QPushButton {
                background-color: #66bb6a; 
                color: white; 
                padding: 5px 10px;
                border: 2px solid #81c784;
                border-radius: 4px;
                font-weight: normal;
            }
            QPushButton:checked {
                background-color: #388e3c;
                border: 4px solid #000000;
                padding: 8px 16px;
                margin: -2px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover:!checked {
                background-color: #81c784;
                border: 2px solid #a5d6a7;
            }
        """)
        cage_buy_btn.clicked.connect(lambda: self.set_add_mode('cage_buy'))
        chart_control_layout.addWidget(cage_buy_btn)
        self.cage_buy_tool_btn = cage_buy_btn
        
        # 笼子卖出按钮
        cage_sell_btn = QPushButton("📦卖")
        cage_sell_btn.setToolTip("笼子卖出：拖动创建价格区间，价格达到上下限时自动卖出，可拖动节点调整")
        cage_sell_btn.setCheckable(True)
        cage_sell_btn.setMinimumWidth(60)
        cage_sell_btn.setMinimumHeight(28)
        cage_sell_btn.setStyleSheet("""
            QPushButton {
                background-color: #ef5350; 
                color: white; 
                padding: 5px 10px;
                border: 2px solid #f48fb1;
                border-radius: 4px;
                font-weight: normal;
            }
            QPushButton:checked {
                background-color: #c62828;
                border: 4px solid #000000;
                padding: 8px 16px;
                margin: -2px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover:!checked {
                background-color: #f48fb1;
                border: 2px solid #f8bbd0;
            }
        """)
        cage_sell_btn.clicked.connect(lambda: self.set_add_mode('cage_sell'))
        chart_control_layout.addWidget(cage_sell_btn)
        self.cage_sell_tool_btn = cage_sell_btn
        
        # 网格买入按钮
        grid_buy_btn = QPushButton("⊞买")
        grid_buy_btn.setToolTip("网格买入：拖动创建价格区间，价格从高到低依次触及网格点时买入，默认2格，可右键设置")
        grid_buy_btn.setCheckable(True)
        grid_buy_btn.setMinimumWidth(60)
        grid_buy_btn.setMinimumHeight(28)
        grid_buy_btn.setStyleSheet("""
            QPushButton {
                background-color: #00897b; 
                color: white; 
                padding: 5px 10px;
                border: 2px solid #26a69a;
                border-radius: 4px;
                font-weight: normal;
            }
            QPushButton:checked {
                background-color: #004d40;
                border: 4px solid #000000;
                padding: 8px 16px;
                margin: -2px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover:!checked {
                background-color: #26a69a;
                border: 2px solid #4db6ac;
            }
        """)
        grid_buy_btn.clicked.connect(lambda: self.set_add_mode('grid_buy'))
        chart_control_layout.addWidget(grid_buy_btn)
        self.grid_buy_tool_btn = grid_buy_btn
        
        # 网格卖出按钮
        grid_sell_btn = QPushButton("⊞卖")
        grid_sell_btn.setToolTip("网格卖出：拖动创建价格区间，价格从低到高依次触及网格点时卖出，默认2格，可右键设置")
        grid_sell_btn.setCheckable(True)
        grid_sell_btn.setMinimumWidth(60)
        grid_sell_btn.setMinimumHeight(28)
        grid_sell_btn.setStyleSheet("""
            QPushButton {
                background-color: #d32f2f; 
                color: white; 
                padding: 5px 10px;
                border: 2px solid #f44336;
                border-radius: 4px;
                font-weight: normal;
            }
            QPushButton:checked {
                background-color: #b71c1c;
                border: 4px solid #000000;
                padding: 8px 16px;
                margin: -2px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover:!checked {
                background-color: #f44336;
                border: 2px solid #e57373;
            }
        """)
        grid_sell_btn.clicked.connect(lambda: self.set_add_mode('grid_sell'))
        chart_control_layout.addWidget(grid_sell_btn)
        self.grid_sell_tool_btn = grid_sell_btn
        
        # 夜市买入按钮
        night_buy_btn = QPushButton("🌙买")
        night_buy_btn.setToolTip("夜市买入：点击按钮后在图表上点击添加夜市买入点，作为夜市委托单提交")
        night_buy_btn.setCheckable(True)
        night_buy_btn.setMinimumWidth(60)
        night_buy_btn.setMinimumHeight(28)
        night_buy_btn.setStyleSheet("""
            QPushButton {
                background-color: #5c6bc0; 
                color: white; 
                padding: 5px 10px;
                border: 2px solid #7986cb;
                border-radius: 4px;
                font-weight: normal;
            }
            QPushButton:checked {
                background-color: #283593;
                border: 4px solid #000000;
                padding: 8px 16px;
                margin: -2px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover:!checked {
                background-color: #7986cb;
                border: 2px solid #9fa8da;
            }
        """)
        night_buy_btn.clicked.connect(lambda: self.set_add_mode('night_buy'))
        chart_control_layout.addWidget(night_buy_btn)
        self.night_buy_tool_btn = night_buy_btn
        
        # 夜市卖出按钮
        night_sell_btn = QPushButton("🌙卖")
        night_sell_btn.setToolTip("夜市卖出：点击按钮后在图表上点击添加夜市卖出点，作为夜市委托单提交")
        night_sell_btn.setCheckable(True)
        night_sell_btn.setMinimumWidth(60)
        night_sell_btn.setMinimumHeight(28)
        night_sell_btn.setStyleSheet("""
            QPushButton {
                background-color: #ab47bc; 
                color: white; 
                padding: 5px 10px;
                border: 2px solid #ba68c8;
                border-radius: 4px;
                font-weight: normal;
            }
            QPushButton:checked {
                background-color: #6a1b9a;
                border: 4px solid #000000;
                padding: 8px 16px;
                margin: -2px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover:!checked {
                background-color: #ba68c8;
                border: 2px solid #ce93d8;
            }
        """)
        night_sell_btn.clicked.connect(lambda: self.set_add_mode('night_sell'))
        chart_control_layout.addWidget(night_sell_btn)
        self.night_sell_tool_btn = night_sell_btn
        
        # 定时清仓按钮
        scheduled_clear_btn = QPushButton("⏰定时清仓")
        scheduled_clear_btn.setToolTip("定时清仓：到达指定时间且价格低于指定价格时自动卖出")
        scheduled_clear_btn.setCheckable(True)
        scheduled_clear_btn.setMinimumWidth(80)
        scheduled_clear_btn.setMinimumHeight(28)
        scheduled_clear_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff9800; 
                color: white; 
                padding: 5px 10px;
                border: 2px solid #ffb74d;
                border-radius: 4px;
                font-weight: normal;
            }
            QPushButton:checked {
                background-color: #f57c00;
                border: 4px solid #000000;
                padding: 8px 16px;
                margin: -2px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover:!checked {
                background-color: #ffb74d;
                border: 2px solid #ffcc80;
            }
        """)
        scheduled_clear_btn.clicked.connect(self.toggle_scheduled_clear)
        scheduled_clear_btn.setContextMenuPolicy(Qt.CustomContextMenu)
        scheduled_clear_btn.customContextMenuRequested.connect(self.show_scheduled_clear_menu)
        chart_control_layout.addWidget(scheduled_clear_btn)
        self.scheduled_clear_tool_btn = scheduled_clear_btn
        
        # 更多规则按钮
        more_btn = QPushButton("⚙️更多")
        more_btn.setToolTip("更多规则类型")
        more_btn.setCheckable(True)
        more_btn.setMinimumWidth(60)
        more_btn.setMinimumHeight(28)
        more_btn.setStyleSheet("""
            QPushButton {
                background-color: #607d8b; 
                color: white; 
                padding: 5px 10px;
                border: 2px solid #78909c;
                border-radius: 4px;
                font-weight: normal;
            }
            QPushButton:checked {
                background-color: #37474f;
                border: 4px solid #000000;
                padding: 8px 16px;
                margin: -2px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover:!checked {
                background-color: #78909c;
                border: 2px solid #90a4ae;
            }
        """)
        more_btn.clicked.connect(self.show_more_rules)
        chart_control_layout.addWidget(more_btn)
        self.more_tool_btn = more_btn
        
        chart_control_layout.addStretch()
        
        # 将控件布局插入到主布局的最前面（在canvas之前）
        main_layout.insertLayout(0, chart_control_layout)
    
    def set_controls_visible(self, visible):
        """动态设置控件布局的显示/隐藏状态"""
        # 如果需要显示但布局不存在，先创建它
        if visible and self.chart_control_layout is None:
            self._create_control_layout()
        
        if self.chart_control_layout is not None:
            # 遍历布局中的所有控件并设置可见性
            for i in range(self.chart_control_layout.count()):
                item = self.chart_control_layout.itemAt(i)
                if item and item.widget():
                    item.widget().setVisible(visible)
        
        # 注意：不再在这里设置最小高度，应该由调用者根据列数来设置
        # 连接鼠标事件（如果还没有连接的话）
        if not hasattr(self, '_mouse_events_connected'):
            self.canvas.mpl_connect('button_press_event', self.on_mouse_press)
            self.canvas.mpl_connect('button_release_event', self.on_mouse_release)
            self.canvas.mpl_connect('motion_notify_event', self.on_mouse_move)
            self._mouse_events_connected = True
    
    def _get_price_precision(self):
        """获取价格精度（根据股票代码）"""
        return SecurityTypeUtil.get_price_precision(self.stock_code)

    def _default_cage_wall_thickness(self):
        """笼子缺省壁厚（元）：ETF/基金 0.003，股票 0.03。"""
        if SecurityTypeUtil.is_fund(self.stock_code):
            return 0.003
        return 0.03

    def _clamp_rule_price_interval(self, price_low, price_high, context=""):
        """笼子/网格：将 [低价, 高价] 约束在涨跌停内；超出时整体平移（与 K 线拖动笼子逻辑一致），并保证低价 < 高价。"""
        try:
            self._ensure_session_prev_close_and_limits()
        except Exception:
            pass
        precision = self._get_price_precision()
        step = 10 ** (-precision)

        limit_up_price = None
        limit_down_price = None
        if getattr(self, "limit_up_price", 0) and self.limit_up_price > 0:
            limit_up_price = self.limit_up_price
        if getattr(self, "limit_down_price", 0) and self.limit_down_price > 0:
            limit_down_price = self.limit_down_price
        if (not limit_up_price or not limit_down_price) and getattr(self, "prev_close_price", 0) and self.prev_close_price > 0:
            try:
                lu, ld = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                if lu:
                    limit_up_price = lu
                    self.limit_up_price = lu
                if ld:
                    limit_down_price = ld
                    self.limit_down_price = ld
            except Exception as e:
                self.logger.warning(f"[{self.stock_code}] _clamp_rule_price_interval 无法计算涨跌停: {e}")

        pl, ph = float(price_low), float(price_high)
        if ph < pl:
            pl, ph = ph, pl

        orig = (pl, ph)

        if limit_up_price is not None:
            if round(ph, precision) > round(limit_up_price, precision):
                delta = ph - limit_up_price
                ph = limit_up_price
                pl -= delta
        if limit_down_price is not None:
            if round(pl, precision) < round(limit_down_price, precision):
                delta = limit_down_price - pl
                pl = limit_down_price
                ph += delta

        pl = round(pl, precision)
        ph = round(ph, precision)
        if limit_up_price is not None:
            ph = min(ph, round(limit_up_price, precision))
        if limit_down_price is not None:
            pl = max(pl, round(limit_down_price, precision))

        if pl >= ph:
            lu_r = round(limit_up_price, precision) if limit_up_price is not None else None
            ld_r = round(limit_down_price, precision) if limit_down_price is not None else None
            if lu_r is not None and ld_r is not None and lu_r > ld_r:
                ph = min(lu_r, pl + step)
                pl = max(ld_r, ph - step)
                if pl >= ph:
                    mid = (ld_r + lu_r) / 2.0
                    pl = round(max(ld_r, min(mid - step / 2, lu_r - step)), precision)
                    ph = round(min(lu_r, max(pl + step, ld_r + step)), precision)
            if pl >= ph:
                ph = pl + step
                if lu_r is not None:
                    ph = min(ph, lu_r)
                if ld_r is not None:
                    pl = max(ld_r, ph - step)
                    ph = max(ph, pl + step)

        if (round(orig[0], precision), round(orig[1], precision)) != (pl, ph):
            tag = f" {context}" if context else ""
            self.logger.info(
                f"[{self.stock_code}]{tag} 价格区间已钳位: [{orig[0]:.{precision}f}, {orig[1]:.{precision}f}]"
                f" -> [{pl:.{precision}f}, {ph:.{precision}f}]"
            )
        return pl, ph

    def _get_cage_inner_bounds(self, rule):
        """笼子规则的有效内区间（考虑壁厚）：[inner_low, inner_high]。

        - 壁厚单位为元。
        - 壁厚 = 0 时，等价于当前逻辑（内区间就是 [price_low, price_high]）。
        - 壁厚 > 0 时，有效内区间为 [price_low + 壁厚, price_high - 壁厚]。
          只有价格进入该内区间才算「进入笼子」。
        """
        price_low = rule.get('price_low', 0) or 0
        price_high = rule.get('price_high', 0) or 0
        wt = rule.get('wall_thickness', 0) or 0

        # 壁厚为 0：退化为原来的区间
        if wt <= 0:
            return price_low, price_high

        inner_low = price_low + wt
        inner_high = price_high - wt

        # 如果壁厚过大导致内区间反转，则退化为中点
        if inner_low > inner_high:
            mid = (price_low + price_high) / 2.0
            inner_low = inner_high = mid

        return inner_low, inner_high

    def set_rules(self, rules):
        """设置交易规则列表"""
        self.rules = rules if rules else []
        for r in self.rules:
            if (
                r.get("type") == "scheduled_clear"
                and not r.get("scheduled_clear_executed")
                and not (r.get("scheduled_clear_effective_date") or "").strip()
            ):
                self._attach_scheduled_clear_effective_date(r, reset_runtime=False)
        
        # 检查是否有定时清仓规则，如果有则启动定时器
        scheduled_clear_rules = [
            r for r in self.rules 
            if r.get('type') == 'scheduled_clear' and r.get('enabled', True)
        ]
        if scheduled_clear_rules:
            # 启动定时器（如果还没有启动）
            if not self.scheduled_clear_timer:
                from PyQt5.QtCore import QTimer
                self.scheduled_clear_timer = QTimer()
                self.scheduled_clear_timer.timeout.connect(self.check_scheduled_clear)
            if not self.scheduled_clear_timer.isActive():
                self.scheduled_clear_timer.start(1000)  # 每秒检查一次
        
        # 规则变化后，重绘图表
        if hasattr(self, 'price_position_ax'):
            self.update_chart()
    
    def set_task_status(self, task_running=False, task_paused=False):
        """设置任务运行状态（从保存的数据中恢复）"""
        self.task_running = task_running
        self.task_paused = task_paused
        
        # 更新UI显示：按钮显示状态并包含操作提示
        if self.task_running and not self.task_paused:
            # 运行中（绿色边框+浅绿背景）
            self.toggle_btn.setText("🟢 运行中 | 暂停")
            self.toggle_btn.setStyleSheet("font-weight: bold; padding: 2px 12px; background-color: #f1f8f4; border: 2px solid #4caf50; border-radius: 8px; color: #2e7d32; font-size: 11px;")
            self.toggle_btn.setEnabled(True)
        elif self.task_paused:
            # 已暂停（橙色边框+浅橙背景）
            self.toggle_btn.setText("🟡 已暂停 | 继续")
            self.toggle_btn.setStyleSheet("font-weight: bold; padding: 2px 12px; background-color: #fff8f0; border: 2px solid #ff9800; border-radius: 8px; color: #e65100; font-size: 11px;")
            self.toggle_btn.setEnabled(True)
        else:
            # 未运行（灰色边框+浅灰背景）
            self.toggle_btn.setText("🔴 未运行 | 启动")
            self.toggle_btn.setStyleSheet("font-weight: bold; padding: 2px 12px; background-color: #fafafa; border: 2px solid #999; border-radius: 8px; color: #666; font-size: 11px;")
            self.toggle_btn.setEnabled(True)
    
    def set_add_mode(self, mode):
        """设置添加模式"""
        # 如果点击已选中的按钮，取消选中
        if self.add_mode == mode:
            self.add_mode = None
            self._uncheck_all_tool_buttons()
        else:
            self.add_mode = mode
            self._uncheck_all_tool_buttons()
            # 选中对应的按钮
            if mode == 'single_buy' and hasattr(self, 'buy_tool_btn'):
                self.buy_tool_btn.setChecked(True)
            elif mode == 'single_sell' and hasattr(self, 'sell_tool_btn'):
                self.sell_tool_btn.setChecked(True)
            elif mode == 'breakthrough_buy' and hasattr(self, 'breakthrough_buy_tool_btn'):
                self.breakthrough_buy_tool_btn.setChecked(True)
            elif mode == 'breakthrough_sell' and hasattr(self, 'breakthrough_sell_tool_btn'):
                self.breakthrough_sell_tool_btn.setChecked(True)
            elif mode == 'cage_buy' and hasattr(self, 'cage_buy_tool_btn'):
                self.cage_buy_tool_btn.setChecked(True)
            elif mode == 'cage_sell' and hasattr(self, 'cage_sell_tool_btn'):
                self.cage_sell_tool_btn.setChecked(True)
            elif mode == 'best_buy' and hasattr(self, 'best_buy_tool_btn'):
                self.best_buy_tool_btn.setChecked(True)
            elif mode == 'best_sell' and hasattr(self, 'best_sell_tool_btn'):
                self.best_sell_tool_btn.setChecked(True)
            elif mode == 'grid_buy' and hasattr(self, 'grid_buy_tool_btn'):
                self.grid_buy_tool_btn.setChecked(True)
            elif mode == 'grid_sell' and hasattr(self, 'grid_sell_tool_btn'):
                self.grid_sell_tool_btn.setChecked(True)
            elif mode == 'night_buy' and hasattr(self, 'night_buy_tool_btn'):
                self.night_buy_tool_btn.setChecked(True)
            elif mode == 'night_sell' and hasattr(self, 'night_sell_tool_btn'):
                self.night_sell_tool_btn.setChecked(True)
            elif mode == 'scheduled_clear' and hasattr(self, 'scheduled_clear_tool_btn'):
                self.scheduled_clear_tool_btn.setChecked(True)
        
        # 更新光标样式
        if self.add_mode:
            self.canvas.setCursor(Qt.CrossCursor)
        else:
            self.canvas.setCursor(Qt.ArrowCursor)
            # 清除价格提示
            self._safe_remove_price_hint()
            self.canvas.draw()
    
    def _uncheck_all_tool_buttons(self):
        """取消所有工具按钮的选中状态"""
        if hasattr(self, 'buy_tool_btn'):
            self.buy_tool_btn.setChecked(False)
        if hasattr(self, 'sell_tool_btn'):
            self.sell_tool_btn.setChecked(False)
        if hasattr(self, 'breakthrough_buy_tool_btn'):
            self.breakthrough_buy_tool_btn.setChecked(False)
        if hasattr(self, 'breakthrough_sell_tool_btn'):
            self.breakthrough_sell_tool_btn.setChecked(False)
        if hasattr(self, 'cage_buy_tool_btn'):
            self.cage_buy_tool_btn.setChecked(False)
        if hasattr(self, 'cage_sell_tool_btn'):
            self.cage_sell_tool_btn.setChecked(False)
        if hasattr(self, 'best_buy_tool_btn'):
            self.best_buy_tool_btn.setChecked(False)
        if hasattr(self, 'best_sell_tool_btn'):
            self.best_sell_tool_btn.setChecked(False)
        if hasattr(self, 'grid_buy_tool_btn'):
            self.grid_buy_tool_btn.setChecked(False)
        if hasattr(self, 'grid_sell_tool_btn'):
            self.grid_sell_tool_btn.setChecked(False)
        if hasattr(self, 'night_buy_tool_btn'):
            self.night_buy_tool_btn.setChecked(False)
        if hasattr(self, 'night_sell_tool_btn'):
            self.night_sell_tool_btn.setChecked(False)
        if hasattr(self, 'scheduled_clear_tool_btn'):
            self.scheduled_clear_tool_btn.setChecked(False)

    def apply_position_sell_strategy(self):
        """一键应用持仓卖出策略：读取可用持仓，添加突破卖出80%、三个笼子卖出(30%/50%/70%)、定时清仓100%"""
        import uuid
        from PyQt5.QtWidgets import QMessageBox
        from datetime import time as dt_time
        avail = getattr(self, 'position_volume', 0) or 0
        if avail <= 0:
            QMessageBox.warning(
                self,
                "持仓卖出策略",
                "当前可用持仓为 0，无法应用持仓卖出策略。\n请确保该任务已关联持仓且有可用数量。"
            )
            return
        avail = int(avail) // 100 * 100
        if avail < 100:
            QMessageBox.warning(self, "持仓卖出策略", "可用持仓不足 100 股，无法添加规则。")
            return
        try:
            self.calculate_key_points(force_recalculate=True)
        except Exception as e:
            self.logger.warning(f"[{self.stock_code}] 计算关键价格失败: {e}")
        kp = {name: price for name, price in (getattr(self, 'key_points', []) or []) if isinstance(price, (int, float)) and price > 0}
        open_price = kp.get('今开盘') or getattr(self, 'prev_close_price', 0)
        limit_up = kp.get('涨停板') or getattr(self, 'limit_up_price', 0)
        ma2 = kp.get('2日')
        ma5 = kp.get('5日')
        ma10 = kp.get('10日')
        ma20 = kp.get('20日')
        if not open_price or not limit_up:
            QMessageBox.warning(
                self,
                "持仓卖出策略",
                "无法获取今开盘或涨停板价格，请确保已加载行情数据后再试。"
            )
            return
        if not all([ma5, ma10, ma20]):
            QMessageBox.warning(
                self,
                "持仓卖出策略",
                "无法获取 5日/10日/20日 均线价格，请确保已加载行情后再试。"
            )
            return
        if ma2 is None or ma2 <= 0:
            ma2 = (open_price + (self.prev_close_price or open_price)) / 2.0
        precision = self._get_price_precision()
        break_price = round(open_price * 0.97, precision)
        clear_price = round(max(ma20, open_price * 0.98), precision)
        v80 = max(100, int(avail * 0.8 / 100) * 100)
        v30 = max(100, int(avail * 0.3 / 100) * 100)
        v50 = max(100, int(avail * 0.5 / 100) * 100)
        v70 = max(100, int(avail * 0.7 / 100) * 100)
        default_time = getattr(self, 'scheduled_clear_time', None) or dt_time(14, 56, 0)
        time_str = default_time.strftime("%H:%M:%S") if hasattr(default_time, 'strftime') else "14:56:00"
        added = []
        r1 = {
            'id': f"rule_{uuid.uuid4().hex[:8]}",
            'type': 'breakthrough_sell',
            'name': '突破卖出（开盘-3%）',
            'enabled': True,
            'price': break_price,
            'volume': v80
        }
        self.rules.append(r1)
        added.append(f"突破卖出80%（{break_price}元）{v80}股")
        for low_name, low_val, pct, vol in [
            ('5日线', ma5, 30, v30),
            ('10日线', ma10, 50, v50),
            ('2日线', ma2, 70, v70)
        ]:
            low_val = round(float(low_val), precision)
            if limit_up <= low_val:
                continue
            r = {
                'id': f"rule_{uuid.uuid4().hex[:8]}",
                'type': 'cage_sell',
                'name': f'笼子卖出（{low_name}-涨停）',
                'enabled': True,
                'price_low': low_val,
                'price_high': round(limit_up, precision),
                'volume': vol,
                'cage_entered': False,
                'wall_thickness': self._default_cage_wall_thickness()
            }
            self.rules.append(r)
            added.append(f"笼子卖出（{low_name}-涨停）{pct}% {vol}股")
        r_clear = {
            'id': f"rule_{uuid.uuid4().hex[:8]}",
            'type': 'scheduled_clear',
            'name': '定时清仓',
            'enabled': True,
            'price': clear_price,
            'volume': avail,
            'scheduled_clear_time': time_str,
            'scheduled_clear_executed': False
        }
        self._attach_scheduled_clear_effective_date(r_clear, reset_runtime=False)
        self.rules.append(r_clear)
        added.append(f"定时清仓（{time_str}，清仓价={clear_price}元）{avail}股")
        if not self.scheduled_clear_timer:
            from PyQt5.QtCore import QTimer
            self.scheduled_clear_timer = QTimer()
            self.scheduled_clear_timer.timeout.connect(self.check_scheduled_clear)
        if not self.scheduled_clear_timer.isActive():
            self.scheduled_clear_timer.start(1000)
        self._save_rules()
        self.update_chart()
        QMessageBox.information(
            self,
            "持仓卖出策略",
            f"已按可用持仓 {avail} 股添加以下规则：\n\n" + "\n".join(added)
        )
    
    def _clear_add_mode(self):
        """清除添加模式（延迟调用，用于视觉反馈）"""
        self.add_mode = None
        self._uncheck_all_tool_buttons()
        self.canvas.setCursor(Qt.ArrowCursor)
        # 清除价格提示
        self._safe_remove_price_hint()
        # 清除保存的鼠标位置
        self.last_mouse_position = None
        self.canvas.draw()
    
    def show_more_rules(self):
        """显示更多规则对话框（高级规则）"""
        import time
        import json
        # #region agent log
        try:
            log_path = os.devnull
            log_entry = {
                'sessionId': 'debug-session',
                'runId': 'run1',
                'hypothesisId': 'C',
                'location': 'stock_chart_widget.py:1333',
                'message': 'show_more_rules called',
                'data': {'stock_code': self.stock_code, 'timestamp': int(time.time() * 1000)}
            }
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except: pass
        # #endregion
        
        from ui.rules_manager_dialog import RulesManagerDialog
        
        # 设置"更多"按钮为按下状态
        if hasattr(self, 'more_tool_btn'):
            self.more_tool_btn.setChecked(True)
        
        dialog_init_start = time.time()
        dialog = RulesManagerDialog(self.rules, self.stock_code, self.stock_name, self)
        dialog_init_time = time.time() - dialog_init_start
        # #region agent log
        try:
            log_path = os.devnull
            log_entry = {
                'sessionId': 'debug-session',
                'runId': 'run1',
                'hypothesisId': 'C',
                'location': 'stock_chart_widget.py:1341',
                'message': 'RulesManagerDialog created',
                'data': {'stock_code': self.stock_code, 'init_time': dialog_init_time, 'timestamp': int(time.time() * 1000)}
            }
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except: pass
        # #endregion
        dialog_result = dialog.exec_()
        
        # 关闭对话框后恢复按钮状态
        if hasattr(self, 'more_tool_btn'):
            self.more_tool_btn.setChecked(False)
        
        if dialog_result == QDialog.Accepted:
            # 获取更新后的规则
            new_rules = dialog.get_rules()
            
            # 验证弹性卖出规则的价格限制
            limit_up_price = None
            limit_down_price = None
            if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                limit_up_price = self.limit_up_price
            if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                limit_down_price = self.limit_down_price
            if not limit_up_price or not limit_down_price:
                if hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                    limit_up_price, limit_down_price = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                    if limit_up_price:
                        self.limit_up_price = limit_up_price
                    if limit_down_price:
                        self.limit_down_price = limit_down_price
            
            # 检查所有弹性买入和弹性卖出规则
            precision = self._get_price_precision()
            invalid_rules = []
            for rule in new_rules:
                rule_type = rule.get('type')
                trigger_price = rule.get('trigger_price', 0)
                if trigger_price > 0:
                    trigger_price_rounded = round(trigger_price, precision)
                    
                    if rule_type == 'best_buy':
                        # 弹性买入：不能超过涨停价且不能低于跌停价
                        if limit_up_price:
                            limit_up_price_rounded = round(limit_up_price, precision)
                            if trigger_price_rounded > limit_up_price_rounded:
                                invalid_rules.append({
                                    'rule': rule,
                                    'reason': f"触发价 {trigger_price:.2f} 元超过涨停价 {limit_up_price:.2f} 元"
                                })
                        if limit_down_price:
                            limit_down_price_rounded = round(limit_down_price, precision)
                            if trigger_price_rounded < limit_down_price_rounded:
                                invalid_rules.append({
                                    'rule': rule,
                                    'reason': f"触发价 {trigger_price:.2f} 元低于跌停价 {limit_down_price:.2f} 元"
                                })
                    elif rule_type == 'best_sell':
                        # 弹性卖出：不能超过涨停价且不能低于跌停价
                        if limit_up_price:
                            limit_up_price_rounded = round(limit_up_price, precision)
                            if trigger_price_rounded > limit_up_price_rounded:
                                invalid_rules.append({
                                    'rule': rule,
                                    'reason': f"触发价 {trigger_price:.2f} 元超过涨停价 {limit_up_price:.2f} 元"
                                })
                        if limit_down_price:
                            limit_down_price_rounded = round(limit_down_price, precision)
                            if trigger_price_rounded < limit_down_price_rounded:
                                invalid_rules.append({
                                    'rule': rule,
                                    'reason': f"触发价 {trigger_price:.2f} 元低于跌停价 {limit_down_price:.2f} 元"
                                })
            
            # 如果有无效规则，显示警告并拒绝保存
            if invalid_rules:
                from PyQt5.QtWidgets import QMessageBox
                msg = QMessageBox()
                msg.setIcon(QMessageBox.Warning)
                msg.setWindowTitle("⚠️ 规则价格超出限制")
                error_text = "<b>以下弹性买入/卖出规则的价格超出限制：</b><br><br>"
                for item in invalid_rules:
                    rule_name = item['rule'].get('name', '未命名规则')
                    error_text += f"• {rule_name}: {item['reason']}<br>"
                error_text += "<br>请修改规则价格后再保存。"
                msg.setText(error_text)
                msg.setStandardButtons(QMessageBox.Ok)
                msg.exec_()
                return  # 不保存规则
            
            # 验证通过，保存规则
            self.rules = new_rules
            self._save_rules()
            # 更新图表显示
            self.update_chart()
    
    def start_task(self):
        """启动任务"""
        from PyQt5.QtWidgets import QMessageBox
        
        # 安全检查：检测是否有规则会立即触发
        immediate_triggers = []
        if self.current_price > 0:
            from core.rule_activation import rule_activation_allows_trigger
            for rule in self.rules:
                if not rule.get('enabled', True) or rule.get('executed', False):
                    continue  # 跳过禁用或已执行的规则
                if not rule_activation_allows_trigger(rule):
                    continue
                
                rule_type = rule.get('type')
                rule_name = rule.get('name', '未命名规则')
                price = rule.get('price', 0)
                volume = rule.get('volume', 0)
                
                if rule_type == 'single_buy' and price >= self.current_price:
                    immediate_triggers.append({
                        'name': rule_name,
                        'type': '买入',
                        'reason': f"买入价({price:.2f}元) >= 当前价({self.current_price:.2f}元)",
                        'volume': volume
                    })
                elif rule_type == 'breakthrough_buy':
                    will_now, why = self._breakthrough_buy_may_trigger_immediately(rule)
                    if will_now:
                        immediate_triggers.append({
                            'name': rule_name,
                            'type': '买入',
                            'reason': why,
                            'volume': volume
                        })
                elif rule_type == 'single_sell' and price <= self.current_price:
                    immediate_triggers.append({
                        'name': rule_name,
                        'type': '卖出',
                        'reason': f"卖出价({price:.2f}元) <= 当前价({self.current_price:.2f}元)",
                        'volume': volume
                    })
                elif rule_type == 'breakthrough_sell' and price > self.current_price:
                    immediate_triggers.append({
                        'name': rule_name,
                        'type': '卖出',
                        'reason': f"卖出价({price:.2f}元) <= 当前价({self.current_price:.2f}元)",
                        'volume': volume
                    })
                elif rule_type == 'cage_buy':
                    price_low = rule.get('price_low', 0)
                    price_high = rule.get('price_high', 0)
                    inner_low, inner_high = self._get_cage_inner_bounds(rule)
                    cage_entered = rule.get('cage_entered', False)
                    # 只有已经进入过笼子，且当前价跌破内下沿或突破外上沿时才会触发
                    if cage_entered and (self.current_price <= inner_low or self.current_price >= price_high):
                        trigger_reason = f"已进入笼子，价格{self.current_price:.2f}元 {'≤ 内下沿' if self.current_price <= inner_low else '≥ 上限'}"
                        immediate_triggers.append({
                            'name': rule_name,
                            'type': '买入',
                            'reason': f"笼子买入 {trigger_reason}",
                            'volume': volume
                        })
                elif rule_type == 'cage_sell':
                    price_low = rule.get('price_low', 0)
                    price_high = rule.get('price_high', 0)
                    inner_low, inner_high = self._get_cage_inner_bounds(rule)
                    cage_entered = rule.get('cage_entered', False)
                    # 只有已经进入过笼子，且当前价跌破外下沿或突破内上沿时才会触发
                    if cage_entered and (self.current_price <= price_low or self.current_price >= inner_high):
                        trigger_reason = f"已进入笼子，价格{self.current_price:.2f}元 {'≤ 下限' if self.current_price <= price_low else '≥ 内上沿'}"
                        immediate_triggers.append({
                            'name': rule_name,
                            'type': '卖出',
                            'reason': f"笼子卖出 {trigger_reason}",
                            'volume': volume
                        })
                elif rule_type == 'grid_buy':
                    # 网格买入：检查当前价格是否 <= 任意未执行的网格点
                    start_price = rule.get('start_price', 0)
                    end_price = rule.get('end_price', 0)
                    num_grids = rule.get('num_grids', 2)
                    volume_per_grid = rule.get('volume_per_grid', rule.get('volume', 0))
                    executed_grids = rule.get('executed_grids', [])
                    
                    if start_price > 0 and end_price > 0:
                        # 计算所有网格价格点（从高到低）
                        for i in range(num_grids + 1):
                            if i in executed_grids:
                                continue  # 该网格已执行，跳过
                            
                            # 计算网格价格
                            if i == 0:
                                grid_price = start_price  # 高价端
                            elif i == num_grids:
                                grid_price = end_price    # 低价端
                            else:
                                precision = self._get_price_precision()
                                grid_price = round(start_price - (start_price - end_price) * i / num_grids, precision)
                            
                            # 网格买入：价格 <= 网格价格时触发
                            if self.current_price <= grid_price:
                                immediate_triggers.append({
                                    'name': rule_name,
                                    'type': '买入',
                                    'reason': f"网格买入 当前价({self.current_price:.2f}元) ≤ 网格点{i}({grid_price:.2f}元)",
                                    'volume': volume_per_grid
                                })
                                break  # 只记录第一个会触发的网格点
                elif rule_type == 'grid_sell':
                    # 网格卖出：检查当前价格是否 >= 任意未执行的网格点
                    start_price = rule.get('start_price', 0)
                    end_price = rule.get('end_price', 0)
                    num_grids = rule.get('num_grids', 2)
                    volume_per_grid = rule.get('volume_per_grid', rule.get('volume', 0))
                    executed_grids = rule.get('executed_grids', [])
                    
                    if start_price > 0 and end_price > 0:
                        # 计算所有网格价格点（从低到高）
                        for i in range(num_grids + 1):
                            if i in executed_grids:
                                continue  # 该网格已执行，跳过
                            
                            # 计算网格价格
                            if i == 0:
                                grid_price = start_price  # 低价端
                            elif i == num_grids:
                                grid_price = end_price    # 高价端
                            else:
                                precision = self._get_price_precision()
                                grid_price = round(start_price + (end_price - start_price) * i / num_grids, precision)
                            
                            # 网格卖出：价格 >= 网格价格时触发
                            if self.current_price >= grid_price:
                                immediate_triggers.append({
                                    'name': rule_name,
                                    'type': '卖出',
                                    'reason': f"网格卖出 当前价({self.current_price:.2f}元) ≥ 网格点{i}({grid_price:.2f}元)",
                                    'volume': volume_per_grid
                                })
                                break  # 只记录第一个会触发的网格点
        
        # 如果有会立即触发的规则，显示警告
        if immediate_triggers:
            trigger_list = "\n".join([
                f"• {t['name']} ({t['type']} {t['volume']}股) - {t['reason']}"
                for t in immediate_triggers
            ])
            
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("⚠️ 规则可能立即触发")
            msg.setText(f"<b>警告：启动后以下规则可能立即触发交易！</b>")
            msg.setInformativeText(
                f"<p>检测到 <b>{len(immediate_triggers)}</b> 个规则满足触发条件：</p>"
                f"<p style='margin-left: 20px; font-family: monospace;'>{trigger_list.replace(chr(10), '<br>')}</p>"
                f"<br><p>您希望如何处理？</p>"
            )
            
            # 添加三个按钮
            btn_start = msg.addButton("仍然启动", QMessageBox.AcceptRole)
            btn_disable = msg.addButton("禁用这些规则后启动（推荐）", QMessageBox.ActionRole)
            btn_cancel = msg.addButton("取消启动", QMessageBox.RejectRole)
            msg.setDefaultButton(btn_disable)
            
            msg.exec_()
            
            if msg.clickedButton() == btn_cancel:
                # 取消启动
                print(f"任务启动已取消: {self.stock_code} {self.stock_name}")
                return
            elif msg.clickedButton() == btn_disable:
                # 禁用这些规则
                for rule in self.rules:
                    rule_name = rule.get('name', '未命名规则')
                    if any(t['name'] == rule_name for t in immediate_triggers):
                        rule['enabled'] = False
                
                self._save_rules()
                self.update_chart()
                print(f"已自动禁用 {len(immediate_triggers)} 个规则")
            # btn_start 则直接启动
        
        # 优先通过任务管理器启动真实任务进程，避免仅UI置状态导致“看起来暂停但实际仍在运行”
        task_id = getattr(self, 'task_id', None)
        if self.task_manager and task_id:
            if not self.task_manager.start_task(task_id):
                # 仍在 running_tasks：对齐为运行中，避免「显示未运行却提示已在运行」
                if task_id in getattr(self.task_manager, "running_tasks", {}):
                    self.logger.info(
                        f"[{self.stock_code}] 任务管理器已在运行，同步图表为运行中"
                    )
                else:
                    self.logger.warning(f"[{self.stock_code}] 启动任务失败，保持当前状态")
                    return
        self.task_running = True
        self.task_paused = False
        
        # 更新UI：按钮显示状态并包含操作提示
        self.toggle_btn.setText("🟢 运行中 | 暂停")
        self.toggle_btn.setStyleSheet("font-weight: bold; padding: 2px 12px; background-color: #f1f8f4; border: 2px solid #4caf50; border-radius: 8px; color: #2e7d32; font-size: 11px;")
        self.toggle_btn.setEnabled(True)
        
        # 保存状态
        self._save_task_status()
        
        # 检查是否有夜市规则，如果有则启动夜市定时器
        # builtin：下单由大 QMT 内置策略按时间窗执行，避免图表定时器空转刷日志
        night_market_rules = [r for r in self.rules if r.get('type') in ['night_buy', 'night_sell'] and r.get('enabled', True) and not r.get('executed', False)]
        if night_market_rules:
            skip_chart_night = False
            try:
                from utils.qmt_execution_config import use_builtin_order_execution

                skip_chart_night = bool(use_builtin_order_execution())
            except Exception:
                skip_chart_night = False
            if skip_chart_night:
                self.logger.info(
                    f"[{self.stock_code}] [builtin] 夜市规则交由大 QMT 执行，"
                    f"跳过图表定时器（{len(night_market_rules)} 条）"
                )
            else:
                self._start_night_market_timer(night_market_rules)
        
        # 提前下单按规则快照；启动时有价则检查（含已挂提前单）
        if self.current_price > 0:
            from datetime import datetime
            tick_data_for_check = {
                'stock_code': self.stock_code,
                'lastPrice': self.current_price,
                'time': datetime.now()
            }
            self._update_early_orders_status(tick_data_for_check)
            self._check_early_orders(tick_data_for_check)
        else:
            self.logger.debug(f"[{self.stock_code}] 当前价为0，跳过启动时提前下单检查")
        
        print(f"任务已启动: {self.stock_code} {self.stock_name}")
    
    def pause_task(self):
        """暂停任务"""
        # 优先通过任务管理器停止真实任务进程，避免退出时仍被判定有运行任务
        task_id = getattr(self, 'task_id', None)
        if self.task_manager and task_id:
            ok = False
            try:
                ok = bool(self.task_manager.stop_task(task_id))
            except Exception as e:
                self.logger.warning(f"[{self.stock_code}] stop_task 异常: {e}")
                ok = False
            if not ok:
                # 旧版/残缺登记：尽量强制清掉，避免永远停不掉
                try:
                    if task_id in getattr(self.task_manager, "running_tasks", {}):
                        self.task_manager._force_remove_running_task(
                            task_id, send_stop=False
                        )
                    mark = getattr(self.task_manager, "_mark_task_stopped_params", None)
                    if callable(mark):
                        mark(task_id, paused=True, status="未运行")
                    self.logger.warning(
                        f"[{self.stock_code}] 暂停走强制清理，已尽量移除运行登记"
                    )
                except Exception as e:
                    self.logger.error(
                        f"[{self.stock_code}] 强制清理失败: {e}", exc_info=True
                    )
        self.task_running = False
        self.task_paused = True
        
        # 先立即更新UI（不等待撤单完成，避免卡顿）
        self.toggle_btn.setText("🟡 已暂停 | 继续")
        self.toggle_btn.setStyleSheet("font-weight: bold; padding: 2px 12px; background-color: #fff8f0; border: 2px solid #ff9800; border-radius: 8px; color: #e65100; font-size: 11px;")
        self.toggle_btn.setEnabled(True)
        
        # 停止夜市定时器
        self._stop_night_market_timer()
        
        # 保存状态
        self._save_task_status()
        
        # 在后台线程中撤消所有提前下单的订单（避免阻塞UI）
        import threading
        def cancel_in_background():
            """在后台线程中执行撤单操作"""
            try:
                self._cancel_early_orders()
            except Exception as e:
                self.logger.error(f"[{self.stock_code}] 后台撤单异常: {str(e)}", exc_info=True)
        
        cancel_thread = threading.Thread(target=cancel_in_background, daemon=True)
        cancel_thread.start()
        
        print(f"任务已暂停: {self.stock_code} {self.stock_name}")
    
    def _early_order_id_is_valid(self, order_id) -> bool:
        if not order_id:
            return False
        order_id_str = str(order_id).strip()
        return bool(
            order_id_str
            and order_id_str not in ('-1', '0')
            and order_id_str.lower() != 'none'
        )

    def _rule_has_active_early_order(self, rule) -> bool:
        """是否存在尚未结束、不应再次提前下单的委托（含撤单等待中）。"""
        if rule.get('executed', False):
            return False
        if rule.get('early_order', False) or rule.get('early_order_cancel_pending', False):
            return True
        return self._early_order_id_is_valid(rule.get('early_order_id'))

    @staticmethod
    def _parse_entrust_no_from_cancel_error(error_msg: str):
        import re
        m = re.search(r'p_entrust_no=(\d+)', error_msg or '')
        return m.group(1) if m else None

    def _clear_early_order_state(self, rule) -> None:
        rule['early_order'] = False
        rule['early_order_id'] = None
        rule['early_order_price'] = None
        rule.pop('early_order_submit_price', None)
        rule.pop('early_order_submit_volume', None)
        rule.pop('early_order_cancel_pending', None)
        rule.pop('early_manual_cancel_pending', None)

    def _get_qmt_adapter(self):
        if hasattr(self, 'task_manager') and self.task_manager:
            return getattr(self.task_manager, 'qmt_adapter', None)
        return None

    def _early_order_submitted_volume(self, rule) -> int:
        vol = rule.get('early_order_submit_volume')
        if vol is not None and int(vol or 0) > 0:
            return int(vol)
        return int(rule.get('volume', 0) or 0)

    def _is_sell_rule_type(self, rule_type: str) -> bool:
        return rule_type in (
            'single_sell', 'breakthrough_sell', 'cage_sell', 'grid_sell', 'best_sell', 'night_sell',
        )

    def _has_pending_sell_locking_position(self, qmt_adapter=None) -> bool:
        """同股是否存在未成交卖单占用可用持仓（含提前下单委托）。"""
        for rule in getattr(self, 'rules', []) or []:
            if rule.get('executed', False):
                continue
            rule_type = rule.get('type', '')
            if not self._is_sell_rule_type(rule_type):
                continue
            if self._rule_has_active_early_order(rule):
                return True

        qmt_adapter = qmt_adapter or self._get_qmt_adapter()
        if not qmt_adapter or not hasattr(qmt_adapter, 'get_today_orders'):
            return False

        try:
            from xtquant import xtconstant
            orders = qmt_adapter.get_today_orders() or []
            for order in orders:
                if getattr(order, 'stock_code', '') != self.stock_code:
                    continue
                if getattr(order, 'order_type', None) != xtconstant.STOCK_SELL:
                    continue
                order_status = getattr(order, 'order_status', None)
                if order_status in (50, 55, 48, 49, 51, 52):
                    return True
        except Exception:
            pass
        return False

    def _skip_sell_due_to_pending_order(self, rule, trade_info, tick_data, price, volume, context: str) -> bool:
        """可用持仓为0但仍有未成交卖单占仓：跳过，不结束规则。"""
        if self.position_volume > 0:
            return False
        if not self._has_pending_sell_locking_position():
            return False

        rule_name = rule.get('name', '未命名规则')
        self._log_throttled(
            key=f"{self.stock_code}:sell_deferred_pending:{rule.get('id', rule_name)}",
            message=(
                f"[{self.stock_code}] {rule_name} - {context}：可用持仓为0，"
                f"但同股仍有未成交卖单占仓，延后执行（不结束任务）"
            ),
            level="info",
            interval_s=15.0,
        )
        self._record_skipped_execution(
            rule,
            trade_info,
            tick_data,
            price,
            0,
            "PENDING_SELL_LOCK",
            f"{context}：持仓被未成交卖单占用，延后执行",
            approval_result="pending_sell_lock",
            executed_reason="pending_sell_lock",
        )
        return True

    def _finalize_no_position_sell(self, rule, trade_info, tick_data, price, volume, context: str) -> None:
        """确认无可用持仓且无未成交卖单占仓时，结束卖出规则。"""
        from datetime import datetime

        rule_name = rule.get('name', '未命名规则')
        rule['executed'] = True
        rule['executed_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        rule['executed_price'] = price
        rule['executed_volume'] = 0
        rule['order_id'] = 'NO_POSITION'
        rule['executed_reason'] = 'no_position'
        self._save_rules()
        self.logger.info(f"[{self.stock_code}] {rule_name} - {context}：可用持仓为0，任务已结束")
        self._record_skipped_execution(
            rule,
            trade_info,
            tick_data,
            price,
            0,
            "NO_POSITION",
            f"{context}：可用持仓为0，未下单（计划卖出{volume}股）",
            approval_result="no_position",
            executed_reason="no_position",
        )
        self.update_chart()

    def _finalize_early_order_manual_cancelled(self, rule, order_id=None) -> None:
        """订单列表人工撤单成功：结束任务，节点变黑（自动撤单位移至复位逻辑，不受影响）。"""
        from PyQt5.QtCore import QTimer
        from datetime import datetime

        if rule.get('executed', False):
            self._clear_early_order_state(rule)
            return

        rule_name = rule.get('name', '未命名规则')
        rule['executed'] = True
        rule['executed_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        rule['executed_price'] = (
            rule.get('early_order_submit_price')
            or rule.get('early_order_price')
            or rule.get('price', 0)
        )
        rule['executed_volume'] = 0
        rule['order_id'] = 'EARLY_CANCELLED'
        rule['executed_reason'] = 'early_cancelled'
        if order_id:
            rule['cancelled_order_id'] = str(order_id)
        elif self._early_order_id_is_valid(rule.get('early_order_id')):
            rule['cancelled_order_id'] = str(rule.get('early_order_id'))
        rule.pop('early_manual_cancel_pending', None)
        self._clear_early_order_state(rule)
        self._save_rules()
        self.logger.info(
            f"[{self.stock_code}] 提前下单已人工撤单，任务结束(黑节点): {rule_name}"
        )
        QTimer.singleShot(100, self.update_chart)

    def _mark_early_manual_cancel_pending(self, order_sysid) -> bool:
        """订单列表点撤单：给匹配的提前挂单打标记，等回报 54 后按人工撤单结束。"""
        oid = str(order_sysid or '').strip()
        if not oid:
            return False
        hit = False
        for rule in getattr(self, 'rules', []) or []:
            if not isinstance(rule, dict):
                continue
            if rule.get('executed'):
                continue
            if not (
                rule.get('early_order')
                or self._early_order_id_is_valid(rule.get('early_order_id'))
            ):
                continue
            stored = str(rule.get('early_order_id') or '').strip()
            if stored and (stored == oid or oid in stored or stored in oid):
                rule['early_manual_cancel_pending'] = True
                hit = True
                continue
            # 无 ID 时：仅当本股有唯一提前挂单才打标，避免误伤
        if hit:
            try:
                self._save_rules()
            except Exception:
                pass
            return True
        early_rules = [
            r for r in (getattr(self, 'rules', []) or [])
            if isinstance(r, dict)
            and not r.get('executed')
            and (r.get('early_order') or self._early_order_id_is_valid(r.get('early_order_id')))
        ]
        if len(early_rules) == 1:
            early_rules[0]['early_manual_cancel_pending'] = True
            try:
                self._save_rules()
            except Exception:
                pass
            return True
        return False

    def _finalize_early_order_uncancellable(self, rule, reason: str, order_id=None) -> None:
        """提前单无法撤销（通常已成交）时结束任务，避免再次下单。"""
        from PyQt5.QtCore import QTimer
        from datetime import datetime

        if rule.get('executed', False):
            return

        rule_name = rule.get('name', '未命名规则')
        rule['executed'] = True
        rule['executed_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        rule['executed_price'] = (
            rule.get('early_order_submit_price')
            or rule.get('early_order_price')
            or rule.get('price', 0)
        )
        rule['executed_volume'] = rule.get('volume', 0)
        if order_id:
            rule['order_id'] = str(order_id)
        elif self._early_order_id_is_valid(rule.get('early_order_id')):
            rule['order_id'] = str(rule.get('early_order_id'))

        self._clear_early_order_state(rule)
        self._save_rules()
        self.logger.warning(
            f"[{self.stock_code}] 提前下单撤单失败，任务已结束: {rule_name}，原因: {reason}"
        )
        QTimer.singleShot(100, self.update_chart)

    def _inspect_early_order_for_cancel(self, qmt_adapter, rule):
        """
        查询当日委托，判断提前单是否已成交/可撤。
        返回 (status, order_sysid)，status 为 filled | cancelable | unknown。
        """
        from xtquant import xtconstant

        stored_id = str(rule.get('early_order_id') or '').strip()
        submit_price = rule.get('early_order_submit_price') or rule.get('early_order_price') or rule.get('price', 0)
        target_price = rule.get('early_order_price', rule.get('price', 0))
        volume = self._early_order_submitted_volume(rule)
        rule_type = rule.get('type', '')
        is_buy = rule_type in ('single_buy', 'breakthrough_buy', 'cage_buy', 'grid_buy', 'elastic_buy')

        if not hasattr(qmt_adapter, 'get_today_orders'):
            return 'unknown', None

        orders = qmt_adapter.get_today_orders() or []
        price_candidates = []
        for px in (submit_price, target_price):
            try:
                px_val = float(px)
            except (TypeError, ValueError):
                continue
            if px_val > 0 and px_val not in price_candidates:
                price_candidates.append(px_val)

        for order in orders:
            try:
                if getattr(order, 'stock_code', '') != self.stock_code:
                    continue

                order_sysid = getattr(order, 'order_sysid', None)
                order_sysid_str = str(order_sysid).strip() if order_sysid else ''
                order_status = getattr(order, 'order_status', None)
                order_price = float(getattr(order, 'price', 0) or 0)
                order_volume = int(getattr(order, 'order_volume', 0) or 0)
                order_type = getattr(order, 'order_type', None)
                order_is_buy = order_type == xtconstant.STOCK_BUY
                if order_is_buy != is_buy:
                    continue

                id_matched = bool(
                    stored_id
                    and order_sysid_str
                    and (stored_id == order_sysid_str or order_sysid_str.endswith(stored_id))
                )
                price_matched = any(abs(order_price - px) <= 0.01 for px in price_candidates)
                volume_matched = order_volume == int(volume or 0)
                if not volume_matched and id_matched and order_volume > 0:
                    volume_matched = True

                if not (id_matched or (price_matched and volume_matched)):
                    continue

                if order_status == 56:
                    return 'filled', order_sysid_str
                if order_status in (50, 55):
                    return 'cancelable', order_sysid_str
                if order_status in (53, 54, 57):
                    return 'cancelled', order_sysid_str
            except Exception:
                continue

        return 'unknown', None

    def handle_early_order_cancel_error(self, order_id, error_msg: str) -> bool:
        """处理撤单失败回调：若对应提前单无法撤销，则结束任务。"""
        candidates = set()
        if self._early_order_id_is_valid(order_id):
            candidates.add(str(order_id).strip())
        entrust_no = self._parse_entrust_no_from_cancel_error(error_msg)
        if entrust_no:
            candidates.add(entrust_no)

        handled = False
        for rule in self.rules:
            if rule.get('executed', False):
                continue
            if not self._rule_has_active_early_order(rule):
                continue

            stored_id = str(rule.get('early_order_id') or '').strip()
            if candidates and stored_id not in candidates and not rule.get('early_order_cancel_pending'):
                continue

            self._finalize_early_order_uncancellable(
                rule,
                f"撤单失败回调: {error_msg}",
                order_id=stored_id or entrust_no or order_id,
            )
            handled = True
            break

        return handled

    def _cancel_single_early_order(self, rule):
        """撤消单个提前下单规则的订单（优化：先尝试使用已有订单ID，避免阻塞查询）"""
        from PyQt5.QtWidgets import QMessageBox
        from PyQt5.QtCore import QTimer
        import threading
        
        if not hasattr(self, 'task_manager') or not self.task_manager:
            return
        
        qmt_adapter = None
        if hasattr(self.task_manager, 'qmt_adapter'):
            qmt_adapter = self.task_manager.qmt_adapter
        
        if not qmt_adapter:
            self.logger.warning(f"[{self.stock_code}] 无法撤单：QMT适配器不可用")
            return
        
        # 只处理已提前下单但未执行的规则
        if not (rule.get('early_order', False) and 
                not rule.get('executed', False)):
            return

        if rule.get('early_order_cancel_pending'):
            return
        
        order_id = rule.get('early_order_id')
        rule_name = rule.get('name', '未命名规则')
        order_price = rule.get('early_order_price', rule.get('price', 0))
        submit_price = rule.get('early_order_submit_price', order_price)
        order_volume = self._early_order_submitted_volume(rule)
        rule_type = rule.get('type', '')
        
        self.logger.info(
            f"[{self.stock_code}] 🔍 [撤单检查] 规则: {rule_name}, 当前订单ID: {order_id} "
            f"(类型: {type(order_id)}), 目标价: {order_price}, 委托价: {submit_price}, 数量: {order_volume}"
        )
        
        # 优化：先检查是否有有效的订单ID，如果有就直接使用，避免阻塞查询
        # 订单ID可能是字符串或数字，需要检查是否有效（不是-1、空字符串等）
        order_id_valid = False
        if order_id:
            order_id_str = str(order_id).strip()
            if order_id_str and order_id_str != '-1' and order_id_str != '0' and order_id_str.lower() != 'none':
                order_id_valid = True
                self.logger.info(f"[{self.stock_code}] ✅ [撤单检查] 订单ID有效: {order_id_str}")
            else:
                self.logger.info(f"[{self.stock_code}] ⚠️ [撤单检查] 订单ID无效: {order_id_str}")
        
        # 如果有订单ID，在后台先尝试用订单列表解析出真实 order_sysid 再撤单（同步下单返回的可能是委托编号，撤单必须用 order_sysid）
        if order_id_valid:
            stored_order_id = str(order_id).strip()
            self.logger.info(f"[{self.stock_code}] 🔄 准备撤单: {rule_name} (存储的订单ID: {stored_order_id})，将先匹配真实 order_sysid 再撤单")

            rule['early_order_cancel_pending'] = True
            self._save_rules()
            QTimer.singleShot(100, self.update_chart)
            
            # 在后台线程中：先通过订单列表匹配真实 order_sysid，再撤单（避免用委托编号撤单导致「订单表记录不存在」）
            def cancel_in_background():
                try:
                    status, matched_sysid = self._inspect_early_order_for_cancel(qmt_adapter, rule)
                    if status == 'filled':
                        self._finalize_early_order_uncancellable(
                            rule,
                            "撤单前检测到委托已全部成交",
                            order_id=matched_sysid or stored_order_id,
                        )
                        return

                    matched_sysid = matched_sysid or self._find_order_by_info(
                        qmt_adapter, submit_price, order_volume, rule_type, orders=None,
                        stored_order_id=stored_order_id,
                    )
                    if not matched_sysid:
                        matched_sysid = self._find_order_by_info(
                            qmt_adapter, order_price, order_volume, rule_type, orders=None,
                            stored_order_id=stored_order_id,
                        )
                    if matched_sysid:
                        order_id_to_cancel = str(matched_sysid).strip()
                        if order_id_to_cancel != stored_order_id:
                            self.logger.info(f"[{self.stock_code}] ✅ [撤单] 使用订单列表匹配的真实 order_sysid: {stored_order_id} -> {order_id_to_cancel} (规则: {rule_name})")
                            rule['early_order_id'] = order_id_to_cancel
                    else:
                        order_id_to_cancel = stored_order_id
                        self.logger.warning(f"[{self.stock_code}] ⚠️ [撤单] 未匹配到真实 order_sysid，使用存储ID撤单: {order_id_to_cancel} (可能撤单失败)")
                    self.logger.info(f"[{self.stock_code}] 🔄 [后台撤单] 开始撤单: 规则={rule_name}, 订单ID={order_id_to_cancel}")
                    cancel_result = qmt_adapter.cancel_order(order_id_to_cancel, self.stock_code)
                    if cancel_result:
                        self.logger.info(f"[{self.stock_code}] ✅ 撤单请求已提交: {rule_name} (订单ID: {order_id_to_cancel})，等待柜台回调")
                    else:
                        self._finalize_early_order_uncancellable(
                            rule,
                            "撤单请求失败（可能订单已成交、已撤销或不存在）",
                            order_id=order_id_to_cancel,
                        )
                        from PyQt5.QtCore import QTimer
                        QTimer.singleShot(0, lambda: self._show_cancel_warning(
                            rule_name, order_id_to_cancel, "撤单请求失败（可能订单已成交、已撤销或不存在），任务已结束"
                        ))
                except Exception as e:
                    self._finalize_early_order_uncancellable(
                        rule,
                        f"撤单异常: {str(e)}",
                        order_id=stored_order_id,
                    )
                    self.logger.error(f"[{self.stock_code}] ❌ 撤单异常: {rule_name}, 错误: {str(e)}")
                    import traceback
                    self.logger.error(f"[{self.stock_code}] ❌ 撤单异常详情: {traceback.format_exc()}")
                    from PyQt5.QtCore import QTimer
                    QTimer.singleShot(0, lambda: self._show_cancel_warning(
                        rule_name, stored_order_id, f"撤单异常: {str(e)}，任务已结束"
                    ))
            
            cancel_thread = threading.Thread(target=cancel_in_background, daemon=True)
            cancel_thread.start()
            return
        
        # 如果订单ID无效，才需要查询订单列表匹配（这种情况较少）
        # 但为了不阻塞UI，我们也采用异步方式
        self.logger.info(f"[{self.stock_code}] ⏳ 订单ID无效，需要查询订单列表匹配: {rule_name} (当前订单ID: {order_id})")
        
        rule['early_order_cancel_pending'] = True
        self._save_rules()
        QTimer.singleShot(100, self.update_chart)
        
        # 在后台线程中查询并撤单
        def find_and_cancel_in_background():
            try:
                self.logger.info(f"[{self.stock_code}] 🔍 [后台匹配] 开始查询订单列表: 规则={rule_name}, 价格={order_price}, 数量={order_volume}, 类型={rule_type}")
                matched_order_sysid = self._find_order_by_info(qmt_adapter, order_price, order_volume, rule_type, orders=None)
                if matched_order_sysid:
                    order_id_to_cancel = str(matched_order_sysid).strip()  # 确保是字符串并去除空格
                    self.logger.info(f"[{self.stock_code}] ✅ [后台匹配] 匹配到真实订单号: {order_id_to_cancel} (类型: {type(order_id_to_cancel)}, 原始匹配值: {matched_order_sysid}), 规则: {rule_name}")
                    
                    try:
                        self.logger.info(f"[{self.stock_code}] 🔄 [后台撤单] 开始撤单: 规则={rule_name}, 订单ID={order_id_to_cancel} (类型: {type(order_id_to_cancel)})")
                        cancel_result = qmt_adapter.cancel_order(order_id_to_cancel, self.stock_code)
                        if cancel_result:
                            self.logger.info(f"[{self.stock_code}] ✅ 撤单成功: {rule_name} (订单ID: {order_id_to_cancel})")
                        else:
                            self.logger.warning(f"[{self.stock_code}] ⚠️ 撤单请求失败: {rule_name} (订单ID: {order_id_to_cancel})")
                            from PyQt5.QtCore import QTimer
                            QTimer.singleShot(0, lambda: self._show_cancel_warning(
                                rule_name, order_id_to_cancel, "撤单请求失败（可能订单已成交、已撤销或不存在）"
                            ))
                    except Exception as e:
                        self.logger.error(f"[{self.stock_code}] ❌ 撤单异常: {rule_name} (订单ID: {order_id_to_cancel}), 错误: {str(e)}")
                        import traceback
                        self.logger.error(f"[{self.stock_code}] ❌ 撤单异常详情: {traceback.format_exc()}")
                        from PyQt5.QtCore import QTimer
                        QTimer.singleShot(0, lambda: self._show_cancel_warning(
                            rule_name, order_id_to_cancel, f"撤单异常: {str(e)}"
                        ))
                else:
                    # 无法匹配，可能是订单已成交或不存在
                    self.logger.warning(f"[{self.stock_code}] ⚠️ 无法匹配到订单: {rule_name}，订单可能已成交、已撤销或不存在")
                    from PyQt5.QtCore import QTimer
                    QTimer.singleShot(0, lambda: self._show_cancel_warning(
                        rule_name, "未知", "订单ID尚未匹配到真实订单号，订单可能已成交、已撤销或不存在"
                    ))
            except Exception as e:
                self.logger.error(f"[{self.stock_code}] ❌ 查询并撤单过程中出错: {rule_name}, 错误: {str(e)}")
                from PyQt5.QtCore import QTimer
                QTimer.singleShot(0, lambda: self._show_cancel_warning(
                    rule_name, "未知", f"查询并撤单过程中出错: {str(e)}"
                ))
        
        # 在后台线程执行查询和撤单
        find_thread = threading.Thread(target=find_and_cancel_in_background, daemon=True)
        find_thread.start()
    
    def _show_cancel_warning(self, rule_name, order_id, reason):
        """显示撤单失败的警告消息（从后台线程调用）"""
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.warning(
            self,
            "⚠️ 撤单失败提醒",
            f"<b>{self.stock_code} {self.stock_name}</b><br><br>"
            f"规则 <b>{rule_name}</b> 的提前订单撤单失败。<br>"
            f"订单ID: {order_id}<br>"
            f"原因: {reason}<br><br>"
            f"请手动检查并撤单。",
            QMessageBox.Ok
        )
    
    def _cancel_early_orders(self):
        """撤消所有提前下单的订单"""
        from PyQt5.QtWidgets import QMessageBox
        from PyQt5.QtCore import QTimer
        
        # 优化：先快速检查是否有需要撤单的规则，如果没有直接返回，避免不必要的查询
        has_early_orders = False
        for rule in self.rules:
            if (rule.get('early_order', False) and 
                rule.get('early_order_id') and 
                not rule.get('executed', False)):
                has_early_orders = True
                break
        
        if not has_early_orders:
            # 没有需要撤单的订单，直接返回
            return
        
        if not hasattr(self, 'task_manager') or not self.task_manager:
            return
        
        qmt_adapter = None
        if hasattr(self.task_manager, 'qmt_adapter'):
            qmt_adapter = self.task_manager.qmt_adapter
        
        if not qmt_adapter:
            self.logger.warning(f"[{self.stock_code}] 无法撤单：QMT适配器不可用")
            return
        
        # 优化：只查询一次订单列表，避免多次阻塞查询（仅在确实有需要撤单的规则时查询）
        orders = None
        try:
            if hasattr(qmt_adapter, 'get_today_orders'):
                orders = qmt_adapter.get_today_orders()
        except Exception as e:
            self.logger.warning(f"[{self.stock_code}] 获取订单列表失败: {str(e)}")
        
        cancelled_count = 0
        failed_orders = []  # 记录撤单失败的订单信息
        pending_rules = []  # 记录需要延迟处理的规则（订单ID尚未匹配）
        
        for rule in self.rules:
            # 只撤单已提前下单但未执行的规则
            if (rule.get('early_order', False) and 
                rule.get('early_order_id') and 
                not rule.get('executed', False)):
                
                order_id = rule.get('early_order_id')
                rule_name = rule.get('name', '未命名规则')
                order_price = rule.get('early_order_price', rule.get('price', 0))
                order_volume = self._early_order_submitted_volume(rule)
                rule_type = rule.get('type', '')
                
                # 总是从QMT订单列表中查找真实的订单（使用order_sysid，和订单列表的撤单按钮一样）
                # 订单列表中的撤单按钮使用的是 order.order_sysid，这是唯一正确的撤单ID
                # 优化：使用已缓存的订单列表，避免重复查询
                matched_order_sysid = self._find_order_by_info(qmt_adapter, order_price, order_volume, rule_type, orders=orders)
                original_order_id = order_id
                
                if matched_order_sysid:
                    # 匹配到真实的order_sysid，使用它撤单（和订单列表一样）
                    order_id_to_cancel = matched_order_sysid
                    if str(order_id_to_cancel) != str(original_order_id):
                        rule['early_order_id'] = str(order_id_to_cancel)  # 更新为真实的order_sysid
                        self.logger.info(f"[{self.stock_code}] ✅ 匹配到真实订单号: {original_order_id} -> {order_id_to_cancel} (规则: {rule_name})")
                    
                    try:
                        # 使用真实的order_sysid撤单（和订单列表的撤单按钮一样）
                        cancel_result = qmt_adapter.cancel_order(str(order_id_to_cancel), self.stock_code)
                        
                        if cancel_result:
                            self.logger.info(f"[{self.stock_code}] ✅ 已请求撤单: {rule_name} (订单ID: {order_id_to_cancel})")
                            cancelled_count += 1
                        else:
                            # 撤单请求失败（即使使用真实order_sysid也可能失败，比如订单已成交）
                            failed_info = {
                                'rule_name': rule_name,
                                'order_id': order_id_to_cancel,
                                'price': order_price,
                                'reason': '撤单请求失败（可能订单已成交、已撤销或不存在）'
                            }
                            failed_orders.append(failed_info)
                            self.logger.warning(f"[{self.stock_code}] ⚠️ 撤单请求失败: {rule_name} (订单ID: {order_id_to_cancel})，可能订单已成交或不存在")
                            cancelled_count += 1
                        
                        # 无论撤单成功与否，都清除提前下单标记
                        rule['early_order'] = False
                        rule['early_order_price'] = None
                        
                    except Exception as e:
                        # 撤单异常
                        failed_info = {
                            'rule_name': rule_name,
                            'order_id': order_id_to_cancel,
                            'price': order_price,
                            'reason': f'撤单异常: {str(e)}'
                        }
                        failed_orders.append(failed_info)
                        self.logger.error(f"[{self.stock_code}] ❌ 撤单异常: {rule_name} (订单ID: {order_id_to_cancel}), 错误: {str(e)}")
                        rule['early_order'] = False
                        rule['early_order_price'] = None
                        cancelled_count += 1
                else:
                    # 匹配不到真实订单号，可能是订单还在处理中，延迟重试
                    self.logger.info(f"[{self.stock_code}] ⏳ 订单可能还在处理中，延迟4秒后重试匹配: {rule_name}")
                    pending_rules.append({
                        'rule': rule,
                        'rule_name': rule_name,
                        'price': order_price,
                        'volume': order_volume,
                        'rule_type': rule_type
                    })
                    continue
        
        # 如果有需要延迟处理的规则，设置定时器延迟匹配和撤销
        if pending_rules:
            def retry_cancel_pending_orders():
                """延迟重试：匹配并撤销待处理的订单"""
                retry_cancelled = 0
                retry_failed = []
                
                for pending in pending_rules:
                    rule = pending['rule']
                    rule_name = pending['rule_name']
                    order_price = pending['price']
                    order_volume = pending['volume']
                    rule_type = pending['rule_type']
                    
                    # 再次尝试匹配订单（延迟重试时重新查询订单列表）
                    matched_order_id = self._find_order_by_info(qmt_adapter, order_price, order_volume, rule_type, orders=None)
                    if matched_order_id:
                        order_id = matched_order_id
                        rule['early_order_id'] = order_id
                        self.logger.info(f"[{self.stock_code}] ✅ 延迟匹配成功: {rule_name} (订单ID: {order_id})")
                        
                        # 尝试撤销
                        try:
                            cancel_result = qmt_adapter.cancel_order(str(order_id), self.stock_code)
                            if cancel_result:
                                self.logger.info(f"[{self.stock_code}] ✅ 延迟撤销成功: {rule_name} (订单ID: {order_id})")
                                retry_cancelled += 1
                            else:
                                retry_failed.append({
                                    'rule_name': rule_name,
                                    'order_id': order_id,
                                    'price': order_price,
                                    'reason': '撤单请求失败（可能订单已成交或不存在）'
                                })
                                self.logger.warning(f"[{self.stock_code}] ⚠️ 延迟撤销失败: {rule_name} (订单ID: {order_id})")
                        except Exception as e:
                            retry_failed.append({
                                'rule_name': rule_name,
                                'order_id': order_id,
                                'price': order_price,
                                'reason': f'撤单异常: {str(e)}'
                            })
                            self.logger.error(f"[{self.stock_code}] ❌ 延迟撤销异常: {rule_name} (订单ID: {order_id}), 错误: {str(e)}")
                        
                        # 清除标记
                        rule['early_order'] = False
                        rule['early_order_price'] = None
                    else:
                        # 仍然无法匹配，记录失败
                        retry_failed.append({
                            'rule_name': rule_name,
                            'order_id': '未知',
                            'price': order_price,
                            'reason': '订单ID尚未匹配到真实订单号，且延迟匹配仍然失败（订单可能已成交、已撤销或不存在）'
                        })
                        self.logger.warning(f"[{self.stock_code}] ⚠️ 延迟匹配失败: {rule_name}，无法找到对应订单")
                        rule['early_order'] = False
                        rule['early_order_price'] = None
                
                if retry_cancelled > 0 or retry_failed:
                    self._save_rules()
                    # 优化：延迟更新图表，避免阻塞UI
                    QTimer.singleShot(100, lambda: self.canvas.draw_idle())
                    if retry_cancelled > 0:
                        self.logger.info(f"[{self.stock_code}] 延迟撤销完成: 成功 {retry_cancelled} 个")
                    if retry_failed:
                        failed_orders.extend(retry_failed)
                        # 显示失败提醒
                        message = f"<b>{self.stock_code} {self.stock_name}</b><br><br>"
                        message += "以下提前订单撤单失败，请手动检查并撤单：<br><br>"
                        
                        for failed in retry_failed:
                            message += f"• <b>{failed['rule_name']}</b><br>"
                            message += f"  订单ID: {failed['order_id']}<br>"
                            message += f"  价格: {failed['price']:.2f}元<br>"
                            message += f"  原因: {failed['reason']}<br><br>"
                        
                        message += "建议：请检查订单列表，确认订单状态并手动撤单。"
                        
                        QMessageBox.warning(
                            self,
                            "⚠️ 撤单失败提醒",
                            message,
                            QMessageBox.Ok
                        )
            
            # 延迟4秒后重试（确保覆盖一个完整的3秒轮询周期，让订单详情更新完成）
            QTimer.singleShot(4000, retry_cancel_pending_orders)
            self.logger.info(f"[{self.stock_code}] 已设置延迟撤销定时器，将在4秒后重试匹配并撤销 {len(pending_rules)} 个待处理订单")
        
        if cancelled_count > 0:
            # 保存规则状态
            self._save_rules()
            # 优化：延迟更新图表，避免阻塞UI
            QTimer.singleShot(100, self.update_chart)
            self.logger.info(f"[{self.stock_code}] 暂停任务时已撤消 {cancelled_count} 个提前订单")
        
        # 如果有立即失败的订单（不是延迟处理的），弹出警告对话框
        if failed_orders:
            message = f"<b>{self.stock_code} {self.stock_name}</b><br><br>"
            message += "以下提前订单撤单失败，请手动检查并撤单：<br><br>"
            
            for failed in failed_orders:
                message += f"• <b>{failed['rule_name']}</b><br>"
                message += f"  订单ID: {failed['order_id']}<br>"
                message += f"  价格: {failed['price']:.2f}元<br>"
                message += f"  原因: {failed['reason']}<br><br>"
            
            message += "建议：请检查订单列表，确认订单状态并手动撤单。"
            
            QMessageBox.warning(
                self,
                "⚠️ 撤单失败提醒",
                message,
                QMessageBox.Ok
            )
    
    def _find_order_by_info(self, qmt_adapter, price, volume, rule_type, orders=None, stored_order_id=None):
        """通过价格、数量、类型等信息从QMT订单列表中匹配订单
        
        Args:
            qmt_adapter: QMT适配器
            price: 订单价格
            volume: 订单数量（优先使用 early_order_submit_volume）
            rule_type: 规则类型
            orders: 可选的订单列表，如果提供则直接使用，否则从qmt_adapter获取（用于避免重复查询）
            stored_order_id: 已知的委托编号/order_sysid，数量不一致时仍可匹配
        """
        try:
            # 如果没有提供订单列表，则获取当日订单列表
            if orders is None:
                if not hasattr(qmt_adapter, 'get_today_orders'):
                    return None
                orders = qmt_adapter.get_today_orders()
            
            if not orders:
                self.logger.debug(f"[{self.stock_code}] 订单列表为空，无法匹配")
                return None
            
            # 确定订单方向（买入或卖出）
            is_buy = rule_type in ('single_buy', 'breakthrough_buy', 'cage_buy', 'grid_buy', 'elastic_buy')
            
            # 在订单列表中查找匹配的订单
            # 匹配条件：股票代码、价格（允许0.01误差）、数量、方向、未成交状态
            # 注意：订单列表中使用的是 order.order_sysid，这是撤单的正确订单号
            self.logger.debug(f"[{self.stock_code}] 🔍 开始匹配订单: 价格={price}, 数量={volume}, 类型={'买入' if is_buy else '卖出'}, 订单总数={len(orders)}")
            
            for order in orders:
                try:
                    order_stock = getattr(order, 'stock_code', '')
                    order_price = getattr(order, 'price', 0)
                    # 使用order_volume而不是order_amount（与订单列表一致）
                    order_volume = getattr(order, 'order_volume', 0)
                    order_type = getattr(order, 'order_type', None)
                    order_status = getattr(order, 'order_status', None)
                    order_sysid = getattr(order, 'order_sysid', None)
                    
                    # 导入xtconstant用于判断订单类型
                    from xtquant import xtconstant
                    
                    # 检查股票代码
                    if order_stock != self.stock_code:
                        continue

                    order_sysid_str = str(order_sysid).strip() if order_sysid else ''
                    stored_id = str(stored_order_id or '').strip()
                    id_matched = bool(
                        stored_id and order_sysid_str and (
                            stored_id == order_sysid_str
                            or order_sysid_str.endswith(stored_id)
                            or stored_id.endswith(order_sysid_str)
                        )
                    )
                    
                    # 检查价格（允许0.01的误差）
                    price_diff = abs(order_price - price)
                    if price_diff > 0.01 and not id_matched:
                        self.logger.debug(f"[{self.stock_code}] 价格不匹配: 订单价格={order_price}, 规则价格={price}, 差异={price_diff:.3f}")
                        continue
                    
                    # 检查数量（允许少量误差，因为可能有部分成交）
                    volume_diff = abs(order_volume - volume)
                    if volume_diff > 0 and not id_matched:
                        self.logger.debug(f"[{self.stock_code}] 数量不匹配: 订单数量={order_volume}, 规则数量={volume}, 差异={volume_diff}")
                        continue
                    
                    # 检查订单方向
                    order_is_buy = (order_type == xtconstant.STOCK_BUY)
                    if order_is_buy != is_buy:
                        self.logger.debug(f"[{self.stock_code}] 方向不匹配: 订单方向={'买入' if order_is_buy else '卖出'}, 规则方向={'买入' if is_buy else '卖出'}")
                        continue
                    
                    # 检查订单状态（只匹配未成交的订单）
                    # order_status: 48=未报, 49=待报, 50=已报, 51=已报待撤, 52=部成待撤, 
                    #               53=部撤, 54=已撤, 55=部成, 56=已成, 57=废单
                    # 我们只匹配已报(50)和部成(55)状态的订单（可撤单状态）
                    if order_status not in (50, 55):
                        self.logger.debug(f"[{self.stock_code}] 状态不匹配: 订单状态={order_status} (需要50或55)")
                        continue
                    
                    # 找到匹配的订单，返回订单ID（使用order_sysid，这是订单列表中使用的撤单ID）
                    if order_sysid:
                        order_sysid_str = str(order_sysid)
                        self.logger.info(f"[{self.stock_code}] ✅ 通过订单信息匹配成功: 价格={price}, 数量={volume}, 类型={'买入' if is_buy else '卖出'}, 订单ID={order_sysid_str} (order_sysid), 状态={order_status}")
                        return order_sysid_str
                    else:
                        self.logger.debug(f"[{self.stock_code}] 订单缺少order_sysid: order={order}")
                    
                except Exception as e:
                    self.logger.debug(f"[{self.stock_code}] 匹配订单时出错: {str(e)}")
                    continue
            
            self.logger.debug(f"[{self.stock_code}] ❌ 未找到匹配的订单: 价格={price}, 数量={volume}, 类型={'买入' if is_buy else '卖出'}")
            return None
            
        except Exception as e:
            self.logger.warning(f"[{self.stock_code}] 通过订单信息匹配订单时出错: {str(e)}")
            return None
    
    def _update_early_order_id_from_callback(self, order_sysid, order_price, order_type):
        """从委托回报回调中更新提前下单的真实订单ID"""
        try:
            # 查找匹配的提前下单规则：有early_order标记、价格匹配、方向匹配
            # 注意：即使没有临时early_order_id（如下单返回-1），也可以通过价格和方向匹配
            matched_rules = []
            for rule in self.rules:
                rule_name = rule.get('name', '未命名规则')
                if not rule.get('early_order', False):
                    continue
                
                # 如果规则已经执行完成，不再更新订单ID（避免重复更新）
                if rule.get('executed', False):
                    continue
                
                existing_order_id = rule.get('early_order_id')
                
                # 如果已经有有效的order_sysid（不是-1或空），且与当前订单号相同，跳过（已匹配过）
                if existing_order_id and existing_order_id != '-1' and existing_order_id != '' and str(existing_order_id) == str(order_sysid):
                    continue  # 已经匹配过了，跳过
                
                # 检查规则类型是否匹配订单类型
                rule_type = rule.get('type')
                is_buy_rule = rule_type in ['single_buy', 'breakthrough_buy', 'cage_buy', 'grid_buy']
                if (order_type == 'buy' and not is_buy_rule) or (order_type == 'sell' and is_buy_rule):
                    continue
                
                # 检查价格是否匹配（允许0.01元的误差）
                rule_price = rule.get('early_order_price', rule.get('price', 0))
                price_matched = False
                
                if rule_type in ['cage_buy', 'cage_sell']:
                    price_low = rule.get('price_low', 0)
                    price_high = rule.get('price_high', 0)
                    # 笼子规则：价格匹配任意端点
                    if abs(order_price - price_low) <= 0.01 or abs(order_price - price_high) <= 0.01:
                        price_matched = True
                elif rule_type == 'grid_buy':
                    start_price = rule.get('start_price', 0)
                    if abs(order_price - start_price) <= 0.01:
                        price_matched = True
                elif rule_type == 'grid_sell':
                    end_price = rule.get('end_price', 0)
                    if abs(order_price - end_price) <= 0.01:
                        price_matched = True
                else:
                    # 单点规则：价格必须匹配
                    if abs(order_price - rule_price) <= 0.01:
                        price_matched = True
                
                if not price_matched:
                    continue
                
                # 价格匹配成功！现在检查是否需要更新订单ID
                # 如果已有订单ID且等于当前order_sysid，跳过
                if existing_order_id and str(existing_order_id) == str(order_sysid):
                    continue
                
                # 如果已有订单ID但与当前order_sysid不同，需要判断：
                # - 如果现有订单ID是临时订单号（长数字，通常是下单接口返回的委托编号），应该用真实的order_sysid覆盖
                # - 如果现有订单ID已经是真实的order_sysid（但不同），可能是订单已变更，也应该更新
                # 关键：只要价格匹配，就应该用回调中的真实order_sysid更新（因为这是撤单时需要的）
                temp_order_id = existing_order_id if existing_order_id else '未知'
                
                # 找到匹配的规则，更新为真实的order_sysid（即使已有临时订单号也要更新）
                rule['early_order_id'] = str(order_sysid)
                self.logger.info(f"[{self.stock_code}] ✅ [订单ID更新] 更新提前下单订单ID: {rule_name}, 临时ID={temp_order_id} -> 真实ID={order_sysid}, 价格={order_price}")
                
                # 保存规则状态
                self._save_rules()
                matched_rules.append(rule_name)
                # 找到匹配的就返回（但先记录所有匹配的规则）
        
            # 未找到匹配的规则（通常是历史订单回调，属于正常情况，不记录日志）
        except Exception as e:
            import traceback
            self.logger.error(f"[{self.stock_code}] ❌ [订单ID更新] _update_early_order_id_from_callback异常: {str(e)}\n{traceback.format_exc()}")

    def _early_order_rule_matches_callback(self, rule, order_sysid, order_price, order_type) -> bool:
        if not rule.get('early_order', False) and not self._early_order_id_is_valid(rule.get('early_order_id')):
            return False
        if rule.get('executed', False):
            return False

        rule_type = rule.get('type', '')
        is_buy_rule = rule_type in ['single_buy', 'breakthrough_buy', 'cage_buy', 'grid_buy']
        if (order_type == 'buy' and not is_buy_rule) or (order_type == 'sell' and is_buy_rule):
            return False

        stored_id = str(rule.get('early_order_id') or '').strip()
        order_sysid_str = str(order_sysid or '').strip()
        if stored_id and order_sysid_str and (
            stored_id == order_sysid_str
            or order_sysid_str.endswith(stored_id)
            or stored_id.endswith(order_sysid_str)
        ):
            return True

        rule_price = rule.get('early_order_price', rule.get('price', 0))
        submit_price = rule.get('early_order_submit_price', rule_price)
        price_matched = (
            abs(float(order_price) - float(rule_price)) <= 0.01
            or abs(float(order_price) - float(submit_price)) <= 0.01
        )
        return bool(price_matched)

    def _get_early_order_live_status(self, rule, qmt_adapter=None) -> str:
        """返回提前单柜台状态: live | filled | cancelled | missing | unknown"""
        qmt_adapter = qmt_adapter or self._get_qmt_adapter()
        if qmt_adapter and hasattr(qmt_adapter, 'get_today_orders'):
            status, _ = self._inspect_early_order_for_cancel(qmt_adapter, rule)
            if status == 'filled':
                return 'filled'
            if status == 'cancelable':
                return 'live'
            if status == 'cancelled':
                return 'cancelled'

        if self._rule_has_active_early_order(rule):
            return 'unknown'
        return 'missing'

    def _confirm_early_order_execution(self, rule, tick_data, current_price, order_id, volume=None) -> None:
        from datetime import datetime

        rule_type = rule.get('type')
        rule_name = rule.get('name', '未命名规则')
        exec_volume = int(volume or self._early_order_submitted_volume(rule) or rule.get('volume', 0) or 0)
        order_id = str(order_id or rule.get('early_order_id') or 'EARLY_ORDER_CONFIRMED')

        rule['executed'] = True
        rule['executed_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        rule['executed_price'] = current_price
        rule['executed_volume'] = exec_volume
        rule['order_id'] = order_id
        self._clear_early_order_state(rule)

        trade_info = {
            'type': 'buy' if rule_type in ['single_buy', 'breakthrough_buy', 'cage_buy', 'grid_buy'] else 'sell',
            'price': current_price,
            'volume': exec_volume,
            'reason': f'提前下单确认-{rule_name}',
            'early_order': True,
        }
        exec_time = tick_data.get('time', datetime.now()) if isinstance(tick_data, dict) else datetime.now()
        if not isinstance(exec_time, datetime):
            exec_time = datetime.now()
        self._record_execution(
            rule, trade_info, tick_data or {}, exec_time, current_price, exec_volume, order_id, False, 'auto',
            approval_time=None,
        )
        self._save_rules()
        self.logger.info(f"[{self.stock_code}] 提前下单任务已确认: {rule_name} (价格达到 {current_price:.2f}元)")
        self._play_trade_sound()
        if hasattr(self, 'canvas'):
            self.canvas.draw_idle()

    @staticmethod
    def _builtin_order_skip_reason(order_rec, order_id="") -> str:
        """大 QMT skipped 订单 → executed_reason（禁买/低于最小买入等非正常结束）。"""
        rec = order_rec or {}
        oid = str(order_id or rec.get("order_sysid") or "").strip()
        if (
            bool(rec.get("buy_block_window"))
            or oid == "SKIPPED_BUY_WINDOW"
            or str(rec.get("order_sysid") or "").strip() == "SKIPPED_BUY_WINDOW"
        ):
            return "buy_block_window"
        if (
            bool(rec.get("band_hard_pass"))
            or oid == "BAND_HARD_PASS"
            or str(rec.get("order_sysid") or "").strip() == "BAND_HARD_PASS"
            or str(rec.get("msg") or "").strip() == "band_hard_pass"
        ):
            return "band_hard_pass"
        if (
            str(rec.get("cash_block") or "") == "order_below_min"
            or oid == "SKIPPED_MIN_BUY"
            or str(rec.get("order_sysid") or "").strip() == "SKIPPED_MIN_BUY"
        ):
            return "order_below_min"
        return ""

    @staticmethod
    def _is_band_hard_pass_rule(rule) -> bool:
        r = rule or {}
        if str(r.get("executed_reason") or "") == "band_hard_pass":
            return True
        if str(r.get("order_id") or "") == "BAND_HARD_PASS":
            return True
        detail = str(
            r.get("executed_detail") or r.get("true_breakthrough_detail") or ""
        )
        return (
            "硬pass" in detail or "真突破放弃" in detail or "首次真突破放弃" in detail
        ) and (
            "有效下沿" in detail or "硬上沿" in detail or "买入参考价" in detail
        )

    @staticmethod
    def _band_hard_pass_kind_label(rule) -> str:
        """深位 / 上沿MA5，供节点与菜单文案。"""
        detail = str(
            (rule or {}).get("executed_detail")
            or (rule or {}).get("true_breakthrough_detail")
            or ""
        )
        if "硬上沿" in detail or "买入参考价" in detail:
            return "上沿MA5"
        if "有效下沿" in detail:
            return "深位"
        return "价格带"

    def _lookup_builtin_true_breakthrough_event(self, task_id: str = ""):
        """从 results.json 的 tb_pass/tb_fail 事件取真突破明细（大 QMT 路径专用）。"""
        tid = str(task_id or "").strip()
        code = str(getattr(self, "stock_code", "") or "").strip().upper()
        if not code:
            return None
        try:
            import json
            from pathlib import Path

            root = Path(__file__).resolve().parents[1]
            path = root / "data" / "results.json"
            if not path.is_file():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            stocks = (data or {}).get("stocks") or {}
            # 兼容 513180 / 513180.SH
            entry = stocks.get(code)
            if entry is None:
                code6 = code.split(".")[0]
                for k, v in stocks.items():
                    if str(k).split(".")[0] == code6:
                        entry = v
                        break
            if not isinstance(entry, dict):
                return None
            events = entry.get("events") or []
            best = None
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                et = str(ev.get("type") or "")
                if et not in ("tb_pass", "tb_fail"):
                    continue
                ev_tid = str(ev.get("task_id") or "").strip()
                if tid and ev_tid and ev_tid != tid and not ev_tid.endswith(":" + tid.split(":")[-1]):
                    # task_id 不完全一致时，允许同 rule_id 后缀匹配
                    rid = tid.split(":")[-1]
                    if rid and not ev_tid.endswith(rid):
                        continue
                best = ev
            return best
        except Exception:
            return None

    def _apply_true_breakthrough_from_builtin_event(self, rule, task_id: str = "", order_rec=None) -> bool:
        """把大 QMT 真突破明细写回规则，供右键菜单显示。"""
        if not isinstance(rule, dict):
            return False
        order_rec = order_rec or {}
        detail = str(
            order_rec.get("true_breakthrough_detail")
            or order_rec.get("detail")
            or rule.get("true_breakthrough_detail")
            or ""
        ).strip()
        passed = order_rec.get("true_breakthrough_passed")
        if passed is None and str(order_rec.get("event_type") or "") == "tb_pass":
            passed = True
        if passed is None and str(order_rec.get("event_type") or "") == "tb_fail":
            passed = False

        if not detail or passed is None:
            ev = self._lookup_builtin_true_breakthrough_event(task_id)
            if isinstance(ev, dict):
                if not detail:
                    detail = str(ev.get("detail") or ev.get("msg") or "").strip()
                if passed is None:
                    et = str(ev.get("type") or "")
                    if et == "tb_pass":
                        passed = True
                    elif et == "tb_fail":
                        passed = False
                    else:
                        metrics = ev.get("metrics") or {}
                        if isinstance(metrics, dict) and "passed" in metrics:
                            passed = bool(metrics.get("passed"))

        changed = False
        if detail and str(rule.get("true_breakthrough_detail") or "").strip() != detail:
            rule["true_breakthrough_detail"] = detail
            rule["executed_detail"] = detail
            changed = True

        # 价格带硬pass：tb_fail + msg/detail 含 band_hard_pass / 硬上沿 / 有效下沿硬pass
        is_band_hp = False
        msg = str(order_rec.get("msg") or order_rec.get("event_msg") or "").strip()
        if msg == "band_hard_pass":
            is_band_hp = True
        elif "band_hard_pass" in detail or (
            "硬pass" in detail
            and ("有效下沿" in detail or "硬上沿" in detail or "买入参考价" in detail)
        ):
            is_band_hp = True
        if not is_band_hp:
            try:
                ev = self._lookup_builtin_true_breakthrough_event(task_id)
                if isinstance(ev, dict) and str(ev.get("msg") or "") == "band_hard_pass":
                    is_band_hp = True
                    if not detail:
                        detail = str(ev.get("detail") or "").strip()
                        if detail:
                            rule["true_breakthrough_detail"] = detail
                            rule["executed_detail"] = detail
                            changed = True
            except Exception:
                pass
        if is_band_hp:
            if rule.get("executed_reason") != "band_hard_pass":
                rule["executed_reason"] = "band_hard_pass"
                changed = True
            if str(rule.get("order_id") or "") != "BAND_HARD_PASS":
                rule["order_id"] = "BAND_HARD_PASS"
                changed = True
            # 真突破条件已过，但因硬pass未下单
            if rule.get("true_breakthrough_passed") is not True:
                rule["true_breakthrough_passed"] = True
                changed = True
            return changed

        if passed is True and rule.get("true_breakthrough_passed") is not True:
            rule["true_breakthrough_passed"] = True
            changed = True
        if passed is False and rule.get("true_breakthrough_passed") is not False:
            # 非真突破结束时通常已有 executed_reason；这里仅补明细
            if not rule.get("executed_reason"):
                rule["executed_reason"] = "not_true_breakthrough"
            changed = True
        return changed

    def _breakthrough_buy_may_trigger_immediately(self, rule, current_price=None):
        """启动/启用时突破买入是否可能立刻下单。

        - MA5 价格带：只在监控带 [band_low, band_high] 内评估；现价在带外
          （含已高于上沿/突破价）不会立即触发，不应弹窗。
        - 普通突破：现价已高于触发价时，首 tick 无前价也可能按上穿处理，仍提示。
        返回 (will_trigger, reason)。
        """
        lp = float(
            current_price
            if current_price is not None
            else (getattr(self, "current_price", 0) or 0)
        )
        if lp <= 0:
            return False, ""
        r = rule or {}
        try:
            trig = float(r.get("price") or 0)
        except (TypeError, ValueError):
            trig = 0.0
        try:
            band_lo = float(r.get("band_low") or 0)
            band_hi = float(r.get("band_high") or 0)
        except (TypeError, ValueError):
            band_lo, band_hi = 0.0, 0.0

        if band_lo > 0 and band_hi >= band_lo:
            if band_lo <= lp <= band_hi:
                return (
                    True,
                    f"当前价({lp:.2f}元)在监控带[{band_lo:.2f},{band_hi:.2f}]内，可能立即判定真突破",
                )
            # 现价在带外：等待回落进带，启动瞬间不会触发
            return False, ""

        if trig > 0 and lp > trig:
            return (
                True,
                f"突破买入价({trig:.2f}元) < 当前价({lp:.2f}元)",
            )
        return False, ""

    def apply_builtin_order_feedback(self, task_id, order_rec=None, order_id=""):
        """大 QMT 内置 passorder 回报：按 task_id 精确匹配规则并标记已执行。"""
        from datetime import datetime

        order_rec = order_rec or {}
        tid = str(task_id or "").strip()
        rule_id = tid.split(":")[-1].strip() if tid else ""
        if not rule_id:
            return False

        # 回写前先与 TaskManager 对齐，避免图表规则过期导致误停任务 / 保存时冲掉其它规则
        try:
            self._sync_rules_from_task_manager()
        except Exception:
            pass

        rule = None
        for r in self.rules or []:
            if not isinstance(r, dict):
                continue
            rid = str(r.get("id") or "").strip()
            if rid and rid == rule_id:
                rule = r
                break
        if rule is None:
            # 精确匹配失败时，不回退到「随便一条未执行规则」，避免误标新节点
            if self.logger:
                self.logger.warning(
                    f"[{self.stock_code}] [builtin] 未找到规则 id={rule_id}，跳过回写"
                )
            return False
        rtype = str(rule.get("type") or "")
        if rtype == "scheduled_clear":
            if rule.get("scheduled_clear_executed"):
                return True
        elif rule.get("executed"):
            # 已执行时仍补真突破明细（此前大 QMT 回写漏写，导致右键看不见）
            if self._apply_true_breakthrough_from_builtin_event(rule, tid, order_rec):
                try:
                    self._save_rules()
                    self.update_chart()
                except Exception:
                    pass
            return True

        # early_confirm / 提前单回写时清提前标志
        if str(order_rec.get("event_type") or "") in ("early_confirm",) or (
            bool(order_rec.get("early_order"))
            and str(order_rec.get("status") or "").lower() == "filled"
        ):
            self._clear_early_order_state(rule)

        px = float(order_rec.get("price") or rule.get("price") or self.current_price or 0)
        vol = int(order_rec.get("volume") or rule.get("volume") or 0)
        oid = str(order_id or order_rec.get("user_order_id") or tid or "PO_BUILTIN")
        at = str(order_rec.get("at") or "")
        try:
            exec_time = (
                datetime.strptime(at[:19], "%Y-%m-%dT%H:%M:%S") if at else datetime.now()
            )
        except Exception:
            exec_time = datetime.now()

        # 定时清仓：标记 scheduled_clear_executed（图表用此字段渲染）
        if rtype == "scheduled_clear":
            st = str(order_rec.get("status") or "").strip().lower()
            attempted = st not in ("skipped", "")
            rule["scheduled_clear_executed"] = True
            rule["scheduled_clear_order_attempted"] = attempted
            rule["pending_tick_execution"] = False
            rule["executed_time"] = exec_time.strftime("%Y-%m-%d %H:%M:%S")
            rule["executed_price"] = px
            rule["executed_volume"] = vol
            if attempted:
                rule["order_id"] = oid
            rule.pop("executed_reason", None)
            try:
                from core.execution_record_manager import ExecutionRecordManager

                ExecutionRecordManager().record_from_builtin_order(
                    order_rec,
                    order_id=oid,
                    stock_name=str(getattr(self, "stock_name", "") or ""),
                    rule=rule,
                )
            except Exception:
                pass
            self._save_rules()
            self.update_chart()
            if self.logger:
                self.logger.info(
                    f"[{self.stock_code}] [builtin] 定时清仓已回写: "
                    f"{rule.get('name')} attempted={attempted} oid={oid}"
                )
            if attempted:
                try:
                    self._play_trade_sound()
                except Exception:
                    pass
            return True

        # 网格：只标记当前点位，全部点位完成才 rule.executed=True
        if rtype in ("grid_buy", "grid_sell"):
            gi = order_rec.get("grid_index")
            try:
                gi = int(gi) if gi is not None else None
            except (TypeError, ValueError):
                gi = None
            if gi is None:
                if self.logger:
                    self.logger.warning(
                        f"[{self.stock_code}] [builtin] 网格回写缺少 grid_index，跳过"
                    )
                return False
            if "executed_grids" not in rule or not isinstance(rule.get("executed_grids"), list):
                rule["executed_grids"] = []
            if gi not in rule["executed_grids"]:
                rule["executed_grids"].append(gi)
            if "executed_grid_prices" not in rule or not isinstance(
                rule.get("executed_grid_prices"), dict
            ):
                rule["executed_grid_prices"] = {}
            if "executed_grid_volumes" not in rule or not isinstance(
                rule.get("executed_grid_volumes"), dict
            ):
                rule["executed_grid_volumes"] = {}
            rule["executed_grid_prices"][str(gi)] = px
            rule["executed_grid_volumes"][str(gi)] = vol
            rule["order_id"] = oid
            num_grids = int(rule.get("num_grids") or 2)
            all_done = len(set(int(x) for x in rule["executed_grids"])) >= num_grids + 1
            if all_done:
                rule["executed"] = True
                rule["executed_time"] = exec_time.strftime("%Y-%m-%d %H:%M:%S")
                rule["executed_price"] = px
                rule["executed_volume"] = vol
                rule.pop("executed_reason", None)
            trade_info = {
                "type": "buy" if "buy" in rtype else "sell",
                "price": px,
                "volume": vol,
                "reason": str(order_rec.get("strategy_name") or "蚂蚁-内置下单"),
                "is_real_order": True,
                "grid_index": gi,
            }
            try:
                from core.execution_record_manager import ExecutionRecordManager

                ExecutionRecordManager().record_from_builtin_order(
                    order_rec,
                    order_id=oid,
                    stock_name=str(getattr(self, "stock_name", "") or ""),
                    rule=rule,
                )
            except Exception:
                pass
            try:
                from utils.position_entry_dates import note_fill_from_order

                note_fill_from_order(
                    stock_code=getattr(self, "stock_code", "") or "",
                    rule=rule,
                    order_rec=order_rec,
                    skip_reason="",
                )
            except Exception:
                pass
            try:
                from utils.filled_legs import note_from_rule_fill

                note_from_rule_fill(
                    stock_code=getattr(self, "stock_code", "") or "",
                    rule=rule,
                    order_rec=order_rec,
                )
            except Exception:
                pass
            self._save_rules()
            self.update_chart()
            if self.logger:
                self.logger.info(
                    f"[{self.stock_code}] [builtin] 网格点位已回写: "
                    f"{rule.get('name')} g={gi} done={all_done} oid={oid}"
                )
            try:
                self._play_trade_sound()
            except Exception:
                pass
            # 大 QMT 回写后不再因「看似无剩余规则」自动停任务（易误判，且卖出后仍要盯盘）
            self._log_builtin_remaining_after_feedback(rule_id)
            return True

        rule["executed"] = True
        rule["executed_time"] = exec_time.strftime("%Y-%m-%d %H:%M:%S")
        rule["executed_price"] = px
        rule["executed_volume"] = vol
        rule["order_id"] = oid
        skip_reason = self._builtin_order_skip_reason(order_rec, oid)
        if skip_reason:
            rule["executed_reason"] = skip_reason
            if skip_reason == "buy_block_window":
                rule["order_id"] = "SKIPPED_BUY_WINDOW"
            elif skip_reason == "order_below_min":
                rule["order_id"] = "SKIPPED_MIN_BUY"
            elif skip_reason == "band_hard_pass":
                rule["order_id"] = "BAND_HARD_PASS"
                rule["executed_volume"] = 0
                detail = str(
                    order_rec.get("detail")
                    or order_rec.get("true_breakthrough_detail")
                    or order_rec.get("msg")
                    or ""
                ).strip()
                if detail:
                    rule["executed_detail"] = detail
        else:
            rule.pop("executed_reason", None)
        # 真突破明细：从 results 事件 / order_rec 写回，供右键显示
        self._apply_true_breakthrough_from_builtin_event(rule, tid, order_rec)
        if rtype in ("night_buy", "night_sell"):
            rule["night_market_pending"] = False
            rule["night_market_order_id"] = oid
        ep = str(order_rec.get("executed_endpoint") or "").strip()
        if ep in ("low", "high"):
            rule["executed_endpoint"] = ep

        # 实盘建仓日：首次买入成交写入；卖出后无仓则清除
        try:
            from utils.position_entry_dates import note_fill_from_order

            note_fill_from_order(
                stock_code=getattr(self, "stock_code", "") or "",
                rule=rule,
                order_rec=order_rec,
                skip_reason=str(skip_reason or ""),
            )
        except Exception:
            pass
        if not skip_reason:
            try:
                from utils.filled_legs import note_from_rule_fill

                note_from_rule_fill(
                    stock_code=getattr(self, "stock_code", "") or "",
                    rule=rule,
                    order_rec=order_rec,
                )
            except Exception:
                pass

        trade_info = {
            "type": "buy" if "buy" in rtype else "sell",
            "price": px,
            "volume": vol,
            "reason": str(order_rec.get("strategy_name") or "蚂蚁-内置下单"),
            "is_real_order": True,
            "true_breakthrough_detail": rule.get("true_breakthrough_detail"),
            "true_breakthrough_passed": rule.get("true_breakthrough_passed"),
        }
        try:
            from core.execution_record_manager import ExecutionRecordManager

            # 与 builtin_price_feed 共用去重键；feed 已写过则这里跳过
            ExecutionRecordManager().record_from_builtin_order(
                order_rec,
                order_id=oid,
                stock_name=str(getattr(self, "stock_name", "") or ""),
                rule=rule,
            )
        except Exception:
            try:
                self._record_execution(
                    rule,
                    trade_info,
                    {},
                    exec_time,
                    px,
                    vol,
                    oid,
                    False,
                    "auto",
                    approval_time=None,
                )
            except Exception:
                pass
        self._save_rules()
        self.update_chart()
        if self.logger:
            self.logger.info(
                f"[{self.stock_code}] [builtin] 规则已回写为已执行: "
                f"{rule.get('name')} id={rule_id} oid={oid} px={px} vol={vol}"
            )
        try:
            self._play_trade_sound()
        except Exception:
            pass

        self._log_builtin_remaining_after_feedback(rule_id)
        return True

    def _rule_still_pending_trade(self, rule) -> bool:
        """规则是否仍有待执行的交易工作（用于剩余规则统计）。"""
        if not isinstance(rule, dict):
            return False
        if rule.get("enabled", True) is False:
            # 延迟激活未决：仍算待办，避免买入后误停
            try:
                from core.rule_activation import has_activation_config

                act = rule.get("activation") or {}
                if has_activation_config(rule) and not act.get("resolved", False):
                    return True
            except Exception:
                pass
            return False
        rtype = str(rule.get("type") or "")
        if rtype == "scheduled_clear":
            return not bool(rule.get("scheduled_clear_executed"))
        if rtype in ("night_buy", "night_sell"):
            return not bool(rule.get("executed"))
        if rtype in ("grid_buy", "grid_sell"):
            if rule.get("executed"):
                return False
            try:
                num_grids = int(rule.get("num_grids") or 2)
            except (TypeError, ValueError):
                num_grids = 2
            done = rule.get("executed_grids") or []
            try:
                n_done = len({int(x) for x in done})
            except (TypeError, ValueError):
                n_done = len(done) if isinstance(done, list) else 0
            return n_done < num_grids + 1
        if rtype in (
            "single_buy",
            "breakthrough_buy",
            "single_sell",
            "breakthrough_sell",
            "cage_buy",
            "cage_sell",
            "best_buy",
            "best_sell",
        ):
            return not bool(rule.get("executed"))
        return False

    def _collect_remaining_trade_rules(self):
        try:
            self._sync_rules_from_task_manager()
        except Exception:
            pass
        out = []
        for r in self.rules or []:
            if self._rule_still_pending_trade(r):
                out.append(r)
        return out

    def _log_builtin_remaining_after_feedback(self, just_rule_id: str = "") -> None:
        """builtin 回写后只记日志，不再自动 stop_task（卖出后误变未运行）。"""
        remaining = self._collect_remaining_trade_rules()
        if self.logger:
            names = [str(r.get("name") or r.get("id") or "") for r in remaining[:8]]
            self.logger.info(
                f"[{self.stock_code}] [builtin] 回写后剩余待执行规则 "
                f"{len(remaining)} 个 (刚完成={just_rule_id or '-'}): {names}"
            )

    def apply_builtin_early_state(self, task_id, state):
        """大 QMT early_states → 规则显示提前挂单中。"""
        tid = str(task_id or "").strip()
        rule_id = tid.split(":")[-1].strip() if tid else ""
        if not rule_id:
            return False
        st = state or {}
        if not bool(st.get("active")):
            return False
        for r in self.rules or []:
            if not isinstance(r, dict):
                continue
            if str(r.get("id") or "") != rule_id:
                continue
            if r.get("executed"):
                return True
            uid = str(st.get("user_order_id") or "").strip() or "BUILTIN_EARLY"
            price = float(st.get("price") or r.get("price") or 0)
            vol = int(st.get("volume") or 0)
            changed = (
                not r.get("early_order")
                or str(r.get("early_order_id") or "") != uid
                or float(r.get("early_order_price") or 0) != price
            )
            r["early_order"] = True
            r["early_order_id"] = uid  # 图表黄色节点依赖此字段
            r["early_order_price"] = price
            if vol > 0:
                r["early_order_submit_volume"] = vol
            if changed:
                try:
                    self._save_rules()
                    self.update_chart()
                except Exception:
                    pass
            return True
        return False

    def apply_builtin_elastic_state(self, task_id, state):
        """大 QMT 弹性跟踪状态：突破后节点变红、动态回落/反弹线。"""
        tid = str(task_id or "").strip()
        rule_id = tid.split(":")[-1].strip() if tid else ""
        if not rule_id:
            return False
        st = state or {}
        if not bool(st.get("triggered")):
            return False
        rule = None
        for r in self.rules or []:
            if isinstance(r, dict) and str(r.get("id") or "") == rule_id:
                rule = r
                break
        if rule is None or rule.get("executed"):
            return False
        rtype = str(rule.get("type") or "")
        if rtype not in ("best_sell", "best_buy"):
            return False
        try:
            from brokers.builtin_price_feed import BuiltinPricePoller

            changed = BuiltinPricePoller._merge_elastic_into_rule(rule, st, rtype)
        except Exception:
            changed = False
            if not rule.get("triggered"):
                rule["triggered"] = True
                changed = True
            if rtype == "best_sell" and st.get("highest_price") is not None:
                try:
                    rule["highest_price"] = float(st.get("highest_price"))
                    changed = True
                except (TypeError, ValueError):
                    pass
            if rtype == "best_buy" and st.get("lowest_price") is not None:
                try:
                    rule["lowest_price"] = float(st.get("lowest_price"))
                    changed = True
                except (TypeError, ValueError):
                    pass
        if not changed:
            return False
        try:
            self._save_rules()
        except Exception:
            pass
        try:
            self.update_chart()
        except Exception:
            pass
        return True

    def handle_early_order_status_from_callback(self, order_sysid, order_status, order_price, order_type) -> bool:
        """委托回报：提前单撤单/成交后同步规则状态。"""
        from PyQt5.QtCore import QTimer
        from datetime import datetime

        handled = False
        for rule in getattr(self, 'rules', []) or []:
            if not self._early_order_rule_matches_callback(rule, order_sysid, order_price, order_type):
                continue

            rule_name = rule.get('name', '未命名规则')
            if order_status == 54:
                if not rule.get('early_order', False) and not self._early_order_id_is_valid(rule.get('early_order_id')):
                    continue
                # 订单列表人工撤单 → 结束为黑节点；系统自动撤单 → 复位等待再挂
                if rule.get('early_manual_cancel_pending'):
                    self._finalize_early_order_manual_cancelled(rule, order_sysid)
                else:
                    self._clear_early_order_state(rule)
                    self._save_rules()
                    self.logger.info(
                        f"[{self.stock_code}] ✅ 提前下单已撤单，状态已复位: {rule_name} (订单ID: {order_sysid})"
                    )
                    QTimer.singleShot(100, self.update_chart)
                handled = True
            elif order_status == 56:
                if rule.get('executed', False):
                    handled = True
                    continue
                tick_data = {
                    'stock_code': self.stock_code,
                    'lastPrice': float(self.current_price or order_price or 0),
                    'time': datetime.now(),
                }
                self._confirm_early_order_execution(
                    rule,
                    tick_data,
                    float(self.current_price or order_price or 0),
                    order_sysid,
                )
                handled = True
            elif order_status in (53, 57):
                self._clear_early_order_state(rule)
                self._save_rules()
                self.logger.info(
                    f"[{self.stock_code}] 提前下单委托已结束({order_status})，状态已复位: {rule_name}"
                )
                QTimer.singleShot(100, self.update_chart)
                handled = True
        return handled
    
    def _update_night_market_rule_from_order(self, order_sysid, order_status, order_price, order_type):
        """根据订单回报更新夜市委托规则状态"""
        try:
            # 查找匹配的夜市委托规则
            for rule in self.rules:
                rule_type = rule.get('type')
                if rule_type not in ['night_buy', 'night_sell']:
                    continue
                
                # 如果规则已经执行完成，跳过
                if rule.get('executed', False):
                    continue
                
                # 检查规则类型是否匹配订单类型
                is_buy_rule = rule_type == 'night_buy'
                if (order_type == 'buy' and not is_buy_rule) or (order_type == 'sell' and is_buy_rule):
                    continue
                
                # 检查价格是否匹配（允许0.01元的误差）
                rule_price = rule.get('price', 0)
                if abs(order_price - rule_price) > 0.01:
                    continue
                
                # 检查数量是否匹配（允许100股的误差，因为可能部分成交）
                rule_volume = rule.get('volume', 0)
                order_volume = rule.get('order_volume', 0)  # 注意：这里需要从订单回报中获取数量
                # 由于order_volume不在参数中，我们通过价格和类型匹配即可
                # 夜市委托通常价格和类型匹配就足够了
                
                # 匹配条件：
                # 1. 订单ID直接匹配（如果保存了night_market_order_id）
                # 2. 或者规则处于pending状态，且价格、类型匹配（订单ID可能不同，因为同步下单返回的订单号和order_sysid可能不同）
                night_market_order_id = rule.get('night_market_order_id')
                is_pending = rule.get('night_market_pending', False)
                
                order_id_matched = False
                if night_market_order_id and str(night_market_order_id) == str(order_sysid):
                    order_id_matched = True
                elif is_pending:
                    # 如果规则处于pending状态，且价格、类型匹配，也认为是匹配的
                    # 因为同步下单返回的订单号和订单回报中的order_sysid可能不同
                    order_id_matched = True
                
                if not order_id_matched:
                    continue
                
                # 找到匹配的规则，根据订单状态更新
                rule_name = rule.get('name', '未命名规则')
                
                # order_status: 48=未报, 49=待报, 50=已报, 51=已报待撤, 52=部成待撤, 
                #               53=部撤, 54=已撤, 55=部成, 56=已成, 57=废单
                if order_status == 50:  # 已报
                    # 订单已报，标记规则为已执行，停止定时器
                    rule['executed'] = True
                    rule['executed_price'] = order_price
                    rule['executed_volume'] = rule.get('volume', 0)
                    rule['order_id'] = str(order_sysid)
                    from datetime import datetime
                    rule['executed_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    rule['night_market_pending'] = False
                    # 更新night_market_order_id为真实的order_sysid
                    rule['night_market_order_id'] = str(order_sysid)
                    self._save_rules()
                    self.logger.info(f"[{self.stock_code}] ✅ 夜市委托订单已报，标记规则为已执行: {rule_name} (订单号: {order_sysid})")
                    
                    # 检查是否所有夜市规则都已完成
                    self._check_night_market_rules_completed()
                elif order_status == 57:  # 废单
                    # 废单：清除pending标记，继续高频下单
                    rule['night_market_pending'] = False
                    rule['night_market_order_id'] = None  # 清除订单ID，允许重新下单
                    self.logger.info(f"[{self.stock_code}] ⚠️ 夜市委托订单废单，继续重试: {rule_name} (订单号: {order_sysid})")
                    # 不保存规则状态，因为规则还在执行中
                else:
                    # 其他状态：保持pending，等待最终状态
                    self.logger.debug(f"[{self.stock_code}] 夜市委托订单状态: {rule_name} (订单号: {order_sysid}, 状态: {order_status})")
                
                # 只处理第一个匹配的规则
                break
                
        except Exception as e:
            import traceback
            self.logger.error(f"[{self.stock_code}] ❌ [夜市委托订单回报] 更新规则状态异常: {str(e)}\n{traceback.format_exc()}")
    
    def _check_night_market_rules_completed(self):
        """检查所有夜市规则是否已完成"""
        try:
            # 检查是否所有夜市规则都已完成
            all_completed = True
            for rule in self.night_market_rules:
                if rule.get('enabled', True) and not rule.get('executed', False):
                    all_completed = False
                    break
            
            if all_completed:
                self.logger.info(f"[{self.stock_code}] 所有夜市规则已完成，停止定时器")
                self._stop_night_market_timer()
        except Exception as e:
            self.logger.error(f"[{self.stock_code}] 检查夜市规则完成状态失败: {str(e)}")
    
    def _toggle_task(self):
        """切换任务运行状态（合并启动/暂停功能）"""
        if self.task_running and not self.task_paused:
            # 运行中 -> 暂停
            self.pause_task()
        elif self.task_paused:
            # 已暂停 -> 继续运行
            self.start_task()
        else:
            # 未运行 -> 启动
            self.start_task()
    
    def _save_task_status(self):
        """保存任务运行状态到文件"""
        if hasattr(self, 'task') and hasattr(self, 'task_manager') and self.task:
            if 'params' not in self.task:
                self.task['params'] = {}
            
            # 保存运行状态
            self.task['params']['task_running'] = self.task_running
            self.task['params']['task_paused'] = self.task_paused
            
            # 保存任务
            if self.task_manager:
                all_tasks = list(self.task_manager.tasks.values())
                self.task_manager.save_tasks(all_tasks)
    
    def _save_rules(self):
        """保存规则到任务"""
        if hasattr(self, 'task') and hasattr(self, 'task_manager') and self.task:
            # 旧规则缺 early_order_enabled 时一次性迁移（不覆盖已有快照）
            for r in self.rules or []:
                self._stamp_early_order_flag(r, force=False)
                self._stamp_breakthrough_flags(r, force=False)
            if 'params' not in self.task:
                self.task['params'] = {}
            # 与 TaskManager 按 id 合并，避免图表侧规则不完整时冲掉其它规则
            try:
                tid = str(self.task.get("task_id") or getattr(self, "task_id", "") or "")
                fresh = self.task_manager.tasks.get(tid) if tid else None
                tm_rules = None
                if isinstance(fresh, dict) and isinstance(fresh.get("params"), dict):
                    tm_rules = fresh["params"].get("rules")
                if isinstance(tm_rules, list) and tm_rules:
                    chart_by_id = {}
                    for r in self.rules or []:
                        if isinstance(r, dict) and r.get("id"):
                            chart_by_id[str(r.get("id"))] = r
                    if chart_by_id:
                        merged = []
                        seen = set()
                        for r in tm_rules:
                            if not isinstance(r, dict):
                                continue
                            rid = str(r.get("id") or "")
                            if rid and rid in chart_by_id:
                                merged.append(chart_by_id[rid])
                                seen.add(rid)
                            else:
                                merged.append(r)
                        for r in self.rules or []:
                            if not isinstance(r, dict):
                                continue
                            rid = str(r.get("id") or "")
                            if rid and rid not in seen:
                                merged.append(r)
                                seen.add(rid)
                        self.rules = merged
            except Exception:
                pass
            self.task['params']['rules'] = self.rules
            
            # 保存任务
            if self.task_manager:
                all_tasks = list(self.task_manager.tasks.values())
                self.task_manager.save_tasks(all_tasks)
    
    def _record_execution(self, rule, trade_info, tick_data, exec_time, price, volume, order_id, require_manual_approval, approval_result, approval_time=None):
        """记录执行记录
        
        Args:
            rule: 规则字典
            trade_info: 交易信息字典
            tick_data: tick数据
            exec_time: 执行时间
            price: 交易价格
            volume: 交易数量
            order_id: 订单号
            require_manual_approval: 是否需要人工审核
            approval_result: 审核结果 (approved/rejected/cancelled)
            approval_time: 审核时间（可选）
        """
        try:
            from core.execution_record_manager import ExecutionRecordManager
            from datetime import datetime
            from core.trading_rules import RULE_TYPE_NAMES, RuleType
            
            # 初始化执行记录管理器
            if not hasattr(self, '_execution_record_manager'):
                self._execution_record_manager = ExecutionRecordManager()
            
            # 获取规则信息
            rule_type = rule.get('type', '')
            rule_name = rule.get('name', '未命名规则')
            # 处理特殊规则类型（可能不在RULE_TYPE_NAMES中或转换失败）
            if rule_type == 'night_buy':
                rule_type_cn = '夜市买入'
            elif rule_type == 'night_sell':
                rule_type_cn = '夜市卖出'
            elif rule_type == 'scheduled_clear':
                rule_type_cn = '定时清仓'
            else:
                try:
                    rule_type_cn = RULE_TYPE_NAMES.get(RuleType(rule_type), rule_type) if rule_type else '未知类型'
                except (ValueError, AttributeError):
                    # 如果转换失败，使用规则类型本身，如果为空则显示"未知类型"
                    rule_type_cn = rule_type if rule_type else '未知类型'
            
            # 获取当前价格
            current_price = tick_data.get('price', self.current_price) if tick_data else self.current_price
            
            # 格式化执行时间
            if isinstance(exec_time, datetime):
                exec_time_str = exec_time.strftime('%Y-%m-%d %H:%M:%S')
            elif exec_time:
                exec_time_str = str(exec_time)
            else:
                exec_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                exec_time = datetime.now()
            
            # 格式化审核时间
            if approval_time is None:
                approval_time = exec_time
            if isinstance(approval_time, datetime):
                approval_time_str = approval_time.strftime('%Y-%m-%d %H:%M:%S')
            else:
                approval_time_str = str(approval_time)
            
            # 格式化规则详情
            rule_detail = self._execution_record_manager.format_rule_detail(rule, rule_type, trade_info)
            tb_detail = (trade_info or {}).get("true_breakthrough_detail")
            if tb_detail:
                rule_detail = f"{rule_detail} | 真突破: {tb_detail}"
            skip_reason = (trade_info or {}).get("skip_reason")
            if skip_reason:
                rule_detail = f"{rule_detail} | {skip_reason}"
            execution_outcome = (trade_info or {}).get("execution_outcome")
            if not execution_outcome:
                skip_ids = {
                    "NOT_TRUE_BREAKTHROUGH",
                    "BAND_HARD_PASS",
                    "SKIPPED_BUY_WINDOW",
                    "NO_CASH",
                    "MIN_BUY_AMOUNT",
                    "NO_POSITION",
                    "CANCELLED",
                    "PROBE_REMAIN_SKIPPED",
                }
                if (
                    str(order_id) == "ORDER_FAILED"
                    or approval_result == "order_failed"
                ):
                    execution_outcome = "order_failed"
                elif str(order_id) in skip_ids or approval_result in (
                    "skipped",
                    "not_true_breakthrough",
                    "band_hard_pass",
                    "buy_block_window",
                    "no_cash",
                    "min_buy_amount",
                    "no_position",
                    "rejected",
                    "probe_remain_skipped",
                ):
                    execution_outcome = "skipped"
                else:
                    execution_outcome = "ordered"
            
            # 构建执行记录
            record = {
                'execution_time': exec_time_str,
                'stock_code': self.stock_code,
                'stock_name': self.stock_name,
                'rule_type': rule_type,
                'rule_type_cn': rule_type_cn,
                'rule_name': rule_name,
                'rule_detail': rule_detail,
                'true_breakthrough_detail': tb_detail,
                'true_breakthrough_passed': (trade_info or {}).get("true_breakthrough_passed"),
                'skip_reason': skip_reason,
                'execution_outcome': execution_outcome,
                'current_price': current_price,
                'trade_price': price,
                'trade_volume': volume,
                'order_id': str(order_id),
                'require_manual_approval': require_manual_approval,
                'approval_result': approval_result,  # approved/rejected/cancelled/auto/skipped/...
                'approval_time': approval_time_str if require_manual_approval else None,
            }
            
            # 添加执行记录
            self._execution_record_manager.add_execution_record(record)
            
        except Exception as e:
            self.logger.error(f"记录执行记录失败: {str(e)}", exc_info=True)

    def _exec_time_from_tick(self, tick_data):
        from datetime import datetime

        if isinstance(tick_data, dict):
            exec_time = tick_data.get("time")
            if isinstance(exec_time, datetime):
                return exec_time
        return datetime.now()

    def _record_skipped_execution(
        self,
        rule,
        trade_info,
        tick_data,
        price,
        volume,
        order_id,
        skip_reason,
        approval_result="skipped",
        executed_reason=None,
    ):
        """规则已结束/触发但未实际下单时写入执行记录，便于复盘。"""
        if executed_reason:
            rule["executed_reason"] = executed_reason
        info = dict(trade_info or {})
        info["skip_reason"] = str(skip_reason or "").strip()
        info["execution_outcome"] = "skipped"
        self._record_execution(
            rule,
            info,
            tick_data,
            self._exec_time_from_tick(tick_data),
            price,
            volume,
            order_id,
            False,
            approval_result,
        )

    def _record_order_failed_execution(
        self,
        rule,
        trade_info,
        tick_data,
        price,
        volume,
        detail_msg,
        raw_order_id=None,
    ):
        """下单接口失败：如实记录，规则标记为已执行，不再重复触发。"""
        exec_time = self._exec_time_from_tick(tick_data)
        rule["executed"] = True
        rule["executed_time"] = exec_time.strftime("%Y-%m-%d %H:%M:%S")
        rule["executed_price"] = price
        rule["executed_volume"] = 0
        rule["order_id"] = "ORDER_FAILED"
        rule["executed_reason"] = "order_failed"
        self._save_rules()

        info = dict(trade_info or {})
        info["skip_reason"] = str(detail_msg or "").strip()
        info["execution_outcome"] = "order_failed"
        oid_repr = raw_order_id
        if oid_repr is not None and str(oid_repr).strip() not in ("", "None"):
            info["skip_reason"] = (
                f"{info['skip_reason']}（接口返回订单号={oid_repr}）"
                if info["skip_reason"]
                else f"接口返回订单号={oid_repr}"
            )
        self._record_execution(
            rule,
            info,
            tick_data,
            exec_time,
            price,
            volume,
            "ORDER_FAILED",
            False,
            "order_failed",
        )
        self.update_chart()
    
    def _show_rule_context_menu(self, rule, event):
        """显示规则的右键菜单"""
        from PyQt5.QtWidgets import QMenu, QMessageBox, QInputDialog
        from PyQt5.QtGui import QCursor
        from core.trading_rules import RULE_TYPE_NAMES, RuleType
        
        menu = QMenu()
        
        # 规则信息
        rule_name = rule.get('name', '未命名规则')
        rule_type = rule.get('type', '')
        rule_enabled = rule.get('enabled', True)
        # 对于定时清仓规则，检查 scheduled_clear_executed 字段
        if rule_type == 'scheduled_clear':
            rule_executed = rule.get('scheduled_clear_executed', False)
        else:
            rule_executed = rule.get('executed', False)

        grid_status_tag = ""
        _n, _total = 0, 0
        if rule_type in ("grid_buy", "grid_sell"):
            _n, _total, grid_status_tag = self._grid_exec_progress(rule)
            if grid_status_tag == "已执行":
                rule_executed = True
        
        # 规则类型的中文名称
        # 处理特殊规则类型（可能不在RULE_TYPE_NAMES中或转换失败）
        if rule_type == 'night_buy':
            type_name = '夜市买入'
        elif rule_type == 'night_sell':
            type_name = '夜市卖出'
        elif rule_type == 'scheduled_clear':
            type_name = '定时清仓'
        else:
            try:
                type_name = RULE_TYPE_NAMES.get(RuleType(rule_type), rule_type) if rule_type else '未知类型'
            except (ValueError, AttributeError):
                # 如果转换失败，使用规则类型本身，如果为空则显示"未知类型"
                type_name = rule_type if rule_type else '未知类型'
        
        # 菜单标题
        if rule_executed:
            # 已执行的规则显示执行信息
            exec_time = rule.get('executed_time', '未知时间')
            exec_price = rule.get('executed_price', 0)
            exec_volume = rule.get('executed_volume', 0)
            executed_reason = str(rule.get('executed_reason', '') or '')
            executed_order_id = str(rule.get('order_id', '') or '')
            if (
                executed_reason == 'buy_block_window'
                or executed_order_id == 'SKIPPED_BUY_WINDOW'
            ):
                info_action = menu.addAction(f"[已执行-禁买窗口跳过] {rule_name} ({type_name})")
            elif (
                executed_reason == 'order_below_min'
                or executed_order_id == 'SKIPPED_MIN_BUY'
            ):
                info_action = menu.addAction(f"[已执行-本笔低于最小买入] {rule_name} ({type_name})")
            elif (
                executed_reason == 'early_cancelled'
                or executed_order_id == 'EARLY_CANCELLED'
            ):
                info_action = menu.addAction(f"[已执行-提前撤单] {rule_name} ({type_name})")
            elif executed_reason == 'not_true_breakthrough':
                info_action = menu.addAction(f"[已结束-非真突破] {rule_name} ({type_name})")
            elif (
                executed_reason == 'band_hard_pass'
                or executed_order_id == 'BAND_HARD_PASS'
                or self._is_band_hard_pass_rule(rule)
            ):
                kind = self._band_hard_pass_kind_label(rule)
                info_action = menu.addAction(
                    f"[已结束-价格带硬pass·{kind}] {rule_name} ({type_name})"
                )
            elif (
                rule_type == 'breakthrough_buy'
                and self._rule_true_breakthrough_passed(rule)
            ):
                info_action = menu.addAction(f"[已执行-真突破] {rule_name} ({type_name})")
            elif executed_reason in ('breakthrough_probe_completed', 'breakthrough_probe_probe_only'):
                info_action = menu.addAction(f"[已执行-试探建仓] {rule_name} ({type_name})")
            elif executed_reason == 'order_failed':
                info_action = menu.addAction(f"[已执行-下单失败] {rule_name} ({type_name})")
            else:
                info_action = menu.addAction(f"[已执行] {rule_name} ({type_name})")
            info_action.setEnabled(False)
            
            # 显示执行详情
            detail_action = menu.addAction(f"  执行时间: {exec_time}")
            detail_action.setEnabled(False)
            detail_action = menu.addAction(f"  执行价格: {exec_price:.2f}元")
            detail_action.setEnabled(False)
            detail_action = menu.addAction(f"  执行数量: {exec_volume}股")
            detail_action.setEnabled(False)
            if rule_type in ("grid_buy", "grid_sell") and grid_status_tag:
                detail_action = menu.addAction(f"  网格进度: {_n}/{_total} 点已完成")
                detail_action.setEnabled(False)
            if (
                executed_reason == 'buy_block_window'
                or executed_order_id == 'SKIPPED_BUY_WINDOW'
            ):
                detail_action = menu.addAction("  执行结果: 命中禁买时间窗，已跳过下单")
                detail_action.setEnabled(False)
            elif (
                executed_reason == 'order_below_min'
                or executed_order_id == 'SKIPPED_MIN_BUY'
            ):
                detail_action = menu.addAction("  执行结果: 本笔低于最小买入，已跳过下单")
                detail_action.setEnabled(False)
            elif (
                executed_reason == 'early_cancelled'
                or executed_order_id == 'EARLY_CANCELLED'
            ):
                detail_action = menu.addAction("  执行结果: 订单列表人工撤单，已结束")
                detail_action.setEnabled(False)
            elif executed_reason == 'not_true_breakthrough':
                detail_action = menu.addAction("  执行结果: 已结束，非真突破未下单")
                detail_action.setEnabled(False)
                tb_detail = str(rule.get('executed_detail', '') or '').strip()
                if tb_detail:
                    detail_action = menu.addAction(f"  详情: {tb_detail}")
                    detail_action.setEnabled(False)
            elif (
                executed_reason == 'band_hard_pass'
                or executed_order_id == 'BAND_HARD_PASS'
                or self._is_band_hard_pass_rule(rule)
            ):
                kind = self._band_hard_pass_kind_label(rule)
                detail_action = menu.addAction(
                    f"  执行结果: 已结束，价格带硬pass（{kind}）未下单"
                )
                detail_action.setEnabled(False)
                hp_detail = str(rule.get('executed_detail', '') or '').strip()
                if not hp_detail:
                    hp_detail = str(rule.get('true_breakthrough_detail', '') or '').strip()
                if hp_detail:
                    detail_action = menu.addAction(f"  详情: {hp_detail}")
                    detail_action.setEnabled(False)
            elif (
                rule_type == 'breakthrough_buy'
                and self._rule_true_breakthrough_passed(rule)
            ):
                detail_action = menu.addAction("  执行结果: 真突破判定通过，已下单")
                detail_action.setEnabled(False)
                tb_detail = self._rule_true_breakthrough_detail(rule)
                if tb_detail:
                    detail_action = menu.addAction(f"  详情: {tb_detail}")
                    detail_action.setEnabled(False)
            elif executed_reason in ('breakthrough_probe_completed', 'breakthrough_probe_probe_only'):
                if executed_reason == 'breakthrough_probe_completed':
                    detail_action = menu.addAction("  执行结果: 试探建仓完成（试探+补买）")
                else:
                    detail_action = menu.addAction("  执行结果: 仅试探仓，已放弃补买")
                detail_action.setEnabled(False)
                probe_detail = str(rule.get('executed_detail', '') or '').strip()
                if probe_detail:
                    detail_action = menu.addAction(f"  详情: {probe_detail}")
                    detail_action.setEnabled(False)
            elif executed_reason == 'order_failed':
                detail_action = menu.addAction("  执行结果: 下单失败，规则已结束")
                detail_action.setEnabled(False)
        elif grid_status_tag.startswith("已部分执行"):
            info_action = menu.addAction(f"[{grid_status_tag}] {rule_name} ({type_name})")
            info_action.setEnabled(False)
            detail_action = menu.addAction(f"  网格进度: {_n}/{_total} 点已完成，剩余点待触发")
            detail_action.setEnabled(False)
        else:
            info_action = menu.addAction(f"📋 {rule_name} ({type_name})")
            info_action.setEnabled(False)  # 只显示，不可点击
        
        menu.addSeparator()
        
        # 已执行的规则只能删除，不能编辑或禁用（但定时清仓规则可以修改时间）
        toggle_action = None
        rename_action = None
        edit_percent_action = None
        edit_grid_action = None
        edit_wall_action = None
        edit_time_action = None
        
        if not rule_executed:
            # 未执行的规则才显示编辑选项
            
            # 启用/禁用选项
            if rule_enabled:
                toggle_action = menu.addAction("⏸️ 禁用此规则")
            else:
                toggle_action = menu.addAction("▶️ 启用此规则")
            
            # 重命名选项
            rename_action = menu.addAction("✏️ 重命名")
            
            # 编辑百分比选项（仅对弹性买入/弹性卖出规则显示）
            if rule_type in ['best_buy', 'best_sell']:
                if rule_type == 'best_buy':
                    edit_percent_action = menu.addAction("📊 编辑反弹百分比")
                else:
                    edit_percent_action = menu.addAction("📊 编辑回落百分比")
            
            # 设置网格数选项（仅对网格买入/卖出规则显示）
            if rule_type in ['grid_buy', 'grid_sell']:
                edit_grid_action = menu.addAction("⚙️ 设置网格数")
            
            # 编辑壁厚选项（仅对笼子买入/笼子卖出规则显示）
            if rule_type in ['cage_buy', 'cage_sell']:
                edit_wall_action = menu.addAction("📏 编辑壁厚")
            
            # 编辑时间选项（仅对定时清仓规则显示）
            if rule_type == 'scheduled_clear':
                edit_time_action = menu.addAction("⏰ 修改时间")
        elif rule_type == 'scheduled_clear':
            # 定时清仓规则即使已执行，也允许修改时间（用于查看/记录）
            edit_time_action = menu.addAction("⏰ 修改时间")
            
            menu.addSeparator()
        
        # 删除选项（所有规则都可以删除）
        delete_action = menu.addAction("🗑️ 删除此规则")
        
        # 显示菜单并获取选择
        action = menu.exec_(QCursor.pos())
        
        if action == delete_action:
            # 已执行的规则直接删除，不需要确认
            if rule_executed:
                # 从规则列表中删除
                self.rules = [r for r in self.rules if r.get('id') != rule.get('id')]
                
                # 保存并更新图表
                self._save_rules()
                self.update_chart()
                
                print(f"已删除已执行规则: {rule_name}")
            else:
                # 未执行的规则需要确认删除
                reply = QMessageBox.question(
                    None,
                    "确认删除",
                    f"确定要删除规则 '{rule_name}' 吗？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )
                
                if reply == QMessageBox.Yes:
                    # 如果是提前下单的未执行规则，先自动撤单，行为与“禁用规则”保持一致
                    if rule.get('early_order', False) and not rule.get('executed', False):
                        self._cancel_single_early_order(rule)

                    # 从规则列表中删除
                    self.rules = [r for r in self.rules if r.get('id') != rule.get('id')]
                    
                    # 保存并更新图表
                    self._save_rules()
                    self.update_chart()
                    
                    print(f"已删除规则: {rule_name}")
        
        elif toggle_action and action == toggle_action:
            # 切换启用/禁用状态
            new_enabled = not rule_enabled
            
            # 如果是从禁用变为启用，并且任务正在运行，需要检查是否会立即触发
            if new_enabled and self.task_running and not self.task_paused:
                will_trigger = False
                trigger_reason = ""
                
                rule_type = rule.get('type')
                price = rule.get('price', 0)
                volume = rule.get('volume', 0)
                
                if rule_type == 'single_buy' and self.current_price > 0:
                    if price >= self.current_price:
                        will_trigger = True
                        trigger_reason = f"买入价格({price:.2f}元) >= 当前价格({self.current_price:.2f}元)"
                elif rule_type == 'breakthrough_buy' and self.current_price > 0:
                    will_now, why = self._breakthrough_buy_may_trigger_immediately(rule)
                    if will_now:
                        will_trigger = True
                        trigger_reason = why
                elif rule_type == 'single_sell' and self.current_price > 0:
                    if price <= self.current_price:
                        will_trigger = True
                        trigger_reason = f"卖出价格({price:.2f}元) <= 当前价格({self.current_price:.2f}元)"
                elif rule_type == 'breakthrough_sell' and self.current_price > 0:
                    if price > self.current_price:
                        will_trigger = True
                        trigger_reason = f"突破卖出价格({price:.2f}元) > 当前价格({self.current_price:.2f}元)"
                elif rule_type == 'cage_buy' and self.current_price > 0:
                    price_low = rule.get('price_low', 0)
                    price_high = rule.get('price_high', 0)
                    inner_low, inner_high = self._get_cage_inner_bounds(rule)
                    cage_entered = rule.get('cage_entered', False)
                    if cage_entered and (self.current_price <= inner_low or self.current_price >= price_high):
                        will_trigger = True
                        trigger_point = "内下沿" if self.current_price <= inner_low else "上限"
                        trigger_price = inner_low if self.current_price <= inner_low else price_high
                        trigger_reason = f"已进入笼子，当前价格({self.current_price:.2f}元)达到{trigger_point}({trigger_price:.2f}元)"
                    elif not cage_entered and inner_low < self.current_price < inner_high:
                        trigger_reason = f"当前价格({self.current_price:.2f}元)在有效笼子内，等待突破触发"
                elif rule_type == 'cage_sell' and self.current_price > 0:
                    price_low = rule.get('price_low', 0)
                    price_high = rule.get('price_high', 0)
                    inner_low, inner_high = self._get_cage_inner_bounds(rule)
                    cage_entered = rule.get('cage_entered', False)
                    if cage_entered and (self.current_price <= price_low or self.current_price >= inner_high):
                        will_trigger = True
                        trigger_point = "下限" if self.current_price <= price_low else "内上沿"
                        trigger_price = price_low if self.current_price <= price_low else inner_high
                        trigger_reason = f"已进入笼子，当前价格({self.current_price:.2f}元)达到{trigger_point}({trigger_price:.2f}元)"
                    elif not cage_entered and inner_low < self.current_price < inner_high:
                        trigger_reason = f"当前价格({self.current_price:.2f}元)在有效笼子内，等待突破触发"
                
                if will_trigger:
                    # 弹出确认对话框
                    msg = QMessageBox()
                    msg.setIcon(QMessageBox.Warning)
                    msg.setWindowTitle("⚠️ 规则可能立即触发")
                    msg.setText(f"<b>警告：启用该规则后可能立即触发交易！</b>")
                    msg.setInformativeText(
                        f"<p>规则名称：{rule_name}</p>"
                        f"<p>触发条件：{trigger_reason}</p>"
                        f"<p>交易数量：{volume}股</p>"
                        f"<br><p>是否仍然启用该规则？</p>"
                    )
                    
                    # 添加两个按钮
                    msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
                    msg.setDefaultButton(QMessageBox.No)
                    msg.button(QMessageBox.Yes).setText("启用")
                    msg.button(QMessageBox.No).setText("取消")
                    
                    reply = msg.exec_()
                    
                    if reply != QMessageBox.Yes:
                        # 用户取消，不启用
                        print(f"已取消启用规则: {rule_name}")
                        return
            
            # 如果禁用规则，且是提前订单，需要撤单
            if not new_enabled and rule.get('early_order', False) and not rule.get('executed', False):
                # 撤单这个提前订单
                self._cancel_single_early_order(rule)
            
            # 执行启用/禁用
            rule['enabled'] = new_enabled
            
            # 保存并更新图表
            self._save_rules()
            self.update_chart()
            
            status = "启用" if rule['enabled'] else "禁用"
            print(f"已{status}规则: {rule_name}")
        
        elif rename_action and action == rename_action:
            # 重命名规则
            new_name, ok = QInputDialog.getText(
                None,
                "重命名规则",
                f"请输入新的规则名称:",
                text=rule_name
            )
            
            if ok and new_name.strip():
                rule['name'] = new_name.strip()
                
                # 保存并更新图表
                self._save_rules()
                self.update_chart()
                
                print(f"已重命名规则: {rule_name} -> {new_name}")
        
        elif edit_percent_action and action == edit_percent_action:
            # 编辑百分比
            self._edit_best_rule_percent(rule)
        
        elif edit_grid_action and action == edit_grid_action:
            # 编辑网格参数
            self._edit_grid_rule_params(rule)
        
        elif edit_wall_action and action == edit_wall_action:
            # 编辑笼子壁厚
            self._edit_cage_wall_thickness(rule)
        
        elif edit_time_action and action == edit_time_action:
            # 编辑定时清仓时间
            self._edit_scheduled_clear_rule_time(rule)
    
    def _edit_scheduled_clear_rule_time(self, rule):
        """编辑定时清仓规则的时间"""
        from datetime import datetime, time as dt_time
        
        rule_name = rule.get('name', '未命名')
        
        # 获取当前时间字符串
        current_time_str = rule.get('scheduled_clear_time', '14:56:00')
        
        # 创建自定义时间对话框
        dialog = QDialog(self)
        dialog.setWindowTitle(f"修改定时清仓时间 - {rule_name}")
        dialog.setModal(True)
        
        layout = QVBoxLayout()
        
        # 提示标签
        label = QLabel(f"规则: {rule_name}\n请设置时间（可使用上下箭头调整时分秒）:")
        label.setFont(QFont("Microsoft YaHei", 10))
        layout.addWidget(label)
        
        # 时间选择控件
        time_edit = QTimeEdit()
        time_edit.setDisplayFormat("HH:mm:ss")
        time_edit.setFont(QFont("Microsoft YaHei", 12))
        
        # 解析当前时间字符串并设置
        try:
            time_obj = datetime.strptime(current_time_str, '%H:%M:%S').time()
            time_edit.setTime(QTime(time_obj.hour, time_obj.minute, time_obj.second))
        except:
            # 如果解析失败，使用默认时间
            time_edit.setTime(QTime(14, 56, 0))
        
        layout.addWidget(time_edit)
        
        # 按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)
        
        dialog.setLayout(layout)
        
        # 显示对话框
        if dialog.exec_() == QDialog.Accepted:
            qtime = time_edit.time()
            time_str = f"{qtime.hour():02d}:{qtime.minute():02d}:{qtime.second():02d}"
            
            # 更新规则中的时间
            rule['scheduled_clear_time'] = time_str
            self._attach_scheduled_clear_effective_date(rule, reset_runtime=True)
            
            # 保存并更新图表
            self._save_rules()
            self.update_chart()
            
            self.logger.info(f"[{self.stock_code}] 定时清仓规则「{rule_name}」时间已修改为: {time_str}")
            print(f"定时清仓规则「{rule_name}」时间已修改为: {time_str}")
    
    def _add_single_point_rule(self, price, volume):
        """添加单点规则"""
        import uuid
        from PyQt5.QtWidgets import QMessageBox
        from core.trading_rules import RULE_TYPE_NAMES, RuleType
        
        # 限制交易量为100的倍数
        volume = max(100, int(abs(volume) / 100) * 100)
        
        # 根据添加模式创建规则
        rule_type = self.add_mode

        band_fields = None
        if rule_type == "breakthrough_buy":
            band_fields = self._prompt_breakthrough_buy_band_options(price, volume)
            if band_fields is False:
                # 用户取消
                self.add_mode = None
                self._uncheck_all_tool_buttons()
                self.canvas.setCursor(Qt.ArrowCursor)
                return
            if isinstance(band_fields, dict) and band_fields.get("use_band"):
                price = float(band_fields["price"])
                volume = int(band_fields.get("volume") or volume)

        try:
            self._ensure_session_prev_close_and_limits()
        except Exception:
            pass
        
        # 价格限制检查：买入不能超过涨停价且不能低于跌停价，卖出不能低于跌停价
        if rule_type in ['single_buy', 'breakthrough_buy', 'night_buy']:
            # 买入规则：检查是否超过涨停价或低于跌停价
            limit_up_price = None
            limit_down_price = None
            if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                limit_up_price = self.limit_up_price
            if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                limit_down_price = self.limit_down_price
            if not limit_up_price or not limit_down_price:
                if hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                    limit_up_price, limit_down_price = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                    if limit_up_price:
                        self.limit_up_price = limit_up_price
                    if limit_down_price:
                        self.limit_down_price = limit_down_price
                else:
                    # 如果还没有涨跌停价，尝试重新计算关键价格点
                    try:
                        self.calculate_key_points(force_recalculate=True)
                        if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                            limit_up_price = self.limit_up_price
                        if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                            limit_down_price = self.limit_down_price
                    except Exception as e:
                        self.logger.warning(f"[{self.stock_code}] 无法获取涨跌停价: {e}")
            
            # 使用精度处理避免浮点数精度问题，允许等于涨跌停价
            precision = self._get_price_precision()
            price_rounded = round(price, precision)
            # 检查是否超过涨停价（与拖动规则、夜市委托一致：超出则自动钳到涨停价，避免浮点/取整误差反复弹窗）
            if limit_up_price:
                limit_up_price_rounded = round(limit_up_price, precision)
                if price_rounded > limit_up_price_rounded:
                    price = limit_up_price
                    price_rounded = limit_up_price_rounded
                    self.logger.info(
                        f"[{self.stock_code}] 买入规则({rule_type})价格超出涨停价，已自动调整为涨停价: {limit_up_price:.{precision}f}元"
                    )
            # 检查是否低于跌停价（自动钳到跌停价）
            if limit_down_price:
                limit_down_price_rounded = round(limit_down_price, precision)
                if price_rounded < limit_down_price_rounded:
                    price = limit_down_price
                    price_rounded = limit_down_price_rounded
                    self.logger.info(
                        f"[{self.stock_code}] 买入规则({rule_type})价格低于跌停价，已自动调整为跌停价: {limit_down_price:.{precision}f}元"
                    )
        
        elif rule_type in ['single_sell', 'breakthrough_sell', 'night_sell', 'scheduled_clear']:
            # 卖出规则：检查是否低于跌停价或超过涨停价
            limit_down_price = None
            limit_up_price = None
            if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                limit_down_price = self.limit_down_price
            if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                limit_up_price = self.limit_up_price
            elif hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                limit_up_price, limit_down_price = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                if limit_up_price:
                    self.limit_up_price = limit_up_price
                if limit_down_price:
                    self.limit_down_price = limit_down_price
            else:
                # 如果还没有涨跌停价，尝试重新计算关键价格点
                try:
                    self.calculate_key_points(force_recalculate=True)
                    if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                        limit_down_price = self.limit_down_price
                    if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                        limit_up_price = self.limit_up_price
                except Exception as e:
                    self.logger.warning(f"[{self.stock_code}] 无法获取涨跌停价: {e}")
            
            # 使用小的容差值避免浮点数精度问题，允许等于涨跌停价
            # 先将两个价格都四舍五入到相同精度，然后再比较
            precision = self._get_price_precision()
            price_rounded = round(price, precision)
            
            # 检查是否低于跌停价（自动钳到跌停价）
            if limit_down_price:
                limit_down_price_rounded = round(limit_down_price, precision)
                if price_rounded < limit_down_price_rounded:
                    price = limit_down_price
                    price_rounded = limit_down_price_rounded
                    self.logger.info(
                        f"[{self.stock_code}] 卖出规则({rule_type})价格低于跌停价，已自动调整为跌停价: {limit_down_price:.{precision}f}元"
                    )
            
            # 检查是否超过涨停价（自动钳到涨停价，含单点/突破/定时清仓）
            if limit_up_price:
                limit_up_price_rounded = round(limit_up_price, precision)
                if price_rounded > limit_up_price_rounded:
                    price = limit_up_price
                    price_rounded = limit_up_price_rounded
                    self.logger.info(
                        f"[{self.stock_code}] 卖出规则({rule_type})价格超过涨停价，已自动调整为涨停价: {limit_up_price:.{precision}f}元"
                    )
        # 处理夜市规则和定时清仓规则（可能不在RULE_TYPE_NAMES中）
        if rule_type == 'night_buy':
            rule_name = '夜市买入'
        elif rule_type == 'night_sell':
            rule_name = '夜市卖出'
        elif rule_type == 'scheduled_clear':
            rule_name = '定时清仓'
        else:
            try:
                rule_name = RULE_TYPE_NAMES.get(RuleType(rule_type), '未命名规则')
            except (ValueError, AttributeError):
                # 如果RuleType枚举中没有该类型，使用默认名称
                rule_name = '未命名规则'
        
        # 计数已有同类规则
        same_type_count = sum(1 for r in self.rules if r.get('type') == rule_type)
        if same_type_count > 0:
            rule_name = f"{rule_name}{same_type_count + 1}"
        
        # 安全检查：如果任务正在运行，检测是否会立即触发
        rule_enabled = True  # 默认启用
        if self.task_running and not self.task_paused:
            will_trigger = False
            trigger_reason = ""
            
            if rule_type == 'single_buy' and self.current_price > 0:
                if price >= self.current_price:
                    will_trigger = True
                    trigger_reason = f"买入价格({price:.2f}元) >= 当前价格({self.current_price:.2f}元)"
            elif rule_type == 'breakthrough_buy' and self.current_price > 0:
                # 创建时：价格带已在 band_fields 分支排除；普通突破仍按「已在触发价上方」提示
                if isinstance(band_fields, dict) and band_fields.get("use_band"):
                    probe = {
                        "type": "breakthrough_buy",
                        "price": price,
                        "band_low": band_fields.get("band_low"),
                        "band_high": band_fields.get("band_high"),
                    }
                    will_now, why = self._breakthrough_buy_may_trigger_immediately(probe)
                    if will_now:
                        will_trigger = True
                        trigger_reason = why
                else:
                    will_now, why = self._breakthrough_buy_may_trigger_immediately(
                        {"type": "breakthrough_buy", "price": price}
                    )
                    if will_now:
                        will_trigger = True
                        trigger_reason = why
            elif rule_type == 'single_sell' and self.current_price > 0:
                if price <= self.current_price:
                    will_trigger = True
                    trigger_reason = f"卖出价格({price:.2f}元) <= 当前价格({self.current_price:.2f}元)"
            elif rule_type == 'breakthrough_sell' and self.current_price > 0:
                if price > self.current_price:
                    will_trigger = True
                    trigger_reason = f"突破卖出价格({price:.2f}元) > 当前价格({self.current_price:.2f}元)"
            
            if will_trigger:
                # 弹出确认对话框
                msg = QMessageBox()
                msg.setIcon(QMessageBox.Warning)
                msg.setWindowTitle("⚠️ 规则可能立即触发")
                msg.setText(f"<b>警告：该规则创建后可能立即触发交易！</b>")
                msg.setInformativeText(
                    f"<p>规则类型：{rule_name}</p>"
                    f"<p>触发条件：{trigger_reason}</p>"
                    f"<p>交易数量：{volume}股</p>"
                    f"<br><p>您希望如何处理？</p>"
                )
                
                # 添加三个按钮
                btn_enable = msg.addButton("创建并启用", QMessageBox.AcceptRole)
                btn_disable = msg.addButton("创建但禁用（推荐）", QMessageBox.ActionRole)
                btn_cancel = msg.addButton("取消", QMessageBox.RejectRole)
                msg.setDefaultButton(btn_disable)
                
                msg.exec_()
                
                if msg.clickedButton() == btn_cancel:
                    # 取消创建
                    self.add_mode = None
                    self._uncheck_all_tool_buttons()
                    self.canvas.setCursor(Qt.ArrowCursor)
                    return
                elif msg.clickedButton() == btn_disable:
                    # 创建但禁用
                    rule_enabled = False
                # btn_enable 则保持 rule_enabled = True
        
        precision = self._get_price_precision()
        new_rule = {
            'id': f"rule_{uuid.uuid4().hex[:8]}",
            'type': rule_type,
            'name': rule_name,
            'enabled': rule_enabled,
            'price': round(price, precision),
            'volume': volume
        }
        if (
            rule_type == "breakthrough_buy"
            and isinstance(band_fields, dict)
            and band_fields.get("use_band")
        ):
            from core.price_band_buy import stamp_band_breakthrough_defaults

            new_rule["band_low"] = round(float(band_fields["band_low"]), precision)
            new_rule["band_high"] = round(float(band_fields["band_high"]), precision)
            new_rule["band_accept_low"] = round(
                float(band_fields["band_accept_low"]), precision
            )
            new_rule["true_breakthrough_window_sec"] = int(
                band_fields.get("window_sec") or 45
            )
            new_rule["name"] = band_fields.get("name") or "突破买入（价格带硬pass）"
            stamp_band_breakthrough_defaults(new_rule)
        self._stamp_early_order_flag(new_rule)
        self._stamp_breakthrough_flags(new_rule)
        if (
            rule_type == "breakthrough_buy"
            and isinstance(band_fields, dict)
            and band_fields.get("use_band")
        ):
            # 价格带必须真突破；覆盖全局可能关掉真突破的快照
            from core.price_band_buy import stamp_band_breakthrough_defaults

            stamp_band_breakthrough_defaults(new_rule)
        
        # 如果是定时清仓规则，添加时间字段
        if rule_type == 'scheduled_clear':
            new_rule['scheduled_clear_time'] = self.scheduled_clear_time.strftime("%H:%M:%S")
            new_rule['scheduled_clear_executed'] = False
            self._attach_scheduled_clear_effective_date(new_rule, reset_runtime=False)
            # 启动定时器（如果还没有启动）
            if not self.scheduled_clear_timer:
                from PyQt5.QtCore import QTimer
                self.scheduled_clear_timer = QTimer()
                self.scheduled_clear_timer.timeout.connect(self.check_scheduled_clear)
            if not self.scheduled_clear_timer.isActive():
                self.scheduled_clear_timer.start(1000)  # 每秒检查一次
        
        self.rules.append(new_rule)
        self._save_rules()
        self.update_chart()
        
        status_text = "（已禁用）" if not rule_enabled else ""
        print(f"添加规则: {rule_name} 价格{price:.2f} 数量{volume} {status_text}")
        
        # 添加完一个规则后，延迟一小段时间再退出添加模式（让按钮保持按下状态一会儿，提供视觉反馈）
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(300, lambda: self._clear_add_mode())  # 300毫秒后恢复
    
    def _prompt_breakthrough_buy_band_options(self, click_price, volume):
        """⬆️买 时选择普通突破或价格带硬pass。返回 False=取消；dict(use_band=...)."""
        from PyQt5.QtWidgets import (
            QButtonGroup,
            QDialog,
            QDialogButtonBox,
            QDoubleSpinBox,
            QFormLayout,
            QHBoxLayout,
            QLabel,
            QMessageBox,
            QRadioButton,
            QVBoxLayout,
        )
        from core.price_band_buy import build_ma5_band_fields

        dlg = QDialog(self)
        dlg.setWindowTitle("突破买入方式")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("选择规则类型（不新增工具栏按钮）："))

        rb_normal = QRadioButton("普通突破买入（上穿触发价）")
        rb_band = QRadioButton("价格带硬pass（监控带内真突破；深位或卖一>MA5作废）")
        rb_normal.setChecked(True)
        grp = QButtonGroup(dlg)
        grp.addButton(rb_normal)
        grp.addButton(rb_band)
        layout.addWidget(rb_normal)
        layout.addWidget(rb_band)

        form = QFormLayout()
        watch_spin = QDoubleSpinBox()
        watch_spin.setRange(0.1, 20.0)
        watch_spin.setDecimals(1)
        watch_spin.setSingleStep(0.5)
        watch_spin.setValue(3.0)
        watch_spin.setSuffix(" %")
        accept_spin = QDoubleSpinBox()
        accept_spin.setRange(0.1, 20.0)
        accept_spin.setDecimals(1)
        accept_spin.setSingleStep(0.5)
        accept_spin.setValue(1.0)
        accept_spin.setSuffix(" %")
        rb_top_click = QRadioButton("上沿=图表点击价")
        rb_top_ma5 = QRadioButton("上沿=当前5日线")
        rb_top_ma5.setChecked(True)
        top_grp = QButtonGroup(dlg)
        top_grp.addButton(rb_top_click)
        top_grp.addButton(rb_top_ma5)
        preview = QLabel("")
        form.addRow("监控带宽", watch_spin)
        form.addRow("有效带宽", accept_spin)
        top_row = QHBoxLayout()
        top_row.addWidget(rb_top_ma5)
        top_row.addWidget(rb_top_click)
        form.addRow("上沿来源", top_row)
        form.addRow("预览", preview)
        layout.addLayout(form)

        def _ma5():
            try:
                self.calculate_key_points(force_recalculate=False)
            except Exception:
                pass
            kp = {
                name: px
                for name, px in (getattr(self, "key_points", []) or [])
                if isinstance(px, (int, float)) and px > 0
            }
            return float(kp.get("5日") or 0)

        def _refresh_preview():
            use_ma5 = rb_top_ma5.isChecked()
            top = _ma5() if use_ma5 else float(click_price or 0)
            if top <= 0:
                preview.setText("无法计算（缺少上沿价格）")
                return
            lu = float(getattr(self, "limit_up_price", 0) or 0)
            ld = float(getattr(self, "limit_down_price", 0) or 0)
            fields = build_ma5_band_fields(
                top,
                band_pct=float(watch_spin.value()) / 100.0,
                accept_pct=float(accept_spin.value()) / 100.0,
                limit_up=lu,
                limit_down=ld,
                precision=self._get_price_precision(),
            )
            if not fields:
                preview.setText("带宽无效或被涨跌停钳没")
                return
            preview.setText(
                f"监控[{fields['band_low']:.2f},{fields['band_high']:.2f}] "
                f"有效下沿={fields['band_accept_low']:.2f}"
            )

        watch_spin.valueChanged.connect(lambda *_: _refresh_preview())
        accept_spin.valueChanged.connect(lambda *_: _refresh_preview())
        rb_top_click.toggled.connect(lambda *_: _refresh_preview())
        rb_top_ma5.toggled.connect(lambda *_: _refresh_preview())
        rb_band.toggled.connect(lambda on: None)
        _refresh_preview()

        def _sync_enabled():
            on = rb_band.isChecked()
            watch_spin.setEnabled(on)
            accept_spin.setEnabled(on)
            rb_top_click.setEnabled(on)
            rb_top_ma5.setEnabled(on)

        rb_normal.toggled.connect(lambda *_: _sync_enabled())
        rb_band.toggled.connect(lambda *_: _sync_enabled())
        _sync_enabled()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec_() != QDialog.Accepted:
            return False
        if rb_normal.isChecked():
            return {"use_band": False}

        top = _ma5() if rb_top_ma5.isChecked() else float(click_price or 0)
        if top <= 0:
            QMessageBox.warning(self, "价格带硬pass", "无法取得上沿价格（5日线或点击价）。")
            return False
        if float(accept_spin.value()) > float(watch_spin.value()):
            QMessageBox.warning(self, "价格带硬pass", "有效带宽不能大于监控带宽。")
            return False
        lu = float(getattr(self, "limit_up_price", 0) or 0)
        ld = float(getattr(self, "limit_down_price", 0) or 0)
        fields = build_ma5_band_fields(
            top,
            band_pct=float(watch_spin.value()) / 100.0,
            accept_pct=float(accept_spin.value()) / 100.0,
            limit_up=lu,
            limit_down=ld,
            precision=self._get_price_precision(),
        )
        if not fields:
            QMessageBox.warning(self, "价格带硬pass", "无法生成有效价格带。")
            return False
        return {
            "use_band": True,
            "price": fields["price"],
            "band_low": fields["band_low"],
            "band_high": fields["band_high"],
            "band_accept_low": fields["band_accept_low"],
            "volume": volume,
            "window_sec": 45,
            "name": (
                f"突破买入（监控{watch_spin.value():.1f}%有效"
                f"{accept_spin.value():.1f}%硬pass）"
            ),
        }

    def _add_best_rule_simple(self, price, volume):
        """添加弹性买入/弹性卖出规则（使用默认百分比）"""
        import uuid
        from core.trading_rules import RULE_TYPE_NAMES, RuleType
        
        # 限制交易量为100的倍数
        volume = max(100, int(abs(volume) / 100) * 100)
        
        # 根据添加模式创建规则
        rule_type = self.add_mode
        
        # 价格限制检查：买入触发价不能超过涨停价且不能低于跌停价，卖出触发价不能低于跌停价
        if rule_type == 'best_buy':
            # 买入规则：检查触发价是否超过涨停价或低于跌停价
            limit_up_price = None
            limit_down_price = None
            if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                limit_up_price = self.limit_up_price
            if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                limit_down_price = self.limit_down_price
            if not limit_up_price or not limit_down_price:
                if hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                    limit_up_price, limit_down_price = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                    if limit_up_price:
                        self.limit_up_price = limit_up_price
                    if limit_down_price:
                        self.limit_down_price = limit_down_price
                else:
                    # 如果还没有涨跌停价，尝试重新计算关键价格点
                    try:
                        self.calculate_key_points(force_recalculate=True)
                        if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                            limit_up_price = self.limit_up_price
                        if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                            limit_down_price = self.limit_down_price
                    except Exception as e:
                        self.logger.warning(f"[{self.stock_code}] 无法获取涨跌停价: {e}")
            
            # 使用精度处理避免浮点数精度问题，允许等于涨跌停价
            precision = self._get_price_precision()
            price_rounded = round(price, precision)
            # 检查是否超过涨停价 / 低于跌停价（自动钳位，与单点规则一致）
            if limit_up_price:
                limit_up_price_rounded = round(limit_up_price, precision)
                if price_rounded > limit_up_price_rounded:
                    price = limit_up_price
                    price_rounded = limit_up_price_rounded
                    self.logger.info(
                        f"[{self.stock_code}] 弹性买入触发价超出涨停价，已自动调整为涨停价: {limit_up_price:.{precision}f}元"
                    )
            if limit_down_price:
                limit_down_price_rounded = round(limit_down_price, precision)
                if price_rounded < limit_down_price_rounded:
                    price = limit_down_price
                    price_rounded = limit_down_price_rounded
                    self.logger.info(
                        f"[{self.stock_code}] 弹性买入触发价低于跌停价，已自动调整为跌停价: {limit_down_price:.{precision}f}元"
                    )
        
        elif rule_type == 'best_sell':
            # 卖出规则：检查触发价是否低于跌停价或超过涨停价
            limit_down_price = None
            limit_up_price = None
            if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                limit_down_price = self.limit_down_price
            if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                limit_up_price = self.limit_up_price
            if not limit_down_price or not limit_up_price:
                if hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                    limit_up_price, limit_down_price = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                    if limit_up_price:
                        self.limit_up_price = limit_up_price
                    if limit_down_price:
                        self.limit_down_price = limit_down_price
                else:
                    # 如果还没有涨跌停价，尝试重新计算关键价格点
                    try:
                        self.calculate_key_points(force_recalculate=True)
                        if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                            limit_down_price = self.limit_down_price
                        if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                            limit_up_price = self.limit_up_price
                    except Exception as e:
                        self.logger.warning(f"[{self.stock_code}] 无法获取涨跌停价: {e}")
            
            # 使用精度处理避免浮点数精度问题，允许等于涨跌停价
            precision = self._get_price_precision()
            price_rounded = round(price, precision)
            
            # 检查是否超过涨停价 / 低于跌停价（自动钳位）
            if limit_up_price:
                limit_up_price_rounded = round(limit_up_price, precision)
                if price_rounded > limit_up_price_rounded:
                    price = limit_up_price
                    price_rounded = limit_up_price_rounded
                    self.logger.info(
                        f"[{self.stock_code}] 弹性卖出触发价超过涨停价，已自动调整为涨停价: {limit_up_price:.{precision}f}元"
                    )
            if limit_down_price:
                limit_down_price_rounded = round(limit_down_price, precision)
                if price_rounded < limit_down_price_rounded:
                    price = limit_down_price
                    price_rounded = limit_down_price_rounded
                    self.logger.info(
                        f"[{self.stock_code}] 弹性卖出触发价低于跌停价，已自动调整为跌停价: {limit_down_price:.{precision}f}元"
                    )
        
        rule_name = RULE_TYPE_NAMES.get(RuleType(rule_type), '未命名规则')
        
        # 计数已有同类规则
        same_type_count = sum(1 for r in self.rules if r.get('type') == rule_type)
        if same_type_count > 0:
            rule_name = f"{rule_name}{same_type_count + 1}"
        
        # 创建规则（使用默认百分比3%）
        precision = self._get_price_precision()
        new_rule = {
            'id': f"rule_{uuid.uuid4().hex[:8]}",
            'type': rule_type,
            'name': rule_name,
            'enabled': True,
            'trigger_price': round(price, precision),
            'volume': volume
        }
        
        if rule_type == 'best_buy':
            new_rule['rise_percent'] = 0.3  # 默认0.3%
        else:
            new_rule['drop_percent'] = 2.5
            new_rule['room_blend_start'] = DEFAULT_ROOM_BLEND_AT_DROP_LOW
        
        self.rules.append(new_rule)
        self._save_rules()
        self.update_chart()
        
        print(f"添加规则: {rule_name} 触发价{price:.2f} 默认百分比3% 数量{volume}")
        
        # 添加完一个规则后，延迟一小段时间再退出添加模式（让按钮保持按下状态一会儿，提供视觉反馈）
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(300, lambda: self._clear_add_mode())  # 300毫秒后恢复
    
    def _create_cage_rule(self, price_low, price_high, volume):
        """创建笼子规则"""
        import uuid
        from PyQt5.QtWidgets import QMessageBox
        from core.trading_rules import RULE_TYPE_NAMES, RuleType
        
        rule_type = self.add_mode
        rule_name = RULE_TYPE_NAMES.get(RuleType(rule_type), '未命名规则')

        # 涨跌停内自动钳位（与单点规则、拖动节点一致）
        if not hasattr(self, 'prev_close_price') or self.prev_close_price <= 0:
            try:
                self.calculate_key_points(force_recalculate=True)
            except Exception as e:
                self.logger.warning(f"[{self.stock_code}] 笼子规则无法刷新关键价位: {e}")
        tag = "笼子买入" if rule_type == "cage_buy" else "笼子卖出"
        price_low, price_high = self._clamp_rule_price_interval(price_low, price_high, tag)
        
        # 计数已有同类规则
        same_type_count = sum(1 for r in self.rules if r.get('type') == rule_type)
        if same_type_count > 0:
            rule_name = f"{rule_name}{same_type_count + 1}"
        
        # 安全检查：如果任务正在运行，检测是否会立即触发
        rule_enabled = True  # 默认启用
        if self.task_running and not self.task_paused:
            will_trigger = False
            trigger_reason = ""
            
            if self.current_price > 0:
                # 笼子规则：只有当前价在笼子内（price_low < current_price < price_high）时，才会在突破上下限时触发
                # 如果当前价已经在笼子内，且达到上下限，会立即触发
                if price_low < self.current_price < price_high:
                    if self.current_price <= price_low + 0.01:  # 接近下限
                        will_trigger = True
                        trigger_reason = f"当前价格({self.current_price:.2f}元)在笼子内且接近下限({price_low:.2f}元)，可能立即触发"
                    elif self.current_price >= price_high - 0.01:  # 接近上限
                        will_trigger = True
                        trigger_reason = f"当前价格({self.current_price:.2f}元)在笼子内且接近上限({price_high:.2f}元)，可能立即触发"
                # 注意：如果当前价在笼子外面，不会立即触发（需要先进入笼子）
            
            if will_trigger:
                # 弹出确认对话框
                msg = QMessageBox()
                msg.setIcon(QMessageBox.Warning)
                msg.setWindowTitle("⚠️ 规则可能立即触发")
                msg.setText(f"<b>警告：该规则创建后可能立即触发交易！</b>")
                msg.setInformativeText(
                    f"<p>规则类型：{rule_name}</p>"
                    f"<p>价格区间：[{price_low:.2f}, {price_high:.2f}]</p>"
                    f"<p>触发条件：{trigger_reason}</p>"
                    f"<p>交易数量：{volume}股</p>"
                    f"<br><p>您希望如何处理？</p>"
                )
                
                # 添加三个按钮
                btn_enable = msg.addButton("创建并启用", QMessageBox.AcceptRole)
                btn_disable = msg.addButton("创建但禁用（推荐）", QMessageBox.ActionRole)
                btn_cancel = msg.addButton("取消", QMessageBox.RejectRole)
                msg.setDefaultButton(btn_disable)
                
                msg.exec_()
                
                if msg.clickedButton() == btn_cancel:
                    # 取消创建
                    self.add_mode = None
                    self._uncheck_all_tool_buttons()
                    self.canvas.setCursor(Qt.ArrowCursor)
                    return
                elif msg.clickedButton() == btn_disable:
                    # 创建但禁用
                    rule_enabled = False
        
        precision = self._get_price_precision()
        new_rule = {
            'id': f"rule_{uuid.uuid4().hex[:8]}",
            'type': rule_type,
            'name': rule_name,
            'enabled': rule_enabled,
            'price_low': round(price_low, precision),
            'price_high': round(price_high, precision),
            'volume': volume,
            'cage_entered': False,  # 标记是否已经进入过笼子（价格曾在有效内区间内）
            'wall_thickness': self._default_cage_wall_thickness()  # ETF 缺省 0.003，股票 0.03
        }
        
        self.rules.append(new_rule)
        self._save_rules()
        self.update_chart()
        
        status_text = "（已禁用）" if not rule_enabled else ""
        print(f"添加笼子规则: {rule_name} 价格区间[{price_low:.2f}, {price_high:.2f}] 数量{volume} {status_text}")
        
        # 添加完一个规则后，延迟一小段时间再退出添加模式（让按钮保持按下状态一会儿，提供视觉反馈）
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(300, lambda: self._clear_add_mode())  # 300毫秒后恢复
    
    def _create_grid_buy_rule_simple(self, price_low, price_high, volume):
        """创建网格买入规则（直接创建，不弹出对话框）"""
        import uuid
        from core.trading_rules import RULE_TYPE_NAMES, RuleType
        
        rule_type = 'grid_buy'
        rule_name = RULE_TYPE_NAMES.get(RuleType(rule_type), '未命名规则')
        
        if not hasattr(self, 'prev_close_price') or self.prev_close_price <= 0:
            try:
                self.calculate_key_points(force_recalculate=True)
            except Exception as e:
                self.logger.warning(f"[{self.stock_code}] 网格买入无法刷新关键价位: {e}")
        price_low, price_high = self._clamp_rule_price_interval(price_low, price_high, "网格买入")
        
        # 使用默认参数
        num_grids = 2  # 默认2个网格
        volume_per_grid = max(100, int(abs(volume) / 100) * 100)  # 确保是100的倍数
        grid_step = (price_high - price_low) / num_grids
        
        # 计数已有同类规则
        same_type_count = sum(1 for r in self.rules if r.get('type') == rule_type)
        if same_type_count > 0:
            rule_name = f"{rule_name}{same_type_count + 1}"
        
        # 创建规则
        precision = self._get_price_precision()
        new_rule = {
            'id': f"rule_{uuid.uuid4().hex[:8]}",
            'type': rule_type,
            'name': rule_name,
            'enabled': True,
            'start_price': round(price_high, precision),  # 从高价开始
            'end_price': round(price_low, precision),     # 添加结束价格
            'grid_step': round(grid_step, precision),
            'volume_per_grid': volume_per_grid if volume_per_grid > 0 else 100,
            'num_grids': num_grids
        }
        self._stamp_early_order_flag(new_rule)
        
        self.rules.append(new_rule)
        self._save_rules()
        self.update_chart()
        
        print(f"添加网格规则: {rule_name} 价格区间[{price_low:.2f}, {price_high:.2f}] 间距{grid_step:.2f} 每格{volume_per_grid}股 共{num_grids}格")
        
        # 添加完成后，延迟一小段时间再退出添加模式（让按钮保持按下状态一会儿，提供视觉反馈）
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(300, lambda: self._clear_add_mode())  # 300毫秒后恢复
    
    def _create_grid_sell_rule_simple(self, price_low, price_high, volume):
        """创建网格卖出规则（直接创建，不弹出对话框）"""
        import uuid
        from core.trading_rules import RULE_TYPE_NAMES, RuleType
        
        rule_type = 'grid_sell'
        rule_name = RULE_TYPE_NAMES.get(RuleType(rule_type), '未命名规则')
        
        if not hasattr(self, 'prev_close_price') or self.prev_close_price <= 0:
            try:
                self.calculate_key_points(force_recalculate=True)
            except Exception as e:
                self.logger.warning(f"[{self.stock_code}] 网格卖出无法刷新关键价位: {e}")
        price_low, price_high = self._clamp_rule_price_interval(price_low, price_high, "网格卖出")
        
        # 使用默认参数
        num_grids = 2  # 默认2个网格
        volume_per_grid = max(100, int(abs(volume) / 100) * 100)  # 确保是100的倍数
        grid_step = (price_high - price_low) / num_grids
        
        # 计数已有同类规则
        same_type_count = sum(1 for r in self.rules if r.get('type') == rule_type)
        if same_type_count > 0:
            rule_name = f"{rule_name}{same_type_count + 1}"
        
        # 创建规则
        precision = self._get_price_precision()
        new_rule = {
            'id': f"rule_{uuid.uuid4().hex[:8]}",
            'type': rule_type,
            'name': rule_name,
            'enabled': True,
            'start_price': round(price_low, precision),   # 从低价开始
            'end_price': round(price_high, precision),    # 添加结束价格
            'grid_step': round(grid_step, precision),
            'volume_per_grid': volume_per_grid if volume_per_grid > 0 else 100,
            'num_grids': num_grids
        }
        self._stamp_early_order_flag(new_rule)
        
        self.rules.append(new_rule)
        self._save_rules()
        self.update_chart()
        
        print(f"添加网格卖出规则: {rule_name} 价格区间[{price_low:.2f}, {price_high:.2f}] 间距{grid_step:.2f} 每格{volume_per_grid}股 共{num_grids}格")
        
        # 添加完成后，延迟一小段时间再退出添加模式（让按钮保持按下状态一会儿，提供视觉反馈）
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(300, lambda: self._clear_add_mode())  # 300毫秒后恢复
    
    def _edit_best_rule_percent(self, rule):
        """编辑弹性买入/弹性卖出规则的百分比"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QDialogButtonBox, QDoubleSpinBox, QLabel
        from core.trading_rules import RULE_TYPE_NAMES, RuleType
        
        rule_type = rule.get('type')
        rule_name = rule.get('name', '未命名')
        trigger_price = rule.get('trigger_price', 0)
        
        # 创建参数设置对话框
        dialog = QDialog()
        dialog.setWindowTitle(f"编辑 {rule_name}")
        dialog_layout = QVBoxLayout()
        
        form_layout = QFormLayout()
        
        # 显示触发价格（只读）- 根据股票代码类型确定精度
        precision = self._get_price_precision()
        price_label = QLabel(f"{trigger_price:.{precision}f} 元")
        form_layout.addRow("触发价格:", price_label)
        
        # 反弹/回落百分比
        percent_spin = QDoubleSpinBox()
        percent_spin.setRange(0.0, 50.0)
        percent_spin.setDecimals(1)
        percent_spin.setSingleStep(0.1)  # 步进值设置为0.1%
        percent_spin.setSuffix(" %")
        
        if rule_type == 'best_buy':
            current_percent = rule.get('rise_percent', 0.3)
            percent_spin.setValue(current_percent)
            percent_spin.setToolTip("反弹百分比：0%表示价格不再下跌时立即买入，无需等待反弹")
            form_layout.addRow("反弹百分比:", percent_spin)
        else:
            current_percent = rule.get('drop_percent', 2.5)
            percent_spin.setValue(current_percent)
            percent_spin.setToolTip("宽段允许从峰值回撤的百分比")
            form_layout.addRow("回落百分比:", percent_spin)

            blend_spin = QDoubleSpinBox()
            blend_spin.setRange(0.5, 10.0)
            blend_spin.setDecimals(1)
            blend_spin.setSingleStep(0.1)
            blend_spin.setSuffix(" pp")
            blend_spin.setValue(float(rule.get('room_blend_start', DEFAULT_ROOM_BLEND_AT_DROP_LOW) or DEFAULT_ROOM_BLEND_AT_DROP_LOW))
            blend_spin.setToolTip(
                "距涨停还剩几个百分点（相对昨收）时开始往近板收紧；"
                "≥此值仍用满回落%。例：3.0≈+7%起收，1.5≈+8.5%仍宽"
            )
            form_layout.addRow("过渡起点(pp):", blend_spin)
        
        # 最大损失额（动态计算；精度与标的价格一致，ETF 一般为 3 位）
        loss_label = QLabel()
        loss_label.setStyleSheet("color: #f44336; font-weight: bold;")
        
        def update_loss_amount():
            percent = percent_spin.value()
            loss_amount = trigger_price * (percent / 100.0)
            loss_label.setText(f"{loss_amount:.{precision}f} 元")
        
        # 初始化最大损失额
        update_loss_amount()
        
        # 当百分比改变时，更新最大损失额
        percent_spin.valueChanged.connect(update_loss_amount)
        
        form_layout.addRow("最大损失额:", loss_label)
        
        dialog_layout.addLayout(form_layout)
        
        # 按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        dialog_layout.addWidget(button_box)
        
        dialog.setLayout(dialog_layout)
        
        # 显示对话框
        if dialog.exec_() == QDialog.Accepted:
            # 获取参数
            percent = percent_spin.value()
            
            # 更新规则
            if rule_type == 'best_buy':
                rule['rise_percent'] = percent
            else:
                rule['drop_percent'] = percent
                rule['room_blend_start'] = blend_spin.value()
            
            # 保存并更新图表
            self._save_rules()
            self.update_chart()
            
            print(f"已修改规则百分比: {rule_name} -> {percent}%")

    def _edit_cage_wall_thickness(self, rule):
        """编辑笼子买入/笼子卖出规则的壁厚（元）。壁厚=0 即原逻辑。"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QDialogButtonBox, QDoubleSpinBox, QLabel
        rule_type = rule.get('type')
        rule_name = rule.get('name', '未命名')
        price_low = rule.get('price_low', 0)
        price_high = rule.get('price_high', 0)
        precision = self._get_price_precision()
        step = 10 ** (-precision)
        default_wt = self._default_cage_wall_thickness()
        dialog = QDialog()
        dialog.setWindowTitle(f"编辑壁厚 - {rule_name}")
        dialog_layout = QVBoxLayout()
        form_layout = QFormLayout()
        form_layout.addRow("价格区间:", QLabel(f"[{price_low:.{precision}f}, {price_high:.{precision}f}] 元"))
        wt_spin = QDoubleSpinBox()
        wt_spin.setRange(0.0, 10.0)
        wt_spin.setDecimals(precision)  # ETF=3，股票=2
        wt_spin.setSingleStep(step)
        wt_spin.setSuffix(" 元")
        current_wt = rule.get('wall_thickness', default_wt)
        if current_wt is None:
            current_wt = default_wt
        wt_spin.setValue(float(current_wt))
        wt_spin.setToolTip(
            "壁厚为 0 时等同于原笼子逻辑；大于 0 时有效内区间为 [下限+壁厚, 上限-壁厚]。"
            f"本标的步进 {step:.{precision}f} 元，缺省 {default_wt:.{precision}f} 元。"
        )
        form_layout.addRow("壁厚:", wt_spin)
        dialog_layout.addLayout(form_layout)
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        dialog_layout.addWidget(button_box)
        dialog.setLayout(dialog_layout)
        if dialog.exec_() == QDialog.Accepted:
            rule['wall_thickness'] = round(wt_spin.value(), precision)
            self._save_rules()
            self.update_chart()
            print(f"已修改规则壁厚: {rule_name} -> {rule['wall_thickness']} 元")
    
    def _edit_grid_rule_params(self, rule):
        """编辑网格规则参数（仅网格数量）"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QDialogButtonBox, QSpinBox, QLabel
        from core.trading_rules import RULE_TYPE_NAMES, RuleType
        
        rule_type = rule.get('type')
        rule_name = rule.get('name', '未命名')
        start_price = rule.get('start_price', 0)
        end_price = rule.get('end_price', 0)
        grid_step = rule.get('grid_step', 0.5)
        num_grids = rule.get('num_grids', 2)
        volume_per_grid = rule.get('volume_per_grid', 100)
        
        # 兼容旧数据：如果没有 end_price，根据 start_price 和 grid_step 计算
        if end_price == 0 and start_price > 0:
            end_price = start_price - num_grids * grid_step
            precision = self._get_price_precision()
            rule['end_price'] = round(end_price, precision)
        
        # 创建参数设置对话框
        dialog = QDialog()
        dialog.setWindowTitle(f"设置网格数量 - {rule_name}")
        dialog_layout = QVBoxLayout()
        
        form_layout = QFormLayout()
        
        # 显示价格区间（只读）- 根据股票代码类型确定精度
        precision = self._get_price_precision()
        range_label = QLabel(f"{end_price:.{precision}f} 元 ~ {start_price:.{precision}f} 元")
        form_layout.addRow("价格区间:", range_label)
        
        # 显示每格交易量（只读）
        volume_label = QLabel(f"{volume_per_grid} 股")
        form_layout.addRow("每格交易量:", volume_label)
        
        # 网格数量（可修改）
        grid_num_spin = QSpinBox()
        grid_num_spin.setRange(1, 20)
        grid_num_spin.setValue(num_grids)
        form_layout.addRow("网格数量:", grid_num_spin)
        
        # 价格间距（动态计算，只读）
        step_label = QLabel()
        
        def update_step():
            num = grid_num_spin.value()
            price_range = start_price - end_price
            new_step = price_range / num if num > 0 else 0
            step_label.setText(f"{new_step:.2f} 元")
        
        # 初始化价格间距
        update_step()
        
        # 当网格数量改变时，更新价格间距
        grid_num_spin.valueChanged.connect(update_step)
        
        form_layout.addRow("价格间距:", step_label)
        
        dialog_layout.addLayout(form_layout)
        
        # 按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        dialog_layout.addWidget(button_box)
        
        dialog.setLayout(dialog_layout)
        
        # 显示对话框
        if dialog.exec_() == QDialog.Accepted:
            # 获取参数
            new_num_grids = grid_num_spin.value()
            
            # 重新计算网格间距
            price_range = start_price - end_price
            new_grid_step = price_range / new_num_grids if new_num_grids > 0 else grid_step
            
            # 更新规则
            precision = self._get_price_precision()
            rule['num_grids'] = new_num_grids
            rule['grid_step'] = round(new_grid_step, precision)
            
            # 保存并更新图表
            self._save_rules()
            self.update_chart()
            
            print(f"已修改网格数量: {rule_name} -> {new_num_grids}格 间距{new_grid_step:.2f}元")
    
    def _find_rule_at_position(self, x, y, include_executed=False):
        """查找指定位置的规则
        
        参数:
            x, y: 坐标位置
            include_executed: 是否包含已执行的规则（默认False，拖动时不包含已执行的规则）
        
        返回: (rule, drag_mode, grid_index)
        - 对于单点规则: (rule, 'point', None)
        - 对于笼子规则: (rule, 'low'/'high'/'middle', None)
        - 对于网格规则: (rule, 'grid_low'/'grid_high'/'grid_middle', grid_index)
        
        优先级：单点买卖 > 最佳买卖 > 笼子/网格节点 > 笼子区域
        """
        if not self.rules:
            return None, None, None
        
        # 计算圆点的实际半径（marker_size=100对应面积，半径约10像素）
        # 需要将像素转换为数据单位
        fig = self.price_position_ax.figure
        bbox = self.price_position_ax.get_window_extent().transformed(fig.dpi_scale_trans.inverted())
        width, height = bbox.width, bbox.height
        width_px = width * fig.dpi  # 坐标轴宽度（像素）
        height_px = height * fig.dpi  # 坐标轴高度（像素）
        
        x_range = self.price_position_ax.get_xlim()
        y_range = self.price_position_ax.get_ylim()
        
        # 圆点半径约10像素，转换为数据单位
        # 留一些余量，使用15像素作为检测范围
        marker_radius_px = 15
        x_threshold = (x_range[1] - x_range[0]) * marker_radius_px / width_px
        y_threshold = (y_range[1] - y_range[0]) * marker_radius_px / height_px
        
        # 第一轮：先检查单点买入/卖出规则和夜市规则（最高优先级）
        for rule in self.rules:
            # 如果不包含已执行的规则，则跳过已执行的规则（已执行的规则不能被拖动）
            if not include_executed and rule.get('executed', False):
                continue
            
            rule_type = rule.get('type')
            
            if rule_type in ['single_buy', 'breakthrough_buy', 'single_sell', 'breakthrough_sell', 'night_buy', 'night_sell', 'scheduled_clear']:
                price = rule.get('price', 0)
                volume = rule.get('volume', 0)
                if rule_type in ['single_sell', 'breakthrough_sell', 'night_sell', 'scheduled_clear']:
                    volume = -volume
                
                if abs(x - price) < x_threshold and abs(y - volume) < y_threshold:
                    return (rule, 'point', None)
        
        # 第二轮：检查弹性买入/弹性卖出规则（次优先级）
        for rule in self.rules:
            # 如果不包含已执行的规则，则跳过已执行的规则（已执行的规则不能被拖动）
            if not include_executed and rule.get('executed', False):
                continue
            
            rule_type = rule.get('type')
            
            if rule_type in ['best_buy', 'best_sell']:
                trigger_price = rule.get('trigger_price', 0)
                volume = rule.get('volume', 0)
                if rule_type == 'best_sell':
                    volume = -volume
                
                if abs(x - trigger_price) < x_threshold and abs(y - volume) < y_threshold:
                    return rule, 'point', None
        
        # 第三轮：检查笼子规则和网格规则的节点
        for rule in self.rules:
            # 如果不包含已执行的规则，则跳过已执行的规则（已执行的规则不能被拖动）
            if not include_executed and rule.get('executed', False):
                continue
            
            rule_type = rule.get('type')
            
            if rule_type in ['cage_buy', 'cage_sell']:
                price_low = rule.get('price_low', 0)
                price_high = rule.get('price_high', 0)
                volume = rule.get('volume', 0)
                
                # 确定点的Y坐标
                if rule_type == 'cage_buy':
                    point_y = volume  # 买入的点在上边缘
                else:  # cage_sell
                    point_y = -volume  # 卖出的点在下边缘
                
                # 检查是否点击了下限点
                if abs(x - price_low) < x_threshold and abs(y - point_y) < y_threshold:
                    return rule, 'low', None
                
                # 检查是否点击了上限点
                if abs(x - price_high) < x_threshold and abs(y - point_y) < y_threshold:
                    return rule, 'high', None
            
            elif rule_type == 'grid_buy':
                # 网格买入：检查高价端和低价端
                start_price = rule.get('start_price', 0)  # 高价（右侧）
                end_price = rule.get('end_price', 0)      # 低价（左侧）
                volume_per_grid = rule.get('volume_per_grid', 0)
                grid_step = rule.get('grid_step', 0.5)
                num_grids = rule.get('num_grids', 2)
                
                # 兼容旧数据：如果没有 end_price，根据 start_price 和 grid_step 计算
                if end_price == 0 and start_price > 0:
                    end_price = start_price - num_grids * grid_step
                    precision = self._get_price_precision()
                    rule['end_price'] = round(end_price, precision)  # 更新到规则中
                
                if start_price > 0 and end_price > 0:
                    # 检查高价端（start_price，X轴右侧，索引为0）
                    if abs(x - start_price) < x_threshold and abs(y - volume_per_grid) < y_threshold:
                        return rule, 'grid_high', 0
                    
                    # 检查低价端（end_price，X轴左侧，索引为num_grids）
                    if abs(x - end_price) < x_threshold and abs(y - volume_per_grid) < y_threshold:
                        return rule, 'grid_low', num_grids
                    
                    # 检查中间的网格点（使用比例插值法，与显示逻辑一致）
                    precision = self._get_price_precision()
                    for i in range(1, num_grids):
                        grid_price = start_price - (start_price - end_price) * i / num_grids
                        if abs(x - round(grid_price, precision)) < x_threshold and abs(y - volume_per_grid) < y_threshold:
                            return rule, 'grid_middle', i
            
            elif rule_type == 'grid_sell':
                # 网格卖出：检查高价端和低价端
                start_price = rule.get('start_price', 0)  # 低价（左侧）
                end_price = rule.get('end_price', 0)      # 高价（右侧）
                volume_per_grid = rule.get('volume_per_grid', 0)
                grid_step = rule.get('grid_step', 0.5)
                num_grids = rule.get('num_grids', 2)
                
                # 兼容旧数据：如果没有 end_price，根据 start_price 和 grid_step 计算
                if end_price == 0 and start_price > 0:
                    end_price = start_price + num_grids * grid_step
                    rule['end_price'] = round(end_price, 2)  # 更新到规则中
                
                if start_price > 0 and end_price > 0:
                    # 检查低价端（start_price，X轴左侧，索引为0）
                    if abs(x - start_price) < x_threshold and abs(y - (-volume_per_grid)) < y_threshold:
                        return rule, 'grid_low', 0
                    
                    # 检查高价端（end_price，X轴右侧，索引为num_grids）
                    if abs(x - end_price) < x_threshold and abs(y - (-volume_per_grid)) < y_threshold:
                        return rule, 'grid_high', num_grids
                    
                    # 检查中间的网格点（使用比例插值法，与显示逻辑一致）
                    precision = self._get_price_precision()
                    for i in range(1, num_grids):
                        grid_price = start_price + (end_price - start_price) * i / num_grids
                        if abs(x - round(grid_price, precision)) < x_threshold and abs(y - (-volume_per_grid)) < y_threshold:
                            return rule, 'grid_middle', i
        
        # 第四轮：检查笼子规则的矩形区域（最低优先级）
        for rule in self.rules:
            # 如果不包含已执行的规则，则跳过已执行的规则（已执行的规则不能被拖动）
            if not include_executed and rule.get('executed', False):
                continue
            
            rule_type = rule.get('type')
            
            if rule_type in ['cage_buy', 'cage_sell']:
                price_low = rule.get('price_low', 0)
                price_high = rule.get('price_high', 0)
                volume = rule.get('volume', 0)
                
                # 确定点的Y坐标
                if rule_type == 'cage_buy':
                    point_y = volume  # 买入的点在上边缘
                else:  # cage_sell
                    point_y = -volume  # 卖出的点在下边缘
                
                # 确定矩形的Y轴范围
                if rule_type == 'cage_buy':
                    y_min_rect = 0
                    y_max_rect = volume
                else:  # cage_sell
                    y_min_rect = -volume
                    y_max_rect = 0
                
                # 检查是否点击了矩形区域内
                if price_low <= x <= price_high and y_min_rect <= y <= y_max_rect:
                    return rule, 'middle', None
        
        return None, None, None
    
    def update_price_data(self, time_data, price_data, volume_data):
        """更新价格数据"""
        print(f"[DEBUG] update_price_data被调用 - time_data长度: {len(time_data) if time_data else 0}, price_data长度: {len(price_data) if price_data else 0}")
        self.time_data = time_data
        self.price_data = price_data
        self.volume_data = volume_data
        
        if price_data:
            self.current_price = price_data[-1]
            # 根据股票代码确定价格精度
            precision = SecurityTypeUtil.get_price_precision(self.stock_code)
            self.current_price_label.setText(f"当前价: {self.current_price:.{precision}f}")
            
            # 更新距离显示
            self.update_distance_display()
        
        self.update_chart()
    
    def load_market_data(self, qmt_adapter=None):
        """加载市场数据 - 必须从QMT获取"""
        if qmt_adapter:
            self.load_realtime_data(qmt_adapter)
        else:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(
                None,
                "初始化失败",
                f"QMT适配器未初始化，无法获取 {self.stock_code} ({self.stock_name}) 的行情数据。",
                QMessageBox.Ok
            )
    
    def get_proper_prev_close(self):
        """根据时间计算正确的昨收盘价格（盘后切到今日收盘作次日基准）。"""
        from utils.session_prev_close import resolve_session_prev_close

        last_px = float(getattr(self, "current_price", 0) or 0)
        stored = float(getattr(self, "prev_close_price", 0) or 0)
        return resolve_session_prev_close(
            self.stock_code,
            qmt_last_close=stored,
            last_price=last_px,
        )

    def _ensure_session_prev_close_and_limits(self):
        """REFERENCE_SWITCH 后刷新昨收与涨跌停，避免盘中缓存的跌停仍被夜市/夹价使用。"""
        from utils.trading_day import is_after_reference_switch
        from utils.session_prev_close import resolve_session_prev_close

        last_px = float(getattr(self, "current_price", 0) or 0)
        stored = float(getattr(self, "prev_close_price", 0) or 0)
        try:
            # 任务管理器若已有会话昨收，一并作参考
            tm = None
            if hasattr(self, "qmt_adapter") and getattr(self, "qmt_adapter", None):
                tm = getattr(self.qmt_adapter, "task_manager", None)
            if tm is not None and hasattr(tm, "get_pre_close_price"):
                tm_pc = float(tm.get_pre_close_price(self.stock_code) or 0)
                if tm_pc > 0:
                    stored = tm_pc
            if tm is not None and last_px <= 0:
                last_px = float((getattr(tm, "latest_prices", {}) or {}).get(self.stock_code, 0) or 0)
        except Exception:
            pass

        resolved = resolve_session_prev_close(
            self.stock_code,
            qmt_last_close=stored,
            last_price=last_px,
        )
        if resolved <= 0:
            return

        after_switch = is_after_reference_switch()
        switched = abs(resolved - float(getattr(self, "prev_close_price", 0) or 0)) > 1e-9
        need_limits = (
            switched
            or float(getattr(self, "limit_up_price", 0) or 0) <= 0
            or float(getattr(self, "limit_down_price", 0) or 0) <= 0
        )

        # 盘后：强制用新昨收重算涨跌停（即使数值碰巧相同，也清掉盘中跌停缓存）
        refresh_key = None
        if after_switch:
            from datetime import date as _date

            refresh_key = _date.today()
            if getattr(self, "_limits_refreshed_after_switch_on", None) != refresh_key:
                need_limits = True

        self.prev_close_price = resolved
        if not need_limits:
            return

        if after_switch and refresh_key is not None:
            try:
                self.calculate_key_points(force_recalculate=True)
                if (
                    float(getattr(self, "prev_close_price", 0) or 0) > 0
                    and float(getattr(self, "limit_down_price", 0) or 0) > 0
                ):
                    # 计算器成功后仍对齐会话解析（防止仍被旧日线带偏）
                    if abs(float(self.prev_close_price) - resolved) > 1e-9:
                        self.prev_close_price = resolved
                        lu, ld = self.calculate_limit_prices(self.stock_code, resolved)
                        if lu > 0:
                            self.limit_up_price = lu
                        if ld > 0:
                            self.limit_down_price = ld
                    self._limits_refreshed_after_switch_on = refresh_key
                    return
            except Exception:
                pass

        lu, ld = self.calculate_limit_prices(self.stock_code, resolved)
        if lu > 0:
            self.limit_up_price = lu
        if ld > 0:
            self.limit_down_price = ld
        if after_switch and refresh_key is not None:
            self._limits_refreshed_after_switch_on = refresh_key

    def load_realtime_data(self, qmt_adapter):
        """加载实时行情数据"""
        try:
            # 判断是否是交易日的交易时段
            from datetime import datetime, time as datetime_time
            from utils.trading_day import is_tradeday
            
            now = datetime.now()
            current_time = now.time()
            today = now.date()
            
            is_trading_day = is_tradeday(today)
            # 价格更新时间段：9:25:01-15:00（集合竞价结束后开始更新）
            is_price_update_hours = is_trading_day and datetime_time(9, 25, 1) <= current_time <= datetime_time(15, 0)
            
            realtime_price = 0
            pre_close_price = 0
            
            # 从任务管理器获取实时价格（由QMT行情回调更新）
            if hasattr(qmt_adapter, 'task_manager') and qmt_adapter.task_manager:
                if hasattr(qmt_adapter.task_manager, 'latest_prices'):
                    if self.stock_code in qmt_adapter.task_manager.latest_prices:
                        realtime_price = qmt_adapter.task_manager.latest_prices[self.stock_code]
                        self.update_current_price(realtime_price)
                    else:
                        # 价格还没有，先尝试立即查询一次价格（如果股票已在订阅列表中）
                        # 检查股票是否在订阅列表中
                        is_subscribed = (
                            hasattr(qmt_adapter, "is_subscribed")
                            and qmt_adapter.is_subscribed(self.stock_code)
                        )
                        
                        # 如果时间已经过了9:25:01（集合竞价结束），且股票已在订阅列表中
                        # 等待一小段时间让tick数据到达，然后再检查
                        if is_subscribed and self._is_price_update_time():
                            from PyQt5.QtCore import QTimer
                            # 使用单次定时器，等待500ms后再次检查价格
                            # 这样可以给tick数据回调一点时间到达
                            wait_timer = QTimer()
                            wait_timer.setSingleShot(True)
                            def check_after_wait():
                                # 再次检查价格是否已更新
                                if (hasattr(qmt_adapter.task_manager, 'latest_prices') and 
                                    self.stock_code in qmt_adapter.task_manager.latest_prices):
                                    realtime_price = qmt_adapter.task_manager.latest_prices[self.stock_code]
                                    if realtime_price > 0:
                                        # 价格已就绪，继续执行后续逻辑
                                        self.update_current_price(realtime_price)
                                        # 继续执行后续逻辑（获取昨收盘等）
                                        # 注意：这里不return，让代码继续执行
                            wait_timer.timeout.connect(check_after_wait)
                            wait_timer.start(500)  # 等待500ms
                        
                        # 先获取关键价格点（从key_price_calculator获取昨收盘等数据）
                        # 这样即使QMT价格未就绪，也能正常显示图表
                        self.calculate_key_points(force_recalculate=True)
                        # 先显示图表（可能没有当前价，但至少有价格区间）
                        self.update_chart()
                        
                        # 设置定时器等待价格更新（避免重复创建定时器）
                        from PyQt5.QtCore import QTimer
                        if not hasattr(self, 'retry_timer') or not self.retry_timer.isActive():
                            if hasattr(self, 'retry_timer'):
                                self.retry_timer.stop()
                                self.retry_timer.deleteLater()
                            self.retry_timer = QTimer()
                            self.retry_timer.timeout.connect(lambda: self.check_price_and_update(qmt_adapter))
                            # 如果股票已在订阅列表中，使用更短的检查间隔（300ms），否则使用1秒
                            # 这样可以更快地检测到订阅回调返回的价格数据
                            check_interval = 300 if is_subscribed else 1000
                            self.retry_timer.start(check_interval)
                        return
                else:
                    raise ValueError(f"任务管理器无latest_prices属性")
                
                # 获取昨收盘价格
                # 优先从QMT/会话解析获取，如果没有，后续会从key_price_calculator获取
                if hasattr(qmt_adapter.task_manager, 'pre_close_prices'):
                    if self.stock_code in qmt_adapter.task_manager.pre_close_prices:
                        pre_close_price = qmt_adapter.task_manager.get_pre_close_price(self.stock_code) \
                            if hasattr(qmt_adapter.task_manager, "get_pre_close_price") else \
                            qmt_adapter.task_manager.pre_close_prices[self.stock_code]
                        if pre_close_price > 0:
                            self.prev_close_price = pre_close_price
                            self._ensure_session_prev_close_and_limits()
                else:
                    raise ValueError(f"任务管理器无pre_close_prices属性")
            else:
                raise ValueError(f"QMT适配器的task_manager未初始化")
            
            # 获取持仓量
            if hasattr(qmt_adapter, 'get_stock_position'):
                try:
                    position = qmt_adapter.get_stock_position(self.stock_code)
                    if position and isinstance(position, dict):
                        # 详细记录QMT返回的原始数据
                        volume = position.get('volume', 0)
                        can_use_volume = position.get('can_use_volume', 0)
                        open_price = position.get('open_price', 0)
                        try:
                            self.position_cost = float(open_price or 0)
                        except Exception:
                            self.position_cost = 0.0
                        
                        #self.logger.info(f"[{self.stock_code}] QMT持仓数据 - volume(总持仓)={volume}, can_use_volume={can_use_volume}, open_price={open_price}")
                        
                        # 判断QMT返回的can_use_volume含义
                        if can_use_volume < 0:
                            # can_use_volume为负数，可能表示冻结数量（已委托）
                            # 实际可用 = 总持仓 - |冻结数量|
                            actual_available = volume + can_use_volume  # volume + (-已委托) = 可用
                            self.logger.warning(f"[{self.stock_code}] can_use_volume为负数({can_use_volume})，计算实际可用: {volume} + ({can_use_volume}) = {actual_available}")
                            self.position_volume = max(0, int(actual_available))
                        elif 'can_use_volume' in position:
                            # can_use_volume为正数，直接使用
                            self.position_volume = int(can_use_volume)
                        else:
                            # 没有can_use_volume字段，使用volume
                            self.position_volume = int(volume)
                        
                        # print(f"股票 {self.stock_code} 可用持仓量: {self.position_volume}")
                    else:
                        self.position_volume = 0
                        self.position_cost = 0.0
                        # print(f"股票 {self.stock_code} 没有持仓信息: position={position}")
                except Exception as e:
                    print(f"获取股票 {self.stock_code} 持仓量失败: {str(e)}")
                    self.logger.error(f"[{self.stock_code}] 获取持仓量失败: {str(e)}", exc_info=True)
                    self.position_volume = 0
            else:
                self.position_volume = 0
                print(f"股票 {self.stock_code} qmt_adapter没有get_stock_position方法")
            
            # 获取可用余额
            if hasattr(qmt_adapter, 'cached_asset'):
                try:
                    if qmt_adapter.cached_asset and isinstance(qmt_adapter.cached_asset, dict) and 'cash' in qmt_adapter.cached_asset:
                        self.available_cash = float(qmt_adapter.cached_asset['cash'])
                        # print(f"可用余额: {self.available_cash:.2f}元")
                    else:
                        self.available_cash = 0
                        print(f"没有可用余额信息: cached_asset={qmt_adapter.cached_asset}")
                except Exception as e:
                    print(f"获取可用余额失败: {str(e)}")
                    self.available_cash = 0
            else:
                self.available_cash = 0
                print(f"qmt_adapter没有cached_asset属性")
            
            # 计算关键价格点（只在数据加载时计算一次，绘制时不再检查）
            # 这确保关键价格点只在需要时计算，不会因为频繁绘制而重复计算
            # 注意：如果QMT没有提供昨收盘价，calculate_key_points会从key_price_calculator获取
            # 这确保了即使QMT数据未就绪，也能从key_price_calculator获取昨收盘等关键数据
            self.calculate_key_points(force_recalculate=True)
            
            # 现在不画折线图了，不需要加载tick数据
            # 直接更新图表显示（即使当前价为0，也能显示价格区间和规则点）
            self.update_chart()
            
        except Exception as e:
            print(f"加载实时数据失败: {e}")
            # 即使失败，也显示空图表
            self.update_chart()

    def _refresh_available_cash_before_buy(self) -> float:
        """下单前刷新可用现金：优先实时查 QMT 资产，失败则回退缓存。"""
        latest_cash = 0.0
        try:
            qmt_adapter = self.task_manager.qmt_adapter if hasattr(self, 'task_manager') and self.task_manager else None
            if qmt_adapter and hasattr(qmt_adapter, 'xt_trader') and getattr(qmt_adapter, 'xt_trader', None):
                try:
                    asset = qmt_adapter.xt_trader.query_stock_asset(qmt_adapter.account)
                    if asset and hasattr(asset, 'cash'):
                        latest_cash = float(asset.cash or 0)
                        # 同步缓存，避免后续 UI 仍显示旧值
                        if hasattr(qmt_adapter, 'cached_asset') and isinstance(qmt_adapter.cached_asset, dict):
                            qmt_adapter.cached_asset['cash'] = latest_cash
                        if hasattr(qmt_adapter, 'task_manager') and qmt_adapter.task_manager:
                            if hasattr(qmt_adapter.task_manager, 'cached_asset') and isinstance(qmt_adapter.task_manager.cached_asset, dict):
                                qmt_adapter.task_manager.cached_asset['cash'] = latest_cash
                except Exception:
                    pass
            if latest_cash <= 0:
                # 回退到已缓存资产
                if qmt_adapter and hasattr(qmt_adapter, 'cached_asset') and isinstance(qmt_adapter.cached_asset, dict):
                    latest_cash = float(qmt_adapter.cached_asset.get('cash') or 0)
        except Exception:
            latest_cash = 0.0
        if latest_cash > 0:
            self.available_cash = latest_cash
        return float(self.available_cash or 0)
    
    def check_price_and_update(self, qmt_adapter):
        """检查价格是否已经更新"""
        try:
            if hasattr(qmt_adapter, 'task_manager') and qmt_adapter.task_manager:
                if hasattr(qmt_adapter.task_manager, 'latest_prices'):
                    if self.stock_code in qmt_adapter.task_manager.latest_prices:
                        realtime_price = qmt_adapter.task_manager.latest_prices[self.stock_code]
                        if realtime_price > 0:
                            # 价格已就绪，停止定时器并更新
                            if hasattr(self, 'retry_timer'):
                                self.retry_timer.stop()
                            self.load_realtime_data(qmt_adapter)
        except:
            pass
    
    def update_current_price(self, price):
        """更新当前价格（带节流和去重绘制）"""
        import time as _time
        _t0 = _time.time()

        # 对价格进行精度处理，确保与涨停板价格使用相同的精度
        precision = self._get_price_precision()
        new_price = round(price, precision)

        # 如果价格在当前精度下没有变化，直接返回，避免无意义重绘
        if hasattr(self, 'current_price') and getattr(self, 'current_price', None) == new_price:
            # 仅在首次初始化后才认为是“没变化”
            return

        self.current_price = new_price

        # 始终及时更新文本标签（代价很小）
        if hasattr(self, 'current_price_label'):
            self.current_price_label.setText(f"当前价: {self.current_price:.{precision}f}")
        
        # 更新距离显示（当前为空实现，保留调用以兼容）
        self.update_distance_display()
        
        # 对价格位置图的重绘做节流；多列同屏时略增大间隔，减轻主线程压力（单列仍 200ms）
        now_ts = _time.time()
        if not hasattr(self, '_last_price_draw_time'):
            self._last_price_draw_time = 0.0
        cols = getattr(self, 'current_columns', None) or 1
        if cols >= 4:
            min_interval = 0.35
        elif cols == 3:
            min_interval = 0.30
        elif cols == 2:
            min_interval = 0.25
        else:
            min_interval = 0.2
        if now_ts - self._last_price_draw_time >= min_interval:
            self._last_price_draw_time = now_ts
            self.draw_price_position_chart()
            elapsed = _time.time() - _t0
            # 主线程 Matplotlib 重绘常 >50ms，50ms 阈值会产生大量误报；仅对明显卡顿记 WARNING
            if elapsed > 0.5 and hasattr(self, 'logger'):
                self.logger.warning(
                    f"[性能监控] stock_chart update_current_price {getattr(self, 'stock_code', '')} 耗时: {elapsed:.3f}秒"
                )
    
    def _toggle_blink(self):
        """已移除闪烁逻辑，改用固定红色显示以提升性能"""
        # 此方法已不再使用，保留仅为了兼容性（如果有其他地方调用）
        pass

    def _sync_rules_from_task_manager(self):
        """从 TaskManager 同步最新 rules（延迟激活等集中调度会更新任务文件）。"""
        task = getattr(self, "task", None)
        if not task or not getattr(self, "task_manager", None):
            return
        task_id = task.get("task_id")
        if not task_id:
            return
        fresh = self.task_manager.tasks.get(task_id)
        if not fresh or not isinstance(fresh.get("params"), dict):
            return
        fresh_rules = fresh.get("params", {}).get("rules")
        if isinstance(fresh_rules, list):
            self.rules = fresh_rules
            self.task["params"] = fresh.get("params", self.task.get("params"))
    
    def on_tick_data(self, tick_data):
        """
        处理tick数据
        tick_data格式: {
            'stock_code': str,
            'lastPrice': float,
            'lastClose': float,
            'open': float,  # 今开盘
            'high': float,  # 今日最高
            'low': float,   # 今日最低
            'askPrice': list,
            'bidPrice': list,
            'askVol': list,
            'bidVol': list,
            'time': datetime
        }
        """
        import time as _time
        _t0 = _time.time()
        # 保存最新的tick数据（用于定时清仓计算滑点）
        self._last_tick_data = tick_data
        
        # 更新当前价格显示
        current_price = tick_data.get('lastPrice', 0)
        if current_price > 0:
            self._maybe_reset_true_breakthrough_state_for_tick(tick_data)
            self.update_current_price(current_price)
        
        # 在价格更新时间段（9:25:01-15:00）实时更新今日最高、最低、开盘
        # 集合竞价在9:25:00结束，此时已有开盘价，所以从9:25:01开始更新
        # 注意：这部分更新应该在检查task_paused之前执行，确保即使任务暂停也能更新显示
        if self._is_price_update_time():
            today_open = float(tick_data.get('open', 0) or 0)
            today_high = float(tick_data.get('high', 0) or 0)
            today_low = float(tick_data.get('low', 0) or 0)
            last_px = float(current_price or 0)
            # 拒绝集合竞价虚拟跌停价污染：官方 low 远低于开盘且现价已回开盘附近时，改用成交轨迹
            if (
                today_open > 0
                and today_low > 0
                and today_low + 1e-9 < today_open * 0.92
                and last_px + 1e-9 >= today_open * 0.98
            ):
                today_low = 0.0
            # 无可靠官方 low 时，用开盘/现价维护最低（与 results 快照逻辑一致）
            if today_low <= 0 and today_open > 0 and last_px > 0:
                today_low = min(today_open, last_px)
            elif today_low <= 0 and last_px > 0:
                today_low = last_px
            if today_high <= 0 and today_open > 0 and last_px > 0:
                today_high = max(today_open, last_px)
            elif today_high <= 0 and last_px > 0:
                today_high = last_px
            precision = self._get_price_precision()
            min_tick = 10 ** (-precision)
            
            # 更新key_points中的今日最高、最低、开盘（仅当值有效且发生变化时）
            if today_open > 0 or today_high > 0 or today_low > 0:
                updated = False
                for i, (name, price) in enumerate(self.key_points):
                    if name == '今开盘' and today_open > 0:
                        if abs(price - today_open) > min_tick * 0.5:
                            self.key_points[i] = (name, round(today_open, precision))
                            updated = True
                    elif name == '今日最高' and today_high > 0:
                        # 只抬高，避免脏数据回退
                        if today_high > float(price or 0) + min_tick * 0.5:
                            self.key_points[i] = (name, round(today_high, precision))
                            updated = True
                    elif name == '今日最低' and today_low > 0:
                        old = float(price or 0)
                        # 污染纠偏：缓存最低远低于开盘且新 low 更合理时允许抬升
                        if today_low + min_tick * 0.5 < old:
                            self.key_points[i] = (name, round(today_low, precision))
                            updated = True
                        elif (
                            today_open > 0
                            and old + 1e-9 < today_open * 0.92
                            and today_low + 1e-9 >= min(today_open, last_px or today_open) - 1e-6
                            and today_low > old + min_tick * 0.5
                        ):
                            self.key_points[i] = (name, round(today_low, precision))
                            updated = True
                
                # 如果key_points中还没有这些项，且数据有效，则添加它们
                key_point_names = [name for name, _ in self.key_points]
                if today_open > 0 and '今开盘' not in key_point_names:
                    self.key_points.append(('今开盘', round(today_open, precision)))
                    updated = True
                if today_high > 0 and '今日最高' not in key_point_names:
                    self.key_points.append(('今日最高', round(today_high, precision)))
                    updated = True
                if today_low > 0 and '今日最低' not in key_point_names:
                    self.key_points.append(('今日最低', round(today_low, precision)))
                    updated = True
                
                # 如果更新了key_points，按价格重新排序（从高到低）
                if updated:
                    # 按价格从高到低排序
                    self.key_points.sort(key=lambda x: x[1] if isinstance(x[1], (int, float)) else float('-inf'), reverse=True)
                    self.draw_price_position_chart()
        
        # 如果任务已暂停，不执行交易规则（定时清仓由 scheduled_clear_manager 独立调度）
        if self.task_paused:
            if _time.time() - _t0 > 0.5 and hasattr(self, 'logger'):
                self.logger.warning(f"[性能监控] stock_chart on_tick_data {getattr(self, 'stock_code', '')} 耗时: {_time.time() - _t0:.3f}秒 (已暂停)")
            if current_price > 0:
                self._advance_true_breakthrough_tick_state(tick_data)
                self._last_tick_price = float(current_price)
            return
        
        # 定时清仓 tick 触发已由 scheduled_clear_manager 集中处理
        
        # 如果任务正在运行且未暂停
        if self.task_running and not self.task_paused:
            self._sync_rules_from_task_manager()
        # 提前下单：按规则快照独立判断（全局开关仅作缺字段回退 / 新单默认）
        self._update_early_orders_status(tick_data)
        self._check_early_orders(tick_data)
        # 智能卖出：先处理已启动的会话（改价/强平/成交检测）
        if hasattr(self, 'smart_sell_runner'):
            self.smart_sell_runner.on_tick(tick_data)
            self._process_breakthrough_probe_confirmations(tick_data)
            # 然后触发规则检查
            self._check_and_execute_rules(tick_data)
        
        if current_price > 0:
            self._advance_true_breakthrough_tick_state(tick_data)
            self._last_tick_price = float(current_price)
        
        if _time.time() - _t0 > 0.5 and hasattr(self, 'logger'):
            self.logger.warning(f"[性能监控] stock_chart on_tick_data {getattr(self, 'stock_code', '')} 耗时: {_time.time() - _t0:.3f}秒")
        
    def update_distance_display(self):
        """更新距离显示（已废弃，保留空方法以兼容）"""
        pass
    
    def _check_and_execute_rules(self, tick_data):
        """
        检查并执行规则
        执行顺序：
        1. 先执行所有卖出规则（回收资金）
        2. 再执行所有买入规则（使用资金）
        优先级：单点 > 弹性 > 笼子 > 网格
        """
        current_price = tick_data.get('lastPrice', 0)
        if current_price <= 0:
            return
        
        # TODO: 检查是否在交易时间内（调试阶段暂时注释，方便非交易时段测试）
        # if not self._is_trading_time():
        #     return
        
        # 规则类型优先级
        rule_type_priority = {
            'single_sell': 1,
            'breakthrough_sell': 1,  # 与单点卖出同优先级
            'best_sell': 2,
            'cage_sell': 3,
            'grid_sell': 4,
            'single_buy': 5,
            'breakthrough_buy': 5,  # 与单点买入同优先级
            'best_buy': 6,
            'cage_buy': 7,
            'grid_buy': 8,
        }
        
        from core.rule_activation import rule_activation_allows_trigger

        # 按优先级排序规则（排除夜市规则、已执行的规则，夜市规则有自己的定时器）
        sorted_rules = sorted(
            [
                r for r in self.rules
                if r.get('enabled', True)
                and not r.get('executed', False)
                and r.get('type') not in ['night_buy', 'night_sell']
                and rule_activation_allows_trigger(r)
            ],
            key=lambda r: rule_type_priority.get(r.get('type', ''), 999)
        )
        
        # 检查并执行规则
        for rule in sorted_rules:
            rule_type = rule.get('type')
            
            # 检查规则是否触发
            triggered, trade_info = self._check_rule_trigger(rule, tick_data)
            
            if triggered:
                # 执行交易
                self._execute_trade(rule, trade_info, tick_data)
    
    def _is_price_update_time(self):
        """检查当前是否在价格更新时间段内（9:25:01-15:00）
        集合竞价在9:25:00结束，此时已有开盘价，所以从9:25:01开始更新价格
        """
        from datetime import datetime, time
        
        # 获取当前时间
        now = datetime.now()
        current_time = now.time()
        
        # 价格更新时间段
        # 上午：9:25:01-11:30（集合竞价结束后开始更新）
        # 下午：13:00-15:00
        morning_start = time(9, 25, 1)  # 9:25:01
        morning_end = time(11, 30)
        afternoon_start = time(13, 0)
        afternoon_end = time(15, 0)
        
        # 判断是否在价格更新时间段内
        is_morning = morning_start <= current_time <= morning_end
        is_afternoon = afternoon_start <= current_time <= afternoon_end
        
        return is_morning or is_afternoon
    
    def _is_trading_time(self):
        """检查当前是否在交易时间内（9:30-15:00）
        用于交易规则触发判断，实际交易时段从9:30开始
        """
        from datetime import datetime, time
        
        # 获取当前时间
        now = datetime.now()
        current_time = now.time()
        
        # 交易时间段
        # 上午：9:30-11:30
        # 下午：13:00-15:00
        morning_start = time(9, 30)
        morning_end = time(11, 30)
        afternoon_start = time(13, 0)
        afternoon_end = time(15, 0)
        
        # 判断是否在交易时间内
        is_morning = morning_start <= current_time <= morning_end
        is_afternoon = afternoon_start <= current_time <= afternoon_end
        
        return is_morning or is_afternoon
    
    def _check_rule_trigger(self, rule, tick_data):
        """
        检查规则是否触发
        返回: (是否触发, 交易信息字典)
        """
        from datetime import datetime
        
        # 如果规则已执行，直接返回，不再检查
        if rule.get('executed', False):
            return False, None

        if (rule.get('smart_sell_active') or (rule.get('smart_sell') or {}).get('active')):
            return False, None
        if (rule.get('breakthrough_probe') or {}).get('active'):
            return False, None

        from core.rule_activation import rule_activation_allows_trigger
        if not rule_activation_allows_trigger(rule):
            return False, None
        
        rule_type = rule.get('type')
        current_price = tick_data.get('lastPrice', 0)
        
        # ⚠️ 提前下单的规则不应该在这里触发，应该在_check_early_orders中处理
        if rule.get('early_order', False):
            return False, None  # 提前下单的规则跳过常规触发检查

        # builtin：单点/突破买卖交由大 QMT 内置策略，图表不再走 xt_trader
        if rule_type in (
            "single_buy",
            "single_sell",
            "breakthrough_buy",
            "breakthrough_sell",
            "best_sell",
            "best_buy",
            "cage_buy",
            "cage_sell",
            "grid_buy",
            "grid_sell",
        ):
            try:
                from utils.qmt_execution_config import use_builtin_order_execution

                if use_builtin_order_execution():
                    return False, None
            except Exception:
                pass
        
        # 单点买入
        if rule_type == 'single_buy':
            trigger_price = rule.get('price', 0)
            volume = rule.get('volume', 0)
            
            if current_price <= trigger_price and volume > 0:
                return True, {
                    'type': 'buy',
                    'price': current_price,
                    'volume': volume,
                    'reason': '单点买入触发'
                }
        
        # 单点卖出
        elif rule_type == 'single_sell':
            trigger_price = rule.get('price', 0)
            volume = rule.get('volume', 0)
            
            if current_price >= trigger_price and volume > 0:
                return True, {
                    'type': 'sell',
                    'price': current_price,
                    'volume': volume,
                    'reason': '单点卖出触发'
                }
        
        # 突破买入（普通/真突破均在「展示价首次上穿触发价」的 tick 判定，与回测/breakbuycheck 一致）
        # 若含 band_low/high：走价格带硬pass（深位或卖一>MA5作废）
        elif rule_type == 'breakthrough_buy':
            from core.price_band_buy import rule_has_price_band

            if rule_has_price_band(rule):
                return self._check_band_breakthrough_buy(rule, tick_data, current_price)

            from core.breakthrough_probe_buy import (
                can_use_probe_mode,
                can_start_rearm_add_confirm,
                init_rearm_add_confirm_state,
                is_past_rearm_add_cutoff,
                make_rearm_meta,
                MAX_REARM_ADD_ATTEMPTS,
                split_probe_volumes,
            )

            trigger_price = rule.get('price', 0)
            volume = rule.get('volume', 0)
            tick_dt = tick_data.get('time')
            if not isinstance(tick_dt, datetime):
                tick_dt = datetime.now()

            if is_past_rearm_add_cutoff(tick_dt):
                rearm_cut = rule.get('breakthrough_probe_rearm') or {}
                if rearm_cut.get('await_rearm'):
                    self._finalize_breakthrough_rearm_at_cutoff(rule, tick_data)
                return False, None

            if volume <= 0:
                return False, None

            require_break_below = self._rule_require_break_below(rule)
            if require_break_below and not rule.get("break_below_trigger_done"):
                if self._is_breakthrough_break_below_tick(tick_data, trigger_price):
                    rule["break_below_trigger_done"] = True
                    self._save_rules()

            if not self._is_breakthrough_buy_price_cross_tick(
                tick_data, trigger_price
            ):
                return False, None

            if require_break_below and not rule.get("break_below_trigger_done"):
                prev = self._last_tick_price
                lp = float(tick_data.get("lastPrice") or 0)
                trig = float(trigger_price or 0)
                from_below_cross = (
                    prev is not None
                    and float(prev) <= trig
                    and lp > trig
                )
                if not from_below_cross:
                    return False, None

            rearm = rule.get('breakthrough_probe_rearm')
            if not isinstance(rearm, dict):
                rearm = make_rearm_meta(
                    trigger_price=float(trigger_price or 0),
                    planned_volume=int(volume or 0),
                )
                rule['breakthrough_probe_rearm'] = rearm

            probe_enabled = self._load_breakthrough_probe_enabled()
            require_tb = self._rule_require_true_breakthrough(rule)

            if probe_enabled:
                min_buy_amount = self._load_min_buy_amount()
                if can_use_probe_mode(volume, current_price, min_buy_amount):
                    if (
                        rearm.get('await_rearm')
                        and rearm.get('probe_bought')
                        and int(rearm.get('remain_pending') or 0) > 0
                        and can_start_rearm_add_confirm(rearm, tick_dt)
                    ):
                        avg_before = (
                            float(self._tb_prefix_sum) / float(self._tb_prefix_cnt)
                            if int(self._tb_prefix_cnt or 0) > 0
                            else 0.0
                        )
                        rearm['await_rearm'] = False
                        state = init_rearm_add_confirm_state(
                            rearm,
                            code=self._code6_for_probe(),
                            break_tick_dt=tick_dt,
                            avg_vol_before=avg_before,
                        )
                        rule['breakthrough_probe'] = state
                        rule['breakthrough_probe_active'] = True
                        self.logger.info(
                            f"[{self.stock_code}] 强突破再次上穿，开启补买确认窗 "
                            f"（已失败{rearm.get('rearm_failed_attempts', 0)}次/最多{MAX_REARM_ADD_ATTEMPTS}次）"
                            f" 待补{rearm.get('remain_pending')}股"
                        )
                        return False, None

                    tb_detail = ""
                    tb_metrics: dict = {}
                    if require_tb:
                        tb_ok, tb_msg, tb_detail, tb_metrics = self._evaluate_true_breakthrough_live(
                            tick_data, trigger_price
                        )
                        if not tb_ok:
                            self._finish_breakthrough_buy_not_true_breakthrough(
                                rule, tick_data, tb_msg, tb_detail
                            )
                            return False, None
                    probe_v, remain_v = split_probe_volumes(volume)
                    reason_core = (
                        f"试探仓20%: {probe_v}股"
                        f"（规则{volume}股，待确认补买{remain_v}股）"
                    )
                    if require_tb and tb_detail:
                        reason = f"突破买入(真突破+试探): {tb_detail}; {reason_core}"
                    else:
                        reason = f"突破买入试探仓20%: {reason_core}"
                    trade_info = {
                        'type': 'buy',
                        'price': current_price,
                        'volume': probe_v,
                        'reason': reason,
                        'breakthrough_probe_keep_active': True,
                        'breakthrough_probe_remain': remain_v,
                        'breakthrough_probe_planned': volume,
                        'breakthrough_probe_phase': 'probe',
                        'breakthrough_probe_tb_vol_ratio': float(
                            (tb_metrics or {}).get('ratio_cond1') or 0
                        ),
                        'breakthrough_probe_tb_passed': bool(require_tb),
                    }
                    if tb_detail:
                        trade_info['true_breakthrough_detail'] = tb_detail
                    rearm.update({
                        'probe_bought': True,
                        'probe_filled': probe_v,
                        'remain_pending': remain_v,
                        'planned_volume': volume,
                        'trigger_price': float(trigger_price or 0),
                        'tb_vol_ratio': float((tb_metrics or {}).get('ratio_cond1') or 0),
                        'true_breakthrough_passed': bool(require_tb),
                        'await_rearm': False,
                    })
                    return True, trade_info

            if require_tb:
                tb_ok, tb_msg, tb_detail, _tb_metrics = self._evaluate_true_breakthrough_live(
                    tick_data, trigger_price
                )
                if not tb_ok:
                    self._finish_breakthrough_buy_not_true_breakthrough(
                        rule, tick_data, tb_msg, tb_detail
                    )
                    return False, None
                reason = f"突破买入(真突破): {tb_detail}"
                return True, {
                    'type': 'buy',
                    'price': current_price,
                    'volume': volume,
                    'reason': reason,
                    'true_breakthrough_detail': tb_detail,
                    'true_breakthrough_passed': True,
                }
            else:
                reason = "突破买入触发"
            return True, {
                'type': 'buy',
                'price': current_price,
                'volume': volume,
                'reason': reason,
            }
        
        # 突破卖出
        elif rule_type == 'breakthrough_sell':
            trigger_price = rule.get('price', 0)
            volume = rule.get('volume', 0)
            
            if current_price < trigger_price and volume > 0:
                return True, {
                    'type': 'sell',
                    'price': current_price,
                    'volume': volume,
                    'reason': '突破卖出触发'
                }
        
        # 笼子买入
        elif rule_type == 'cage_buy':
            price_low = rule.get('price_low', 0)
            price_high = rule.get('price_high', 0)
            inner_low, inner_high = self._get_cage_inner_bounds(rule)
            volume = rule.get('volume', 0)
            cage_entered = rule.get('cage_entered', False)  # 是否已经进入过笼子
            
            if volume > 0:
                # 检查当前价是否在有效内区间（考虑壁厚）
                if inner_low < current_price < inner_high:
                    # 如果还没有标记为已进入，现在标记为已进入
                    if not cage_entered:
                        rule['cage_entered'] = True
                        # 延迟保存，避免频繁保存导致阻塞（在触发时会统一保存）
                # 如果已经进入过笼子，检查是否突破上下限
                elif cage_entered:
                    # 跌破内下沿，或突破外上沿时触发
                    if current_price <= inner_low or current_price >= price_high:
                        trigger_point = "下限" if current_price <= inner_low else "上限"
                        trigger_price = inner_low if current_price <= inner_low else price_high
                        executed_endpoint = 'low' if current_price <= inner_low else 'high'
                        
                        return True, {
                            'type': 'buy',
                            'price': current_price,
                            'volume': volume,
                            'reason': '笼子买入触发',
                            'executed_endpoint': executed_endpoint  # 记录触发的端点：'low' 或 'high'
                        }
        
        # 笼子卖出
        elif rule_type == 'cage_sell':
            price_low = rule.get('price_low', 0)
            price_high = rule.get('price_high', 0)
            inner_low, inner_high = self._get_cage_inner_bounds(rule)
            volume = rule.get('volume', 0)
            cage_entered = rule.get('cage_entered', False)  # 是否已经进入过笼子
            
            if volume > 0:
                # 检查当前价是否在有效内区间（考虑壁厚）
                if inner_low < current_price < inner_high:
                    # 如果还没有标记为已进入，现在标记为已进入
                    if not cage_entered:
                        rule['cage_entered'] = True
                        # 延迟保存，避免频繁保存导致阻塞（在触发时会统一保存）
                # 如果已经进入过笼子，检查是否突破上下限
                elif cage_entered:
                    # 突破外下沿，或跌破内上沿时触发
                    if current_price <= price_low or current_price >= inner_high:
                        trigger_point = "下限" if current_price <= price_low else "上限"
                        trigger_price = price_low if current_price <= price_low else inner_high
                        executed_endpoint = 'low' if current_price <= price_low else 'high'
                        
                        return True, {
                            'type': 'sell',
                            'price': current_price,
                            'volume': volume,
                            'reason': '笼子卖出触发',
                            'executed_endpoint': executed_endpoint  # 记录触发的端点：'low' 或 'high'
                        }
        
        # 网格买入
        elif rule_type == 'grid_buy':
            start_price = rule.get('start_price', 0)
            end_price = rule.get('end_price', 0)
            num_grids = rule.get('num_grids', 2)
            volume_per_grid = rule.get('volume_per_grid', 100)
            
            # 获取已执行的网格索引列表
            executed_grids = rule.get('executed_grids', [])
            
            # 计算所有网格价格点（从高到低）
            grid_prices = []
            for i in range(num_grids + 1):
                # 首尾两个点使用精确值，中间点用比例插值法
                if i == 0:
                    grid_price = start_price  # 高价端
                elif i == num_grids:
                    grid_price = end_price    # 低价端
                else:
                    precision = self._get_price_precision()
                    grid_price = round(start_price - (start_price - end_price) * i / num_grids, precision)
                grid_prices.append((i, grid_price))
            
            # 检查当前价格是否触及任意未执行的网格点
            for grid_index, grid_price in grid_prices:
                if grid_index in executed_grids:
                    continue  # 该网格已执行，跳过
                
                # 网格买入：价格 <= 网格价格时触发
                if current_price <= grid_price:
                    return True, {
                        'type': 'buy',
                        'price': current_price,
                        'volume': volume_per_grid,
                        'reason': '网格买入触发',
                        'grid_index': grid_index  # 记录触发的网格索引
                    }
        
        # 网格卖出
        elif rule_type == 'grid_sell':
            start_price = rule.get('start_price', 0)
            end_price = rule.get('end_price', 0)
            num_grids = rule.get('num_grids', 2)
            volume_per_grid = rule.get('volume_per_grid', 100)
            
            # 获取已执行的网格索引列表
            executed_grids = rule.get('executed_grids', [])
            
            # 计算所有网格价格点（从低到高）
            grid_prices = []
            for i in range(num_grids + 1):
                # 首尾两个点使用精确值，中间点用比例插值法
                if i == 0:
                    grid_price = start_price  # 低价端
                elif i == num_grids:
                    grid_price = end_price    # 高价端
                else:
                    precision = self._get_price_precision()
                    grid_price = round(start_price + (end_price - start_price) * i / num_grids, precision)
                grid_prices.append((i, grid_price))
            
            # 检查当前价格是否触及任意未执行的网格点
            for grid_index, grid_price in grid_prices:
                if grid_index in executed_grids:
                    continue  # 该网格已执行，跳过
                
                # 网格卖出：价格 >= 网格价格时触发
                if current_price >= grid_price:
                    return True, {
                        'type': 'sell',
                        'price': current_price,
                        'volume': volume_per_grid,
                        'reason': '网格卖出触发',
                        'grid_index': grid_index  # 记录触发的网格索引
                    }
        
        # 弹性买入
        elif rule_type == 'best_buy':
            trigger_price = rule.get('trigger_price', 0)
            rise_percent = rule.get('rise_percent', 0.3)
            volume = rule.get('volume', 0)
            rule_name = rule.get('name', '未命名规则')
            
            if volume <= 0:
                self.logger.debug(f"[{self.stock_code}] {rule_name} - 弹性买入：volume={volume}，跳过检查")
                return False, None
            
            # 获取状态：是否已触发，最低价
            triggered = rule.get('triggered', False)
            lowest_price = rule.get('lowest_price', None)
            # 防抖/冷却（按 tick 次数）：tick 若是 3 秒一跳，用“秒”防抖没有意义。
            # confirm_ticks: 连续满足条件多少个 tick 才确认（默认 2）
            # cooldown_ticks: 创新低后的多少个 tick 内不允许确认（默认 1）
            # 优先级：rule 自定义 > config.ini 全局 > 代码兜底
            # 注意：允许显式设置 0（0=关闭确认/冷却），不能用 `or` 回退
            cfg_confirm, cfg_cooldown, cfg_dyn = self._load_elastic_confirm_config()
            _r_confirm = rule.get("confirm_ticks", None)
            _r_cool = rule.get("cooldown_after_extreme_ticks", None)
            confirm_ticks = int(cfg_confirm) if _r_confirm is None else int(_r_confirm)
            cooldown_ticks = int(cfg_cooldown) if _r_cool is None else int(_r_cool)
            if confirm_ticks < 0:
                confirm_ticks = 2
            if confirm_ticks == 0:
                confirm_ticks = 1
            if cooldown_ticks < 0:
                cooldown_ticks = 0
            # 与回测 simulator 一致：每条行情评估先递增，保证极值 tick 与冷却计算有效（否则 tick_idx 长期为 0，lowest_tick_idx/highest_tick_idx 也为 0，导致 `if idx and` 跳过冷却）
            rule["tick_idx"] = int(rule.get("tick_idx") or 0) + 1

            # 如果价格跌破触发价，开始追踪最低价
            if current_price < trigger_price:
                if not triggered:
                    # 首次跌破触发价，开始追踪
                    rule['triggered'] = True
                    rule['lowest_price'] = current_price
                    rule['lowest_tick_idx'] = int(rule.get("tick_idx") or 0)
                    rule.pop('rebound_hit_count', None)
                    self._save_rules()
                    self.logger.info(f"[{self.stock_code}] {rule_name} - 弹性买入：价格{current_price:.2f}跌破触发价{trigger_price:.2f}，开始追踪最低价")
                    return False, None  # 刚触发，等待反弹
                else:
                    # 已触发，继续追踪更低的价格
                    if lowest_price is None or current_price < lowest_price:
                        rule['lowest_price'] = current_price
                        rule['lowest_tick_idx'] = int(rule.get("tick_idx") or 0)
                        rule.pop('rebound_hit_count', None)
                        self._save_rules()
                        self.logger.debug(f"[{self.stock_code}] {rule_name} - 弹性买入：价格创新低{current_price:.2f}，更新最低价")
                        return False, None  # 创出新低，继续等待反弹
                    # 如果价格没有创新低，继续检查反弹条件（不能直接返回）
            
            # 如果已经触发，检查是否满足反弹条件
            if triggered and lowest_price is not None and lowest_price > 0:
                # 计算反弹目标价：最低价 * (1 + 反弹百分比/100)
                # 动态反弹阈值：跌得越深，允许更“松”的反弹阈值（减少快速下跌后的小反弹误触发）
                # rule可选参数：rise_scale（默认0.35）、max_rise_percent（默认4.0）
                try:
                    drop_from_trigger_pct = max(0.0, (trigger_price / lowest_price - 1.0) * 100.0) if trigger_price and lowest_price else 0.0
                except Exception:
                    drop_from_trigger_pct = 0.0
                rise_scale = float(rule.get("rise_scale") or 0.35)
                max_rise = float(rule.get("max_rise_percent") or 4.0)
                if int(cfg_dyn) <= 0:
                    eff_rise = float(rise_percent)
                else:
                    eff_rise = min(max_rise, float(rise_percent) + drop_from_trigger_pct * rise_scale)
                target_price = lowest_price * (1 + eff_rise / 100.0)
                
                # 只有当价格从最低价反弹到目标价时才触发买入
                # 即：当前价必须 >= 目标价，且当前价必须 > 最低价（说明已经从最低价反弹了）
                # 冷却：创新低后的若干个 tick 内不确认反弹
                lowest_idx = int(rule.get("lowest_tick_idx") or 0)
                if lowest_idx > 0 and (int(rule["tick_idx"]) - lowest_idx) <= cooldown_ticks:
                    return False, None

                hit = (current_price >= target_price and current_price > lowest_price)
                if hit:
                    cnt = int(rule.get("rebound_hit_count") or 0) + 1
                    rule["rebound_hit_count"] = cnt
                    self._save_rules()
                    if cnt < confirm_ticks:
                        return False, None
                    self.logger.info(f"[{self.stock_code}] {rule_name} - 弹性买入：价格从最低价{lowest_price:.2f}反弹到{current_price:.2f}（目标价{target_price:.2f}），触发买入{volume}股")
                    return True, {
                        'type': 'buy',
                        'price': current_price,
                        'volume': volume,
                        'reason': '弹性买入触发'
                    }
                else:
                    # 未命中：清空连续命中计数
                    if rule.get("rebound_hit_count"):
                        rule.pop("rebound_hit_count", None)
                        self._save_rules()
                    # 添加调试日志，帮助排查问题
                    if current_price > lowest_price:
                        rule_id = rule.get("id", rule_name)
                        self._log_throttled(
                            key=f"{self.stock_code}:best_buy:not_rebound:{rule_id}",
                            message=f"[{self.stock_code}] {rule_name} - 弹性买入：价格{current_price:.2f} > 最低价{lowest_price:.2f}，目标价{target_price:.2f}，尚未达到反弹条件",
                            level="debug",
                            interval_s=30.0,
                        )
            
            return False, None
        
        # 弹性卖出
        elif rule_type == 'best_sell':
            trigger_price = rule.get('trigger_price', 0)
            drop_percent = rule.get('drop_percent', 0.3)
            volume = rule.get('volume', 0)
            rule_name = rule.get('name', '未命名规则')
            
            # 获取状态：是否已触发，最高价
            triggered = rule.get('triggered', False)
            highest_price = rule.get('highest_price', None)
            # 防抖/冷却（按 tick 次数）：tick 若是 3 秒一跳，用“秒”防抖没有意义。
            # 优先级：rule 自定义 > config.ini 全局 > 代码兜底
            # 注意：允许显式设置 0（0=关闭确认/冷却），不能用 `or` 回退
            cfg_confirm, cfg_cooldown, cfg_dyn = self._load_elastic_confirm_config()
            _r_confirm = rule.get("confirm_ticks", None)
            _r_cool = rule.get("cooldown_after_extreme_ticks", None)
            confirm_ticks = int(cfg_confirm) if _r_confirm is None else int(_r_confirm)
            cooldown_ticks = int(cfg_cooldown) if _r_cool is None else int(_r_cool)
            if confirm_ticks < 0:
                confirm_ticks = 2
            if confirm_ticks == 0:
                confirm_ticks = 1
            if cooldown_ticks < 0:
                cooldown_ticks = 0
            rule["tick_idx"] = int(rule.get("tick_idx") or 0) + 1

            # 如果价格突破触发价，开始追踪最高价
            if current_price > trigger_price:
                if not triggered:
                    # 首次突破触发价，开始追踪
                    rule['triggered'] = True
                    rule['highest_price'] = current_price
                    rule['highest_tick_idx'] = int(rule.get("tick_idx") or 0)
                    rule.pop('pullback_hit_count', None)
                    self._save_rules()
                    self.logger.info(f"[{self.stock_code}] {rule_name} - 弹性卖出：价格{current_price:.2f}突破触发价{trigger_price:.2f}，开始追踪最高价")
                    return False, None  # 刚触发，等待回落
                else:
                    # 已触发，继续追踪更高的价格
                    if highest_price is None or current_price > highest_price:
                        rule['highest_price'] = current_price
                        rule['highest_tick_idx'] = int(rule.get("tick_idx") or 0)
                        rule.pop('pullback_hit_count', None)
                        self._save_rules()
                        self.logger.debug(f"[{self.stock_code}] {rule_name} - 弹性卖出：价格创新高{current_price:.2f}，更新最高价")
                        return False, None  # 创出新高，继续等待回落
                    # 如果价格没有创新高，继续检查回落条件（不能直接返回）
            
            # 如果已经触发，检查是否满足回落条件
            if triggered and highest_price is not None and highest_price > 0:
                limit_up_px, pre_close_px = self._best_sell_limit_pre_close()
                _, target_price = compute_best_sell_fallback_from_rule(
                    float(highest_price),
                    rule,
                    limit_up=limit_up_px,
                    pre_close=pre_close_px,
                )
                
                # 只有当价格从最高价回落到目标价时才触发卖出
                # 即：当前价必须 <= 目标价，且当前价必须 < 最高价（说明已经从最高价回落了）
                # 冷却：创新高后的若干个 tick 内不确认回落
                highest_idx = int(rule.get("highest_tick_idx") or 0)
                if highest_idx > 0 and (int(rule["tick_idx"]) - highest_idx) <= cooldown_ticks:
                    return False, None

                hit = (current_price <= target_price and current_price < highest_price)
                if hit:
                    cnt = int(rule.get("pullback_hit_count") or 0) + 1
                    rule["pullback_hit_count"] = cnt
                    self._save_rules()
                    if cnt < confirm_ticks:
                        return False, None
                    # 卖出数量：如果volume为0，表示全部卖出
                    sell_volume = volume if volume > 0 else self.position_volume
                    
                    if sell_volume <= 0:
                        if self._has_pending_sell_locking_position():
                            self._log_throttled(
                                key=f"{self.stock_code}:best_sell:pending_lock:{rule.get('id', rule_name)}",
                                message=(
                                    f"[{self.stock_code}] {rule_name} - 弹性卖出：可用持仓为0，"
                                    f"但同股仍有未成交卖单占仓，延后执行"
                                ),
                                level="info",
                                interval_s=15.0,
                            )
                            return False, None
                        self._finalize_no_position_sell(
                            rule,
                            {
                                "type": "sell",
                                "price": current_price,
                                "volume": 0,
                                "reason": "弹性卖出触发",
                            },
                            tick_data,
                            current_price,
                            0,
                            "弹性卖出",
                        )
                        return False, None

                    if self.position_volume <= 0 and self._has_pending_sell_locking_position():
                        self._log_throttled(
                            key=f"{self.stock_code}:best_sell:pending_lock2:{rule.get('id', rule_name)}",
                            message=(
                                f"[{self.stock_code}] {rule_name} - 弹性卖出：计划卖出{sell_volume}股，"
                                f"可用持仓为0且被未成交卖单占仓，延后执行"
                            ),
                            level="info",
                            interval_s=15.0,
                        )
                        return False, None
                    
                    if sell_volume > 0:
                        self.logger.info(
                            f"[{self.stock_code}] {rule_name} - 弹性卖出：价格从最高价{highest_price:.2f}回落到{current_price:.2f}（目标价{target_price:.2f}），"
                            f"触发卖出{sell_volume}股 | confirm={confirm_ticks}, cooldown={cooldown_ticks}, tick_idx={rule.get('tick_idx')}, pullback_hits={cnt}"
                        )
                        return True, {
                            'type': 'sell',
                            'price': current_price,
                            'volume': sell_volume,
                            'reason': '弹性卖出触发'
                        }
                else:
                    # 未命中：清空连续命中计数
                    if rule.get("pullback_hit_count"):
                        rule.pop("pullback_hit_count", None)
                        self._save_rules()
                    # 添加调试日志，帮助排查问题
                    if current_price < highest_price:
                        rule_id = rule.get("id", rule_name)
                        self._log_throttled(
                            key=f"{self.stock_code}:best_sell:not_pullback:{rule_id}",
                            message=f"[{self.stock_code}] {rule_name} - 弹性卖出：价格{current_price:.2f} < 最高价{highest_price:.2f}，目标价{target_price:.2f}，尚未达到回落条件",
                            level="debug",
                            interval_s=30.0,
                        )
            
            return False, None
        
        return False, None
    
    def _load_require_manual_approval(self):
        """从config.ini加载是否需要人工审核设置，默认True"""
        try:
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(current_dir, 'data', 'config.ini')
            
            if os.path.exists(config_path):
                config = configparser.ConfigParser()
                config.read(config_path, encoding='utf-8')
                
                if 'Trading' in config:
                    value = config.get('Trading', 'require_manual_approval', fallback='1')
                    return value.lower() in ('1', 'true', 'yes', 'on')
            
            # 默认返回True（需要人工审核）
            return True
        except Exception as e:
            # 出错时默认返回True（安全起见）
            return True
    
    def _load_breakthrough_require_true_breakthrough(self):
        """从 config.ini 读取：突破买入是否额外判断真突破。默认 False（仅价格突破）。"""
        try:
            from core.trading_config import breakthrough_buy_require_true_breakthrough

            return breakthrough_buy_require_true_breakthrough(default=False)
        except Exception:
            return False

    def _load_breakthrough_probe_enabled(self):
        """突破买入试探建仓（先 20% 后确认补 80%）。默认 False。"""
        try:
            from core.trading_config import breakthrough_buy_probe_enabled

            return breakthrough_buy_probe_enabled(default=False)
        except Exception:
            return False

    def _load_breakthrough_require_break_below_trigger(self):
        """突破买入须先跌破触发价再上穿。默认 False。"""
        try:
            from core.trading_config import breakthrough_buy_require_break_below_trigger

            return breakthrough_buy_require_break_below_trigger(default=False)
        except Exception:
            return False

    def _rule_true_breakthrough_detail(self, rule) -> str:
        rule = rule or {}
        detail = str(rule.get("true_breakthrough_detail") or "").strip()
        if detail:
            return detail
        if bool(rule.get("true_breakthrough_passed")):
            detail = str(rule.get("executed_detail") or "").strip()
            if detail:
                return detail
        # 大 QMT 回写漏写时，从 results 事件兜底（右键仍能看见三项数值）
        try:
            tid = ""
            rid = str(rule.get("id") or "").strip()
            parent = str(getattr(self, "task_id", "") or "").strip()
            if parent and rid:
                tid = f"{parent}:{rid}"
            elif rid:
                tid = rid
            ev = self._lookup_builtin_true_breakthrough_event(tid)
            if isinstance(ev, dict):
                return str(ev.get("detail") or ev.get("msg") or "").strip()
        except Exception:
            pass
        return ""

    def _rule_true_breakthrough_passed(self, rule) -> bool:
        rule = rule or {}
        # 价格带硬pass：真突破条件可能已过，但未下单，不算「已执行-真突破」
        if self._is_band_hard_pass_rule(rule):
            return False
        if rule.get("true_breakthrough_passed") is True:
            return True
        state = rule.get("breakthrough_probe") or {}
        if state.get("true_breakthrough_passed"):
            return True
        # 大 QMT：规则名含真突破且已执行下单，或 results 有 tb_pass
        try:
            name = str(rule.get("name") or "")
            if (
                rule.get("executed")
                and "真突破" in name
                and rule.get("executed_reason")
                not in ("not_true_breakthrough", "band_hard_pass")
            ):
                rid = str(rule.get("id") or "").strip()
                parent = str(getattr(self, "task_id", "") or "").strip()
                tid = f"{parent}:{rid}" if parent and rid else rid
                ev = self._lookup_builtin_true_breakthrough_event(tid)
                if isinstance(ev, dict) and str(ev.get("type") or "") == "tb_pass":
                    return True
        except Exception:
            pass
        return False

    def _stash_true_breakthrough_detail_on_rule(self, rule, trade_info) -> None:
        info = trade_info or {}
        detail = str(info.get("true_breakthrough_detail") or "").strip()
        passed = info.get("true_breakthrough_passed")
        if passed is None:
            passed = info.get("breakthrough_probe_tb_passed")
        if detail:
            rule["true_breakthrough_detail"] = detail
        if passed is True:
            rule["true_breakthrough_passed"] = True

    def _should_defer_breakthrough_probe_finish(self, trade_info) -> bool:
        return bool((trade_info or {}).get("breakthrough_probe_keep_active"))

    def _code6_for_probe(self) -> str:
        code = (self.stock_code or "").strip()
        return code.split(".")[0][:6] if code else ""

    def _init_breakthrough_probe_state(self, rule, trade_info, tick_data) -> None:
        from datetime import datetime
        from core.breakthrough_probe_buy import init_confirm_state

        tick_dt = (tick_data or {}).get("time")
        if not isinstance(tick_dt, datetime):
            tick_dt = datetime.now()
        avg_before = (
            float(self._tb_prefix_sum) / float(self._tb_prefix_cnt)
            if int(self._tb_prefix_cnt or 0) > 0
            else 0.0
        )
        planned = int(trade_info.get("breakthrough_probe_planned") or rule.get("volume") or 0)
        probe_v = int(trade_info.get("volume") or 0)
        remain_v = int(trade_info.get("breakthrough_probe_remain") or 0)
        state = init_confirm_state(
            code=self._code6_for_probe(),
            trigger_price=float(rule.get("price") or 0),
            planned_volume=planned,
            probe_volume=probe_v,
            remain_volume=remain_v,
            break_tick_dt=tick_dt,
            avg_vol_before=avg_before,
            tb_vol_ratio=float(trade_info.get("breakthrough_probe_tb_vol_ratio") or 0),
            true_breakthrough_passed=bool(trade_info.get("breakthrough_probe_tb_passed")),
        )
        rule["breakthrough_probe"] = state
        rule["breakthrough_probe_active"] = True
        self._stash_true_breakthrough_detail_on_rule(rule, trade_info)
        self.logger.info(
            f"[{self.stock_code}] 突破买入试探建仓启动: 试探{probe_v}股 待确认补买{remain_v}股 "
            f"触发价={rule.get('price')} 窗口={state.get('max_add_display')}"
        )

    def _record_probe_remain_skipped(self, rule, state, tick_data) -> None:
        """试探仓已成交、确认窗口放弃补买时追加一条 skipped 执行记录。"""
        from core.breakthrough_probe_buy import finish_summary

        remain = int(state.get("remain_volume") or 0)
        detail = finish_summary(state)
        price = float((tick_data or {}).get("lastPrice") or rule.get("executed_price") or 0)
        info = {
            "type": "buy",
            "breakthrough_probe_phase": "remain_skipped",
            "breakthrough_probe_planned": int(state.get("planned_volume") or rule.get("volume") or 0),
            "volume": remain,
            "reason": f"突破买入放弃补买: {detail}",
        }
        self._record_skipped_execution(
            rule,
            info,
            tick_data,
            price,
            remain,
            "PROBE_REMAIN_SKIPPED",
            detail or "确认窗口内放弃补买",
            approval_result="probe_remain_skipped",
        )

    def _finalize_breakthrough_probe_rule(self, rule, tick_data, *, remain_executed: bool) -> None:
        from datetime import datetime
        from core.breakthrough_probe_buy import finish_summary, total_filled_volume

        state = rule.get("breakthrough_probe") or {}
        state["active"] = False
        rule["breakthrough_probe_active"] = False
        rule["executed"] = True
        tick_dt = (tick_data or {}).get("time")
        if not isinstance(tick_dt, datetime):
            tick_dt = datetime.now()
        total_vol = total_filled_volume(state)
        rule["executed_volume"] = total_vol
        if remain_executed:
            rule["executed_reason"] = "breakthrough_probe_completed"
        else:
            rule["executed_reason"] = "breakthrough_probe_probe_only"
        detail = finish_summary(state)
        tb_detail = str(rule.get("true_breakthrough_detail") or "").strip()
        if detail and tb_detail:
            rule["executed_detail"] = f"{detail} | 真突破: {tb_detail}"
        elif detail:
            rule["executed_detail"] = detail
        elif tb_detail:
            rule["executed_detail"] = tb_detail
        if not rule.get("executed_time"):
            rule["executed_time"] = tick_dt.strftime("%Y-%m-%d %H:%M:%S")
        self._save_rules()
        self.logger.info(
            f"[{self.stock_code}] 突破买入试探建仓结束: {rule.get('name', '')} "
            f"合计{total_vol}股 — {detail or '无说明'}"
        )
        try:
            self.update_chart()
        except Exception:
            pass

    def _process_breakthrough_probe_confirmations(self, tick_data) -> None:
        from core.breakthrough_probe_buy import (
            DECISION_ADD,
            DECISION_TIMEOUT_ADD,
            MAX_REARM_ADD_ATTEMPTS,
            enter_await_rearm,
            finish_summary,
            is_past_rearm_add_cutoff,
            make_rearm_meta,
            process_confirm_tick,
            record_rearm_confirm_failed,
            should_defer_remain_to_rearm,
        )

        if not isinstance(tick_data, dict):
            return
        current_price = float(tick_data.get("lastPrice") or 0)
        if current_price <= 0:
            return
        code6 = self._code6_for_probe()
        tick_dt = tick_data.get("time")
        if not isinstance(tick_dt, datetime):
            tick_dt = datetime.now()
        tick_vol = self._tick_break_volume_sh(tick_data)

        for rule in getattr(self, "rules", []) or []:
            if not rule.get("enabled", True) or rule.get("executed", False):
                continue
            if rule.get("type") != "breakthrough_buy":
                continue

            rearm = rule.get("breakthrough_probe_rearm")
            if isinstance(rearm, dict) and rearm.get("await_rearm"):
                if is_past_rearm_add_cutoff(tick_dt):
                    self._finalize_breakthrough_rearm_at_cutoff(rule, tick_data)
                continue

            state = rule.get("breakthrough_probe") or {}
            if not state.get("active"):
                continue

            decision = process_confirm_tick(state, code6, current_price, tick_dt, tick_vol)
            if not decision:
                if is_past_rearm_add_cutoff(tick_dt) and int(state.get("remain_volume") or 0) > 0:
                    self._finalize_breakthrough_rearm_at_cutoff(rule, tick_data)
                continue

            if decision in (DECISION_ADD, DECISION_TIMEOUT_ADD):
                remain = int(state.get("remain_volume") or 0)
                if remain <= 0:
                    self._finalize_breakthrough_probe_rule(rule, tick_data, remain_executed=False)
                    continue
                state["remain_filled_volume"] = remain
                rearm = rule.get("breakthrough_probe_rearm") or make_rearm_meta()
                rule["breakthrough_probe_rearm"] = rearm
                rearm["remain_pending"] = 0
                rearm["await_rearm"] = False
                trade_info = {
                    "type": "buy",
                    "price": current_price,
                    "volume": remain,
                    "reason": f"突破买入补买80%: {finish_summary(state)}",
                    "breakthrough_probe_phase": "remain",
                    "breakthrough_probe_planned": int(state.get("planned_volume") or rule.get("volume") or 0),
                }
                self._execute_trade(rule, trade_info, tick_data)
                self._finalize_breakthrough_probe_rule(rule, tick_data, remain_executed=True)
            else:
                skip_msg = finish_summary(state)
                self.logger.info(
                    f"[{self.stock_code}] 突破买入放弃补买: {skip_msg}"
                )
                self._record_probe_remain_skipped(rule, state, tick_data)
                rearm = rule.get("breakthrough_probe_rearm") or make_rearm_meta(
                    trigger_price=float(rule.get("price") or 0),
                    planned_volume=int(rule.get("volume") or 0),
                )
                rule["breakthrough_probe_rearm"] = rearm
                rearm.update({
                    "probe_bought": True,
                    "probe_filled": int(
                        state.get("probe_filled_volume") or state.get("probe_volume") or 0
                    ),
                    "remain_pending": int(state.get("remain_volume") or 0),
                    "planned_volume": int(state.get("planned_volume") or rule.get("volume") or 0),
                    "trigger_price": float(state.get("trigger_price") or rule.get("price") or 0),
                    "tb_vol_ratio": float(state.get("tb_vol_ratio") or 0),
                    "true_breakthrough_passed": bool(state.get("true_breakthrough_passed")),
                })
                state["active"] = False
                rule["breakthrough_probe_active"] = False
                if should_defer_remain_to_rearm(rearm, tick_dt):
                    if state.get("rearm_cross"):
                        record_rearm_confirm_failed(rearm)
                    enter_await_rearm(rearm, reason=skip_msg)
                    self.logger.info(
                        f"[{self.stock_code}] 待跌回触发价后再次上穿补买 "
                        f"（14:57前，已失败{rearm.get('rearm_failed_attempts', 0)}次"
                        f"/最多{MAX_REARM_ADD_ATTEMPTS}次，待补{rearm.get('remain_pending')}股）"
                    )
                    try:
                        self.update_chart()
                    except Exception:
                        pass
                else:
                    self._finalize_breakthrough_probe_rule(rule, tick_data, remain_executed=False)

    def _enter_breakthrough_buy_await_rearm(self, rule, tick_data, tb_msg, tb_detail=None) -> None:
        """非真突破：不结束规则，等待跌回触发价后再次上穿重判。"""
        from core.breakthrough_probe_buy import enter_await_rearm, make_rearm_meta

        rule_name = rule.get("name", "未命名规则")
        detail_text = str(tb_detail or tb_msg or "").strip()
        reason = "非真突破，等待下次上穿" + (f": {detail_text}" if detail_text else "")
        rearm = rule.get("breakthrough_probe_rearm")
        if not isinstance(rearm, dict):
            rearm = make_rearm_meta(
                trigger_price=float(rule.get("price") or 0),
                planned_volume=int(rule.get("volume") or 0),
            )
            rule["breakthrough_probe_rearm"] = rearm
        enter_await_rearm(rearm, reason=reason)
        self._save_rules()
        self.logger.info(
            f"[{self.stock_code}] {rule_name} - {reason}"
        )
        exec_time = (tick_data or {}).get("time")
        if not isinstance(exec_time, datetime):
            exec_time = datetime.now()
        current_price = float((tick_data or {}).get("lastPrice") or 0)
        self._record_true_breakthrough_execution(
            rule,
            tick_data,
            exec_time,
            current_price,
            detail_text,
            passed=False,
        )
        try:
            self.update_chart()
        except Exception:
            pass

    def _finalize_breakthrough_rearm_at_cutoff(self, rule, tick_data) -> None:
        """14:57 后不再等待上穿补买 / 重判真突破。"""
        from core.breakthrough_probe_buy import finish_summary

        rearm = rule.get("breakthrough_probe_rearm") or {}
        state = rule.get("breakthrough_probe") or {}
        if state.get("active"):
            state["active"] = False
            rule["breakthrough_probe_active"] = False
            if int(state.get("remain_volume") or 0) > 0:
                self._record_probe_remain_skipped(rule, state, tick_data)
        if rearm.get("probe_bought") and int(rearm.get("remain_pending") or 0) > 0:
            if not state.get("finish_reason"):
                state["finish_reason"] = rearm.get("last_skip_reason") or "14:57后截止，放弃补买"
            self._finalize_breakthrough_probe_rule(rule, tick_data, remain_executed=False)
            return
        rule_name = rule.get("name", "未命名规则")
        tick_dt = (tick_data or {}).get("time")
        if not isinstance(tick_dt, datetime):
            tick_dt = datetime.now()
        rule["executed"] = True
        rule["executed_time"] = tick_dt.strftime("%Y-%m-%d %H:%M:%S")
        rule["executed_price"] = float((tick_data or {}).get("lastPrice") or rule.get("price") or 0)
        rule["executed_volume"] = 0
        rule["order_id"] = "REARM_CUTOFF"
        rule["executed_reason"] = "not_true_breakthrough"
        rule["executed_detail"] = rearm.get("last_skip_reason") or "14:57后截止"
        rule.pop("breakthrough_probe_rearm", None)
        self._save_rules()
        self.logger.info(
            f"[{self.stock_code}] {rule_name} - 14:57截止: {rule['executed_detail']}"
        )
        try:
            self.update_chart()
        except Exception:
            pass

    def _maybe_reset_true_breakthrough_state_for_tick(self, tick_data):
        from datetime import date as date_cls

        tick_time = tick_data.get("time") if isinstance(tick_data, dict) else None
        tick_date = tick_time.date() if hasattr(tick_time, "date") else date_cls.today()
        if self._tb_state_date != tick_date:
            self._tb_state_date = tick_date
            self._tb_prev_tick_row = None
            self._tb_recent_tick_rows = []
            self._tb_recent_break_vols = []
            self._tb_prefix_sum = 0.0
            self._tb_prefix_cnt = 0
            self._tb_last_cum_volume = None
            self._last_tick_price = None

    def _is_breakthrough_buy_price_cross_tick(self, tick_data, trigger_price) -> bool:
        """仅在价格由 <= 触发价 上穿至 > 触发价 的首 tick 返回 True（与 breakbuycheck 突破时刻一致）。"""
        if not isinstance(tick_data, dict):
            return False
        try:
            from strategy_generator_app.backtest.true_breakthrough import (
                is_breakthrough_buy_price_cross_tick,
            )
        except Exception:
            return False

        code = (self.stock_code or "").strip()
        code6 = code.split(".")[0][:6] if code else ""
        lp = float(tick_data.get("lastPrice") or 0)
        prev = self._last_tick_price
        return is_breakthrough_buy_price_cross_tick(
            code6, lp, float(trigger_price or 0), prev
        )

    def _is_breakthrough_break_below_tick(self, tick_data, trigger_price) -> bool:
        """展示价由 >= 触发价 跌破至 < 触发价 的首 tick（先跌破再突破之前置条件）。"""
        if not isinstance(tick_data, dict):
            return False
        try:
            from strategy_generator_app.backtest.true_breakthrough import (
                is_breakthrough_break_below_trigger_tick,
            )
        except Exception:
            return False

        code = (self.stock_code or "").strip()
        code6 = code.split(".")[0][:6] if code else ""
        lp = float(tick_data.get("lastPrice") or 0)
        prev = self._last_tick_price
        return is_breakthrough_break_below_trigger_tick(
            code6, lp, float(trigger_price or 0), prev
        )

    def _finish_breakthrough_buy_not_true_breakthrough(self, rule, tick_data, tb_msg, tb_detail=None):
        """突破时刻未过真突破：结束规则，不下单。"""
        rule_name = rule.get("name", "未命名规则")
        current_price = float((tick_data or {}).get("lastPrice") or 0)
        exec_time = (tick_data or {}).get("time")
        if not isinstance(exec_time, datetime):
            exec_time = datetime.now()

        detail_text = str(tb_detail or tb_msg or "").strip()
        rule["executed"] = True
        rule["executed_time"] = exec_time.strftime("%Y-%m-%d %H:%M:%S")
        rule["executed_price"] = current_price if current_price > 0 else float(rule.get("price") or 0)
        rule["executed_volume"] = 0
        rule["order_id"] = "NOT_TRUE_BREAKTHROUGH"
        rule["executed_reason"] = "not_true_breakthrough"
        rule["executed_detail"] = detail_text
        self._save_rules()
        self.logger.info(
            f"[{self.stock_code}] {rule_name} - 已结束，非真突破未下单"
            + (f": {detail_text}" if detail_text else "")
        )
        self._record_true_breakthrough_execution(
            rule,
            tick_data,
            exec_time,
            current_price,
            detail_text,
            passed=False,
        )
        self.update_chart()

    def _check_band_breakthrough_buy(self, rule, tick_data, current_price):
        """价格带硬pass：监控带内真突破；深位或买入参考价>MA5(带上沿)则作废。"""
        from core.price_band_buy import (
            band_hard_pass_reason,
            estimate_band_buy_ref_price,
            get_band_accept_low,
            get_price_band,
        )

        try:
            band_lo, band_hi = get_price_band(rule)
            volume = int(rule.get("volume") or 0)
            current_price = float(current_price or 0)
        except (TypeError, ValueError):
            return False, None
        if volume <= 0 or current_price <= 0 or band_lo <= 0 or band_hi < band_lo:
            return False, None
        if not (band_lo <= current_price <= band_hi):
            return False, None

        tb_ok, tb_msg, tb_detail, _tb_metrics = self._evaluate_true_breakthrough_live(
            tick_data,
            rule.get("price") or band_hi,
            rule=rule,
            require_price_above_trigger=False,
        )
        if not tb_ok:
            # 带内未过真突破：继续盯，不结束规则
            return False, None

        precision = 2
        try:
            from utils.security_type_util import SecurityTypeUtil

            precision = int(SecurityTypeUtil.get_price_precision(self.stock_code) or 2)
        except Exception:
            precision = 3 if str(getattr(self, "stock_code", "") or "").startswith("688") else 2
        slippage = 0.001 if precision == 3 else 0.01
        buy_ref = estimate_band_buy_ref_price(
            current_price,
            tick_data,
            slippage=slippage,
            precision=precision,
        )
        accept_lo = get_band_accept_low(rule)
        hp = band_hard_pass_reason(
            last_price=current_price,
            band_low=band_lo,
            band_high=band_hi,
            accept_low=accept_lo,
            buy_ref_price=buy_ref,
        )
        if hp:
            self._finish_band_hard_pass(
                rule,
                tick_data,
                current_price,
                band_lo,
                band_hi,
                hp,
                tb_detail=tb_detail,
            )
            return False, None

        accept_s = f" 有效下沿={float(accept_lo):.2f}" if accept_lo is not None else ""
        reason = (
            f"价格带量价买入: 现价={current_price:.2f} "
            f"带=[{band_lo:.2f},{band_hi:.2f}]{accept_s}; {tb_detail}"
        )
        return True, {
            "type": "buy",
            "price": current_price,
            "volume": volume,
            "reason": reason,
            "true_breakthrough_detail": tb_detail,
            "true_breakthrough_passed": True,
        }

    def _finish_band_hard_pass(
        self,
        rule,
        tick_data,
        current_price,
        band_lo,
        band_hi,
        detail_text,
        tb_detail=None,
    ):
        """首次真突破硬pass结束，不下单（深位或买入参考价>MA5）。"""
        rule_name = rule.get("name", "未命名规则")
        exec_time = (tick_data or {}).get("time")
        if not isinstance(exec_time, datetime):
            exec_time = datetime.now()
        detail_text = str(detail_text or "").strip() or (
            f"首次真突破放弃（监控带[{float(band_lo):.2f},{float(band_hi):.2f}]）"
        )
        if tb_detail and str(tb_detail) not in detail_text:
            detail_text = f"{detail_text}; {tb_detail}"
        rule["executed"] = True
        rule["executed_time"] = exec_time.strftime("%Y-%m-%d %H:%M:%S")
        rule["executed_price"] = float(current_price or 0)
        rule["executed_volume"] = 0
        rule["order_id"] = "BAND_HARD_PASS"
        rule["executed_reason"] = "band_hard_pass"
        rule["executed_detail"] = detail_text
        self._save_rules()
        self.logger.info(f"[{self.stock_code}] {rule_name} - {detail_text}")
        synthetic = {
            "skip_reason": detail_text,
            "execution_outcome": "skipped",
            "true_breakthrough_passed": True,
            "true_breakthrough_detail": str(tb_detail or ""),
        }
        self._record_execution(
            rule,
            synthetic,
            tick_data,
            exec_time,
            float(current_price or 0),
            0,
            "BAND_HARD_PASS",
            False,
            "band_hard_pass",
        )
        self.update_chart()

    def _record_true_breakthrough_execution(
        self,
        rule,
        tick_data,
        exec_time,
        current_price,
        detail_text,
        passed,
        trade_info=None,
        price=0,
        volume=0,
        order_id="",
        require_manual_approval=False,
        approval_result="auto",
    ):
        """记录真突破判定相关的执行记录（含三条条件数值）。"""
        if not detail_text:
            return
        synthetic_trade_info = dict(trade_info or {})
        synthetic_trade_info["true_breakthrough_detail"] = detail_text
        synthetic_trade_info["true_breakthrough_passed"] = bool(passed)
        if passed:
            synthetic_trade_info["execution_outcome"] = "ordered"
        else:
            synthetic_trade_info["skip_reason"] = f"非真突破未下单: {detail_text}"
            synthetic_trade_info["execution_outcome"] = "skipped"
        self._record_execution(
            rule,
            synthetic_trade_info,
            tick_data,
            exec_time,
            price,
            volume,
            order_id or ("NOT_TRUE_BREAKTHROUGH" if not passed else order_id),
            require_manual_approval,
            "not_true_breakthrough" if not passed else approval_result,
        )

    def _tick_break_volume_sh(self, tick_data) -> Optional[float]:
        if not isinstance(tick_data, dict):
            return None
        for key in (
            "lastVol",
            "tradeVol",
            "tradeVolume",
            "tickVol",
            "singleVol",
            "matchQty",
            "qty",
            "volume_delta",
        ):
            raw = tick_data.get(key)
            if raw is None:
                continue
            try:
                v = float(raw)
            except (TypeError, ValueError):
                continue
            if v >= 0:
                return v
        cum = None
        for key in ("volume", "cumVol", "totalVol", "cum_volume", "dealVol", "pvolume"):
            raw = tick_data.get(key)
            if raw is not None:
                try:
                    cum = float(raw)
                    break
                except (TypeError, ValueError):
                    continue
        if cum is None:
            return None
        if self._tb_last_cum_volume is None:
            return max(0.0, cum) * float(self._tb_vol_mul or 100.0)
        delta = cum - float(self._tb_last_cum_volume)
        if delta < -1e-3:
            return max(0.0, cum) * float(self._tb_vol_mul or 100.0)
        return max(0.0, delta) * float(self._tb_vol_mul or 100.0)

    def _evaluate_true_breakthrough_live(
        self,
        tick_data,
        trigger_price,
        rule=None,
        require_price_above_trigger: bool = True,
    ):
        try:
            from strategy_generator_app.backtest.true_breakthrough import (
                evaluate_true_breakthrough_tick_with_detail,
                max_cond1_breakthrough_volume_from_recent,
                normalize_true_breakthrough_cond1_mode,
                round_price_like_display,
                window_prior_ticks_from_seconds,
            )
        except Exception as e:
            return False, f"真突破模块不可用: {e}", f"真突破模块不可用: {e}", {}

        code = (self.stock_code or "").strip()
        code6 = code.split(".")[0][:6] if code else ""
        row = dict(tick_data or {})
        v_break = self._tick_break_volume_sh(tick_data)
        avg_before = (
            float(self._tb_prefix_sum) / float(self._tb_prefix_cnt)
            if self._tb_prefix_cnt > 0
            else None
        )
        v_cond1 = max_cond1_breakthrough_volume_from_recent(
            list(self._tb_recent_break_vols or []), v_break
        )
        ratio_window = (list(self._tb_recent_tick_rows or []) + [row])[-5:]

        cond1_mode = "tick3"
        lookback_prior = None
        if isinstance(rule, dict):
            mode_raw = rule.get("true_breakthrough_cond1_mode")
            if mode_raw:
                cond1_mode = normalize_true_breakthrough_cond1_mode(mode_raw)
            if rule.get("true_breakthrough_window_sec") is not None:
                lookback_prior = window_prior_ticks_from_seconds(
                    rule.get("true_breakthrough_window_sec")
                )
                if not mode_raw:
                    cond1_mode = "window"

        ok, msg, detail, _metrics = evaluate_true_breakthrough_tick_with_detail(
            code6,
            row,
            self._tb_prev_tick_row,
            float(self._tb_vol_mul or 100.0),
            avg_before,
            v_break,
            ratio_window,
            v_break_cond1=v_cond1,
            recent_vols=list(self._tb_recent_break_vols or []),
            cond1_mode=cond1_mode,
            lookback_prior=lookback_prior,
        )
        if not require_price_above_trigger:
            return bool(ok), msg, detail, dict(_metrics or {})
        lp = float(tick_data.get("lastPrice") or 0)
        trig = float(trigger_price or 0)
        r_lp = round_price_like_display(code6, lp)
        r_ref = round_price_like_display(code6, trig)
        passed_price = trig > 0 and r_lp > r_ref
        return bool(passed_price and ok), msg, detail, dict(_metrics or {})

    def _advance_true_breakthrough_tick_state(self, tick_data):
        if not isinstance(tick_data, dict):
            return
        row = dict(tick_data)
        v_break = self._tick_break_volume_sh(tick_data)
        for key in ("volume", "cumVol", "totalVol", "cum_volume", "dealVol", "pvolume"):
            raw = tick_data.get(key)
            if raw is not None:
                try:
                    self._tb_last_cum_volume = float(raw)
                    break
                except (TypeError, ValueError):
                    pass
        self._tb_prev_tick_row = row
        hist = list(self._tb_recent_tick_rows or [])
        hist.append(row)
        self._tb_recent_tick_rows = hist[-5:]
        if v_break is not None:
            self._tb_prefix_sum += float(v_break)
            self._tb_prefix_cnt += 1
            vhist = list(self._tb_recent_break_vols or [])
            vhist.append(float(v_break))
            self._tb_recent_break_vols = vhist[-5:]

    def _load_early_order(self):
        """从config.ini加载是否提前下单设置，默认False（仅作新规则默认值）。"""
        try:
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(current_dir, 'data', 'config.ini')
            
            if os.path.exists(config_path):
                config = configparser.ConfigParser()
                config.read(config_path, encoding='utf-8')
                
                if 'Trading' in config:
                    value = config.get('Trading', 'early_order', fallback='0')
                    return value.lower() in ('1', 'true', 'yes', 'on')
            
            # 默认返回False（不提前下单）
            return False
        except Exception as e:
            # 出错时默认返回False
            return False

    def _stamp_early_order_flag(self, rule, *, force: bool = True):
        """单点/网格规则：创建时快照当前提前下单设置；已有字段默认不覆盖。"""
        if not isinstance(rule, dict):
            return rule
        rule_type = rule.get('type') or rule.get('rule_type')
        if rule_type not in ('single_buy', 'single_sell', 'grid_buy', 'grid_sell'):
            return rule
        if force or 'early_order_enabled' not in rule:
            rule['early_order_enabled'] = bool(self._load_early_order())
        return rule

    def _stamp_breakthrough_flags(self, rule, *, force: bool = True):
        """突破买入：创建时快照「真突破 / 须先跌破」；已有字段默认不覆盖。"""
        if not isinstance(rule, dict):
            return rule
        if (rule.get('type') or rule.get('rule_type')) != 'breakthrough_buy':
            return rule
        if force or 'require_true_breakthrough' not in rule:
            rule['require_true_breakthrough'] = bool(
                self._load_breakthrough_require_true_breakthrough()
            )
        if force or 'require_break_below' not in rule:
            rule['require_break_below'] = bool(
                self._load_breakthrough_require_break_below_trigger()
            )
        return rule

    def _rule_early_order_enabled(self, rule) -> bool:
        """该规则是否走提前下单：优先规则快照，缺省回退全局。"""
        if not isinstance(rule, dict):
            return False
        if 'early_order_enabled' in rule:
            return bool(rule.get('early_order_enabled'))
        return bool(self._load_early_order())

    def _rule_require_true_breakthrough(self, rule) -> bool:
        if not isinstance(rule, dict):
            return bool(self._load_breakthrough_require_true_breakthrough())
        if 'require_true_breakthrough' in rule:
            return bool(rule.get('require_true_breakthrough'))
        return bool(self._load_breakthrough_require_true_breakthrough())

    def _rule_require_break_below(self, rule) -> bool:
        if not isinstance(rule, dict):
            return bool(self._load_breakthrough_require_break_below_trigger())
        if 'require_break_below' in rule:
            return bool(rule.get('require_break_below'))
        return bool(self._load_breakthrough_require_break_below_trigger())

    def _load_min_buy_amount(self):
        """从config.ini加载全局最小买入金额（元），默认5000。"""
        try:
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(current_dir, 'data', 'config.ini')

            if os.path.exists(config_path):
                config = configparser.ConfigParser()
                config.read(config_path, encoding='utf-8')
                if 'Trading' in config:
                    value = float(config.get('Trading', 'min_buy_amount', fallback='5000'))
                    return max(0.0, value)

            return 5000.0
        except Exception:
            # 出错时使用安全默认值，避免频繁小单
            return 5000.0

    def _assess_buy_cash_requirements(self, price: float, volume: int) -> dict:
        """买入门控：最小买入只卡本笔价×量；现金不足可缩量或暂缓。"""
        min_buy_amount = self._load_min_buy_amount()
        required_amount = float(price) * int(volume)
        cash = float(self.available_cash or 0)

        def _blocked(order_id, approval_result, message):
            return {
                "blocked": True,
                "order_id": order_id,
                "approval_result": approval_result,
                "message": message,
            }

        # 最小买入：只看本笔委托金额，与可用现金无关
        if min_buy_amount > 0 and required_amount < min_buy_amount:
            return _blocked(
                "SKIPPED_MIN_BUY",
                "order_below_min",
                f"买入金额低于最小买入金额，未下单（约{required_amount:.2f}元 < 最小{min_buy_amount:.2f}元）",
            )

        if cash <= 0:
            return _blocked(
                "NO_CASH",
                "no_cash",
                f"无可用资金，未下单（需要约{required_amount:.2f}元，可用{cash:.2f}元）",
            )

        if required_amount > cash:
            max_volume = int(cash / price / 100) * 100
            if max_volume <= 0:
                return _blocked(
                    "NO_CASH",
                    "no_cash",
                    f"可用资金不足100股，未下单（需要约{required_amount:.2f}元，可用{cash:.2f}元）",
                )
            adjusted_amount = float(price) * max_volume
            # 缩量后变成小单：不下这单（等资金够买到最小金额），不结束大单任务
            if min_buy_amount > 0 and adjusted_amount < min_buy_amount:
                return _blocked(
                    "NO_CASH",
                    "no_cash",
                    f"现金不足且缩量后低于最小买入，暂不下单（约{adjusted_amount:.2f}元 < 最小{min_buy_amount:.2f}元，避免小单）",
                )
            return {"blocked": False, "volume": max_volume}

        return {"blocked": False, "volume": int(volume)}

    def _revert_rule_executed_for_cash_retry(self, rule, trade_info) -> None:
        """撤销人工审核预标记的 executed，便于资金不足时继续等待触发。"""
        rule_type = rule.get("type")
        if rule_type in ["grid_buy", "grid_sell"]:
            grid_index = (trade_info or {}).get("grid_index")
            if grid_index is not None:
                executed_grids = rule.get("executed_grids") or []
                if grid_index in executed_grids:
                    executed_grids.remove(grid_index)
                    rule["executed_grids"] = executed_grids
                for key in ("executed_grid_prices", "executed_grid_volumes"):
                    bucket = rule.get(key)
                    if isinstance(bucket, dict):
                        bucket.pop(grid_index, None)
            rule["executed"] = len(rule.get("executed_grids") or []) >= int(rule.get("num_grids", 2) or 2) + 1
        else:
            rule["executed"] = False

        for key in ("executed_time", "executed_price", "executed_volume", "order_id", "executed_reason"):
            rule.pop(key, None)

    def _defer_buy_for_insufficient_funds(
        self,
        rule,
        trade_info,
        tick_data,
        price,
        order_id,
        skip_reason,
        approval_result,
        *,
        revert_executed=False,
    ) -> None:
        """资金/最小买入金额不足：记录日志但不结束任务，等待下次触发。"""
        from datetime import datetime

        rule_name = rule.get("name", "未命名规则")
        if revert_executed:
            self._revert_rule_executed_for_cash_retry(rule, trade_info)

        now_ts = datetime.now().timestamp()
        last_ts = float(rule.get("_cash_defer_last_log_ts") or 0)
        if now_ts - last_ts >= 60:
            rule["_cash_defer_last_log_ts"] = now_ts
            self.logger.info(
                f"[{self.stock_code}] {rule_name} - {skip_reason}，任务继续等待下次触发"
            )
            info = dict(trade_info or {})
            info["skip_reason"] = f"{skip_reason}（任务继续等待）"
            info["execution_outcome"] = "deferred"
            self._record_execution(
                rule,
                info,
                tick_data,
                self._exec_time_from_tick(tick_data),
                price,
                0,
                order_id,
                False,
                f"{approval_result}_deferred",
            )
            self._save_rules()
            self.update_chart()

    def _parse_hms_time(self, text, default_time):
        """解析 HH:MM 或 HH:MM:SS 到 datetime.time。"""
        try:
            parts = [int(x) for x in str(text or "").strip().split(":")]
            if len(parts) == 2:
                h, m = parts
                s = 0
            elif len(parts) >= 3:
                h, m, s = parts[:3]
            else:
                return default_time
            from datetime import time as dt_time
            return dt_time(h, m, s)
        except Exception:
            return default_time

    def _load_buy_block_window_config(self):
        """读取开盘禁买时间窗配置。"""
        from datetime import time as dt_time
        enabled = False
        start_t = dt_time(9, 30, 0)
        end_t = dt_time(9, 31, 30)
        try:
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(current_dir, 'data', 'config.ini')
            if not os.path.exists(config_path):
                return enabled, start_t, end_t
            config = configparser.ConfigParser()
            config.read(config_path, encoding='utf-8')
            if 'Trading' not in config:
                return enabled, start_t, end_t
            enabled = config.get('Trading', 'buy_block_window_enabled', fallback='0').lower() in ('1', 'true', 'yes', 'on')
            start_t = self._parse_hms_time(config.get('Trading', 'buy_block_start', fallback='09:30:00'), start_t)
            end_t = self._parse_hms_time(config.get('Trading', 'buy_block_end', fallback='09:31:30'), end_t)
        except Exception:
            pass
        return enabled, start_t, end_t

    def _is_in_buy_block_window(self):
        """当前时刻是否命中开盘禁买时间窗。"""
        from datetime import datetime
        enabled, start_t, end_t = self._load_buy_block_window_config()
        if not enabled:
            return False
        now_t = datetime.now().time()
        if start_t <= end_t:
            return start_t <= now_t <= end_t
        # 跨午夜兜底（通常不会用到）
        return now_t >= start_t or now_t <= end_t

    def _load_elastic_confirm_config(self):
        """
        从 config.ini 读取弹性买卖（best_buy/best_sell）的全局确认/冷却参数（按 tick 次数）。
        返回 (confirm_ticks, cooldown_after_extreme_ticks, dynamic_thresholds)。
        """
        try:
            return load_elastic_confirm_triple()
        except Exception:
            return 4, 2, 2

    def _best_sell_limit_pre_close(self):
        """弹性卖出 room 公式用的涨停价与昨收。"""
        limit_up = float(getattr(self, "limit_up_price", 0) or 0)
        pre_close = float(getattr(self, "prev_close_price", 0) or 0)
        if limit_up <= 0 and pre_close > 0 and getattr(self, "stock_code", None):
            try:
                lu, _ = self.calculate_limit_prices(self.stock_code, pre_close)
                if lu:
                    limit_up = float(lu)
                    self.limit_up_price = limit_up
            except Exception:
                pass
        return limit_up, pre_close

    def _elastic_best_sell_target_price_for_display(self, rule, cfg_dyn: int):
        """与规则检查里 best_sell 的回落目标价一致（不含 confirm/cooldown），供价格-仓位图虚线对齐。"""
        if not rule.get("triggered", False):
            return None
        hp_raw = rule.get("highest_price")
        if hp_raw is None:
            return None
        try:
            highest_price = float(hp_raw)
        except (TypeError, ValueError):
            return None
        if highest_price <= 0:
            return None
        limit_up_px, pre_close_px = self._best_sell_limit_pre_close()
        _, target = compute_best_sell_fallback_from_rule(
            highest_price,
            rule,
            limit_up=limit_up_px,
            pre_close=pre_close_px,
        )
        return target

    def _elastic_best_buy_target_price_for_display(self, rule, cfg_dyn: int):
        """与规则检查里 best_buy 的反弹目标价一致（不含 confirm/cooldown），供价格-仓位图虚线对齐。"""
        trigger_price = float(rule.get("trigger_price", 0) or 0)
        rise_percent = float(rule.get("rise_percent", 0.3) or 0)
        if not rule.get("triggered", False):
            return None
        lp_raw = rule.get("lowest_price")
        if lp_raw is None:
            return None
        try:
            lowest_price = float(lp_raw)
        except (TypeError, ValueError):
            return None
        if lowest_price <= 0:
            return None
        try:
            drop_from_trigger_pct = max(
                0.0, (trigger_price / lowest_price - 1.0) * 100.0
            ) if trigger_price and lowest_price else 0.0
        except Exception:
            drop_from_trigger_pct = 0.0
        rise_scale = float(rule.get("rise_scale") or 0.35)
        max_rise = float(rule.get("max_rise_percent") or 4.0)
        if int(cfg_dyn) <= 0:
            eff_rise = rise_percent
        else:
            eff_rise = min(max_rise, rise_percent + drop_from_trigger_pct * rise_scale)
        return lowest_price * (1 + eff_rise / 100.0)
    
    def _grid_exec_progress(self, rule):
        """网格执行进度。(已完成点数, 总点数, 状态文案)。

        文案：无完成→''；部分→'已部分执行 a/b'；全部→'已执行'。
        """
        try:
            num_grids = int(rule.get("num_grids") or 2)
        except (TypeError, ValueError):
            num_grids = 2
        total = max(1, num_grids + 1)
        done = set()
        for x in rule.get("executed_grids") or []:
            try:
                done.add(int(x))
            except (TypeError, ValueError):
                continue
        n = len(done)
        if bool(rule.get("executed")) or n >= total:
            return n, total, "已执行"
        if n > 0:
            return n, total, "已部分执行 %d/%d" % (n, total)
        return n, total, ""

    def _get_rule_target_price(self, rule):
        """获取规则的指定价格（用于提前下单计算）
        
        返回：(价格, 是否有效)
        """
        rule_type = rule.get('type')
        
        if rule_type == 'single_buy' or rule_type == 'single_sell':
            price = rule.get('price', 0)
            return price, price > 0
        elif rule_type == 'cage_buy':
            price_low = rule.get('price_low', 0)
            price_high = rule.get('price_high', 0)
            # 笼子买入：使用更接近当前价的端点
            if price_low > 0 and price_high > 0:
                if abs(price_low - self.current_price) < abs(price_high - self.current_price):
                    return price_low, True
                else:
                    return price_high, True
            return 0, False
        elif rule_type == 'cage_sell':
            price_low = rule.get('price_low', 0)
            price_high = rule.get('price_high', 0)
            # 笼子卖出：使用更接近当前价的端点
            if price_low > 0 and price_high > 0:
                if abs(price_low - self.current_price) < abs(price_high - self.current_price):
                    return price_low, True
                else:
                    return price_high, True
            return 0, False
        elif rule_type == 'grid_buy':
            # 网格买入：返回下一个未执行的网格点价格（从高到低）
            # 注意：网格买入中 start_price 是高价端，end_price 是低价端
            start_price = rule.get('start_price', 0)
            end_price = rule.get('end_price', 0)
            num_grids = rule.get('num_grids', 2)
            executed_grids = rule.get('executed_grids', [])
            
            if start_price > 0 and end_price > 0:
                # 计算所有网格价格点（从高到低，因为网格买入是从高到低触发）
                for i in range(num_grids + 1):
                    if i in executed_grids:
                        continue  # 该网格已执行，跳过
                    
                    # 计算网格价格（网格买入：i=0是高价端start_price，i=num_grids是低价端end_price）
                    if i == 0:
                        grid_price = start_price  # 高价端
                    elif i == num_grids:
                        grid_price = end_price    # 低价端
                    else:
                        precision = self._get_price_precision()
                        grid_price = round(start_price - (start_price - end_price) * i / num_grids, precision)
                    
                    # 返回第一个未执行的网格点价格（从高到低，即i从0开始）
                    return grid_price, True
            
            return 0, False
        elif rule_type == 'grid_sell':
            # 网格卖出：返回下一个未执行的网格点价格（从低到高）
            start_price = rule.get('start_price', 0)
            end_price = rule.get('end_price', 0)
            num_grids = rule.get('num_grids', 2)
            executed_grids = rule.get('executed_grids', [])
            
            if start_price > 0 and end_price > 0:
                # 计算所有网格价格点（从低到高）
                for i in range(num_grids + 1):
                    if i in executed_grids:
                        continue  # 该网格已执行，跳过
                    
                    # 计算网格价格
                    if i == 0:
                        grid_price = start_price  # 低价端
                    elif i == num_grids:
                        grid_price = end_price    # 高价端
                    else:
                        precision = self._get_price_precision()
                        grid_price = round(start_price + (end_price - start_price) * i / num_grids, precision)
                    
                    # 返回第一个未执行的网格点价格
                    return grid_price, True
            
            return 0, False
        else:
            return 0, False
    
    def _update_early_orders_status(self, tick_data):
        """根据价格差百分比和方向条件更新提前下单状态（仅单点/网格）。
        
        新逻辑：
        - 适用范围：单点买入/卖出、网格买入/卖出（突破买入/卖出、笼子、弹性一律不提前下单）
        - 价格差条件：|(当前价-指定价格)/指定价格| < 0.5% 时开启，> 1% 时撤销
        - 方向条件：
          * 提前买入：节点价格必须 < 当前价（这样下单后要等价格上涨才会成交，达到提前下单的目的）
          * 提前卖出：节点价格必须 > 当前价（这样下单后要等价格下跌才会成交，达到提前下单的目的）
        - 两个条件都满足时才开启提前下单，任一条件不满足时撤销提前下单
        """
        try:
            from utils.qmt_execution_config import use_builtin_order_execution

            if use_builtin_order_execution():
                return
        except Exception:
            pass
        current_price = tick_data.get('lastPrice', 0)
        if current_price <= 0:
            return
        
        self.current_price = current_price  # 更新当前价，供_get_rule_target_price使用
        
        for rule in self.rules:
            # 跳过已禁用、已执行；提前下单仅允许单点/网格（突破提前挂会绕过突破条件直接排队成交）
            rule_type = rule.get('type')
            if (not rule.get('enabled', True) or 
                rule.get('executed', False) or 
                rule_type not in ('single_buy', 'single_sell', 'grid_buy', 'grid_sell')):
                # 若历史上误挂了突破/笼子的提前单，遇到时一并撤销
                if (
                    rule_type in (
                        'breakthrough_buy',
                        'breakthrough_sell',
                        'cage_buy',
                        'cage_sell',
                        'best_buy',
                        'best_sell',
                    )
                    and self._rule_has_active_early_order(rule)
                    and not rule.get('executed', False)
                ):
                    self.logger.info(
                        f"[{self.stock_code}] 提前下单不适用规则类型 {rule_type}，撤销: "
                        f"{rule.get('name', '未命名规则')}"
                    )
                    self._cancel_single_early_order(rule)
                continue

            # 该规则创建时未开提前下单：不新挂；若已有挂单则撤销
            if not self._rule_early_order_enabled(rule):
                if self._rule_has_active_early_order(rule) and not rule.get('executed', False):
                    self._cancel_single_early_order(rule)
                continue
            
            # 获取规则的指定价格
            target_price, is_valid = self._get_rule_target_price(rule)
            if not is_valid or target_price <= 0:
                continue
            
            rule_name = rule.get('name', '未命名规则')
            is_early_order = rule.get('early_order', False)
            
            # 计算价格差百分比：|(当前价-指定价格)/指定价格|
            price_diff_percent = abs((current_price - target_price) / target_price) * 100
            
            # 判断是否为买入类规则（此处已限定仅单点/网格）
            is_buy_rule = rule_type in ('single_buy', 'grid_buy')
            
            # 检查是否满足提前下单的方向条件：
            # - 提前买入：节点价格必须 < 当前价（这样下单后要等价格上涨才会成交）
            # - 提前卖出：节点价格必须 > 当前价（这样下单后要等价格下跌才会成交）
            direction_ok = False
            if is_buy_rule:
                # 买入：目标价必须 < 当前价
                direction_ok = target_price < current_price
            else:
                # 卖出：目标价必须 > 当前价
                direction_ok = target_price > current_price
            
            # 如果价格差 < 0.5% 且方向正确 且未提前下单，则开启提前下单
            if price_diff_percent < 0.5 and direction_ok and not self._rule_has_active_early_order(rule):
                volume = rule.get('volume', 0)
                if rule_type in ('grid_buy', 'grid_sell'):
                    volume = int(rule.get('volume_per_grid') or volume or 0)
                if volume > 0:
                    trade_type = 'buy' if is_buy_rule else 'sell'
                    self.logger.info(f"[{self.stock_code}] ✅ 价格差 {price_diff_percent:.2f}% < 0.5% 且方向正确，开启提前下单: {rule_name} (目标价: {target_price:.2f}元, 当前价: {current_price:.2f}元)")
                    self._execute_early_order(rule, trade_type, tick_data)
            
            # 如果价格差 > 1% 且仍处于提前下单状态（未执行、未禁用），则撤销提前下单
            should_cancel = False
            cancel_reason = ""
            
            # 检查价格差条件
            if price_diff_percent > 1.0:
                should_cancel = True
                cancel_reason = f"价格差过大（{price_diff_percent:.2f}%）"
            
            # 如果满足取消条件，撤销提前下单
            if (should_cancel and 
                is_early_order and 
                not rule.get('executed', False) and 
                rule.get('enabled', True)):
                if rule.get('early_order_cancel_pending'):
                    continue
                self.logger.info(f"[{self.stock_code}] ⚠️ {cancel_reason}，撤销提前下单: {rule_name} (目标价: {target_price:.2f}元, 当前价: {current_price:.2f}元)")
                self._cancel_single_early_order(rule)
    
    def _place_early_orders(self):
        """启动时放置提前下单（已废弃，保留以兼容，实际由_update_early_orders_status处理）"""
        # 新逻辑已改为在tick数据更新时自动检查，这里不再需要手动放置
        # 保留方法以兼容旧代码
        pass
    
    def _execute_early_order(self, rule, trade_type, tick_data=None):
        """执行提前下单（仅单点/网格）。"""
        try:
            from utils.qmt_execution_config import use_builtin_order_execution

            if use_builtin_order_execution():
                return
        except Exception:
            pass
        from datetime import datetime
        
        rule_type = rule.get('type')
        rule_name = rule.get('name', '未命名规则')
        if rule_type not in ('single_buy', 'single_sell', 'grid_buy', 'grid_sell'):
            self.logger.warning(
                f"[{self.stock_code}] 拒绝提前下单：规则类型 {rule_type} 不适用: {rule_name}"
            )
            return
        
        # 构建交易信息
        price = 0
        volume = rule.get('volume', 0)
        if rule_type in ('grid_buy', 'grid_sell'):
            volume = int(rule.get('volume_per_grid') or volume or 0)
        
        if rule_type == 'single_buy' or rule_type == 'single_sell':
            price = rule.get('price', 0)
        elif rule_type == 'grid_buy' or rule_type == 'grid_sell':
            # 网格交易：使用下一个未执行的网格点价格（与_get_rule_target_price保持一致）
            target_price, is_valid = self._get_rule_target_price(rule)
            if not is_valid or target_price <= 0:
                return
            price = target_price
        else:
            return
        
        if price <= 0 or volume <= 0:
            return
        
        # 构建交易信息（不包含reason，因为是提前下单）
        trade_info = {
            'type': trade_type,
            'price': price,
            'volume': volume,
            'reason': '提前下单',
            'early_order': True  # 标记为提前下单
        }
        
        # 执行交易（提前下单）
        tick_src = tick_data if isinstance(tick_data, dict) else (self._last_tick_data if isinstance(self._last_tick_data, dict) else {})
        tick_data = {
            'stock_code': self.stock_code,
            'lastPrice': float(tick_src.get('lastPrice', self.current_price) or self.current_price or 0),
            'askPrice': tick_src.get('askPrice') or [],
            'bidPrice': tick_src.get('bidPrice') or [],
            'time': tick_src.get('time') or datetime.now(),
        }
        
        # 标记为提前下单（在执行交易前标记，这样_execute_trade可以识别）
        rule['early_order'] = True
        rule['early_order_price'] = price
        
        # 调用执行交易方法（会保存订单ID，但不会标记为已执行）
        # 注意：在执行前，确保rule['early_order']已经设置为True，这样_execute_trade可以正确识别
        original_executed = rule.get('executed', False)  # 保存原始状态
        
        # 在执行前，强制确保规则没有被标记为已执行
        if rule.get('executed', False):
            self.logger.warning(f"[{self.stock_code}] ⚠️ 提前下单前规则已被标记为已执行，先清除: {rule_name}")
            rule['executed'] = False
            if 'executed_time' in rule:
                del rule['executed_time']
            if 'executed_price' in rule:
                del rule['executed_price']
            if 'executed_volume' in rule:
                del rule['executed_volume']
        
        try:
            self._execute_trade(rule, trade_info, tick_data)
        finally:
            # 强制执行后检查：提前下单后绝对不能标记为已执行
            if rule.get('early_order', False):
                if rule.get('executed', False):
                    # 如果被标记为已执行，立即修复
                    self.logger.error(f"[{self.stock_code}] ❌ 提前下单任务在执行后被错误标记为已执行！立即强制修复: {rule_name}")
                    rule['executed'] = False
                    if 'executed_time' in rule:
                        del rule['executed_time']
                    if 'executed_price' in rule:
                        del rule['executed_price']
                    if 'executed_volume' in rule:
                        del rule['executed_volume']
                    # 保留early_order_id，这是正确的
                    self._save_rules()
                    self.update_chart()
        
        # 最终验证
        if rule.get('early_order', False) and rule.get('executed', False):
            self.logger.error(f"[{self.stock_code}] ❌❌❌ 严重错误：提前下单任务在finally后仍然被标记为已执行！强制修复: {rule_name}")
            rule['executed'] = False
            self._save_rules()
            self.update_chart()
        
        self.logger.info(f"[{self.stock_code}] ✅ 提前下单成功: {rule_name} ({trade_type} {volume}股 @ {price:.2f}元，订单ID: {rule.get('early_order_id', '未知')})，执行状态: executed={rule.get('executed', False)}，等待价格达到")
        self.update_chart()
    
    def _check_early_orders(self, tick_data):
        """检查提前下单的任务是否达到价格"""
        try:
            from utils.qmt_execution_config import use_builtin_order_execution

            if use_builtin_order_execution():
                return
        except Exception:
            pass
        current_price = tick_data.get('lastPrice', 0)
        if current_price <= 0:
            return
        
        from datetime import datetime
        
        for rule in self.rules:
            # 只检查已提前下单但未执行的任务
            if not rule.get('early_order', False) or rule.get('executed', False):
                continue
            
            rule_type = rule.get('type')
            if rule_type not in ('single_buy', 'single_sell', 'grid_buy', 'grid_sell'):
                # 突破等类型不应保留提前下单状态
                if self._rule_has_active_early_order(rule) and not rule.get('executed', False):
                    self._cancel_single_early_order(rule)
                continue
            early_order_price = rule.get('early_order_price', 0)
            rule_name = rule.get('name', '未命名规则')
            
            # 检查是否达到价格
            price_reached = False
            
            if rule_type == 'single_buy':
                # 买入：当前价 <= 目标价（价格跌到买入价或以下时，买入订单成交）
                if current_price <= early_order_price:
                    price_reached = True
            elif rule_type == 'single_sell':
                # 卖出：当前价 >= 目标价（价格涨到卖出价或以上时，卖出订单成交）
                if current_price >= early_order_price:
                    price_reached = True
            elif rule_type == 'cage_buy':
                price_low = rule.get('price_low', 0)
                price_high = rule.get('price_high', 0)
                inner_low, inner_high = self._get_cage_inner_bounds(rule)
                cage_entered = rule.get('cage_entered', False)
                if inner_low < current_price < inner_high:
                    if not cage_entered:
                        rule['cage_entered'] = True
                        cage_entered = True
                        self.logger.info(f"[{self.stock_code}] 📍 笼子买入：价格进入有效笼子，标记为已进入: {rule_name} (当前价: {current_price:.2f}元, 有效区间: [{inner_low:.2f}, {inner_high:.2f}]元)")
                if cage_entered and (current_price <= inner_low or current_price >= price_high):
                    price_reached = True
            elif rule_type == 'cage_sell':
                price_low = rule.get('price_low', 0)
                price_high = rule.get('price_high', 0)
                inner_low, inner_high = self._get_cage_inner_bounds(rule)
                cage_entered = rule.get('cage_entered', False)
                if inner_low < current_price < inner_high:
                    if not cage_entered:
                        rule['cage_entered'] = True
                        cage_entered = True
                        self.logger.info(f"[{self.stock_code}] 📍 笼子卖出：价格进入有效笼子，标记为已进入: {rule_name} (当前价: {current_price:.2f}元, 有效区间: [{inner_low:.2f}, {inner_high:.2f}]元)")
                if cage_entered and (current_price <= price_low or current_price >= inner_high):
                    price_reached = True
            elif rule_type == 'grid_buy':
                # 网格买入：检查当前价是否达到提前下单的网格点价格
                # 提前买入订单：当前价 <= 下单价格时成交
                if current_price <= early_order_price:
                    price_reached = True
            elif rule_type == 'grid_sell':
                # 网格卖出：检查当前价是否达到提前下单的网格点价格
                # 提前卖出订单：当前价 >= 下单价格时成交
                if current_price >= early_order_price:
                    price_reached = True
            
            if price_reached:
                live_status = self._get_early_order_live_status(rule)
                if live_status in ('cancelled', 'missing'):
                    self._clear_early_order_state(rule)
                    self._save_rules()
                    self.logger.info(
                        f"[{self.stock_code}] 提前下单价格已达但委托已无效({live_status})，"
                        f"状态已复位，等待重新挂单: {rule_name}"
                    )
                    continue
                if live_status == 'unknown':
                    continue

                # 对于网格交易，需要找到对应的grid_index并更新executed_grids
                grid_index = None
                if rule_type in ['grid_buy', 'grid_sell']:
                    start_price = rule.get('start_price', 0)
                    end_price = rule.get('end_price', 0)
                    num_grids = rule.get('num_grids', 2)
                    
                    # 计算所有网格价格点，找到与early_order_price匹配的grid_index
                    for i in range(num_grids + 1):
                        if rule_type == 'grid_buy':
                            if i == 0:
                                grid_price = start_price
                            elif i == num_grids:
                                grid_price = end_price
                            else:
                                precision = self._get_price_precision()
                                grid_price = round(start_price - (start_price - end_price) * i / num_grids, precision)
                        else:  # grid_sell
                            if i == 0:
                                grid_price = start_price
                            elif i == num_grids:
                                grid_price = end_price
                            else:
                                precision = self._get_price_precision()
                                grid_price = round(start_price + (end_price - start_price) * i / num_grids, precision)
                        
                        precision = self._get_price_precision()
                        if abs(grid_price - early_order_price) < 10 ** (-precision - 1):
                            grid_index = i
                            break
                
                order_id = rule.get('early_order_id', None) or 'EARLY_ORDER_CONFIRMED'

                if grid_index is not None:
                    if 'executed_grids' not in rule:
                        rule['executed_grids'] = []
                    if grid_index not in rule['executed_grids']:
                        rule['executed_grids'].append(grid_index)
                    if 'executed_grid_prices' not in rule:
                        rule['executed_grid_prices'] = {}
                    rule['executed_grid_prices'][grid_index] = current_price
                    volume_per_grid = rule.get('volume_per_grid', rule.get('volume', 0))
                    if 'executed_grid_volumes' not in rule:
                        rule['executed_grid_volumes'] = {}
                    rule['executed_grid_volumes'][grid_index] = volume_per_grid
                    self._clear_early_order_state(rule)

                    trade_info = {
                        'type': 'buy' if rule_type in ['single_buy', 'breakthrough_buy', 'cage_buy', 'grid_buy'] else 'sell',
                        'price': current_price,
                        'volume': volume_per_grid,
                        'reason': f'提前下单确认-{rule_name}',
                        'early_order': True,
                        'grid_index': grid_index,
                    }
                    exec_time = tick_data.get('time', datetime.now())
                    if not isinstance(exec_time, datetime):
                        exec_time = datetime.now()
                    self._record_execution(
                        rule, trade_info, tick_data, exec_time, current_price, volume_per_grid, order_id,
                        False, 'auto', approval_time=None,
                    )
                    self._save_rules()
                    self.logger.info(f"[{self.stock_code}] 提前下单任务已确认: {rule_name} (价格达到 {current_price:.2f}元)")
                    self._play_trade_sound()
                    if hasattr(self, 'canvas'):
                        self.canvas.draw_idle()
                else:
                    self._confirm_early_order_execution(
                        rule,
                        tick_data,
                        current_price,
                        order_id,
                        volume=self._early_order_submitted_volume(rule),
                    )
                continue
    
    def _execute_trade(self, rule, trade_info, tick_data):
        """
        执行交易
        """
        from PyQt5.QtWidgets import QMessageBox
        from datetime import datetime
        
        trade_type = trade_info.get('type')  # 'buy' or 'sell'
        price = trade_info.get('price')
        volume = trade_info.get('volume')
        reason = trade_info.get('reason')
        rule_id = rule.get('id')
        rule_name = rule.get('name', '未命名规则')
        
        # 0. 首先检查是否是提前下单（必须在最前面检查，避免被错误标记为已执行）
        is_early_order = trade_info.get('early_order', False) or rule.get('early_order', False)
        
        # 调试日志：确认提前下单状态
        if is_early_order:
            self.logger.debug(f"[{self.stock_code}] 🔍 _execute_trade: 识别为提前下单，规则名={rule_name}, early_order={is_early_order}, executed={rule.get('executed', False)}")
        
        # 检查规则是否已执行（提前下单的规则例外，允许重复处理以保存订单ID）
        if rule.get('executed', False) and not is_early_order:
            return  # 已执行且非提前下单，跳过

        # 0.25 builtin：禁止图表走 xt_trader，避免「已执行-下单失败」假完成
        try:
            from utils.qmt_execution_config import use_builtin_order_execution

            if use_builtin_order_execution() and not is_early_order:
                self.logger.info(
                    f"[{self.stock_code}] [builtin] 跳过图表 xt 下单: {rule_name} "
                    f"({trade_type} {volume}股 @ {price})；由大 QMT 内置策略执行"
                )
                return
        except Exception:
            pass

        # 0.5 开盘禁买时间窗：命中时将任务标记为已执行，但不实际下单
        if trade_type == 'buy' and not is_early_order and self._is_in_buy_block_window():
            from datetime import datetime
            exec_time = tick_data.get('time') if tick_data else datetime.now()
            if not isinstance(exec_time, datetime):
                exec_time = datetime.now()
            rule_type = rule.get('type')
            if rule_type in ['grid_buy', 'grid_sell']:
                grid_index = trade_info.get('grid_index')
                if grid_index is not None:
                    if 'executed_grids' not in rule:
                        rule['executed_grids'] = []
                    if grid_index not in rule['executed_grids']:
                        rule['executed_grids'].append(grid_index)
                    num_grids = rule.get('num_grids', 2)
                    if len(rule['executed_grids']) >= num_grids + 1:
                        rule['executed'] = True
            else:
                rule['executed'] = True

            rule['executed_time'] = exec_time.strftime('%Y-%m-%d %H:%M:%S')
            rule['executed_price'] = price
            rule['executed_volume'] = 0
            rule['order_id'] = 'SKIPPED_BUY_WINDOW'
            rule['executed_reason'] = 'buy_block_window'
            self._save_rules()
            self.update_chart()
            self.logger.info(
                f"[{self.stock_code}] ⏳ {rule_name} 命中开盘禁买时间窗，标记已执行并跳过下单 "
                f"(price={price:.2f}, volume={volume})"
            )
            self._record_skipped_execution(
                rule,
                trade_info,
                tick_data,
                price,
                0,
                "SKIPPED_BUY_WINDOW",
                f"命中开盘禁买时间窗，未下单（计划{volume}股 @ {price:.2f}元）",
                approval_result="buy_block_window",
                executed_reason="buy_block_window",
            )
            return
        
        # 0.6 买入资金预检：现金不足可等待；本笔金额低于下限则直接结束
        if trade_type == "buy" and not is_early_order:
            self._refresh_available_cash_before_buy()
            cash_check = self._assess_buy_cash_requirements(price, volume)
            if cash_check.get("blocked"):
                if cash_check.get("approval_result") == "order_below_min":
                    from datetime import datetime as _dt

                    exec_time = self._exec_time_from_tick(tick_data) or _dt.now()
                    rule["executed"] = True
                    rule["executed_time"] = exec_time.strftime("%Y-%m-%d %H:%M:%S")
                    rule["executed_price"] = price
                    rule["executed_volume"] = 0
                    rule["order_id"] = "SKIPPED_MIN_BUY"
                    rule["executed_reason"] = "order_below_min"
                    self._save_rules()
                    self.update_chart()
                    self.logger.info(
                        f"[{self.stock_code}] ⏳ {rule.get('name', '未命名规则')} - "
                        f"{cash_check['message']}，标记已执行并结束"
                    )
                    self._record_skipped_execution(
                        rule,
                        trade_info,
                        tick_data,
                        price,
                        0,
                        "SKIPPED_MIN_BUY",
                        cash_check["message"],
                        approval_result="order_below_min",
                        executed_reason="order_below_min",
                    )
                    return
                self._defer_buy_for_insufficient_funds(
                    rule,
                    trade_info,
                    tick_data,
                    price,
                    cash_check["order_id"],
                    cash_check["message"],
                    cash_check["approval_result"],
                )
                return
            adjusted_volume = int(cash_check.get("volume") or volume)
            if adjusted_volume != volume:
                order_type_desc = "提前下单" if is_early_order else "普通下单"
                self.logger.warning(
                    f"[{self.stock_code}] {order_type_desc}-{reason} - 资金不足，自动调整买入数量："
                    f"{volume}股 → {adjusted_volume}股（可用余额: {self.available_cash:.2f}元，能买多少买多少）"
                )
                volume = adjusted_volume
                trade_info["volume"] = volume
        
        # 1. 检查是否需要人工审核（从全局配置读取）
        require_manual_approval = self._load_require_manual_approval()
        
        # 提前下单的情况：不需要人工审核，因为订单已经提前下单了，只需要等待价格达到即可
        if require_manual_approval and not is_early_order:
            # 先标记任务为已完成，避免重复触发（但提前下单的情况不标记）
            from datetime import datetime
            rule_type = rule.get('type')
            
            # 对于网格规则，只标记特定网格点为已执行
            if rule_type in ['grid_buy', 'grid_sell']:
                grid_index = trade_info.get('grid_index')
                if grid_index is not None:
                    if 'executed_grids' not in rule:
                        rule['executed_grids'] = []
                    if 'executed_grid_prices' not in rule:
                        rule['executed_grid_prices'] = {}  # {grid_index: fixed_price}
                    if grid_index not in rule['executed_grids']:
                        rule['executed_grids'].append(grid_index)
                        # 保存已执行节点的固定价格（使用成交价格）
                        trade_price = trade_info.get('price', 0)
                        if trade_price > 0:
                            rule['executed_grid_prices'][grid_index] = trade_price
                        
                        # 保存已执行节点的固定股数（使用成交股数）
                        if 'executed_grid_volumes' not in rule:
                            rule['executed_grid_volumes'] = {}  # {grid_index: fixed_volume}
                        trade_volume = trade_info.get('volume', 0)
                        if trade_volume > 0:
                            rule['executed_grid_volumes'][grid_index] = trade_volume
                        else:
                            # 如果没有成交股数，使用volume_per_grid作为后备
                            volume_per_grid = rule.get('volume_per_grid', 0)
                            if volume_per_grid > 0:
                                rule['executed_grid_volumes'][grid_index] = volume_per_grid
                    # 检查是否所有网格点都已执行
                    num_grids = rule.get('num_grids', 2)
                    if len(rule['executed_grids']) >= num_grids + 1:
                        rule['executed'] = True
            elif rule_type in ['best_buy', 'best_sell']:
                # 对于弹性买入/卖出规则，标记为已执行
                rule['executed'] = True
            elif not self._should_defer_breakthrough_probe_finish(trade_info):
                # 对于其他规则，标记为已执行
                rule['executed'] = True
            
            # 保存状态
            self._save_rules()
            
            # 构建确认消息
            trade_type_cn = "买入" if trade_type == 'buy' else "卖出"
            message = f"<b>{self.stock_code} {self.stock_name}</b><br><br>"
            message += f"规则：{rule_name}<br>"
            message += f"操作：{trade_type_cn}<br>"
            message += f"价格：{price:.2f}元<br>"
            message += f"数量：{volume}股<br>"
            message += f"原因：{reason}<br><br>"
            message += f"是否执行此交易？"
            
            # 记录审核开始时间
            approval_start_time = datetime.now()
            
            # 弹出确认对话框（模态，但不会阻塞其他任务，因为它们在不同的线程/事件循环中）
            reply = QMessageBox.question(
                self,
                f"人工审核 - {trade_type_cn}确认",
                message,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            # 记录审核结束时间
            approval_time = datetime.now()
            
            if reply != QMessageBox.Yes:
                # 用户选择不执行
                self.logger.info(f"[{self.stock_code}] ❌ {reason} - 用户取消交易")
                
                # 记录取消时间（任务已处理，标记为已完成）
                from datetime import datetime
                exec_time = tick_data.get('time')
                if isinstance(exec_time, datetime):
                    rule['executed_time'] = exec_time.strftime('%Y-%m-%d %H:%M:%S')
                elif exec_time:
                    rule['executed_time'] = str(exec_time)
                else:
                    rule['executed_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
                rule['executed_price'] = price
                rule['executed_volume'] = volume
                rule['order_id'] = 'CANCELLED'  # 标记为已取消
                
                # 记录取消的执行记录
                from datetime import datetime
                exec_time = tick_data.get('time') if tick_data else datetime.now()
                if not isinstance(exec_time, datetime):
                    exec_time = datetime.now()
                rejected_info = dict(trade_info or {})
                rejected_info["skip_reason"] = "人工审核未通过，未下单"
                rejected_info["execution_outcome"] = "skipped"
                self._record_execution(rule, rejected_info, tick_data, exec_time, price, volume, 'CANCELLED', True, 'rejected', approval_time=approval_time)
                
                # 保存规则状态
                self._save_rules()
                
                # 更新图表显示
                self.update_chart()
                return
        
        # 1. 资金/持仓检查
        # ⚠️ 提前下单也需要检查资金/持仓，因为这是第一次下单，需要确保有足够的资金/持仓
        # 只有夜市委托（非提前下单）才跳过持仓检查
        if trade_type == 'buy':
            # 每次买入前都实时刷新可用现金，避免连续下单使用旧余额导致废单
            self._refresh_available_cash_before_buy()
            cash_check = self._assess_buy_cash_requirements(price, volume)
            if cash_check.get("blocked"):
                if cash_check.get("approval_result") == "order_below_min":
                    from datetime import datetime as _dt

                    exec_time = self._exec_time_from_tick(tick_data) or _dt.now()
                    rule["executed"] = True
                    rule["executed_time"] = exec_time.strftime("%Y-%m-%d %H:%M:%S")
                    rule["executed_price"] = price
                    rule["executed_volume"] = 0
                    rule["order_id"] = "SKIPPED_MIN_BUY"
                    rule["executed_reason"] = "order_below_min"
                    self._clear_early_order_state(rule)
                    self._save_rules()
                    self.update_chart()
                    self.logger.info(
                        f"[{self.stock_code}] ⏳ {rule.get('name', '未命名规则')} - "
                        f"{cash_check['message']}，标记已执行并结束"
                    )
                    self._record_skipped_execution(
                        rule,
                        trade_info,
                        tick_data,
                        price,
                        0,
                        "SKIPPED_MIN_BUY",
                        cash_check["message"],
                        approval_result="order_below_min",
                        executed_reason="order_below_min",
                    )
                    return
                self._defer_buy_for_insufficient_funds(
                    rule,
                    trade_info,
                    tick_data,
                    price,
                    cash_check["order_id"],
                    cash_check["message"],
                    cash_check["approval_result"],
                    revert_executed=bool(require_manual_approval and not is_early_order),
                )
                return
            adjusted_volume = int(cash_check.get("volume") or volume)
            if adjusted_volume != volume:
                order_type_desc = "提前下单" if is_early_order else "普通下单"
                self.logger.warning(
                    f"[{self.stock_code}] {order_type_desc}-{reason} - 资金不足，自动调整买入数量："
                    f"{volume}股 → {adjusted_volume}股（可用余额: {self.available_cash:.2f}元，能买多少买多少）"
                )
                volume = adjusted_volume
                trade_info["volume"] = volume
        elif trade_type == 'sell':
            # 卖出：检查持仓是否充足
            # ⚠️ 夜市委托特殊处理：不检查持仓，直接使用客户指定的股数
            is_night_market = reason == '夜市委托'
            
            if not is_night_market:
                # 非夜市委托：检查持仓（包括提前下单）
                if self.position_volume <= 0:
                    if self._skip_sell_due_to_pending_order(
                        rule, trade_info, tick_data, price, volume, "卖出"
                    ):
                        return
                    self._finalize_no_position_sell(rule, trade_info, tick_data, price, volume, "卖出")
                    return
                
                # 如果卖出数量大于可用持仓，自动调整为可用持仓数量（有多少卖多少）
                if volume > self.position_volume:
                    original_volume = volume
                    volume = self.position_volume
                    order_type_desc = "提前下单" if is_early_order else "普通下单"
                    self.logger.warning(f"[{self.stock_code}] {order_type_desc}-{reason} - 持仓不足，自动调整卖出数量：{original_volume}股 → {volume}股（可用持仓: {self.position_volume}股）")
                    # 更新 trade_info 中的 volume
                    trade_info['volume'] = volume
            else:
                # 夜市委托：直接使用客户指定的股数，不检查持仓
                self.logger.info(f"[{self.stock_code}] 夜市委托 - 跳过持仓检查，直接使用指定股数: {volume}股")
        
        # 1.5. 价格验证：检查买入价格是否超过涨停价，卖出价格是否低于跌停价
        try:
            self._ensure_session_prev_close_and_limits()
        except Exception:
            pass
        if trade_type == 'buy':
            # 获取涨停价
            limit_up_price = None
            limit_down_price = None
            if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                limit_up_price = self.limit_up_price
            else:
                # 如果没有涨停价，尝试计算
                if hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                    limit_up_price, limit_down_price = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
            if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                limit_down_price = self.limit_down_price
            
            # 如果价格超过涨停价，调整为涨停价
            if limit_up_price and price > limit_up_price:
                original_price = price
                price = limit_up_price
                self.logger.warning(f"[{self.stock_code}] ⚠️ 买入价格 {original_price:.2f} 超过涨停价 {limit_up_price:.2f}，已调整为涨停价")
                # 更新 trade_info 中的 price
                trade_info['price'] = price
            # 如果价格低于跌停价，调整为跌停价（买入也需要兜底）
            if limit_down_price and price < limit_down_price:
                original_price = price
                price = limit_down_price
                self.logger.warning(f"[{self.stock_code}] ⚠️ 买入价格 {original_price:.2f} 低于跌停价 {limit_down_price:.2f}，已调整为跌停价")
                trade_info['price'] = price
        elif trade_type == 'sell':
            limit_up_price = None
            limit_down_price = None
            if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                limit_up_price = self.limit_up_price
            if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                limit_down_price = self.limit_down_price
            if (not limit_up_price or not limit_down_price) and hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                lu, ld = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                if lu and not limit_up_price:
                    limit_up_price = lu
                if ld and not limit_down_price:
                    limit_down_price = ld
            
            # 如果价格高于涨停价，调整为涨停价（与买入侧对称，防止规则价异常）
            if limit_up_price and price > limit_up_price:
                original_price = price
                price = limit_up_price
                self.logger.warning(f"[{self.stock_code}] ⚠️ 卖出价格 {original_price:.2f} 超过涨停价 {limit_up_price:.2f}，已调整为涨停价")
                trade_info['price'] = price
            # 如果价格低于跌停价，调整为跌停价
            if limit_down_price and price < limit_down_price:
                original_price = price
                price = limit_down_price
                self.logger.warning(f"[{self.stock_code}] ⚠️ 卖出价格 {original_price:.2f} 低于跌停价 {limit_down_price:.2f}，已调整为跌停价")
                trade_info['price'] = price
        
        # 1.6. 计算带滑点的交易价格
        # 检查是否为夜市委托（夜市委托不使用滑点）
        is_night_market = '夜市' in reason or reason == '夜市委托'
        
        # 非夜市委托、非提前下单：买入用卖一+一跳，卖出用买一-一跳（tick 触发成交时抢盘口）。
        # 提前下单一律按目标价限价排队，不加减盘口滑点。
        needs_slippage = not is_night_market
        
        # 14:57:00-15:00:00 收盘集合竞价：直接用用户指定价格报价，体现用户意图，不加滑点、不用买卖盘
        from datetime import time as dt_time
        from core.utils.security_type import SecurityTypeUtil
        precision = 3 if SecurityTypeUtil.is_fund(self.stock_code) else 2
        current_time = None
        if tick_data and tick_data.get('time'):
            t = tick_data['time']
            if hasattr(t, 'time'):
                current_time = t.time()
            elif isinstance(t, (int, float)):
                from datetime import datetime
                current_time = datetime.fromtimestamp(t / 1000.0 if t > 1e12 else t).time()
        if current_time is None:
            current_time = __import__('datetime').datetime.now().time()
        in_closing_call_auction = dt_time(14, 57, 0) <= current_time < dt_time(15, 0, 0)
        
        if in_closing_call_auction and needs_slippage:
            # 集合竞价阶段：用用户指定价格（规则中的 price 或触发价），仅四舍五入到精度
            user_price = rule.get('price') if rule.get('price') and rule.get('price') > 0 else price
            price = round(user_price, precision)
            trade_info['price'] = price
            self.logger.info(f"[{self.stock_code}] 收盘集合竞价(14:57-15:00)，使用用户指定价格委托：{price:.{precision}f}")
        elif is_early_order and needs_slippage:
            limit_px = round(float(price or 0), precision)
            if limit_px <= 0:
                limit_px = round(
                    float(rule.get('early_order_price') or rule.get('price') or 0),
                    precision,
                )
            if limit_up_price and limit_up_price > 0 and limit_px > limit_up_price:
                limit_px = round(float(limit_up_price), precision)
            if limit_down_price and limit_down_price > 0 and limit_px < limit_down_price:
                limit_px = round(float(limit_down_price), precision)
            price = limit_px
            trade_info['price'] = price
            side_txt = '买入' if trade_type == 'buy' else '卖出'
            cur_px = round(float(self.current_price or 0), precision)
            self.logger.info(
                f"[{self.stock_code}] 提前下单{side_txt}：按目标价限价排队 {price:.{precision}f} "
                f"(当前价={cur_px:.{precision}f})"
            )
        elif needs_slippage and tick_data:
            try:
                # 根据精度设置滑点值
                slippage = 0.001 if precision == 3 else 0.01
                
                if trade_type == 'buy':
                    # 买入时，以卖一价（askPrice[0]）为基准，加上滑点（向上调整）
                    # askPrice是卖档（卖方挂单），askPrice[0]是卖一价
                    if 'askPrice' in tick_data and tick_data['askPrice'] and len(tick_data['askPrice']) > 0:
                        base_price = tick_data['askPrice'][0]
                    else:
                        # 如果没有askPrice，使用当前价格
                        base_price = price
                    # ask一价异常（如为0），回退到当前委托价，避免滑点后出现不合理价格
                    if base_price is None or float(base_price) <= 0:
                        base_price = price
                    # 向上调整一个最小单位
                    trade_price = round(base_price + slippage, precision)
                    # 涨跌停钳制：避免滑点把买入价推到不允许的区间
                    if limit_up_price and limit_up_price > 0 and trade_price > limit_up_price:
                        trade_price = round(float(limit_up_price), precision)
                    if limit_down_price and limit_down_price > 0 and trade_price < limit_down_price:
                        trade_price = round(float(limit_down_price), precision)
                    # 更新价格
                    if trade_price != price:
                        # 根据reason确定交易类型名称
                        reason_type_map = {
                            '单点买入触发': '单点买入',
                            '突破买入触发': '突破买入',
                            '笼子买入触发': '笼子买入',
                            '网格买入触发': '网格买入',
                            '弹性买入触发': '弹性买入'
                        }
                        reason_type = reason_type_map.get(reason, '买入')
                        self.logger.info(
                            f"[{self.stock_code}] {reason_type}价格调整：{price:.{precision}f} → {trade_price:.{precision}f} "
                            f"(卖一价={base_price:.{precision}f}, 滑点=+{slippage:.{precision}f})"
                        )
                        price = trade_price
                        trade_info['price'] = price
                else:  # sell
                    # 卖出时，以买一价（bidPrice[0]）为基准，减去滑点（向下调整）
                    if 'bidPrice' in tick_data and tick_data['bidPrice'] and len(tick_data['bidPrice']) > 0:
                        base_price = tick_data['bidPrice'][0]
                    else:
                        base_price = price
                    trade_price = round(base_price - slippage, precision)

                    if limit_down_price and limit_down_price > 0:
                        limit_dn = round(float(limit_down_price), precision)
                        cur_px = round(float(self.current_price or 0), precision)
                        if cur_px <= limit_dn or trade_price < limit_dn:
                            if trade_price != limit_dn:
                                self.logger.info(
                                    f"[{self.stock_code}] 卖出委托价钳制：{trade_price:.{precision}f} → 跌停价 {limit_dn:.{precision}f} "
                                    f"(买一价={float(base_price):.{precision}f}, 滑点=-{slippage:.{precision}f})"
                                )
                            trade_price = limit_dn
                    if trade_price != price:
                        reason_type_map = {
                            '单点卖出触发': '单点卖出',
                            '突破卖出触发': '突破卖出',
                            '笼子卖出触发': '笼子卖出',
                            '网格卖出触发': '网格卖出',
                            '弹性卖出触发': '弹性卖出'
                        }
                        reason_type = reason_type_map.get(reason, '卖出')
                        self.logger.info(
                            f"[{self.stock_code}] {reason_type}价格调整：{price:.{precision}f} → {trade_price:.{precision}f} "
                            f"(买一价={base_price:.{precision}f}, 滑点=-{slippage:.{precision}f})"
                        )
                        price = trade_price
                        trade_info['price'] = price
            except Exception as e:
                # 如果计算滑点失败，使用原价格，记录警告但不影响交易
                self.logger.warning(f"[{self.stock_code}] 计算滑点价格失败，使用原价格: {str(e)}")
        
        # 1.7 智能卖出：非提前下单卖单走盘口自适应流程（不立即标记 executed）
        if trade_type == 'sell' and not is_early_order:
            if hasattr(self, 'smart_sell_runner') and self.smart_sell_runner.try_intercept_execute_trade(rule, trade_info, tick_data):
                return

        # 2. 调用QMT下单接口
        try:
            if hasattr(self, 'task_manager') and self.task_manager:
                qmt_adapter = self.task_manager.qmt_adapter if hasattr(self.task_manager, 'qmt_adapter') else None
                
                if qmt_adapter:
                    from core.smart_sell import resolve_order_strategy_name

                    order_strategy_name = resolve_order_strategy_name(
                        rule,
                        trade_info,
                        in_closing_auction=(in_closing_call_auction and needs_slippage),
                    )
                    # 发送交易指令
                    order_id = qmt_adapter.trade(
                        stock_code=self.stock_code,
                        order_type=trade_type,
                        price=price,
                        volume=volume,
                        strategy_name=order_strategy_name
                    )
                    
                    # 检查订单号是否有效
                    # 注意：非交易时段下单可能返回-1，但订单可能已成功提交（夜市委托）
                    # 对于提前下单，即使返回-1也保持early_order标记，等待订单列表查询更新真实订单ID
                    if not order_id or order_id == '' or order_id == '-1' or order_id == '0':
                        if is_early_order:
                            # 提前下单：即使返回-1，也保持early_order标记
                            # 保存-1作为临时标记，等待订单列表查询后通过匹配更新为真实order_sysid
                            self.logger.warning(f"[{self.stock_code}] ⚠️ 提前下单接口返回-1: {rule_name}, 价格={price:.2f}, 数量={volume}")
                            self.logger.info(f"[{self.stock_code}] 保持提前下单标记，等待订单列表查询后匹配真实订单ID（可能已作为夜市委托提交）")
                            # 保存-1作为临时标记，后续通过订单列表匹配更新
                            rule['early_order_id'] = '-1'
                            # 不弹窗，因为订单可能已成功提交（非交易时段的夜市委托）
                            # 继续执行，保存规则状态
                        else:
                            # 非提前下单：返回-1视为失败
                            error_msg = f"[{self.stock_code}] ❌ {reason} - 下单失败，订单号无效: {order_id}"
                            self.logger.error(error_msg)
                            self._record_order_failed_execution(
                                rule,
                                trade_info,
                                tick_data,
                                price,
                                volume,
                                "下单接口返回无效订单号，未提交委托，规则已结束",
                                raw_order_id=order_id,
                            )
                            return
                    else:
                        # 订单号有效，正常保存
                        pass
                    
                    # 3. 记录执行日志
                    self.logger.info(f"[{self.stock_code}] ✅ {reason} - {trade_type} {volume}股 @ {price:.2f} (订单号: {order_id})")
                    # 买入下单后本地预扣可用现金，避免资产回调未及时更新时的连续超额下单
                    if trade_type == 'buy':
                        try:
                            reserve = float(price) * float(volume)
                            if reserve > 0:
                                self.available_cash = max(0.0, float(self.available_cash or 0) - reserve)
                        except Exception:
                            pass
                    
                    # 4. 标记规则为已执行（如果还没标记）
                    # ⚠️ 提前下单的情况：绝对不标记为已执行，只保存订单ID，等待价格达到时再标记
                    # 强制执行检查：如果是提前下单，确保不会标记为已执行
                    if is_early_order:
                        # 提前下单：只保存订单ID，不标记为已执行
                        # 如果之前被错误标记，立即清除
                        if rule.get('executed', False):
                            self.logger.error(f"[{self.stock_code}] ❌ 提前下单任务在_execute_trade中被错误标记为已执行！立即修复: {rule_name}, executed={rule.get('executed')}, early_order={is_early_order}")
                            rule['executed'] = False
                        # 保存订单ID（注意：同步下单返回的可能是委托编号，真实的order_sysid需要通过委托回报更新）
                        rule['early_order_id'] = str(order_id)
                        rule['early_order_submit_price'] = price
                        rule['early_order_submit_volume'] = int(volume or 0)
                        # 不更新executed_price、executed_volume、executed_time，等待价格达到时再更新
                        # 再次确认：提前下单绝对不能标记为已执行
                        if rule.get('executed', False):
                            self.logger.error(f"[{self.stock_code}] ❌❌ 严重：提前下单任务在下单后仍然被标记为已执行！强制修复: {rule_name}")
                            rule['executed'] = False
                    else:
                        # 非提前下单的情况：正常标记为已执行
                        # 如果启用了人工审核，已经在前面标记过了，这里只需要更新执行时间和订单号
                        if not require_manual_approval:
                            from datetime import datetime
                            rule_type = rule.get('type')
                            
                            # 对于网格规则，只标记特定网格点为已执行
                            if rule_type in ['grid_buy', 'grid_sell']:
                                grid_index = trade_info.get('grid_index')
                                if grid_index is not None:
                                    # 初始化 executed_grids 列表
                                    if 'executed_grids' not in rule:
                                        rule['executed_grids'] = []
                                    if 'executed_grid_prices' not in rule:
                                        rule['executed_grid_prices'] = {}  # {grid_index: fixed_price}
                                    
                                    # 添加已执行的网格索引
                                    if grid_index not in rule['executed_grids']:
                                        rule['executed_grids'].append(grid_index)
                                        # 保存已执行节点的固定价格（使用成交价格）
                                        trade_price = trade_info.get('price', 0)
                                        if trade_price > 0:
                                            rule['executed_grid_prices'][grid_index] = trade_price
                                        elif self.current_price > 0:
                                            # 如果没有成交价格，使用当前价格作为后备
                                            rule['executed_grid_prices'][grid_index] = self.current_price
                                        
                                        # 保存已执行节点的固定股数（使用成交股数）
                                        if 'executed_grid_volumes' not in rule:
                                            rule['executed_grid_volumes'] = {}  # {grid_index: fixed_volume}
                                        trade_volume = trade_info.get('volume', 0)
                                        if trade_volume > 0:
                                            rule['executed_grid_volumes'][grid_index] = trade_volume
                                        else:
                                            # 如果没有成交股数，使用volume_per_grid作为后备
                                            volume_per_grid = rule.get('volume_per_grid', 0)
                                            if volume_per_grid > 0:
                                                rule['executed_grid_volumes'][grid_index] = volume_per_grid
                                    
                                    # 检查是否所有网格点都已执行
                                    num_grids = rule.get('num_grids', 2)
                                    if len(rule['executed_grids']) >= num_grids + 1:
                                        # 所有网格点都已执行，标记整个规则为已执行
                                        rule['executed'] = True
                            elif rule_type in ['best_buy', 'best_sell']:
                                # 对于弹性买入/卖出规则，标记为已执行
                                # 保留triggered和lowest_price/highest_price状态以供查看
                                rule['executed'] = True
                            elif rule_type in ['night_buy', 'night_sell']:
                                # 夜市委托：不立即标记为已执行，等待订单回报确认"已报"状态
                                rule['night_market_order_id'] = str(order_id)
                                rule['night_market_pending'] = True  # 标记为等待订单回报确认
                                self.logger.info(f"[{self.stock_code}] 夜市委托下单成功，等待订单回报确认: {rule_name} (订单号: {order_id})")
                            elif self._should_defer_breakthrough_probe_finish(trade_info):
                                self._init_breakthrough_probe_state(rule, trade_info, tick_data)
                            else:
                                # 对于其他非网格规则，标记整个规则为已执行
                                rule['executed'] = True
                        
                        # 非提前下单：正常保存执行信息（夜市委托除外）
                        if rule_type not in ['night_buy', 'night_sell']:
                            if self._should_defer_breakthrough_probe_finish(trade_info):
                                rule['executed_volume'] = int(trade_info.get('volume') or 0)
                                rule['executed_price'] = price
                                rule['probe_order_id'] = str(order_id)
                            else:
                                rule['executed_price'] = price
                                rule['executed_volume'] = volume
                                rule['order_id'] = str(order_id)
                        # 对于笼子买卖规则，保存触发的端点信息
                        if trade_info.get('executed_endpoint'):
                            rule['executed_endpoint'] = trade_info.get('executed_endpoint')  # 'low' 或 'high'
                        # 更新执行时间
                        from datetime import datetime
                        exec_time = tick_data.get('time')
                        if isinstance(exec_time, datetime):
                            rule['executed_time'] = exec_time.strftime('%Y-%m-%d %H:%M:%S')
                        elif exec_time:
                            rule['executed_time'] = str(exec_time)
                        else:
                            rule['executed_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        self._stash_true_breakthrough_detail_on_rule(rule, trade_info)
                    # else: 提前下单的情况已经在上面处理了（只保存early_order_id，不标记为已执行）
                    
                    # ⚠️ 最终安全检查：如果是提前下单，绝对不能标记为已执行
                    if is_early_order and rule.get('executed', False):
                        self.logger.error(f"[{self.stock_code}] ❌ 严重错误：提前下单任务在保存前被标记为已执行，强制修复！")
                        rule['executed'] = False
                        if 'executed_time' in rule:
                            del rule['executed_time']
                        if 'executed_price' in rule:
                            del rule['executed_price']
                        if 'executed_volume' in rule:
                            del rule['executed_volume']
                    
                    # 保存规则状态
                    self._save_rules()
                    
                    # 5. 记录执行记录（提前下单不记录，等待价格达到时再记录）
                    if not is_early_order:
                        # 非提前下单：正常记录执行记录
                        approval_result = 'approved' if require_manual_approval else 'auto'
                        approval_time_for_record = approval_time if require_manual_approval else None
                        exec_time = tick_data.get('time')
                        if isinstance(exec_time, datetime):
                            exec_time_obj = exec_time
                        elif exec_time:
                            try:
                                exec_time_obj = datetime.strptime(str(exec_time), '%Y-%m-%d %H:%M:%S')
                            except:
                                exec_time_obj = datetime.now()
                        else:
                            exec_time_obj = datetime.now()
                        
                        ordered_info = dict(trade_info or {})
                        ordered_info["execution_outcome"] = "ordered"
                        self._record_execution(
                            rule,
                            ordered_info,
                            tick_data,
                            exec_time_obj,
                            price,
                            volume,
                            order_id,
                            require_manual_approval,
                            approval_result,
                            approval_time=approval_time_for_record,
                        )
                        # 播放交易执行音效
                        self._play_trade_sound()
                    # 提前下单的情况：不记录执行记录，等待价格达到时再记录
                    
                    # 6. 更新图表显示
                    self.update_chart()
                    
                else:
                    self.logger.error(f"[{self.stock_code}] QMT适配器不可用，无法执行交易")
                    self._record_order_failed_execution(
                        rule,
                        trade_info,
                        tick_data,
                        price,
                        volume,
                        "QMT适配器不可用，未能调用下单接口，规则已结束",
                    )
            else:
                self.logger.error(f"[{self.stock_code}] 任务管理器不可用，无法执行交易")
                self._record_order_failed_execution(
                    rule,
                    trade_info,
                    tick_data,
                    price,
                    volume,
                    "任务管理器不可用，未能调用下单接口，规则已结束",
                )
                
        except Exception as e:
            self.logger.error(f"[{self.stock_code}] 执行交易失败: {str(e)}", exc_info=True)
            try:
                fail_price = float(price or (trade_info or {}).get("price") or 0)
                fail_volume = int(volume or (trade_info or {}).get("volume") or 0)
                self._record_order_failed_execution(
                    rule,
                    trade_info,
                    tick_data,
                    fail_price,
                    fail_volume,
                    f"执行交易异常：{e}，规则已结束",
                )
            except Exception:
                pass
    
    @property
    def logger(self):
        """获取logger"""
        if hasattr(self, 'parent') and self.parent() and hasattr(self.parent(), 'logger'):
            return self.parent().logger
        # 如果没有父组件的logger，创建一个临时的
        from utils.logger import Logger
        if not hasattr(self, '_logger'):
            self._logger = Logger()
        return self._logger
        
    def calculate_limit_prices(self, stock_code, base_price):
        """计算涨跌停板价格"""
        try:
            from utils.limit_ratio import get_limit_ratio

            stock_name = self.stock_name or ""
            limit_ratio = get_limit_ratio(stock_code, stock_name)
            
            # 使用统一的价格精度工具获取精度（根据股票代码，而不是价格大小）
            precision = self._get_price_precision()
            
            # 计算原始价格（未四舍五入）
            limit_up_price_raw = base_price * (1 + limit_ratio)
            limit_down_price_raw = base_price * (1 - limit_ratio)
            
            # A股涨停板价格使用标准四舍五入（不是银行家舍入）
            # Python的round()使用银行家舍入（round half to even），对于10.285会舍入为10.28
            # 股票价格应该使用标准四舍五入，10.285应该舍入为10.29
            def stock_price_round(value, precision):
                """
                A股价格标准四舍五入函数
                规则：小数部分 >= 0.5 时向上取整，< 0.5 时向下取整
                例如：10.285 -> 10.29, 10.284 -> 10.28
                """
                multiplier = 10 ** precision
                # 使用 math.floor(value * multiplier + 0.5) 实现标准四舍五入
                # 这样可以避免银行家舍入的问题
                return math.floor(value * multiplier + 0.5) / multiplier
            
            limit_up_price = stock_price_round(limit_up_price_raw, precision)
            limit_down_price = stock_price_round(limit_down_price_raw, precision)
            
            return limit_up_price, limit_down_price
        except Exception as e:
            print(f"计算涨跌停板失败: {e}")
            # 默认按10%计算
            precision = self._get_price_precision()
            limit_up = round(base_price * 1.10, precision)
            limit_down = round(base_price * 0.90, precision)
            self.logger.warning(f"[{stock_code}] calculate_limit_prices异常，使用默认计算: 涨停板={limit_up:.{precision}f}, 跌停板={limit_down:.{precision}f}")
            return limit_up, limit_down
    
    def calculate_key_points(self, force_recalculate=False):
        """计算关键价格点（复用web版的key_price_calculator）
        Args:
            force_recalculate: 是否强制重新计算（默认False，使用缓存）
        """
        # 防止频繁重复计算：如果已经计算过且数据有效，且不是强制重新计算，则跳过
        if not force_recalculate:
            if (hasattr(self, '_key_points_calculated') and self._key_points_calculated and
                hasattr(self, 'prev_close_price') and self.prev_close_price > 0 and
                hasattr(self, 'limit_up_price') and self.limit_up_price > 0 and
                hasattr(self, 'limit_down_price') and self.limit_down_price > 0):
                # 已经有有效数据，且不是强制重新计算，跳过
                return
        
        try:
            # 使用web版的key_price_calculator计算关键价格点（它会自动处理昨收盘的逻辑）
            from key_price_calculator import KeyPriceCalculator
            calculator = KeyPriceCalculator()
            key_price_result = calculator.calculate_key_points(self.stock_code)
            
            if not key_price_result:
                print(f"计算关键价格点失败: {self.stock_code}")
                return
            
            # 从结果中提取昨收盘、涨停板、跌停板价格
            prev_close_from_calculator = None
            limit_up_from_calculator = None
            limit_down_from_calculator = None
            
            for item in key_price_result:
                if isinstance(item, dict):
                    name = item.get('name', '')
                    price = item.get('price', 0)
                    if price and isinstance(price, (int, float)):
                        price = float(price)
                        if name == '昨收盘':
                            prev_close_from_calculator = price
                            self.prev_close_price = price
                        elif name == '涨停板':
                            limit_up_from_calculator = price
                        elif name == '跌停板':
                            limit_down_from_calculator = price
            
            if prev_close_from_calculator is None:
                print(f"无法从key_price_calculator获取昨收盘价格: {self.stock_code}")
                return
            
            # 使用key_price_calculator返回的涨跌停板价格
            if limit_up_from_calculator is not None and limit_down_from_calculator is not None:
                self.limit_up_price = limit_up_from_calculator
                self.limit_down_price = limit_down_from_calculator
            else:
                # 如果没有找到，使用计算的方法作为后备
                self.limit_up_price, self.limit_down_price = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
            
            # 初始化关键价格点列表
            self.key_points = []
            
            # 将key_price_calculator返回的结果转换为内部格式
            for item in key_price_result:
                if isinstance(item, dict):
                    name = item.get('name', '')
                    price = item.get('price', 0)
                    # 更严格的检查：确保价格是有效的正数（不是NaN、不是0、不是负数）
                    if name and isinstance(price, (int, float)):
                        try:
                            price_float = float(price)
                            # 检查是否是NaN或无效值（math模块已在文件开头导入）
                            if not (math.isnan(price_float) or math.isinf(price_float) or price_float <= 0):
                                # 跳过"最新价"，我们有自己的current_price显示
                                if name != "最新价":
                                    self.key_points.append((name, price_float))
                        except (ValueError, TypeError):
                            # 如果转换失败，跳过这个价格点
                            continue

            try:
                from utils.qmt_execution_config import use_builtin_price_feed
                if use_builtin_price_feed():
                    from utils.builtin_live_prices import overlay_intraday_on_key_points
                    overlay_intraday_on_key_points(self.key_points, self.stock_code)
            except Exception:
                pass
            
            # 保存已计算的标志，避免重复计算
            self._key_points_calculated = True
            
        except Exception as e:
            # 即使失败也标记为已计算，避免无限重试
            self._key_points_calculated = True
    
    def update_chart(self):
        """更新图表显示"""
        # 直接绘制价格位置图
        self.draw_price_position_chart()

    def _rule_price_is_tradeable(self, price) -> bool:
        """当日有效撮合区间 [跌停, 涨停]。"""
        lu = getattr(self, "limit_up_price", 0) or 0
        ld = getattr(self, "limit_down_price", 0) or 0
        if lu <= 0 or ld <= 0 or lu < ld:
            return True
        try:
            p = float(price)
        except (TypeError, ValueError):
            return False
        if math.isnan(p) or p <= 0:
            return False
        return ld <= p <= lu

    def _append_rule_chart_point(self, price_points, price, label, color, draggable, volume):
        if not self._rule_price_is_tradeable(price):
            return
        price_points.append((float(price), label, color, draggable, volume))

    def _cage_pair_tradeable(self, price_low, price_high) -> bool:
        """笼子上下沿均须在 [跌停, 涨停] 内；否则不可撮合，不绘制。"""
        try:
            pl = float(price_low)
            ph = float(price_high)
        except (TypeError, ValueError):
            return False
        return self._rule_price_is_tradeable(pl) and self._rule_price_is_tradeable(ph)

    def _add_rule_price_points(self, price_points):
        """从规则列表生成价格点
        返回网格规则的点列表，用于绘制连接线
        """
        from core.trading_rules import RuleType, RULE_TYPE_COLORS
        from core.rule_activation import rule_activation_chart_suffix
        grid_lines = []  # 存储网格规则的连接线信息 [(rule_name, [(price, volume), ...]), ...]
        
        if not self.rules:
            return grid_lines

        for rule in self.rules:
            # 兼容持久化/导入时只写了 rule_type 的情况；枚举序列化后也可能是字符串
            rule_type = rule.get("type") or rule.get("rule_type")
            if isinstance(rule_type, str):
                rule_type = rule_type.strip()
            rule_name = rule.get('name', '未命名规则')
            rule_enabled = rule.get('enabled', True)
            rule_executed = rule.get('executed', False)
            
            # 对于定时清仓规则，检查 scheduled_clear_executed 字段
            if rule_type == 'scheduled_clear':
                scheduled_clear_executed = rule.get('scheduled_clear_executed', False)
                scheduled_clear_order_attempted = rule.get('scheduled_clear_order_attempted', False)
                # 获取定时清仓时间
                scheduled_clear_time = rule.get('scheduled_clear_time', '14:56:00')
                # 在标签中显示时间
                rule_name_with_time = f"{rule_name} ({scheduled_clear_time})"
                
                # 定时清仓规则：已执行时根据是否调用了下单指令区分显示
                if scheduled_clear_executed:
                    # 如果调用了下单指令（已下单），显示为灰色
                    if scheduled_clear_order_attempted:
                        color = '#999999'  # 灰色节点（已下单）
                        rule_name = f"[已执行] {rule_name_with_time}"
                    else:
                        # 如果没有调用下单指令，可能是价格不满足条件或时间已过
                        color = '#ffffff'  # 白色节点（已执行但未下单）
                        rule_name = f"[已执行] {rule_name_with_time}"
                elif not rule_enabled:
                    color = '#000000'  # 禁用：黑色（与“已执行/已结束”的灰色区分）
                    rule_name = f"[已禁用] {rule_name_with_time}"
                else:
                    # 定时清仓规则使用紫色，便于区分
                    color = '#9c27b0'  # 紫色
                    # 未执行且启用的规则，显示时间
                    rule_name = rule_name_with_time
            else:
                # 其他规则类型的颜色逻辑
                if rule_executed:
                    # 已执行的规则显示为深灰色；若是禁买窗口跳过，单独显示文案和颜色
                    executed_reason = str(rule.get('executed_reason', '') or '')
                    executed_order_id = str(rule.get('order_id', '') or '')
                    if (
                        executed_reason == 'buy_block_window'
                        or executed_order_id == 'SKIPPED_BUY_WINDOW'
                    ):
                        color = '#000000'  # 禁买窗口跳过：黑色节点，便于与常规已执行灰色区分
                        rule_name = f"[已执行-禁买跳过] {rule_name}"
                    elif (
                        executed_reason == 'order_below_min'
                        or executed_order_id == 'SKIPPED_MIN_BUY'
                    ):
                        color = '#000000'  # 本笔低于最小买入：异常结束，黑色
                        rule_name = f"[已执行-本笔低于最小买入] {rule_name}"
                    elif (
                        executed_reason == 'early_cancelled'
                        or executed_order_id == 'EARLY_CANCELLED'
                    ):
                        color = '#000000'  # 提前挂单人工撤单：黑色
                        rule_name = f"[已执行-提前撤单] {rule_name}"
                    elif executed_reason == 'not_true_breakthrough':
                        color = '#000000'
                        rule_name = f"[已结束-非真突破] {rule_name}"
                    elif (
                        executed_reason == 'band_hard_pass'
                        or executed_order_id == 'BAND_HARD_PASS'
                        or self._is_band_hard_pass_rule(rule)
                    ):
                        color = '#000000'
                        kind = self._band_hard_pass_kind_label(rule)
                        rule_name = f"[已结束-价格带硬pass·{kind}] {rule_name}"
                    elif (
                        rule_type == 'breakthrough_buy'
                        and self._rule_true_breakthrough_passed(rule)
                    ):
                        color = '#999999'
                        rule_name = f"[已执行-真突破] {rule_name}"
                    elif executed_reason == 'order_failed' or executed_order_id == 'ORDER_FAILED':
                        color = '#555555'
                        rule_name = f"[已执行-下单失败] {rule_name}"
                    elif executed_order_id in ('MIN_BUY_AMOUNT', 'NO_CASH'):
                        color = '#555555'
                        rule_name = f"[已执行-资金不足] {rule_name}"
                    else:
                        color = '#999999'
                        rule_name = f"[已执行] {rule_name}"
                elif not rule_enabled:
                    # 禁用的规则显示为浅灰色
                    color = '#000000'  # 禁用：黑色（与“已执行/已结束”的灰色区分）
                    rule_name = f"[已禁用] {rule_name}"
                else:
                    # 正常规则使用规则类型颜色
                    # 夜市规则使用特殊颜色
                    if rule_type == 'night_buy':
                        color = '#5c6bc0'  # 深蓝色（与按钮颜色一致）
                    elif rule_type == 'night_sell':
                        color = '#ab47bc'  # 紫色（与按钮颜色一致）
                    else:
                        try:
                            color = RULE_TYPE_COLORS.get(RuleType(rule_type), '#888888') if rule_type else '#888888'
                        except (ValueError, AttributeError):
                            color = '#888888'

            act_suffix = rule_activation_chart_suffix(rule)
            if act_suffix:
                rule_name = f"{rule_name}{act_suffix}"
            
            # 根据规则类型添加价格点（禁用的规则即使volume为0也显示）
            if rule_type in ['single_buy', 'breakthrough_buy', 'night_buy']:
                # 单点买入/突破买入：在指定价格显示买入点
                try:
                    price = float(rule.get('price', 0) or 0)
                    volume = int(rule.get('volume', 0) or 0)
                except (TypeError, ValueError):
                    price, volume = 0.0, 0
                if math.isnan(price) or price <= 0:
                    pass
                elif volume > 0 or not rule_enabled or rule_type == 'breakthrough_buy':
                    # 如果是提前下单且未执行，使用黄色显示（必须有订单ID，说明已真正下单）
                    if (rule.get('early_order', False) and 
                        rule.get('early_order_id') and  # 必须有订单ID，说明已真正下单
                        not rule_executed and 
                        self.task_running and 
                        not self.task_paused):
                        # 使用黄色表示提前下单状态（不再闪烁以提升性能）
                        early_order_color = '#ffeb3b'  # 黄色
                        self._append_rule_chart_point(price_points, price, rule_name, early_order_color, True, volume)
                    else:
                        self._append_rule_chart_point(price_points, price, rule_name, color, True, volume)
            
            elif rule_type in ['single_sell', 'breakthrough_sell', 'night_sell', 'scheduled_clear']:
                # 单点卖出/突破卖出/夜市卖出/定时清仓：在指定价格显示卖出点
                try:
                    price = float(rule.get('price', 0) or 0)
                    volume = int(rule.get('volume', 0) or 0)
                except (TypeError, ValueError):
                    price, volume = 0.0, 0
                # 突破卖出：只要有有效突破价就显示节点（数量为 0 时仍显示，避免「节点消失」）
                show_sell = (
                    price > 0
                    and not math.isnan(price)
                    and (
                        volume > 0
                        or not rule_enabled
                        or rule_type == 'breakthrough_sell'
                    )
                )
                if show_sell:
                    # 对于定时清仓规则，检查 scheduled_clear_executed 而不是 executed
                    if rule_type == 'scheduled_clear':
                        scheduled_clear_executed = rule.get('scheduled_clear_executed', False)
                        # 如果是提前下单且未执行，使用黄色显示（必须有订单ID，说明已真正下单）
                        if (rule.get('early_order', False) and 
                            rule.get('early_order_id') and  # 必须有订单ID，说明已真正下单
                            not scheduled_clear_executed and 
                            self.task_running and 
                            not self.task_paused):
                            # 使用黄色表示提前下单状态（不再闪烁以提升性能）
                            early_order_color = '#ffeb3b'  # 黄色
                            self._append_rule_chart_point(price_points, price, rule_name, early_order_color, True, -volume)
                        else:
                            # 已执行时使用白色节点（color 已经在上面设置为白色）
                            self._append_rule_chart_point(price_points, price, rule_name, color, True, -volume)
                    else:
                        # 其他卖出规则（single_sell, night_sell）使用原有逻辑
                        # 如果是提前下单且未执行，使用黄色显示（必须有订单ID，说明已真正下单）
                        if (rule.get('early_order', False) and 
                            rule.get('early_order_id') and  # 必须有订单ID，说明已真正下单
                            not rule_executed and 
                            self.task_running and 
                            not self.task_paused):
                            # 使用黄色表示提前下单状态（不再闪烁以提升性能）
                            early_order_color = '#ffeb3b'  # 黄色
                            self._append_rule_chart_point(price_points, price, rule_name, early_order_color, True, -volume)
                        else:
                            self._append_rule_chart_point(price_points, price, rule_name, color, True, -volume)
            
            elif rule_type == 'cage_buy':
                # 笼子买入：显示两个价格点（两端均须在涨跌停内，否则整段不画）
                price_low = rule.get('price_low', 0)
                price_high = rule.get('price_high', 0)
                volume = rule.get('volume', 0)
                if price_low > 0 and price_high > 0 and (volume > 0 or not rule_enabled):
                    if not self._cage_pair_tradeable(price_low, price_high):
                        continue
                    # 如果是提前下单且未执行，使用黄色显示
                    if (rule.get('early_order', False) and 
                        rule.get('early_order_id') and  # 必须有订单ID，说明已真正下单
                        not rule_executed and 
                        self.task_running and 
                        not self.task_paused):
                        # 使用黄色表示提前下单状态（不再闪烁以提升性能）
                        early_order_color = '#ffeb3b'  # 黄色
                        price_points.append((price_low, f'{rule_name}(下)', early_order_color, True, volume))
                        price_points.append((price_high, f'{rule_name}(上)', early_order_color, True, volume))
                    elif rule_executed:
                        # 已执行：根据保存的executed_endpoint判断是哪个端点执行的，未执行的端点显示为白色
                        executed_endpoint = rule.get('executed_endpoint')  # 'low' 或 'high'
                        if executed_endpoint == 'low':
                            # 下限执行（灰色），上限未执行（白色）
                            price_points.append((price_low, f'{rule_name}(下)', color, True, volume))  # 灰色
                            price_points.append((price_high, f'{rule_name}(上)', '#ffffff', True, volume))  # 白色
                        elif executed_endpoint == 'high':
                            # 上限执行（灰色），下限未执行（白色）
                            price_points.append((price_low, f'{rule_name}(下)', '#ffffff', True, volume))  # 白色
                            price_points.append((price_high, f'{rule_name}(上)', color, True, volume))  # 灰色
                        else:
                            # 没有端点记录（兼容旧数据），通过价格距离判断
                            executed_price = rule.get('executed_price', 0)
                            if executed_price > 0:
                                dist_to_low = abs(executed_price - price_low)
                                dist_to_high = abs(executed_price - price_high)
                                if dist_to_low < dist_to_high:
                                    price_points.append((price_low, f'{rule_name}(下)', color, True, volume))
                                    price_points.append((price_high, f'{rule_name}(上)', '#ffffff', True, volume))
                                else:
                                    price_points.append((price_low, f'{rule_name}(下)', '#ffffff', True, volume))
                                    price_points.append((price_high, f'{rule_name}(上)', color, True, volume))
                            else:
                                # 两个都显示为灰色（兼容旧数据）
                                price_points.append((price_low, f'{rule_name}(下)', color, True, volume))
                                price_points.append((price_high, f'{rule_name}(上)', color, True, volume))
                    else:
                        price_points.append((price_low, f'{rule_name}(下)', color, True, volume))
                        price_points.append((price_high, f'{rule_name}(上)', color, True, volume))
            
            elif rule_type == 'cage_sell':
                # 笼子卖出：显示两个价格点
                price_low = rule.get('price_low', 0)
                price_high = rule.get('price_high', 0)
                volume = rule.get('volume', 0)
                if price_low > 0 and price_high > 0 and (volume > 0 or not rule_enabled):
                    # 如果是提前下单且未执行，使用黄色显示
                    if (rule.get('early_order', False) and 
                        rule.get('early_order_id') and  # 必须有订单ID，说明已真正下单
                        not rule_executed and 
                        self.task_running and 
                        not self.task_paused):
                        # 使用黄色表示提前下单状态（不再闪烁以提升性能）
                        early_order_color = '#ffeb3b'  # 黄色
                        price_points.append((price_low, f'{rule_name}(下)', early_order_color, True, -volume))
                        price_points.append((price_high, f'{rule_name}(上)', early_order_color, True, -volume))
                    elif rule_executed:
                        # 已执行：根据保存的executed_endpoint判断是哪个端点执行的，未执行的端点显示为白色
                        executed_endpoint = rule.get('executed_endpoint')  # 'low' 或 'high'
                        if executed_endpoint == 'low':
                            # 下限执行（灰色），上限未执行（白色）
                            price_points.append((price_low, f'{rule_name}(下)', color, True, -volume))  # 灰色
                            price_points.append((price_high, f'{rule_name}(上)', '#ffffff', True, -volume))  # 白色
                        elif executed_endpoint == 'high':
                            # 上限执行（灰色），下限未执行（白色）
                            price_points.append((price_low, f'{rule_name}(下)', '#ffffff', True, -volume))  # 白色
                            price_points.append((price_high, f'{rule_name}(上)', color, True, -volume))  # 灰色
                        else:
                            # 没有端点记录（兼容旧数据），通过价格距离判断
                            executed_price = rule.get('executed_price', 0)
                            if executed_price > 0:
                                dist_to_low = abs(executed_price - price_low)
                                dist_to_high = abs(executed_price - price_high)
                                if dist_to_low < dist_to_high:
                                    price_points.append((price_low, f'{rule_name}(下)', color, True, -volume))
                                    price_points.append((price_high, f'{rule_name}(上)', '#ffffff', True, -volume))
                                else:
                                    price_points.append((price_low, f'{rule_name}(下)', '#ffffff', True, -volume))
                                    price_points.append((price_high, f'{rule_name}(上)', color, True, -volume))
                            else:
                                # 两个都显示为灰色（兼容旧数据）
                                price_points.append((price_low, f'{rule_name}(下)', color, True, -volume))
                                price_points.append((price_high, f'{rule_name}(上)', color, True, -volume))
                    else:
                        price_points.append((price_low, f'{rule_name}(下)', color, True, -volume))
                        price_points.append((price_high, f'{rule_name}(上)', color, True, -volume))
            
            elif rule_type == 'best_sell':
                # 弹性卖出：显示触发价格
                trigger_price = rule.get('trigger_price', 0)
                volume = rule.get('volume', 0)
                drop_percent = rule.get('drop_percent', 0.3)
                triggered = rule.get('triggered', False)
                if trigger_price > 0 and (volume > 0 or not rule_enabled):
                    label = f'{rule_name}\n(回落{drop_percent:.2f}%)'
                    # 如果已触发且未执行且任务运行中，使用红色显示（不再闪烁以提升性能）
                    if triggered and not rule_executed and self.task_running and not self.task_paused:
                        # 使用红色表示已触发但未执行的状态
                        triggered_color = '#ff0000'  # 红色
                        price_points.append((trigger_price, label, triggered_color, True, -volume if volume > 0 else 0))
                    else:
                        # 已执行或未触发或任务未运行：使用正常颜色（已执行时为灰色）
                        price_points.append((trigger_price, label, color, True, -volume if volume > 0 else 0))
            
            elif rule_type == 'best_buy':
                # 弹性买入：显示触发价格
                trigger_price = rule.get('trigger_price', 0)
                volume = rule.get('volume', 0)
                rise_percent = rule.get('rise_percent', 0.3)
                triggered = rule.get('triggered', False)
                if trigger_price > 0 and (volume > 0 or not rule_enabled):
                    label = f'{rule_name}\n(反弹{rise_percent:.2f}%)'
                    # 如果已触发且未执行且任务运行中，使用红色显示（不再闪烁以提升性能）
                    if triggered and not rule_executed and self.task_running and not self.task_paused:
                        # 使用红色表示已触发但未执行的状态
                        triggered_color = '#ff0000'  # 红色
                        price_points.append((trigger_price, label, triggered_color, True, volume))
                    else:
                        # 已执行或未触发或任务未运行：使用正常颜色（已执行时为灰色）
                        price_points.append((trigger_price, label, color, True, volume))
            
            elif rule_type == 'grid_buy':
                # 网格买入：显示所有网格线
                start_price = rule.get('start_price', 0)
                end_price = rule.get('end_price', 0)
                volume_per_grid = rule.get('volume_per_grid', 0)
                grid_step = rule.get('grid_step', 0.5)
                num_grids = rule.get('num_grids', 2)
                executed_grids = rule.get('executed_grids', [])
                
                # 兼容旧数据：如果没有 end_price，根据 start_price 和 grid_step 计算
                if end_price == 0 and start_price > 0:
                    end_price = start_price - num_grids * grid_step
                    rule['end_price'] = round(end_price, 2)  # 更新到规则中
                
                if start_price > 0 and end_price > 0:
                    # 获取已执行节点的固定价格和固定股数
                    executed_grid_prices = rule.get('executed_grid_prices', {})  # {grid_index: fixed_price}
                    executed_grid_volumes = rule.get('executed_grid_volumes', {})  # {grid_index: fixed_volume}
                    executed_grids = set()
                    for x in rule.get('executed_grids', []) or []:
                        try:
                            executed_grids.add(int(x))
                        except (TypeError, ValueError):
                            pass
                    _done_n, _done_total, grid_status_tag = self._grid_exec_progress(rule)
                    
                    # 收集网格点的坐标，用于绘制连接线
                    grid_coords = []  # [(price, volume), ...]
                    
                    # 添加所有网格点
                    # 思路：先按照正常逻辑计算所有节点的位置和股数（基于start_price和end_price，允许拖动调整）
                    # 然后对于已执行的节点，用固定价格和固定股数替换计算出的值
                    for i in range(num_grids + 1):
                        # 先按正常逻辑计算节点位置（基于start_price和end_price）
                        if i == 0:
                            calculated_price = start_price  # 高价端
                        elif i == num_grids:
                            calculated_price = end_price    # 低价端
                        else:
                            # 中间点：使用比例插值法
                            calculated_price = start_price - (start_price - end_price) * i / num_grids
                            precision = self._get_price_precision()
                            calculated_price = round(calculated_price, precision)
                        
                        # 如果该节点已执行，用固定价格和固定股数替换计算出的值
                        if i in executed_grid_prices or str(i) in executed_grid_prices:
                            grid_price = executed_grid_prices.get(i, executed_grid_prices.get(str(i)))
                            grid_volume = executed_grid_volumes.get(
                                i, executed_grid_volumes.get(str(i), volume_per_grid)
                            )
                        else:
                            grid_price = calculated_price  # 使用计算出的价格（可拖动调整）
                            grid_volume = volume_per_grid  # 使用计算出的股数（可拖动调整）
                        
                        # 保存网格点坐标（用于绘制连接线）
                        grid_coords.append((grid_price, grid_volume))
                        
                        # 根据网格点是否已执行选择颜色和标签
                        if i in executed_grids:
                            grid_color = '#999999'  # 已执行：深灰色
                            # 部分完成用「已部分执行」，全部完成才用「已执行」
                            if grid_status_tag and grid_status_tag not in rule_name:
                                prefix = f'[{grid_status_tag}] '
                            else:
                                prefix = ''
                        elif (rule.get('early_order', False) and 
                              rule.get('early_order_id') and  # 必须有订单ID，说明已真正下单
                              not rule_executed and 
                              self.task_running and 
                              not self.task_paused):
                            # 如果是提前下单且未执行，使用黄色显示（不再闪烁以提升性能）
                            grid_color = '#ffeb3b'  # 黄色
                            prefix = ''
                        else:
                            grid_color = color  # 未执行：使用规则颜色
                            prefix = ''
                        
                        if i == 0:
                            # 第一个点显示完整标签
                            label = f'{prefix}{rule_name}点1\n(间距{grid_step}元)'
                        else:
                            # 其他点显示简短标签（点2、点3...）
                            label = f'{prefix}{rule_name}点{i+1}'
                        price_points.append((grid_price, label, grid_color, True, grid_volume))
                    
                    # 保存网格连接线信息
                    if len(grid_coords) > 1:
                        grid_lines.append((rule_name, grid_coords, color))
            
            elif rule_type == 'grid_sell':
                # 网格卖出：显示所有网格线
                start_price = rule.get('start_price', 0)
                end_price = rule.get('end_price', 0)
                volume_per_grid = rule.get('volume_per_grid', 0)
                grid_step = rule.get('grid_step', 0.5)
                num_grids = rule.get('num_grids', 2)
                executed_grids = rule.get('executed_grids', [])
                
                # 兼容旧数据：如果没有 end_price，根据 start_price 和 grid_step 计算
                if end_price == 0 and start_price > 0:
                    end_price = start_price + num_grids * grid_step
                    precision = self._get_price_precision()
                    rule['end_price'] = round(end_price, precision)  # 更新到规则中
                
                if start_price > 0 and end_price > 0:
                    # 获取已执行节点的固定价格和固定股数
                    executed_grid_prices = rule.get('executed_grid_prices', {})  # {grid_index: fixed_price}
                    executed_grid_volumes = rule.get('executed_grid_volumes', {})  # {grid_index: fixed_volume}
                    executed_grids = set()
                    for x in rule.get('executed_grids', []) or []:
                        try:
                            executed_grids.add(int(x))
                        except (TypeError, ValueError):
                            pass
                    _done_n, _done_total, grid_status_tag = self._grid_exec_progress(rule)
                    
                    # 收集网格点的坐标，用于绘制连接线
                    grid_coords = []  # [(price, volume), ...]
                    
                    # 添加所有网格点（显示在Y轴负方向）
                    # 思路：先按照正常逻辑计算所有节点的位置和股数（基于start_price和end_price，允许拖动调整）
                    # 然后对于已执行的节点，用固定价格和固定股数替换计算出的值
                    precision = self._get_price_precision()
                    for i in range(num_grids + 1):
                        # 先按正常逻辑计算节点位置（基于start_price和end_price）
                        if i == 0:
                            calculated_price = start_price  # 低价端
                        elif i == num_grids:
                            calculated_price = end_price    # 高价端
                        else:
                            # 中间点：使用比例插值法
                            calculated_price = start_price + (end_price - start_price) * i / num_grids
                            calculated_price = round(calculated_price, precision)
                        
                        # 如果该节点已执行，用固定价格和固定股数替换计算出的值
                        if i in executed_grid_prices or str(i) in executed_grid_prices:
                            grid_price = executed_grid_prices.get(i, executed_grid_prices.get(str(i)))
                            grid_volume = executed_grid_volumes.get(
                                i, executed_grid_volumes.get(str(i), volume_per_grid)
                            )
                        else:
                            grid_price = calculated_price  # 使用计算出的价格（可拖动调整）
                            grid_volume = volume_per_grid  # 使用计算出的股数（可拖动调整）
                        
                        # 保存网格点坐标（用于绘制连接线，注意Y坐标为负值）
                        grid_coords.append((grid_price, -grid_volume))
                        
                        # 根据网格点是否已执行选择颜色和标签
                        if i in executed_grids:
                            grid_color = '#999999'  # 已执行：深灰色
                            if grid_status_tag and grid_status_tag not in rule_name:
                                prefix = f'[{grid_status_tag}] '
                            else:
                                prefix = ''
                        elif (rule.get('early_order', False) and 
                              rule.get('early_order_id') and  # 必须有订单ID，说明已真正下单
                              not rule_executed and 
                              self.task_running and 
                              not self.task_paused):
                            # 如果是提前下单且未执行，使用黄色显示（不再闪烁以提升性能）
                            grid_color = '#ffeb3b'  # 黄色
                            prefix = ''
                        else:
                            grid_color = color  # 未执行：使用规则颜色
                            prefix = ''
                        
                        if i == 0:
                            # 第一个点显示完整标签
                            label = f'{prefix}{rule_name}点1\n(间距{grid_step}元)'
                        else:
                            # 其他点显示简短标签（点2、点3...）
                            label = f'{prefix}{rule_name}点{i+1}'
                        price_points.append((grid_price, label, grid_color, True, -grid_volume))
                    
                    # 保存网格连接线信息
                    if len(grid_coords) > 1:
                        grid_lines.append((rule_name, grid_coords, color))
        
        # 返回网格连接线信息
        return grid_lines
    
    def draw_price_position_chart(self):
        """绘制二维坐标图（X轴：价格，Y轴：交易量）"""
        self.price_position_ax.clear()
        
        # 关键价格点应该在数据加载时已经计算完成（load_realtime_data中）
        # 如果数据没有加载，不绘制，避免错误提示（因为此时数据可能还在加载中）
        if self.prev_close_price <= 0:
            # 如果没有昨收盘价，可能是数据还在加载中，或者加载失败
            # 不显示错误提示（避免在数据加载过程中误报），只显示空白图表
            self.price_position_ax.axis('off')
            return
        
        # 检查涨跌停板价格是否已设置（应该在数据加载时已经设置）
        if not hasattr(self, 'limit_up_price') or not hasattr(self, 'limit_down_price') or self.limit_up_price <= 0 or self.limit_down_price <= 0:
            # 如果涨跌停板价格没有设置，说明数据加载不完整，不绘制
            self.price_position_ax.axis('off')
            return
        
        limit_up_price = self.limit_up_price
        limit_down_price = self.limit_down_price
        
        # 以涨跌停为 X 轴基准；区间外的关键价位不加入，避免把图表横向拉扁
        price_range = limit_up_price - limit_down_price
        padding = price_range * 0.10  # 10%边距
        
        x_min = limit_down_price - padding
        x_max = limit_up_price + padding
        
        # 收集所有价格点（使用列表存储，支持相同价格的多个标签）
        price_points = []  # [(price, name, color, draggable, volume), ...]
        
        # 从规则列表生成价格点，同时获取网格连接线信息
        grid_lines = self._add_rule_price_points(price_points)
        if grid_lines is None:
            grid_lines = []
        
        # 添加当前价（根据运行状态设置颜色）
        if self.current_price > 0 and self._rule_price_is_tradeable(self.current_price):
            # 运行中显示蓝色，未运行或已暂停显示灰色
            if self.task_running and not self.task_paused:
                current_price_color = '#1f77b4'  # 蓝色
            else:
                current_price_color = '#808080'  # 灰色
            price_points.append((self.current_price, '当前价', current_price_color, False, 0))
        
        # 添加关键价格点（仅显示涨跌停有效区间内的价位）
        for name, price in self.key_points:
            # 跳过"最新价"，因为我们已经有"当前价"了
            if name == '最新价':
                continue
            
            if price > 0:
                is_prev_close = '昨收盘' in name
                if is_prev_close:
                    if not self._rule_price_is_tradeable(price):
                        continue
                    # 昨收盘：加入列表以便绘制标签，线已在上面用 x=0 虚线绘制，下面绘制时不画节点
                    price_points.append((price, name, '#666666', False, 0))
                    continue
                if not self._rule_price_is_tradeable(price):
                    continue
                # 根据价格类型选择颜色
                if name == '涨停板':
                    color = '#ff0000'  # 红色
                elif name == '跌停板':
                    color = '#00aa00'  # 绿色
                elif name == '今日最高':
                    color = '#ff0000'  # 红色
                elif name == '今日最低':
                    color = '#00ff00'  # 绿色
                elif 'MA' in name or '均线' in name or name.endswith('日'):
                    # 均线：包含"MA"、"均线"，或以"日"结尾（如"5日"、"10日"）
                    color = '#ff8800'
                elif '布林' in name:
                    color = '#8800ff'
                else:
                    # 前高、前低、最近涨停、最近跌停等都使用灰色
                    color = '#888888'
                
                price_points.append((price, name, color, False, 0))
        
        if not price_points:
            self.price_position_ax.axis('off')
            return
        
        # 确定交易量范围
        volumes = [point[4] for point in price_points]  # point[4] 是 volume
        
        # 获取持仓量（如果有的话）
        position_volume = 0
        if hasattr(self, 'position_volume') and self.position_volume > 0:
            position_volume = self.position_volume
        
        # 检查是否有买入点（volume > 0）
        has_buy_points = any(v > 0 for v in volumes)
        
        # 从所有规则点和持仓量中找出最大交易量的绝对值
        all_volumes = volumes.copy()
        if position_volume > 0:
            all_volumes.append(position_volume)
        
        # 如果没有持仓且没有买入点，考虑可用余额
        if position_volume == 0 and not has_buy_points:
            # 获取可用余额和最新价
            available_cash = 0
            if hasattr(self, 'available_cash') and self.available_cash > 0:
                available_cash = self.available_cash
            
            current_price = 0
            if hasattr(self, 'current_price') and self.current_price > 0:
                current_price = self.current_price
            
            # 计算可用余额能买多少股（向下取整到100的倍数）
            if available_cash > 0 and current_price > 0:
                buyable_volume = int(available_cash / current_price / 100) * 100
                if buyable_volume > 0:
                    all_volumes.append(buyable_volume)
        
        max_volume = max(abs(v) for v in all_volumes) if all_volumes else 0
        
        # 根据最大交易量动态计算Y轴最大值
        # 方案：基于max_volume的1.3倍（留30%空间），然后向上取整到合适的基数
        # 这样既能保证数据点有足够的显示空间（至少占77%），又能保持稳定性
        if max_volume == 0:
            # 如果没有数据，使用默认值1200
            y_max = 1200
        else:
            # 计算基础值：留30%的空间，确保数据点至少占据77%的Y轴空间
            base = max_volume * 1.3
            
            # 根据数值大小，向上取整到不同的基数，保持稳定性
            # 注意：股票交易量都是100的倍数（一手=100股）
            if max_volume < 1000:
                # 小值：取整到100的倍数（符合股票交易单位）
                y_max = math.ceil(base / 100) * 100
            elif max_volume < 10000:
                # 中等值：取整到200的倍数
                y_max = math.ceil(base / 200) * 200
            else:
                # 大值：取整到1000的倍数
                y_max = math.ceil(base / 1000) * 1000
            
            # 设置最小值，避免太小
            y_max = max(y_max, 1200)
        
        # 保存Y轴范围，供悬停检测使用
        self.y_max = y_max
        
        y_min = -y_max
        
        # 绘制坐标轴
        self.price_position_ax.axhline(y=0, color='#000000', linewidth=1, alpha=0.5, zorder=60)  # Y=0轴线
        # 运行中：涨跌停/昨收/当前价用彩色；未运行或暂停：四条线都灰色，便于一眼看出是否在运行
        is_running = self.task_running and not self.task_paused
        line_style = dict(linewidth=2, alpha=0.8, linestyle='--', zorder=65)
        if is_running:
            self.price_position_ax.axvline(x=limit_up_price, color='#ff0000', **line_style)       # 涨停板-红
            self.price_position_ax.axvline(x=limit_down_price, color='#00ff00', **line_style)     # 跌停板-绿
            self.price_position_ax.axvline(x=self.prev_close_price, color='#e6b800', **line_style)  # 昨收盘-黄
            key_line_color = '#1f77b4'  # 当前价-蓝
        else:
            gray = '#808080'
            self.price_position_ax.axvline(x=limit_up_price, color=gray, **line_style)
            self.price_position_ax.axvline(x=limit_down_price, color=gray, **line_style)
            self.price_position_ax.axvline(x=self.prev_close_price, color=gray, **line_style)
            key_line_color = gray
        if self.current_price > 0:
            self.price_position_ax.axvline(x=self.current_price, color=key_line_color, linewidth=2, alpha=0.8, linestyle='-', zorder=70)  # 当前价格线（实线更直观）
        
        # 判断是否应该显示标签（3列和4列时不显示）
        should_show_labels = True
        
        # 优先使用存储的列数，如果没有则查找父组件
        columns = self.current_columns
        if columns is None and not self.show_controls:  # 不在单列布局时，尝试查找父组件
            parent = self.parent()
            while parent:
                if parent.__class__.__name__ == 'TasksChartsView':
                    if hasattr(parent, 'columns'):
                        columns = parent.columns
                        # 同时更新存储的列数，避免下次再查找
                        self.current_columns = columns
                    break
                parent = parent.parent()
        
        if columns is not None and columns >= 3:  # 3列或4列时不显示标签
            should_show_labels = False
        
        # 绘制持仓线（如果有持仓）
        # 持仓线显示为 y = -1 * 可用持仓量 的横线
        if position_volume > 0:
            self.price_position_ax.axhline(y=-position_volume, color='#ffa500', linewidth=2, alpha=0.8, linestyle=':', zorder=65)  # 持仓线（橙色点线）
            
            # 在持仓线右侧添加标签（仅在应该显示时）
            if should_show_labels:
                self.price_position_ax.text(
                    x_max - (x_max - x_min) * 0.02,  # 右侧留一点边距
                    -position_volume,
                    f'可用持仓 {position_volume}股',
                    ha='right', va='center',
                    fontsize=10, color='black', weight='bold',
                    bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor='#ffa500', alpha=0.8),
                    zorder=80
                )
            
            # 绘制仓位线（1/4仓、1/3仓、1/2仓、3/4仓）
            position_ratios = [
                (1/4, '1/4仓'),
                (1/3, '1/3仓'),
                (1/2, '1/2仓'),
                (3/4, '3/4仓')
            ]
            
            # 计算所有仓位的股数，并按股数分组（相同股数只保留比例最大的）
            volume_to_label = {}  # {股数: (比例, 标签)}
            for ratio, label in position_ratios:
                volume = int(position_volume * ratio)
                # 确保是100的倍数
                volume = int(volume / 100) * 100
                if volume > 0:
                    # 如果这个股数已存在，只保留比例较大的
                    if volume not in volume_to_label or ratio > volume_to_label[volume][0]:
                        volume_to_label[volume] = (ratio, label)
            
            # 绘制仓位线和标签
            for volume, (ratio, label) in volume_to_label.items():
                self.price_position_ax.axhline(y=-volume, color='#ffa500', linewidth=1, alpha=0.5, linestyle='--', zorder=65)  # 仓位线（橙色虚线，更细更淡）
                
                # 在仓位线右侧添加标签（仅在应该显示时）
                if should_show_labels:
                    self.price_position_ax.text(
                        x_max - (x_max - x_min) * 0.02,  # 右侧留一点边距
                        -volume,
                        f'{label} {volume}股',
                        ha='right', va='center',
                        fontsize=10, color='black', weight='normal',
                        bbox=dict(boxstyle='round,pad=0.2', facecolor='white', edgecolor='#ffa500', alpha=0.6),
                        zorder=80
                    )
        
        # 设置坐标轴范围
        self.price_position_ax.set_xlim(x_min, x_max)
        self.price_position_ax.set_ylim(y_min, y_max)
        self.x_min = x_min
        self.x_max = x_max
        # 禁用Y轴自动调整，确保使用我们设置的范围
        self.price_position_ax.set_autoscale_on(False)
        
        # 设置X轴刻度 - 以昨收盘为基准，上下各划分10格
        # 获取昨收盘价
        prev_close = self.prev_close_price
        
        # 生成X轴刻度值
        x_tick_values = []
        
        if prev_close > 0 and limit_down_price > 0 and limit_up_price > 0:
            # 确保昨收盘在跌停板和涨停板之间（理论上应该总是如此）
            if limit_down_price <= prev_close <= limit_up_price:
                # 从跌停板到昨收盘划分10格
                lower_range = prev_close - limit_down_price
                lower_step = lower_range / 10.0 if lower_range > 0 else 0
                
                # 从昨收盘到涨停板划分10格
                upper_range = limit_up_price - prev_close
                upper_step = upper_range / 10.0 if upper_range > 0 else 0
                
                # 生成下半部分刻度（从跌停板到昨收盘，共11个点：跌停板 + 10个中间点）
                for i in range(11):
                    if i == 0:
                        # 跌停板
                        x_tick_values.append(limit_down_price)
                    else:
                        # 中间点
                        tick_value = limit_down_price + lower_step * i
                        x_tick_values.append(tick_value)
                
                # 生成上半部分刻度（从昨收盘到涨停板，共10个点：不包括昨收盘，因为已经添加了）
                for i in range(1, 11):
                    tick_value = prev_close + upper_step * i
                    x_tick_values.append(tick_value)
                
                # 确保包含涨停板（最后一个点）
                if limit_up_price not in x_tick_values:
                    x_tick_values.append(limit_up_price)
                
                # 去重并排序
                x_tick_values = sorted(list(set(x_tick_values)))
                
                # 扩展范围：如果x_min或x_max超出涨跌停板范围，添加额外的刻度
                if x_min < limit_down_price:
                    # 在跌停板下方添加刻度，使用相同的间隔
                    extra_ticks = []
                    current = limit_down_price - lower_step
                    while current >= x_min:
                        extra_ticks.insert(0, current)
                        current -= lower_step
                    x_tick_values = extra_ticks + x_tick_values
                
                if x_max > limit_up_price:
                    # 在涨停板上方添加刻度，使用相同的间隔
                    current = limit_up_price + upper_step
                    while current <= x_max:
                        x_tick_values.append(current)
                        current += upper_step
            else:
                # 如果昨收盘不在正常范围内，回退到原来的逻辑
                price_range = x_max - x_min
                if price_range <= 1:
                    tick_step = 0.05
                elif price_range <= 2:
                    tick_step = 0.1
                elif price_range <= 5:
                    tick_step = 0.25
                elif price_range <= 10:
                    tick_step = 0.5
                elif price_range <= 20:
                    tick_step = 1.0
                elif price_range <= 50:
                    tick_step = 2.5
                else:
                    tick_step = 5.0
                
                start_tick = math.ceil(x_min / tick_step) * tick_step
                current_tick = start_tick
                while current_tick <= x_max:
                    x_tick_values.append(current_tick)
                    current_tick += tick_step
        else:
            # 如果没有昨收盘价或涨跌停板价格，回退到原来的逻辑
            price_range = x_max - x_min
            if price_range <= 1:
                tick_step = 0.05
            elif price_range <= 2:
                tick_step = 0.1
            elif price_range <= 5:
                tick_step = 0.25
            elif price_range <= 10:
                tick_step = 0.5
            elif price_range <= 20:
                tick_step = 1.0
            elif price_range <= 50:
                tick_step = 2.5
            else:
                tick_step = 5.0
            
            start_tick = math.ceil(x_min / tick_step) * tick_step
            current_tick = start_tick
            while current_tick <= x_max:
                x_tick_values.append(current_tick)
                current_tick += tick_step
        
        # 根据列数调整X轴刻度数量，避免重叠
        # 使用之前已经获取的columns变量（在方法开始部分已获取）
        # 如果columns仍为None，默认为1列
        if columns is None:
            columns = 1
        
        # 根据列数决定显示的刻度数量和样式
        # 保持所有刻度显示，通过旋转角度和字体大小避免重叠
        if columns == 1:
            # 1列：显示所有刻度，水平显示
            display_tick_values = x_tick_values
            tick_fontsize = 11
            tick_rotation = 0
        elif columns == 2:
            # 2列：显示所有刻度，垂直显示避免遮挡
            display_tick_values = x_tick_values
            tick_fontsize = 10
            tick_rotation = 90
        elif columns == 3:
            # 3列：显示所有刻度，垂直显示避免遮挡
            display_tick_values = x_tick_values
            tick_fontsize = 9
            tick_rotation = 90
        elif columns == 4:
            # 4列：显示所有刻度，垂直显示避免遮挡
            display_tick_values = x_tick_values
            tick_fontsize = 8
            tick_rotation = 90
        else:
            # 默认：显示所有刻度
            display_tick_values = x_tick_values
            tick_fontsize = 11
            tick_rotation = 0
        
        # 设置X轴刻度
        self.price_position_ax.set_xticks(display_tick_values)
        # 格式化刻度标签：智能去除末尾的0和不必要的小数点
        def format_price_label(value):
            """格式化价格标签：去掉末尾的0和不必要的小数点"""
            # 计算最小刻度间隔，用于确定小数位数
            min_step = None
            if len(display_tick_values) > 1:
                sorted_ticks = sorted(display_tick_values)
                steps = [sorted_ticks[i+1] - sorted_ticks[i] for i in range(len(sorted_ticks)-1) if sorted_ticks[i+1] > sorted_ticks[i]]
                if steps:
                    min_step = min(steps)
            
            # 根据最小间隔确定最大小数位数
            if min_step is not None:
                if min_step < 0.1:
                    # 0.05间隔，最多保留2位小数
                    formatted = f'{value:.2f}'
                elif min_step < 0.5:
                    # 0.1, 0.25间隔，最多保留2位小数
                    formatted = f'{value:.2f}'
                elif min_step < 1:
                    # 0.5间隔，最多保留1位小数
                    formatted = f'{value:.1f}'
                elif min_step < 3:
                    # 1.0, 2.5间隔，最多保留1位小数
                    formatted = f'{value:.1f}'
                else:
                    # 5.0及以上间隔，整数
                    formatted = f'{value:.0f}'
            else:
                # 如果无法计算间隔，使用默认格式（保留2位小数）
                formatted = f'{value:.2f}'
            
            # 去掉末尾的0和小数点
            # 例如：27.0 -> 27, 9.50 -> 9.5, 9.05 -> 9.05
            if '.' in formatted:
                formatted = formatted.rstrip('0').rstrip('.')
            return formatted
        
        x_tick_labels = [format_price_label(v) for v in display_tick_values]
        # 旋转90度时使用center对齐，45度时使用right对齐
        ha_alignment = 'center' if tick_rotation == 90 else ('right' if tick_rotation > 0 else 'center')
        self.price_position_ax.set_xticklabels(x_tick_labels, rotation=tick_rotation, fontsize=tick_fontsize, ha=ha_alignment)
        
        # 先绘制笼子规则的矩形区域（在点之前绘制，避免遮挡圆点）
        # 以及突破买入价格带（监控带半透明矩形 + 有效下沿虚线）
        for rule in self.rules:
            rule_type = rule.get('type')
            rule_enabled = rule.get('enabled', True)
            
            if rule_type in ['cage_buy', 'cage_sell']:
                price_low = rule.get('price_low', 0)
                price_high = rule.get('price_high', 0)
                volume = rule.get('volume', 0)
                
                if price_low > 0 and price_high > 0 and volume > 0:
                    # 根据规则类型和启用状态选择颜色和Y轴方向
                    if not rule_enabled:
                        # 禁用的规则：黑色边框，浅黑填充（与“已执行”的灰色区分）
                        facecolor = '#000000'
                        edgecolor = '#000000'
                    elif rule_type == 'cage_buy':
                        facecolor = '#4caf50'
                        edgecolor = '#66bb6a'
                    else:  # cage_sell
                        facecolor = '#f44336'
                        edgecolor = '#ef5350'
                    
                    if rule_type == 'cage_buy':
                        y_pos = 0
                        height = volume
                    else:  # cage_sell
                        y_pos = -volume
                        height = volume
                    
                    # 绘制矩形
                    from matplotlib.patches import Rectangle
                    rect = Rectangle((price_low, y_pos), price_high - price_low, height,
                                    alpha=0.08 if not rule_enabled else 0.15, facecolor=facecolor, edgecolor=edgecolor, 
                                    linewidth=2, linestyle='-', zorder=50)
                    self.price_position_ax.add_patch(rect)
            elif rule_type == "breakthrough_buy":
                try:
                    from core.price_band_buy import (
                        get_band_accept_low,
                        get_price_band,
                        rule_has_price_band,
                    )
                except Exception:
                    continue
                if not rule_has_price_band(rule):
                    continue
                try:
                    band_lo, band_hi = get_price_band(rule)
                    volume = int(rule.get("volume") or 0)
                except (TypeError, ValueError):
                    continue
                if band_lo <= 0 or band_hi < band_lo or volume <= 0:
                    continue
                from matplotlib.patches import Rectangle

                rule_executed = bool(rule.get("executed"))
                hard_passed = rule_executed and self._is_band_hard_pass_rule(rule)
                if not rule_enabled or hard_passed:
                    # 禁用或硬pass结束：黑色带，与仍在监控的青绿带区分
                    facecolor = "#000000"
                    edgecolor = "#000000"
                    alpha = 0.08
                else:
                    facecolor = "#26a69a"
                    edgecolor = "#00897b"
                    alpha = 0.14
                rect = Rectangle(
                    (band_lo, 0),
                    band_hi - band_lo,
                    volume,
                    alpha=alpha,
                    facecolor=facecolor,
                    edgecolor=edgecolor,
                    linewidth=1.5,
                    linestyle="-",
                    zorder=48,
                )
                self.price_position_ax.add_patch(rect)
                accept_lo = get_band_accept_low(rule)
                if accept_lo is not None and band_lo < float(accept_lo) < band_hi:
                    self.price_position_ax.plot(
                        [float(accept_lo), float(accept_lo)],
                        [0, volume],
                        color="#ff9800",
                        linewidth=1.2,
                        linestyle="--",
                        alpha=0.85,
                        zorder=49,
                    )
        
        # 绘制网格规则的连接线（在点之前绘制，避免遮挡点）
        for rule_name, grid_coords, line_color in grid_lines:
            if len(grid_coords) > 1:
                # 按价格排序（网格买入从高到低，网格卖出从低到高）
                prices = [coord[0] for coord in grid_coords]
                if len(prices) > 1 and prices[0] > prices[-1]:
                    # 网格买入：从高到低，需要反转
                    grid_coords_sorted = sorted(grid_coords, key=lambda x: x[0], reverse=True)
                else:
                    # 网格卖出：从低到高，保持原顺序或排序
                    grid_coords_sorted = sorted(grid_coords, key=lambda x: x[0])
                
                # 提取X和Y坐标
                x_coords = [coord[0] for coord in grid_coords_sorted]
                y_coords = [coord[1] for coord in grid_coords_sorted]
                
                # 绘制连接线（细线，半透明，颜色与规则一致）
                self.price_position_ax.plot(x_coords, y_coords, 
                                          color=line_color, 
                                          linewidth=1, 
                                          alpha=0.4, 
                                          linestyle='-', 
                                          zorder=40)  # zorder较低，在点下方
        
        # 绘制弹性买入/卖出规则的回弹点虚线
        current_price = self.current_price
        if current_price > 0:
            _, _, _elastic_cfg_dyn = self._load_elastic_confirm_config()
            # 多档 best_sell 动态红线若价格相同会完全重叠；按「同展示价位」计数微移 X
            best_sell_red_stagger = {}
            for rule in self.rules:
                rule_type = rule.get('type')
                rule_enabled = rule.get('enabled', True)
                rule_executed = rule.get('executed', False)
                
                # 获取规则颜色
                if rule_executed:
                    executed_order_id = str(rule.get('order_id', '') or '')
                    executed_reason = str(rule.get('executed_reason', '') or '')
                    if (
                        executed_reason == 'buy_block_window'
                        or executed_order_id == 'SKIPPED_BUY_WINDOW'
                    ):
                        color = '#000000'  # 禁买跳过：黑色
                    elif (
                        executed_reason == 'order_below_min'
                        or executed_order_id == 'SKIPPED_MIN_BUY'
                    ):
                        color = '#000000'  # 本笔低于最小买入：黑色
                    elif (
                        executed_reason == 'early_cancelled'
                        or executed_order_id == 'EARLY_CANCELLED'
                    ):
                        color = '#000000'  # 提前挂单人工撤单：黑色
                    elif executed_reason == 'not_true_breakthrough':
                        color = '#000000'
                    elif (
                        executed_reason == 'band_hard_pass'
                        or executed_order_id == 'BAND_HARD_PASS'
                        or self._is_band_hard_pass_rule(rule)
                    ):
                        color = '#000000'
                    elif executed_reason == 'order_failed' or executed_order_id == 'ORDER_FAILED':
                        color = '#555555'  # 下单失败结束：深灰色
                    elif executed_order_id in ('MIN_BUY_AMOUNT', 'NO_CASH'):
                        color = '#555555'  # 资金不足结束：深灰色
                    else:
                        color = '#999999'  # 已执行显示为灰色
                elif not rule_enabled:
                    color = '#000000'  # 禁用：黑色（与“已执行/已结束”的灰色区分）
                else:
                    from core.trading_rules import RuleType, RULE_TYPE_COLORS
                    try:
                        color = RULE_TYPE_COLORS.get(RuleType(rule_type), '#888888') if rule_type else '#888888'
                    except (ValueError, AttributeError):
                        color = '#888888'
                
                # 弹性买入规则：绘制回弹点竖线
                if rule_type == 'best_buy':
                    trigger_price = rule.get('trigger_price', 0)
                    rise_percent = rule.get('rise_percent', 0.3)
                    volume = rule.get('volume', 0)
                    triggered = rule.get('triggered', False)
                    lowest_price = rule.get('lowest_price', None)
                    
                    if trigger_price > 0 and (volume > 0 or not rule_enabled):
                        # 计算回弹点：已触发时用与执行逻辑一致的真实 target_price；未触发仍用触发价估算
                        real_target = self._elastic_best_buy_target_price_for_display(rule, _elastic_cfg_dyn)
                        if real_target is not None:
                            rebound_point = real_target
                        elif triggered and lowest_price is not None and lowest_price > 0:
                            rebound_point = lowest_price * (1 + rise_percent / 100)
                        else:
                            rebound_point = trigger_price * (1 + rise_percent / 100)
                        
                        # 获取触发价格的Y坐标（交易量位置）
                        # 使用触发价格的volume作为Y坐标，如果没有volume则使用0
                        trigger_y = volume if volume > 0 else 0
                        
                        # 如果已触发且未执行且任务运行中，竖线也使用红色
                        if triggered and not rule_executed and self.task_running and not self.task_paused:
                            line_color = '#ff0000'  # 红色
                        else:
                            line_color = color
                        
                        # 绘制竖线：在回弹点位置绘制一条从Y轴底部到顶部的竖线
                        # 获取Y轴范围
                        y_min = self.price_position_ax.get_ylim()[0]
                        y_max = self.price_position_ax.get_ylim()[1]
                        
                        # 绘制竖线虚线：在回弹点位置
                        self.price_position_ax.plot(
                            [rebound_point, rebound_point],
                            [y_min, y_max],
                            color=line_color,
                            linewidth=0.8,  # 适中的线条粗细
                            alpha=0.6,  # 透明度：0.0完全透明，1.0完全不透明
                            linestyle='--',  # 虚线
                            zorder=35  # zorder在点下方，但在网格线下方
                        )
                
                # 弹性卖出规则：绘制回落点竖线
                elif rule_type == 'best_sell':
                    trigger_price = rule.get('trigger_price', 0)
                    drop_percent = rule.get('drop_percent', 0.3)
                    volume = rule.get('volume', 0)
                    triggered = rule.get('triggered', False)
                    highest_price = rule.get('highest_price', None)
                    
                    # 只要有触发价格就绘制竖线（不需要volume > 0的条件）
                    if trigger_price > 0:
                        # 初始参考线：固定使用“触发价 * (1-回落%)”，不随最高价变化
                        base_drop_point = trigger_price * (1 - drop_percent / 100)

                        # 实时目标线：已触发后使用与执行逻辑一致的真实 target_price（会随最高价变化）
                        real_target = self._elastic_best_sell_target_price_for_display(rule, _elastic_cfg_dyn)

                        # 获取Y轴范围
                        y_min = self.price_position_ax.get_ylim()[0]
                        y_max = self.price_position_ax.get_ylim()[1]

                        # 1) 固定黄色参考线（原始回落线）
                        self.price_position_ax.plot(
                            [base_drop_point, base_drop_point],
                            [y_min, y_max],
                            color='#fdd835',  # 黄色：初始参考线
                            linewidth=0.8,
                            alpha=0.6,
                            linestyle='--',  # 虚线
                            zorder=35  # zorder在点下方，但在网格线下方
                        )

                        # 2) 红色动态线：与弹性买入一致——「已突破且未成交且任务运行中」每档必画一根红线。
                        # 原先仅在 real_target 与 base_drop_point 差值 > 1e-8 时画红，两档规则在最高价仍贴近
                        # 触发价时动态目标与黄线重合或差值过小，会导致第二根红线不画。
                        active_red = (
                            triggered
                            and (not rule_executed)
                            and self.task_running
                            and (not self.task_paused)
                        )
                        if active_red:
                            red_x = (
                                float(real_target)
                                if real_target is not None
                                else float(base_drop_point)
                            )
                            prec = int(self._get_price_precision())
                            tick = float(10 ** (-prec))
                            if tick <= 0:
                                tick = 0.01
                            bucket = round(round(red_x / tick) * tick, prec)
                            n_dup = best_sell_red_stagger.get(bucket, 0)
                            best_sell_red_stagger[bucket] = n_dup + 1
                            # 第二根起沿高价方向错开：每多一档约 1 个最小价位，避免多根红线完全叠在一起看不清
                            red_plot_x = red_x + n_dup * tick * 1.0
                            self.price_position_ax.plot(
                                [red_plot_x, red_plot_x],
                                [y_min, y_max],
                                color='#ff0000',  # 红色：动态目标线（每档规则各一根）
                                linewidth=0.8,
                                alpha=0.7,
                                linestyle='--',
                                zorder=36,
                            )
                        elif real_target is not None and abs(real_target - base_drop_point) > 1e-8:
                            # 已触发但任务暂停/已结束等：动态与静态不一致时仍显示灰色参考
                            self.price_position_ax.plot(
                                [real_target, real_target],
                                [y_min, y_max],
                                color='#888888',
                                linewidth=0.8,
                                alpha=0.55,
                                linestyle='--',
                                zorder=36,
                            )
                
                # 笼子买入/笼子卖出：壁厚>0 时绘制有效内区间两条竖线虚线，仅在矩形内部
                elif rule_type in ['cage_buy', 'cage_sell']:
                    wt = rule.get('wall_thickness', 0) or 0
                    if wt > 0:
                        price_low = rule.get('price_low', 0) or 0
                        price_high = rule.get('price_high', 0) or 0
                        volume = rule.get('volume', 0) or 0
                        inner_low, inner_high = self._get_cage_inner_bounds(rule)
                        if price_low > 0 and price_high > 0 and volume > 0 and (inner_low > price_low or inner_high < price_high):
                            # 矩形的纵向范围：买入 [0, volume]，卖出 [-volume, 0]
                            if rule_type == 'cage_buy':
                                y_bottom, y_top = 0, volume
                            else:
                                y_bottom, y_top = -volume, 0
                            if inner_low > price_low:
                                self.price_position_ax.plot(
                                    [inner_low, inner_low], [y_bottom, y_top],
                                    color=color, linewidth=0.8, alpha=0.5, linestyle='--', zorder=55
                                )
                            if inner_high < price_high:
                                self.price_position_ax.plot(
                                    [inner_high, inner_high], [y_bottom, y_top],
                                    color=color, linewidth=0.8, alpha=0.5, linestyle='--', zorder=55
                                )
        
        # 按价格分组，处理相同价格的多个标签（必须用舍入价作键，否则 13.98 与 13.9800001 分成两组，同价多标签错开 dup_idx 永远为 0）
        from collections import defaultdict
        _pk_prec = SecurityTypeUtil.get_price_precision(self.stock_code)

        def _price_group_key(raw):
            try:
                return round(float(raw), _pk_prec)
            except (TypeError, ValueError):
                return raw

        price_groups = defaultdict(list)
        for point in price_points:
            price_groups[_price_group_key(point[0])].append(point)

        def _sort_points_at_same_price(pts):
            """同价位多点时：可拖动优先；今日最高/最低/今开盘其次；其余在后。"""
            def _key(p):
                _, name, _, draggable, _ = p
                if draggable:
                    return (0, 0, name)
                if name == "今日最高":
                    return (1, 0, name)
                if name == "今日最低":
                    return (1, 1, name)
                if name == "今开盘":
                    return (1, 2, name)
                return (2, 0, name)

            return sorted(pts, key=_key)

        def _node_vol_key(v):
            """同节点键：成交量统一，避免 0 与 0.0、-0.0 分成两个键"""
            try:
                fv = float(v)
                if abs(fv) < 1e-9:
                    return 0
                if abs(fv - round(fv)) < 1e-6:
                    return int(round(fv))
                return round(fv, 4)
            except (TypeError, ValueError):
                return 0
        
        # 先收集所有不可拖动节点的价格，用于计算独立的索引
        non_draggable_prices = []
        for price in price_groups.keys():
            points_at_price = price_groups[price]
            for point in points_at_price:
                # point结构: (price, name, color, draggable, volume)
                if not point[3]:  # point[3] 是 draggable
                    if price not in non_draggable_prices:
                        non_draggable_prices.append(price)
                    break
        
        # 对不可拖动节点的价格排序，创建稳定的索引映射
        non_draggable_prices_sorted = sorted(non_draggable_prices)
        non_draggable_price_to_idx = {price: idx for idx, price in enumerate(non_draggable_prices_sorted)}
        
        # 清空节点标签映射（每次重新绘制时清空）
        self.node_label_map = {}
        self.label_original_zorder = {}
        self.label_original_bbox = {}
        
        # 自动布局标签：收集所有需要放置标签的点信息
        label_placements = []  # 存储已放置的标签位置信息 [(label_y, price, name), ...]
        price_range_for_labels = x_max - x_min  # 价格范围，用于判断标签是否在同一价格附近
        
        def find_best_label_position(node_volume, node_price, node_name, is_draggable, is_current_price=False):
            """为节点找到最佳标签位置，避免重叠"""
            nonlocal price_range_for_labels  # 允许访问外部变量
            # 标签高度估算（基于y_max的比例，实际标签高度大约为y_max的0.15）
            label_height = y_max * 0.15
            
            # 候选位置列表：优先尝试上方，然后尝试下方
            candidate_positions = []
            
            # 对于不可拖动的关键价格点，优先放在顶部或底部空白区域
            if not is_draggable:
                # 尝试底部
                candidate_positions.append((-y_max + y_max * 0.08, 'bottom'))
                # 尝试顶部
                candidate_positions.append((y_max - y_max * 0.08, 'top'))
            else:
                # 对于可拖动的节点，优先在节点附近
                # 尝试多个偏移量（从小到大，优先上方）
                offsets = [0.12, 0.18, 0.24, 0.30, 0.36]  # y_max的倍数
                for offset_ratio in offsets:
                    # 上方位置
                    candidate_positions.append((node_volume + y_max * offset_ratio, 'bottom'))
                    # 下方位置
                    candidate_positions.append((node_volume - y_max * offset_ratio, 'top'))
            
            # 对于当前价，优先放在最上方
            if is_current_price:
                candidate_positions.insert(0, (node_volume + y_max * 0.25, 'bottom'))
            
            # 检查每个候选位置是否与已有标签冲突
            for label_y, va in candidate_positions:
                # 检查是否与已有标签重叠（考虑标签高度）
                has_conflict = False
                for existing_y, existing_price, existing_name in label_placements:
                    # 如果两个标签在Y轴上距离小于标签高度，认为冲突
                    if abs(label_y - existing_y) < label_height * 0.8:  # 0.8倍高度，留一些间距
                        # 如果价格相同或非常接近，更可能冲突
                        if abs(node_price - existing_price) < price_range_for_labels * 0.05:  # 价格相差小于5%范围
                            has_conflict = True
                            break
                
                # 如果没有冲突，使用这个位置
                if not has_conflict:
                    label_placements.append((label_y, node_price, node_name))
                    return label_y, va
            
            # 如果所有位置都冲突，使用最后一个候选位置（可能仍有重叠，但至少能显示）
            if candidate_positions:
                label_y, va = candidate_positions[-1]
                label_placements.append((label_y, node_price, node_name))
                return label_y, va
            
            # 默认位置（不应该到达这里）
            return node_volume + y_max * 0.15, 'bottom'
        
        # 绘制所有价格点（支持相同价格的多个标签）
        all_prices_sorted = sorted(price_groups.keys())
        # 同价同成交量节点若出现多个标签：与 find_best_label_position 使用同一套水平带（底带/顶带或拖动点偏移），不横向偏移
        node_key_counts = {}
        for _gp in all_prices_sorted:
            for _pt in price_groups[_gp]:
                _rp, _nm, _c, _d, _vol = _pt
                _nk = (_gp, _node_vol_key(_vol))
                node_key_counts[_nk] = node_key_counts.get(_nk, 0) + 1
        global_idx = 0
        
        # grp_price 为分组键（已舍入），勿与 point[0] 原始浮点混用，否则同桶内 dup_idx 无法累加
        for grp_price in all_prices_sorted:
            points_at_price = _sort_points_at_same_price(price_groups[grp_price])
            
            # 对于每个价格，先检查是否已经有不可拖动的点被绘制
            non_draggable_drawn = False
            
            # 对于每个价格，绘制所有标签
            for point_idx, point in enumerate(points_at_price):
                raw_price, name, color, draggable, volume = point

                if not draggable and not self._rule_price_is_tradeable(raw_price):
                    continue
                
                # 绘制点
                # 规则：
                # - 可拖动的规则点：每个都要绘制（因为Y坐标可能不同）
                # - 不可拖动的关键价格点：相同价格只绘制一次（但如果该价格下没有其他不可拖动点被绘制，即使不是第一个也要绘制）
                # - 今日最高/今日最低：始终绘制（可与其它灰色关键价位同价共存，且 zorder 更高盖住灰色圆点）
                # - 当前价、昨收盘不绘制节点（已有竖线表示位置）
                should_draw_circle = False
                if name == '当前价':
                    # 当前价不绘制节点，只保留竖线和标签
                    should_draw_circle = False
                elif '昨收盘' in name and not draggable:
                    # 仅关键价位「昨收盘」不绘制节点；规则名含「昨收盘」子串（如突破卖出（昨收盘-1.5%））
                    # 仍须绘制节点（draggable=True）
                    should_draw_circle = False
                elif name == '涨停板' or name == '跌停板':
                    # 涨跌停板已有竖线和价格标签，不再额外绘制节点圆圈
                    should_draw_circle = False
                elif draggable:
                    # 可拖动的规则点：始终绘制
                    should_draw_circle = True
                elif name in ("今日最高", "今日最低"):
                    # 与「最近涨停」等同价时，不能只让第一个灰色点占坑，否则今日高/低无圆点或被盖住
                    should_draw_circle = True
                    non_draggable_drawn = True
                elif not non_draggable_drawn:
                    # 不可拖动的点：如果该价格下还没有其他不可拖动点被绘制，就绘制
                    should_draw_circle = True
                    non_draggable_drawn = True  # 标记已绘制
                
                if should_draw_circle:
                    if draggable:
                        # 可拖动的买卖点，绘制大圆点
                        marker_size = 100
                        alpha = 1.0
                        marker = 'o'
                        # 可拖动的点zorder较高（90）
                        if name == '当前价':
                            circle_zorder = 100
                        else:
                            circle_zorder = 90
                    else:
                        # 不可拖动的价格点，绘制小圆点
                        marker_size = 50
                        alpha = 0.7
                        marker = 'o'
                        # 不可拖动的点默认 zorder 较低（80）；今日最高/最低带色、需压过「最近涨停」等灰色点
                        circle_zorder = 88 if name in ("今日最高", "今日最低") else 80
                    
                    line = self.price_position_ax.scatter(raw_price, volume, c=color, s=marker_size, 
                                                        alpha=alpha, marker=marker, edgecolors='black', linewidth=1,
                                                        zorder=circle_zorder)
                    
                    # 如果是可拖动的买点或卖点，保存引用
                    if draggable:
                        if name == '买点':
                            self.buy_line_ref = line
                        elif name == '卖点':
                            self.sell_line_ref = line
                
                # 添加标签 - 同一价格的多个标签分散显示（仅在应该显示时）
                # 优先级：当前价 > 买点/卖点 > 今日最高/今日最低 > 其他标签
                if not should_show_labels:
                    continue  # 跳过标签绘制，但已绘制的点会保留
                
                label_zorder = 80  # 默认zorder
                label_weight = 'bold'
                label_fontsize = 10
                
                # 使用自动布局算法找到最佳标签位置
                is_current_price = (name == '当前价')
                
                # 设置特殊标签的zorder和字体大小
                if is_current_price:
                    label_fontsize = 10  # 当前价字号稍大
                    label_zorder = 100  # 当前价zorder最高
                elif draggable:
                    label_zorder = 90  # 可拖动规则点zorder较高
                elif name in ("今日最高", "今日最低"):
                    label_zorder = 88  # 与对应散点一致，高于灰色关键价位标签
                else:
                    label_zorder = 80  # 不可拖动点zorder较低
                
                node_key = (grp_price, _node_vol_key(volume))
                dup_idx = len(self.node_label_map.get(node_key, []))
                if node_key_counts.get(node_key, 0) > 1:
                    # 与其它节点标签同一水平基准：不可拖动=关键价位底带/顶带；可拖动=与 find_best 相同的 offset 上下对
                    ymax_lim = y_max * 0.98
                    ymin_lim = -y_max * 0.98
                    if not draggable:
                        y_bottom = -y_max + y_max * 0.08
                        y_top = y_max - y_max * 0.08
                        pair = dup_idx // 2
                        slot = dup_idx % 2
                        if pair == 0:
                            label_y = y_bottom if slot == 0 else y_top
                            va = "bottom" if slot == 0 else "top"
                        else:
                            band_shift = pair * y_max * 0.06
                            if slot == 0:
                                label_y = y_bottom + band_shift
                                va = "bottom"
                            else:
                                label_y = y_top - band_shift
                                va = "top"
                        label_y = min(ymax_lim, max(ymin_lim, label_y))
                    else:
                        _offs = [0.12, 0.18, 0.24, 0.30, 0.36]
                        k = dup_idx // 2
                        slot = dup_idx % 2
                        off_ratio = _offs[min(k, len(_offs) - 1)]
                        if slot == 0:
                            label_y = volume + y_max * off_ratio
                            va = "bottom"
                        else:
                            label_y = volume - y_max * off_ratio
                            va = "top"
                        label_y = min(ymax_lim, max(ymin_lim, label_y))
                    label_placements.append((label_y, raw_price, name))
                else:
                    label_y, va = find_best_label_position(volume, raw_price, name, draggable, is_current_price)
                
                # 添加交易量信息到标签
                if volume != 0:
                    volume_text = f'{abs(volume):.0f}股' if volume > 0 else f'{abs(volume):.0f}股'
                    
                    # 根据股票代码确定价格精度
                    precision = SecurityTypeUtil.get_price_precision(self.stock_code)
                    
                    # 如果是买入类型（volume > 0），添加所需金额和占可用余额的百分比
                    if volume > 0:
                        # 计算买入所需金额
                        buy_amount = raw_price * volume
                        # 计算占可用余额的百分比
                        if hasattr(self, 'available_cash') and self.available_cash > 0:
                            cash_ratio = (buy_amount / self.available_cash) * 100
                            label_text = f'{name}\n{raw_price:.{precision}f}元\n{volume_text}\n{buy_amount:.{precision}f}元\n占余额{cash_ratio:.1f}%'
                        else:
                            label_text = f'{name}\n{raw_price:.{precision}f}元\n{volume_text}\n{buy_amount:.{precision}f}元'
                    else:
                        # 卖出类型，添加卖出金额和占可用仓位的百分比
                        sell_volume = abs(volume)  # 卖出数量（转为正数）
                        sell_amount = raw_price * sell_volume  # 卖出金额
                        
                        # 获取可用仓位
                        position_volume = 0
                        if hasattr(self, 'position_volume'):
                            position_volume = self.position_volume
                        
                        if position_volume > 0:
                            # 有可用仓位，计算占比
                            position_ratio = (sell_volume / position_volume) * 100
                            label_text = f'{name}\n{raw_price:.{precision}f}元\n{volume_text}\n{sell_amount:.{precision}f}元\n占仓位{position_ratio:.1f}%'
                        else:
                            # 可用仓位为0，显示提示
                            label_text = f'{name}\n{raw_price:.{precision}f}元\n{volume_text}\n{sell_amount:.{precision}f}元\n可用仓位0'
                else:
                    # 根据股票代码确定价格精度
                    precision = SecurityTypeUtil.get_price_precision(self.stock_code)
                    label_text = f'{name}\n{raw_price:.{precision}f}'
                
                # 可拖动买卖节点多时易与 X 轴刻度重叠：白底更透；其它关键价位略透
                if draggable:
                    _bbox = dict(
                        boxstyle="round,pad=0.25",
                        facecolor="white",
                        edgecolor=color,
                        alpha=0.38,
                        linewidth=0.9,
                    )
                else:
                    _bbox = dict(
                        boxstyle="round,pad=0.3",
                        facecolor="white",
                        edgecolor=color,
                        alpha=0.72,
                    )
                text_obj = self.price_position_ax.text(raw_price, label_y, 
                                          label_text,
                                          ha='center', va=va,
                                          fontsize=label_fontsize, color='black', weight=label_weight,
                                          bbox=_bbox,
                                          zorder=label_zorder)
                
                # 保存标签对象和节点坐标的映射关系，用于悬停检测（同坐标可多个标签）
                if node_key not in self.node_label_map:
                    self.node_label_map[node_key] = []
                self.node_label_map[node_key].append(text_obj)
                # 保存标签的原始zorder，用于恢复
                self.label_original_zorder[text_obj] = label_zorder
                self.label_original_bbox[text_obj] = dict(_bbox)
            
            global_idx += 1
        
        # 设置坐标轴标签
        # 3列和4列时不显示坐标轴标签
        if self.current_columns is not None and self.current_columns >= 3:
            # 不显示坐标轴标签
            self.price_position_ax.set_xlabel('', fontsize=10)
            self.price_position_ax.set_ylabel('', fontsize=10)
        else:
            self.price_position_ax.set_xlabel('价格 (元)', fontsize=10)
            self.price_position_ax.set_ylabel('交易量 (股)', fontsize=10)
        
        # 设置Y轴刻度 - 以100股为单位，增加刻度密度
        # 确保所有刻度都是100的倍数
        range_volume = int(y_max / 100) * 100  # 确保是100的倍数
        
        # 计算刻度值，确保都是100的倍数
        # 增加刻度密度：从5个刻度增加到9个刻度
        max_tick = range_volume
        tick_values = []
        tick_labels = []
        
        # 生成9个刻度：-max, -3/4*max, -1/2*max, -1/4*max, 0, 1/4*max, 1/2*max, 3/4*max, max
        # 确保所有值都是100的倍数
        tick_values = [
            -max_tick,
            int(-max_tick * 0.75 / 100) * 100,  # -3/4
            int(-max_tick * 0.5 / 100) * 100,   # -1/2
            int(-max_tick * 0.25 / 100) * 100,  # -1/4
            0,
            int(max_tick * 0.25 / 100) * 100,   # 1/4
            int(max_tick * 0.5 / 100) * 100,   # 1/2
            int(max_tick * 0.75 / 100) * 100,   # 3/4
            max_tick
        ]
        tick_labels = [f'{v}' if v != 0 else '0' for v in tick_values]
        
        # 根据列数设置Y轴刻度字号，与X轴保持一致
        if self.current_columns is not None:
            if self.current_columns == 1:
                y_tick_fontsize = 11
            elif self.current_columns == 2:
                y_tick_fontsize = 10
            elif self.current_columns == 3:
                y_tick_fontsize = 9
            elif self.current_columns == 4:
                y_tick_fontsize = 8
            else:
                y_tick_fontsize = 11
        else:
            y_tick_fontsize = 11
        
        self.price_position_ax.set_yticks(tick_values)
        self.price_position_ax.set_yticklabels(tick_labels, fontsize=y_tick_fontsize)
        
        # 添加网格
        self.price_position_ax.grid(True, alpha=0.3)
        
        # 调整布局
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            self.figure.tight_layout()
        # tight_layout 可能扰动坐标范围；以涨跌停为基准重新锁定 X 轴
        self.price_position_ax.set_xlim(x_min, x_max)
        self.price_position_ax.set_ylim(y_min, y_max)
        
        # 交易节点标签 zorder 最高约 100；将坐标轴刻度文字提到最上层并加白描边，避免被半透明白底挡住
        _axis_label_z = 150
        try:
            import matplotlib.patheffects as _mpe
            _tick_pe = [_mpe.withStroke(linewidth=2.8, foreground="white"), _mpe.Normal()]
        except Exception:
            _tick_pe = None
        for _lbl in list(self.price_position_ax.get_xticklabels()) + list(
            self.price_position_ax.get_yticklabels()
        ):
            _lbl.set_zorder(_axis_label_z)
            if _tick_pe is not None:
                _lbl.set_path_effects(_tick_pe)
        try:
            self.price_position_ax.xaxis.label.set_zorder(_axis_label_z)
            self.price_position_ax.yaxis.label.set_zorder(_axis_label_z)
        except Exception:
            pass
        
        # 连接鼠标事件（用于拖动）— 与 init 中 _mouse_events_connected 共用，避免重复绑定
        if not getattr(self, "_mouse_events_connected", False):
            self.canvas.mpl_connect('button_press_event', self.on_mouse_press)
            self.canvas.mpl_connect('button_release_event', self.on_mouse_release)
            self.canvas.mpl_connect('motion_notify_event', self.on_mouse_move)
            self._mouse_events_connected = True
        
        # 如果处于添加模式且有保存的鼠标位置，重新创建价格提示（图表重绘后恢复）
        # 注意：只在图表重绘时恢复，避免与鼠标移动时的更新冲突
        if self.add_mode and self.last_mouse_position and self.price_hint_annotation is None:
            current_price, current_volume = self.last_mouse_position
            buy_modes = ['single_buy', 'best_buy', 'cage_buy', 'grid_buy', 'night_buy']
            sell_modes = ['single_sell', 'best_sell', 'cage_sell', 'grid_sell', 'night_sell', 'scheduled_clear']
            
            # 检查买入/卖出规则的位置限制
            can_show = True
            if self.add_mode in buy_modes:
                if current_volume < 0:
                    can_show = False
            elif self.add_mode in sell_modes:
                if current_volume > 0:
                    can_show = False
            
            if can_show:
                self._create_price_hint(current_price, current_volume, buy_modes, sell_modes)
        
        # draw_idle：合并同一事件循环内多次刷新，Qt 后端推荐，风险低于立即 draw()
        self.canvas.draw_idle()
    
    
    def on_label_click(self, event):
        """处理标签点击事件，切换显示顺序"""
        if event.artist in self.label_texts:
            # 找到被点击的标签在当前显示顺序中的索引
            clicked_index = self.label_texts.index(event.artist)
            
            # 获取被点击标签对应的原始索引（在merged_labels中的位置）
            clicked_order_idx = self.label_order[clicked_index]
            
            # 从顺序中移除，然后插入到最前面
            self.label_order.remove(clicked_order_idx)
            self.label_order.insert(0, clicked_order_idx)
            
            # 重新绘制整个图表（包括标签），以反映新的顺序
            self.update_chart()
    
    def on_mouse_move(self, event):
        """处理鼠标移动事件，显示悬停提示"""
        if event.inaxes != self.price_ax:
            # 鼠标不在图表区域内，隐藏提示
            if self.hover_annotation:
                self.hover_annotation.remove()
                self.hover_annotation = None
                self.canvas.draw()
            return
        
        # 检查鼠标是否在标签附近
        mouse_x, mouse_y = event.xdata, event.ydata
        if mouse_x is None or mouse_y is None:
            return
        
        # 查找最近的标签
        closest_label = None
        min_distance = float('inf')
        
        for text_obj in self.label_texts:
            pos = text_obj.get_position()
            label_x, label_y = pos[0], pos[1]
            
            # 计算距离（考虑标签的宽度）
            distance = ((mouse_x - label_x) ** 2 + (mouse_y - label_y) ** 2) ** 0.5
            
            # 如果鼠标在标签附近（距离小于0.5个单位）
            if distance < 0.5 and distance < min_distance:
                min_distance = distance
                closest_label = text_obj
        
        # 显示或更新悬停提示
        if closest_label:
            label_text = closest_label.get_text()
            pos = closest_label.get_position()
            
            # 移除旧的提示
            if self.hover_annotation:
                self.hover_annotation.remove()
            
            # 创建新的悬停提示
            self.hover_annotation = self.price_ax.annotate(
                label_text,
                xy=(pos[0], pos[1]),
                xytext=(pos[0] + 0.5, pos[1] + 0.5),
                fontsize=8,
                bbox=dict(boxstyle='round,pad=0.3', facecolor='yellow', edgecolor='black', alpha=0.8),
                arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0'),
                zorder=1000  # 确保在最上层
            )
            self.canvas.draw()
        else:
            # 鼠标不在标签附近，隐藏提示
            if self.hover_annotation:
                self.hover_annotation.remove()
                self.hover_annotation = None
                self.canvas.draw()
        
        
    def on_mouse_press(self, event):
        """鼠标按下事件"""
        if event.inaxes != self.price_position_ax:
            return
        
        if event.button == 1:  # 左键
            # 检测双击：如果距离上次点击时间很短，且不在添加模式下，则触发双击事件
            import time
            current_time = time.time()
            time_since_last_click = current_time - self._last_click_time
            
            if (not self.add_mode and 
                self._last_click_time > 0 and  # 确保有上次点击记录
                time_since_last_click < self._double_click_interval):
                # 这是双击事件（仅在非添加模式下触发）
                # 在所有布局模式下都响应双击事件
                self._handle_double_click()
                self._last_click_time = 0  # 重置，避免连续三次点击被误判为两次双击
                return
            else:
                # 记录点击时间
                self._last_click_time = current_time
            
            # 如果处于添加模式
            if self.add_mode:
                current_price = event.xdata
                current_volume = event.ydata
                
                # 检查买入/卖出规则的位置限制
                # 买入规则（single_buy, best_buy, cage_buy, grid_buy, night_buy）：必须在X轴上方（y >= 0）
                # 卖出规则（single_sell, best_sell, cage_sell, grid_sell, night_sell）：必须在X轴下方（y <= 0）
                buy_modes = ['single_buy', 'best_buy', 'cage_buy', 'grid_buy', 'night_buy']
                sell_modes = ['single_sell', 'best_sell', 'cage_sell', 'grid_sell', 'night_sell', 'scheduled_clear']
                
                can_add = True
                if self.add_mode in buy_modes:
                    # 买入模式：鼠标必须在X轴上方（y >= 0）
                    if current_volume < 0:
                        can_add = False
                    
                    # 检查买入价格是否超过涨停价
                    if can_add:
                        limit_up_price = None
                        # 优先使用已存储的涨停价
                        if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                            limit_up_price = self.limit_up_price
                        # 如果没有涨停价，尝试计算
                        elif hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                            limit_up_price, _ = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                            # 保存计算出的涨停价
                            if limit_up_price:
                                self.limit_up_price = limit_up_price
                        else:
                            # 如果还没有涨跌停价，尝试重新计算关键价格点
                            try:
                                self.calculate_key_points(force_recalculate=True)
                                if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                                    limit_up_price = self.limit_up_price
                            except Exception as e:
                                self.logger.warning(f"[{self.stock_code}] 无法获取涨停价: {e}")
                        
                        # 如果价格超过涨停价，处理价格限制
                        # 使用精度处理避免浮点数精度问题，允许等于涨停价
                        if limit_up_price:
                            precision = self._get_price_precision()
                            current_price_rounded = round(current_price, precision)
                            limit_up_price_rounded = round(limit_up_price, precision)
                            # 如果四舍五入后的价格大于涨停价：自动钳到涨停价（与拖动规则一致）
                            if current_price_rounded > limit_up_price_rounded:
                                current_price = limit_up_price
                                self.logger.info(
                                    f"[{self.stock_code}] 买入({self.add_mode})点击价超出涨停价，已自动调整为涨停价: {limit_up_price:.{precision}f}元"
                                )
                            
                elif self.add_mode in sell_modes:
                    # 卖出模式：鼠标必须在X轴下方（y <= 0）
                    if current_volume > 0:
                        can_add = False
                    
                    # 检查卖出价格是否低于跌停价
                    if can_add:
                        limit_down_price = None
                        # 优先使用已存储的跌停价
                        if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                            limit_down_price = self.limit_down_price
                        # 如果没有跌停价，尝试计算
                        elif hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                            _, limit_down_price = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                            # 保存计算出的跌停价
                            if limit_down_price:
                                self.limit_down_price = limit_down_price
                        else:
                            # 如果还没有涨跌停价，尝试重新计算关键价格点
                            try:
                                self.calculate_key_points(force_recalculate=True)
                                if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                                    limit_down_price = self.limit_down_price
                            except Exception as e:
                                self.logger.warning(f"[{self.stock_code}] 无法获取跌停价: {e}")
                        
                        # 如果价格低于跌停价：自动钳到跌停价
                        if limit_down_price:
                            precision = self._get_price_precision()
                            current_price_rounded = round(current_price, precision)
                            limit_down_price_rounded = round(limit_down_price, precision)
                            if current_price_rounded < limit_down_price_rounded:
                                current_price = limit_down_price
                                self.logger.info(
                                    f"[{self.stock_code}] 卖出({self.add_mode})点击价低于跌停价，已自动调整为跌停价: {limit_down_price:.{precision}f}元"
                                )
                        # 卖出：同时限制不能超过涨停价（点击路径原先未校验上限）
                        limit_up_price = None
                        if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                            limit_up_price = self.limit_up_price
                        elif hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                            limit_up_price, _ = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                            if limit_up_price:
                                self.limit_up_price = limit_up_price
                        else:
                            try:
                                self.calculate_key_points(force_recalculate=True)
                                if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                                    limit_up_price = self.limit_up_price
                            except Exception as e:
                                self.logger.warning(f"[{self.stock_code}] 无法获取涨停价: {e}")
                        if limit_up_price:
                            precision = self._get_price_precision()
                            current_price_rounded = round(current_price, precision)
                            limit_up_price_rounded = round(limit_up_price, precision)
                            if current_price_rounded > limit_up_price_rounded:
                                current_price = limit_up_price
                                self.logger.info(
                                    f"[{self.stock_code}] 卖出({self.add_mode})点击价超过涨停价，已自动调整为涨停价: {limit_up_price:.{precision}f}元"
                                )
                
                if not can_add:
                    # 不符合位置要求，不添加规则
                    return
                
                if self.add_mode in ['single_buy', 'breakthrough_buy', 'single_sell', 'breakthrough_sell', 'night_buy', 'night_sell', 'scheduled_clear']:
                    # 单点规则、突破规则、夜市规则和定时清仓规则：点击直接添加
                    self._add_single_point_rule(current_price, current_volume)
                elif self.add_mode in ['cage_buy', 'cage_sell']:
                    # 笼子规则：开始拖动
                    self.adding_rule = True
                    self.temp_rule_start = current_price
                elif self.add_mode in ['best_buy', 'best_sell']:
                    # 弹性买入/弹性卖出：点击直接添加（使用默认百分比）
                    self._add_best_rule_simple(current_price, current_volume)
                elif self.add_mode in ['grid_buy', 'grid_sell']:
                    # 网格买入/卖出：开始拖动创建价格区间
                    self.adding_rule = True
                    self.temp_rule_start = current_price
                return
            
            # 否则检查是否要拖动现有规则
            self.dragging = None
            self.dragged_rule = None
            self.drag_mode = None
            self.drag_start_x = None  # 记录拖动开始时的X坐标（用于笼子整体平移）
            self.drag_start_y = None  # 拖动开始时的Y坐标（用于计算相对变化，避免Y轴扩大导致的恶性循环）
            self.drag_start_volume = None  # 拖动开始时的交易量（用于计算相对变化）
            self.drag_start_y_pixel = None  # 拖动开始时的Y屏幕坐标（像素，用于准确计算相对移动）
            
            # 检查是否点击了某个规则点（先检查包括已执行的规则，看是否点击了已执行的节点）
            clicked_rule_with_executed, _, grid_index_with_executed = self._find_rule_at_position(event.xdata, event.ydata, include_executed=True)
            
            # 检查是否点击了已执行完整的规则（整个规则都执行完了）
            if clicked_rule_with_executed and clicked_rule_with_executed.get('executed', False):
                # 点击的是已执行完成的规则，不能拖动，给出提示
                from PyQt5.QtWidgets import QMessageBox
                rule_name = clicked_rule_with_executed.get('name', '未命名规则')
                QMessageBox.information(
                    self,
                    "提示",
                    f"规则「{rule_name}」已执行完成，无法拖动修改。\n\n"
                    f"如需删除，请右键点击该节点。"
                )
                return
            
            # 检查是否点击了网格规则中已执行的节点
            if (clicked_rule_with_executed and 
                grid_index_with_executed is not None and 
                clicked_rule_with_executed.get('type') in ['grid_buy', 'grid_sell']):
                executed_grids = clicked_rule_with_executed.get('executed_grids', [])
                if grid_index_with_executed in executed_grids:
                    # 点击的是已执行的网格节点，不能拖动，给出提示
                    from PyQt5.QtWidgets import QMessageBox
                    rule_name = clicked_rule_with_executed.get('name', '未命名规则')
                    QMessageBox.information(
                        self,
                        "提示",
                        f"网格规则「{rule_name}」的该节点已执行完成，无法拖动修改。\n\n"
                        f"可以拖动其他未执行的节点，或右键点击该节点查看详情。"
                    )
                    return
            
            # 检查是否点击了未执行的规则点（用于拖动）
            clicked_rule, drag_mode, grid_index = self._find_rule_at_position(event.xdata, event.ydata, include_executed=False)
            if clicked_rule:
                # 如果是网格规则，需要检查
                if clicked_rule.get('type') in ['grid_buy', 'grid_sell']:
                    executed_grids = clicked_rule.get('executed_grids', [])
                    
                    # 检查该网格节点是否已执行
                    if grid_index is not None and grid_index in executed_grids:
                        # 该网格节点已执行，不能拖动
                        from PyQt5.QtWidgets import QMessageBox
                        rule_name = clicked_rule.get('name', '未命名规则')
                        QMessageBox.information(
                            self,
                            "提示",
                            f"网格规则「{rule_name}」的该节点已执行完成，无法拖动修改。\n\n"
                            f"可以拖动其他未执行的节点，或右键点击该节点查看详情。"
                        )
                        return
                    
                
                self.dragging = 'rule'
                self.dragged_rule = clicked_rule
                self.drag_mode = drag_mode
                self.drag_start_x = event.xdata
                self.drag_start_y = event.ydata  # 记录拖动开始时的Y坐标
                # 记录拖动开始时的屏幕Y坐标（像素），用于准确计算相对移动
                if event.inaxes:
                    self.drag_start_y_pixel = event.y
                else:
                    self.drag_start_y_pixel = None
                
                # 记录拖动开始时的交易量（根据规则类型获取）
                rule_type = clicked_rule.get('type')
                if rule_type in ['single_buy', 'breakthrough_buy', 'single_sell', 'breakthrough_sell', 'best_buy', 'best_sell', 'night_buy', 'night_sell', 'scheduled_clear']:
                    self.drag_start_volume = clicked_rule.get('volume', 0)
                elif rule_type in ['cage_buy', 'cage_sell']:
                    self.drag_start_volume = clicked_rule.get('volume', 0)
                elif rule_type in ['grid_buy', 'grid_sell']:
                    self.drag_start_volume = clicked_rule.get('volume_per_grid', 0)
                else:
                    self.drag_start_volume = 0
                
                return
        
        elif event.button == 3:  # 右键
            # 检查是否点击了某个规则（包括已执行的规则，因为右键菜单需要显示）
            # 优先级已在 _find_rule_at_position 中处理：
            # 单点规则 > 笼子节点 > 笼子区域
            clicked_rule, drag_mode, grid_index = self._find_rule_at_position(event.xdata, event.ydata, include_executed=True)
            if clicked_rule:
                self._show_rule_context_menu(clicked_rule, event)
                return
                    
    def _calculate_volume_from_pixel_drag(self, event):
        """
        基于屏幕坐标计算拖动后的交易量，避免Y轴扩大导致的恶性循环
        
        参数:
            event: matplotlib鼠标事件
        
        返回:
            new_volume: 计算出的新交易量
        """
        if (self.drag_start_y_pixel is not None and 
            self.drag_start_volume is not None and 
            event.inaxes):
            # 计算屏幕Y坐标的移动距离（像素）
            # 在matplotlib中，event.y是显示坐标（从下往上，原点在底部）
            # 向上拖动鼠标 → event.y增大 → 我们想要交易量增加 → delta_y_data应为正
            # 向下拖动鼠标 → event.y减小 → 我们想要交易量减少 → delta_y_data应为负
            delta_y_pixel = event.y - self.drag_start_y_pixel  # 向上拖动时为正，向下拖动时为负
            # 获取当前Y轴范围，计算每像素对应的数据单位
            y_min, y_max = event.inaxes.get_ylim()
            ax_height_pixels = event.inaxes.bbox.height
            if ax_height_pixels > 0:
                # 每像素对应的数据单位
                units_per_pixel = (y_max - y_min) / ax_height_pixels
                # 将像素移动转换为数据单位移动
                # 向上拖动 → delta_y_pixel为正 → delta_y_data为正 → 交易量增加
                # 向下拖动 → delta_y_pixel为负 → delta_y_data为负 → 交易量减少
                delta_y_data = delta_y_pixel * units_per_pixel
                # 将Y坐标变化量转换为交易量增量（每100单位Y对应100股）
                delta_volume = round(delta_y_data / 100) * 100
                
                # 检查是否是卖出规则，如果是则需要反转方向
                # 卖出规则在图表上显示为负Y值（Y坐标 = -volume）
                # 向上拖动鼠标 → Y坐标值增大（从-1000向0移动，绝对值减小） → 卖出股数应该减少（1000 → 500 → 0）
                # 向下拖动鼠标 → Y坐标值减小（从-500向-1000移动，绝对值增大） → 卖出股数应该增加（500 → 1000）
                # 所以对于卖出规则，delta_volume的符号应该与delta_y_data相反
                if (hasattr(self, 'dragged_rule') and self.dragged_rule):
                    rule_type = self.dragged_rule.get('type', '')
                    if rule_type in ['single_sell', 'breakthrough_sell', 'best_sell', 'cage_sell', 'grid_sell', 'night_sell', 'scheduled_clear']:
                        # 卖出规则：反转方向，使向上拖动减少股数，向下拖动增加股数
                        delta_volume = -delta_volume
                
                new_volume = max(100, self.drag_start_volume + delta_volume)
                return new_volume
        
        # 回退方案：如果初始值未记录，使用绝对值计算
        return max(100, round(abs(event.ydata) / 100) * 100)
    
    def _safe_remove_price_hint(self):
        """安全移除价格提示annotation"""
        if self.price_hint_annotation is None:
            return
        try:
            # 先尝试设置为不可见
            try:
                if hasattr(self.price_hint_annotation, 'set_visible'):
                    self.price_hint_annotation.set_visible(False)
            except:
                pass
            # 然后尝试移除（即使hasattr返回True，remove也可能失败）
            try:
                if hasattr(self.price_hint_annotation, 'remove'):
                    self.price_hint_annotation.remove()
            except (NotImplementedError, AttributeError, ValueError, TypeError):
                # remove失败，尝试其他方式
                try:
                    if hasattr(self, 'price_position_ax') and self.price_position_ax:
                        # 尝试通过文本对象移除
                        if hasattr(self.price_hint_annotation, 'text'):
                            if hasattr(self.price_hint_annotation.text, 'remove'):
                                self.price_hint_annotation.text.remove()
                except:
                    pass  # 忽略所有错误，确保程序继续运行
        except Exception:
            # 捕获所有其他可能的异常
            pass
        finally:
            # 无论成功与否，都清除引用
            self.price_hint_annotation = None
    
    def _create_price_hint(self, current_price, current_volume, buy_modes, sell_modes):
        """创建价格提示标签"""
        # 格式化股数：对齐到100的倍数（与添加规则时的计算逻辑一致）
        volume_aligned = max(100, int(abs(current_volume) / 100) * 100)
        if current_volume < 0:
            volume_aligned = -volume_aligned
        volume_text = f"{int(abs(volume_aligned))}股"
        
        # 根据股票代码确定价格精度
        precision = SecurityTypeUtil.get_price_precision(self.stock_code)
        price_text = f"{current_price:.{precision}f}元\n{volume_text}"
        
        # 根据价格敏感度设置价格提示的背景颜色
        # 买入规则（buy_modes）：如果买入价 > 当前价，背景为红色（提醒可能立即触发）
        # 卖出规则（sell_modes）：如果卖出价 < 当前价，背景为红色（提醒可能立即触发）
        # 否则使用黄色背景，提醒要谨慎
        hint_bg_color = '#ffffcc'  # 默认黄色背景，提醒要谨慎
        if self.current_price > 0:
            if self.add_mode in buy_modes:
                # 买入规则：如果买入价 > 当前价，背景为红色
                if current_price > self.current_price:
                    hint_bg_color = '#ff6666'  # 深红色背景，提醒价格敏感
            elif self.add_mode in sell_modes:
                # 卖出规则：如果卖出价 < 当前价，背景为红色
                if current_price < self.current_price:
                    hint_bg_color = '#ff6666'  # 深红色背景，提醒价格敏感
        
        try:
            self.price_hint_annotation = self.price_position_ax.annotate(
                price_text,
                xy=(current_price, current_volume),
                xytext=(10, 10),  # 相对于鼠标位置的偏移（像素）
                textcoords='offset points',
                fontsize=10,
                bbox=dict(boxstyle='round,pad=0.5', facecolor=hint_bg_color, edgecolor='black', alpha=0.9),
                zorder=1000  # 确保在最上层
            )
        except Exception as e:
            # 如果创建annotation失败，记录错误但不影响程序运行
            self.logger.debug(f"创建价格提示失败: {str(e)}")
            self.price_hint_annotation = None
    
    def on_mouse_move(self, event):
        """鼠标移动事件 - 拖动买卖点或规则"""
        if event.inaxes != self.price_position_ax:
            # 鼠标不在图表区域内，清除价格提示
            self._safe_remove_price_hint()
            # 清除保存的鼠标位置
            self.last_mouse_position = None
            # 恢复所有标签的zorder
            self._reset_label_zorders()
            return
        
        # 注意：ydata/xdata 可能为 0（恰在价格轴或成交量 0 处），不能用 if not xdata
        if event.xdata is None or event.ydata is None:
            return
        
        # 检测鼠标是否在某个节点附近或直接悬停在标签上，如果是，提高对应标签的zorder
        self._handle_label_hover(event.xdata, event.ydata, event)
        
        # 如果处于添加模式，显示价格提示（包括拖动时）
        if self.add_mode:
            current_price = event.xdata
            current_volume = event.ydata
            
            # 检查买入/卖出规则的位置限制
            # 买入规则（single_buy, best_buy, cage_buy, grid_buy, night_buy）：必须在X轴上方（y >= 0）
            # 卖出规则（single_sell, best_sell, cage_sell, grid_sell, night_sell）：必须在X轴下方（y <= 0）
            buy_modes = ['single_buy', 'best_buy', 'cage_buy', 'grid_buy', 'night_buy']
            sell_modes = ['single_sell', 'best_sell', 'cage_sell', 'grid_sell', 'night_sell', 'scheduled_clear']
            
            can_show = True
            if self.add_mode in buy_modes:
                # 买入模式：鼠标必须在X轴上方（y >= 0）
                if current_volume < 0:
                    can_show = False
            elif self.add_mode in sell_modes:
                # 卖出模式：鼠标必须在X轴下方（y <= 0）
                if current_volume > 0:
                    can_show = False
            
            if can_show:
                # 保存鼠标位置，用于图表重绘后恢复价格提示
                self.last_mouse_position = (current_price, current_volume)
                
                # 检查价格提示是否需要更新（只在位置有明显变化时更新，避免频繁重建）
                should_update = True
                if self.price_hint_annotation is not None:
                    try:
                        # 获取当前价格提示的位置
                        hint_pos = self.price_hint_annotation.xy
                        if hint_pos is not None and len(hint_pos) >= 2:
                            hint_price, hint_volume = hint_pos[0], hint_pos[1]
                            # 如果位置变化很小（价格差异小于0.01元，股数差异小于50股），不更新
                            if abs(hint_price - current_price) < 0.01 and abs(hint_volume - current_volume) < 50:
                                should_update = False
                    except:
                        pass  # 如果获取位置失败，继续更新
                
                if should_update:
                    # 移除旧的价格提示
                    self._safe_remove_price_hint()
                    # 创建新的价格提示（显示在鼠标位置右上方）
                    self._create_price_hint(current_price, current_volume, buy_modes, sell_modes)
                    # 只在更新时绘制，避免频繁绘制导致卡顿
                    if not self.adding_rule and not self.dragging:
                        self.canvas.draw_idle()  # 使用draw_idle提高性能
            else:
                # 不符合位置要求，清除价格提示
                self._safe_remove_price_hint()
                self.last_mouse_position = None  # 清除保存的鼠标位置
        else:
            # 不在添加模式时，清除价格提示
            self._safe_remove_price_hint()
            self.last_mouse_position = None  # 清除保存的鼠标位置
        
        # 如果正在添加笼子规则或网格规则（都需要拖动创建价格区间）
        if self.adding_rule and self.temp_rule_start and event.button == 1:
            # 临时绘制区域预览
            self.draw_price_position_chart()
            
            # 绘制临时区域（笼子或网格的价格区间）
            price_low = min(self.temp_rule_start, event.xdata)
            price_high = max(self.temp_rule_start, event.xdata)
            current_volume = event.ydata
            
            # 检查买入/卖出规则的位置限制
            buy_modes = ['cage_buy', 'grid_buy']
            sell_modes = ['cage_sell', 'grid_sell']
            
            can_draw = True
            if self.add_mode in buy_modes:
                # 买入模式：鼠标必须在X轴上方（y >= 0）
                if current_volume < 0:
                    can_draw = False
            elif self.add_mode in sell_modes:
                # 卖出模式：鼠标必须在X轴下方（y <= 0）
                if current_volume > 0:
                    can_draw = False
            
            if can_draw:
                # 对齐到100的倍数（与添加规则时的计算逻辑一致）
                volume = max(100, int(abs(current_volume) / 100) * 100)
                if current_volume < 0:
                    volume = -volume
                
                # 绘制临时矩形
                from matplotlib.patches import Rectangle
                if self.add_mode in ['cage_buy', 'grid_buy']:
                    rect = Rectangle((price_low, 0), price_high - price_low, volume,
                                    alpha=0.2, facecolor='green', edgecolor='green', linestyle='--', zorder=50)
                elif self.add_mode in ['cage_sell', 'grid_sell']:
                    rect = Rectangle((price_low, 0), price_high - price_low, volume,
                                    alpha=0.2, facecolor='red', edgecolor='red', linestyle='--', zorder=50)
                else:
                    rect = None
                
                if rect:
                    self.price_position_ax.add_patch(rect)
                
                # 重新显示价格提示（因为draw_price_position_chart清除了所有内容）
                current_price = event.xdata
                # 保存鼠标位置，用于图表重绘后恢复价格提示
                self.last_mouse_position = (current_price, current_volume)
                # 使用辅助方法创建价格提示
                buy_modes = ['cage_buy', 'grid_buy']
                sell_modes = ['cage_sell', 'grid_sell']
                # 扩展为完整的买入/卖出模式列表
                all_buy_modes = ['single_buy', 'best_buy', 'cage_buy', 'grid_buy', 'night_buy']
                all_sell_modes = ['single_sell', 'best_sell', 'cage_sell', 'grid_sell', 'night_sell', 'scheduled_clear']
                self._create_price_hint(current_price, current_volume, all_buy_modes, all_sell_modes)
            
            self.canvas.draw()
            return
        
        # 如果正在拖动规则
        if self.dragging == 'rule' and self.dragged_rule and event.button == 1:
            rule_type = self.dragged_rule.get('type')
            
            if rule_type in ['single_buy', 'breakthrough_buy', 'single_sell', 'breakthrough_sell', 'night_buy', 'night_sell', 'scheduled_clear']:
                # 单点规则/突破规则：更新价格和数量
                new_price = event.xdata
                # 使用屏幕坐标计算相对移动，避免Y轴扩大导致的恶性循环
                new_volume = self._calculate_volume_from_pixel_drag(event)
                
                # 价格限制：买入不能超过涨停价且不能低于跌停价，卖出不能低于跌停价
                if rule_type in ['single_buy', 'breakthrough_buy', 'night_buy']:
                    limit_up_price = None
                    limit_down_price = None
                    if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                        limit_up_price = self.limit_up_price
                    if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                        limit_down_price = self.limit_down_price
                    if not limit_up_price or not limit_down_price:
                        if hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                            limit_up_price, limit_down_price = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                            if limit_up_price:
                                self.limit_up_price = limit_up_price
                            if limit_down_price:
                                self.limit_down_price = limit_down_price
                    # 使用精度处理避免浮点数精度问题，允许等于涨跌停价
                    if limit_down_price:
                        precision = self._get_price_precision()
                        new_price_rounded = round(new_price, precision)
                        limit_down_price_rounded = round(limit_down_price, precision)
                        if new_price_rounded < limit_down_price_rounded:
                            new_price = limit_down_price
                    if limit_up_price:
                        precision = self._get_price_precision()
                        new_price_rounded = round(new_price, precision)
                        limit_up_price_rounded = round(limit_up_price, precision)
                        if new_price_rounded > limit_up_price_rounded:
                            new_price = limit_up_price
                elif rule_type in ['single_sell', 'breakthrough_sell', 'night_sell', 'scheduled_clear']:
                    limit_down_price = None
                    limit_up_price = None
                    if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                        limit_down_price = self.limit_down_price
                    if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                        limit_up_price = self.limit_up_price
                    elif hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                        limit_up_price, limit_down_price = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                        if limit_up_price:
                            self.limit_up_price = limit_up_price
                        if limit_down_price:
                            self.limit_down_price = limit_down_price
                    if limit_down_price and new_price < limit_down_price:
                        new_price = limit_down_price
                    # 使用精度处理避免浮点数精度问题，允许等于涨停价
                    if limit_up_price:
                        precision = self._get_price_precision()
                        new_price_rounded = round(new_price, precision)
                        limit_up_price_rounded = round(limit_up_price, precision)
                        if new_price_rounded > limit_up_price_rounded:
                            new_price = limit_up_price
                
                precision = self._get_price_precision()
                self.dragged_rule['price'] = round(new_price, precision)
                self.dragged_rule['volume'] = new_volume
                
                self.draw_price_position_chart()
            
            elif rule_type in ['best_buy', 'best_sell']:
                # 弹性买入/弹性卖出规则：更新触发价格和数量
                new_price = event.xdata
                # 使用屏幕坐标计算相对移动，避免Y轴扩大导致的恶性循环
                new_volume = self._calculate_volume_from_pixel_drag(event)
                
                # 价格限制：买入不能超过涨停价且不能低于跌停价，卖出不能低于跌停价且不能超过涨停价
                if rule_type == 'best_buy':
                    limit_up_price = None
                    limit_down_price = None
                    if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                        limit_up_price = self.limit_up_price
                    if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                        limit_down_price = self.limit_down_price
                    if not limit_up_price or not limit_down_price:
                        if hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                            limit_up_price, limit_down_price = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                            if limit_up_price:
                                self.limit_up_price = limit_up_price
                            if limit_down_price:
                                self.limit_down_price = limit_down_price
                    # 使用精度处理避免浮点数精度问题
                    precision = self._get_price_precision()
                    # 检查不能低于跌停价
                    if limit_down_price:
                        limit_down_price_rounded = round(limit_down_price, precision)
                        new_price_rounded = round(new_price, precision)
                        if new_price_rounded < limit_down_price_rounded:
                            new_price = limit_down_price
                    # 检查不能超过涨停价
                    if limit_up_price:
                        limit_up_price_rounded = round(limit_up_price, precision)
                        new_price_rounded = round(new_price, precision)
                        if new_price_rounded > limit_up_price_rounded:
                            new_price = limit_up_price
                elif rule_type == 'best_sell':
                    limit_down_price = None
                    limit_up_price = None
                    if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                        limit_down_price = self.limit_down_price
                    if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                        limit_up_price = self.limit_up_price
                    elif hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                        limit_up_price, limit_down_price = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                        if limit_up_price:
                            self.limit_up_price = limit_up_price
                        if limit_down_price:
                            self.limit_down_price = limit_down_price
                    if limit_down_price and new_price < limit_down_price:
                        new_price = limit_down_price
                    # 使用精度处理避免浮点数精度问题，允许等于涨停价
                    if limit_up_price:
                        precision = self._get_price_precision()
                        new_price_rounded = round(new_price, precision)
                        limit_up_price_rounded = round(limit_up_price, precision)
                        if new_price_rounded > limit_up_price_rounded:
                            new_price = limit_up_price
                
                precision = self._get_price_precision()
                self.dragged_rule['trigger_price'] = round(new_price, precision)
                self.dragged_rule['volume'] = new_volume
                
                self.draw_price_position_chart()
            
            elif rule_type in ['cage_buy', 'cage_sell']:
                # 笼子规则拖动逻辑
                price_low = self.dragged_rule.get('price_low', 0)
                price_high = self.dragged_rule.get('price_high', 0)
                
                if self.drag_mode == 'low':
                    # 拖动下限点：只改变下限价格，不改变上限
                    new_price_low = event.xdata
                    # 确保下限不超过上限
                    if new_price_low < price_high:
                        # 价格限制：买入笼子的下限不能低于跌停价，卖出笼子的下限不能低于跌停价
                        if rule_type == 'cage_buy':
                            limit_down_price = None
                            if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                                limit_down_price = self.limit_down_price
                            elif hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                                _, limit_down_price = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                                if limit_down_price:
                                    self.limit_down_price = limit_down_price
                            # 使用精度处理避免浮点数精度问题，允许等于跌停价
                            if limit_down_price:
                                precision = self._get_price_precision()
                                new_price_low_rounded = round(new_price_low, precision)
                                limit_down_price_rounded = round(limit_down_price, precision)
                                if new_price_low_rounded < limit_down_price_rounded:
                                    new_price_low = limit_down_price
                        elif rule_type == 'cage_sell':
                            limit_down_price = None
                            limit_up_price = None
                            if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                                limit_down_price = self.limit_down_price
                            if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                                limit_up_price = self.limit_up_price
                            elif hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                                limit_up_price, limit_down_price = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                                if limit_up_price:
                                    self.limit_up_price = limit_up_price
                                if limit_down_price:
                                    self.limit_down_price = limit_down_price
                            if limit_down_price and new_price_low < limit_down_price:
                                new_price_low = limit_down_price
                            # 使用精度处理避免浮点数精度问题，允许等于涨停价
                            if limit_up_price:
                                precision = self._get_price_precision()
                                new_price_low_rounded = round(new_price_low, precision)
                                limit_up_price_rounded = round(limit_up_price, precision)
                                if new_price_low_rounded > limit_up_price_rounded:
                                    new_price_low = limit_up_price
                        
                        precision = self._get_price_precision()
                        self.dragged_rule['price_low'] = round(new_price_low, precision)
                        # 使用屏幕坐标计算相对移动，避免Y轴扩大导致的恶性循环
                        new_volume = self._calculate_volume_from_pixel_drag(event)
                        self.dragged_rule['volume'] = new_volume
                        self.draw_price_position_chart()
                
                elif self.drag_mode == 'high':
                    # 拖动上限点：只改变上限价格，不改变下限
                    new_price_high = event.xdata
                    # 确保上限不低于下限
                    if new_price_high > price_low:
                        # 价格限制：买入笼子的上限不能超过涨停价，卖出笼子的上限不能超过涨停价
                        if rule_type == 'cage_buy':
                            limit_up_price = None
                            if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                                limit_up_price = self.limit_up_price
                            elif hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                                limit_up_price, _ = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                                if limit_up_price:
                                    self.limit_up_price = limit_up_price
                            # 使用精度处理避免浮点数精度问题，允许等于涨停价
                            if limit_up_price:
                                precision = self._get_price_precision()
                                new_price_high_rounded = round(new_price_high, precision)
                                limit_up_price_rounded = round(limit_up_price, precision)
                                if new_price_high_rounded > limit_up_price_rounded:
                                    new_price_high = limit_up_price
                        elif rule_type == 'cage_sell':
                            limit_up_price = None
                            if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                                limit_up_price = self.limit_up_price
                            elif hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                                limit_up_price, _ = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                                if limit_up_price:
                                    self.limit_up_price = limit_up_price
                            # 使用小的容差值避免浮点数精度问题，允许等于涨停价
                            epsilon = 0.001
                            if limit_up_price and new_price_high > limit_up_price + epsilon:
                                new_price_high = limit_up_price
                        
                        precision = self._get_price_precision()
                        self.dragged_rule['price_high'] = round(new_price_high, precision)
                        # 使用屏幕坐标计算相对移动，避免Y轴扩大导致的恶性循环
                        new_volume = self._calculate_volume_from_pixel_drag(event)
                        self.dragged_rule['volume'] = new_volume
                        self.draw_price_position_chart()
                
                elif self.drag_mode == 'middle':
                    # 拖动中间区域：整体平移（保持区间宽度）
                    if self.drag_start_x:
                        delta_x = event.xdata - self.drag_start_x
                        new_price_low = price_low + delta_x
                        new_price_high = price_high + delta_x
                        
                        # 价格限制：确保整体平移后不超出涨跌停价
                        if rule_type == 'cage_buy':
                            limit_up_price = None
                            limit_down_price = None
                            if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                                limit_up_price = self.limit_up_price
                            if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                                limit_down_price = self.limit_down_price
                            if not limit_up_price or not limit_down_price:
                                if hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                                    limit_up_price, limit_down_price = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                                    if limit_up_price:
                                        self.limit_up_price = limit_up_price
                                    if limit_down_price:
                                        self.limit_down_price = limit_down_price
                            # 使用精度处理避免浮点数精度问题，允许等于涨跌停价
                            if limit_up_price:
                                precision = self._get_price_precision()
                                new_price_high_rounded = round(new_price_high, precision)
                                limit_up_price_rounded = round(limit_up_price, precision)
                                if new_price_high_rounded > limit_up_price_rounded:
                                    # 如果上限超过涨停价，整体向下调整
                                    delta = new_price_high - limit_up_price
                                    new_price_high = limit_up_price
                                    new_price_low = new_price_low - delta
                            if limit_down_price:
                                precision = self._get_price_precision()
                                new_price_low_rounded = round(new_price_low, precision)
                                limit_down_price_rounded = round(limit_down_price, precision)
                                if new_price_low_rounded < limit_down_price_rounded:
                                    # 如果下限低于跌停价，整体向上调整
                                    delta = limit_down_price - new_price_low
                                    new_price_low = limit_down_price
                                    new_price_high = new_price_high + delta
                        elif rule_type == 'cage_sell':
                            limit_up_price = None
                            limit_down_price = None
                            if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                                limit_up_price = self.limit_up_price
                            if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                                limit_down_price = self.limit_down_price
                            if not limit_up_price or not limit_down_price:
                                if hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                                    limit_up_price, limit_down_price = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                                    if limit_up_price:
                                        self.limit_up_price = limit_up_price
                                    if limit_down_price:
                                        self.limit_down_price = limit_down_price
                            # 使用小的容差值避免浮点数精度问题，允许等于涨停价
                            epsilon = 0.001
                            if limit_up_price and new_price_high > limit_up_price + epsilon:
                                # 如果上限超过涨停价，整体向下调整
                                delta = new_price_high - limit_up_price
                                new_price_high = limit_up_price
                                new_price_low = new_price_low - delta
                            if limit_down_price and new_price_low < limit_down_price:
                                # 如果下限低于跌停价，整体向上调整
                                delta = limit_down_price - new_price_low
                                new_price_low = limit_down_price
                                new_price_high = new_price_high + delta
                        
                        precision = self._get_price_precision()
                        self.dragged_rule['price_low'] = round(new_price_low, precision)
                        self.dragged_rule['price_high'] = round(new_price_high, precision)
                        
                        # 使用屏幕坐标计算相对移动，避免Y轴扩大导致的恶性循环
                        new_volume = self._calculate_volume_from_pixel_drag(event)
                        self.dragged_rule['volume'] = new_volume
                        
                        # 更新拖动起始点，实现连续拖动
                        self.drag_start_x = event.xdata
                        
                        self.draw_price_position_chart()
            
            elif rule_type == 'grid_buy':
                # 网格买入拖动逻辑
                # start_price = 高价（右侧）, end_price = 低价（左侧）
                start_price = self.dragged_rule.get('start_price', 0)
                end_price = self.dragged_rule.get('end_price', 0)
                num_grids = self.dragged_rule.get('num_grids', 2)
                grid_step = self.dragged_rule.get('grid_step', 0.5)
                executed_grids = self.dragged_rule.get('executed_grids', [])
                
                # 兼容旧数据：如果没有 end_price，根据 start_price 和 grid_step 计算
                if end_price == 0 and start_price > 0:
                    end_price = start_price - num_grids * grid_step
                    precision = self._get_price_precision()
                    self.dragged_rule['end_price'] = round(end_price, precision)
                
                # Y轴方向：改变每格交易量，使用屏幕坐标计算相对移动，避免Y轴扩大导致的恶性循环
                new_volume_per_grid = self._calculate_volume_from_pixel_drag(event)
                self.dragged_rule['volume_per_grid'] = new_volume_per_grid
                
                # 获取已执行节点的固定价格（从保存的固定价格中获取）
                executed_grid_prices = self.dragged_rule.get('executed_grid_prices', {})  # {grid_index: fixed_price}
                executed_prices = executed_grid_prices.copy()  # 使用保存的固定价格，而不是重新计算
                
                if self.drag_mode == 'grid_high':
                    # 拖动高价端（右侧）：改变start_price
                    new_start_price = event.xdata
                    
                    # 如果高价端已执行（索引0），禁止拖动
                    if 0 in executed_grids:
                        # 高价端已执行，不允许拖动，保持start_price不变
                        new_start_price = start_price
                    else:
                        # 高价端未执行，可以正常拖动
                        # 价格限制：网格买入的高价端不能超过涨停价
                        limit_up_price = None
                        if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                            limit_up_price = self.limit_up_price
                        elif hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                            limit_up_price, _ = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                            if limit_up_price:
                                self.limit_up_price = limit_up_price
                        # 使用精度处理避免浮点数精度问题，允许等于涨停价
                        if limit_up_price:
                            precision = self._get_price_precision()
                            new_start_price_rounded = round(new_start_price, precision)
                            limit_up_price_rounded = round(limit_up_price, precision)
                            if new_start_price_rounded > limit_up_price_rounded:
                                new_start_price = limit_up_price
                        
                        precision = self._get_price_precision()
                        self.dragged_rule['start_price'] = round(new_start_price, precision)
                        # 重新计算网格间距
                        price_range = new_start_price - end_price
                        if price_range > 0 and num_grids > 0:
                            new_grid_step = price_range / num_grids
                            self.dragged_rule['grid_step'] = round(new_grid_step, precision)
                    
                    self.draw_price_position_chart()
                
                elif self.drag_mode == 'grid_low':
                    # 拖动低价端（左侧）：改变end_price
                    new_end_price = event.xdata
                    
                    # 如果低价端已执行（索引num_grids），禁止拖动
                    if num_grids in executed_grids:
                        # 低价端已执行，不允许拖动，保持end_price不变
                        new_end_price = end_price
                    else:
                        # 低价端未执行，可以正常拖动
                        # 价格限制：网格买入的低价端不能低于跌停价
                        limit_down_price = None
                        if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                            limit_down_price = self.limit_down_price
                        elif hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                            _, limit_down_price = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                            if limit_down_price:
                                self.limit_down_price = limit_down_price
                        # 使用精度处理避免浮点数精度问题，允许等于跌停价
                        if limit_down_price:
                            precision = self._get_price_precision()
                            new_end_price_rounded = round(new_end_price, precision)
                            limit_down_price_rounded = round(limit_down_price, precision)
                            if new_end_price_rounded < limit_down_price_rounded:
                                new_end_price = limit_down_price
                        
                        precision = self._get_price_precision()
                        self.dragged_rule['end_price'] = round(new_end_price, precision)
                        # 重新计算网格间距
                        price_range = start_price - new_end_price
                        if price_range > 0 and num_grids > 0:
                            new_grid_step = price_range / num_grids
                            self.dragged_rule['grid_step'] = round(new_grid_step, precision)
                    
                    self.draw_price_position_chart()
                
                elif self.drag_mode == 'grid_middle':
                    # 拖动中间点：只改变每格交易量（已在上面处理）
                    self.draw_price_position_chart()
            
            elif rule_type == 'grid_sell':
                # 网格卖出拖动逻辑
                # start_price = 低价（左侧）, end_price = 高价（右侧）
                start_price = self.dragged_rule.get('start_price', 0)
                end_price = self.dragged_rule.get('end_price', 0)
                num_grids = self.dragged_rule.get('num_grids', 2)
                grid_step = self.dragged_rule.get('grid_step', 0.5)
                executed_grids = self.dragged_rule.get('executed_grids', [])
                
                # 兼容旧数据：如果没有 end_price，根据 start_price 和 grid_step 计算
                if end_price == 0 and start_price > 0:
                    end_price = start_price + num_grids * grid_step
                    precision = self._get_price_precision()
                    self.dragged_rule['end_price'] = round(end_price, precision)
                
                # Y轴方向：改变每格交易量，使用屏幕坐标计算相对移动，避免Y轴扩大导致的恶性循环
                new_volume_per_grid = self._calculate_volume_from_pixel_drag(event)
                self.dragged_rule['volume_per_grid'] = new_volume_per_grid
                
                # 获取已执行节点的固定价格（从保存的固定价格中获取）
                executed_grid_prices = self.dragged_rule.get('executed_grid_prices', {})  # {grid_index: fixed_price}
                executed_prices = executed_grid_prices.copy()  # 使用保存的固定价格，而不是重新计算
                
                if self.drag_mode == 'grid_low':
                    # 拖动低价端（左侧）：改变start_price
                    new_start_price = event.xdata
                    
                    # 如果低价端已执行（索引0），禁止拖动
                    if 0 in executed_grids:
                        # 低价端已执行，不允许拖动，保持start_price不变
                        new_start_price = start_price
                    else:
                        # 低价端未执行，可以正常拖动
                        # 价格限制：网格卖出的低价端不能低于跌停价，也不能超过涨停价
                        limit_down_price = None
                        limit_up_price = None
                        if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                            limit_down_price = self.limit_down_price
                        if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                            limit_up_price = self.limit_up_price
                        elif hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                            limit_up_price, limit_down_price = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                            if limit_up_price:
                                self.limit_up_price = limit_up_price
                            if limit_down_price:
                                self.limit_down_price = limit_down_price
                        if limit_down_price and new_start_price < limit_down_price:
                            new_start_price = limit_down_price
                        # 使用精度处理避免浮点数精度问题，允许等于涨停价
                        if limit_up_price:
                            precision = self._get_price_precision()
                            new_start_price_rounded = round(new_start_price, precision)
                            limit_up_price_rounded = round(limit_up_price, precision)
                            if new_start_price_rounded > limit_up_price_rounded:
                                new_start_price = limit_up_price
                        
                        precision = self._get_price_precision()
                        self.dragged_rule['start_price'] = round(new_start_price, precision)
                        # 重新计算网格间距
                        price_range = end_price - new_start_price
                        if price_range > 0 and num_grids > 0:
                            new_grid_step = price_range / num_grids
                            self.dragged_rule['grid_step'] = round(new_grid_step, precision)
                    
                    self.draw_price_position_chart()
                
                elif self.drag_mode == 'grid_high':
                    # 拖动高价端（右侧）：改变end_price
                    new_end_price = event.xdata
                    
                    # 如果高价端已执行（索引num_grids），禁止拖动
                    if num_grids in executed_grids:
                        # 高价端已执行，不允许拖动，保持end_price不变
                        new_end_price = end_price
                    else:
                        # 高价端未执行，可以正常拖动
                        # 价格限制：网格卖出的高价端不能超过涨停价
                        limit_up_price = None
                        if hasattr(self, 'limit_up_price') and self.limit_up_price > 0:
                            limit_up_price = self.limit_up_price
                        elif hasattr(self, 'prev_close_price') and self.prev_close_price > 0:
                            limit_up_price, _ = self.calculate_limit_prices(self.stock_code, self.prev_close_price)
                            if limit_up_price:
                                self.limit_up_price = limit_up_price
                        # 使用小的容差值避免浮点数精度问题，允许等于涨停价
                        epsilon = 0.001
                        if limit_up_price and new_end_price > limit_up_price + epsilon:
                            new_end_price = limit_up_price
                        
                        precision = self._get_price_precision()
                        self.dragged_rule['end_price'] = round(new_end_price, precision)
                        # 重新计算网格间距
                        price_range = new_end_price - start_price
                        if price_range > 0 and num_grids > 0:
                            new_grid_step = price_range / num_grids
                            self.dragged_rule['grid_step'] = round(new_grid_step, precision)
                    
                    self.draw_price_position_chart()
                
                elif self.drag_mode == 'grid_middle':
                    # 拖动中间点：只改变每格交易量（已在上面处理）
                    self.draw_price_position_chart()
                
    def on_mouse_release(self, event):
        """鼠标释放事件"""
        # 如果正在添加规则（笼子/网格）
        if self.adding_rule and self.temp_rule_start:
            # 检查事件是否在坐标轴内
            if event.inaxes != self.price_position_ax or event.xdata is None:
                # 鼠标不在图表区域内，重置状态
                self.adding_rule = False
                self.temp_rule_start = None
                return
            
            # 检查是否只点击了一个点（没有拖动）
            # 最小区间按标的最小价位：股票约 0.01，ETF 约 0.001（原先写死 0.01 导致 ETF 小笼子画不上）
            price_diff = abs(event.xdata - self.temp_rule_start)
            precision = self._get_price_precision()
            min_tick = 10 ** (-int(precision))
            min_span = min_tick  # 至少跨越 1 个最小价位
            
            if price_diff < min_span:
                from PyQt5.QtWidgets import QMessageBox
                rule_type = self.add_mode
                if rule_type in ['cage_buy', 'cage_sell']:
                    rule_name = "笼子买入" if rule_type == 'cage_buy' else "笼子卖出"
                elif rule_type in ['grid_buy', 'grid_sell']:
                    rule_name = "网格买入" if rule_type == 'grid_buy' else "网格卖出"
                else:
                    rule_name = "规则"
                
                QMessageBox.warning(
                    self,
                    "提示",
                    f"价格区间太小，未能创建{rule_name}。\n\n"
                    f"当前标的最小价位约 {min_tick:.{precision}f} 元，"
                    f"请按住左键横向拖出更大的价格区间后再松开。\n\n"
                    f"操作方式：\n"
                    f"1. 按住鼠标左键不松\n"
                    f"2. 在价格轴方向拖动到目标价\n"
                    f"3. 松开鼠标完成创建"
                )
                
                # 重置状态，不创建规则
                self.adding_rule = False
                self.temp_rule_start = None
                return
            
            price_low = min(self.temp_rule_start, event.xdata)
            price_high = max(self.temp_rule_start, event.xdata)
            current_volume = event.ydata
            
            # 检查买入/卖出规则的位置限制
            buy_modes = ['cage_buy', 'grid_buy']
            sell_modes = ['cage_sell', 'grid_sell']
            
            can_add = True
            if self.add_mode in buy_modes:
                # 买入模式：鼠标必须在X轴上方（y >= 0）
                if current_volume < 0:
                    can_add = False
                
                # 买入区间：自动钳到涨跌停内（与创建规则内逻辑一致）
                if can_add:
                    if not hasattr(self, 'prev_close_price') or self.prev_close_price <= 0:
                        try:
                            self.calculate_key_points(force_recalculate=True)
                        except Exception as e:
                            self.logger.warning(f"[{self.stock_code}] 拖动创建笼子/网格无法刷新关键价位: {e}")
                    price_low, price_high = self._clamp_rule_price_interval(
                        price_low, price_high, "拖动创建(买)"
                    )
                            
            elif self.add_mode in sell_modes:
                # 卖出模式：鼠标必须在X轴下方（y <= 0）
                if current_volume > 0:
                    can_add = False
                
                if can_add:
                    if not hasattr(self, 'prev_close_price') or self.prev_close_price <= 0:
                        try:
                            self.calculate_key_points(force_recalculate=True)
                        except Exception as e:
                            self.logger.warning(f"[{self.stock_code}] 拖动创建笼子/网格无法刷新关键价位: {e}")
                    price_low, price_high = self._clamp_rule_price_interval(
                        price_low, price_high, "拖动创建(卖)"
                    )
            
            if not can_add:
                # 不符合位置要求，不添加规则，重置状态
                self.adding_rule = False
                self.temp_rule_start = None
                return
            
            # 对齐到100的倍数（与添加规则时的计算逻辑一致）
            volume = max(100, int(abs(current_volume) / 100) * 100)
            
            rule_type = self.add_mode
            
            if rule_type in ['cage_buy', 'cage_sell']:
                # 笼子规则：直接创建
                self._create_cage_rule(price_low, price_high, volume)
            elif rule_type == 'grid_buy':
                # 网格买入：直接创建（不弹出对话框）
                self._create_grid_buy_rule_simple(price_low, price_high, volume)
            elif rule_type == 'grid_sell':
                # 网格卖出：直接创建（不弹出对话框）
                self._create_grid_sell_rule_simple(price_low, price_high, volume)
            
            # 重置状态
            self.adding_rule = False
            self.temp_rule_start = None
            return
        
        # 如果正在拖动规则
        if self.dragging == 'rule' and self.dragged_rule:
            # 如果规则已经提前下单，取消之前的订单（因为价格已改变）
            if (self.dragged_rule.get('early_order', False) and 
                not self.dragged_rule.get('executed', False)):
                rule_name = self.dragged_rule.get('name', '未命名规则')
                self.logger.info(f"[{self.stock_code}] 🔄 拖动已提前下单的节点，取消之前的订单: {rule_name}")
                self._cancel_single_early_order(self.dragged_rule)
            
            # 保存规则
            self._save_rules()
        
        self.dragging = None
        self.dragged_rule = None
        self.adding_rule = False
        self.temp_rule_start = None
    
    def _handle_double_click(self):
        """处理双击事件：
        - 在1列全屏模式下：双击退出全屏
        - 在2-4列全屏模式下：双击切换到该股的1列全屏
        - 在单列非全屏模式下：双击打开全屏
        - 在2-4列非全屏模式下：双击切换到1列布局并显示该股票
        """
        try:
            # 查找父组件 TasksChartsView
            parent = self.parent()
            tasks_charts_view = None
            while parent:
                # 检查是否是 TasksChartsView 类型
                if parent.__class__.__name__ == 'TasksChartsView':
                    tasks_charts_view = parent
                    break
                parent = parent.parent()
            
            if not tasks_charts_view:
                return
            
            # 检查是否在全屏模式和当前列数（直接检查columns，更可靠）
            is_fullscreen = hasattr(tasks_charts_view, 'is_fullscreen') and tasks_charts_view.is_fullscreen
            current_columns = getattr(tasks_charts_view, 'columns', 1)
            
            if is_fullscreen:
                # 全屏模式下
                if current_columns == 1:
                    # 1列全屏：双击退出全屏，保持在1列布局
                    if hasattr(tasks_charts_view, 'exit_fullscreen'):
                        tasks_charts_view.exit_fullscreen()
                        # 确保退出全屏后保持在1列布局（延迟检查，带重试机制）
                        from PyQt5.QtCore import QTimer
                        retry_count = [0]  # 使用列表以便在闭包中修改
                        max_retries = 5
                        def ensure_single_column():
                            retry_count[0] += 1
                            if (hasattr(tasks_charts_view, 'columns') and 
                                tasks_charts_view.columns != 1):
                                # 如果列数不是1列，切换到1列
                                tasks_charts_view.columns = 1
                                if hasattr(tasks_charts_view, 'column_button_group'):
                                    button = tasks_charts_view.column_button_group.button(1)
                                    if button:
                                        button.blockSignals(True)
                                        button.setChecked(True)
                                        button.blockSignals(False)
                                tasks_charts_view.load_tasks()
                                # 如果还有重试次数，继续检查
                                if retry_count[0] < max_retries:
                                    QTimer.singleShot(200, ensure_single_column)
                        QTimer.singleShot(100, ensure_single_column)
                else:
                    # 2-4列全屏：双击切换到该股的1列全屏
                    if hasattr(tasks_charts_view, 'switch_to_single_column_and_show_stock'):
                        tasks_charts_view.switch_to_single_column_and_show_stock(self.stock_code)
                    # 延迟强制更新布局，确保 load_tasks() 完成后布局正确显示（带重试机制）
                    from PyQt5.QtCore import QTimer
                    retry_count = [0]
                    max_retries = 10
                    def update_layout():
                        retry_count[0] += 1
                        # 确保列数已切换到1列
                        if hasattr(tasks_charts_view, 'columns') and tasks_charts_view.columns == 1:
                            # 强制更新布局
                            if hasattr(tasks_charts_view, 'grid_layout'):
                                tasks_charts_view.grid_layout.update()
                            # 确保全屏状态保持
                            if (hasattr(tasks_charts_view, 'is_fullscreen') and 
                                    not tasks_charts_view.is_fullscreen):
                                if hasattr(tasks_charts_view, 'enter_fullscreen'):
                                    tasks_charts_view.enter_fullscreen()
                        elif retry_count[0] < max_retries:
                            # 如果列数还没切换，继续重试
                            QTimer.singleShot(200, update_layout)
                    # 延迟500ms，确保 load_tasks() 和图表创建完成
                    QTimer.singleShot(500, update_layout)
            else:
                # 非全屏模式下
                if current_columns == 1:
                    # 单列非全屏模式：双击打开全屏
                    if hasattr(tasks_charts_view, 'enter_fullscreen'):
                        tasks_charts_view.enter_fullscreen()
                else:
                    # 2-4列非全屏：切换到1列显示该股票（不进入全屏）
                    if hasattr(tasks_charts_view, 'switch_to_single_column_and_show_stock'):
                        tasks_charts_view.switch_to_single_column_and_show_stock(self.stock_code)
        except Exception as e:
            self.logger.error(f"处理双击事件失败: {str(e)}", exc_info=True)
    
    def _start_night_market_timer(self, night_market_rules):
        """启动夜市规则定时器"""
        from datetime import datetime, time, timedelta
        from PyQt5.QtCore import QTimer
        
        # 保存夜市规则列表
        self.night_market_rules = night_market_rules
        self.night_market_send_count = 0
        self.night_market_high_freq_mode = False
        self.night_market_high_freq_end_time = None
        
        # 判断是否是交易日
        try:
            from utils.trading_day import is_tradeday
            current_date = datetime.now().date()
            is_trading_day = is_tradeday(current_date)
        except ImportError:
            # 如果没有chncal模块，简单判断工作日
            is_trading_day = datetime.now().weekday() < 5
        
        # 获取当前时间
        now = datetime.now()
        current_time = now.time()
        
        # 判断执行时机
        should_start_immediately = False
        start_time = None
        
        if not is_trading_day:
            # 非交易日，立即执行
            should_start_immediately = True
            self.logger.info(f"[{self.stock_code}] 非交易日，夜市规则立即开始执行")
        elif current_time < time(9, 15):
            # 交易日且在9:15:00前，立即执行
            should_start_immediately = True
            self.logger.info(f"[{self.stock_code}] 交易日9:15前，夜市规则立即开始执行")
        else:
            # 交易日且在9:15:00后，在19:29:59.9开始
            today = now.date()
            start_time = datetime.combine(today, time(19, 29, 59, 900000))  # 19:29:59.9
            if start_time < now:
                # 如果已经过了19:29:59.9，则立即开始执行（而不是等到明天）
                should_start_immediately = True
                self.logger.info(f"[{self.stock_code}] 交易日已过19:29:59.90，夜市规则立即开始执行")
            else:
                self.logger.info(f"[{self.stock_code}] 夜市规则将在 {start_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-4]} 开始执行")
        
        # 计算结束时间（下一个交易日的9:15:00前）
        self.night_market_end_time = self._get_next_trading_day_915()
        self.logger.info(f"[{self.stock_code}] 夜市规则将在 {self.night_market_end_time.strftime('%Y-%m-%d %H:%M:%S')} 结束")
        
        if should_start_immediately:
            # 立即开始执行
            self.night_market_start_time = now
            self._execute_night_market_rules()
            # 创建定时器，每10秒执行一次
            self.night_market_timer = QTimer()
            self.night_market_timer.timeout.connect(self._on_night_market_timer)
            self.night_market_timer.start(10000)  # 每10秒一次
        else:
            # 等待到指定时间开始
            self.night_market_start_time = start_time
            delay_ms = int((start_time - now).total_seconds() * 1000)
            if delay_ms > 0:
                # 创建单次定时器，在指定时间触发
                QTimer.singleShot(delay_ms, self._start_night_market_execution)
            else:
                # 如果已经过了开始时间，立即开始
                self._start_night_market_execution()
    
    def _start_night_market_execution(self):
        """在指定时间开始夜市规则执行"""
        from datetime import datetime
        
        self.night_market_start_time = datetime.now()
        self.night_market_high_freq_mode = True
        self.night_market_high_freq_end_time = self.night_market_start_time + timedelta(seconds=2)
        self.night_market_send_count = 0
        
        self.logger.info(f"[{self.stock_code}] 夜市规则开始执行（高频模式）")
        
        # 立即执行一次
        self._execute_night_market_rules()
        
        # 创建定时器，高频模式：每秒10次（100ms一次），持续2秒
        self.night_market_timer = QTimer()
        self.night_market_timer.timeout.connect(self._on_night_market_timer)
        self.night_market_timer.start(100)  # 100ms = 每秒10次
    
    def _on_night_market_timer(self):
        """夜市定时器回调"""
        from datetime import datetime
        
        # 检查是否应该结束
        if datetime.now() >= self.night_market_end_time:
            self.logger.info(f"[{self.stock_code}] 夜市规则已到达结束时间，停止执行")
            self._stop_night_market_timer()
            return
        
        # 检查是否应该切换到正常频率
        if self.night_market_high_freq_mode:
            if datetime.now() >= self.night_market_high_freq_end_time:
                # 切换到正常频率：每10秒一次
                self.night_market_high_freq_mode = False
                self.night_market_timer.stop()
                self.night_market_timer.start(10000)  # 每10秒一次
                self.logger.info(f"[{self.stock_code}] 夜市规则切换到正常频率（每10秒一次）")
        
        # 执行夜市规则
        self._execute_night_market_rules()
    
    def _execute_night_market_rules(self):
        """执行夜市规则"""
        if not self.task_running or self.task_paused:
            return
        
        # 过滤出仍然有效的夜市规则
        # 注意：夜市委托不立即标记为已执行，而是等待订单回报确认"已报"状态
        # 所以这里只过滤已禁用或已确认"已报"的规则
        valid_rules = []
        for r in self.night_market_rules:
            if not r.get('enabled', True):
                continue
            # 如果规则已标记为已执行（通过订单回报确认），则跳过
            if r.get('executed', False):
                continue
            # 如果规则正在等待订单回报确认，也继续执行（可能收到废单，需要重试）
            valid_rules.append(r)
        
        if not valid_rules:
            self.logger.info(f"[{self.stock_code}] 所有夜市规则已完成或已禁用，停止定时器")
            self._stop_night_market_timer()
            return
        
        # 为每个规则发送订单
        for rule in valid_rules:
            rule_type = rule.get('type')
            price = rule.get('price', 0)
            volume = rule.get('volume', 0)
            
            if price <= 0 or volume <= 0:
                continue
            
            # 构造交易信息
            trade_info = {
                'type': 'buy' if rule_type == 'night_buy' else 'sell',
                'price': price,
                'volume': volume,
                'reason': '夜市委托'
            }
            
            # 构造tick数据（夜市规则不需要真实的tick数据）
            tick_data = {
                'stock_code': self.stock_code,
                'lastPrice': price,
                'time': datetime.now()
            }
            
            # 执行交易
            self._execute_trade(rule, trade_info, tick_data)
        
        self.night_market_send_count += 1
    
    def _get_next_trading_day_915(self):
        """获取下一个交易日的9:15:00"""
        from datetime import datetime, time, timedelta
        
        try:
            from utils.trading_day import is_tradeday
        except ImportError:
            # 如果没有chncal模块，简单计算下一个工作日
            next_day = datetime.now().date() + timedelta(days=1)
            while next_day.weekday() >= 5:  # 跳过周末
                next_day += timedelta(days=1)
            return datetime.combine(next_day, time(9, 15))
        
        # 查找下一个交易日
        check_date = datetime.now().date() + timedelta(days=1)
        max_days = 10  # 最多查找10天
        
        for _ in range(max_days):
            if is_tradeday(check_date):
                return datetime.combine(check_date, time(9, 15))
            check_date += timedelta(days=1)
        
        # 如果找不到，返回当前时间+1天的9:15（作为fallback）
        return datetime.combine(datetime.now().date() + timedelta(days=1), time(9, 15))
    
    def _next_trading_day_date(self, after_date=None):
        """after_date 之后的第一个交易日（不含 after_date 当天）。"""
        from datetime import date, timedelta, datetime as dt_cls
        
        if after_date is None:
            after_date = dt_cls.now().date()
        try:
            from utils.trading_day import is_tradeday
        except ImportError:
            d = after_date + timedelta(days=1)
            while d.weekday() >= 5:
                d += timedelta(days=1)
            return d
        d = after_date + timedelta(days=1)
        for _ in range(60):
            if is_tradeday(d):
                return d
            d += timedelta(days=1)
        return after_date + timedelta(days=1)

    def _scheduled_clear_effective_date_for_new_rule(self):
        """
        定时清仓生效交易日：交易日 15:00 之后（及非交易日）新建/改时间 → 下一交易日；
        交易日 15:00 前 → 当日。
        """
        from datetime import datetime, time as dt_time
        
        now = datetime.now()
        today = now.date()
        try:
            from utils.trading_day import is_tradeday
        except ImportError:
            is_td = today.weekday() < 5
        else:
            is_td = is_tradeday(today)
        if is_td and now.time() >= dt_time(15, 0):
            return self._next_trading_day_date(today)
        if is_td:
            return today
        return self._next_trading_day_date(today)

    def _resolve_scheduled_clear_effective_date(self, rule):
        """读取或推断定时清仓应在哪个交易日检查执行。"""
        from datetime import datetime, time as dt_time
        
        s = (rule.get("scheduled_clear_effective_date") or "").strip()
        if s:
            try:
                return datetime.strptime(s, "%Y-%m-%d").date()
            except ValueError:
                pass
        # 旧规则无生效日：交易日 15:00 后不再按「当日已过定时点」误判为已执行/错过
        now = datetime.now()
        today = now.date()
        try:
            from utils.trading_day import is_tradeday
        except ImportError:
            is_td = today.weekday() < 5
        else:
            is_td = is_tradeday(today)
        if is_td and now.time() >= dt_time(15, 0):
            return self._next_trading_day_date(today)
        if is_td:
            return today
        return self._next_trading_day_date(today)

    def _scheduled_clear_rule_active_today(self, rule) -> bool:
        from datetime import datetime
        
        return datetime.now().date() == self._resolve_scheduled_clear_effective_date(rule)

    def _attach_scheduled_clear_effective_date(self, rule, *, reset_runtime: bool = True):
        """写入生效交易日；可选重置执行/待执行状态（新建或改时间时用）。"""
        eff = self._scheduled_clear_effective_date_for_new_rule()
        rule["scheduled_clear_effective_date"] = eff.strftime("%Y-%m-%d")
        if reset_runtime:
            rule["scheduled_clear_executed"] = False
            rule["pending_tick_execution"] = False
            rule.pop("scheduled_clear_order_attempted", None)
    
    def _get_main_window_ext(self):
        """获取MainWindowExt实例"""
        try:
            # 向上遍历父组件找到主窗口
            parent = self.parent()
            while parent:
                # 检查是否是主窗口
                if hasattr(parent, 'ext'):
                    return parent.ext
                elif hasattr(parent, 'window'):
                    main_window = parent.window()
                    if hasattr(main_window, 'ext'):
                        return main_window.ext
                parent = parent.parent()
            
            # 如果找不到，尝试从task_manager获取
            if hasattr(self, 'task_manager') and self.task_manager:
                if hasattr(self.task_manager, 'qmt_adapter') and self.task_manager.qmt_adapter:
                    if hasattr(self.task_manager.qmt_adapter, 'main_window'):
                        return self.task_manager.qmt_adapter.main_window
            
            return None
        except Exception as e:
            self.logger.warning(f"获取MainWindowExt失败: {str(e)}")
            return None
    
    def _restore_one_label_visual(self, text_obj):
        """悬停结束：还原标签的 zorder、bbox、描边（与重绘前一致）"""
        if text_obj is None:
            return
        try:
            if text_obj in self.label_original_bbox:
                text_obj.set_bbox(dict(self.label_original_bbox[text_obj]))
            if text_obj in self.label_original_zorder:
                text_obj.set_zorder(self.label_original_zorder[text_obj])
            text_obj.set_path_effects([])
        except Exception:
            pass

    def _emphasize_hovered_label(self, text_obj):
        """悬停中：抬高 zorder、加粗边框、提高不透明度、白描边，便于在重叠标签中辨认"""
        if text_obj is None or text_obj not in self.label_original_bbox:
            return
        try:
            b = dict(self.label_original_bbox[text_obj])
            b["alpha"] = 0.98
            lw = float(b.get("linewidth") or 1)
            b["linewidth"] = max(lw, 2.8)
            b["boxstyle"] = "round,pad=0.42"
            text_obj.set_bbox(b)
            # 高于坐标轴刻度(150)、价格提示(1000)与交易标签本身
            text_obj.set_zorder(3500)
            import matplotlib.patheffects as mpe
            text_obj.set_path_effects(
                [mpe.withStroke(linewidth=4.5, foreground="white"), mpe.Normal()]
            )
        except Exception:
            try:
                text_obj.set_zorder(3500)
            except Exception:
                pass

    def _handle_label_hover(self, mouse_x, mouse_y, _event=None):
        """处理标签悬停：仅当鼠标靠近散点节点（价格×成交量）时高亮对应标签，避免大标签框挡住节点操作。"""
        try:
            # 如果正在拖动或添加规则，不处理悬停
            if self.dragging or self.adding_rule:
                return
            
            # 获取价格和交易量范围
            x_min = getattr(self, 'x_min', None)
            x_max = getattr(self, 'x_max', None)
            y_max = getattr(self, 'y_max', None)
            
            if x_min is None or x_max is None or y_max is None:
                return
            
            price_range = x_max - x_min
            volume_range = y_max * 2
            
            # 检测距离阈值（价格和交易量的相对距离）— 只用于节点，不用于文字标签区域
            price_threshold = price_range * 0.02  # 价格范围的2%
            volume_threshold = volume_range * 0.03  # 交易量范围的3%
            
            current_labels = None
            closest_node = None
            closest_distance = float("inf")
            for (node_price, node_volume), _labels in self.node_label_map.items():
                price_dist = abs(mouse_x - node_price)
                volume_dist = abs(mouse_y - node_volume)
                if price_dist < price_threshold and volume_dist < volume_threshold:
                    normalized_dist = (price_dist / price_threshold) + (volume_dist / volume_threshold)
                    if normalized_dist < closest_distance:
                        closest_distance = normalized_dist
                        closest_node = (node_price, node_volume)
            if closest_node:
                current_labels = self.node_label_map.get(closest_node) or []
            
            if current_labels:
                if closest_node == self._hovered_node_key and self.hovered_labels:
                    return
                for t in self.hovered_labels:
                    self._restore_one_label_visual(t)
                for t in current_labels:
                    self._emphasize_hovered_label(t)
                self.hovered_labels = list(current_labels)
                self._hovered_node_key = closest_node
                self.canvas.draw_idle()
            else:
                self._reset_label_zorders()
        except Exception:
            pass
    
    def _reset_label_zorders(self):
        """恢复悬停标签的原始 zorder 与样式"""
        try:
            if self.hovered_labels:
                for t in self.hovered_labels:
                    self._restore_one_label_visual(t)
                self.hovered_labels = []
                self._hovered_node_key = None
                self.canvas.draw_idle()
        except Exception:
            pass
    
    def _play_trade_sound(self):
        """播放交易执行音效"""
        try:
            main_window_ext = self._get_main_window_ext()
            if main_window_ext and hasattr(main_window_ext, 'play_trade_sound'):
                main_window_ext.play_trade_sound()
        except Exception as e:
            # 静默失败，不影响交易执行
            pass
    
    def _stop_night_market_timer(self):
        """停止夜市定时器"""
        if self.night_market_timer:
            self.night_market_timer.stop()
            self.night_market_timer = None
    
    def toggle_scheduled_clear(self):
        """切换定时清仓启用状态（进入添加模式，在图表上点击添加节点）"""
        if self.add_mode == 'scheduled_clear':
            # 如果已经在添加模式，则退出
            self._clear_add_mode()
        else:
            # 进入添加模式
            self.set_add_mode('scheduled_clear')
    
    def show_scheduled_clear_menu(self, pos):
        """显示定时清仓右键菜单"""
        from PyQt5.QtWidgets import QMenu, QInputDialog, QMessageBox
        from datetime import datetime, time as dt_time
        
        menu = QMenu(self)
        
        # 修改时间
        edit_time_action = menu.addAction("修改时间")
        edit_time_action.triggered.connect(self.edit_scheduled_clear_time)
        
        # 如果已启用，显示当前设置
        if self.scheduled_clear_enabled:
            menu.addSeparator()
            time_str = self.scheduled_clear_time.strftime("%H:%M:%S")
            info_action = menu.addAction(f"当前设置:\n时间: {time_str}\n触发价格: {self.scheduled_clear_price:.2f}元\n卖出数量: {self.scheduled_clear_volume}股")
            info_action.setEnabled(False)
        
        menu.exec_(self.scheduled_clear_tool_btn.mapToGlobal(pos))
    
    def edit_scheduled_clear_time(self):
        """编辑定时清仓时间"""
        from datetime import time as dt_time
        
        # 创建自定义时间对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("修改定时清仓时间")
        dialog.setModal(True)
        
        layout = QVBoxLayout()
        
        # 提示标签
        label = QLabel("请设置时间（可使用上下箭头调整时分秒）:")
        label.setFont(QFont("Microsoft YaHei", 10))
        layout.addWidget(label)
        
        # 时间选择控件
        time_edit = QTimeEdit()
        time_edit.setDisplayFormat("HH:mm:ss")
        time_edit.setFont(QFont("Microsoft YaHei", 12))
        
        # 设置当前时间
        current_time = self.scheduled_clear_time
        time_edit.setTime(QTime(current_time.hour, current_time.minute, current_time.second))
        
        layout.addWidget(time_edit)
        
        # 按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)
        
        dialog.setLayout(layout)
        
        # 显示对话框
        if dialog.exec_() == QDialog.Accepted:
            qtime = time_edit.time()
            self.scheduled_clear_time = dt_time(qtime.hour(), qtime.minute(), qtime.second())
            time_str = f"{qtime.hour():02d}:{qtime.minute():02d}:{qtime.second():02d}"
            
            # 更新按钮显示
            if self.scheduled_clear_enabled:
                self.scheduled_clear_tool_btn.setText(f"⏰定时清仓\n{time_str}")
                self.scheduled_clear_tool_btn.setToolTip(
                    f"定时清仓已启用\n时间: {time_str}\n触发价格: {self.scheduled_clear_price:.2f}元\n卖出数量: {self.scheduled_clear_volume}股"
                )
            
            self.logger.info(f"[{self.stock_code}] 定时清仓时间已修改为: {time_str}")
    
    def check_scheduled_clear(self):
        """定时清仓已由 task_manager.scheduled_clear_manager 集中调度。"""
        return
    
    def execute_scheduled_clear_rule(self, rule, tick_data=None):
        """执行定时清仓卖出（针对特定规则）
        
        Args:
            rule: 定时清仓规则
            tick_data: 可选的tick数据，如果提供则优先使用此数据计算买一价
        """
        rule_name = rule.get('name', '未命名')
        
        # 如果任务已暂停，不执行定时清仓规则
        if self.task_paused:
            self.logger.info(f"[{self.stock_code}] 定时清仓规则「{rule_name}」跳过执行：任务已暂停")
            return
        
        if rule.get('scheduled_clear_executed', False):
            return
        
        # 检查持仓
        if self.position_volume <= 0:
            self.logger.warning(f"[{self.stock_code}] 定时清仓规则「{rule_name}」失败：无可用持仓")
            rule['scheduled_clear_executed'] = True
            self._save_rules()
            return
        
        # 获取触发价格和数量
        trigger_price = rule.get('price', 0)
        volume = rule.get('volume', 0)
        
        # 调整卖出数量（不超过可用持仓）
        sell_volume = min(volume, self.position_volume)
        
        # 获取qmt_adapter
        qmt_adapter = None
        if hasattr(self, 'task_manager') and self.task_manager:
            if hasattr(self.task_manager, 'qmt_adapter'):
                qmt_adapter = self.task_manager.qmt_adapter
        
        if not qmt_adapter:
            self.logger.error(f"[{self.stock_code}] 定时清仓规则「{rule_name}」失败：无法获取QMT适配器")
            rule['scheduled_clear_executed'] = True
            self._save_rules()
            return
        
        # 计算带滑点的卖出价格（与突破卖出一致）
        # 使用买一价（bidPrice[0]）为基准，减去滑点（向下调整）
        sell_price = self.current_price  # 默认使用当前价格
        
        # 优先使用传入的tick_data，如果没有则使用保存的最新tick数据
        tick_data_to_use = tick_data if tick_data is not None else self._last_tick_data
        
        # 尝试使用tick数据计算带滑点的价格
        if tick_data_to_use:
            try:
                # 导入必要的工具类
                from core.utils.security_type import SecurityTypeUtil
                
                # 根据证券类型决定精度
                precision = 3 if SecurityTypeUtil.is_fund(self.stock_code) else 2
                
                # 根据精度设置滑点值
                slippage = 0.001 if precision == 3 else 0.01
                
                # 检查是否为夜市任务（定时清仓通常不是夜市，但为了兼容性保留检查）
                is_night_market = False  # 定时清仓通常不是夜市
                
                # 卖出时，以买一价（bidPrice[0]）为基准，减去滑点（向下调整）
                # bidPrice是买档（买方挂单），bidPrice[0]是买一价
                bid_list = tick_data_to_use.get('bidPrice') or []
                base_price = None
                try:
                    if isinstance(bid_list, (list, tuple)) and len(bid_list) > 0:
                        bp0 = float(bid_list[0] or 0)
                        # bidPrice[0]==0 时也会导致卖出价为负数/不合理；此处回退到当前价
                        if bp0 > 0:
                            base_price = bp0
                except Exception:
                    base_price = None

                if base_price is not None:
                    self.logger.info(
                        f"[{self.stock_code}] 定时清仓使用tick数据的买一价: {base_price:.{precision}f}元"
                    )
                else:
                    # 如果没有有效买一价（为空/为0/异常），使用当前价格
                    base_price = float(self.current_price or 0)
                    self.logger.warning(
                        f"[{self.stock_code}] 定时清仓tick数据买一价无效（<=0），使用当前价: {base_price:.{precision}f}元"
                    )
                
                # 夜市不使用滑点，其他委托向下调整一个最小单位
                if is_night_market:
                    sell_price = round(base_price, precision)
                else:
                    sell_price = round(base_price - slippage, precision)

                # 防止出现负数/0 价格
                if sell_price <= 0:
                    sell_price = round(float(self.current_price or 0), precision)

                # 若已计算出低于跌停价，则直接钳制到跌停价，避免定时清仓因为价格低于跌停而失败
                if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
                    limit_dn = float(self.limit_down_price)
                    cur_px = float(self.current_price or 0)
                    # 跌停状态下，只允许以跌停价成交/排单，因此：
                    # 1) 当前价已接近/低于跌停价：强制委托价=跌停价
                    # 2) 否则若计算价低于跌停价：也钳制到跌停价（避免失败）
                    if cur_px <= limit_dn or sell_price < limit_dn:
                        self.logger.info(
                            f"[{self.stock_code}] 定时清仓委托价钳制：{sell_price:.{precision}f} → 跌停价 {limit_dn:.{precision}f}"
                        )
                        sell_price = round(limit_dn, precision)
                
                # 记录价格调整日志
                if sell_price != self.current_price:
                    self.logger.info(
                        f"[{self.stock_code}] 定时清仓价格调整：{self.current_price:.{precision}f} → {sell_price:.{precision}f} "
                        f"(买一价={base_price:.{precision}f}, 滑点=-{slippage:.{precision}f})"
                    )
            except Exception as e:
                # 如果计算滑点失败，使用原价格，记录警告但不影响交易
                self.logger.warning(f"[{self.stock_code}] 定时清仓计算滑点价格失败，使用当前价格: {str(e)}")
                sell_price = self.current_price
        else:
            # 如果没有tick数据，使用当前价格
            self.logger.warning(
                f"[{self.stock_code}] 定时清仓无可用tick数据，使用当前价格: {sell_price:.2f}元"
            )
        
        # 检查价格是否低于跌停价
        if hasattr(self, 'limit_down_price') and self.limit_down_price > 0:
            if sell_price < self.limit_down_price:
                self.logger.warning(
                    f"[{self.stock_code}] 定时清仓规则「{rule_name}」失败：卖出价格 {sell_price:.2f}元 低于跌停价 {self.limit_down_price:.2f}元"
                )
                rule['scheduled_clear_executed'] = True
                self._save_rules()
                return
        
        # 执行卖出
        try:
            from core.smart_sell import direct_sell_order_strategy_name

            order_id = qmt_adapter.trade(
                stock_code=self.stock_code,
                order_type='sell',
                price=sell_price,
                volume=sell_volume,
                strategy_name=direct_sell_order_strategy_name("scheduled_clear")
            )
            
            # 只要调用了下单指令，就标记为已下单（显示灰色）
            rule['scheduled_clear_order_attempted'] = True
            
            if order_id and str(order_id) not in ['-1', '0', '']:
                self.logger.info(
                    f"[{self.stock_code}] ✅ 定时清仓规则「{rule_name}」执行成功: 卖出 {sell_volume}股 @ {sell_price:.2f}元 (订单号: {order_id})"
                )
                # 播放交易音效
                self._play_trade_sound()
            else:
                self.logger.warning(
                    f"[{self.stock_code}] ⚠️ 定时清仓规则「{rule_name}」下单返回无效订单号: {order_id}, 可能已作为夜市委托提交"
                )
            
            # 标记为已执行
            rule['scheduled_clear_executed'] = True
            self._save_rules()
            # 刷新图表，使节点颜色变为灰色（已下单）
            QTimer.singleShot(100, self.update_chart)
            
        except Exception as e:
            self.logger.error(f"[{self.stock_code}] 定时清仓规则「{rule_name}」执行失败: {str(e)}", exc_info=True)
            # 即使下单失败，只要调用了下单指令，也标记为已下单（显示灰色）
            rule['scheduled_clear_order_attempted'] = True
            rule['scheduled_clear_executed'] = True
            self._save_rules()
            # 刷新图表，使节点颜色变为灰色（已下单）
            QTimer.singleShot(100, self.update_chart)


if __name__ == '__main__':
    from PyQt5.QtWidgets import QApplication
    
    app = QApplication(sys.argv)
    
    # 创建测试数据
    import datetime
    import random
    
    # 生成模拟数据
    start_time = datetime.datetime.now()
    time_data = []
    price_data = []
    volume_data = []
    
    for i in range(100):
        time_data.append(start_time + datetime.timedelta(minutes=i))
        price_data.append(10.0 + random.gauss(0, 0.5))
        volume_data.append(random.randint(1000, 10000))
    
    # 创建图表
    widget = StockChartWidget("000001", "平安银行")
    widget.set_prices(buy_price=10.5, sell_price=11.5, buy_volume=1000, sell_volume=1000)
    widget.update_price_data(time_data, price_data, volume_data)
    
    widget.show()
    sys.exit(app.exec_())

