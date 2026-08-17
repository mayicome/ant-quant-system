from PyQt5.QtCore import pyqtSlot, Qt, QTimer, QMetaObject, Q_ARG, QObject, QDateTime, QDate, QTime
from PyQt5.QtWidgets import (QProgressBar, QWidget, QHBoxLayout, QTableWidgetItem, 
                           QComboBox, QPushButton, QMenu, QDialog, QVBoxLayout, 
                           QLabel, QSpinBox, QDoubleSpinBox, QDialogButtonBox, 
                           QStyle, QSplitter, QTextEdit, QMessageBox, QSizePolicy, 
                           QScrollArea, QFrame, QInputDialog, QAction, QLineEdit, QAbstractItemView,
                           QDateTimeEdit)
from PyQt5.QtGui import QColor
from .main_window import Ui_mainWindow
from .dialogs import (VolumeEditDialog, ParameterDialog, 
                     NightSellParameterDialog, NightBuyParameterDialog)
from .tasks_charts_view import TasksChartsView
import pandas as pd
import os
from datetime import datetime, timedelta, timezone, time as datetime_time
import logging
from core.task_manager import TaskManager
from utils.trading_day import is_tradeday
from PyQt5.QtWidgets import QApplication
from ui.custom_text_edit import AutoScrollTextEdit
from core.utils.security_type import SecurityTypeUtil
from utils.logger import Logger
import my_function as myf
import re
import time
import sys
import json
import threading
# 移除冲突的导入，使用utils.stock_info_manager中的函数
# from my_function import get_stock_name, load_all_stocks_info
from functools import partial
import uuid
import cProfile
import pstats
import io
import functools
from ui.position_manager import PositionManager
from ui.trade_record_manager import TradeRecordManager
from utils.config import Config
from brokers.qmt_adapter import QMTManager
import psutil

class TextEditHandler(logging.Handler):
    """自定义日志处理器，将日志输出到TextEdit控件"""
    def __init__(self, text_edit):
        super().__init__()
        self.text_edit = text_edit
        
    def emit(self, record):
        """输出日志记录"""
        msg = self.format(record)
        self.text_edit.append(msg)


def _default_schedule_reload_qdatetime() -> QDateTime:
    """
    状态栏「定时重载」缺省时间：下一有效执行点 = 交易日 09:28:00（本地）。
    - 若今天为交易日且当前时刻早于 09:28，用今天 09:28；
    - 否则顺延到之后第一个交易日 09:28。
    与原先「当前时间+1小时」不同；后者易被误认为缺省被改。
    """
    t_target = datetime_time(9, 28, 0)
    now = datetime.now()
    d = now.date()
    for _ in range(400):
        try:
            traded = is_tradeday(d)
        except Exception:
            traded = d.weekday() < 5
        if traded:
            cand = datetime.combine(d, t_target)
            if cand > now:
                return QDateTime(QDate(d.year, d.month, d.day), QTime(9, 28, 0))
        d = d + timedelta(days=1)
    # 交易日历异常时：至少按「工作日 09:28」给出可读缺省，避免长期落在「+1 小时」
    dd = now.date()
    for _ in range(14):
        if dd.weekday() < 5:
            cand = datetime.combine(dd, t_target)
            if cand > now:
                return QDateTime(QDate(dd.year, dd.month, dd.day), QTime(9, 28, 0))
        dd = dd + timedelta(days=1)
    return QDateTime.currentDateTime().addSecs(3600)


class MainWindowExt(Ui_mainWindow):
    """扩展主窗口功能，不修改原始生成文件"""
    MORNING_CHECK_TIME = (9, 17, 50)  # (小时, 分钟, 秒)
    #MORNING_CHECK_TIME = (20, 25, 55)  # (小时, 分钟, 秒)
    def __init__(self):
        super().__init__()
        # 移除不必要的TaskManager创建，使用外部传入的task_manager
        # self.task_manager = TaskManager()
        self.task_manager = None  # 将由set_task_manager方法设置
        # 初始化 logger
        self.logger = Logger()
        self.qmt_adapter = None
        self._last_started_row = None
        # 初始化订单监控字典
        self.order_monitors = {}
        self.position_manager = None  # 将在set_qmt_adapter中初始化
        self.trade_record_manager = TradeRecordManager()
        # 音效开关状态（默认关闭）
        self.sound_enabled = False
        # 音效设置文件路径
        self.sound_settings_file = 'sound_settings.json'
        # 定时重载并自动启动（到点执行一次）
        self._scheduled_reload_run_at = None  # type: datetime | None
        self._scheduled_reload_busy = False


    def append_log(self, text):
        """添加日志到文本框"""
        # 检查是否正在关闭程序
        if hasattr(self, '_is_closing') and self._is_closing:
            return
            
        if hasattr(self, 'textEdit') and self.textEdit is not None:
            try:
                self.textEdit.append(text)
                # 滚动到底部
                self.textEdit.verticalScrollBar().setValue(self.textEdit.verticalScrollBar().maximum())
            except RuntimeError:
                # textEdit已被删除，忽略错误
                pass

    def setup_ui(self, window):
        """初始化UI"""
        super().setupUi(window)
        
        # 设置窗口字体
        font = window.font()
        font.setFamily("Microsoft YaHei")
        font.setPointSize(14)
        font.setBold(True)
        window.setFont(font)
        
        # 设置窗口样式 - 蓝色主题
        window.setStyleSheet("""
            QMainWindow {
                background-color: #f0f0f0;
            }
        """)
        
        # 直接设置菜单栏样式
        if hasattr(window, 'menuBar') and window.menuBar():
            menu_bar = window.menuBar()
            menu_bar.setStyleSheet("""
                QMenuBar {
                    background-color: #2E86AB !important;
                    color: white !important;
                    border: none !important;
                    font-weight: bold !important;
                }
                QMenuBar::item {
                    background-color: transparent !important;
                    color: white !important;
                    padding: 8px 15px !important;
                    margin: 2px !important;
                }
                QMenuBar::item:selected {
                    background-color: #1E6A8B !important;
                    border-radius: 3px !important;
                }
                QMenuBar::item:pressed {
                    background-color: #0D4A6B !important;
                    border-radius: 3px !important;
                }
                QMenu {
                    background-color: #2E86AB !important;
                    color: white !important;
                    border: 1px solid #1E6A8B !important;
                }
                QMenu::item {
                    background-color: transparent !important;
                    color: white !important;
                    padding: 8px 20px !important;
                }
                QMenu::item:selected {
                    background-color: #1E6A8B !important;
                }
                QMenu::separator {
                    background-color: #1E6A8B !important;
                    height: 1px !important;
                    margin: 5px 0px !important;
                }
            """)
        
        # 设置应用程序级别的样式表，确保菜单栏样式生效
        app = QApplication.instance()
        if app:
            app.setStyleSheet("""
                QMenuBar {
                    background-color: #2E86AB !important;
                    color: white !important;
                    border: none !important;
                    font-weight: bold !important;
                }
                QMenuBar::item {
                    background-color: transparent !important;
                    color: white !important;
                    padding: 8px 15px !important;
                    margin: 2px !important;
                }
                QMenuBar::item:selected {
                    background-color: #1E6A8B !important;
                    border-radius: 3px !important;
                }
                QMenuBar::item:pressed {
                    background-color: #0D4A6B !important;
                    border-radius: 3px !important;
                }
                QMenu {
                    background-color: #2E86AB !important;
                    color: white !important;
                    border: 1px solid #1E6A8B !important;
                }
                QMenu::item {
                    background-color: transparent !important;
                    color: white !important;
                    padding: 8px 20px !important;
                }
                QMenu::item:selected {
                    background-color: #1E6A8B !important;
                }
                QMenu::separator {
                    background-color: #1E6A8B !important;
                    height: 1px !important;
                    margin: 5px 0px !important;
                }
            """)
        
        # 隐藏整个菜单栏
        window.menuBar().setVisible(False)
        
        # 统一设置所有控件的字体样式
        window.setStyleSheet("""
            QTableWidget, QTextEdit, QLabel, QComboBox, QPushButton, QSpinBox, QDoubleSpinBox {
                font-family: "Microsoft YaHei";
                font-size: 12pt;
            }
            QMenuBar {
                font-family: "Microsoft YaHei";
                font-size: 12pt;
                min-height: 28px;
                padding: 2px;
            }
            QMenuBar::item {
                padding: 5px 10px;
                margin: 0px;
            }
            QMenu {
                font-family: "Microsoft YaHei";
                font-size: 12pt;
            }
            QMenu::item {
                padding: 8px 25px;
            }
        """)
        
        # 替换原有的 QTextEdit
        old_text_edit = self.textEdit
        
        # 创建新的 textEdit
        self.textEdit = AutoScrollTextEdit(window)
        self.textEdit.setObjectName(old_text_edit.objectName())

        # 获取父控件
        parent_widget = old_text_edit.parentWidget()
        if parent_widget:
            # 如果是 QSplitter，使用 insertWidget
            if isinstance(parent_widget, QSplitter):
                # 找到 old_text_edit 在 QSplitter 中的索引
                index = parent_widget.indexOf(old_text_edit)
                # 设置 textEdit 的尺寸策略
                self.textEdit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                # 设置最小尺寸
                self.textEdit.setMinimumSize(200, 100)
                # 在相同位置插入新控件
                parent_widget.insertWidget(index, self.textEdit)
                # 设置拉伸因子
                parent_widget.setStretchFactor(index, 1)
                # 显示 textEdit
                self.textEdit.setVisible(True)
                # 强制更新布局
                parent_widget.updateGeometry()
                parent_widget.update()

        # 删除旧控件
        old_text_edit.deleteLater()

        # 设置文本编辑框的样式
        self.textEdit.setStyleSheet("""
            QTextEdit {
                font-family: "Microsoft YaHei";
                font-size: 12pt;
                border: 1px solid #CCCCCC;
                background-color: white;
            }
        """)

        # 添加textEdit的logger
        if hasattr(window, 'logger'):
            window.logger.add_text_edit_handler(self.textEdit)
        
        # 设置表格
        #self.logger.info("设置表格")
        self.setup_position_slots(window)
        #self.logger.info("设置持仓表格完成")
        self.setup_task_list(window)
        #self.logger.info("设置任务列表完成")
        self.setup_trade_record(window)
        #self.logger.info("设置交易记录完成")
        
        # 添加状态栏
        self.statusBar = window.statusBar()
        self.statusBar.setStyleSheet("""
            QStatusBar {
                font-family: "Microsoft YaHei";
                font-size: 12pt;
                color: #333333;
                padding: 3px;
            }
        """)
        
        # 在状态栏永久显示版本信息（双击可查看详情）
        from PyQt5.QtWidgets import QLabel
        from PyQt5.QtCore import Qt
        class VersionLabel(QLabel):
            def __init__(self, parent, main_window_ext):
                super().__init__(parent)
                self.main_window_ext = main_window_ext
                self.setText("蚂蚁量化交易系统")
                self.setStyleSheet("color: #2E86AB; font-weight: bold; padding: 0 10px;")
                self.setToolTip("双击查看版本号")
                self.setCursor(Qt.PointingHandCursor)
            
            def mouseDoubleClickEvent(self, event):
                if self.main_window_ext:
                    self.main_window_ext.show_version_dialog()
                super().mouseDoubleClickEvent(event)
        
        # 确保状态栏可见
        self.statusBar.setVisible(True)

        # 布局基准（复盘 / 次日准备）
        # 必须用 permanent：本程序每 3s showMessage，会盖住 addWidget 的普通控件
        self.layout_basis_label = QLabel("")
        self.layout_basis_label.setStyleSheet(
            "font-family: 'Microsoft YaHei'; font-size: 12pt; padding: 2px 8px; color: #888;"
        )
        self._layout_basis_phase = None
        
        # 添加音效开关按钮（先添加，这样会在版本标签左边）
        from PyQt5.QtWidgets import QPushButton
        from PyQt5.QtGui import QIcon
        from PyQt5.QtCore import Qt
        
        # 加载音效设置
        self._load_sound_settings()
        
        # 创建音效开关按钮
        self.sound_btn = QPushButton()
        self.sound_btn.setCheckable(True)
        self.sound_btn.setChecked(self.sound_enabled)
        self.sound_btn.setToolTip("点击切换音效开关（默认关闭）")
        self.sound_btn.setCursor(Qt.PointingHandCursor)
        self.sound_btn.setFixedSize(40, 28)  # 增大按钮尺寸，更容易看到
        self.sound_btn.clicked.connect(self._toggle_sound)
        self._update_sound_button_icon()
        
        # 创建任务设置按钮（彩色齿轮图标，与音效按钮匹配）
        self.settings_btn = QPushButton("⚙️")
        self.settings_btn.setToolTip("任务设置（人工审核、提前下单等）")
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.settings_btn.setFixedSize(40, 28)  # 与音效按钮相同的尺寸
        self.settings_btn.clicked.connect(self.show_task_settings)
        self.settings_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: 2px solid #1976D2;
                border-radius: 4px;
                font-size: 18px;
                font-weight: bold;
                padding: 2px;
            }
            QPushButton:hover {
                background-color: #42A5F5;
                border-color: #1565C0;
            }
            QPushButton:pressed {
                background-color: #1976D2;
            }
        """)
        
        # 定时重载并自动启动控件（精确到秒）
        self.schedule_reload_label = QLabel("定时重载:")
        self.schedule_reload_dt_edit = QDateTimeEdit()
        self.schedule_reload_dt_edit.setCalendarPopup(True)
        self.schedule_reload_dt_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.schedule_reload_dt_edit.setDateTime(_default_schedule_reload_qdatetime())
        self.schedule_reload_dt_edit.setToolTip(
            "缺省为下一交易日 09:28:00（本地）；到点后自动执行：重新加载任务 + 自动启动；"
            "若当日任务表仍为空则每 5 秒重试，直到有任务或手动清除预约"
        )
        self.schedule_reload_dt_edit.setFixedWidth(170)
        self.schedule_reload_save_btn = QPushButton("预约")
        self.schedule_reload_save_btn.setFixedSize(48, 28)
        self.schedule_reload_save_btn.clicked.connect(self._on_save_scheduled_reload)
        self.schedule_reload_clear_btn = QPushButton("清除")
        self.schedule_reload_clear_btn.setFixedSize(48, 28)
        self.schedule_reload_clear_btn.clicked.connect(self._on_clear_scheduled_reload)
        self.schedule_reload_status = QLabel("未预约")
        self._schedule_reload_status_base_style = "padding: 0 6px;"
        self.schedule_reload_status.setStyleSheet(self._schedule_reload_status_base_style + "color: #666;")

        # addPermanentWidget 从右到左排列：最后添加的在最右边
        # 布局基准最先加 → 停在右侧控件组最左侧（仍不被 showMessage 盖掉）
        self.statusBar.addPermanentWidget(self.layout_basis_label)
        self._refresh_layout_basis_label()
        self.statusBar.addPermanentWidget(self.schedule_reload_label)
        self.statusBar.addPermanentWidget(self.schedule_reload_dt_edit)
        self.statusBar.addPermanentWidget(self.schedule_reload_save_btn)
        self.statusBar.addPermanentWidget(self.schedule_reload_clear_btn)
        self.statusBar.addPermanentWidget(self.schedule_reload_status)
        self.statusBar.addPermanentWidget(self.settings_btn)
        self.statusBar.addPermanentWidget(self.sound_btn)
        
        version_label = VersionLabel(window, self)
        self.statusBar.addPermanentWidget(version_label)
        
        # 创建定时器用于更新状态栏
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_status_bar)
        self.status_timer.start(3000)

        self.schedule_reload_timer = QTimer()
        self.schedule_reload_timer.timeout.connect(self._check_scheduled_reload)
        self.schedule_reload_timer.start(5000)
        self._load_scheduled_reload_from_disk()
        # 部分环境在首帧前交易日历未就绪，延迟再刷一次缺省时间，避免仍显示旧逻辑「+1 小时」
        QTimer.singleShot(300, partial(self._reapply_schedule_reload_default_display))
        app0 = QApplication.instance()
        if app0:
            try:
                app0.applicationStateChanged.connect(self._on_app_state_for_scheduled_reload)
            except Exception:
                pass

        # 创建定时器用于检查操作列宽度
        self.column_check_timer = QTimer()
        self.column_check_timer.timeout.connect(self.check_operation_column_width)
        # 临时注释掉列宽检查定时器，测试是否解决鼠标悬停卡顿问题
        # self.column_check_timer.start(60000)  # 临时改为6秒检查一次，减少频率
        
        # 不再使用定时刷新订单列表，因为：
        # 1. 订单回报(on_stock_order)已经能实时快速获取订单ID
        # 2. 撤单/下单时会主动查询订单列表
        # 3. 周期性查询会产生大量历史订单回调，导致不必要的处理和日志
        # 改为按需刷新：用户切换到订单列表标签时刷新（如有需要，可添加标签切换事件监听）
        # self.order_refresh_timer = QTimer()
        # self.order_refresh_timer.timeout.connect(self.refresh_order_list)
        # self.order_refresh_timer.start(60000)  # 已移除定时刷新
        self.order_refresh_timer = None  # 不再使用定时器
        
        # 用于记录当前显示位置
        self.current_display_index = 0
        
        # 保存 window 引用，用于关闭事件
        self.window = window
        # 不在这里覆写 closeEvent，避免绕过 TradingApp.closeEvent 的完整清理链路
        # （QMTManager.stop / TaskManager 子进程回收等都在 TradingApp.closeEvent 中）
        
        # 设置左右分隔条的比例 (splitter_3) - 左边任务列表+订单详情，右边持仓信息+实时信息
        self.splitter_3.setStretchFactor(0, 7)  # 左边占7 (任务列表+订单详情)
        self.splitter_3.setStretchFactor(1, 3)  # 右边占3 (持仓信息+实时信息)
        
        # 设置左边内部的上下分隔条比例 (splitter) - 上面任务列表，下面订单详情
        self.splitter.setStretchFactor(0, 7)  # 上面任务列表占7
        self.splitter.setStretchFactor(1, 3)  # 下面订单详情占3
        
        # 设置右边内部的上下分隔条比例 (splitter_2) - 上面持仓信息，下面实时信息
        self.splitter_2.setStretchFactor(0, 5)  # 上面持仓信息占5
        self.splitter_2.setStretchFactor(1, 5)  # 下面实时信息占5
        
        # 注意：enable_charts_view_mode 将在 set_task_manager 或 set_qmt_adapter 中调用
        # 因为此时这些对象还未设置
        
        # 强制设置分隔条的初始大小以确保比例正确
        self.splitter_3.setSizes([600, 400])  # 强制设置左右比例为6:4
        self.splitter.setSizes([600, 400])    # 强制设置左边内部上下比例为6:4
        self.splitter_2.setSizes([500, 500])  # 强制设置右边内部上下比例为5:5
        
        # 设置分隔条的样式
        splitter_style = """
            QSplitter::handle {
                background-color: #F0F0F0;
                height: 4px;
            }
        """
        self.splitter.setStyleSheet(splitter_style)
        self.splitter_2.setStyleSheet(splitter_style)
        self.splitter_3.setStyleSheet(splitter_style)
        
        # 优化UI性能
        self.optimize_ui_performance()

    def setup_position_slots(self, window):
        """设置持仓表格的基本属性"""
        if self.position_manager is not None:
            self.position_manager.setup_position_table(self.tableWidget)
        else:
            # 如果position_manager还没有初始化，先设置基本的表格属性
            self._setup_position_table_basic()
    
    def _setup_position_table_basic(self):
        """设置持仓表格的基本属性（在position_manager初始化前使用）"""
        try:
            # 设置表头
            headers = ['仓位', '代码', '名称', '持仓', '可用', '摊薄成本', '市值']
            self.tableWidget.setColumnCount(len(headers))
            self.tableWidget.setHorizontalHeaderLabels(headers)
            
            # 设置表格属性
            self.tableWidget.setShowGrid(True)
            self.tableWidget.verticalHeader().setVisible(False)
            self.tableWidget.setEditTriggers(self.tableWidget.NoEditTriggers)
            
            # 设置表格选择模式
            self.tableWidget.setSelectionBehavior(self.tableWidget.SelectRows)
            self.tableWidget.setSelectionMode(self.tableWidget.SingleSelection)
            
            # 设置列宽
            header = self.tableWidget.horizontalHeader()
            header.setSectionResizeMode(0, header.Stretch)  # 第一列拉伸
            
            # 其他列使用固定宽度
            column_widths = [100, 120, 100, 100, 120, 120]
            for i, width in enumerate(column_widths, 1):
                header.setSectionResizeMode(i, header.Fixed)
                self.tableWidget.setColumnWidth(i, width)
                
        except Exception as e:
            self.logger.error(f"设置持仓表格基本属性失败: {str(e)}")
    
    def setup_task_list(self, window):
        """设置任务列表"""
        # 设置表格样式，添加标题和边框（参照订单列表的样式）
        self.tableWidget_2.setStyleSheet("""
            QTableWidget {
                border: 1px solid #CCCCCC;
                background-color: white;
                font-family: "Microsoft YaHei";
                font-size: 12pt;
            }
            QHeaderView::section {
                background-color: #F5F5F5;
                padding: 5px;
                border: none;
                font-weight: bold;
                border-bottom: 1px solid #CCCCCC;
                font-family: "Microsoft YaHei";
                font-size: 12pt;
            }
            QTableWidget::item {
                padding: 5px;
            }
            QComboBox {
                font-family: "Microsoft YaHei";
                font-size: 12pt;
                padding: 0px;
                margin: 0px;
            }
            QPushButton {
                font-family: "Microsoft YaHei";
                font-size: 12pt;
                padding: 0px;
                margin: 0px;
                border: 1px solid #CCCCCC;
                background-color: white;
            }
            QPushButton:hover {
                background-color: #F0F0F0;
            }
            QPushButton:pressed {
                background-color: #E0E0E0;
            }
            QTableWidget::item:selected {
                background-color: #CCCCCC;
            }
        """)
        
        # 设置表头
        headers = ['代码', '名称', '可用持仓', '委托数量', '策略', '参数', '状态', '操作']
        self.tableWidget_2.setColumnCount(len(headers))
        self.tableWidget_2.setHorizontalHeaderLabels(headers)
        
        # 设置表格属性
        self.tableWidget_2.setShowGrid(True)  # 显示网格线
        self.tableWidget_2.verticalHeader().setVisible(False)
        self.tableWidget_2.setEditTriggers(self.tableWidget_2.DoubleClicked)  # 允许双击编辑
        
        # 设置行高
        self.tableWidget_2.verticalHeader().setDefaultSectionSize(40)  # 改为70像素高度，以适应两行文本
        
        # 设置表格宽度策略，确保不出现横向滚动条
        self.tableWidget_2.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        # 设置列宽 - 采用和订单列表一样的简单策略
        header = self.tableWidget_2.horizontalHeader()
        
        # 除了状态列(索引6)和操作列(索引7)保持固定宽度外，其他列都设为可交互调整
        for i in range(len(headers)):
            if i == 6:  # 状态列固定
                header.setSectionResizeMode(i, header.Fixed)
                self.tableWidget_2.setColumnWidth(i, 80)  # 状态列
            elif i == 7:  # 操作列固定
                header.setSectionResizeMode(i, header.Fixed)
                self.tableWidget_2.setColumnWidth(i, 200)  # 操作列
            elif i == 5:  # 参数列设为拉伸模式，自动填充剩余空间
                header.setSectionResizeMode(i, header.Stretch)
            else:  # 其他列都可以拖拉调整
                header.setSectionResizeMode(i, header.Interactive)
                # 设置各列的初始宽度
                if i == 0:  # 代码列
                    self.tableWidget_2.setColumnWidth(i, 120)
                elif i == 1:  # 名称列
                    self.tableWidget_2.setColumnWidth(i, 120)
                elif i == 2:  # 数量列
                    self.tableWidget_2.setColumnWidth(i, 150)
                elif i == 3:  # 初始持仓列
                    self.tableWidget_2.setColumnWidth(i, 150)
                elif i == 4:  # 策略列
                    self.tableWidget_2.setColumnWidth(i, 100)
                elif i == 5:  # 参数列
                    self.tableWidget_2.setColumnWidth(i, 80)
                elif i == 6:  # 状态列
                    self.tableWidget_2.setColumnWidth(i, 80)
                elif i == 7:  # 操作列
                    self.tableWidget_2.setColumnWidth(i, 200)
        
        # 关闭最后一列拉伸，因为现在参数列负责拉伸
        header.setStretchLastSection(False)
        
        # 添加右键菜单
        self.tableWidget_2.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tableWidget_2.customContextMenuRequested.connect(self.show_task_context_menu)
        
        # 连接信号
        self.tableWidget_2.cellChanged.connect(self.on_cell_editing)
        self.tableWidget_2.cellDoubleClicked.connect(self.on_cell_double_clicked)
        
        # 连接列宽调整信号
        self.tableWidget_2.horizontalHeader().sectionResized.connect(self.on_column_resized)
        
        # 移除重复的任务加载，因为任务管理器已经在初始化时加载了任务
        # self.task_manager.load_tasks()
    
    def show_task_context_menu(self, pos):
        """显示任务右键菜单"""
        try:
            # 获取点击的行
            row = self.tableWidget_2.rowAt(pos.y())
            if row < 0:
                return
            
            # 创建菜单
            menu = QMenu(self.window)
            
            # 使用get_task_id_from_table_row函数获取正确的任务ID
            task_id = self.get_task_id_from_table_row(row)
            if not task_id:
                self.logger.error(f"无法获取行{row}的任务ID")
                return
            
            # 获取当前状态
            status_item = self.tableWidget_2.item(row, 6)  # 状态列
            status = status_item.text() if status_item else '未运行'
            
            # 根据状态添加不同的菜单项
            if status == '未运行':
                start_action = menu.addAction('启动任务')
                start_action.triggered.connect(lambda: self.start_task_by_row(row))
            elif status == '运行中':
                stop_action = menu.addAction('停止任务')
                stop_action.triggered.connect(lambda: self.stop_task_by_row(row))
            
            # 添加删除任务选项
            delete_action = menu.addAction('删除任务')
            delete_action.triggered.connect(lambda: self.delete_task_by_row(row))
            
            # 显示菜单
            menu.exec_(self.tableWidget_2.mapToGlobal(pos))
            
        except Exception as e:
            self.logger.error(f"显示任务右键菜单失败: {str(e)}")

    def delete_task_by_row(self, row):
        """通过行号删除任务（安全版本）"""
        try:
            # 验证行号是否有效
            if row < 0 or row >= self.tableWidget_2.rowCount():
                self.logger.error(f"无效的行号: {row}")
                return
            
            # 验证该行是否有有效的股票代码
            stock_code_item = self.tableWidget_2.item(row, 0)
            if not stock_code_item or not stock_code_item.text():
                self.logger.error(f"行{row}没有有效的股票代码")
                return
            
            # 调用原有的删除方法
            self.delete_task(row)
            
        except Exception as e:
            self.logger.error(f"通过行号删除任务失败: {str(e)}")

    def update_position_list(self, asset, positions):
        """更新持仓列表"""
        self.position_manager.update_position_list(self.tableWidget, asset, positions)

    def save_daily_positions(self, positions):
        """保存每日初始持仓数据"""
        self.position_manager.save_daily_positions(positions)

    def get_previous_trading_day(self):
        """获取上一个交易日"""
        return self.position_manager.get_previous_trading_day()

    def determine_buy_date(self, stock_data):
        """确定股票的买入日期"""
        return self.position_manager.determine_buy_date(stock_data, self.task_manager)

    def update_task_list(self, positions, force_refresh=False):
        """更新任务列表
        Args:
            positions: 持仓信息
            force_refresh: 是否强制完整刷新，默认False
        """
        try:
            # 确保股票信息管理器已加载完成
            try:
                from utils.stock_info_manager import get_stock_info_manager
                stock_manager = get_stock_info_manager()
                if not stock_manager._stock_info_cache:
                    self.logger.warning("[任务列表更新] 股票信息管理器尚未加载完成，等待加载...")
                    # 等待一小段时间让股票信息管理器加载完成
                    import time
                    time.sleep(0.1)
            except Exception as e:
                self.logger.warning(f"[任务列表更新] 股票信息管理器检查失败: {e}")
            
            self.logger.info(f"[任务列表更新] 开始更新任务列表，持仓数量: {len(positions) if positions else 0}, 强制刷新: {force_refresh}")
            
            # 如果正在更新中，直接返回
            if hasattr(self, '_updating_tasks') and self._updating_tasks:
                return
            
            # 获取当前时间
            current_time = time.time()
            
            # 添加防抖机制，避免频繁更新
            if hasattr(self, '_last_task_update_time'):
                if current_time - self._last_task_update_time < 0.5:  # 0.5秒内不重复更新
                    return
            self._last_task_update_time = current_time
                
            self._updating_tasks = True
            table = self.tableWidget_2
            
            # 保存当前滚动位置
            scrollbar = table.verticalScrollBar()
            current_scroll_position = scrollbar.value()
            
            # 如果不是强制刷新，检查任务列表是否真的需要更新
            if not force_refresh:
                current_task_ids = set()
                current_task_statuses = {}
                for row in range(table.rowCount()):
                    # 从行的UserRole数据中获取任务ID
                    stock_code_item = table.item(row, 0)
                    if stock_code_item and stock_code_item.data(Qt.UserRole):
                        task_id = stock_code_item.data(Qt.UserRole)
                        current_task_ids.add(task_id)
                        # 记录当前任务状态
                        status_item = table.item(row, 6)  # 状态列是索引6
                        if status_item:
                            current_task_statuses[task_id] = status_item.text()
                
                # 检查任务管理器中的任务
                manager_task_ids = set(self.task_manager.tasks.keys())
                
                self.logger.info(f"[任务列表更新] 当前表格任务: {len(current_task_ids)}, 管理器任务: {len(manager_task_ids)}")
                
                # 如果任务列表没有变化，直接返回
                if current_task_ids == manager_task_ids:
                    self._updating_tasks = False
                    return
            
            # 保存当前正在运行的任务状态
            running_task_statuses = {}
            for row in range(table.rowCount()):
                stock_code_item = table.item(row, 0)
                if stock_code_item and stock_code_item.data(Qt.UserRole):
                    task_id = stock_code_item.data(Qt.UserRole)
                    status_item = table.item(row, 6)  # 状态列是索引6
                    if status_item and status_item.text() == '运行中':
                        # 检查任务是否真的在运行中（在running_tasks中）
                        if task_id in self.task_manager.running_tasks:
                            running_task_statuses[task_id] = '运行中'
                            self.logger.info(f"[任务列表更新] 确认任务 {task_id} 真正在运行中")
                        else:
                            self.logger.warning(f"[任务列表更新] 任务 {task_id} UI显示运行中但实际未运行，不恢复状态")
            
            self.logger.info(f"[任务列表更新] 真正运行中的任务: {list(running_task_statuses.keys())}")
            
            table.blockSignals(True)
            
            # 清空表格
            table.setRowCount(0)
            
            # 记录已添加的任务ID，避免重复
            added_task_ids = set()
            
            # 首先添加有持仓的任务（移除持仓数量过滤条件）
            if self.position_manager.get_all_positions():
                self.logger.info(f"[任务列表更新] 处理持仓股票: {list(self.position_manager.get_all_positions().keys())}")
                for stock_code, stock_data in self.position_manager.get_all_positions().items():
                    if not stock_data or not isinstance(stock_data, dict):
                        self.logger.warning(f"股票 {stock_code} 的数据无效")
                        continue
                        
                    # 移除持仓数量过滤条件，显示所有持仓股票的任务
                    # 查找该股票的所有任务
                    for task_id, task in self.task_manager.tasks.items():
                        if task.get('stock_code') == stock_code:
                            if task_id not in added_task_ids:
                                self.logger.info(f"[任务列表更新] 添加持仓任务: {task_id}")
                                # 合并任务数据和股票数据，但保护用户编辑的init_volume
                                task_data = task.copy()
                                
                                # 只更新股票名称等非关键字段，不覆盖init_volume
                                protected_fields = ['init_volume', 'volume', 'init_cost', 'base_price', 'buy_date', 'strategy', 'params']
                                for key, value in stock_data.items():
                                    if key not in protected_fields:
                                        task_data[key] = value
                                
                                # 对于普通策略任务，更新volume字段为可用持仓数量
                                if '夜市' not in task.get('strategy', ''):
                                    task_data['volume'] = self.position_manager.get_available_volume(stock_code)
                                
                                task_data['task_id'] = task_id
                                
                                # 如果这个任务之前是运行中状态，恢复其状态
                                if task_id in running_task_statuses:
                                    task_data['status'] = running_task_statuses[task_id]
                                    # 同时更新任务管理器中的状态
                                    self.task_manager.tasks[task_id]['status'] = running_task_statuses[task_id]
                                    self.logger.info(f"[任务列表更新] 恢复任务 {task_id} 状态为运行中")
                                else:
                                    # 如果任务不在真正运行中，使用任务管理器中的实际状态
                                    actual_status = self.task_manager.tasks[task_id].get('status', '未运行')
                                    task_data['status'] = actual_status
                                    self.logger.info(f"[任务列表更新] 使用任务 {task_id} 实际状态: {actual_status}")
                                
                                self._add_task_to_table(task_data)
                                added_task_ids.add(task_id)
            
            # 然后添加从文件加载的任务（移除初始股数过滤条件）
            for task_id, task in self.task_manager.tasks.items():
                if task_id not in added_task_ids:
                    stock_code = task.get('stock_code')
                    # 确保stock_code是字符串类型
                    stock_code = str(stock_code) if stock_code is not None else ''
                    if not stock_code:
                        continue
                    
                    # 移除初始股数过滤条件，显示所有任务
                    # 不再检查初始股数，让所有任务都能显示
                    
                    self.logger.info(f"[任务列表更新] 添加文件任务: {task_id}")
                    
                    # 获取股票名称（任务里「未知」时回查）
                    stock_name = task.get('stock_name', '')
                    if not stock_name or stock_name in ("未知", "未知名称"):
                        try:
                            from utils.stock_info_manager import get_stock_name
                            stock_name = get_stock_name(stock_code)
                        except Exception as e:
                            self.logger.warning(f"获取股票名称失败: {stock_code}, 错误: {e}")
                            stock_name = "未知名称"
                        if stock_name and stock_name not in ("未知", "未知名称"):
                            try:
                                self.task_manager.tasks[task_id]['stock_name'] = stock_name
                            except Exception:
                                pass
                    
                    # 创建完整的任务数据
                    task_data = task.copy()
                    task_data['stock_name'] = stock_name
                    task_data['task_id'] = task_id
                    
                    # 如果这个任务之前是运行中状态，恢复其状态
                    if task_id in running_task_statuses:
                        task_data['status'] = running_task_statuses[task_id]
                        # 同时更新任务管理器中的状态
                        self.task_manager.tasks[task_id]['status'] = running_task_statuses[task_id]
                        self.logger.info(f"[任务列表更新] 恢复文件任务 {task_id} 状态为运行中")
                    else:
                        # 如果任务不在真正运行中，使用任务管理器中的实际状态
                        actual_status = self.task_manager.tasks[task_id].get('status', '未运行')
                        task_data['status'] = actual_status
                        self.logger.info(f"[任务列表更新] 使用文件任务 {task_id} 实际状态: {actual_status}")
                    
                    self._add_task_to_table(task_data)
                    added_task_ids.add(task_id)
            
            self.logger.info(f"[任务列表更新] 共添加 {len(added_task_ids)} 个任务到表格")
            
            table.blockSignals(False)
            self._updating_tasks = False
            
            # 恢复滚动位置
            scrollbar.setValue(current_scroll_position)
            
        except Exception as e:
            self.logger.error(f"[任务列表更新] 更新任务列表失败：{str(e)}", exc_info=True)
            # 确保在异常情况下也重置状态
            if hasattr(self, '_updating_tasks'):
                self._updating_tasks = False
            # 只有在table已定义时才调用blockSignals
            try:
                table = self.tableWidget_2
                table.blockSignals(False)
            except:
                pass

    def _is_rule_task_strategy(self, strategy):
        """规则任务策略别名判断：兼容历史“万能策略”和新“规则任务”文案。"""
        s = (strategy or '').strip()
        return s == '规则任务' or s == '万能策略' or s.startswith('规则') or s.startswith('万能')

    def _strategy_display(self, task):
        """策略列显示：规则任务显示规则名（单点买入、弹性买入等），否则显示策略名"""
        s = task.get('strategy', '规则任务') or '规则任务'
        if not self._is_rule_task_strategy(s):
            return s
        params = task.get('params') or {}
        if isinstance(params, str):
            return '规则任务'
        rules = params.get('rules') or []
        names = [r.get('name') for r in rules if isinstance(r, dict) and r.get('name')]
        # rules 只描述「图形/规则表」里的条目；规则任务还会按 up/down 阈值与 up/down_operation 交易，
        # 智能买入的「从最低点反弹」即 moderate_strategy 里 down_operation=买入 + enable_smart_buy，不会单独占一行任务。
        try:
            up_th = float(params.get('up_threshold') or 0)
            down_th = float(params.get('down_threshold') or 0)
        except (TypeError, ValueError):
            up_th, down_th = 0.0, 0.0
        up_op = params.get('up_operation', '买入')
        down_op = params.get('down_operation', '不动')
        th_hint = ""
        if up_th > 0 or down_th > 0:
            th_hint = f" | 阈值↑{up_th:g}%→{up_op} ↓{down_th:g}%→{down_op}"
        if names:
            return "规则(" + ", ".join(names) + ")" + th_hint
        return ("规则任务" + th_hint) if th_hint else '规则任务'

    def _add_task_to_table(self, task_data):
        """添加任务到表格"""
        # 性能分析：开始计时
        import time
        step_start = time.time()
        
        try:
            stock_code = task_data.get('stock_code', '')
            
            # 改进task_type获取逻辑
            task_type = ''
            
            # 首先尝试从task_data的顶层获取task_type
            if 'task_type' in task_data:
                task_type = task_data.get('task_type', '')
            
            # 如果顶层没有，尝试从params中获取
            if not task_type and 'params' in task_data:
                params = task_data.get('params', {})
                if isinstance(params, dict):
                    task_type = params.get('task_type', '')
                else:
                    self.logger.warning(f"params不是字典类型: {type(params)}")
            
            # 如果还是没有，根据策略名称推断
            if not task_type:
                strategy = task_data.get('strategy', '')
                if '夜市买入' in strategy:
                    task_type = 'buy'
                elif '夜市卖出' in strategy:
                    task_type = 'sell'
            
            # 获取数量信息
            init_volume = task_data.get('init_volume', 0)
            current_volume = task_data.get('volume', 0)

            # 获取可用持仓数量
            can_use_volume = 0
            if self.position_manager.has_position(stock_code):
                can_use_volume = self.position_manager.get_available_volume(stock_code)

            
            # 直接添加任务，不做任何过滤
            #self.logger.info(f"添加任务到表格: {stock_code}, strategy={task_data.get('strategy', '')}")
            
            step_time = time.time() - step_start
            #self.logger.info(f"[性能分析] _add_task_to_table数据准备耗时: {step_time:.3f}秒")
            
            # 插入新行
            step_start = time.time()
            table = self.tableWidget_2
            row = table.rowCount()
            table.insertRow(row)
            step_time = time.time() - step_start
            #self.logger.info(f"[性能分析] 插入新行耗时: {step_time:.3f}秒")
            
            # 根据任务类型设置显示文本
            step_start = time.time()
            if task_type == 'sell':
                # 夜市卖出任务，数量列显示可用持仓，初始持仓列显示拟卖出数量
                display_volume = can_use_volume if can_use_volume > 0 else current_volume
                quantity_text = f"{display_volume}股"
                #self.logger.info(f"[任务显示] 夜市卖出任务: {stock_code}, 可用持仓: {display_volume}股, 拟卖出: {init_volume}股")
            elif task_type == 'buy':
                # 夜市买入任务，可用持仓列显示实际持仓数量，委托数量列显示拟买入数量
                display_volume = can_use_volume if can_use_volume > 0 else 0  # 显示实际持仓数量
                quantity_text = f"{display_volume}股"
            else:
                # 普通策略任务，显示可用持仓数量
                display_volume = can_use_volume if can_use_volume > 0 else current_volume
                quantity_text = f"{display_volume}股"
                #self.logger.info(f"[任务显示] 普通任务: {stock_code}, 数量: {display_volume}股(初始持仓)")
            
            # 设置基本信息
            buy_date_display = task_data.get('buy_date', '')
            # 确保buy_date_display是字符串类型
            if hasattr(buy_date_display, 'strftime'):
                # 如果是datetime对象，转换为字符串
                buy_date_display = buy_date_display.strftime('%Y-%m-%d')
            else:
                buy_date_display = str(buy_date_display)
            
            # 如果是夜市任务且买入日期包含时间戳，只显示日期部分
            if '夜市' in task_data.get('strategy', '') and '_' in buy_date_display:
                buy_date_display = buy_date_display.split('_')[0]  # 只取日期部分
            
            # 设置初始持仓显示文本
            if task_type == 'buy':
                # 夜市买入任务，委托数量列显示拟买入数量
                init_volume_text = f"{init_volume}股(拟买入)"
            elif task_type == 'sell':
                # 夜市卖出任务，显示拟卖出数量
                init_volume_text = f"{init_volume}股(拟卖出)"
            else:
                # 普通策略任务，显示初始持仓
                init_volume_text = f"{init_volume}股"
            
            # 去掉股票代码后缀，只显示6位数字
            # 确保stock_code是字符串类型
            stock_code = str(stock_code) if stock_code is not None else ''
            stock_code_display = stock_code.split('.')[0] if '.' in stock_code else stock_code
            
            items = [
                stock_code_display,
                task_data.get('stock_name', ''),
                quantity_text,
                init_volume_text,  # 初始持仓列
                task_data.get('strategy', ''),
                '',  # 参数列，后面会添加按钮
                task_data.get('status', '未运行'),  # 确保使用任务的实际状态
                ''   # 操作列，后面会添加按钮
            ]
            
            step_time = time.time() - step_start
            #self.logger.info(f"[性能分析] 准备表格数据耗时: {step_time:.3f}秒")
            
            # 设置表格项
            for col, value in enumerate(items):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)
                # 设置代码、名称、数量列不可编辑，其他列可以编辑
                if col < 3:  # 代码、名称、数量列不能修改
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, col, item)
            
            # 验证数量列是否正确设置
            volume_item = table.item(row, 2)
            if volume_item:
                actual_text = volume_item.text()
                if actual_text != quantity_text:
                    self.logger.error(f"数量列设置失败！期望: {quantity_text}, 实际: {actual_text}")
            
            # 为数量列添加双击事件处理
            volume_item = table.item(row, 2)
            if volume_item:
                volume_item.setData(Qt.UserRole, {'row': row, 'task_type': task_type})
            
            # 为初始持仓列添加双击事件处理
            init_volume_item = table.item(row, 3)
            if init_volume_item:
                init_volume_item.setData(Qt.UserRole, {'row': row, 'task_type': task_type})
                # 设置初始持仓列可编辑
                init_volume_item.setFlags(init_volume_item.flags() | Qt.ItemIsEditable)
            
            step_time = time.time() - step_start
            #self.logger.info(f"[性能分析] 设置表格项耗时: {step_time:.3f}秒")
            
            # 添加策略文本显示（不可编辑）；规则任务显示规则名如 规则(单点买入, 弹性买入)
            strategy_item = QTableWidgetItem(self._strategy_display(task_data))
            strategy_item.setTextAlignment(Qt.AlignCenter)
            strategy_item.setFlags(strategy_item.flags() & ~Qt.ItemIsEditable)  # 设置为不可编辑
            table.setItem(row, 4, strategy_item)
            
            step_time = time.time() - step_start
            #self.logger.info(f"[性能分析] 创建策略下拉框耗时: {step_time:.3f}秒")
            
            # 设置任务ID作为行数据 - 使用正确的任务ID格式
            step_start = time.time()
            if 'task_id' in task_data:
                task_id = task_data['task_id']
            else:
                # 如果没有task_id，使用UUID格式生成
                task_id = self.task_manager.generate_task_id()
            
            table.item(row, 0).setData(Qt.UserRole, task_id)
            
            # 验证任务ID是否正确设置
            stored_task_id = table.item(row, 0).data(Qt.UserRole)
            if stored_task_id != task_id:
                self.logger.error(f"任务ID设置失败！期望: {task_id}, 实际: {stored_task_id}")
                # 重新设置任务ID
                table.item(row, 0).setData(Qt.UserRole, task_id)
                self.logger.info(f"重新设置任务ID: {task_id}")
            #else:
            #    self.logger.info(f"任务ID设置成功: {task_id}")
            
            step_time = time.time() - step_start
            #self.logger.info(f"[性能分析] 设置任务ID耗时: {step_time:.3f}秒")
            
            # 添加参数设置按钮和其他控件
            step_start = time.time()
            self.setup_task_row_widgets(row, stock_code)
            step_time = time.time() - step_start
            #self.logger.info(f"[性能分析] setup_task_row_widgets耗时: {step_time:.3f}秒")
            
            # 最后再次验证任务ID是否还在
            final_task_id = table.item(row, 0).data(Qt.UserRole)
            if final_task_id != task_id:
                self.logger.error(f"任务ID在setup_task_row_widgets后被覆盖！期望: {task_id}, 实际: {final_task_id}")
                # 重新设置任务ID
                table.item(row, 0).setData(Qt.UserRole, task_id)
                self.logger.info(f"最终重新设置任务ID: {task_id}")
            #else:
            #    self.logger.info(f"任务ID保持正确: {task_id}")
            
            # 根据任务状态设置UI样式
            task_status = task_data.get('status', '未运行')
            if task_status == '运行中':
                # 设置运行中任务的UI样式
                self._set_running_task_ui_style(row)
                #self.logger.info(f"任务 {task_id} 状态为运行中，已设置UI样式")
            elif task_status == '已委托':
                # 设置已委托任务的UI样式
                self._set_delegated_task_ui_style(row)
                #self.logger.info(f"任务 {task_id} 状态为已委托，已设置UI样式")
            
        except Exception as e:
            self.logger.error(f"添加任务到表格失败: {str(e)}")
            import traceback
            self.logger.error(f"详细错误信息: {traceback.format_exc()}")


    def is_stock_in_table(self, stock_code):
        """检查股票是否已在任务列表中"""
        table = self.tableWidget_2
        for row in range(table.rowCount()):
            stock_code_item = table.item(row, 0)
            if not stock_code_item:
                continue
                
            current_stock_code = stock_code_item.text()
            if current_stock_code == stock_code:
                # 使用get_task_id_from_table_row函数获取正确的任务ID
                task_id = self.get_task_id_from_table_row(row)
                if task_id and task_id in self.task_manager.tasks:
                    return True
                
        return False
    
    def create_param_column_widget(self, row, stock_code, task_id, strategy):
        """创建参数列显示控件（包含完整策略参数信息）"""
        from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QColor
        
        # 创建主容器
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(5, 2, 5, 2)
        layout.setSpacing(2)
        
        # 获取任务参数
        task = None
        if task_id and task_id in self.task_manager.tasks:
            task = self.task_manager.tasks[task_id]
        
        if not task:
            # 如果获取不到任务信息，显示默认内容
            default_label = QLabel("参数信息不可用")
            default_label.setStyleSheet("color: gray; font-size: 10px;")
            layout.addWidget(default_label)
            return widget
        
        # 获取参数信息
        base_price = task.get('base_price', 0.0)
        params = task.get('params', {})
        
        # 确保params是字典类型（防御性编程）
        if not isinstance(params, dict):
            self.logger.warning(f"任务 {stock_code} params不是字典类型: {type(params)}, 尝试转换")
            if isinstance(params, str):
                try:
                    import json
                    params = json.loads(params)
                except (json.JSONDecodeError, TypeError, ValueError):
                    try:
                        params = json.loads(params.replace("'", '"'))
                    except:
                        params = {}
                if not isinstance(params, dict):
                    params = {}
            else:
                params = {}
        
        # 构建参数显示文本
        param_text = self.build_param_display_text(base_price, params, strategy, stock_code)
        
        # 创建参数显示标签
        param_label = QLabel(param_text)
        param_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        param_label.setStyleSheet("color: #333; line-height: 1.2;")
        param_label.setWordWrap(True)  # 允许换行
        layout.addWidget(param_label)
        
        return widget
    
    def build_param_display_text(self, base_price, params, strategy, stock_code=''):
        """构建参数显示文本"""
        # 根据股票代码确定价格精度
        from core.utils.security_type import SecurityTypeUtil
        precision = SecurityTypeUtil.get_price_precision(stock_code) if stock_code else 2
        
        # 基础信息
        text_parts = []
        
        # 基准价
        text_parts.append(f"基准价{base_price:.{precision}f}")
        
        # 检查是否为夜市任务
        if '夜市' in strategy:
            # 夜市任务只显示基准价，不显示其他参数
            return " ".join(text_parts)
        
        # 交易量和执行次数
        trade_volume = params.get('trade_volume', 0)
        cycle_times = params.get('cycle_times', 0)
        
        if trade_volume > 0:
            volume_text = f"每笔{trade_volume}股"
            if cycle_times > 0:
                volume_text += f"执行{cycle_times}次"
            text_parts.append(volume_text)
        
        # 清仓设置
        enable_smart_sell = params.get('enable_smart_sell', True)
        if not enable_smart_sell:
            text_parts.append("不清仓")
        
        # 阈值信息
        up_threshold = params.get('up_threshold', 0)
        down_threshold = params.get('down_threshold', 0)
        up_operation = params.get('up_operation', '买入')
        down_operation = params.get('down_operation', '不动')
        
        threshold_parts = []
        if up_threshold > 0:
            # 使用向上箭头符号，去掉中括号
            up_arrow = "↑"
            threshold_parts.append(f"{up_arrow}{up_threshold}%({up_operation})")
        
        if down_threshold > 0:
            # 使用向下箭头符号，去掉中括号
            down_arrow = "↓"
            threshold_parts.append(f"{down_arrow}{down_threshold}%({down_operation})")
        
        if threshold_parts:
            text_parts.append("|".join(threshold_parts))
        
        # 组合所有部分
        return " ".join(text_parts)
    
    def update_param_column_base_price(self, row, value):
        """更新参数列中的基准价显示，支持颜色变化"""
        from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QColor
        
        table = self.tableWidget_2
        
        # 获取当前参数列的控件
        current_widget = table.cellWidget(row, 5)
        if not current_widget:
            return
        
        # 获取任务信息
        stock_code_item = table.item(row, 0)
        if not stock_code_item:
            return
        
        stock_code = stock_code_item.text()
        task_id = stock_code_item.data(Qt.UserRole)
        
        if not task_id or task_id not in self.task_manager.tasks:
            return
        
        task = self.task_manager.tasks[task_id]
        strategy = task.get('strategy', '')
        
        # 如果value是字典格式（包含价格变化信息），需要特殊处理
        if isinstance(value, dict):
            new_price = value['new_price']
            old_price = value['old_price']
            price_change = value['price_change']
            
            # 更新任务中的基准价格
            task['base_price'] = new_price
            
            # 创建新的参数列控件
            new_widget = self.create_param_column_widget(row, stock_code, task_id, strategy)
            
            # 查找参数标签并设置颜色
            param_label = new_widget.findChild(QLabel)
            if param_label:
                if price_change > 0:
                    # 价格上涨，显示红色
                    param_label.setStyleSheet("color: red; line-height: 1.2;")
                    self.logger.info(f"基准价格上涨: {old_price:.3f} -> {new_price:.3f} (红色)")
                elif price_change < 0:
                    # 价格下跌，显示深绿色
                    param_label.setStyleSheet("color: #008000; line-height: 1.2;")  # 深绿色
                    self.logger.info(f"基准价格下跌: {old_price:.3f} -> {new_price:.3f} (深绿色)")
                else:
                    # 价格无变化，显示默认颜色
                    param_label.setStyleSheet("color: #333; line-height: 1.2;")
            
            # 更新表格中的控件
            table.setCellWidget(row, 5, new_widget)
        else:
            # 如果不是字典格式，直接更新基准价格并重新创建控件
            task['base_price'] = value
            new_widget = self.create_param_column_widget(row, stock_code, task_id, strategy)
            table.setCellWidget(row, 5, new_widget)
    
    def setup_task_row_widgets(self, row, stock_code):
        """设置任务行的控件"""
        # 性能分析：开始计时
        import time
        step_start = time.time()
        
        table = self.tableWidget_2

        # 获取任务ID和策略信息
        task_id = self.get_task_id_from_table_row(row)
        strategy = ""
        if task_id and task_id in self.task_manager.tasks:
            strategy = self.task_manager.tasks[task_id].get('strategy', '')
        
        # 检查是否为夜市任务（买入/卖出策略已弃用，仅保留夜市与万能）
        is_night_task = '夜市' in strategy
        is_buy_sell_strategy = False
        
        # 创建参数列显示内容（包含基准价）
        param_widget = self.create_param_column_widget(row, stock_code, task_id, strategy)
        table.setCellWidget(row, 5, param_widget)
        
        step_time = time.time() - step_start
        #self.logger.info(f"[性能分析] 创建参数按钮耗时: {step_time:.3f}秒")
        
        # 获取任务ID和实际状态
        step_start = time.time()
        task_id = self.get_task_id_from_table_row(row)
        
        # 如果从表格中获取不到任务ID，尝试从表格行的UserRole中直接获取
        if not task_id:
            stock_code_item = table.item(row, 0)
            if stock_code_item:
                task_id = stock_code_item.data(Qt.UserRole)
        
        actual_status = "未运行"
        if task_id and task_id in self.task_manager.tasks:
            actual_status = self.task_manager.tasks[task_id].get('status', '未运行')
        step_time = time.time() - step_start
        #self.logger.info(f"[性能分析] 获取任务状态耗时: {step_time:.3f}秒")
        
        # 设置状态，添加颜色样式
        step_start = time.time()
        status_item = QTableWidgetItem(actual_status)
        status_item.setTextAlignment(Qt.AlignCenter)
        # 根据状态设置颜色
        if actual_status == '运行中':
            status_item.setForeground(Qt.red)
            # 设置整行灰色背景
            for col in range(table.columnCount()):
                if col in [4, 5, 7]:  # 策略列、参数列、操作列
                    widget = table.cellWidget(row, col)
                    if widget and hasattr(widget, 'setStyleSheet'):
                        try:
                            widget.setStyleSheet("""
                                QWidget {
                                    background-color: lightGray;
                                    margin: 0px;
                                    padding: 0px;
                                }
                            """)
                        except Exception as e:
                            self.logger.warning(f"设置widget样式失败: {str(e)}")
                else:
                    item = table.item(row, col)
                    if item:
                        item.setBackground(Qt.lightGray)
            
            # 策略列现在是普通文本，不需要特殊处理
            
            # 设置参数按钮不可点击
            # 对于规则任务（兼容万能策略历史文案），即使在运行中也可以查看和修改参数
            # 对于夜市任务，参数按钮已被禁用
            if not is_night_task and not is_buy_sell_strategy:
                param_button = table.cellWidget(row, 5)
                if param_button:
                    # 检查是否为规则任务
                    task_id = self.get_task_id_from_table_row(row)
                    is_universal_strategy = False
                    if task_id and task_id in self.task_manager.tasks:
                        strategy = self.task_manager.tasks[task_id].get('strategy', '')
                        is_universal_strategy = self._is_rule_task_strategy(strategy)
                    
                    if not is_universal_strategy:
                        # 非规则任务在运行中时禁用参数按钮
                        param_button.setEnabled(False)
                        param_button.setStyleSheet("""
                            QPushButton {
                                background-color: lightGray;
                                margin: 0px;
                                padding: 0px;
                                border: 1px solid #CCCCCC;
                                color: black;
                            }
                        """)
                    else:
                        # 规则任务在运行中时保持参数按钮可用
                        param_button.setEnabled(True)
                        param_button.setStyleSheet("""
                            QPushButton { 
                                background-color: white; 
                                color: blue; 
                                text-decoration: underline; 
                                text-align: center;
                                margin: 0px;
                                padding: 0px;
                                border: 1px solid #CCCCCC;
                            }
                        """)
        else:
            status_item.setForeground(Qt.black)

            # 策略列现在是普通文本，不需要特殊处理
        
        table.setItem(row, 6, status_item)
        step_time = time.time() - step_start
        #self.logger.info(f"[性能分析] 设置状态样式耗时: {step_time:.3f}秒")
        
        # 添加操作按钮
        step_start = time.time()
        operation_widget = self.create_operation_buttons(row)
        table.setCellWidget(row, 7, operation_widget)
        step_time = time.time() - step_start
        #self.logger.info(f"[性能分析] 创建操作按钮耗时: {step_time:.3f}秒")
        
        # 更新参数显示（对于夜市任务不更新参数按钮文本）
        if not is_night_task and not is_buy_sell_strategy:
            step_start = time.time()
            self.update_param_button_text(row, stock_code)
            step_time = time.time() - step_start
        
        
        # 确保任务ID不被覆盖
        original_task_id = table.item(row, 0).data(Qt.UserRole)
        if original_task_id:
            # 重新设置任务ID，确保不被覆盖
            table.item(row, 0).setData(Qt.UserRole, original_task_id)
            #self.logger.info(f"确保任务ID不被覆盖: {original_task_id}")
    
    def show_parameter_dialog_by_row(self, row, stock_code):
        """通过行号显示参数设置对话框（安全版本）"""
        try:
            # 验证行号是否有效
            if row < 0 or row >= self.tableWidget_2.rowCount():
                self.logger.error(f"无效的行号: {row}")
                return
            
            # 验证该行是否有有效的股票代码
            stock_code_item = self.tableWidget_2.item(row, 0)
            if not stock_code_item or not stock_code_item.text():
                self.logger.error(f"行{row}没有有效的股票代码")
                return
            
            # 调用原有的参数设置方法
            self.show_parameter_dialog(row, stock_code)
            
        except Exception as e:
            self.logger.error(f"通过行号显示参数对话框失败: {str(e)}")

    def show_parameter_dialog(self, row, stock_code):
        """显示参数设置对话框"""
        try:
            # 获取任务ID
            task_id = self.get_task_id_from_table_row(row)
            if not task_id:
                self.logger.error(f"无法获取行{row}的任务ID")
                return
            
            # 获取当前参数
            current_params = self.task_manager.get_task_params(task_id)
            if not current_params:
                current_params = {}
            
            # 获取基准价格
            task = self.task_manager.tasks.get(task_id, {})
            base_price = task.get('base_price', 0.0)
            
            # 创建参数设置对话框，传递当前参数和基准价格
            dialog = ParameterDialog(self.window, current_params, base_price)
            
            if dialog.exec_() == QDialog.Accepted:
                # 获取新参数
                new_params = dialog.get_params()
                if new_params is None:  # 参数验证失败
                    return
                
                # 更新任务参数
                self.task_manager.update_task_params(task_id, new_params)
                
                # 重新创建参数列控件以显示更新后的参数
                self.refresh_param_column(row, stock_code, task_id)
                
                # 保存任务到文件
                self.task_manager.save_tasks(list(self.task_manager.tasks.values()))
                
                self.logger.info(f"任务 {task_id} 参数已更新: {new_params}")
                
                # 检查是否为运行中的规则任务，如果是则询问是否重新启动
                task = self.task_manager.tasks.get(task_id, {})
                strategy = task.get('strategy', '')
                status = task.get('status', '未运行')
                
                if (status == '运行中' and self._is_rule_task_strategy(strategy)):
                    from PyQt5.QtWidgets import QMessageBox
                    QMessageBox.information(
                        self.window,
                        "参数已更新",
                        f"规则任务参数已更新！\n\n"
                        f"股票代码: {stock_code}\n"
                        f"策略: {strategy}\n\n"
                        f"由于任务正在运行中，新参数将在下次启动时生效。\n"
                        f"如需立即应用新参数，请手动停止并重新启动该任务。"
                    )
                
        except Exception as e:
            self.logger.error(f"显示参数设置对话框失败: {str(e)}")
            QMessageBox.critical(self.window, "错误", f"显示参数设置对话框失败: {str(e)}")

    def start_task(self, row):
        """启动任务"""
        # 记录本次启动的行号（如果还没有设置的话）
        if not hasattr(self, '_last_started_row') or self._last_started_row is None:
            self._last_started_row = row
        
        stock_code = self.tableWidget_2.item(row, 0).text()
        
        # 添加调试信息
        #self.logger.info(f"===== 开始启动任务 =====")
        #self.logger.info(f"尝试启动第{row}行的任务，股票代码: {stock_code}")
        
        # 直接从表格行中获取存储的任务ID
        stock_code_item = self.tableWidget_2.item(row, 0)
        if not stock_code_item:
            self.logger.error(f"第{row}行没有股票代码项")
            return
            
        task_id = stock_code_item.data(Qt.UserRole)
        if not task_id:
            self.logger.error(f"第{row}行没有存储任务ID，无法启动任务")
            return
            
        # 验证任务ID是否存在于任务管理器中
        if task_id not in self.task_manager.tasks:
            self.logger.error(f"任务ID {task_id} 不存在于任务管理器中，无法启动任务")
            return
        
        # 添加详细调试信息
        #self.logger.info(f"从表格第{row}行获取到任务ID: {task_id}")
        #self.logger.info(f"当前运行中的任务数量: {len(self.task_manager.running_tasks)}")
        #self.logger.info(f"当前运行中的任务列表: {list(self.task_manager.running_tasks.keys())}")
        
        # 检查是否有同股票代码的其他任务在运行
        '''same_stock_running_tasks = []
        for running_task_id in self.task_manager.running_tasks:
            running_task = self.task_manager.tasks.get(running_task_id)
            if running_task and running_task.get('stock_code') == stock_code:
                same_stock_running_tasks.append(running_task_id)
        
        if same_stock_running_tasks:
            self.logger.warning(f"股票 {stock_code} 已有以下任务在运行: {same_stock_running_tasks}")
            self.logger.warning(f"即将启动的任务ID: {task_id}")'''
        
        # 在启动之前，检查是否为夜市任务且存在冲突
        task = self.task_manager.tasks.get(task_id)
        if task:
            strategy = task.get('strategy', '')
            is_night_task = '夜市' in strategy
            is_buy_sell_strategy = False
            if '夜市' in strategy or strategy in ['夜市卖出', '夜市买入']:
                # 获取当前任务的关键参数用于精确匹配
                current_direction = '买入' if '买入' in strategy else '卖出'
                current_volume = task.get('params', {}).get('buy_volume' if '买入' in strategy else 'sell_volume', task.get('init_volume', 0))
                current_price = task.get('base_price', 0)
                
                #self.logger.info(f"夜市任务检查 - 方向: {current_direction}, 数量: {current_volume}, 价格: {current_price}")
                
                # 检查是否有相同的夜市任务在运行（股票代码+方向+数量+价格完全相同）
                '''for running_task_id in self.task_manager.running_tasks:
                    # 跳过当前要启动的任务自身
                    if running_task_id == task_id:
                        continue
                        
                    running_task = self.task_manager.tasks.get(running_task_id)
                    if not running_task:
                        continue
                        
                    running_stock_code = running_task.get('stock_code')
                    running_strategy = running_task.get('strategy', '')
                    
                    if (running_stock_code == stock_code and 
                        ('夜市' in running_strategy or running_strategy in ['夜市卖出', '夜市买入'])):
                        
                        running_direction = '买入' if '买入' in running_strategy else '卖出'
                        
                        if current_direction == running_direction:
                            # 检查数量和价格是否相同（精确匹配）
                            running_volume = running_task.get('params', {}).get('buy_volume' if '买入' in running_strategy else 'sell_volume', running_task.get('init_volume', 0))
                            running_price = running_task.get('base_price', 0)
                            
                            self.logger.info(f"对比运行中任务 {running_task_id} - 方向: {running_direction}, 数量: {running_volume}, 价格: {running_price}")
                            
                            # 允许小误差的精确匹配
                            if (abs(float(current_volume) - float(running_volume)) < 1 and 
                                abs(float(current_price) - float(running_price)) < 0.01):
                                # 显示用户友好的错误提示
                                from PyQt5.QtWidgets import QMessageBox
                                self.logger.warning(f"发现冲突！阻止启动任务 {task_id}")
                                QMessageBox.warning(
                                    self.window,
                                    "启动任务失败",
                                    f"股票 {stock_code} 已有相同的夜市{current_direction}任务正在运行！\n"
                                    f"数量: {running_volume}, 价格: {running_price}\n"
                                    f"请先停止现有任务再启动新任务。"
                                )
                                return'''
        
        #self.logger.info(f"准备调用 task_manager.start_task({task_id})")
        start_result = self.task_manager.start_task(task_id)
        #self.logger.info(f"task_manager.start_task 返回结果: {start_result}")
        
        # 添加启动后状态验证
        #self.logger.info(f"启动后运行中的任务数量: {len(self.task_manager.running_tasks)}")
        #self.logger.info(f"启动后运行中的任务列表: {list(self.task_manager.running_tasks.keys())}")
        
        if start_result:
            # 暂时禁用信号
            self.tableWidget_2.blockSignals(True)
            
            # 更新状态为"运行中"并设置红色
            status_item = self.tableWidget_2.item(row, 6)  # 状态列是索引6
            status_item.setText("运行中")
            status_item.setForeground(Qt.red)  # 设置文字颜色为红色
            
            # 先重新创建操作按钮，确保状态正确
            operation_widget = self.create_operation_buttons(row)
            self.tableWidget_2.setCellWidget(row, 7, operation_widget)
            
            # 基准价列已移除，不再需要设置样式
            
            # 设置整行灰色背景（排除操作列，因为操作按钮需要保持正常样式）
            for col in range(self.tableWidget_2.columnCount()):
                if col in [4, 5]:  # 策略列、参数列
                    widget = self.tableWidget_2.cellWidget(row, col)
                    if widget:
                        try:
                            widget.setStyleSheet("""
                                QWidget {
                                    background-color: lightGray;
                                    margin: 0px;
                                    padding: 0px;
                                }
                            """)
                            # 设置布局边距为0
                            if widget.layout():
                                widget.layout().setContentsMargins(0, 0, 0, 0)
                        except Exception as e:
                            self.logger.warning(f"设置第{row}行第{col}列widget样式失败: {str(e)}")
                elif col == 7:  # 操作列，不设置灰色背景，保持按钮正常样式
                    pass
                else:
                    item = self.tableWidget_2.item(row, col)
                    if item:
                        item.setBackground(Qt.lightGray)
            
            # 策略列现在是普通文本，不需要特殊处理
            
            # 设置参数按钮不可点击（对于夜市任务已被禁用）
            # 对于规则任务（兼容万能策略历史文案），即使在运行中也可以查看和修改参数
            if not is_night_task and not is_buy_sell_strategy:
                param_button = self.tableWidget_2.cellWidget(row, 5)
                if param_button:
                    # 检查是否为规则任务
                    task_id = self.get_task_id_from_table_row(row)
                    is_universal_strategy = False
                    if task_id and task_id in self.task_manager.tasks:
                        strategy = self.task_manager.tasks[task_id].get('strategy', '')
                        is_universal_strategy = self._is_rule_task_strategy(strategy)
                    
                    if not is_universal_strategy:
                        # 非规则任务在运行中时禁用参数按钮
                        param_button.setEnabled(False)
                        try:
                            param_button.setStyleSheet("""
                                QPushButton {
                                    background-color: lightGray;
                                    margin: 0px;
                                    padding: 0px;
                                    border: 1px solid #CCCCCC;
                                    color: black;
                                }
                            """)
                        except Exception as e:
                            self.logger.warning(f"设置第{row}行参数按钮样式失败: {str(e)}")
                    else:
                        # 规则任务在运行中时保持参数按钮可用
                        param_button.setEnabled(True)
                        try:
                            param_button.setStyleSheet("""
                                QPushButton { 
                                    background-color: white; 
                                    color: blue; 
                                    text-decoration: underline; 
                                    text-align: center;
                                    margin: 0px;
                                    padding: 0px;
                                    border: 1px solid #CCCCCC;
                                }
                            """)
                        except Exception as e:
                            self.logger.warning(f"设置第{row}行规则任务参数按钮样式失败: {str(e)}")
            
            # 恢复信号
            self.tableWidget_2.blockSignals(False)
            
            # 更新按钮状态
            operation_widget = self.tableWidget_2.cellWidget(row, 7)
            if operation_widget:
                start_button = operation_widget.layout().itemAt(0).widget()
                stop_button = operation_widget.layout().itemAt(1).widget()
                start_button.setEnabled(False)  # 禁用启动按钮
                stop_button.setEnabled(True)    # 启用停止按钮
        else:
            # 启动返回 False，但可能只是「已在 running_tasks」未同步 UI
            if task_id in getattr(self.task_manager, "running_tasks", {}):
                self.logger.info(f"任务 {task_id} 已在运行，同步列表状态为运行中")
                self.tableWidget_2.blockSignals(True)
                try:
                    status_item = self.tableWidget_2.item(row, 6)
                    if status_item:
                        status_item.setText("运行中")
                        status_item.setForeground(Qt.red)
                    operation_widget = self.create_operation_buttons(row)
                    self.tableWidget_2.setCellWidget(row, 7, operation_widget)
                finally:
                    self.tableWidget_2.blockSignals(False)
                # 同步图表（若已打开）
                try:
                    charts_view = getattr(self, "tasks_charts_view", None) or getattr(
                        self.window, "tasks_charts_view", None
                    )
                    cache = getattr(charts_view, "_chart_cache", None) or {}
                    for cached in cache.values():
                        if not isinstance(cached, dict):
                            continue
                        chart = cached.get("chart")
                        if not chart or getattr(chart, "task_id", None) != task_id:
                            continue
                        chart.set_task_status(True, False)
                        if getattr(chart, "task", None) and isinstance(chart.task, dict):
                            chart.task.setdefault("params", {})
                            chart.task["params"]["task_running"] = True
                            chart.task["params"]["task_paused"] = False
                except Exception:
                    pass
            else:
                self.logger.error(f"启动任务失败: {task_id}")

    def _parse_task_order_id(self, task_data):
        """从任务数据解析撤单用订单号，优先 order_sysid，返回 (oid_str, has_valid_order)。"""
        oid_raw = None
        if task_data:
            oid_raw = task_data.get("order_sysid") or task_data.get("order_id")
        oid_str = str(oid_raw).strip() if oid_raw is not None else ""
        has_valid = bool(
            oid_str and oid_str.lower() != "nan" and oid_str not in ("-1", "0")
        )
        return oid_str, has_valid

    def _run_broker_cancel_order_if_selected(
        self, cancel_orders_first, oid_str, stock_code, context="delete"
    ):
        """
        用户选择「撤单」时执行 QMT 撤单；未连接时可改为不撤单继续。
        context: 'delete' | 'stop' — 仅影响提示文案。
        返回 False 表示用户放弃删除/停止；True 表示可继续后续流程。
        """
        if not cancel_orders_first:
            return True
        if not getattr(self, "qmt_adapter", None):
            only_text = "仅停止任务" if context == "stop" else "仅删除本地任务"
            reply = QMessageBox.question(
                self.window,
                "无法撤单",
                f"当前未连接交易端，无法发送撤单。\n是否改为{only_text}（不撤券商委托）？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return False
            return True
        try:
            ok = self.qmt_adapter.cancel_order(oid_str, stock_code)
        except Exception as ex:
            tag = "停止任务" if context == "stop" else "删除任务"
            self.logger.error(f"{tag}前撤单异常: {ex}", exc_info=True)
            ok = False
        if ok:
            QMessageBox.information(
                self.window,
                "撤单",
                "撤单请求已提交，请稍后在订单列表确认状态后再核对持仓。",
            )
        else:
            warn_tail = (
                "仍将删除本地任务记录，请在券商端核对订单。"
                if context == "delete"
                else "仍将停止任务，请在券商端核对订单。"
            )
            QMessageBox.warning(
                self.window,
                "撤单",
                "撤单接口返回失败（可能已成交、已撤或委托号无效）。\n" + warn_tail,
            )
        return True

    def stop_task(self, row):
        """停止任务"""
        stock_code = self.tableWidget_2.item(row, 0).text()
        
        # 添加调试信息
        #self.logger.info(f"尝试停止第{row}行的任务，股票代码: {stock_code}")
        
        # 直接从表格行中获取存储的任务ID
        stock_code_item = self.tableWidget_2.item(row, 0)
        if not stock_code_item:
            self.logger.error(f"第{row}行没有股票代码项")
            return
            
        task_id = stock_code_item.data(Qt.UserRole)
        if not task_id:
            self.logger.error(f"第{row}行没有存储任务ID，无法停止任务")
            return
            
        # 验证任务ID是否存在于任务管理器中
        if task_id not in self.task_manager.tasks:
            self.logger.error(f"任务ID {task_id} 不存在于任务管理器中，无法停止任务")
            return
        
        # 获取任务策略信息
        task = self.task_manager.tasks.get(task_id)
        strategy = (task.get('strategy', '规则任务') if task else '规则任务')
        is_night_task = False
        is_buy_sell_strategy = False
        if task:
            is_night_task = '夜市' in strategy
            is_buy_sell_strategy = False
        # 添加调试信息
        #self.logger.info(f"从表格第{row}行获取到任务ID: {task_id}")

        # 运行中任务在表格里常显示「运行中」，已委托状态以内存为准；与删除任务一致：已委托 + 有效委托号时询问是否撤单
        cancel_orders_first = False
        oid_str = ""
        if (
            task
            and task_id in self.task_manager.running_tasks
            and task.get("status") == "已委托"
        ):
            oid_str, has_valid_order = self._parse_task_order_id(task)
            if has_valid_order:
                box = QMessageBox(self.window)
                box.setWindowTitle("确认停止")
                box.setIcon(QMessageBox.Question)
                box.setText(
                    f"任务 {stock_code}（{strategy}）当前为「已委托」且仍在运行，本地委托号：{oid_str}。\n\n"
                    "请选择：停止任务时是否同时向券商发送撤单（未成交部分以交易所实际状态为准）。\n"
                    "若选择「仅停止任务」，策略将退出，但交易所未成交委托单仍可能有效。"
                )
                btn_cancel_and = box.addButton("停止并撤单", QMessageBox.AcceptRole)
                box.addButton("仅停止任务", QMessageBox.ActionRole)
                btn_abort = box.addButton("取消", QMessageBox.RejectRole)
                box.setDefaultButton(btn_abort)
                box.exec_()
                clicked = box.clickedButton()
                if clicked == btn_abort:
                    return
                cancel_orders_first = clicked == btn_cancel_and

        if not self._run_broker_cancel_order_if_selected(
            cancel_orders_first, oid_str, stock_code, context="stop"
        ):
            return

        if self.task_manager.stop_task(task_id):
            # 暂时禁用信号
            self.tableWidget_2.blockSignals(True)
            
            # 注释掉强制设置状态，让任务管理器通过信号来更新状态
            # status_item = self.tableWidget_2.item(row, 5)
            # status_item.setText("未运行")
            # status_item.setForeground(Qt.black)  # 设置文字颜色为黑色
            
            # 设置可编辑列可编辑，并恢复样式
            for col in [4]:  # 基准价
                item = self.tableWidget_2.item(row, col)
                if item:
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
                    # 恢复默认背景和文字颜色
                    item.setBackground(Qt.white)
                    item.setForeground(Qt.black)
            
            # 设置整行白色背景和黑色文字
            for col in range(self.tableWidget_2.columnCount()):
                if col in [4, 5, 7]:  # 策略列、参数列、操作列
                    widget = self.tableWidget_2.cellWidget(row, col)
                    if widget and hasattr(widget, 'setStyleSheet'):
                        try:
                            widget.setStyleSheet("""
                                QWidget {
                                    background-color: white;
                                    margin: 0px;
                                    padding: 0px;
                                }
                            """)
                            # 设置布局边距为0
                            if widget.layout():
                                widget.layout().setContentsMargins(0, 0, 0, 0)
                        except Exception as e:
                            self.logger.warning(f"设置widget样式失败: {str(e)}")
                else:
                    item = self.tableWidget_2.item(row, col)
                    if item:
                        item.setBackground(Qt.white)
                        item.setForeground(Qt.black)
            
            # 策略列现在是普通文本，不需要特殊处理
            
            # 设置参数按钮可点击（对于夜市任务保持禁用）
            if not is_night_task and not is_buy_sell_strategy:
                param_button = self.tableWidget_2.cellWidget(row, 5)
                if param_button:
                    param_button.setEnabled(True)
                param_button.setStyleSheet("""
                    QPushButton { 
                        background-color: white; 
                        color: blue; 
                        text-decoration: underline; 
                        text-align: center;
                        padding: 0px;
                        margin: 0px;
                        border: 1px solid #CCCCCC;
                    }
                """)
            
            # 恢复信号
            self.tableWidget_2.blockSignals(False)

    def delete_task(self, row):
        """删除任务"""
        try:
            # 获取任务信息
            stock_code = self.tableWidget_2.item(row, 0).text()
            
            # 添加调试信息
            #self.logger.info(f"尝试删除第{row}行的任务，股票代码: {stock_code}")
            
            # 直接从表格行中获取存储的任务ID
            stock_code_item = self.tableWidget_2.item(row, 0)
            if not stock_code_item:
                self.logger.error(f"第{row}行没有股票代码项")
                return
                
            task_id = stock_code_item.data(Qt.UserRole)
            if not task_id:
                self.logger.error(f"第{row}行没有存储任务ID，无法删除任务")
                return
                
            # 验证任务ID是否存在于任务管理器中
            if task_id not in self.task_manager.tasks:
                self.logger.error(f"任务ID {task_id} 不存在于任务管理器中，无法删除任务")
                return
            
            # 添加调试信息
            #self.logger.info(f"从表格第{row}行获取到任务ID: {task_id}")
            
            # 安全地获取策略名称
            strategy = "规则任务"
            task_data = self.task_manager.tasks.get(task_id)
            strategy = task_data.get('strategy', '规则任务') if task_data else '规则任务'
            buy_date_item = self.tableWidget_2.item(row, 4)
            buy_date = buy_date_item.text() if buy_date_item else datetime.now().strftime('%Y-%m-%d')
            
            # 检查是否有实际运行的进程
            is_actually_running = False
            if hasattr(self, 'task_manager') and self.task_manager and task_id in self.task_manager.running_tasks:
                try:
                    self.task_manager._cleanup_dead_processes()
                except Exception:
                    pass
                info = self.task_manager.running_tasks.get(task_id)
                alive_fn = getattr(self.task_manager, "_running_process_alive", None)
                if callable(alive_fn):
                    is_actually_running = bool(alive_fn(info))
                else:
                    is_actually_running = task_id in self.task_manager.running_tasks
            
            # 如果任务实际在运行中，无论UI显示什么状态都不能删除
            if is_actually_running:
                QMessageBox.warning(self.window, "警告", f"任务 {stock_code} ({strategy}) 正在运行中，请先停止任务再删除")
                return
            
            # 检查UI显示的状态（状态列为第 6 列：代码/名称/可用/委托数量/策略/参数/状态/操作）
            status_item = self.tableWidget_2.item(row, 6)
            current_status = status_item.text() if status_item else "未运行"
            cancel_orders_first = False
            oid_str = ""
            
            if current_status == "运行中":
                # UI显示运行中，但实际没有运行进程，可能是状态不同步
                QMessageBox.warning(self.window, "警告", f"任务 {stock_code} ({strategy}) 显示为运行中，请先停止任务再删除")
                return
            elif current_status == "已委托":
                # 已委托且存在有效委托号：让用户选择「删除并撤单」/「仅删除任务」/「取消」
                oid_str, has_valid_order = self._parse_task_order_id(task_data)
                # 注：若任务在 running_tasks 中，上面已 return，此处无需再判断 is_actually_running
                if has_valid_order:
                    box = QMessageBox(self.window)
                    box.setWindowTitle("确认删除")
                    box.setIcon(QMessageBox.Question)
                    box.setText(
                        f"任务 {stock_code}（{strategy}）当前为「已委托」，本地委托号：{oid_str}。\n\n"
                        "请选择：是否同时向券商发送撤单（未成交部分以交易所实际状态为准）。"
                    )
                    btn_cancel_and = box.addButton("删除并撤单", QMessageBox.AcceptRole)
                    box.addButton("仅删除任务", QMessageBox.ActionRole)
                    btn_abort = box.addButton("取消", QMessageBox.RejectRole)
                    box.setDefaultButton(btn_abort)
                    box.exec_()
                    clicked = box.clickedButton()
                    if clicked == btn_abort:
                        return
                    cancel_orders_first = clicked == btn_cancel_and
                else:
                    cancel_orders_first = False
            
            # 选择「删除并撤单」时先向 QMT 发送撤单，再删本地任务
            if not self._run_broker_cancel_order_if_selected(
                cancel_orders_first, oid_str, stock_code, context="delete"
            ):
                return
            
            # 暂时阻止tasks_updated信号触发，避免无限循环
            self.task_manager._block_tasks_updated_signal = True
            
            try:
                # 从任务管理器中删除任务
                self.task_manager.delete_task(task_id)
                
                # 保存更新后的任务列表
                self.task_manager.save_tasks(list(self.task_manager.tasks.values()))
                
                # 删除行后，重建整个表格（与新增任务保持一致）
                self.refresh_task_table()

                # 清理股票订阅（如果股票不在持仓、任务或其他监控中，则取消订阅）
                if hasattr(self, '_cleanup_stock_subscription'):
                    self._cleanup_stock_subscription(stock_code)

                #self.logger.info(f"已删除任务: {task_id}")
                
                # 移除重复的信号发送，因为save_tasks已经会自动发送tasks_updated信号
                # self.task_manager.tasks_updated.emit()
                
            finally:
                # 恢复tasks_updated信号
                self.task_manager._block_tasks_updated_signal = False
            
        except Exception as e:
            self.logger.error(f"删除任务失败: {str(e)}")
            QMessageBox.critical(self.window, "错误", f"删除任务失败: {str(e)}")

    def rebuild_table_task_mapping(self):
        """重新建立表格行与任务ID的对应关系"""
        try:
            table = self.tableWidget_2
            for row in range(table.rowCount()):
                # 获取当前行的任务信息
                stock_code_item = table.item(row, 0)
                if not stock_code_item:
                    continue
                    
                stock_code = stock_code_item.text()
                strategy_container = table.cellWidget(row, 4)
                strategy = strategy_container.combo.currentText() if strategy_container else "规则任务"
                buy_date_item = table.item(row, 4)
                buy_date_display = buy_date_item.text() if buy_date_item else datetime.now().strftime('%Y-%m-%d')
                
                # 查找对应的任务ID
                task_id = None
                if '夜市' in strategy:
                    # 查找匹配的夜市任务
                    for existing_task_id, existing_task in self.task_manager.tasks.items():
                        if (existing_task.get('stock_code') == stock_code and 
                            existing_task.get('strategy') == strategy):
                            # 检查买入日期的日期部分是否匹配
                            existing_buy_date = existing_task.get('buy_date', '')
                            if existing_buy_date.startswith(buy_date_display):
                                task_id = existing_task_id
                                break
                else:
                    # 普通策略任务
                    task_id = f"{stock_code}_{strategy}_{buy_date_display}"
                
                # 设置任务ID到UserRole
                if task_id and task_id in self.task_manager.tasks:
                    stock_code_item.setData(Qt.UserRole, task_id)
                    self.logger.info(f"重新建立行{row}与任务ID的映射: {task_id}")
                else:
                    self.logger.warning(f"行{row}未找到对应的任务ID")
                    
        except Exception as e:
            self.logger.error(f"重新建立表格任务映射失败: {str(e)}")

    def refresh_task_table(self):
        """刷新任务表格"""
        # 性能分析：开始计时（仅用于日志观测，不改变逻辑）
        import time
        start_time = time.time()
        
        try:
            # 检查是否正在刷新中
            if hasattr(self, '_is_refreshing') and self._is_refreshing:
                return
            
            self._is_refreshing = True
            
            # 获取当前时间用于防抖
            current_time = time.time()
            
            # 防抖机制：1秒内不重复刷新
            if hasattr(self, '_last_refresh_time'):
                if current_time - self._last_refresh_time < 1.0:
                    self._is_refreshing = False
                    return
            self._last_refresh_time = current_time
            
            # 记录开始时间
            step_start = time.time()
            
            # 检查任务数量变化
            current_task_count = len(self.task_manager.tasks) if self.task_manager else 0
            if hasattr(self, '_last_task_count'):
                if current_task_count == self._last_task_count:
                    # 尝试增量更新
                    if self._incremental_update_task_table():
                        #self.logger.info(f"[性能分析] 增量更新成功，跳过完全重建")
                        self._is_refreshing = False
                        return
            self._last_task_count = current_task_count
            
            # 获取表格引用
            table = self.tableWidget_2
            
            # 保存滚动位置
            scrollbar = table.verticalScrollBar()
            current_scroll_position = scrollbar.value()
            
            # 阻塞信号
            table.blockSignals(True)
            
            # 清理旧控件 - 这是关键的性能瓶颈点
            step_start = time.time()
            #self.logger.info(f"[性能分析] 开始清理旧控件，当前表格行数: {table.rowCount()}")
            
            # 清理所有cellWidget，防止内存和信号泄漏
            for row in range(table.rowCount()):
                for col in range(table.columnCount()):
                    widget = table.cellWidget(row, col)
                    if widget:
                        widget.deleteLater()
            
            # 清空表格
            table.setRowCount(0)
            
            # 重建表格内容
            step_start = time.time()
            #self.logger.info(f"[性能分析] 开始重建表格，任务数量: {len(self.task_manager.tasks) if self.task_manager else 0}")
            
            # 记录已添加的任务ID，避免重复
            added_task_ids = set()
            
            # 保存当前正在运行的任务状态
            running_task_statuses = {}
            for row in range(table.rowCount()):
                stock_code_item = table.item(row, 0)
                if stock_code_item and stock_code_item.data(Qt.UserRole):
                    task_id = stock_code_item.data(Qt.UserRole)
                    status_item = table.item(row, 6)  # 状态列是索引6
                    if status_item and status_item.text() == '运行中':
                        # 检查任务是否真的在运行中（在running_tasks中）
                        if task_id in self.task_manager.running_tasks:
                            running_task_statuses[task_id] = '运行中'
                            self.logger.info(f"[任务列表更新] 确认任务 {task_id} 真正在运行中")
                        else:
                            self.logger.warning(f"[任务列表更新] 任务 {task_id} UI显示运行中但实际未运行，不恢复状态")
            
            #self.logger.info(f"[任务列表更新] 真正运行中的任务: {list(running_task_statuses.keys())}")
            
            # 首先添加有持仓的任务（移除持仓数量过滤条件）
            if self.position_manager.get_all_positions():
                #self.logger.info(f"[任务列表更新] 处理持仓股票: {list(self.position_manager.get_all_positions().keys())}")
                for stock_code, stock_data in self.position_manager.get_all_positions().items():
                    if not stock_data or not isinstance(stock_data, dict):
                        self.logger.warning(f"股票 {stock_code} 的数据无效")
                        continue
                        
                    # 移除持仓数量过滤条件，显示所有持仓股票的任务
                    # 查找该股票的所有任务
                    for task_id, task in self.task_manager.tasks.items():
                        if task.get('stock_code') == stock_code:
                            if task_id not in added_task_ids:
                                #self.logger.info(f"[任务列表更新] 添加持仓任务: {task_id}")
                                # 合并任务数据和股票数据，但保护用户编辑的init_volume
                                task_data = task.copy()
                                
                                # 只更新股票名称等非关键字段，不覆盖init_volume
                                protected_fields = ['init_volume', 'volume', 'init_cost', 'base_price', 'buy_date', 'strategy', 'params']
                                for key, value in stock_data.items():
                                    if key not in protected_fields:
                                        task_data[key] = value
                                
                                # 对于普通策略任务，更新volume字段为可用持仓数量
                                if '夜市' not in task.get('strategy', ''):
                                    task_data['volume'] = self.position_manager.get_available_volume(stock_code)
                                
                                task_data['task_id'] = task_id
                                
                                # 如果这个任务之前是运行中状态，恢复其状态
                                if task_id in running_task_statuses:
                                    task_data['status'] = running_task_statuses[task_id]
                                    # 同时更新任务管理器中的状态
                                    self.task_manager.tasks[task_id]['status'] = running_task_statuses[task_id]
                                    self.logger.info(f"[任务列表更新] 恢复任务 {task_id} 状态为运行中")
                                else:
                                    # 如果任务不在真正运行中，使用任务管理器中的实际状态
                                    actual_status = self.task_manager.tasks[task_id].get('status', '未运行')
                                    task_data['status'] = actual_status
                                    #self.logger.info(f"[任务列表更新] 使用任务 {task_id} 实际状态: {actual_status}")
                                
                                self._add_task_to_table(task_data)
                                added_task_ids.add(task_id)
            
            # 然后添加从文件加载的任务（只添加还没有添加过的，且初始股数大于0的）
            for task_id, task in self.task_manager.tasks.items():
                if task_id not in added_task_ids:
                    stock_code = task.get('stock_code')
                    if not stock_code:
                        continue
                    
                    # 检查初始股数，对于夜市任务和规则任务，即使初始股数为0也要显示
                    init_volume = task.get('init_volume', 0)
                    current_volume = task.get('volume', 0)
                    strategy = task.get('strategy', '')
                    if init_volume <= 0 and current_volume <= 0 and '夜市' not in strategy and not self._is_rule_task_strategy(strategy):
                        continue
                    
                    #self.logger.info(f"[性能分析] 添加文件任务: {task_id}")
                    
                    # 添加调试日志，检查params字段
                    #if '夜市' in task.get('strategy', ''):
                    #    self.logger.info(f"[调试] 夜市任务 {task_id} 的params: {task.get('params', '无params')}")
                    #    self.logger.info(f"[调试] 夜市任务 {task_id} 的完整数据: {task}")
                    
                    # 获取股票名称（任务里「未知」时回查）
                    stock_name = task.get('stock_name', '')
                    if not stock_name or stock_name in ("未知", "未知名称"):
                        try:
                            from utils.stock_info_manager import get_stock_name
                            stock_name = get_stock_name(stock_code)
                        except Exception as e:
                            self.logger.warning(f"获取股票名称失败: {stock_code}, 错误: {e}")
                            stock_name = "未知名称"
                        if stock_name and stock_name not in ("未知", "未知名称"):
                            try:
                                self.task_manager.tasks[task_id]['stock_name'] = stock_name
                            except Exception:
                                pass
                    
                    # 创建完整的任务数据，确保保留所有状态信息
                    task_data = task.copy()
                    task_data['stock_name'] = stock_name
                    task_data['task_id'] = task_id
                    
                    # 确保保留任务的实际状态
                    task_status = task.get('status', '未运行')
                    #self.logger.info(f"[表格重建] 任务 {task_id} 状态: {task_status}")
                    
                    # 检查任务是否在运行中
                    if task_id in self.task_manager.running_tasks:
                        self.logger.info(f"[表格重建] 任务 {task_id} 在running_tasks中，状态应为运行中")
                        task_data['status'] = '运行中'
                    elif task_status in ['运行中', '已委托']:
                        #self.logger.info(f"[表格重建] 任务 {task_id} 状态为 {task_status}，保留此状态")
                        task_data['status'] = task_status
                    
                    self._add_task_to_table(task_data)
                    added_task_ids.add(task_id)
            
            # 恢复信号
            table.blockSignals(False)
            
            # 恢复滚动位置
            scrollbar.setValue(current_scroll_position)
            
            # 恢复焦点到上次操作的行
            if hasattr(self, '_last_started_row') and self._last_started_row is not None:
                try:
                    if self._last_started_row < table.rowCount():
                        # 选中并滚动到指定行
                        table.selectRow(self._last_started_row)
                        table.scrollToItem(table.item(self._last_started_row, 0))
                except Exception as e:
                    # 如果恢复焦点失败，不影响主要功能
                    pass
            
            elapsed = time.time() - start_time
            if elapsed > 0.1:
                self.logger.warning(f"[性能监控] refresh_task_table 耗时: {elapsed:.3f}秒")
            self._is_refreshing = False
            
        except Exception as e:
            elapsed = time.time() - start_time
            if elapsed > 0.1:
                self.logger.warning(f"[性能监控] refresh_task_table 异常退出耗时: {elapsed:.3f}秒")
            self.logger.error(f"刷新任务表格失败: {str(e)}")
            self._is_refreshing = False
            # 确保在异常情况下也恢复信号
            try:
                table = self.tableWidget_2
                table.blockSignals(False)
            except:
                pass
        finally:
            # 这里不再使用 cProfile，避免额外开销；仅依赖时间日志
            pass

    def _incremental_update_task_table(self):
        """增量更新任务表格，只更新变化的部分"""
        import time as _time
        _t0 = _time.time()
        try:
            table = self.tableWidget_2
            updated_count = 0
            
            #self.logger.info(f"[增量更新] 开始增量更新，表格行数: {table.rowCount()}")
            
            # 检查是否有新增任务
            table_task_ids = set()
            for row in range(table.rowCount()):
                stock_code_item = table.item(row, 0)
                if stock_code_item and stock_code_item.data(Qt.UserRole):
                    table_task_ids.add(stock_code_item.data(Qt.UserRole))
            
            # 检查任务管理器中的任务
            manager_task_ids = set(self.task_manager.tasks.keys())
            
            # 如果有新增任务，需要完全重建
            new_tasks = manager_task_ids - table_task_ids
            if new_tasks:
                #self.logger.info(f"[增量更新] 发现新增任务: {new_tasks}，需要完全重建")
                if _time.time() - _t0 > 0.05:
                    self.logger.warning(f"[性能监控] _incremental_update_task_table 耗时: {_time.time() - _t0:.3f}秒 (提前返回)")
                return False
            
            # 遍历表格中的每一行
            for row in range(table.rowCount()):
                # 获取该行的任务ID
                stock_code_item = table.item(row, 0)
                if not stock_code_item or not stock_code_item.data(Qt.UserRole):
                    self.logger.debug(f"[增量更新] 第{row}行没有有效的任务ID，跳过")
                    continue
                
                task_id = stock_code_item.data(Qt.UserRole)
                #self.logger.debug(f"[增量更新] 第{row}行任务ID: {task_id}")
                
                # 检查任务是否还存在
                if task_id not in self.task_manager.tasks:
                    # 任务已被删除，标记为需要完全重建
                    if _time.time() - _t0 > 0.05:
                        self.logger.warning(f"[性能监控] _incremental_update_task_table 耗时: {_time.time() - _t0:.3f}秒 (任务已删)")
                    return False
                
                # 获取任务数据
                task_data = self.task_manager.tasks[task_id]
                current_status = task_data.get('status', '未运行')
                
                # 获取股票代码 - 确保在所有代码路径中都有定义
                stock_code = task_data.get('stock_code', '')
                if not stock_code:
                    self.logger.warning(f"[增量更新] 第{row}行任务 {task_id} 没有股票代码，跳过")
                    continue
                
                # 获取策略信息
                strategy = task_data.get('strategy', '')
                is_night_task = '夜市' in strategy
                is_buy_sell_strategy = False
                #self.logger.debug(f"[增量更新] 第{row}行任务状态: {current_status}")
                
                # 检查是否需要更新数量列
                current_volume = task_data.get('volume', 0)
                current_init_volume = task_data.get('init_volume', 0)

                # 获取可用持仓数量
                can_use_volume = 0
                if hasattr(self, 'position_manager') and self.position_manager and self.position_manager.has_position(stock_code):
                    can_use_volume = self.position_manager.get_available_volume(stock_code)
                
                # 对于夜市任务，需要从params中获取正确的数量
                strategy = task_data.get('strategy', '')
                if '夜市' in strategy:
                    params = task_data.get('params', {})
                    if isinstance(params, dict):
                        if '夜市买入' in strategy:
                            # 夜市买入任务，可用持仓列显示实际持仓数量，委托数量列显示拟买入数量
                            buy_volume = params.get('buy_volume', 0)
                            display_volume = can_use_volume if can_use_volume > 0 else 0  # 显示实际持仓数量
                            expected_display = f"{display_volume}股"
                        elif '夜市卖出' in strategy:
                            # 夜市卖出任务，数量列显示可用持仓，初始持仓列显示拟卖出数量
                            sell_volume = params.get('sell_volume', 0)
                            display_volume = can_use_volume if can_use_volume > 0 else current_volume
                            expected_display = f"{display_volume}股"
                        else:
                            # 其他夜市任务，使用volume
                            expected_display = f"{current_volume}股"
                    else:
                        expected_display = f"{current_volume}股"
                else:
                    # 普通策略任务，显示可用持仓数量
                    display_volume = can_use_volume if can_use_volume > 0 else current_volume
                    expected_display = f"{display_volume}股"
                
                volume_item = table.item(row, 2)
                if volume_item:
                    current_display = volume_item.text()
                    if current_display != expected_display:
                        volume_item.setText(expected_display)
                        updated_count += 1
                        #self.logger.info(f"[增量更新] 更新第{row}行数量列: {current_display} -> {expected_display}")
                
                # 检查是否需要更新初始持仓列
                # 根据任务类型设置初始持仓显示文本
                if '夜市' in strategy:
                    params = task_data.get('params', {})
                    if isinstance(params, dict):
                        if '夜市买入' in strategy:
                            # 夜市买入任务，显示拟买入数量
                            buy_volume = params.get('buy_volume', current_init_volume)
                            expected_init_display = f"{buy_volume}股(拟买入)"
                        elif '夜市卖出' in strategy:
                            # 夜市卖出任务，显示拟卖出数量
                            expected_init_display = f"{current_init_volume}股(拟卖出)"
                        else:
                            # 其他夜市任务，使用普通格式
                            expected_init_display = f"{current_init_volume}股"
                    else:
                        expected_init_display = f"{current_init_volume}股"
                else:
                    # 普通策略任务，显示初始持仓
                    expected_init_display = f"{current_init_volume}股"
                
                init_volume_item = table.item(row, 3)
                if init_volume_item:
                    current_init_display = init_volume_item.text()
                    if current_init_display != expected_init_display:
                        init_volume_item.setText(expected_init_display)
                        updated_count += 1
                        #self.logger.info(f"[增量更新] 更新第{row}行初始持仓列: {current_init_display} -> {expected_init_display}")
                else:
                    self.logger.warning(f"第{row}行初始持仓列未找到，无法更新")
                
                # 检查是否需要更新状态列
                status_item = table.item(row, 6)  # 状态列是索引6
                if status_item:
                    current_table_status = status_item.text()
                    if current_table_status != current_status:
                        #self.logger.info(f"[增量更新] 更新第{row}行状态列: {current_table_status} -> {current_status}")
                        status_item.setText(current_status)
                        updated_count += 1
                        
                        # 更新按钮状态
                        try:
                            self.update_task_buttons(row)
                            #self.logger.debug(f"[增量更新] 第{row}行按钮状态已更新")
                        except Exception as e:
                            self.logger.error(f"[增量更新] 更新第{row}行按钮状态失败: {str(e)}")
                    #else:
                    #    self.logger.debug(f"[增量更新] 第{row}行状态无需更新: {current_status}")
                else:
                    self.logger.warning(f"[增量更新] 第{row}行状态列为空")
            
            #if updated_count > 0:
            #    self.logger.info(f"[增量更新] 增量更新完成，更新了 {updated_count} 个字段")
            #else:
            #    self.logger.debug(f"[增量更新] 增量更新完成，没有字段需要更新")
            
            if _time.time() - _t0 > 0.05:
                self.logger.warning(f"[性能监控] _incremental_update_task_table 耗时: {_time.time() - _t0:.3f}秒")
            return True
            
        except Exception as e:
            if _time.time() - _t0 > 0.05:
                self.logger.warning(f"[性能监控] _incremental_update_task_table 异常耗时: {_time.time() - _t0:.3f}秒")
            self.logger.error(f"[增量更新] 增量更新失败: {str(e)}")
            import traceback
            self.logger.error(f"[增量更新] 错误堆栈: {traceback.format_exc()}")
            return False

    def create_operation_buttons(self, row):
        """创建操作按钮"""
        # 性能分析：开始计时
        import time
        step_start = time.time()
        
        widget = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(5, 2, 5, 2)
        
        # 创建启动按钮
        start_button = QPushButton("启动")
        start_button.setFixedWidth(50)
        start_button.clicked.connect(lambda checked, r=row: self.start_task_by_row(r))

        # 创建停止按钮
        stop_button = QPushButton("暂停")
        stop_button.setFixedWidth(50)
        stop_button.clicked.connect(lambda checked, r=row: self.stop_task_by_row(r))
        
        # 根据任务状态设置按钮状态
        step_start = time.time()
        status = self.tableWidget_2.item(row, 6).text()  # 状态列是第6列（索引6）
        if status == "运行中":
            start_button.setEnabled(False)  # 运行中时禁用启动按钮
            stop_button.setEnabled(True)    # 运行中时启用停止按钮
        elif status == "可能已委托":
            start_button.setEnabled(False)  # 可能已委托时禁用启动按钮
            stop_button.setEnabled(True)    # 可能已委托时启用停止按钮
        elif status == "已委托":
            start_button.setEnabled(True)   # 已委托时启用启动按钮（可以重新启动）
            stop_button.setEnabled(False)   # 已委托时禁用停止按钮（已经不在运行中）
        else:  # "未运行"、"连接断开"等其他状态
            start_button.setEnabled(True)   # 未运行时启用启动按钮
            stop_button.setEnabled(False)   # 未运行时禁用停止按钮
        
        layout.addWidget(start_button)
        layout.addWidget(stop_button)
        widget.setLayout(layout)
        step_time = time.time() - step_start
        #self.logger.info(f"[性能分析] 设置按钮状态和布局耗时: {step_time:.3f}秒")
        
        return widget

    def start_task_by_row(self, row):
        """通过行号启动任务（安全版本）"""
        try:
            # 验证行号是否有效
            if row < 0 or row >= self.tableWidget_2.rowCount():
                self.logger.error(f"无效的行号: {row}")
                return
            # 验证该行是否有有效的股票代码
            stock_code_item = self.tableWidget_2.item(row, 0)
            if not stock_code_item or not stock_code_item.text():
                self.logger.error(f"行{row}没有有效的股票代码")
                return
            # 记录本次启动的行号
            #self.logger.info(f"===== 设置 _last_started_row = {row} =====")
            self._last_started_row = row
            # 调用原有的启动方法
            self.start_task(row)
        except Exception as e:
            self.logger.error(f"通过行号启动任务失败: {str(e)}")

    def stop_task_by_row(self, row):
        """通过行号停止任务（安全版本）"""
        try:
            # 验证行号是否有效
            if row < 0 or row >= self.tableWidget_2.rowCount():
                self.logger.error(f"无效的行号: {row}")
                return
            
            # 验证该行是否有有效的股票代码
            stock_code_item = self.tableWidget_2.item(row, 0)
            if not stock_code_item or not stock_code_item.text():
                self.logger.error(f"行{row}没有有效的股票代码")
                return
            
            # 记录当前行号，用于保持焦点
            self._last_started_row = row
            
            # 保存当前滚动位置
            scrollbar = self.tableWidget_2.verticalScrollBar()
            self._saved_scroll_position = scrollbar.value()
            
            # 调用原有的停止方法
            self.stop_task(row)
            
            # 立即恢复焦点和滚动位置
            self._restore_focus_and_scroll()
            
        except Exception as e:
            self.logger.error(f"通过行号停止任务失败: {str(e)}")

    def _restore_focus_and_scroll(self):
        """恢复焦点和滚动位置"""
        try:
            table = self.tableWidget_2
            if hasattr(self, '_last_started_row') and self._last_started_row is not None:
                if self._last_started_row < table.rowCount():
                    # 选中指定行
                    table.selectRow(self._last_started_row)
                    # 滚动到指定行
                    table.scrollToItem(table.item(self._last_started_row, 0))
            
            # 恢复滚动位置
            if hasattr(self, '_saved_scroll_position'):
                scrollbar = table.verticalScrollBar()
                scrollbar.setValue(self._saved_scroll_position)
        except Exception as e:
            # 如果恢复焦点失败，不影响主要功能
            pass

    def setup_trade_record(self, window):
        """设置交易记录表格"""
        self.trade_record_manager.setup_trade_record_table(self.tableWidget_3)

    def add_trade_record(self, stock_code, trade_info):
        """添加交易记录"""
        try:
            # 验证参数
            if not stock_code or not trade_info:
                self.logger.warning(f"add_trade_record参数无效: stock_code={stock_code}, trade_info={trade_info}")
                return
            
            # 检查表格是否存在
            if not hasattr(self, 'tableWidget_3') or not self.tableWidget_3:
                self.logger.error("订单表格不存在")
                return
            
            # 检查trade_record_manager是否存在
            if not hasattr(self, 'trade_record_manager') or not self.trade_record_manager:
                self.logger.error("trade_record_manager不存在")
                return
            
            # 调用trade_record_manager的方法
            self.trade_record_manager.add_trade_record(
                self.tableWidget_3, 
                stock_code, 
                trade_info, 
                self.qmt_adapter, 
                self
            )
            
        except Exception as e:
            self.logger.error(f"add_trade_record失败: {str(e)}", exc_info=True)

    def sort_order_table(self):
        """对订单表格进行排序：按订单编号倒序"""
        self.trade_record_manager.sort_order_table(self.tableWidget_3)

    def reorder_table_rows(self, table, sorted_rows_data):
        """重新排列表格行"""
        self.trade_record_manager.reorder_table_rows(table, sorted_rows_data)

    def find_existing_row(self, table, stock_code, trade_info, order_time, price):
        """查找已存在的行"""
        return self.trade_record_manager.find_existing_row(table, stock_code, trade_info, order_time, price)

    def create_table_item(self, value, align_center=True):
        """创建表格项"""
        return self.trade_record_manager.create_table_item(value, align_center)

    def update_existing_row(self, table, row, items):
        """更新现有行数据"""
        self.trade_record_manager.update_existing_row(table, row, items)

    def insert_new_row(self, table, items):
        """插入新行数据"""
        self.trade_record_manager.insert_new_row(table, items)

    def save_current_tasks(self):
        """保存当前任务列表状态"""
        self.logger.info("开始保存当前任务...")
        table = self.tableWidget_2
        tasks_data = []
        
        for row in range(table.rowCount()):
            try:
                # 获取股票代码
                stock_code_item = table.item(row, 0)
                if not stock_code_item:
                    continue
                stock_code = stock_code_item.text()
                
                # 获取策略选择下拉框的值
                strategy_container = table.cellWidget(row, 4)
                strategy = strategy_container.combo.currentText() if strategy_container else "规则任务"
                
                # 获取其他单元格的值，添加空值检查
                stock_name_item = table.item(row, 1)
                volume_item = table.item(row, 2)  # 数量列
                init_volume_item = table.item(row, 3)  # 初始持仓列
                status_item = table.item(row, 6)  # 状态列
                
                # 如果任何必要字段为空，跳过该行
                if not all([stock_name_item, volume_item, init_volume_item, status_item]):
                    self.logger.warning(f"第{row}行缺少必要字段，跳过")
                    continue
                
                # 从初始持仓列提取实际数值
                init_volume_text = init_volume_item.text()
                init_volume = 0
                
                try:
                    # 提取数字部分（去掉"股"等后缀）
                    volume_match = re.search(r'(\d+)', init_volume_text)
                    if volume_match:
                        init_volume = int(volume_match.group(1))
                    else:
                        # 如果无法提取数字，尝试直接转换
                        try:
                            init_volume = int(init_volume_text)
                        except:
                            # 如果仍然无法解析，尝试从任务管理器中获取原始数量
                            task_id = self.get_task_id_from_table_row(row)
                            if task_id and task_id in self.task_manager.tasks:
                                original_task = self.task_manager.tasks[task_id]
                                init_volume = original_task.get('init_volume', 0)
                                self.logger.warning(f"第{row}行初始持仓解析失败，使用原始数量: {init_volume}")
                            else:
                                init_volume = 0
                                self.logger.warning(f"第{row}行初始持仓解析失败且无法获取原始数量: {init_volume_text}")
                except Exception as e:
                    # 如果解析过程中出现异常，尝试从任务管理器中获取原始数量
                    task_id = self.get_task_id_from_table_row(row)
                    if task_id and task_id in self.task_manager.tasks:
                        original_task = self.task_manager.tasks[task_id]
                        init_volume = original_task.get('init_volume', 0)
                        self.logger.warning(f"第{row}行初始持仓解析异常，使用原始数量: {init_volume}, 异常: {str(e)}")
                    else:
                        init_volume = 0
                        self.logger.warning(f"第{row}行初始持仓解析异常且无法获取原始数量: {init_volume_text}, 异常: {str(e)}")
                
                # 检查初始股数，记录警告但继续处理
                if init_volume <= 0:
                    self.logger.warning(f"第{row}行初始持仓为0或无效: {init_volume_text}, 解析结果: {init_volume}")
                    # 继续处理，让task_manager来处理过滤逻辑
                
                # 生成任务ID：使用通用函数
                task_id = self.get_task_id_from_table_row(row)
                if not task_id:
                    self.logger.error(f"第{row}行生成任务ID失败，跳过")
                    continue
                
                # 获取现有参数
                params = self.task_manager.get_task_params(task_id)
                
                # 检查是否是夜市任务，如果是则更新params中的数量
                if '夜市' in strategy:
                    # 从UserRole获取任务类型
                    volume_item = table.item(row, 2)
                    task_type = ''
                    if volume_item and volume_item.data(Qt.UserRole):
                        user_data = volume_item.data(Qt.UserRole)
                        if isinstance(user_data, dict):
                            task_type = user_data.get('task_type', '')
                    
                    # 根据任务类型更新params中的数量
                    if task_type == 'sell':
                        if 'params' not in params:
                            params = {}
                        params['sell_volume'] = init_volume
                        params['task_type'] = 'sell'
                    elif task_type == 'buy':
                        if 'params' not in params:
                            params = {}
                        params['buy_volume'] = init_volume
                        params['task_type'] = 'buy'
                
                task = {
                    'task_id': task_id,  # 添加任务ID
                    'stock_code': stock_code,
                    'stock_name': stock_name_item.text(),
                    'init_volume': init_volume,
                    'volume': init_volume,  # 初始时volume等于init_volume
                    'base_price': params.get('base_price', 0.0),
                    'strategy': strategy,
                    'status': status_item.text(),
                    'params': params
                }
                tasks_data.append(task)
                
                # 同时更新task_manager中的任务信息
                self.task_manager.tasks[task_id] = task
                
                self.logger.info(f"处理任务: {task_id}, 初始持仓: {init_volume}, 参数: {params}")
                
            except Exception as e:
                self.logger.error(f"处理第{row}行任务数据时出错: {str(e)}")
                continue
        
        self.logger.info(f"准备保存 {len(tasks_data)} 个任务")
        if tasks_data:
            result = self.task_manager.save_tasks(tasks_data)
            self.logger.info(f"保存任务结果: {result}")
        else:
            self.logger.warning("没有任务需要保存")

    def on_strategy_changed(self, row, text):
        """策略改变时的处理"""
        stock_code = self.tableWidget_2.item(row, 0).text()
        #self.logger.info(f"[{stock_code}] 的策略改为: {text}")
        self.save_current_tasks()

    def on_cell_double_clicked(self, row, col):
        """处理单元格双击事件"""
        #self.logger.info(f"双击事件触发: row={row}, col={col}")
        
        if col == 5:  # 参数列
            # 获取任务ID和策略信息
            task_id = self.get_task_id_from_table_row(row)
            if task_id and task_id in self.task_manager.tasks:
                strategy = self.task_manager.tasks[task_id].get('strategy', '')
                # 检查是否为夜市任务
                if '夜市' in strategy:
                    # 夜市任务不弹出参数设置窗口
                    return
            
            # 双击参数列，打开参数设置对话框
            stock_code = self.tableWidget_2.item(row, 0).text()
            self.show_parameter_dialog_by_row(row, stock_code)
            return
        
        if col == 2:  # 数量列
            #self.logger.info("双击数量列，开始处理数量编辑")
            try:
                # 获取当前数量
                volume_item = self.tableWidget_2.item(row, 2)
                if not volume_item:
                    self.logger.warning("未找到数量项")
                    return
                
                volume_text = volume_item.text()
                #self.logger.info(f"当前数量文本: {volume_text}")
                # 提取数字部分
                volume_match = re.search(r'(\d+)', volume_text)
                if not volume_match:
                    self.logger.warning("无法从数量文本中提取数字")
                    return
                
                current_volume = int(volume_match.group(1))
                #self.logger.info(f"提取的当前数量: {current_volume}")
                
                # 获取任务类型
                task_type = ''
                user_data = volume_item.data(Qt.UserRole)
                #self.logger.info(f"UserRole数据: {user_data}")
                if user_data and isinstance(user_data, dict):
                    task_type = user_data.get('task_type', '')
                    #self.logger.info(f"从UserRole获取的任务类型: {task_type}")
                
                # 如果从UserRole获取不到，尝试从任务数据中获取
                if not task_type:
                    task_id = self.get_task_id_from_table_row(row)
                    
                    if task_id in self.task_manager.tasks:
                        task_data = self.task_manager.tasks[task_id]
                        task_type = task_data.get('task_type', '')
                        # 如果task_type为空，尝试从params中获取
                        if not task_type and 'params' in task_data:
                            params = task_data.get('params', {})
                            if isinstance(params, dict):
                                task_type = params.get('task_type', '')
                
                #self.logger.info(f"最终确定的任务类型: {task_type}")
                
                # 显示数量编辑对话框
                dialog = VolumeEditDialog(self.window, current_volume, task_type)
                if dialog.exec_() == QDialog.Accepted:
                    new_volume = dialog.get_volume()
                    #self.logger.info(f"用户输入的新数量: {new_volume}")
                    
                    # 根据任务类型设置显示文本
                    if task_type == 'sell':
                        new_text = f"{new_volume}股(拟卖出)"
                    elif task_type == 'buy':
                        new_text = f"{new_volume}股(拟买入)"
                    else:
                        new_text = f"{new_volume}股(初始持仓)"
                    
                    # 更新显示
                    volume_item.setText(new_text)
                    
                    # 获取任务ID - 优先从表格中获取
                    task_id = self.get_task_id_from_table_row(row)
                    if not task_id:
                        self.logger.error("无法获取任务ID，无法更新任务数据")
                        return
                    
                    #self.logger.info(f"获取到任务ID: {task_id}")
                    
                    # 更新任务数据
                    if task_id in self.task_manager.tasks:
                        self.task_manager.tasks[task_id]['init_volume'] = new_volume
                        self.task_manager.tasks[task_id]['volume'] = new_volume
                        
                        # 如果是夜市任务，还需要更新params中的数量
                        if task_type == 'sell':
                            if 'params' not in self.task_manager.tasks[task_id]:
                                self.task_manager.tasks[task_id]['params'] = {}
                            self.task_manager.tasks[task_id]['params']['sell_volume'] = new_volume
                        elif task_type == 'buy':
                            if 'params' not in self.task_manager.tasks[task_id]:
                                self.task_manager.tasks[task_id]['params'] = {}
                            self.task_manager.tasks[task_id]['params']['buy_volume'] = new_volume
                        
                        #self.logger.info(f"已更新任务 {task_id} 的数量为: {new_volume}")
                    else:
                        self.logger.error(f"任务ID {task_id} 不存在于任务管理器中")
                        return
                    
                    # 保存到文件 - 使用正确的方法调用
                    try:
                        # 获取所有任务数据
                        all_tasks = list(self.task_manager.tasks.values())
                        result = self.task_manager.save_tasks(all_tasks)
                        if result:
                            #self.logger.info("数量编辑完成并保存到文件")
                            pass
                        else:
                            self.logger.error("保存任务失败")
                    except Exception as e:
                        self.logger.error(f"保存任务失败: {str(e)}")
                        # 如果直接保存失败，回退到原来的方法
                        self.save_current_tasks()
                    
            except Exception as e:
                self.logger.error(f"处理数量编辑失败: {str(e)}")
        
        elif col == 3:  # 初始持仓列
            #self.logger.info("双击初始持仓列，开始处理初始持仓编辑")
            try:
                # 获取当前初始持仓
                init_volume_item = self.tableWidget_2.item(row, 3)
                if not init_volume_item:
                    self.logger.warning("未找到初始持仓项")
                    return
                
                init_volume_text = init_volume_item.text()
                #self.logger.info(f"当前初始持仓文本: {init_volume_text}")
                # 提取数字部分
                volume_match = re.search(r'(\d+)', init_volume_text)
                if not volume_match:
                    self.logger.warning("无法从初始持仓文本中提取数字")
                    return
                
                current_init_volume = int(volume_match.group(1))
                #self.logger.info(f"提取的当前初始持仓: {current_init_volume}")
                
                # 获取任务类型
                task_type = ''
                user_data = init_volume_item.data(Qt.UserRole)
                #self.logger.info(f"UserRole数据: {user_data}")
                if user_data and isinstance(user_data, dict):
                    task_type = user_data.get('task_type', '')
                    #self.logger.info(f"从UserRole获取的任务类型: {task_type}")
                
                # 如果从UserRole获取不到，尝试从任务数据中获取
                if not task_type:
                    task_id = self.get_task_id_from_table_row(row)
                    
                    if task_id in self.task_manager.tasks:
                        task_data = self.task_manager.tasks[task_id]
                        task_type = task_data.get('task_type', '')
                        # 如果task_type为空，尝试从params中获取
                        if not task_type and 'params' in task_data:
                            params = task_data.get('params', {})
                            if isinstance(params, dict):
                                task_type = params.get('task_type', '')
                
                #self.logger.info(f"最终确定的任务类型: {task_type}")
                
                # 显示初始持仓编辑对话框
                dialog = VolumeEditDialog(self.window, current_init_volume, task_type)
                if dialog.exec_() == QDialog.Accepted:
                    new_init_volume = dialog.get_volume()
                    #self.logger.info(f"用户输入的新初始持仓: {new_init_volume}")
                    
                    # 更新初始持仓显示
                    new_text = f"{new_init_volume}股"
                    init_volume_item.setText(new_text)
                    
                    # 获取任务ID - 优先从表格中获取
                    task_id = self.get_task_id_from_table_row(row)
                    if not task_id:
                        self.logger.error("无法获取任务ID，无法更新任务数据")
                        return
                    
                    #self.logger.info(f"获取到任务ID: {task_id}")
                    
                    # 更新任务数据
                    if task_id in self.task_manager.tasks:
                        self.task_manager.tasks[task_id]['init_volume'] = new_init_volume
                        
                        # 如果是夜市任务，还需要更新params中的数量
                        if task_type == 'sell':
                            if 'params' not in self.task_manager.tasks[task_id]:
                                self.task_manager.tasks[task_id]['params'] = {}
                            self.task_manager.tasks[task_id]['params']['sell_volume'] = new_init_volume
                        elif task_type == 'buy':
                            if 'params' not in self.task_manager.tasks[task_id]:
                                self.task_manager.tasks[task_id]['params'] = {}
                            self.task_manager.tasks[task_id]['params']['buy_volume'] = new_init_volume
                        
                        #self.logger.info(f"已更新任务 {task_id} 的初始持仓为: {new_init_volume}")
                    else:
                        self.logger.error(f"任务ID {task_id} 不存在于任务管理器中")
                        return
                    
                    # 保存到文件 - 使用正确的方法调用
                    try:
                        # 获取所有任务数据
                        all_tasks = list(self.task_manager.tasks.values())
                        result = self.task_manager.save_tasks(all_tasks)
                        if result:
                            #self.logger.info("初始持仓编辑完成并保存到文件")
                            pass
                        else:
                            self.logger.error("保存任务失败")
                    except Exception as e:
                        self.logger.error(f"保存任务失败: {str(e)}")
                        # 如果直接保存失败，回退到原来的方法
                        self.save_current_tasks()
                    
            except Exception as e:
                self.logger.error(f"处理初始持仓编辑失败: {str(e)}")
        #else:
        #    self.logger.info(f"双击的不是数量列或初始持仓列，忽略")

    def on_cell_editing(self, row, col):
        """处理单元格编辑完成事件"""
        #self.logger.info(f"开始处理单元格编辑: row={row}, col={col}")
        
        # 获取单元格项
        item = self.tableWidget_2.item(row, col)
        #self.logger.info(f"单元格项: {item}")
        
        # 获取股票代码
        code_item = self.tableWidget_2.item(row, 0)
        #self.logger.info(f"股票代码项: {code_item}")
        
        if not code_item:
            self.logger.error("未找到股票代码项")
            return
            
        stock_code = code_item.text()
        #self.logger.info(f"股票代码: {stock_code}")
        
        # 获取新值
        new_value = item.text()
        #self.logger.info(f"新值: {new_value}")
        
        # 根据列索引处理不同的编辑
        # 基准价格现在在策略参数设置中管理，不再在表格中直接编辑
        if False:  # 原来的基准价格列处理逻辑已移除
            self.logger.info("处理基准价格编辑")
            try:
                new_base_price = float(new_value)
                # 使用通用函数获取任务ID
                task_id = self.get_task_id_from_table_row(row)
                if not task_id:
                    self.logger.error("生成任务ID失败，无法更新基准价格")
                    return
                # 更新任务中的基准价格
                if task_id in self.task_manager.tasks:
                    self.task_manager.tasks[task_id]['base_price'] = new_base_price
                    self.logger.info(f"更新任务 {task_id} 的基准价格为: {new_base_price}")
                else:
                    self.logger.error(f"任务ID {task_id} 不存在于任务管理器中，无法更新基准价格")
                    return
                
                # 同时调用update_base_price方法更新其他相关数据
                self.task_manager.update_base_price(stock_code, new_base_price, from_ui=True)
                
                # 更新状态栏显示
                self.update_status_bar()
                
                # 直接保存任务数据，避免从UI表格读取数量导致数量变为0
                all_tasks = list(self.task_manager.tasks.values())
                self.task_manager.save_tasks(all_tasks)
                #self.logger.info(f"基准价修改后直接保存任务数据，避免数量被重置")
                
            except ValueError:
                self.logger.error(f"基准价格值无效: {new_value}")
                # 恢复原值
                task_id = self.get_task_id_from_table_row(row)
                if task_id and task_id in self.task_manager.tasks:
                    item.setText(str(self.task_manager.tasks[task_id].get('base_price', 0)))
                else:
                    item.setText("0.000")

    def refresh_param_column(self, row, stock_code, task_id):
        """刷新参数列显示，重新创建参数列控件"""
        try:
            # 获取任务信息
            task = self.task_manager.tasks.get(task_id, {})
            strategy = task.get('strategy', '')
            
            # 重新创建参数列控件
            param_widget = self.create_param_column_widget(row, stock_code, task_id, strategy)
            self.tableWidget_2.setCellWidget(row, 5, param_widget)
            
            self.logger.info(f"[{stock_code}] 参数列已刷新，显示最新参数")
            
        except Exception as e:
            self.logger.error(f"刷新参数列失败: {str(e)}")

    def update_param_button_text(self, row, stock_code):
        """更新参数按钮文本（保留此方法用于向后兼容）"""
        try:
            # 获取任务ID
            task_id = self.get_task_id_from_table_row(row)
            if not task_id:
                return
            
            # 获取任务参数
            task = self.task_manager.tasks.get(task_id, {})
            params = task.get('params', {})
            
            # 根据策略类型显示不同的参数文本
            strategy = task.get('strategy', '')
            if '夜市' in strategy:
                # 夜市任务显示特殊参数
                if '夜市买入' in strategy:
                    buy_volume = params.get('buy_volume', 0)
                    buy_price = params.get('buy_price', 0)
                    text = f"买入{buy_volume}股 {buy_price}元"
                elif '夜市卖出' in strategy:
                    sell_volume = params.get('sell_volume', 0)
                    sell_price = params.get('sell_price', 0)
                    text = f"卖出{sell_volume}股 {sell_price}元"
                else:
                    text = "夜市任务"
            else:
                # 默认策略参数
                cycle_times = params.get('cycle_times', 0)
                clear_time = params.get('clear_time', '00:00:00')
                up_threshold = params.get('up_threshold', 5.0)
                down_threshold = params.get('down_threshold', 3.0)
                up_operation = params.get('up_operation', '卖出')
                down_operation = params.get('down_operation', '买入')
                
                # 使用每笔操作股数显示
                trade_volume = params.get('trade_volume', 1000)
                # 根据清仓时间判断是否启用清仓
                if clear_time == '00:00:00':
                    # 不清仓
                    if cycle_times > 0:
                        text = f"每笔{trade_volume}股 循环{cycle_times}次 不清仓\n↑{up_threshold}%({up_operation}) ↓{down_threshold}%({down_operation})"
                    else:
                        text = f"每笔{trade_volume}股 不清仓\n↑{up_threshold}%({up_operation}) ↓{down_threshold}%({down_operation})"
                else:
                    # 启用清仓
                    if cycle_times > 0:
                        text = f"每笔{trade_volume}股 循环{cycle_times}次 清仓{clear_time}\n↑{up_threshold}%({up_operation}) ↓{down_threshold}%({down_operation})"
                    else:
                        text = f"每笔{trade_volume}股 清仓{clear_time}\n↑{up_threshold}%({up_operation}) ↓{down_threshold}%({down_operation})"
            
            button = self.tableWidget_2.cellWidget(row, 5)
            if isinstance(button, QPushButton):
                button.setText(text)
                button.setStyleSheet("""
                    QPushButton { 
                        color: blue; 
                        text-decoration: underline; 
                        text-align: center;
                        padding: 5px;
                        height: 70px;
                    }
                """)
                
        except Exception as e:
            self.logger.error(f"更新参数按钮文本失败: {str(e)}")
            # 设置默认文本
            button = self.tableWidget_2.cellWidget(row, 5)
            if isinstance(button, QPushButton):
                button.setText("设置参数")
                button.setStyleSheet("""
                    QPushButton { 
                        color: blue; 
                        text-decoration: underline; 
                        text-align: center;
                        padding: 5px;
                        height: 70px;
                    }
                """)


    def _refresh_layout_basis_label(self):
        """状态栏：复盘 / 次日准备布局基准提示（permanent，不被 showMessage 盖住）。"""
        label = getattr(self, "layout_basis_label", None)
        if label is None:
            return
        try:
            from utils.trading_day import get_layout_basis_status

            phase, text, tip = get_layout_basis_status()
        except Exception:
            return
        if phase == getattr(self, "_layout_basis_phase", None) and label.text() == (text or ""):
            return
        self._layout_basis_phase = phase
        label.setToolTip(tip or "")
        if phase == "review":
            label.setText(text)
            label.setStyleSheet(
                "font-family: 'Microsoft YaHei'; font-size: 12pt; font-weight: bold; "
                "padding: 2px 8px; color: #8B4513; background-color: #FFE0B2; border-radius: 3px;"
            )
            label.setVisible(True)
        elif phase == "next_day":
            label.setText(text)
            label.setStyleSheet(
                "font-family: 'Microsoft YaHei'; font-size: 12pt; font-weight: bold; "
                "padding: 2px 8px; color: #0D47A1; background-color: #BBDEFB; border-radius: 3px;"
            )
            label.setVisible(True)
        else:
            # 盘中不抢戏：清空
            label.setText("")
            label.setStyleSheet(
                "font-family: 'Microsoft YaHei'; font-size: 12pt; padding: 2px 8px; color: #888;"
            )
            label.setVisible(False)

    def update_status_bar(self):
        """更新状态栏显示"""
        import time as _time
        _t0 = _time.time()
        def _perf_log():
            if _time.time() - _t0 > 0.05:
                self.logger.warning(f"[性能监控] update_status_bar 耗时: {_time.time() - _t0:.3f}秒")
        try:
            # 检查是否正在关闭程序
            if hasattr(self, '_is_closing') and self._is_closing:
                _perf_log()
                return
            
            # 检查状态栏对象是否还存在
            if not hasattr(self, 'statusBar') or self.statusBar is None:
                _perf_log()
                return
            
            # 检查状态栏是否已被删除
            try:
                # 尝试访问状态栏对象，如果已被删除会抛出异常
                self.statusBar.objectName()
            except RuntimeError:
                # 状态栏已被删除，停止定时器并返回
                if hasattr(self, 'status_timer') and self.status_timer:
                    try:
                        self.status_timer.stop()
                    except RuntimeError:
                        pass  # 定时器可能已被删除
                _perf_log()
                return
            
            # 检查窗口是否正在关闭
            if hasattr(self, '_is_closing') and self._is_closing:
                return

            try:
                self._refresh_layout_basis_label()
            except Exception:
                pass
                
            if not hasattr(self, 'task_manager') or not self.task_manager:
                _perf_log()
                return
            
            # 自然日切换（彻夜不关程序）：任务表文件名按 current_tasks_YYYY-MM-DD.xlsx 走日历日，
            # 与 QMT「新交易日」逻辑解耦（后者在有运行任务/无内存任务时可能不更新路径）。
            from datetime import date, datetime, time as datetime_time
            import time as time_module

            today_d = date.today()
            guard_d = getattr(self, "_task_file_calendar_guard", None)
            if guard_d != today_d:
                tm = self.task_manager
                path_changed = False
                if tm and hasattr(tm, "update_tasks_file_path"):
                    try:
                        path_changed = bool(tm.update_tasks_file_path())
                    except Exception as e:
                        self.logger.warning(f"[任务文件] 跨日对齐路径失败: {e}")
                if path_changed:
                    try:
                        tm._tasks_loaded = False
                        tm.load_tasks(force_reload=True)
                        self.logger.info(
                            f"[任务文件] 自然日已切换为 {today_d.isoformat()}，已从当日任务表自动重载"
                        )
                    except Exception as e:
                        self.logger.error(f"[任务文件] 跨日自动 load_tasks 失败: {e}", exc_info=True)
                    try:
                        self.refresh_task_table()
                    except Exception:
                        pass
                    tcv = getattr(self, "tasks_charts_view", None)
                    if tcv:
                        try:
                            tcv.load_tasks()
                        except Exception:
                            pass
                self._task_file_calendar_guard = today_d

            # 获取当前时间和交易时间状态
            now = datetime.now()
            current_time = now.time()
            is_trading_time = self._is_trading_time(current_time)

            # 大 QMT 健康告警（builtin 行情/账户滞后）
            health_alert = ""
            try:
                health_alert = str(
                    getattr(self, "builtin_health_alert", "")
                    or getattr(getattr(self, "qmt_adapter", None), "builtin_health_alert", "")
                    or getattr(getattr(self, "task_manager", None), "builtin_health_alert", "")
                    or ""
                ).strip()
            except Exception:
                health_alert = ""

            # 获取时间显示文本
            if is_trading_time:
                # 交易时间内，尝试获取最新tick时间
                try:
                    if self.task_manager.qmt_adapter:
                        tick_time_str = self.task_manager.qmt_adapter.get_latest_tick_time_str() or "等待数据"
                        if tick_time_str != "等待数据":
                            time_str = f"实时 {tick_time_str}"
                        else:
                            time_str = f"交易中[{datetime.now().strftime('%H:%M:%S')}]"
                    else:
                        time_str = f"交易中[{datetime.now().strftime('%H:%M:%S')}]"
                except Exception as e:
                    time_str = f"交易中[{datetime.now().strftime('%H:%M:%S')}]"
            else:
                # 非交易时间，显示当前系统时间
                if current_time < datetime_time(9, 30):
                    time_str = f"开盘前[{datetime.now().strftime('%H:%M:%S')}]"
                elif current_time > datetime_time(15, 0):
                    time_str = f"收盘后[{datetime.now().strftime('%H:%M:%S')}]"
                else:
                    time_str = f"休市中[{datetime.now().strftime('%H:%M:%S')}]"

            if health_alert:
                time_str = f"{health_alert} | {time_str}"
            
            # 获取价格显示信息
            price_displays = getattr(self.task_manager, 'price_displays', {})
            #self.logger.debug(f"[状态栏] 更新状态栏，当前price_displays: {price_displays}")
            
            if not price_displays:
                # 只显示时间和中性提示：
                # 实时行情时间由网关 get_latest_tick_time_str 提供，
                # 与是否有运行任务无关。这里不要误导成“等待行情数据（断连）”。
                try:
                    msg = f"{time_str} | 暂无任务行情显示"
                    self.statusBar.showMessage(msg)
                    self.statusBar.setToolTip(health_alert or "")
                except RuntimeError:
                    # 状态栏已被删除，停止定时器
                    if hasattr(self, 'status_timer') and self.status_timer:
                        try:
                            self.status_timer.stop()
                        except RuntimeError:
                            pass
                    _perf_log()
                    return
                #self.logger.info(f"[状态栏] price_displays为空，只显示时间")
                _perf_log()
                return
            
            # 构建状态栏文本，显示所有股票
            # 过滤掉不在任务列表和持仓中的股票
            status_parts = []
            
            # 获取持仓列表
            positions = {}
            if hasattr(self, 'qmt_adapter') and self.qmt_adapter:
                positions = getattr(self.qmt_adapter, 'cached_positions', {})
            
            # 获取任务列表中的股票代码
            task_stock_codes = set()
            if hasattr(self, 'task_manager') and self.task_manager:
                for task in self.task_manager.tasks.values():
                    task_stock_codes.add(task.get('stock_code'))
            
            # 先收集需要删除的股票代码（避免在迭代时修改字典）
            stocks_to_remove = []
            
            for stock_code, display_data in price_displays.items():
                # 检查股票是否还在任务列表或持仓中
                if stock_code not in task_stock_codes and stock_code not in positions:
                    # 股票不在任务列表和持仓中，标记为需要删除
                    stocks_to_remove.append(stock_code)
                    continue
                
                try:
                    # 获取股票名称用于显示
                    stock_name = self._get_stock_name_for_display(stock_code)
                    
                    # 处理新的列表格式或旧的字符串格式
                    if isinstance(display_data, list):
                        # 新格式：任务列表 [(task_id, display_text), ...]
                        if display_data:
                            # 检查列表元素是否是元组格式
                            if isinstance(display_data[0], tuple) and len(display_data[0]) == 2:
                                # 提取价格（从第一个任务中获取）
                                first_task_id, first_display_text = display_data[0]
                            else:
                                # 旧格式或格式不正确，直接使用第一个元素
                                first_display_text = str(display_data[0])
                            # 从显示文本中提取价格部分（格式：价格 [上限/下限]）
                            if '[' in first_display_text and ']' in first_display_text:
                                price_part = first_display_text.split('[')[0].strip()
                            else:
                                price_part = first_display_text
                            
                            # 提取所有任务的阈值信息
                            threshold_parts = []
                            for item in display_data:
                                # 检查是否是元组格式
                                if isinstance(item, tuple) and len(item) == 2:
                                    task_id, display_text = item
                                else:
                                    display_text = str(item)
                                
                                if '[' in display_text and ']' in display_text:
                                    threshold_part = display_text.split('[')[1].split(']')[0]
                                    threshold_parts.append(threshold_part)
                            
                            # 组合显示：价格 [阈值1] | [阈值2] | ...
                            if threshold_parts:
                                combined_thresholds = " | ".join(threshold_parts)
                                combined_display = f"{price_part} [{combined_thresholds}]"
                            else:
                                combined_display = price_part
                            
                            status_parts.append(f"{stock_name} {combined_display}")
                        else:
                            # 空列表，不显示该股票（因为所有任务都已停止）
                            continue
                    else:
                        # 旧格式：直接字符串
                        status_parts.append(f"{stock_name} {display_data}")
                except Exception as e:
                    # 单个股票处理失败，继续处理下一个
                    self.logger.warning(f"处理股票{stock_code}显示失败: {str(e)}")
                    continue
            
            # 清理不在任务列表和持仓中的股票显示信息（在遍历完成后删除，避免字典在迭代时被修改）
            if stocks_to_remove and hasattr(self, 'task_manager') and self.task_manager:
                for stock_code in stocks_to_remove:
                    if stock_code in self.task_manager.price_displays:
                        del self.task_manager.price_displays[stock_code]
            
            # 构建最终状态栏文本，移除连接状态
            status_text = f"{time_str} | {' | '.join(status_parts)}"
            
            # 如果内容过长，截断显示
            if len(status_text) > 200:  # 限制长度到200字符
                status_text = status_text[:197] + "..."
            
            #self.logger.debug(f"[状态栏] 状态栏文本: {status_text}")
            
            # 更新状态栏
            try:
                self.statusBar.showMessage(status_text)
                # 设置工具提示显示完整内容
                full_status_text = f"{time_str} | {' | '.join(status_parts)}"
                if len(full_status_text) > len(status_text):
                    self.statusBar.setToolTip(full_status_text)
                else:
                    self.statusBar.setToolTip("")
            except RuntimeError:
                # 状态栏已被删除，停止定时器
                if hasattr(self, 'status_timer') and self.status_timer:
                    try:
                        self.status_timer.stop()
                    except RuntimeError:
                        pass
                _perf_log()
                return
            _perf_log()
            
        except Exception as e:
            # 检查是否是因为程序正在关闭导致的错误
            if hasattr(self, '_is_closing') and self._is_closing:
                return
            _perf_log()
            self.logger.error(f"[状态栏] 更新状态栏失败: {str(e)}")
            import traceback
            self.logger.error(f"[状态栏] 详细错误: {traceback.format_exc()}")
            # 发生异常时显示简单状态
            try:
                if hasattr(self, 'statusBar') and self.statusBar:
                    self.statusBar.showMessage("状态栏更新异常")
            except RuntimeError:
                # 状态栏已被删除，停止定时器
                if hasattr(self, 'status_timer') and self.status_timer:
                    try:
                        self.status_timer.stop()
                    except RuntimeError:
                        pass

    def _get_stock_name_for_display(self, stock_code):
        """获取股票名称用于状态栏显示，优先级：任务信息 > QMT股票列表 > 外部股票信息 > 股票代码"""
        try:
            # 获取6位股票代码（去掉市场后缀）
            # 确保stock_code是字符串类型
            stock_code = str(stock_code) if stock_code is not None else ''
            clean_code = stock_code.split('.')[0] if '.' in stock_code else stock_code
            
            # 1. 优先从任务信息中获取股票名称
            if hasattr(self, 'task_manager') and self.task_manager:
                task_info = self.task_manager.tasks.get(stock_code, {})
                # 检查task_info是否为字典类型
                if isinstance(task_info, dict) and task_info.get('stock_name'):
                    stock_name = task_info.get('stock_name')
                    if stock_name and stock_name not in ('未知名称', '未知'):
                        return stock_name
                
                # 也尝试用不带后缀的代码查找
                task_info = self.task_manager.tasks.get(clean_code, {})
                # 检查task_info是否为字典类型
                if isinstance(task_info, dict) and task_info.get('stock_name'):
                    stock_name = task_info.get('stock_name')
                    if stock_name and stock_name not in ('未知名称', '未知'):
                        return stock_name
            
            # 2. 使用全局股票信息管理器
            try:
                from utils.stock_info_manager import get_stock_name
                stock_name = get_stock_name(clean_code)
                if stock_name and stock_name not in ('未知名称', '未知'):
                    return stock_name
            except Exception as e:
                self.logger.debug(f"从全局股票信息管理器获取名称失败: {str(e)}")
            
            # 4. 如果都获取不到，显示格式化的股票代码
            # 尝试添加简单的市场标识
            if clean_code.startswith('6'):
                return f"{clean_code}(SH)"
            elif clean_code.startswith(('0', '3')):
                return f"{clean_code}(SZ)"
            elif clean_code.startswith(('4', '8', '920')):
                return f"{clean_code}(BJ)"
            else:
                return clean_code
                
        except Exception as e:
            self.logger.warning(f"获取股票{stock_code}名称失败: {str(e)}")
            # 返回最基本的代码显示
            # 确保stock_code是字符串类型
            stock_code = str(stock_code) if stock_code is not None else ''
            clean_code = stock_code.split('.')[0] if '.' in stock_code else stock_code
            return clean_code

    def set_qmt_adapter(self, qmt_adapter):
        """设置QMT适配器"""
        self.qmt_adapter = qmt_adapter
        
        # 初始化持仓管理器
        self.position_manager = PositionManager(qmt_adapter, self)
        
        # 注意：不在这里初始化图表视图，等待交易连接建立后再初始化
        # 图表视图将在交易连接成功后（通过信号或回调）加载数据
        
        # 重新设置持仓表格（包括右键菜单）
        self.position_manager.setup_position_table(self.tableWidget)
        
        # 设置主窗口引用到QMT适配器，以便在行情回调中访问order_monitors
        qmt_adapter.main_window = self
        
        if self.task_manager is not None:
            self.task_manager.set_qmt_adapter(qmt_adapter)
        
        # 设置trade record manager的qmt_adapter
        self.trade_record_manager.set_qmt_adapter(qmt_adapter)
        
        # 连接撤单失败信号
        if hasattr(qmt_adapter, 'cancel_error_signal'):
            qmt_adapter.cancel_error_signal.connect(self.on_cancel_error)

    def on_cancel_error(self, order_id, error_msg):
        """处理撤单失败回调"""
        try:
            self.logger.info(f"收到撤单失败回调：订单号={order_id}, 错误={error_msg}")

            # 提前下单：撤单失败通常表示已成交，结束对应买入/卖出任务
            if hasattr(self, 'tasks_charts_view') and self.tasks_charts_view:
                if self.tasks_charts_view.handle_early_order_cancel_error(order_id, error_msg):
                    self.logger.info("提前下单任务已因撤单失败而结束")
            
            # 查找对应的订单行
            table = self.tableWidget_3
            target_row = -1
            
            for row in range(table.rowCount()):
                order_id_item = table.item(row, 0)
                if order_id_item and order_id_item.text() == order_id:
                    target_row = row
                    break
            
            if target_row >= 0:
                # 获取股票代码
                stock_code_item = table.item(target_row, 1)
                stock_code = stock_code_item.text() if stock_code_item else "未知"
                
                # 恢复撤单按钮状态 - 修复列索引：从第12列和第13列改为第8列
                operation_container = table.cellWidget(target_row, 8)  # 操作容器在第9列
                
                if operation_container:
                    # 从容器中获取按钮
                    cancel_button = operation_container.findChild(QPushButton, "cancel_button")
                    monitor_button = operation_container.findChild(QPushButton, "monitor_button")
                    
                    # 如果没有找到按钮，尝试从布局中获取
                    if not cancel_button or not monitor_button:
                        layout = operation_container.layout()
                        if layout:
                            cancel_button = layout.itemAt(0).widget() if layout.count() > 0 else None
                            monitor_button = layout.itemAt(1).widget() if layout.count() > 1 else None
                    
                    if cancel_button:
                        cancel_button.setEnabled(True)
                        cancel_button.setText("撤单")
                        cancel_button.setStyleSheet("""
                            QPushButton {
                                background-color: #FF6B6B;
                                color: white;
                                border: 1px solid #FF6B6B;
                                border-radius: 3px;
                            }
                            QPushButton:hover {
                                background-color: #FF5252;
                            }
                            QPushButton:pressed {
                                background-color: #E53935;
                            }
                        """)
                    
                    if monitor_button:
                        monitor_button.setEnabled(True)
                        monitor_button.setText("监控")
                        self._apply_order_op_btn_style(monitor_button, "monitor")
                
                # 解析错误信息并显示用户友好的提示
                if "[250001]" in error_msg and "订单表记录不存在" in error_msg:
                    user_msg = "撤单失败：订单不存在（可能已成交或已撤销）"
                elif "[120147]" in error_msg and "当前时间不允许委托" in error_msg:
                    user_msg = "撤单失败：当前时间不允许委托操作"
                else:
                    user_msg = f"撤单失败：{error_msg}"
                
                # 显示撤单失败消息
                QMessageBox.warning(
                    self.window, 
                    "撤单失败", 
                    f"订单号: {order_id}\n股票代码: {stock_code}\n\n{user_msg}"
                )
                
            else:
                self.logger.warning(f"未找到撤单失败的订单行: {order_id}")
                # 即使找不到行，也显示撤单失败消息
                if "[250001]" in error_msg and "订单表记录不存在" in error_msg:
                    user_msg = "撤单失败：订单不存在（可能已成交或已撤销）"
                elif "[120147]" in error_msg and "当前时间不允许委托" in error_msg:
                    user_msg = "撤单失败：当前时间不允许委托操作"
                else:
                    user_msg = f"撤单失败：{error_msg}"
                
                QMessageBox.warning(
                    self.window, 
                    "撤单失败", 
                    f"订单号: {order_id}\n\n{user_msg}"
                )
                
        except Exception as e:
            self.logger.error(f"处理撤单失败回调时出错: {str(e)}")

    def update_task_field(self, identifier, field_name, value):
        """更新任务字段
        Args:
            identifier: 股票代码或任务ID
            field_name: 字段名
            value: 新值
        """
        # 查找对应的表格行
        table = self.tableWidget_2
        row = -1
        
        # 检查identifier是否是任务ID格式（UUID格式包含连字符，旧格式包含下划线）
        is_task_id = ('-' in identifier and len(identifier) > 30) or ('_' in identifier and len(identifier) > 10)
        
        for r in range(table.rowCount()):
            stock_code_item = table.item(r, 0)
            if not stock_code_item:
                continue
                
            if is_task_id:
                # 如果是任务ID，通过UserRole数据精确匹配
                stored_task_id = stock_code_item.data(Qt.UserRole)
                if stored_task_id == identifier:
                    row = r
                    break
            else:
                # 如果是股票代码，通过文本匹配（向后兼容）
                if stock_code_item.text() == identifier:
                    row = r
                    break
            
        if row >= 0:
            # 更新表格中的值
            if field_name == 'status':
                status_item = QTableWidgetItem(value)
                status_item.setTextAlignment(Qt.AlignCenter)
                self.tableWidget_2.setItem(row, 6, status_item)
                # 根据状态设置颜色
                if value == '运行中':
                    status_item.setForeground(Qt.red)
                elif value == '连接断开':
                    status_item.setForeground(Qt.red)
                elif value == '未运行':
                    status_item.setForeground(Qt.black)
                    # 对于未运行状态，更新按钮状态
                    self.update_task_buttons(row)
                elif value == '已委托':
                    status_item.setForeground(Qt.blue)  # 蓝色显示已委托状态
                    # 对于已委托状态，调用完整的UI样式设置，确保按钮布局正确
                    self._set_delegated_task_ui_style(row)
                elif value == '已完成':
                    status_item.setForeground(Qt.green)  # 绿色显示已完成状态
                    # 对于已完成状态，更新按钮状态
                    self.update_task_buttons(row)
                else:
                    # 其他状态只更新按钮状态
                    self.update_task_buttons(row)
                
                # 获取股票代码用于日志
                stock_code_item = table.item(row, 0)
                if stock_code_item:
                    stock_code = stock_code_item.text()
                    task_id = stock_code_item.data(Qt.UserRole) if stock_code_item else "未知"
                    self.logger.info(f"[{stock_code}] UI状态已更新为: {value} (任务ID: {task_id})")
                else:
                    self.logger.warning(f"第{row}行股票代码项为空，无法记录状态更新日志")
                
                # 如果状态更新为"未运行"，可能是暂停操作，恢复焦点
                if field_name == 'status' and value == '未运行':
                    # 延迟恢复焦点，确保UI更新完成
                    from PyQt5.QtCore import QTimer
                    QTimer.singleShot(100, self._restore_focus_and_scroll)
            elif field_name == 'volume':
                self.tableWidget_2.setItem(row, 2, self.create_table_item(str(value)))
            elif field_name == 'base_price':
                # 处理基准价格更新，支持颜色显示
                self.logger.info(f"收到基准价格更新: identifier={identifier}, value={value}, row={row}")
                
                # 更新参数列中的基准价显示
                self.update_param_column_base_price(row, value)
            elif field_name == 'current_price':
                # 这里可能需要根据实际列位置调整
                pass
            elif field_name == 'profit':
                # 这里可能需要根据实际列位置调整
                pass
            elif field_name == 'hold_days':
                self.tableWidget_2.setItem(row, 5, self.create_table_item(str(value)))
        else:
            # 找不到对应的表格行，说明这是已清仓股票的历史任务记录
            identifier_type = "任务ID" if is_task_id else "股票代码"
            # 新增任务在UI表格刷新前，常会先收到一轮「待审核」状态更新；
            # 这属于时序正常，不应按告警输出，避免刷屏干扰。
            if is_task_id and field_name == 'status' and str(value) == '待审核':
                task = self.task_manager.tasks.get(identifier, {})
                stock_code = task.get('stock_code', '未知') if isinstance(task, dict) else '未知'
                self.logger.debug(f"[{stock_code}] 新任务尚未入表，忽略状态更新: {identifier} -> {value}")
            else:
                self.logger.warning(
                    f"找不到对应的表格行: {identifier_type}={identifier}, field_name={field_name}, value={value}"
                )
            
            # 添加调试信息：显示表格中实际存储的任务ID
            #self.logger.info("表格中实际存储的任务ID列表：")
            #table = self.tableWidget_2
            #for r in range(table.rowCount()):
            #    stock_code_item = table.item(r, 0)
            #    if stock_code_item:
            #        stored_task_id = stock_code_item.data(Qt.UserRole)
            #        stock_code = stock_code_item.text()
            #        self.logger.info(f"  行{r}: 股票代码={stock_code}, 任务ID={stored_task_id}")
            #    else:
            #        self.logger.info(f"  行{r}: 无股票代码项")
            
            # 自动清理无效的任务记录 - 只清理特定的任务，不影响其他运行中的任务
            if is_task_id and identifier in self.task_manager.tasks:
                stock_code = self.task_manager.tasks[identifier].get('stock_code', '未知')
                task_status = self.task_manager.tasks[identifier].get('status', '')
                
                # 只清理已完成或已委托的任务，不清理正在运行的任务
                if task_status in ['已完成', '已委托', '未运行']:
                    self.logger.info(f"[{stock_code}] 检测到已完成任务记录 {identifier}，自动清理")
                    
                    # 从任务管理器中删除无效任务
                    del self.task_manager.tasks[identifier]
                    
                    # 同时删除任务参数
                    if identifier in self.task_manager.task_params:
                        del self.task_manager.task_params[identifier]
                    
                    # 如果任务正在运行，停止它（这种情况应该很少见）
                    if identifier in self.task_manager.running_tasks:
                        try:
                            task_info = self.task_manager.running_tasks[identifier]
                            if 'process' in task_info and task_info['process'].is_alive():
                                task_info['process'].terminate()
                            if 'control_pipe' in task_info:
                                task_info['control_pipe'].close()
                            if 'log_pipe' in task_info:
                                task_info['log_pipe'].close()
                            del self.task_manager.running_tasks[identifier]
                            
                            # 清理task_processes中的对应条目
                            if stock_code and stock_code in self.task_manager.task_processes:
                                del self.task_manager.task_processes[stock_code]
                                self.logger.info(f"[{stock_code}] 历史任务已从task_processes中移除，停止接收行情数据")
                            
                            # 清理price_displays中的阈值信息
                            if stock_code and stock_code in self.task_manager.price_displays:
                                current_display = self.task_manager.price_displays[stock_code]
                                if '[' in current_display and ']' in current_display:
                                    price_part = current_display.split('[')[0].strip()
                                    self.task_manager.price_displays[stock_code] = price_part
                                    self.logger.info(f"[{stock_code}] 历史任务清理，保留价格显示: {price_part}")
                            
                            self.logger.info(f"[{stock_code}] 已停止并清理运行中的历史任务")
                        except Exception as e:
                            self.logger.error(f"清理运行中历史任务时出错: {str(e)}")
                    
                    # 保存更新后的任务列表
                    try:
                        self.task_manager.save_tasks(list(self.task_manager.tasks.values()))
                        self.logger.info(f"[{stock_code}] 历史任务记录已清理并保存")
                    except Exception as e:
                        self.logger.error(f"保存清理后的任务列表时出错: {str(e)}")
                else:
                    # 任务状态不是已完成或已委托，可能是正在运行，不进行清理
                    # 「待审核」是新生成任务常见中间态，避免重复调试日志刷屏。
                    if task_status != '待审核':
                        self.logger.debug(f"[{stock_code}] 任务 {identifier} 状态为 {task_status}，跳过清理")
            else:
                # 对于股票代码匹配的情况，也进行类似的清理
                if not is_task_id:
                    # 查找所有与该股票代码相关的已完成任务并清理
                    tasks_to_remove = []
                    for task_id, task in self.task_manager.tasks.items():
                        if task.get('stock_code') == identifier:
                            task_status = task.get('status', '')
                            # 只清理已完成或已委托的任务，不清理正在运行的任务
                            if task_status in ['已完成', '已委托', '未运行']:
                                tasks_to_remove.append(task_id)
                    
                    if tasks_to_remove:
                        self.logger.info(f"[{identifier}] 检测到已完成任务记录，自动清理 {len(tasks_to_remove)} 个任务")
                        
                        for task_id in tasks_to_remove:
                            # 删除任务
                            if task_id in self.task_manager.tasks:
                                del self.task_manager.tasks[task_id]
                            
                            # 删除任务参数
                            if task_id in self.task_manager.task_params:
                                del self.task_manager.task_params[task_id]
                            
                            # 停止运行中的任务（这种情况应该很少见）
                            if task_id in self.task_manager.running_tasks:
                                try:
                                    task_info = self.task_manager.running_tasks[task_id]
                                    if 'process' in task_info and task_info['process'].is_alive():
                                        task_info['process'].terminate()
                                    if 'control_pipe' in task_info:
                                        task_info['control_pipe'].close()
                                    if 'log_pipe' in task_info:
                                        task_info['log_pipe'].close()
                                    del self.task_manager.running_tasks[task_id]
                                    
                                    # 清理task_processes中的对应条目
                                    if stock_code and stock_code in self.task_manager.task_processes:
                                        del self.task_manager.task_processes[stock_code]
                                        self.logger.info(f"[{stock_code}] 历史任务已从task_processes中移除，停止接收行情数据")
                                    
                                    # 清理price_displays中的阈值信息
                                    if stock_code and stock_code in self.task_manager.price_displays:
                                        current_display = self.task_manager.price_displays[stock_code]
                                        if '[' in current_display and ']' in current_display:
                                            price_part = current_display.split('[')[0].strip()
                                            self.task_manager.price_displays[stock_code] = price_part
                                            self.logger.info(f"[{stock_code}] 历史任务清理，保留价格显示: {price_part}")
                                except Exception as e:
                                    self.logger.error(f"清理运行中历史任务时出错: {str(e)}")
                        
                        # 保存更新后的任务列表
                        try:
                            self.task_manager.save_tasks(list(self.task_manager.tasks.values()))
                            self.logger.info(f"[{identifier}] 历史任务记录已清理并保存")
                        except Exception as e:
                            self.logger.error(f"保存清理后的任务列表时出错: {str(e)}")
                    #else:
                    #    self.logger.debug(f"[{identifier}] 未找到需要清理的已完成任务")

    def has_running_tasks(self):
        """检查是否有正在运行的任务"""
        # 优先检查任务管理器中是否有实际运行的进程
        actual_running_tasks = 0
        if hasattr(self, 'task_manager') and self.task_manager:
            # 先清理死进程，确保running_tasks的准确性
            self.task_manager._cleanup_dead_processes()
            actual_running_tasks = len(self.task_manager.running_tasks)
        
        # 如果任务管理器中没有运行的任务，直接返回False
        if actual_running_tasks == 0:
            return False
        
        # 检查UI表格中的状态 - 只有"运行中"才算运行任务，"已委托"不算
        table = self.tableWidget_2
        ui_running_tasks = 0
        for row in range(table.rowCount()):
            status_item = table.item(row, 6)  # 状态列
            if status_item and status_item.text() == "运行中":  # 只检查"运行中"状态
                ui_running_tasks += 1
            
            # 记录详细信息用于调试
            if ui_running_tasks > 0 or actual_running_tasks > 0:
                self.logger.info(f"关闭检查：UI显示 {ui_running_tasks} 个运行任务，实际运行 {actual_running_tasks} 个任务")
                
                # 记录UI中显示为运行状态的任务
                ui_tasks = []
                for row in range(table.rowCount()):
                    status_item = table.item(row, 6)  # 状态列是索引6
                    if status_item and status_item.text() == "运行中":  # 只记录"运行中"状态
                        stock_code = table.item(row, 0).text() if table.item(row, 0) else "未知"
                        ui_tasks.append(f"{stock_code}({status_item.text()})")
                
                if ui_tasks:
                    self.logger.info(f"UI中运行状态的任务：{', '.join(ui_tasks)}")
                
                # 记录任务管理器中实际运行的任务
                if actual_running_tasks > 0:
                    running_task_ids = list(self.task_manager.running_tasks.keys())
                    self.logger.info(f"任务管理器中运行的任务：{', '.join(running_task_ids)}")
        
        # 如果UI显示有运行任务但任务管理器中没有，自动同步状态
        if ui_running_tasks > 0 and actual_running_tasks == 0:
            self.logger.warning("检测到UI状态与任务管理器不同步，自动同步任务状态")
            
            # 自动将UI中显示为运行但实际没有运行的任务状态更新为"未运行"
            for row in range(table.rowCount()):
                status_item = table.item(row, 6)  # 状态列是索引6
                if status_item and status_item.text() == "运行中":  # 只处理"运行中"状态
                    # 检查该任务是否真的在运行
                    stock_code = table.item(row, 0).text() if table.item(row, 0) else ""
                    task_id = table.item(row, 0).data(Qt.UserRole) if table.item(row, 0) else None
                    
                    # 如果任务管理器中没有这个任务在运行，将状态改为"未运行"
                    is_actually_running = False
                    if task_id and task_id in self.task_manager.running_tasks:
                        is_actually_running = True
                    
                    if not is_actually_running:
                        # "运行中"状态但实际没有运行的任务改为"未运行"
                        status_item.setText("未运行")
                        status_item.setForeground(Qt.black)
                        self.logger.info(f"[{stock_code}] 任务状态已自动同步为：未运行（进程未运行）")
                        
                        # 更新按钮状态
                        self.update_task_buttons(row)
            
            # 重新计算UI中的运行任务数
            ui_running_tasks = 0
            for row in range(table.rowCount()):
                status_item = table.item(row, 6)  # 状态列是索引6
                if status_item and status_item.text() == "运行中":  # 只计算"运行中"状态
                    ui_running_tasks += 1
            
            self.logger.info(f"状态同步完成，UI现在显示 {ui_running_tasks} 个运行任务")
        
        # 返回是否有任务在运行（以任务管理器为准）
        return actual_running_tasks > 0

    def handle_close_event(self, event):
        """处理窗口关闭事件"""
        try:
            # 设置关闭标志，防止其他方法继续调用UI
            self._is_closing = True

            #self.logger.info("程序关闭，开始保存任务...")
            
            # 停止所有定时器
            if hasattr(self, 'status_timer') and self.status_timer is not None:
                try:
                    if hasattr(self.status_timer, 'timeout'):
                        self.status_timer.timeout.disconnect()
                except (RuntimeError, TypeError, AttributeError):
                    pass  # 信号可能已被断开
                try:
                    if hasattr(self.status_timer, 'stop'):
                        self.status_timer.stop()
                except (RuntimeError, AttributeError):
                    pass  # 定时器可能已被删除
                #self.logger.info("已停止状态栏更新定时器")
            
            if hasattr(self, 'order_refresh_timer') and self.order_refresh_timer is not None:
                try:
                    if hasattr(self.order_refresh_timer, 'stop'):
                        self.order_refresh_timer.stop()
                except (RuntimeError, AttributeError):
                    pass  # 定时器可能已被删除
                #self.logger.info("已停止订单刷新定时器")
            
            if hasattr(self, 'column_check_timer') and self.column_check_timer is not None:
                try:
                    if hasattr(self.column_check_timer, 'stop'):
                        self.column_check_timer.stop()
                except (RuntimeError, AttributeError):
                    pass  # 定时器可能已被删除
                #self.logger.info("已停止列宽检查定时器")
            
            # 停止QMT适配器的订阅线程，避免行情回调继续调用UI组件
            if hasattr(self, 'qmt_adapter') and self.qmt_adapter:
                try:
                    self.qmt_adapter.stop_quote_feed()
                except Exception as e:
                    self.logger.error(f"停止行情订阅失败: {str(e)}")
            
            # 清理所有订单监控任务
            if hasattr(self, 'order_monitors'):
                for order_id in list(self.order_monitors.keys()):
                    try:
                        self._stop_order_monitor(order_id)
                    except Exception as e:
                        self.logger.error(f"停止订单监控{order_id}失败: {str(e)}")
                #self.logger.info("已清理所有订单监控任务")
            
            # 检查是否有正在运行的任务
            if self.has_running_tasks():
                reply = QMessageBox.warning(
                    self.window,
                    "警告",
                    "还有正在运行的任务，请先停止所有任务再退出程序。",
                    QMessageBox.Ok
                )
                event.ignore()  # 阻止关闭
                return
            
            # 保存所有任务（包括参数修改）
            if hasattr(self, 'task_manager') and self.task_manager:
                try:
                    # 强制保存所有任务到文件
                    self.task_manager._block_tasks_updated_signal = True  # 阻止UI更新信号
                    self.task_manager.save_tasks(list(self.task_manager.tasks.values()))
                    #self.logger.info("任务保存成功")
                except Exception as e:
                    self.logger.error(f"保存任务失败: {str(e)}")
                finally:
                    self.task_manager._block_tasks_updated_signal = False
            
            # 停止所有任务
            if hasattr(self, 'task_manager') and self.task_manager:
                # 停止所有运行中的任务
                for task_id in list(self.task_manager.running_tasks.keys()):
                    try:
                        self.task_manager.stop_task(task_id)
                    except Exception as e:
                        self.logger.error(f"停止任务{task_id}失败: {str(e)}")
                #self.logger.info("已停止所有任务")
            
            # 取消任何延迟保存定时器
            if hasattr(self, '_save_timer') and self._save_timer is not None:
                try:
                    if hasattr(self._save_timer, 'stop'):
                        self._save_timer.stop()
                except (RuntimeError, AttributeError):
                    pass  # 定时器可能已被删除
                #self.logger.info("已取消延迟保存定时器")
            
            # 停止所有图表组件的定时清仓定时器
            if hasattr(self, 'tasks_charts_view') and self.tasks_charts_view:
                try:
                    self.tasks_charts_view.stop_all_chart_timers()
                    #self.logger.info("已停止所有图表定时器")
                except Exception as e:
                    self.logger.error(f"停止图表定时器失败: {str(e)}")
            
            self.logger.info("程序关闭")
            event.accept()  # 允许关闭
                
        except Exception as e:
            self.logger.error(f"处理窗口关闭事件失败: {str(e)}")
            event.accept()  # 发生异常时也允许关闭

    def update_task_buttons(self, row):
        """更新任务按钮状态"""
        try:
            table = self.tableWidget_2
            operation_widget = table.cellWidget(row, 7)
            if operation_widget and operation_widget.layout():
                start_button = operation_widget.layout().itemAt(0)
                stop_button = operation_widget.layout().itemAt(1)
                
                # 检查按钮是否存在
                if not start_button or not stop_button:
                    self.logger.warning(f"第{row}行操作按钮布局不完整")
                    return
                    
                start_button = start_button.widget()
                stop_button = stop_button.widget()
                
                if not start_button or not stop_button:
                    self.logger.warning(f"第{row}行操作按钮为空")
                    return
                
                # 根据任务状态更新按钮状态
                status_item = table.item(row, 6)  # 状态列是索引6
                if not status_item:
                    self.logger.warning(f"第{row}行状态项为空")
                    return
                    
                status = status_item.text()
                #self.logger.debug(f"[按钮更新] 第{row}行状态: {status}")
                
                if status == "运行中":
                    start_button.setEnabled(False)  # 运行中时禁用启动按钮
                    stop_button.setEnabled(True)    # 运行中时启用停止按钮
                    #self.logger.debug(f"[按钮更新] 第{row}行设置为运行中状态")
                elif status == "可能已委托":
                    start_button.setEnabled(False)  # 可能已委托时禁用启动按钮
                    stop_button.setEnabled(True)    # 可能已委托时启用停止按钮
                    #self.logger.debug(f"[按钮更新] 第{row}行设置为可能已委托状态")
                elif status == "已委托":
                    start_button.setEnabled(True)   # 已委托时启用启动按钮（可以重新启动）
                    stop_button.setEnabled(False)   # 已委托时禁用停止按钮（已经不在运行中）
                    #self.logger.debug(f"[按钮更新] 第{row}行设置为已委托状态")
                else:  # "未运行"、"连接断开"等其他状态
                    start_button.setEnabled(True)   # 未运行时启用启动按钮
                    stop_button.setEnabled(False)   # 未运行时禁用停止按钮
                    #self.logger.debug(f"[按钮更新] 第{row}行设置为其他状态: {status}")
            else:
                #self.logger.warning(f"[按钮更新] 第{row}行操作按钮为空")
                pass
        except Exception as e:
            self.logger.error(f"[按钮更新] 更新第{row}行按钮状态失败: {str(e)}")

    def set_task_manager(self, task_manager):
        """设置任务管理器"""
        self.task_manager = task_manager
        # 注意：信号连接已在main.py中完成，这里不需要重复连接
        # self.task_manager.update_task_ui.connect(self.update_task_field)
        # 让task_manager能回调主窗口
        self.task_manager.main_window = self
        # 移除可能导致循环的信号连接
        # self.task_manager.tasks_updated.connect(lambda: self.update_task_list(self.positions))
        
        # 注意：不在这里初始化图表视图，等待交易连接建立后再初始化
        # 由 handle_connection_restored 或交易连接成功后触发

    def _reapply_schedule_reload_default_display(self):
        """启动后再刷一次缺省时间，避免首帧时交易日历未就绪仍显示旧回退值。"""
        try:
            ra = getattr(self, "_scheduled_reload_run_at", None)
            if ra is not None:
                self.schedule_reload_dt_edit.setDateTime(
                    QDateTime(QDate(ra.year, ra.month, ra.day), QTime(ra.hour, ra.minute, ra.second))
                )
                return
            self.schedule_reload_dt_edit.setDateTime(_default_schedule_reload_qdatetime())
        except Exception:
            pass

    def _schedule_reload_state_path(self):
        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
        try:
            os.makedirs(data_dir, exist_ok=True)
        except OSError:
            pass
        return os.path.join(data_dir, "schedule_reload_pending.json")

    def _persist_scheduled_reload(self, run_at):
        """将预约写入 data/schedule_reload_pending.json，关闭程序后仍可恢复。"""
        path = self._schedule_reload_state_path()
        try:
            if run_at is None:
                if os.path.isfile(path):
                    os.remove(path)
                return
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"run_at": run_at.isoformat(timespec="seconds")}, f, ensure_ascii=False)
        except Exception as e:
            if getattr(self, "logger", None):
                self.logger.warning(f"[定时重载] 写入预约文件失败: {e}")

    def _load_scheduled_reload_from_disk(self):
        """启动时恢复未完成的预约；若已过期且在 48 小时内则补跑一次。"""
        path = self._schedule_reload_state_path()
        if not os.path.isfile(path):
            return
        if not getattr(self, "schedule_reload_status", None):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            s = data.get("run_at")
            if not s:
                return
            run_at = datetime.fromisoformat(s)
            now = datetime.now()
            if run_at > now:
                self._scheduled_reload_run_at = run_at
                self.schedule_reload_status.setText(f"已预约 {run_at.strftime('%Y-%m-%d %H:%M:%S')}")
                self.schedule_reload_status.setStyleSheet(
                    self._schedule_reload_status_base_style + "color: #d32f2f; font-weight: bold;"
                )
                if self.logger:
                    self.logger.info(f"[定时重载] 已从文件恢复预约: {run_at.strftime('%Y-%m-%d %H:%M:%S')}")
                return
            age_sec = (now - run_at).total_seconds()
            if age_sec <= 48 * 3600:
                self._scheduled_reload_run_at = run_at
                self.schedule_reload_status.setText(f"已预约 {run_at.strftime('%Y-%m-%d %H:%M:%S')}（已过期将补执行）")
                self.schedule_reload_status.setStyleSheet(
                    self._schedule_reload_status_base_style + "color: #d32f2f; font-weight: bold;"
                )
                if self.logger:
                    self.logger.info(
                        f"[定时重载] 发现已过期预约 {run_at.strftime('%Y-%m-%d %H:%M:%S')}，将在就绪后补执行"
                    )
                QTimer.singleShot(800, self._check_scheduled_reload)
            else:
                os.remove(path)
                if self.logger:
                    self.logger.info("[定时重载] 忽略过久的历史预约文件并删除")
        except Exception as e:
            if self.logger:
                self.logger.warning(f"[定时重载] 读取预约文件失败: {e}")

    def _on_app_state_for_scheduled_reload(self, state):
        """睡眠唤醒、切回前台时补检，避免仅靠定时器漏触发。"""
        try:
            if int(state) == int(Qt.ApplicationActive):
                QTimer.singleShot(0, self._check_scheduled_reload)
        except Exception:
            pass

    def _on_save_scheduled_reload(self):
        """保存一次性预约：到点自动执行「重新加载任务 + 启动全部任务」"""
        try:
            qdt = self.schedule_reload_dt_edit.dateTime()
            if not qdt.isValid():
                QMessageBox.warning(self.window, "提示", "请选择有效的日期和时间。")
                return
            run_at = datetime(
                qdt.date().year(),
                qdt.date().month(),
                qdt.date().day(),
                qdt.time().hour(),
                qdt.time().minute(),
                qdt.time().second(),
            )
            if run_at <= datetime.now():
                QMessageBox.warning(self.window, "提示", "预约时间必须晚于当前时间。")
                return
            self._scheduled_reload_run_at = run_at
            self._persist_scheduled_reload(run_at)
            self.schedule_reload_status.setText(f"已预约 {run_at.strftime('%Y-%m-%d %H:%M:%S')}")
            self.schedule_reload_status.setStyleSheet(
                self._schedule_reload_status_base_style + "color: #d32f2f; font-weight: bold;"
            )
            self.logger.info(f"[定时重载] 已预约自动重载并启动: {run_at.strftime('%Y-%m-%d %H:%M:%S')}")
            QMessageBox.information(self.window, "完成", "已保存定时预约，到点将自动重载并启动任务。")
        except Exception as e:
            self.logger.error(f"保存定时重载失败: {str(e)}")
            QMessageBox.warning(self.window, "错误", f"保存定时重载失败: {e}")

    def _on_clear_scheduled_reload(self):
        """清除一次性预约"""
        self._scheduled_reload_run_at = None
        self._scheduled_reload_did_force_load = False
        self._persist_scheduled_reload(None)
        self.schedule_reload_status.setText("未预约")
        self.schedule_reload_status.setStyleSheet(self._schedule_reload_status_base_style + "color: #666;")
        self.logger.info("[定时重载] 已清除预约")

    def _check_scheduled_reload(self):
        """定时检查一次性预约是否到点（串行执行，防止重复触发）。"""
        try:
            if self._scheduled_reload_busy:
                return
            run_at = getattr(self, "_scheduled_reload_run_at", None)
            if run_at is None:
                return
            if datetime.now() < run_at:
                return
            self._scheduled_reload_busy = True
            planned = run_at
            try:
                self.schedule_reload_status.setText("执行中...")
                self.schedule_reload_status.setStyleSheet(
                    self._schedule_reload_status_base_style + "color: #1565c0; font-weight: bold;"
                )
                ok = self._run_scheduled_reload_and_start_once()
                if not ok:
                    # 尚无任务等：保留预约，按定时器间隔继续重试
                    self._scheduled_reload_run_at = planned
                    self._persist_scheduled_reload(planned)
                    self.schedule_reload_status.setText(f"已预约 {planned.strftime('%Y-%m-%d %H:%M:%S')}")
                    self.schedule_reload_status.setStyleSheet(
                        self._schedule_reload_status_base_style + "color: #d32f2f; font-weight: bold;"
                    )
                    return
                self._scheduled_reload_run_at = None
                self._persist_scheduled_reload(None)
                self._scheduled_reload_did_force_load = False
                self.schedule_reload_status.setText("已执行")
                self.schedule_reload_status.setStyleSheet(self._schedule_reload_status_base_style + "color: #2e7d32;")
            except Exception as exec_err:
                # 启动阶段异常：保留预约以便重试，但不再每次 force_reload（见 _scheduled_reload_did_force_load）
                self._scheduled_reload_run_at = planned
                self._persist_scheduled_reload(planned)
                self.schedule_reload_status.setText("执行失败")
                self.schedule_reload_status.setStyleSheet(
                    self._schedule_reload_status_base_style + "color: #c62828; font-weight: bold;"
                )
                self.logger.error(f"[定时重载] 执行异常: {exec_err}", exc_info=True)
            finally:
                self._scheduled_reload_busy = False
        except Exception as e:
            self.schedule_reload_status.setText("执行失败")
            self.schedule_reload_status.setStyleSheet(
                self._schedule_reload_status_base_style + "color: #c62828; font-weight: bold;"
            )
            self.logger.error(f"[定时重载] 检查执行失败: {str(e)}", exc_info=True)

    def _run_scheduled_reload_and_start_once(self):
        """执行一次自动流程：重载任务 -> 刷新列表 -> 启动全部任务。成功返回 True。"""
        if not self.task_manager:
            self.logger.warning("[定时重载] task_manager 未初始化，取消本次执行")
            return False
        self.logger.info("[定时重载] 开始执行：重新加载任务 + 启动全部任务")
        # 同一预约多次重试时：只首次 force_reload，避免反复把状态打成「未运行」
        already_reloaded = bool(getattr(self, "_scheduled_reload_did_force_load", False))
        if not already_reloaded:
            # 必须从磁盘强制重载：否则 _tasks_loaded 为 True 时 load_tasks 会直接 return，列表与文件不一致
            self.task_manager.load_tasks(force_reload=True)
            self._scheduled_reload_did_force_load = True
        n_tasks = len(getattr(self.task_manager, "tasks", {}) or {})
        if n_tasks <= 0:
            self.logger.info("[定时重载] 当日任务表为空，保留预约稍后重试")
            # 空表允许下次再 force_reload（文件可能稍后才写入）
            self._scheduled_reload_did_force_load = False
            return False
        # 强制重建表格，避免防抖把刷新跳过导致 start_all 扫到旧表
        try:
            self._last_refresh_time = 0
            self._is_refreshing = False
        except Exception:
            pass
        self.refresh_task_table()
        tcv = getattr(self, "tasks_charts_view", None)
        if tcv and not already_reloaded:
            try:
                tcv.load_tasks()
            except Exception as e:
                self.logger.error(f"[定时重载] 刷新图表任务视图失败: {e}", exc_info=True)
        started, failed = self.start_all_tasks()
        self.logger.info(f"[定时重载] 启动结果: 成功={started} 失败={failed}")
        if tcv:
            try:
                n = tcv.sync_charts_with_running_tasks()
                self.logger.info(f"[定时重载] 已同步图表运行态: {n}")
            except Exception as e:
                self.logger.error(f"[定时重载] 同步图表运行态失败: {e}", exc_info=True)
        try:
            self._last_refresh_time = 0
            self._is_refreshing = False
        except Exception:
            pass
        self.refresh_task_table()
        self.logger.info("[定时重载] 执行完成")
        self._scheduled_reload_did_force_load = False
        return True

    def start_all_tasks(self):
        """启动所有任务。

        规则买入（无持仓）也必须能启动；旧逻辑要求「可用持仓>0」会把
        纯 single_buy 等任务在定时重载后漏掉，只显示未运行。

        优先按 task_manager.tasks 启动（不依赖表格行是否已刷完），
        再回退扫 tableWidget_2。返回 (started_count, failed_count)。
        """
        if not self.task_manager:
            return 0, 0

        buy_rule_types = {
            "single_buy",
            "breakthrough_buy",
            "cage_buy",
            "best_buy",
            "grid_buy",
            "night_buy",
        }

        def _task_should_start(task):
            if not isinstance(task, dict):
                return False
            params = task.get("params") if isinstance(task.get("params"), dict) else {}
            rules = params.get("rules") or []
            if isinstance(rules, list):
                for r in rules:
                    if not isinstance(r, dict):
                        continue
                    rt = str(r.get("type") or r.get("rule_type") or "").strip()
                    if rt in buy_rule_types:
                        return True
            strategy = str(task.get("strategy") or "")
            if "夜市买入" in strategy or "买入" in strategy:
                return True
            # 无买入规则：卖出类需有可用持仓
            try:
                vol = int(task.get("volume") or task.get("init_volume") or 0)
            except (TypeError, ValueError):
                vol = 0
            if vol > 0:
                return True
            code = str(task.get("stock_code") or "")
            if code and self.position_manager:
                try:
                    return int(self.position_manager.get_available_volume(code) or 0) > 0
                except Exception:
                    return False
            return False

        started = 0
        failed = 0
        started_ids = set()

        # 1) 主路径：直接扫任务管理器（预约重载最可靠）
        # 注意：必须 list((d or {}).items())，不能写 list(d or {}).items()
        # （后者会先把 dict 变成 key 列表，再调 .items() 直接报错）
        tasks_map = getattr(self.task_manager, "tasks", None) or {}
        if not isinstance(tasks_map, dict):
            self.logger.error(
                f"[启动全部] task_manager.tasks 类型异常: {type(tasks_map).__name__}，跳过启动"
            )
            return 0, 0
        for task_id, task in list(tasks_map.items()):
            if not _task_should_start(task):
                continue
            stock_code = str(task.get("stock_code") or "")
            try:
                ok = self.task_manager.start_task(task_id)
                if ok:
                    started += 1
                    started_ids.add(task_id)
                else:
                    failed += 1
                    self.logger.error(f"[启动全部] 启动失败: {stock_code} id={task_id}")
            except Exception as e:
                failed += 1
                self.logger.error(f"[启动全部] 启动 {stock_code} 异常: {e}", exc_info=True)

        # 2) 同步表格行状态（已启动的标成运行中）
        try:
            for row in range(self.tableWidget_2.rowCount()):
                stock_code_item = self.tableWidget_2.item(row, 0)
                if not stock_code_item:
                    continue
                task_id = stock_code_item.data(Qt.UserRole)
                if task_id in started_ids:
                    try:
                        status_item = self.tableWidget_2.item(row, 6)
                        if status_item:
                            status_item.setText("运行中")
                            status_item.setForeground(Qt.red)
                        operation_widget = self.create_operation_buttons(row)
                        self.tableWidget_2.setCellWidget(row, 7, operation_widget)
                    except Exception:
                        pass
        except Exception as e:
            self.logger.warning(f"[启动全部] 同步表格状态失败: {e}")

        # 3) 同步图表按钮
        tcv = getattr(self, "tasks_charts_view", None)
        if tcv and hasattr(tcv, "sync_charts_with_running_tasks"):
            try:
                tcv.sync_charts_with_running_tasks()
            except Exception as e:
                self.logger.warning(f"[启动全部] 同步图表失败: {e}")

        return started, failed

    def stop_all_tasks(self):
        """暂停所有任务"""
        if not self.task_manager:
            return
            
        for row in range(self.tableWidget_2.rowCount()):
            stock_code = self.tableWidget_2.item(row, 0).text()
            if stock_code:
                self.stop_task(row)

    def update_hold_days(self):
        """更新所有任务的持有交易日"""
        try:
            table = self.tableWidget_2
            for row in range(table.rowCount()):
                stock_code = table.item(row, 0).text()
                strategy_container = table.cellWidget(row, 4)
                strategy = strategy_container.combo.currentText() if strategy_container else "规则任务"
                buy_date_item = table.item(row, 4)
                buy_date = buy_date_item.text() if buy_date_item else datetime.now().strftime('%Y-%m-%d')
                
                # 重新计算持有交易日
                hold_days = self.task_manager.calculate_hold_days(buy_date)
                
                # 更新显示
                hold_days_item = QTableWidgetItem(str(hold_days))
                hold_days_item.setTextAlignment(Qt.AlignCenter)
                hold_days_item.setFlags(hold_days_item.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, 5, hold_days_item)
                
                # 构建任务ID，支持夜市任务的时间戳格式
                if '夜市' in strategy:
                    # 检查买入日期是否已经包含时间戳
                    if '_' in buy_date and len(buy_date.split('_')) >= 4:
                        # 已经有时间戳，使用完整格式
                        task_id = f"{stock_code}_{strategy}_{buy_date}"
                    else:
                        # 没有时间戳，添加时间戳
                        timestamp = datetime.now().strftime('%H%M%S')
                        task_id = f"{stock_code}_{strategy}_{buy_date}_{timestamp}"
                else:
                    # 普通策略任务，使用原有格式
                    task_id = f"{stock_code}_{strategy}_{buy_date}"
                
                # 更新task_manager中的持有交易日
                if task_id in self.task_manager.tasks:
                    self.task_manager.tasks[task_id]['hold_days'] = hold_days
            
            # 保存更新后的任务
            self.save_current_tasks()
            self.logger.info("零点更新：已更新所有任务的持有交易日")
            
        except Exception as e:
            self.logger.error(f"更新持有交易日失败：{str(e)}")

    def create_night_sell_task(self):
        """创建夜市卖出任务"""
        try:
            # 检查是否有持仓
            if not self.position_manager.get_all_positions():
                QMessageBox.warning(self.window, "警告", "当前没有持仓，无法创建卖出任务")
                return
            
            # 检查是否有可卖出的股票
            sellable_stocks = [code for code, data in self.position_manager.get_all_positions().items() if data.get('volume', 0) > 0]
            if not sellable_stocks:
                QMessageBox.warning(self.window, "警告", "当前没有可卖出的股票")
                return
            
            # 创建参数设置对话框
            dialog = NightSellParameterDialog(self.window, self.position_manager.get_all_positions(), self.qmt_adapter)
            if dialog.exec_() == QDialog.Accepted:
                # 获取参数
                params = dialog.get_sell_params()
                stock_code = params['stock_code']
                
                if not stock_code:
                    QMessageBox.warning(self.window, "警告", "请选择股票")
                    return
                
                # 检查持仓
                if not self.position_manager.has_position(stock_code):
                    QMessageBox.warning(self.window, "警告", "该股票不在持仓中")
                    return
                    
                position = self.position_manager.get_position_data(stock_code)
                if params['sell_volume'] > position['volume']:
                    QMessageBox.warning(self.window, "警告", f"卖出数量不能大于持仓数量({position['volume']}股)")
                    return
                
                # 生成任务ID
                task_id = self.task_manager.generate_task_id()
                
                # 创建任务数据
                current_time = datetime.now()
                task_data = {
                    'stock_code': stock_code,
                    'stock_name': position.get('stock_name', ''),
                    # 初始持仓列用于显示"拟卖出数量"，应取参数中的卖出数量
                    'init_volume': params['sell_volume'],
                    # 数量列显示可用持仓，使用can_use_volume而不是volume
                    'volume': position.get('can_use_volume', 0),
                    'init_cost': position.get('open_price', 0),
                    'buy_date': current_time.strftime('%Y-%m-%d'),
                    'hold_days': 0,
                    'base_price': params.get('sell_price', 0),
                    'strategy': "夜市卖出",
                    'status': '未运行',
                    'task_id': task_id,
                    'create_time': current_time.strftime('%Y-%m-%d %H:%M:%S'),  # 添加创建时间
                    'params': {
                        'sell_volume': params['sell_volume'],
                        'sell_type': params['sell_type'],
                        'sell_price': params.get('sell_price', 0),
                        'is_night_task': True,
                        'task_type': 'sell'
                    }
                }
                
                # 直接添加到任务管理器
                self.task_manager.tasks[task_id] = task_data
                self.task_manager.task_params[task_id] = task_data['params']
                
                # 直接保存到文件
                all_tasks = list(self.task_manager.tasks.values())
                self.task_manager.save_tasks(all_tasks)
                
                # 重新加载任务列表
                self.task_manager.load_tasks()
                
                # 刷新UI
                self.refresh_task_table()
                
                self.logger.info(f"夜市卖出任务创建成功: {task_id}")
                QMessageBox.information(self.window, "成功", f"夜市卖出任务创建成功！\n股票代码: {stock_code}\n卖出数量: {params['sell_volume']}股")
                
        except Exception as e:
            self.logger.error(f"创建夜市卖出任务失败: {str(e)}")
            QMessageBox.critical(self.window, "错误", f"创建夜市卖出任务失败: {str(e)}")

    def create_buy_task(self):
        """创建买入任务"""
        try:
            # 创建参数设置对话框
            from ui.dialogs import BuyTaskParameterDialog
            dialog = BuyTaskParameterDialog(self.window, self.qmt_adapter)
            
            if dialog.exec_() == QDialog.Accepted:
                # 获取参数
                params = dialog.get_buy_params()
                if not params:
                    QMessageBox.warning(self.window, "警告", "请输入正确的6位股票代码")
                    return
                
                stock_code = params['stock_code']
                if not stock_code:
                    QMessageBox.warning(self.window, "警告", "请输入股票代码")
                    return
                
                # 补齐股票代码后缀
                if len(stock_code) == 6:
                    if stock_code.startswith(('0', '1', '3')):
                        stock_code = f"{stock_code}.SZ"
                    elif stock_code.startswith(('5', '6')):
                        stock_code = f"{stock_code}.SH"
                    elif stock_code.startswith(('4', '8', '920')):
                        stock_code = f"{stock_code}.BJ"
                
                # 获取股票名称
                stock_name = myf.get_stock_name(None, stock_code)
                
                # 如果获取失败或返回"未知名称"，尝试使用全局股票信息管理器重新获取
                if not stock_name or stock_name == "未知名称":
                    try:
                        from utils.stock_info_manager import get_stock_name
                        # 清理股票代码格式，去掉市场后缀
                        # 确保stock_code是字符串类型
                        stock_code = str(stock_code) if stock_code is not None else ''
                        clean_code = stock_code.split('.')[0] if '.' in stock_code else stock_code
                        stock_name = get_stock_name(clean_code)
                    except Exception as e:
                        self.logger.warning(f"[买入任务] 重新获取股票名称失败: {stock_code}, 错误: {e}")
                
                # 检查当前持仓情况
                current_volume = 0
                if self.position_manager.has_position(stock_code):
                    current_volume = self.position_manager.get_available_volume(stock_code)
                
                # 生成任务ID
                task_id = self.task_manager.generate_task_id()
                
                # 创建任务数据（规则任务 + 单点买入规则，与图表任务一致）
                current_time = datetime.now()
                buy_price = params.get('buy_price', 0)
                buy_vol = params['buy_volume']
                single_buy_rule = {
                    'id': f"rule_{uuid.uuid4().hex[:8]}",
                    'type': 'single_buy',
                    'enabled': True,
                    'name': '单点买入',
                    'price': buy_price,
                    'volume': buy_vol
                }
                try:
                    import configparser
                    cfg = configparser.ConfigParser()
                    cfg_path = os.path.join(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'data',
                        'config.ini',
                    )
                    early = False
                    if os.path.isfile(cfg_path):
                        cfg.read(cfg_path, encoding='utf-8')
                        if cfg.has_option('Trading', 'early_order'):
                            early = str(cfg.get('Trading', 'early_order') or '0').strip().lower() in (
                                '1', 'true', 'yes', 'on',
                            )
                    single_buy_rule['early_order_enabled'] = early
                except Exception:
                    single_buy_rule['early_order_enabled'] = False
                task_data = {
                    'stock_code': stock_code,
                    'stock_name': stock_name,
                    'init_volume': buy_vol,
                    'volume': current_volume,
                    'init_cost': buy_price,
                    'buy_date': current_time.strftime('%Y-%m-%d'),
                    'hold_days': 0,
                    'base_price': buy_price,
                    'strategy': '规则任务',
                    'status': '未运行',
                    'task_id': task_id,
                    'create_time': current_time.strftime('%Y-%m-%d %H:%M:%S'),
                    'params': {
                        'rules': [single_buy_rule],
                        'up_threshold': 10.0,
                        'down_threshold': 10.0,
                        'up_operation': '卖出',
                        'down_operation': '买入',
                        'trade_volume': buy_vol,
                        'cycle_times': 0,
                        'enable_smart_sell': True,
                        'sell_drop_threshold': 0.002,
                        'sell_timeout': 14400,
                        'enable_smart_buy': True,
                        'buy_rebound_threshold': 0.002,
                        'buy_timeout': 14400
                    }
                }
                
                # 直接添加到任务管理器
                self.task_manager.tasks[task_id] = task_data
                self.task_manager.task_params[task_id] = task_data['params']
                
                # 直接保存到文件
                all_tasks = list(self.task_manager.tasks.values())
                self.task_manager.save_tasks(all_tasks)
                
                # 重新加载任务列表
                self.task_manager.load_tasks()
                
                # 刷新UI
                self.refresh_task_table()
                
                self.logger.info(f"规则任务（单点买入）创建成功: {task_id}")
                from core.utils.security_type import SecurityTypeUtil
                precision = SecurityTypeUtil.get_price_precision(stock_code)
                QMessageBox.information(self.window, "成功", f"已创建规则任务（含单点买入规则）！\n股票代码: {stock_code}\n买入数量: {params['buy_volume']}股\n买入价格: {params.get('buy_price', 0):.{precision}f}元")
                
        except Exception as e:
            self.logger.error(f"创建买入任务失败: {str(e)}")
            QMessageBox.critical(self.window, "错误", f"创建买入任务失败: {str(e)}")
    
    def create_universal_strategy(self):
        """创建规则任务"""
        try:
            # 创建规则任务对话框（兼容旧类名）
            from ui.create_universal_strategy_dialog import CreateUniversalStrategyDialog
            dialog = CreateUniversalStrategyDialog(self.window, self.qmt_adapter)
            
            if dialog.exec_() == QDialog.Accepted:
                values = dialog.get_values()
                
                # 补齐股票代码后缀，确保与订阅格式一致
                stock_code = values['stock_code']
                full_stock_code = stock_code
                if len(stock_code) == 6:
                    if stock_code.startswith(('0', '1', '3')):
                        full_stock_code = f"{stock_code}.SZ"
                    elif stock_code.startswith(('5', '6')):
                        full_stock_code = f"{stock_code}.SH"
                    elif stock_code.startswith(('4', '8')):
                        full_stock_code = f"{stock_code}.BJ"
                
                # 添加调试日志
                self.logger.info(f"股票代码转换: {stock_code} -> {full_stock_code}")
                
                # 创建任务数据
                current_time = datetime.now()
                task_data = {
                    'stock_code': full_stock_code,  # 使用带后缀的完整股票代码
                    'stock_name': values['stock_name'],
                    'strategy': '规则任务',
                    'base_price': values['base_price'],
                    'init_volume': 0,  # 没有持仓，初始数量为0
                    'volume': 0,       # 当前可用数量为0
                    'buy_date': current_time.strftime('%Y-%m-%d'),
                    'hold_days': 0,
                    'status': '未运行',
                    'create_time': current_time.strftime('%Y-%m-%d %H:%M:%S'),  # 添加创建时间
                    'params': {
                        'up_threshold': values['up_threshold'],
                        'down_threshold': values['down_threshold'],
                        'up_operation': values['up_operation'],
                        'down_operation': values['down_operation'],
                        'trade_volume': values['trade_volume'],
                        'cycle_times': values['cycle_times'],
                        'enable_smart_sell': values.get('enable_smart_sell', True),
                        'sell_drop_threshold': values.get('sell_drop_threshold', 0.002),
                        'sell_timeout': values.get('sell_timeout', 14400),
                        'enable_smart_buy': values.get('enable_smart_buy', True),
                        'buy_rebound_threshold': values.get('buy_rebound_threshold', 0.002),
                        'buy_timeout': values.get('buy_timeout', 14400)
                    }
                }
                
                # 添加到任务管理器
                if hasattr(self, 'task_manager') and self.task_manager:
                    # 生成任务ID
                    task_id = self.task_manager.generate_task_id()
                    task_data['task_id'] = task_id
                    
                    # 直接添加到任务管理器
                    self.task_manager.tasks[task_id] = task_data
                    self.task_manager.task_params[task_id] = task_data['params']
                    
                    # 添加调试日志
                    self.logger.info(f"规则任务已添加到任务管理器: {task_id}, 股票代码: {full_stock_code}")
                    self.logger.info(f"当前任务管理器中的任务数量: {len(self.task_manager.tasks)}")
                    
                    # 直接保存到文件
                    all_tasks = list(self.task_manager.tasks.values())
                    self.task_manager.save_tasks(all_tasks)
                    
                    # 不需要重新加载任务列表，因为任务已经直接添加到内存中
                    # self.task_manager.load_tasks()  # 注释掉，避免_tasks_loaded标志阻止重新加载
                    
                    # 刷新UI
                    self.refresh_task_table()
                    
                    # 添加股票到订阅列表，确保能获取实时价格数据
                    if hasattr(self, 'qmt_adapter') and self.qmt_adapter:
                        try:
                            if self.qmt_adapter.ensure_subscribed(full_stock_code):
                                subscribed = self.qmt_adapter.get_subscribed_codes()
                                self.logger.info(
                                    f"已将股票 {full_stock_code} 添加到订阅列表，当前订阅数量: {len(subscribed)}"
                                )
                            else:
                                self.logger.info(f"股票 {full_stock_code} 已在订阅列表中")
                        except Exception as e:
                            self.logger.warning(f"添加股票 {full_stock_code} 到订阅列表失败: {str(e)}")
                    else:
                        self.logger.warning("QMT适配器未初始化，无法添加股票到订阅列表")
                    
                    self.logger.info(f"成功创建规则任务: {task_id}")
                    
                    # 显示成功消息
                    # 根据股票代码确定价格精度
                    precision = SecurityTypeUtil.get_price_precision(values['stock_code'])
                    QMessageBox.information(
                        self.window, 
                        "创建成功", 
                        f"规则任务创建成功！\n\n"
                        f"股票代码: {values['stock_code']}\n"
                        f"股票名称: {values['stock_name']}\n"
                        f"基准价格: {values['base_price']:.{precision}f} 元\n"
                        f"上涨阈值: {values['up_threshold']:.1f}%\n"
                        f"下跌阈值: {values['down_threshold']:.1f}%\n"
                        f"上涨操作: {values['up_operation']}\n"
                        f"下跌操作: {values['down_operation']}\n"
                        f"每笔操作股数: {values['trade_volume']} 股\n"
                        f"循环次数: {values['cycle_times']} 次"
                    )
                else:
                    QMessageBox.warning(self.window, "警告", "任务管理器未初始化")
                    
        except Exception as e:
            self.logger.error(f"创建规则任务失败: {str(e)}")
            QMessageBox.critical(self.window, "错误", f"创建规则任务失败: {str(e)}")


    def create_night_buy_task(self):
        """创建夜市买入任务"""
        try:
            # 创建参数设置对话框
            dialog = NightBuyParameterDialog(self.window, self.qmt_adapter)
            
            if dialog.exec_() == QDialog.Accepted:
                # 获取参数
                params = dialog.get_buy_params()
                if not params:
                    QMessageBox.warning(self.window, "警告", "请输入正确的6位股票代码")
                    return
                
                stock_code = params['stock_code']
                if not stock_code:
                    QMessageBox.warning(self.window, "警告", "请输入股票代码")
                    return
                
                # 补齐股票代码后缀
                if len(stock_code) == 6:
                    if stock_code.startswith(('0', '1', '3')):
                        stock_code = f"{stock_code}.SZ"
                    elif stock_code.startswith(('5', '6')):
                        stock_code = f"{stock_code}.SH"
                    elif stock_code.startswith(('4', '8', '920')):
                        stock_code = f"{stock_code}.BJ"
                
                # 获取股票名称
                stock_name = myf.get_stock_name(None, stock_code)
                
                # 如果获取失败或返回"未知名称"，尝试使用全局股票信息管理器重新获取
                if not stock_name or stock_name == "未知名称":
                    try:
                        from utils.stock_info_manager import get_stock_name
                        # 清理股票代码格式，去掉市场后缀
                        # 确保stock_code是字符串类型
                        stock_code = str(stock_code) if stock_code is not None else ''
                        clean_code = stock_code.split('.')[0] if '.' in stock_code else stock_code
                        stock_name = get_stock_name(clean_code)
                    except Exception as e:
                        self.logger.warning(f"[夜市买入] 重新获取股票名称失败: {stock_code}, 错误: {e}")
                
                # 检查当前持仓情况
                current_volume = 0
                if self.position_manager.has_position(stock_code):
                    current_volume = self.position_manager.get_available_volume(stock_code)
                
                # 生成任务ID
                task_id = self.task_manager.generate_task_id()
                
                # 创建任务数据
                current_time = datetime.now()
                task_data = {
                    'stock_code': stock_code,
                    'stock_name': stock_name,
                    'init_volume': params['buy_volume'],
                    'volume': params['buy_volume'],  # 使用委托买入数量，而不是当前持仓数量
                    'init_cost': params.get('buy_price', 0),
                    'buy_date': current_time.strftime('%Y-%m-%d'),
                    'hold_days': 0,
                    'base_price': params.get('buy_price', 0),
                    'strategy': "夜市买入",
                    'status': '未运行',
                    'task_id': task_id,
                    'create_time': current_time.strftime('%Y-%m-%d %H:%M:%S'),  # 添加创建时间
                    'params': {
                        'buy_volume': params['buy_volume'],
                        'buy_type': params['buy_type'],
                        'buy_price': params.get('buy_price', 0),
                        'is_night_task': True,
                        'task_type': 'buy'
                    }
                }
                
                # 直接添加到任务管理器
                self.task_manager.tasks[task_id] = task_data
                self.task_manager.task_params[task_id] = task_data['params']
                
                # 直接保存到文件
                all_tasks = list(self.task_manager.tasks.values())
                self.task_manager.save_tasks(all_tasks)
                
                # 重新加载任务列表
                self.task_manager.load_tasks()
                
                # 刷新UI
                self.refresh_task_table()
                
                self.logger.info(f"夜市买入任务创建成功: {task_id}, 股票: {stock_code}, 买入数量: {params['buy_volume']}股")
                QMessageBox.information(self.window, "成功", f"夜市买入任务创建成功！\n股票代码: {stock_code}\n买入数量: {params['buy_volume']}股")
                
        except Exception as e:
            self.logger.error(f"创建夜市买入任务失败: {str(e)}")
            QMessageBox.critical(self.window, "错误", f"创建夜市买入任务失败: {str(e)}")

    def _load_sound_settings(self):
        """加载音效设置"""
        try:
            if os.path.exists(self.sound_settings_file):
                with open(self.sound_settings_file, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                    self.sound_enabled = settings.get('sound_enabled', False)
            else:
                self.sound_enabled = False  # 默认关闭
        except Exception as e:
            self.logger.warning(f"加载音效设置失败: {str(e)}，使用默认值（关闭）")
            self.sound_enabled = False
    
    def _save_sound_settings(self):
        """保存音效设置"""
        try:
            settings = {
                'sound_enabled': self.sound_enabled
            }
            with open(self.sound_settings_file, 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"保存音效设置失败: {str(e)}")
    
    def show_task_settings(self):
        """显示任务设置对话框"""
        from ui.task_settings_dialog import TaskSettingsDialog
        from PyQt5.QtWidgets import QDialog

        dialog = TaskSettingsDialog(self.window)
        if dialog.exec_() != QDialog.Accepted:
            return
        # builtin：最小买入/提前下单等写入 config 后立刻推 rules_armed，供 QMT 热刷新
        try:
            from brokers.qmt_order_mode import use_builtin_order_execution

            if use_builtin_order_execution() and self.task_manager is not None:
                self.task_manager._sync_rules_armed_if_builtin()
        except Exception as e:
            if self.logger:
                self.logger.debug(f"[task_settings] sync_rules_armed: {e}")
    
    def _toggle_sound(self):
        """切换音效开关"""
        self.sound_enabled = not self.sound_enabled
        self._update_sound_button_icon()
        self._save_sound_settings()
        status_text = "已开启" if self.sound_enabled else "已关闭"
        self.logger.info(f"音效开关：{status_text}")
    
    def _update_sound_button_icon(self):
        """更新音效按钮图标"""
        if not hasattr(self, 'sound_btn') or self.sound_btn is None:
            return
        
        if self.sound_enabled:
            # 开启状态：显示🔊图标（使用Unicode字符）
            self.sound_btn.setText("🔊")
            self.sound_btn.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    border: 2px solid #2E7D32;
                    border-radius: 4px;
                    font-size: 18px;
                    font-weight: bold;
                    padding: 2px;
                }
                QPushButton:hover {
                    background-color: #45a049;
                    border-color: #1B5E20;
                }
                QPushButton:checked {
                    background-color: #4CAF50;
                }
                QPushButton:pressed {
                    background-color: #388E3C;
                }
            """)
            self.sound_btn.setToolTip("音效已开启（点击关闭）")
        else:
            # 关闭状态：显示🔇图标
            self.sound_btn.setText("🔇")
            self.sound_btn.setStyleSheet("""
                QPushButton {
                    background-color: #9E9E9E;
                    color: white;
                    border: 2px solid #616161;
                    border-radius: 4px;
                    font-size: 18px;
                    font-weight: bold;
                    padding: 2px;
                }
                QPushButton:hover {
                    background-color: #757575;
                    border-color: #424242;
                }
                QPushButton:checked {
                    background-color: #9E9E9E;
                }
                QPushButton:pressed {
                    background-color: #616161;
                }
            """)
            self.sound_btn.setToolTip("音效已关闭（点击开启）")
    
    def play_trade_sound(self):
        """播放交易执行音效（婉转悠扬的音调）"""
        if not self.sound_enabled:
            return
        
        try:
            import winsound
            import threading
            
            def play_melody():
                """在后台线程播放音调，避免阻塞"""
                try:
                    # 使用两个音符组成一个婉转的音调：先高后低，形成优美的下降音调
                    # 第一个音：800Hz，150ms（稍高一些）
                    winsound.Beep(800, 150)
                    # 短暂间隔
                    import time
                    time.sleep(0.05)
                    # 第二个音：600Hz，200ms（较低，更柔和）
                    winsound.Beep(600, 200)
                except Exception as e:
                    pass  # 静默失败
            
            # 在后台线程播放，不阻塞主线程
            sound_thread = threading.Thread(target=play_melody, daemon=True)
            sound_thread.start()
            
        except Exception as e:
            # 如果winsound不可用，尝试使用其他方法
            try:
                import sys
                if sys.platform == 'win32':
                    import winsound
                    import threading
                    import time
                    
                    def play_melody():
                        try:
                            winsound.Beep(800, 150)
                            time.sleep(0.05)
                            winsound.Beep(600, 200)
                        except:
                            pass
                    
                    sound_thread = threading.Thread(target=play_melody, daemon=True)
                    sound_thread.start()
                else:
                    # Linux/Mac可以使用系统的beep命令或其他方法
                    pass
            except Exception as e2:
                self.logger.warning(f"播放音效失败: {str(e2)}")
    
    def show_version_dialog(self):
        """显示版本号"""
        QMessageBox.information(self.window, "版本信息", "版本号：V4.2")


    def on_column_resized(self, logicalIndex, oldSize, newSize):
        """处理列宽改变事件，确保状态列和操作列保持固定宽度"""
        try:
            # 确保状态列（第7列）和操作列（第8列）保持固定宽度
            if logicalIndex == 7:
                self.tableWidget_2.setColumnWidth(logicalIndex, 120)  # 状态列
            elif logicalIndex == 8:
                self.tableWidget_2.setColumnWidth(logicalIndex, 120)  # 操作列
                    
        except Exception as e:
            self.logger.error(f"处理列宽改变事件失败: {str(e)}")
            # 发生异常时也要确保固定列保持固定宽度
            try:
                self.tableWidget_2.setColumnWidth(7, 120)  # 状态列
                self.tableWidget_2.setColumnWidth(8, 120)  # 操作列
            except:
                pass

    def check_operation_column_width(self):
        """检查操作列宽度"""
        try:
            # 性能监控：检查更新频率
            if hasattr(self, '_last_column_check_time'):
                current_time = time.time()
                time_diff = current_time - self._last_column_check_time
                if time_diff < 1.0:  # 如果更新间隔小于1秒，可能存在性能问题
                    return
            self._last_column_check_time = current_time
                
        except Exception as e:
            # 静默处理异常，避免日志过多
            pass

    def get_task_id_from_table_row(self, row):
        """从表格行获取任务ID
        Args:
            row: 表格行号
        Returns:
            task_id: 任务ID，如果找不到则返回None
        """
        try:
            # 首先尝试从表格行的UserRole数据中获取任务ID
            stock_code_item = self.tableWidget_2.item(row, 0)
            
            
            if stock_code_item and stock_code_item.data(Qt.UserRole):
                task_id = stock_code_item.data(Qt.UserRole)
                # 验证任务ID是否存在于任务管理器中
                if task_id in self.task_manager.tasks:
                    return task_id
                else:
                    self.logger.error(f"表格行{row}的任务ID不存在于任务管理器中: {task_id}")
                    return None

            # 如果UserRole中没有有效的任务ID，直接报错
            self.logger.error(f"表格行{row}没有有效的任务ID，无法进行基准价修改")
            return None
                
        except Exception as e:
            self.logger.error(f"获取任务ID失败: {str(e)}")
            return None

    def on_stock_code_changed(self, text):
        """股票代码输入改变时的处理"""
        # 只允许输入数字
        text = ''.join(filter(str.isdigit, text))
        if len(text) > 6:
            text = text[:6]
        
        # 更新输入框
        if text != self.stock_code.text():
            self.stock_code.setText(text)
            return
        
        # 验证股票代码
        if len(text) == 6:
            # 使用全局股票信息管理器验证股票代码
            try:
                from utils.stock_info_manager import get_stock_name
                stock_name = get_stock_name(text)
                if stock_name and stock_name != "未知名称":
                    # 显示完整的股票代码和名称
                    self.stock_name_label.setText(f"{text} {stock_name}")
                    self.stock_name_label.setStyleSheet("color: green; font-weight: bold;")
                else:
                    self.stock_name_label.setText("未找到该股票代码，请检查输入")
                    self.stock_name_label.setStyleSheet("color: red; font-weight: bold;")
            except Exception as e:
                self.stock_name_label.setText("无法验证股票代码，请检查网络连接")
                self.stock_name_label.setStyleSheet("color: orange; font-weight: bold;")
        else:
            self.stock_name_label.setText("")

    def optimize_ui_performance(self):
        """优化UI性能，防止卡死"""
        try:
            # 减少定时器频率
            if hasattr(self, 'status_timer') and self.status_timer is not None and hasattr(self.status_timer, 'stop'):
                self.status_timer.stop()
                self.status_timer.start(3000)  # 改为3秒更新一次
            
            if hasattr(self, 'column_check_timer') and self.column_check_timer is not None and hasattr(self.column_check_timer, 'stop'):
                self.column_check_timer.stop()
                self.column_check_timer.start(3000)  # 改为3秒检查一次
            
            # 清理可能的内存泄漏
            if hasattr(self, 'tableWidget_2'):
                # 限制表格行数，避免过多行导致性能问题
                max_rows = 100
                if self.tableWidget_2.rowCount() > max_rows:
                    self.logger.warning(f"任务表格行数过多({self.tableWidget_2.rowCount()})，可能影响性能")
            
            # 强制垃圾回收
            import gc
            gc.collect()
            
            #self.logger.info("UI性能优化完成")
            
        except Exception as e:
            self.logger.error(f"UI性能优化失败: {str(e)}")

    def handle_ui_freeze(self):
        """处理UI卡死问题"""
        try:
            self.logger.warning("检测到UI可能卡死，开始优化...")
            
            # 停止所有定时器
            if hasattr(self, 'status_timer') and self.status_timer is not None and hasattr(self.status_timer, 'stop'):
                self.status_timer.stop()
            if hasattr(self, 'column_check_timer') and self.column_check_timer is not None and hasattr(self.column_check_timer, 'stop'):
                self.column_check_timer.stop()
            
            # 恢复所有信号
            if hasattr(self, 'tableWidget_2'):
                self.tableWidget_2.blockSignals(False)
            
            # 清理更新标志
            self._updating_tasks = False
            
            # 重新启动定时器，但频率更低
            if hasattr(self, 'status_timer'):
                self.status_timer.start(5000)  # 5秒更新一次
            if hasattr(self, 'column_check_timer'):
                self.column_check_timer.start(5000)  # 5秒检查一次
            
            self.logger.info("UI卡死处理完成")
            
        except Exception as e:
            self.logger.error(f"处理UI卡死失败: {str(e)}")

    def add_cancel_button(self, table, row, order_status, order_id):
        """添加撤单按钮"""
        try:
            # 检查参数
            if not table or row < 0 or not order_id:
                self.logger.error(f"参数无效 - table: {table}, row: {row}, order_id: {order_id}")
                return
                
            # 检查是否已经在监控中
            is_monitoring = order_id in self.order_monitors
            
            # 1. 使用 trade_record_manager 创建操作控件
            try:
                #self.logger.info(f"创建操作控件 - row: {row}")
                operation_container = self.trade_record_manager.create_operation_widget(order_status)
                if not operation_container:
                    self.logger.error(f"创建操作控件失败 - row: {row}")
                    return
                #self.logger.info(f"操作控件创建成功 - row: {row}")
                
                # 添加调试信息
                #self.logger.info(f"操作控件大小: {operation_container.size()}")
                #self.logger.info(f"操作控件可见性: {operation_container.isVisible()}")
                
            except Exception as e:
                self.logger.error(f"创建操作控件异常: {e}")
                return
            
            # 2. 在 UI 层 setCellWidget
            try:
                #self.logger.info(f"设置操作控件到表格 - row: {row}, col: 8")
                table.setCellWidget(row, 8, operation_container)
                #self.logger.info(f"操作控件设置成功 - row: {row}")
                
                # 验证设置是否成功
                cell_widget = table.cellWidget(row, 8)
                if cell_widget:
                    #self.logger.info(f"验证成功：单元格控件已设置 - row: {row}")
                    #self.logger.info(f"单元格控件大小: {cell_widget.size()}")
                    #self.logger.info(f"单元格控件可见性: {cell_widget.isVisible()}")
                    pass
                else:
                    self.logger.error(f"验证失败：单元格控件未设置 - row: {row}")
                
            except Exception as e:
                self.logger.error(f"setCellWidget 异常: {e}")
                return
            
            # 3. 获取按钮对象
            try:
                #self.logger.info(f"获取按钮对象 - row: {row}")
                cancel_button = operation_container.findChild(QPushButton, "cancel_button")
                monitor_button = operation_container.findChild(QPushButton, "monitor_button")
                
                if not cancel_button or not monitor_button:
                    self.logger.error(f"获取按钮对象失败 - cancel_button: {cancel_button}, monitor_button: {monitor_button}")
                    return
                #self.logger.info(f"按钮对象获取成功 - cancel_button: {cancel_button}, monitor_button: {monitor_button}")
                
                # 添加按钮调试信息
                #self.logger.info(f"撤单按钮大小: {cancel_button.size()}, 监控按钮大小: {monitor_button.size()}")
                #self.logger.info(f"撤单按钮可见: {cancel_button.isVisible()}, 监控按钮可见: {monitor_button.isVisible()}")
                #self.logger.info(f"撤单按钮文本: {cancel_button.text()}, 监控按钮文本: {monitor_button.text()}")
                
            except Exception as e:
                self.logger.error(f"获取按钮对象异常: {e}")
                return
            
            # 4. 立即连接信号
            try:
                #self.logger.info(f"连接按钮信号 - row: {row}, order_id: {order_id}")
                cancel_button.clicked.connect(lambda checked, r=row, oid=order_id: self._handle_cancel_click(r, oid))
                monitor_button.clicked.connect(lambda checked, r=row, oid=order_id: self._handle_monitor_click(r, oid))
                #self.logger.info(f"按钮信号连接成功 - row: {row}")
            except Exception as e:
                self.logger.error(f"信号连接异常: {e}")
                return
            
            # 5. 根据监控状态设置监控按钮样式（字体走统一样式）
            try:
                from ui.trade_record_manager import (
                    _STYLE_ENDED,
                    _STYLE_MONITOR,
                    _STYLE_MONITORING,
                )

                if order_status in ['已成', '已撤', '废单']:
                    monitor_button.setEnabled(False)
                    monitor_button.setText("已结束")
                    monitor_button.setStyleSheet(_STYLE_ENDED)
                elif is_monitoring:
                    monitor_button.setText("监控中")
                    monitor_button.setStyleSheet(_STYLE_MONITORING)
                else:
                    monitor_button.setText("监控")
                    monitor_button.setStyleSheet(_STYLE_MONITOR)
            except Exception as e:
                self.logger.error(f"设置按钮样式异常: {e}")
                
            #self.logger.info(f"撤单按钮添加完成 - row: {row}, order_id: {order_id}")
                
        except Exception as e:
            self.logger.error(f"添加撤单按钮失败: {e}")

    def _apply_order_op_btn_style(self, button, kind: str) -> None:
        """订单列表操作按钮统一样式：cancel / monitor / monitoring / ended。"""
        try:
            from ui.trade_record_manager import (
                _STYLE_CANCEL,
                _STYLE_ENDED,
                _STYLE_MONITOR,
                _STYLE_MONITORING,
            )
            styles = {
                "cancel": _STYLE_CANCEL,
                "monitor": _STYLE_MONITOR,
                "monitoring": _STYLE_MONITORING,
                "ended": _STYLE_ENDED,
            }
            button.setStyleSheet(styles.get(kind, _STYLE_MONITOR))
        except Exception:
            pass

    def _handle_cancel_click(self, row, order_id):
        """处理撤单按钮点击事件"""
        #print(f"🔥🔥🔥 撤单按钮被点击！🔥🔥🔥 - row: {row}, order_id: {order_id}")
        self.cancel_order(row, order_id)

    def _handle_monitor_click(self, row, order_id):
        """处理监控按钮点击事件"""
        print(f"🔥🔥🔥 监控按钮被点击！🔥🔥🔥 - row: {row}, order_id: {order_id}")
        self.logger.info(f"监控按钮被点击: row={row}, order_id={order_id}")
        self.toggle_monitor(row, order_id)

    def _start_order_monitor(self, row, order_id, stock_code, order_status):
        """启动订单监控 - 类似夜间委托的监控逻辑"""
        try:
            # 将股票添加到订阅列表，确保能获取到价格数据
            if hasattr(self, 'qmt_adapter') and self.qmt_adapter:
                self.qmt_adapter.ensure_subscribed(stock_code)
            else:
                self.logger.error(f"[订单监控] qmt_adapter未初始化，无法添加股票到订阅列表")
            
            # 检查是否为夜市委托
            table = self.tableWidget_3
            strategy_item = table.item(row, 4)  # 第5列是策略类型
            strategy_type = strategy_item.text() if strategy_item else ""
            is_night_market = "夜市" in strategy_type
            
            # 创建监控任务数据
            monitor_task = {
                'order_id': order_id,
                'stock_code': stock_code,
                'row': row,
                'order_status': order_status,
                'monitor_start_time': datetime.now(),
                'is_monitoring': True,
                'tick_data': [],
                'limit_up_price': None,
                'limit_down_price': None,
                'check_timer': None,
                'is_night_market': is_night_market  # 添加夜市标识
            }
            
            # 保存监控任务到全局字典
            if not hasattr(self, 'order_monitors'):
                self.order_monitors = {}
            self.order_monitors[order_id] = monitor_task
            
            # 如果是夜市委托，启动涨停板检查定时器
            if is_night_market:
                self.logger.info(f"[订单监控] 夜市委托 {order_id} 启动涨停板检查定时器")
                self._start_night_market_limit_check_timer(order_id, stock_code)
            else:
                # 启动普通监控定时器
                self._start_monitor_timer(order_id)
            
        except Exception as e:
            self.logger.error(f"[订单监控] 启动订单监控失败: {order_id}, 错误: {str(e)}")
            import traceback
            self.logger.error(f"[订单监控] 详细错误: {traceback.format_exc()}")

    def _start_night_market_limit_check_timer(self, order_id, stock_code):
        """启动夜市委托的涨停板检查定时器"""
        try:
            from datetime import datetime, timedelta
            from strategies.night_market_strategy import get_morning_clear_time, get_morning_check_time
            
            # 计算到9点14分55秒的时间（清空tick数据）
            now = datetime.now()
            clear_time = get_morning_clear_time()
            
            # 如果今天已经过了清空时间，设置为明天
            if now >= clear_time:
                clear_time += timedelta(days=1)
            
            # 计算到9点17分55秒的时间（检查涨停板）
            check_time = get_morning_check_time()
            if now >= check_time:
                check_time += timedelta(days=1)
            
            # 计算等待时间（秒）
            clear_wait_seconds = (clear_time - now).total_seconds()
            check_wait_seconds = (check_time - now).total_seconds()
            
            self.logger.info(f"[夜市监控] {order_id} 涨停板检查定时器将在 {check_time.strftime('%Y-%m-%d %H:%M:%S')} 执行，等待 {check_wait_seconds:.1f} 秒")
            self.logger.info(f"[夜市监控] {order_id} 将在 {clear_time.strftime('%Y-%m-%d %H:%M:%S')} 清空tick数据，等待 {clear_wait_seconds:.1f} 秒")
            
            # 创建清空tick数据的定时器
            import threading
            clear_timer = threading.Timer(clear_wait_seconds, self._clear_night_market_tick_data, args=[order_id])
            clear_timer.daemon = True
            clear_timer.start()
            
            # 创建涨停板检查定时器
            check_timer = threading.Timer(check_wait_seconds, self._check_night_market_limit_and_cancel, args=[order_id, stock_code])
            check_timer.daemon = True
            check_timer.start()
            
            # 保存定时器引用
            if order_id in self.order_monitors:
                self.order_monitors[order_id]['clear_timer'] = clear_timer
                self.order_monitors[order_id]['check_timer'] = check_timer
            
        except Exception as e:
            self.logger.error(f"[夜市监控] 启动涨停板检查定时器失败：{str(e)}")
            import traceback
            self.logger.error(f"[夜市监控] 错误详情：{traceback.format_exc()}")

    def _clear_night_market_tick_data(self, order_id):
        """清空夜市委托的tick数据"""
        try:
            from strategies.night_market_strategy import get_morning_clear_time
            clear_time = get_morning_clear_time()
            self.logger.info(f"[夜市监控] {order_id} {clear_time.strftime('%H:%M:%S')}清空tick数据，准备接收新的实时数据")
            
            if order_id in self.order_monitors:
                self.order_monitors[order_id]['tick_data'] = []
        except Exception as e:
            self.logger.error(f"[夜市监控] 清空tick数据失败：{str(e)}")

    def _check_night_market_limit_and_cancel(self, order_id, stock_code):
        """检查夜市委托是否封涨停板，如果没有则撤单"""
        try:
            self.logger.info(f"[夜市监控] {order_id} 开始检查涨停板状态")
            
            # 获取订单信息
            if order_id not in self.order_monitors:
                self.logger.error(f"[夜市监控] {order_id} 不在监控列表中")
                return
            
            monitor_info = self.order_monitors[order_id]
            row = monitor_info.get('row')
            
            # 获取订单价格
            table = self.tableWidget_3
            if row >= table.rowCount():
                self.logger.error(f"[夜市监控] {order_id} 行号超出范围")
                return
            
            price_item = table.item(row, 4)  # 第5列是委托/均价
            if not price_item:
                self.logger.error(f"[夜市监控] {order_id} 无法获取订单价格")
                return
            
            order_price_text = price_item.text()
            try:
                # 解析价格，格式可能是 "委托价/均价"
                if '/' in order_price_text:
                    order_price = float(order_price_text.split('/')[0])
                else:
                    order_price = float(order_price_text)
            except ValueError:
                self.logger.error(f"[夜市监控] {order_id} 订单价格格式错误: {order_price_text}")
                return
            
            # 获取最新价格
            latest_price = self._get_latest_price_for_stock(stock_code)
            if latest_price is None:
                self.logger.error(f"[夜市监控] {order_id} 无法获取最新价格")
                return
            
            self.logger.info(f"[夜市监控] {order_id} 订单价格: {order_price}, 最新价格: {latest_price}")
            
            # 判断是否需要撤单
            if latest_price < order_price:
                self.logger.info(f"[夜市监控] {order_id} 最新价{latest_price} < 委托价{order_price}，执行撤单")
                self.cancel_order(row, order_id)
            else:
                self.logger.info(f"[夜市监控] {order_id} 最新价{latest_price} >= 委托价{order_price}，不撤单")
            
        except Exception as e:
            self.logger.error(f"[夜市监控] 检查涨停板状态失败：{str(e)}")
            import traceback
            self.logger.error(f"[夜市监控] 错误详情：{traceback.format_exc()}")

    def _get_latest_price_for_stock(self, stock_code):
        """获取股票的最新价格"""
        try:
            if hasattr(self, 'task_manager') and self.task_manager:
                latest_price = self.task_manager.get_latest_price(stock_code)
                if latest_price is not None and latest_price > 0:
                    return latest_price
                else:
                    self.logger.warning(f"[夜市监控] 无法获取股票 {stock_code} 的最新价格")
                    return None
            else:
                self.logger.error(f"[夜市监控] task_manager未初始化，无法获取股票价格")
                return None
        except Exception as e:
            self.logger.error(f"[夜市监控] 获取股票价格失败: {str(e)}")
            return None

    def _start_monitor_timer(self, order_id):
        """启动监控定时器 - 类似夜间委托的涨跌停板检查"""
        try:
            if not hasattr(self, 'order_monitors') or order_id not in self.order_monitors:
                return
            
            monitor_task = self.order_monitors[order_id]
            
            # 计算到明天9:17:55的时间
            now = datetime.now()
            h, m, s = self.MORNING_CHECK_TIME
            today_check_time = now.replace(hour=h, minute=m, second=s, microsecond=0)
            if now < today_check_time:
                next_check_time = today_check_time
            else:
                tomorrow = now + timedelta(days=1)
                next_check_time = tomorrow.replace(hour=h, minute=m, second=s, microsecond=0)
            
            # 计算延迟时间（毫秒）
            delay_ms = int((next_check_time - now).total_seconds() * 1000)
            
            # 创建监控定时器
            check_timer = QTimer()
            check_timer.timeout.connect(lambda: self._check_order_and_limit(order_id))
            check_timer.start(delay_ms)
            
            # 保存定时器引用
            monitor_task['check_timer'] = check_timer
            
            # 添加夜间委托的MORNING_CHECK_TIME检查
            self._start_morning_check_timer(order_id)
            
            self.logger.info(f"早盘检查定时器已启动，柜台合同号{order_id}，将在 {next_check_time.strftime('%Y-%m-%d %H:%M:%S')} 检查")
            
        except Exception as e:
            self.logger.error(f"启动监控定时器失败: {str(e)}")

    def _start_morning_check_timer(self, order_id):
        """启动早盘检查定时器 - 类似夜间委托的MORNING_CHECK_TIME"""
        try:
            if not hasattr(self, 'order_monitors') or order_id not in self.order_monitors:
                return
            
            monitor_task = self.order_monitors[order_id]
            
            # 计算到明天9:17:55的时间
            now = datetime.now()
            h, m, s = self.MORNING_CHECK_TIME
            today_check_time = now.replace(hour=h, minute=m, second=s, microsecond=0)
            if now < today_check_time:
                next_check_time = today_check_time
            else:
                tomorrow = now + timedelta(days=1)
                next_check_time = tomorrow.replace(hour=h, minute=m, second=s, microsecond=0)
            
            # 计算延迟时间（毫秒）
            delay_ms = int((next_check_time - now).total_seconds() * 1000)
            
            # 创建早盘检查定时器
            morning_timer = QTimer()
            morning_timer.setSingleShot(True)  # 只执行一次
            morning_timer.timeout.connect(lambda: self._morning_check_order(order_id))
            morning_timer.start(delay_ms)
            
            # 保存早盘检查定时器引用
            monitor_task['morning_check_timer'] = morning_timer
            
        except Exception as e:
            self.logger.error(f"启动早盘检查定时器失败: {str(e)}")

    def _morning_check_order(self, order_id):
        """早盘检查订单 - 类似夜间委托的涨跌停板检查"""
        try:
            if not hasattr(self, 'order_monitors') or order_id not in self.order_monitors:
                return
            
            monitor_task = self.order_monitors[order_id]
            if not monitor_task.get('is_monitoring', False):
                return
            
            stock_code = monitor_task['stock_code']
            row = monitor_task['row']
            
            self.logger.info(f"开始早盘检查订单: {order_id}, 股票代码: {stock_code}")
            
            # 检查订单状态是否已结束
            table = self.tableWidget_3
            if row < table.rowCount():
                order_status_item = table.item(row, 6)  # 第7列是订单状态
                current_status = order_status_item.text() if order_status_item else "未知"
                
                # 如果订单已结束，停止监控
                if any(status in current_status for status in ['已成', '已撤', '废单']):
                    self.logger.info(f"订单 {order_id} 已结束，停止早盘检查")
                    self._stop_order_monitor(order_id)
                    return
            
            # 获取最新价格和涨跌停板价格
            latest_price, limit_up_price, limit_down_price = self._get_stock_price_and_limits(stock_code)
            
            if latest_price and limit_up_price and limit_down_price:
                self.logger.info(f"早盘检查 - 股票: {stock_code}, 最新价: {latest_price}, 涨停价: {limit_up_price}, 跌停价: {limit_down_price}")
                
                # 检查是否需要撤单 - 类似夜间委托的逻辑
                should_cancel = self._should_cancel_order(
                    order_id, stock_code, latest_price, limit_up_price, limit_down_price
                )
                
                if should_cancel:
                    self.logger.info(f"早盘检查检测到需要撤单: {order_id}")
                    # 撤单成功后会自动调用 _stop_order_monitor，所以这里不需要重复调用
                    self._cancel_monitored_order(order_id)
                else:
                    self.logger.info(f"早盘检查完成，无需撤单: {order_id}")
                    # 只有不需要撤单时才手动停止监控
                    self._stop_order_monitor(order_id)
            else:
                self.logger.warning(f"早盘检查获取股票价格失败: {stock_code}")
                # 价格获取失败时也停止监控
                self._stop_order_monitor(order_id)

            # 更新按钮状态为已完成
            table = self.tableWidget_3
            if row < table.rowCount():
                monitor_button = table.cellWidget(row, 8)
                if monitor_button:
                    monitor_button.setText("已完成")
                    monitor_button.setEnabled(False)
                    monitor_button.setStyleSheet("""
                        QPushButton {
                            background-color: #4CAF50;
                            color: white;
                            border: 1px solid #4CAF50;
                            border-radius: 3px;
                        }
                    """)
            
        except Exception as e:
            self.logger.error(f"早盘检查订单失败: {str(e)}")

    def _check_order_and_limit(self, order_id):
        """检查订单状态和涨跌停板 - 类似夜间委托的检查逻辑"""
        try:
            if not hasattr(self, 'order_monitors') or order_id not in self.order_monitors:
                return
            
            monitor_task = self.order_monitors[order_id]
            if not monitor_task.get('is_monitoring', False):
                return
            
            stock_code = monitor_task['stock_code']
            row = monitor_task['row']
            
            # 1. 检查订单状态是否已结束
            table = self.tableWidget_3
            if row < table.rowCount():
                order_status_item = table.item(row, 6)  # 第7列是订单状态
                current_status = order_status_item.text() if order_status_item else "未知"
                
                # 如果订单已结束，停止监控
                if any(status in current_status for status in ['已成', '已撤', '废单']):
                    self.logger.info(f"订单 {order_id} 已结束，停止监控")
                    self._stop_order_monitor(order_id)
                    
                    # 更新按钮状态（cellWidget 是容器，需找真正的按钮）
                    operation_container = table.cellWidget(row, 8)
                    if operation_container:
                        cancel_button = operation_container.findChild(QPushButton, "cancel_button")
                        monitor_button = operation_container.findChild(QPushButton, "monitor_button")
                        for btn in (cancel_button, monitor_button):
                            if not btn:
                                continue
                            btn.setText("已结束")
                            btn.setEnabled(False)
                            self._apply_order_op_btn_style(btn, "ended")
                    return
            
            # 2. 检查是否在交易时间
            current_time = datetime.now().time()
            if not self._is_trading_time(current_time):
                return  # 非交易时间不检查涨跌停板
            
            # 3. 获取最新价格和涨跌停板价格
            latest_price, limit_up_price, limit_down_price = self._get_stock_price_and_limits(stock_code)
            
            if latest_price and limit_up_price and limit_down_price:
                # 4. 检查是否需要撤单 - 类似夜间委托的逻辑
                should_cancel = self._should_cancel_order(
                    order_id, stock_code, latest_price, limit_up_price, limit_down_price
                )
                
                if should_cancel:
                    self.logger.info(f"监控检测到需要撤单: {order_id}")
                    self._cancel_monitored_order(order_id)
            
        except Exception as e:
            self.logger.error(f"检查订单和涨跌停板失败: {str(e)}")

    def _is_trading_time(self, current_time):
        """检查是否在交易时间（须为 A 股交易日且处于连续竞价时段）。"""
        if not is_tradeday(datetime.now().date()):
            return False

        # 早盘：9:30-11:30
        morning_start = datetime_time(9, 30)
        morning_end = datetime_time(11, 30)
        
        # 午盘：13:00-15:00
        afternoon_start = datetime_time(13, 0)
        afternoon_end = datetime_time(15, 0)
        
        return ((morning_start <= current_time <= morning_end) or 
                (afternoon_start <= current_time <= afternoon_end))

    def _get_stock_price_and_limits(self, stock_code):
        """获取股票最新价格和涨跌停板价格"""
        try:
            # 直接从任务管理器获取价格信息
            if (hasattr(self, 'task_manager') and self.task_manager and 
                hasattr(self.task_manager, 'latest_prices') and 
                stock_code in self.task_manager.latest_prices):
                
                # 获取实时价格和昨收盘价
                latest_price = self.task_manager.get_latest_price(stock_code)
                pre_close_price = self.task_manager.get_pre_close_price(stock_code)
                
                #self.logger.info(f"XXXXXXXXXXXXXXXXXXXXXXX股票 {stock_code} 实时价={latest_price}, 昨收={pre_close_price}")
                
                if latest_price > 0 and pre_close_price > 0:
                    # 计算涨跌停板价格，传入昨收盘价
                    limit_up_price, limit_down_price = self._calculate_limit_prices(stock_code, latest_price, pre_close_price)
                    
                    return latest_price, limit_up_price, limit_down_price
                else:
                    self.logger.warning(f"股票 {stock_code} 价格数据不完整，实时价={latest_price}, 昨收={pre_close_price}")
                    return None, None, None
            else:
                self.logger.warning(f"股票 {stock_code} 未在订阅列表中，无法获取价格数据")
                return None, None, None
            
        except Exception as e:
            self.logger.error(f"获取股票价格和涨跌停板失败: {stock_code}, 错误: {e}")
            return None, None, None

    def _calculate_limit_prices(self, stock_code, current_price, pre_close_price):
        """计算涨跌停板价格"""
        try:
            # 使用传入的昨收盘价，不再从QMT获取
            pre_close = pre_close_price
            
            try:
                from utils.stock_info_manager import get_stock_name
                stock_name = get_stock_name(stock_code) or ""
            except Exception:
                stock_name = ""

            from utils.limit_ratio import get_limit_ratio
            limit_ratio = get_limit_ratio(stock_code, stock_name)
            
            # 计算涨跌停价格
            limit_up_price = pre_close * (1 + limit_ratio)
            limit_down_price = pre_close * (1 - limit_ratio)
            
            # 获取价格精度并四舍五入
            from core.utils.security_type import SecurityTypeUtil
            precision = SecurityTypeUtil.get_price_precision(stock_code)
            limit_up_price = round(limit_up_price, precision)
            limit_down_price = round(limit_down_price, precision)
            
            return limit_up_price, limit_down_price
            
        except Exception as e:
            self.logger.error(f"计算涨跌停板价格失败: {stock_code}, 错误: {e}")
            return None, None

    def _get_limit_prices(self, stock_code):
        """获取股票的涨跌停板价格（保持原接口兼容性）"""
        latest_price, limit_up_price, limit_down_price = self._get_stock_price_and_limits(stock_code)
        return limit_up_price, limit_down_price

    def _should_cancel_order(self, order_id, stock_code, latest_price, limit_up_price, limit_down_price):
        """判断是否应该撤单 - 类似夜间委托的逻辑"""
        try:
            # 获取订单信息
            table = self.tableWidget_3
            for row in range(table.rowCount()):
                order_id_item = table.item(row, 0)
                if order_id_item and order_id_item.text() == order_id:
                    # 获取委托价格
                    price_item = table.item(row, 5)  # 第7列是委托价格
                    order_price = float(price_item.text()) if price_item else 0
                    
                    # 获取订单类型
                    type_item = table.item(row, 4)  # 第5列是订单类型
                    order_type = type_item.text() if type_item else ""
                    
                    # 类似夜间委托的撤单逻辑
                    if '买入' in order_type:
                        # 买入订单：如果接近涨停价但未涨停，考虑撤单
                        if abs(order_price - limit_up_price) <= 0.01:
                            price_diff = abs(latest_price - limit_up_price)
                            if price_diff > 0.01:  # 未涨停
                                self.logger.info(f"涨停价买入订单在可撤单阶段未涨停，执行撤单: {order_id}")
                                return True
                    elif '卖出' in order_type:
                        # 卖出订单：如果接近跌停价但未跌停，考虑撤单
                        if abs(order_price - limit_down_price) <= 0.01:
                            price_diff = abs(latest_price - limit_down_price)
                            if price_diff > 0.01:  # 未跌停
                                self.logger.info(f"跌停价卖出订单在可撤单阶段未跌停，执行撤单: {order_id}")
                                return True
                    
                    break
            
            return False
            
        except Exception as e:
            self.logger.error(f"判断是否撤单失败: {str(e)}")
            return False

    def _cancel_monitored_order(self, order_id):
        """撤单被监控的订单"""
        try:
            # 找到对应的行
            table = self.tableWidget_3
            target_row = -1
            
            for row in range(table.rowCount()):
                order_id_item = table.item(row, 0)
                if order_id_item and order_id_item.text() == order_id:
                    target_row = row
                    break
            
            if target_row >= 0:
                # 调用撤单方法
                self.cancel_order(target_row, order_id)
                
                # 停止监控
                self._stop_order_monitor(order_id)
                
                self.logger.info(f"监控撤单完成: {order_id}")
            else:
                self.logger.warning(f"未找到要撤单的订单行: {order_id}")
                
        except Exception as e:
            self.logger.error(f"监控撤单失败: {str(e)}")

    def on_order_column_resized(self, logicalIndex, oldSize, newSize):
        """处理列宽改变事件，确保撤单列和监控列保持固定宽度"""
        try:
            # 确保撤单列（第9列）和监控列（第10列）保持固定宽度
            if logicalIndex == 9:
                self.tableWidget_3.setColumnWidth(logicalIndex, 120)  # 撤单列
            elif logicalIndex == 10:
                self.tableWidget_3.setColumnWidth(logicalIndex, 120)  # 监控列
                    
        except Exception as e:
            self.logger.error(f"处理列宽改变事件失败: {str(e)}")
            # 发生异常时也要确保固定列保持固定宽度
            try:
                self.tableWidget_3.setColumnWidth(9, 120)  # 撤单列
                self.tableWidget_3.setColumnWidth(10, 120)  # 监控列
            except:
                pass

    def refresh_order_list(self):
        """按需刷新订单列表（不再定时刷新，改为用户查看订单列表时主动调用）"""
        try:
            # 检查QMT适配器是否可用
            if not hasattr(self, 'qmt_adapter') or not self.qmt_adapter:
                self.logger.debug("QMT适配器未初始化，跳过订单列表刷新")
                return

            try:
                from utils.qmt_execution_config import use_builtin_price_feed

                if use_builtin_price_feed():
                    # builtin：从 results 回填，切勿先清空再查 xt_trader（查不到会一直空）
                    poller = getattr(self.qmt_adapter, "_builtin_price_poller", None)
                    if poller is not None:
                        poller._order_status_seen = {}
                        poller._orders_ui_bootstrapped = False
                        poller._apply_orders_snapshot()
                    else:
                        self.logger.debug("builtin 订单轮询未启动，跳过刷新")
                    return
            except Exception as e:
                self.logger.warning(f"builtin 刷新订单列表失败: {e}")

            # mini：全量刷新前清空，避免跨日旧行残留
            try:
                if hasattr(self, 'tableWidget_3') and self.tableWidget_3:
                    self.tableWidget_3.setRowCount(0)
            except Exception:
                pass
            
            # 调用QMT适配器的查询当日订单方法
            if hasattr(self.qmt_adapter, 'get_today_orders'):
                self.qmt_adapter.get_today_orders()
            else:
                self.logger.warning("QMT适配器没有get_today_orders方法")
                
        except Exception as e:
            self.logger.error(f"刷新订单列表失败: {str(e)}")

    def cancel_order(self, row, order_id):
        """执行撤单操作"""
        try:
            # 添加调试信息
            print(f"=== 撤单按钮被点击！行号: {row}, 订单ID: {order_id} ===")
            self.logger.info(f"撤单按钮被点击: row={row}, order_id={order_id}")
            
            if not self.qmt_adapter:
                self.logger.error("QMT适配器未初始化")
                return
            
            # 获取股票代码
            table = self.tableWidget_3
            
            # 检查行是否仍然有效
            if row >= table.rowCount():
                self.logger.warning(f"行索引{row}超出表格范围，可能表格已被重新排序")
                return
                
            stock_code_item = table.item(row, 1)  # 第2列是股票代码
            stock_code = stock_code_item.text() if stock_code_item else None
            
            #self.logger.info(f"开始撤单，订单号: {order_id}, 股票代码: {stock_code}")
            
            # 重新检查行是否有效（可能发生了重新排序）
            if row >= table.rowCount():
                self.logger.warning(f"撤单前，行索引{row}超出表格范围，表格可能已被重新排序")
                # 尝试根据order_id重新找到行
                new_row = self._find_row_by_order_id(order_id)
                if new_row is not None:
                    row = new_row
                    self.logger.info(f"根据订单号{order_id}重新找到行索引: {row}")
                else:
                    self.logger.warning(f"无法找到订单号{order_id}对应的行，取消撤单操作")
                    return
            
            # 保存当前滚动位置
            scrollbar = table.verticalScrollBar()
            current_scroll_position = scrollbar.value() if scrollbar else 0
            
            # 安全地获取操作容器和按钮对象
            def safe_get_operation_container(container_col):
                """安全地获取操作容器对象"""
                try:
                    if row >= table.rowCount():
                        return None
                    container = table.cellWidget(row, container_col)
                    if container is None:
                        return None
                    # 检查容器对象是否仍然有效
                    try:
                        _ = container.layout()  # 尝试访问容器属性
                        return container
                    except RuntimeError:
                        self.logger.warning("操作容器对象已被删除")
                        return None
                except Exception as e:
                    self.logger.warning(f"获取操作容器失败: {str(e)}")
                    return None
            
            # 先更新按钮状态为"撤单中"
            operation_container = safe_get_operation_container(8)  # 操作按钮在第8列
            if operation_container:
                # 直接从布局中获取按钮
                layout = operation_container.layout()
                if layout and layout.count() >= 2:
                    cancel_button = layout.itemAt(0).widget()  # 第一个按钮是撤单按钮
                    monitor_button = layout.itemAt(1).widget()  # 第二个按钮是监控按钮
                else:
                    cancel_button = None
                    monitor_button = None
                
                if cancel_button:
                    try:
                        cancel_button.setEnabled(False)
                        cancel_button.setText("撤单中")
                        cancel_button.setStyleSheet("""
                            QPushButton {
                                background-color: #FFA726;
                                color: white;
                                border: 1px solid #FFA726;
                                border-radius: 3px;
                            }
                        """)
                    except RuntimeError:
                        self.logger.warning("撤单按钮在更新状态时已被删除")
                        cancel_button = None
                
                # 同时禁用监控按钮
                if monitor_button:
                    try:
                        monitor_button.setEnabled(False)
                        monitor_button.setText("撤单中")
                        monitor_button.setStyleSheet("""
                            QPushButton {
                                background-color: #FFA726;
                                color: white;
                                border: 1px solid #FFA726;
                                border-radius: 3px;
                            }
                        """)
                    except RuntimeError:
                        self.logger.warning("监控按钮在更新状态时已被删除")
                        monitor_button = None
            
            # 调用QMT撤单接口，传递股票代码
            print(f"🔍 [订单列表撤单] 调用撤单接口 - 订单ID: {order_id} (类型: {type(order_id)}), 股票代码: {stock_code}")
            self.logger.info(f"[订单列表撤单] 调用撤单接口 - 订单ID: {order_id} (类型: {type(order_id)}), 股票代码: {stock_code}")
            
            # 获取订单表格中显示的订单号（第0列）
            order_id_item = table.item(row, 0)  # 第1列是订单号（合同号）
            displayed_order_id = order_id_item.text() if order_id_item else None
            print(f"🔍 [订单列表撤单] 表格显示的订单号: {displayed_order_id}, 传入的order_id: {order_id}")
            self.logger.info(f"[订单列表撤单] 表格显示的订单号: {displayed_order_id}, 传入的order_id: {order_id}")

            # 若是提前挂单：先打人工撤单标记，成功后节点变黑结束（系统自动撤单不会走此路径）
            try:
                view = getattr(self, "tasks_charts_view", None)
                if view is not None and hasattr(view, "mark_early_manual_cancel_pending"):
                    view.mark_early_manual_cancel_pending(stock_code, order_id)
            except Exception as e:
                self.logger.debug(f"[订单列表撤单] 标记提前单人工撤单失败: {e}")
            
            result = self.qmt_adapter.cancel_order(order_id, stock_code)
            
            print(f"🔍 [订单列表撤单] 撤单接口返回结果: {result}")
            self.logger.info(f"[订单列表撤单] 撤单接口返回结果: {result}")
            
            # 撤单完成后，重新获取操作容器（可能因为表格重新排序而改变）
            if row >= table.rowCount():
                # 表格已被重新排序，尝试根据order_id重新找到行
                new_row = self._find_row_by_order_id(order_id)
                if new_row is not None:
                    row = new_row
                    self.logger.info(f"撤单完成后重新找到行索引: {row}")
            
            # 重新安全地获取操作容器
            operation_container = safe_get_operation_container(8)
            
            if result:
                self.logger.info(f"撤单成功: {order_id}")
                try:
                    view = getattr(self, "tasks_charts_view", None)
                    if view is not None and hasattr(view, "finalize_early_manual_cancel_if_pending"):
                        if view.finalize_early_manual_cancel_if_pending(stock_code, order_id):
                            self.logger.info(
                                f"[订单列表撤单] 提前挂单已按人工撤单结束(黑节点): {stock_code} {order_id}"
                            )
                except Exception as e:
                    self.logger.debug(f"[订单列表撤单] 提前单人工撤单收尾失败: {e}")
                
                # 撤单成功后，更新按钮状态为"已结束"
                if operation_container:
                    # 直接从布局中获取按钮
                    layout = operation_container.layout()
                    if layout and layout.count() >= 2:
                        cancel_button = layout.itemAt(0).widget()  # 第一个按钮是撤单按钮
                        monitor_button = layout.itemAt(1).widget()  # 第二个按钮是监控按钮
                    else:
                        cancel_button = None
                        monitor_button = None
                    
                    if cancel_button:
                        try:
                            cancel_button.setEnabled(False)
                            cancel_button.setText("已结束")
                            self._apply_order_op_btn_style(cancel_button, "ended")
                        except RuntimeError:
                            self.logger.warning("撤单成功后，撤单按钮已被删除，无法更新状态")
                    
                    if monitor_button:
                        try:
                            monitor_button.setEnabled(False)
                            monitor_button.setText("已结束")
                            self._apply_order_op_btn_style(monitor_button, "ended")
                        except RuntimeError:
                            self.logger.warning("撤单成功后，监控按钮已被删除，无法更新状态")
                
                # 使用QTimer延迟恢复滚动位置，确保订单列表更新完成
                #300ms不够，500ms还没试过。
                QTimer.singleShot(500, lambda: self._restore_scroll_position(table, current_scroll_position))
                
            else:
                self.logger.error(f"撤单失败: {order_id}")
                
                # 撤单失败后，恢复按钮状态
                if operation_container:
                    cancel_button = operation_container.findChild(QPushButton, "cancel_button")
                    monitor_button = operation_container.findChild(QPushButton, "monitor_button")
                    
                    if not cancel_button or not monitor_button:
                        layout = operation_container.layout()
                        if layout:
                            cancel_button = layout.itemAt(0).widget() if layout.count() > 0 else None
                            monitor_button = layout.itemAt(1).widget() if layout.count() > 1 else None
                    
                    if cancel_button:
                        try:
                            cancel_button.setEnabled(True)
                            cancel_button.setText("撤单")
                            self._apply_order_op_btn_style(cancel_button, "cancel")
                        except RuntimeError:
                            self.logger.warning("撤单失败后，撤单按钮已被删除，无法恢复状态")
                    
                    if monitor_button:
                        try:
                            monitor_button.setEnabled(True)
                            monitor_button.setText("监控")
                            self._apply_order_op_btn_style(monitor_button, "monitor")
                        except RuntimeError:
                            self.logger.warning("撤单失败后，监控按钮已被删除，无法恢复状态")
                
        except Exception as e:
            self.logger.error(f"撤单操作失败: {str(e)}")
            QMessageBox.critical(self.window, "错误", f"撤单操作失败: {str(e)}")
            
            # 发生异常时，不尝试恢复按钮状态，因为按钮对象可能已被删除
            # 表格重新排序时会自动重新创建按钮并设置正确的状态

    def _find_row_by_order_id(self, order_id):
        """根据订单号在表格中查找对应的行"""
        try:
            table = self.tableWidget_3
            for row in range(table.rowCount()):
                order_id_item = table.item(row, 0)  # 第1列是订单号
                if order_id_item and order_id_item.text() == order_id:
                    return row
            return None
        except Exception as e:
            self.logger.error(f"根据订单号查找行失败: {str(e)}")
            return None

    def toggle_monitor(self, row, order_id):
        """切换监控状态 - 实现类似夜间委托的监控逻辑"""
        try:
            # 添加调试信息
            self.logger.info(f"toggle_monitor被调用: row={row}, order_id={order_id}")
            print(f"🔥🔥🔥 toggle_monitor被调用！🔥🔥🔥 - row: {row}, order_id: {order_id}")
            
            if not self.qmt_adapter:
                QMessageBox.warning(self.window, "警告", "QMT适配器未初始化")
                return
            
            # 获取股票代码和订单信息
            table = self.tableWidget_3
            stock_code_item = table.item(row, 1)  # 第2列是股票代码
            stock_code = stock_code_item.text() if stock_code_item else None
            
            # 获取订单状态
            order_status_item = table.item(row, 6)  # 第7列是订单状态
            order_status = order_status_item.text() if order_status_item else "未知"
            
            # 获取当前监控按钮 - 修复查找逻辑
            operation_container = table.cellWidget(row, 8)  
            if not operation_container:
                self.logger.error(f"找不到操作容器 - row: {row}")
                return
            
            # 从容器中获取监控按钮 - 使用正确的查找方式
            monitor_button = operation_container.findChild(QPushButton, "monitor_button")
            if not monitor_button:
                # 备用查找方式：通过布局查找
                layout = operation_container.layout()
                if layout and layout.count() > 1:
                    monitor_button = layout.itemAt(1).widget()
            
            if not monitor_button:
                self.logger.error(f"找不到监控按钮 - row: {row}")
                return
            
            # 获取当前监控状态
            current_text = monitor_button.text()
            self.logger.info(f"当前监控按钮文本: {current_text}")
            
            # 检查订单状态，如果是已结束状态，不允许切换监控
            if any(status in order_status for status in ['已成', '已撤', '废单']):
                self.logger.info(f"订单状态为已结束状态: {order_status}，不允许切换监控")
                QMessageBox.information(self.window, "提示", "订单已结束，无法切换监控状态")
                return
            
            if current_text == "监控":
                # 切换到监控状态
                monitor_button.setText("监控中")
                self._apply_order_op_btn_style(monitor_button, "monitoring")
                
                # 创建监控任务 - 类似夜间委托的监控逻辑
                self._start_order_monitor(row, order_id, stock_code, order_status)
                
                self.logger.info(f"开启订单监控: 订单号={order_id}, 股票代码={stock_code}")
                
            elif current_text == "监控中":
                # 切换到关闭监控状态
                monitor_button.setText("监控")
                self._apply_order_op_btn_style(monitor_button, "monitor")
                
                # 停止监控任务
                self._stop_order_monitor(order_id)
                
                self.logger.info(f"关闭订单监控: 订单号={order_id}, 股票代码={stock_code}")
            
        except Exception as e:
            self.logger.error(f"切换监控状态失败: {str(e)}")
            QMessageBox.critical(self.window, "错误", f"切换监控状态失败: {str(e)}")

    def _set_running_task_ui_style(self, row):
        """设置运行中任务的UI样式"""
        try:
            table = self.tableWidget_2
            
            # 获取任务ID
            stock_code_item = table.item(row, 0)
            if not stock_code_item or not stock_code_item.data(Qt.UserRole):
                return
            task_id = stock_code_item.data(Qt.UserRole)
            
            # 更新状态列颜色
            status_item = table.item(row, 6)  # 状态列是索引6
            if status_item:
                status_item.setForeground(Qt.red)  # 设置文字颜色为红色
            
            # 设置可编辑列不可编辑，并改变样式
            for col in [4]:  # 基准价
                item = table.item(row, col)
                if item:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    # 设置灰色背景
                    item.setBackground(Qt.lightGray)
            
            # 设置整行灰色背景
            for col in range(table.columnCount()):
                if col in [4, 5, 7]:  # 策略列、参数列、操作列
                    widget = table.cellWidget(row, col)
                    if widget:
                        widget.setStyleSheet("""
                            QWidget {
                                background-color: lightGray;
                                margin: 0px;
                                padding: 0px;
                            }
                        """)
                        # 设置布局边距为0
                        if widget.layout():
                            widget.layout().setContentsMargins(0, 0, 0, 0)
                else:
                    item = table.item(row, col)
                    if item:
                        item.setBackground(Qt.lightGray)
            
            # 策略列现在是普通文本，不需要特殊处理
            
            # 设置参数按钮不可点击（对于夜市任务已被禁用）
            # 重新获取策略信息
            task_data = self.task_manager.tasks.get(task_id)
            if task_data:
                strategy = task_data.get('strategy', '')
                is_night_task = '夜市' in strategy
                is_buy_sell_strategy = False
                if not is_night_task and not is_buy_sell_strategy:
                    param_button = table.cellWidget(row, 5)
                    if param_button:
                        # 检查是否为规则任务
                        is_universal_strategy = self._is_rule_task_strategy(strategy)
                        
                        if not is_universal_strategy:
                            # 非规则任务在运行中时禁用参数按钮
                            param_button.setEnabled(False)
                            param_button.setStyleSheet("""
                                QPushButton {
                                    background-color: lightGray;
                                    margin: 0px;
                                    padding: 0px;
                                    border: 1px solid #CCCCCC;
                                    color: black;
                                }
                            """)
                        else:
                            # 规则任务在运行中时保持参数按钮可用
                            param_button.setEnabled(True)
                            param_button.setStyleSheet("""
                                QPushButton { 
                                    background-color: white; 
                                    color: blue; 
                                    text-decoration: underline; 
                                    text-align: center;
                                    margin: 0px;
                                    padding: 0px;
                                    border: 1px solid #CCCCCC;
                                }
                            """)
            
            # 更新按钮状态
            operation_widget = table.cellWidget(row, 7)
            if operation_widget:
                start_button = operation_widget.layout().itemAt(0).widget()
                stop_button = operation_widget.layout().itemAt(1).widget()
                start_button.setEnabled(False)  # 禁用启动按钮
                stop_button.setEnabled(True)    # 启用停止按钮
                
        except Exception as e:
            self.logger.error(f"设置运行中任务UI样式失败: {str(e)}")

    def _set_delegated_task_ui_style(self, row):
        """设置已委托任务的UI样式"""
        try:
            table = self.tableWidget_2
            
            # 更新状态列颜色
            status_item = table.item(row, 6)  # 状态列是索引6
            if status_item:
                status_item.setForeground(Qt.blue)  # 设置文字颜色为蓝色
            
            # 更新按钮状态
            operation_widget = table.cellWidget(row, 7)
            if operation_widget:
                start_button = operation_widget.layout().itemAt(0).widget()
                stop_button = operation_widget.layout().itemAt(1).widget()
                start_button.setEnabled(True)   # 启用启动按钮（可以重新启动）
                stop_button.setEnabled(False)   # 禁用停止按钮（已经不在运行中）
                
                # 设置布局边距为0，确保按钮分开显示（与stop_task方法保持一致）
                if operation_widget.layout():
                    operation_widget.layout().setContentsMargins(0, 0, 0, 0)
                
        except Exception as e:
            self.logger.error(f"设置已委托任务UI样式失败: {str(e)}")

    def _restore_scroll_position(self, table, scroll_position):
        """恢复表格的滚动位置"""
        try:
            scrollbar = table.verticalScrollBar()
            if scrollbar:
                scrollbar.setValue(scroll_position)
        except Exception as e:
            self.logger.warning(f"恢复滚动位置失败: {str(e)}")

    def refresh_price_display_for_stock(self, stock_code):
        """基准价变化后，主动刷新状态栏阈值显示"""
        self.logger.info(f"[{stock_code}] 开始刷新状态栏阈值显示")
        
        # 显示所有相关任务的基准价
        self.logger.info(f"[{stock_code}] 所有相关任务:")
        for task_id, task in self.task_manager.tasks.items():
            if task.get('stock_code') == stock_code:
                self.logger.info(f"[{stock_code}] 任务{task_id}: 基准价={task.get('base_price', 0):.3f}, 参数={task.get('params', {})}")
        
        # 优先获取运行中的任务
        running_task = None
        if hasattr(self.task_manager, 'running_tasks'):
            for task_id in self.task_manager.running_tasks:
                task = self.task_manager.tasks.get(task_id)
                if task and task.get('stock_code') == stock_code:
                    running_task = task
                    self.logger.info(f"[{stock_code}] 找到运行中任务: {task_id}, 基准价={task.get('base_price', 0):.3f}")
                    break
        
        # 如果没有运行中的任务，获取任意任务
        if not running_task:
            running_task = self.task_manager.get_task_by_stock_code(stock_code)
            self.logger.info(f"[{stock_code}] 使用任意任务，基准价={running_task.get('base_price', 0):.3f}" if running_task else f"[{stock_code}] 未找到任务")
        
        if not running_task:
            self.logger.warning(f"[{stock_code}] 未找到任务，无法刷新状态栏")
            return
            
        params = running_task.get('params', {})
        base_price = running_task.get('base_price', 0)
        current_price = self.task_manager.get_latest_price(stock_code) or 0
        
        # 添加调试信息，显示任务中的参数
        self.logger.info(f"[{stock_code}] 任务参数详情: {params}")
        self.logger.info(f"[{stock_code}] 使用任务基准价: {base_price}")
        
        # 直接使用任务中的基准价和参数计算阈值
        up_threshold = float(params.get('up_threshold', 5.0)) / 100
        down_threshold = float(params.get('down_threshold', 3.0)) / 100
        up_price = base_price * (1 + up_threshold) if base_price > 0 else 0
        down_price = base_price * (1 - down_threshold) if base_price > 0 else 0
        
        # 添加详细的计算过程日志
        self.logger.info(f"[{stock_code}] 阈值计算详情:")
        self.logger.info(f"[{stock_code}] - 基准价: {base_price:.3f}")
        self.logger.info(f"[{stock_code}] - 上限阈值参数: {params.get('up_threshold', 5.0)}%")
        self.logger.info(f"[{stock_code}] - 下限阈值参数: {params.get('down_threshold', 3.0)}%")
        self.logger.info(f"[{stock_code}] - 上限阈值计算: {base_price:.3f} * (1 + {up_threshold:.4f}) = {up_price:.3f}")
        self.logger.info(f"[{stock_code}] - 下限阈值计算: {base_price:.3f} * (1 - {down_threshold:.4f}) = {down_price:.3f}")
        
        precision = 2
        display_text = f"{current_price:.{precision}f} [{up_price:.{precision}f}/{down_price:.{precision}f}]"
        self.task_manager.price_displays[stock_code] = display_text
        self.logger.info(f"[{stock_code}] 状态栏刷新 - 当前价:{current_price:.3f}, 任务基准价:{base_price:.3f}, 上限阈值:{up_threshold*100:.1f}%, 下限阈值:{down_threshold*100:.1f}%, 显示:{display_text}")
        self.update_status_bar()

    def update_cancel_button(self, table, row, order_status, order_id):
        """更新指定行的撤单和监控按钮状态 - 重构版本"""
        try:
            # 检查是否已经在监控中
            is_monitoring = order_id in self.order_monitors
            
            # 获取操作容器
            operation_container = table.cellWidget(row, 8)
            if not operation_container:
                return
            
            # 使用 trade_record_manager 更新基本按钮状态
            self.trade_record_manager.update_operation_widget(operation_container, order_status)
            
            # 获取监控按钮并更新监控状态
            monitor_button = operation_container.findChild(QPushButton, "monitor_button")
            if monitor_button:
                if any(status in order_status for status in ['已成', '已撤', '废单']):
                    monitor_button.setEnabled(False)
                    monitor_button.setText("已结束")
                    self._apply_order_op_btn_style(monitor_button, "ended")
                else:
                    monitor_button.setEnabled(True)
                    if is_monitoring:
                        monitor_button.setText("监控中")
                        self._apply_order_op_btn_style(monitor_button, "monitoring")
                    else:
                        monitor_button.setText("监控")
                        self._apply_order_op_btn_style(monitor_button, "monitor")
            
        except Exception as e:
            self.logger.error(f"更新撤单按钮失败: {e}")

    def _stop_order_monitor(self, order_id):
        """停止订单监控"""
        try:
            #self.logger.info(f"[订单监控] 停止监控订单: {order_id}")
            
            if order_id not in self.order_monitors:
                self.logger.warning(f"[订单监控] 订单 {order_id} 未在监控列表中")
                return
            
            monitor_info = self.order_monitors[order_id]
            stock_code = monitor_info.get('stock_code')
            
            # 停止定时器
            for timer_key in ['monitor_timer', 'morning_timer', 'clear_timer', 'check_timer']:
                if (timer_key in monitor_info and 
                    monitor_info[timer_key] is not None and
                    hasattr(monitor_info[timer_key], 'stop')):
                    try:
                        monitor_info[timer_key].stop()
                    except RuntimeError:
                        # 如果定时器在错误的线程中，忽略错误
                        pass
                    monitor_info[timer_key] = None
                    self.logger.debug(f"[订单监控] 已停止 {order_id} 的 {timer_key}")
            
            # 设置监控状态为False
            monitor_info['is_monitoring'] = False
            self.logger.info(f"[订单监控] 订单 {order_id} 监控状态已设置为False")
            
            # 清理price_displays中的显示信息
            if stock_code and hasattr(self, 'task_manager') and self.task_manager:
                if hasattr(self.task_manager, 'price_displays') and stock_code in self.task_manager.price_displays:
                    # 检查该股票是否还有其他用途（任务或其他监控）
                    has_other_usage = False
                    
                    # 检查是否有任务在使用这个股票
                    task_info = self.task_manager.get_task_by_stock_code(stock_code)
                    if task_info:
                        has_other_usage = True
                        self.logger.debug(f"[订单监控] 股票 {stock_code} 仍有任务在使用，保留显示")
                    
                    # 检查是否有其他订单监控在使用这个股票
                    if not has_other_usage:
                        for other_order_id, other_monitor in self.order_monitors.items():
                            if (other_order_id != order_id and 
                                other_monitor.get('stock_code') == stock_code and 
                                other_monitor.get('is_monitoring', False)):
                                has_other_usage = True
                                self.logger.debug(f"[订单监控] 股票 {stock_code} 仍有其他订单监控 {other_order_id} 在使用，保留显示")
                                break
                    
                    if not has_other_usage:
                        # 没有其他用途，从price_displays中删除
                        del self.task_manager.price_displays[stock_code]
                        #self.logger.info(f"[订单监控] 已从状态栏清除股票 {stock_code} 的显示")
                        
                        # 主动更新状态栏
                        self.update_status_bar()
                        self.logger.debug(f"[订单监控] 已触发状态栏更新")
                    else:
                        self.logger.debug(f"[订单监控] 股票 {stock_code} 仍有其他用途，保留状态栏显示")
            
            # 智能清理订阅
            if stock_code:
                self._cleanup_stock_subscription(stock_code)
                
        except Exception as e:
            self.logger.error(f"[订单监控] 停止监控订单 {order_id} 失败: {str(e)}")
            import traceback
            self.logger.error(f"[订单监控] 详细错误: {traceback.format_exc()}")

    def _cleanup_stock_subscription(self, stock_code):
        """清理股票订阅 - 如果股票不在持仓、任务或其他监控中，则取消订阅"""
        try:
            if not stock_code:
                return
            
            # 检查股票是否还需要订阅
            should_keep_subscription = False
            
            # 1. 检查是否在持仓中
            if hasattr(self, 'qmt_adapter') and self.qmt_adapter:
                positions = getattr(self.qmt_adapter, 'cached_positions', {})
                if stock_code in positions:
                    should_keep_subscription = True
                    self.logger.debug(f"股票 {stock_code} 在持仓中，保持订阅")
            
            # 2. 检查是否在任务管理器的任务中
            if not should_keep_subscription and hasattr(self, 'task_manager') and self.task_manager:
                for task in self.task_manager.tasks.values():
                    if task.get('stock_code') == stock_code:
                        should_keep_subscription = True
                        self.logger.debug(f"股票 {stock_code} 在任务列表中，保持订阅")
                        break
            
            # 3. 检查是否在其他订单监控中
            if not should_keep_subscription and hasattr(self, 'order_monitors'):
                for monitor in self.order_monitors.values():
                    if monitor.get('stock_code') == stock_code and monitor.get('is_monitoring', False):
                        should_keep_subscription = True
                        self.logger.debug(f"股票 {stock_code} 在其他订单监控中，保持订阅")
                        break
            
            # 4. 如果不需要保持订阅，则从订阅列表中移除，并清理状态栏显示
            if not should_keep_subscription:
                # 清理price_displays中的显示信息
                if hasattr(self, 'task_manager') and self.task_manager:
                    if stock_code in self.task_manager.price_displays:
                        del self.task_manager.price_displays[stock_code]
                        self.logger.debug(f"已从状态栏移除股票 {stock_code} 的显示")
                
                if hasattr(self, 'qmt_adapter') and self.qmt_adapter:
                    self.qmt_adapter.remove_subscribe_codes([stock_code])
                
                # 更新状态栏
                if hasattr(self, 'update_status_bar'):
                    self.update_status_bar()
            else:
                self.logger.debug(f"股票 {stock_code} 仍需要订阅，保持订阅状态")
            
        except Exception as e:
            self.logger.error(f"清理股票订阅失败: {stock_code}, 错误: {str(e)}")

    def exit_application(self):
        """退出应用程序"""
        try:
            # 检查是否有正在运行的任务
            if self.has_running_tasks():
                reply = QMessageBox.warning(
                    self.window,
                    "警告",
                    "还有正在运行的任务，请先停止所有任务再退出程序。",
                    QMessageBox.Ok
                )
                return
            
            # 设置关闭标志
            self._is_closing = True
            
            # 在主线程中安全地停止定时器
            self._safe_stop_timers()
            
            # 保存任务（若任务文件已被策略生成系统等修改，则不覆盖，避免丢失新任务）
            if hasattr(self, 'task_manager') and self.task_manager:
                try:
                    self.task_manager._block_tasks_updated_signal = True
                    result = self.task_manager.save_tasks(list(self.task_manager.tasks.values()))
                    if result == "externally_modified":
                        QMessageBox.information(
                            self.window,
                            "提示",
                            "任务文件已被策略生成系统等程序修改，未覆盖。\n重新打开程序后将看到最新任务。"
                        )
                except Exception as e:
                    self.logger.error(f"保存任务失败: {str(e)}")
                finally:
                    self.task_manager._block_tasks_updated_signal = False
            
            # 停止行情订阅
            if hasattr(self, 'qmt_adapter') and self.qmt_adapter:
                try:
                    self.qmt_adapter.stop_quote_feed()
                except Exception as e:
                    self.logger.error(f"停止行情订阅失败: {str(e)}")
            
            # 清理订单监控（简化处理，避免线程问题）
            if hasattr(self, 'order_monitors'):
                for order_id in list(self.order_monitors.keys()):
                    try:
                        monitor_info = self.order_monitors[order_id]
                        # 直接设置监控状态为False，不调用复杂的停止逻辑
                        monitor_info['is_monitoring'] = False
                        # 安全地停止定时器
                        for timer_key in ['monitor_timer', 'morning_timer', 'clear_timer', 'check_timer']:
                            if (timer_key in monitor_info and 
                                monitor_info[timer_key] is not None and
                                hasattr(monitor_info[timer_key], 'stop')):
                                try:
                                    monitor_info[timer_key].stop()
                                except RuntimeError:
                                    pass  # 忽略线程错误
                                monitor_info[timer_key] = None
                    except Exception as e:
                        self.logger.error(f"清理订单监控{order_id}失败: {str(e)}")
            
            self.logger.info("程序退出")
            
            # 关闭窗口
            self.window.close()
                
        except Exception as e:
            self.logger.error(f"退出应用程序失败: {str(e)}")
            # 如果出现异常，强制关闭
            try:
                self.window.close()
            except:
                pass

    def _safe_stop_timers(self):
        """安全地停止所有定时器"""
        try:
            # 停止状态栏更新定时器
            if hasattr(self, 'status_timer') and self.status_timer is not None:
                try:
                    if hasattr(self.status_timer, 'timeout'):
                        self.status_timer.timeout.disconnect()
                except (RuntimeError, TypeError, AttributeError):
                    pass
                try:
                    if hasattr(self.status_timer, 'stop'):
                        self.status_timer.stop()
                except (RuntimeError, AttributeError):
                    pass
            
            # 停止订单刷新定时器
            if hasattr(self, 'order_refresh_timer') and self.order_refresh_timer is not None:
                try:
                    if hasattr(self.order_refresh_timer, 'stop'):
                        self.order_refresh_timer.stop()
                except (RuntimeError, AttributeError):
                    pass
            
            # 停止列宽检查定时器
            if hasattr(self, 'column_check_timer') and self.column_check_timer is not None:
                try:
                    if hasattr(self.column_check_timer, 'stop'):
                        self.column_check_timer.stop()
                except (RuntimeError, AttributeError):
                    pass
            
            # 停止延迟保存定时器
            if hasattr(self, '_save_timer') and self._save_timer is not None:
                try:
                    if hasattr(self._save_timer, 'stop'):
                        self._save_timer.stop()
                except (RuntimeError, AttributeError):
                    pass
                    
        except Exception as e:
            self.logger.error(f"停止定时器失败: {str(e)}")


    def show_parameter_settings(self):
        """显示参数设置"""
        try:
            self.logger.info("打开参数设置功能")
            from .dialogs import ParameterSettingsDialog
            
            # 创建并显示参数设置对话框（非模态）
            dialog = ParameterSettingsDialog(self.window)
            dialog.setWindowModality(Qt.NonModal)  # 设置为非模态
            dialog.show()  # 使用show()而不是exec_()
            
        except Exception as e:
            self.logger.error(f"显示参数设置失败: {str(e)}")
            QMessageBox.critical(
                self.window,
                "错误",
                f"显示参数设置失败: {str(e)}",
                QMessageBox.Ok
            )

    def enable_charts_view_mode(self):
        """启用图表视图模式 - 将tableWidget_2替换为图表视图"""
        try:
            # 检查必要的属性
            if not hasattr(self, 'task_manager') or self.task_manager is None:
                self.logger.warning("task_manager 未设置，跳过图表视图初始化")
                return
                
            if not hasattr(self, 'qmt_adapter') or self.qmt_adapter is None:
                self.logger.warning("qmt_adapter 未设置，跳过图表视图初始化")
                return
            
            # 检查是否已经初始化过了
            if hasattr(self, 'tasks_charts_view') and self.tasks_charts_view:
                # 确保图表视图是显示的
                self.tasks_charts_view.show()
                self.tasks_charts_view.raise_()
                return
                
            # 创建图表视图
            charts_view = TasksChartsView(
                task_manager=self.task_manager,
                qmt_adapter=self.qmt_adapter,
                parent=self.window
            )
            
            # 将图表视图插入到splitter中（替换tableWidget_2）
            splitter = self.splitter
            
            # 遍历找到tableWidget_2的位置
            target_index = -1
            for i in range(splitter.count()):
                widget = splitter.widget(i)
                if widget == self.tableWidget_2:
                    target_index = i
                    break
            
            if target_index >= 0:
                # 先隐藏原来的tableWidget_2
                self.tableWidget_2.hide()
                
                # 使用replaceWidget方法替换widget
                splitter.replaceWidget(target_index, charts_view)
                
                # 设置拉伸比例
                splitter.setStretchFactor(target_index, 7)
            else:
                self.logger.warning("未找到tableWidget_2，无法替换")
                return
            
            # 存储引用
            self.tasks_charts_view = charts_view
            
            # 强制显示图表视图
            charts_view.show()
            charts_view.raise_()
            charts_view.setVisible(True)
            
        except Exception as e:
            self.logger.error(f"启用图表视图失败: {str(e)}", exc_info=True)
            import traceback
            traceback.print_exc()