from PyQt5.QtCore import pyqtSlot, Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtWidgets import QProgressBar, QWidget, QHBoxLayout, QTableWidgetItem, QComboBox, QPushButton, QMenu, QDialog, QVBoxLayout, QLabel, QSpinBox, QDoubleSpinBox, QDialogButtonBox, QStyle, QSplitter, QTextEdit, QMessageBox, QDateEdit, QGridLayout, QGroupBox, QLineEdit, QFileDialog, QAction
from .main_window import Ui_mainWindow
import pandas as pd
import os
from datetime import datetime, timedelta
import logging
from core.task_manager import TaskManager
from utils.trading_day import is_tradeday
from PyQt5.QtWidgets import QApplication
from ui.custom_text_edit import AutoScrollTextEdit
from .backtest_window import Ui_MainWindow  # 改用 backtest_window
import time
from xtquant import xtdata
from core.backtest_manager import BacktestManager
import json
from utils.logger import Logger
from core.backtest_engine import BacktestEngine
import traceback
import configparser

def parse_date_auto(date_str):
    """自动解析多种格式的日期字符串
    Args:
        date_str: 日期字符串
    Returns:
        datetime对象
    """
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S'):
        try:
            return datetime.strptime(str(date_str), fmt)
        except Exception:
            continue
    raise ValueError(f"无法识别的日期格式: {date_str}")

class ParameterDialog(QDialog):
    """参数设置对话框"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("策略参数设置")
        # 去掉右上角的问号（帮助按钮）
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setup_ui()
        
    def setup_ui(self):
        # 创建主布局
        main_layout = QVBoxLayout()
        
        # 卖出次数设置
        sell_times_group = QGroupBox("分几次卖出（用逗号分隔，例如：1,3,5）")
        sell_times_layout = QVBoxLayout()
        self.sell_times_input = QLineEdit()
        self.sell_times_input.setPlaceholderText("例如：1,3,5")
        sell_times_layout.addWidget(self.sell_times_input)
        sell_times_group.setLayout(sell_times_layout)
        main_layout.addWidget(sell_times_group)
        
        # 清仓天数设置
        max_days_group = QGroupBox("清仓天数")
        max_days_layout = QVBoxLayout()
        self.max_days_input = QLineEdit()
        self.max_days_input.setPlaceholderText("例如：2")
        max_days_layout.addWidget(self.max_days_input)
        max_days_group.setLayout(max_days_layout)
        main_layout.addWidget(max_days_group)
        
        # 上升阈值设置
        up_threshold_group = QGroupBox("上升阈值（%）")
        up_threshold_layout = QVBoxLayout()
        self.up_threshold_input = QLineEdit()
        self.up_threshold_input.setPlaceholderText("例如：1.0000")
        up_threshold_layout.addWidget(self.up_threshold_input)
        up_threshold_group.setLayout(up_threshold_layout)
        main_layout.addWidget(up_threshold_group)
        
        # 下降阈值设置
        down_threshold_group = QGroupBox("下降阈值（%）")
        down_threshold_layout = QVBoxLayout()
        self.down_threshold_input = QLineEdit()
        self.down_threshold_input.setPlaceholderText("例如：1.0000")
        down_threshold_layout.addWidget(self.down_threshold_input)
        down_threshold_group.setLayout(down_threshold_layout)
        main_layout.addWidget(down_threshold_group)
        
        # 添加示例按钮
        example_button = QPushButton("显示示例")
        example_button.clicked.connect(self.show_example)
        main_layout.addWidget(example_button)
        
        # 添加验证按钮
        validate_button = QPushButton("验证参数")
        validate_button.clicked.connect(self.validate_parameters)
        main_layout.addWidget(validate_button)
        
        # 添加清空按钮
        clear_button = QPushButton("清空输入")
        clear_button.clicked.connect(self.clear_inputs)
        main_layout.addWidget(clear_button)
        
        # 添加确定取消按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main_layout.addWidget(buttons)
        
        self.setLayout(main_layout)
        
    def show_example(self):
        """显示示例参数"""
        self.sell_times_input.setText("1,3,5")
        self.max_days_input.setText("2")
        self.up_threshold_input.setText("1.0000")
        self.down_threshold_input.setText("1.0000")
        
    def clear_inputs(self):
        """清空输入"""
        self.sell_times_input.clear()
        self.max_days_input.clear()
        self.up_threshold_input.clear()
        self.down_threshold_input.clear()
        
    def validate_parameters(self):
        """验证参数输入"""
        try:
            # 获取并验证卖出次数
            sell_times = self.parse_input(self.sell_times_input.text(), int)
            if not all(1 <= x <= 99 for x in sell_times):
                raise ValueError("卖出次数必须在1-99之间")
                
            # 获取并验证清仓天数
            max_days = self.parse_input(self.max_days_input.text(), int)
            if not all(1 <= x <= 99 for x in max_days):
                raise ValueError("清仓天数必须在1-99之间")
                
            # 获取并验证上升阈值
            up_threshold = self.parse_input(self.up_threshold_input.text(), float)
            if not all(0 <= x <= 99 for x in up_threshold):
                raise ValueError("上升阈值必须在0-99之间")
                
            # 获取并验证下降阈值
            down_threshold = self.parse_input(self.down_threshold_input.text(), float)
            if not all(0 <= x <= 99 for x in down_threshold):
                raise ValueError("下降阈值必须在0-99之间")
                
            QMessageBox.information(self, "验证通过", "所有参数输入有效！")
            
        except ValueError as e:
            QMessageBox.warning(self, "参数错误", str(e))
            
    def parse_input(self, text, type_func):
        """解析输入文本为指定类型的列表"""
        if not text.strip():
            return []
        try:
            # 将中文逗号替换为英文逗号
            text = text.replace('，', ',')
            # 分割并转换
            values = [type_func(x.strip()) for x in text.split(',') if x.strip()]
            # 去除重复值并排序
            return sorted(list(set(values)))
        except ValueError:
            raise ValueError(f"输入格式错误，请使用逗号分隔的数字")
            
    def get_batch_parameters(self):
        """获取批量参数组合"""
        try:
            sell_times = self.parse_input(self.sell_times_input.text(), int)
            max_days = self.parse_input(self.max_days_input.text(), int)
            up_threshold = self.parse_input(self.up_threshold_input.text(), float)
            down_threshold = self.parse_input(self.down_threshold_input.text(), float)
            
            # 生成所有参数组合
            parameter_combinations = []
            for st in sell_times:
                for md in max_days:
                    for ut in up_threshold:
                        for dt in down_threshold:
                            parameter_combinations.append({
                                'sell_times': st,
                                'max_days': md,
                                'up_threshold': ut,
                                'down_threshold': dt
                            })
            
            return parameter_combinations
            
        except ValueError as e:
            QMessageBox.warning(self, "参数错误", str(e))
            return []

class BacktestThread(QThread):
    """回测线程"""
    finished = pyqtSignal(str, bool)  # 发送股票代码和是否成功的信号
    progress = pyqtSignal(str, dict)  # 发送进度信息的信号
    trade_record_signal = pyqtSignal(dict)  # 新增的交易记录信号

    def __init__(self, backtest_manager, stock_code, stock_data, strategy_name, params, buy_date, start_date, end_date, current_index=1, total_combinations=0):
        super().__init__()
        self.backtest_manager = backtest_manager
        self.stock_code = stock_code
        self.stock_data = stock_data
        self.strategy_name = strategy_name
        self.params = params
        self.buy_date = buy_date
        self.start_date = start_date
        self.end_date = end_date
        self.is_running = True  # 添加运行状态标志
        self.stopped = False
        self.logger = backtest_manager.logger  # 使用回测管理器的logger
        self.current_index = current_index  # 添加当前索引
        self.total_combinations = total_combinations  # 添加总组合数

    def stop(self):
        """停止回测"""
        self.is_running = False

    def run(self):
        """运行回测线程"""
        try:
            success = False
            
            try:
                # 直接调用start_backtest方法
                success = self.backtest_manager.start_backtest(
                    self.stock_code, 
                    self.stock_data,
                    self.strategy_name,
                    self.params,
                    self.buy_date,
                    self.start_date,
                    self.end_date,
                    self
                )
                
            except Exception as e:
                self.logger.error(f"[{self.stock_code}] 回测过程出错: {str(e)}", exc_info=True)
                success = False
                
            self.finished.emit(self.stock_code, success)
            
        except Exception as e:
            self.logger.error(f"[{self.stock_code}] 回测线程运行出错: {str(e)}", exc_info=True)

class BacktestWindowExt(Ui_MainWindow):
    """扩展主窗口功能，不修改原始生成文件"""
    
    def __init__(self, logger=None):  # 添加 logger 参数
        super().__init__()
        self.logger = logger or Logger(mode='backtest')  # 使用传入的 logger 或创建新的
        self.task_manager = TaskManager(mode='backtest')  # 在初始化时就创建 TaskManager 实例
        self.positions = {}
        self.backtest_manager = BacktestManager()
        self.backtest_threads = {}  # 添加这行来初始化回测线程字典
        
        # 读取默认参数配置
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'config.ini')
        if os.path.exists(config_path):
            import configparser
            config = configparser.ConfigParser()
            config.read(config_path, encoding='utf-8')
            
            # 设置默认参数
            self.default_params = {
                'sell_times': config.getint('DEFAULT', 'sell_times', fallback=3),
                'max_days': config.getint('DEFAULT', 'max_days', fallback=2),
                'up_threshold': config.getfloat('DEFAULT', 'up_threshold', fallback=1.0),
                'down_threshold': config.getfloat('DEFAULT', 'down_threshold', fallback=1.0)
            }
        else:
            # 如果配置文件不存在，使用硬编码的默认值
            self.default_params = {
                'sell_times': 3,
                'max_days': 2,
                'up_threshold': 1.0,
                'down_threshold': 1.0
            }
        self.logger.info(f"加载默认参数配置: {self.default_params}")

    def setup_position_table(self):
        """设置持仓表格"""
        # 设置表头
        headers = ['代码', '名称', '持仓', '可用', '成本', '市值']
        self.tableWidget.setColumnCount(len(headers))
        self.tableWidget.setHorizontalHeaderLabels(headers)
        
        # 设置表格属性
        self.tableWidget.setShowGrid(False)
        self.tableWidget.verticalHeader().setVisible(False)
        self.tableWidget.setEditTriggers(self.tableWidget.NoEditTriggers)
        
        # 设置列宽
        header = self.tableWidget.horizontalHeader()
        for i in range(len(headers)):
            header.setSectionResizeMode(i, header.Stretch)
            
    def setup_position_slots(self, window):
        """设置持仓表格的基本属性"""
        # 设置表头 - 修改这里，添加"仓位"列
        headers = ['仓位', '代码', '名称', '持仓', '可用', '成本', '市值']  # 添加"仓位"列
        self.tableWidget.setColumnCount(len(headers))
        self.tableWidget.setHorizontalHeaderLabels(headers)
        
        # 设置表格属性
        self.tableWidget.setShowGrid(False)
        self.tableWidget.verticalHeader().setVisible(False)
        self.tableWidget.setEditTriggers(self.tableWidget.NoEditTriggers)
        
        # 设置列宽
        header = self.tableWidget.horizontalHeader()
        
        # 设置第一列（仓位列）固定宽度
        header.setSectionResizeMode(0, header.Fixed)
        self.tableWidget.setColumnWidth(0, 120)
        
        # 设置其他列自动调整
        for i in range(1, len(headers)):
            header.setSectionResizeMode(i, header.Stretch)  # 改为 Stretch 模式
        
        # 启用最后一列后的拉伸
        header.setStretchLastSection(True)
        
        # 设置样式
        self.tableWidget.setStyleSheet("""
            QTableWidget {
                border: 1px solid #CCCCCC;
                background-color: white;
                font-family: "Microsoft YaHei";
                font-size: 12pt;
            }
            QHeaderView::section {
                background-color: #E8E8E8;
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
        """)
        
    def setup_ui(self, window):
        """初始化UI"""
        # 先调用父类的 setupUi 创建基本 UI 元素
        super().setupUi(window)
        
        # 修改窗口标题，明确标明是回测系统
        window.setWindowTitle("蚂蚁量化交易策略 - 回测系统")
        
        # 添加首板分析菜单项
        self.action_limit_up = QAction(window)
        self.action_limit_up.setText("首板分析")
        self.menu_F.insertAction(self.action_4, self.action_limit_up)  # 在退出选项前插入
        self.action_limit_up.triggered.connect(self.analyze_limit_up)
        
        # 连接版本菜单
        self.action.triggered.connect(self.show_version_dialog)
        
        # 连接使用前必读菜单
        self.action_2.triggered.connect(self.show_read_before_use_dialog)
        
        # 设置分隔条的比例
        self.splitter_2.setStretchFactor(0, 7)  # 上面部分占7
        self.splitter_2.setStretchFactor(1, 3)  # 下面部分占3
        self.splitter_3.setStretchFactor(0, 7)  # 左边部分占7
        self.splitter_3.setStretchFactor(1, 3)  # 右边部分占3
        
        # 设置日期选择器
        self.dateEdit.setCalendarPopup(True)  # 允许弹出日历
        self.dateEdit.setDate(datetime.now().date())  # 设置当前日期
        self.dateEdit.dateChanged.connect(self.load_positions)  # 添加日期变化事件处理
        
        # 设置持仓表格
        self.setup_position_table()
        # 设置任务表格
        self.setup_task_list(window)
        # 设置交易记录表格
        self.setup_trade_record()
        
        # 连接cellChanged信号
        self.tableWidget_2.cellChanged.connect(self.on_cell_editing)
        
        # 设置窗口标题和字体
        font = window.font()
        font.setFamily("Microsoft YaHei")
        font.setPointSize(14)
        font.setBold(True)
        window.setFont(font)
        
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
        self.textEdit = AutoScrollTextEdit(window)
        
        # 复制原有控件的几何属性和对象名
        self.textEdit.setGeometry(old_text_edit.geometry())
        self.textEdit.setObjectName(old_text_edit.objectName())
        
        # 获取父控件
        parent_widget = old_text_edit.parentWidget()
        if parent_widget:
            # 设置新控件的父对象
            self.textEdit.setParent(parent_widget)
            # 设置新控件的位置和大小
            self.textEdit.setGeometry(old_text_edit.geometry())
            # 显示新控件
            self.textEdit.show()
        
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
        
        # 设置表格
        self.setup_position_slots(window)
        # 设置交易记录表格
        self.setup_trade_record()
        
        # 设置回测管理器的日志显示
        self.backtest_manager.set_text_edit(self.textEdit)
        
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
        
        # 创建定时器用于更新状态栏
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_status_bar)
        self.status_timer.start(1000)  # 每秒更新一次
        
        # 用于记录当前显示位置
        self.current_display_index = 0
        
        # 保存 window 引用，用于关闭事件
        self.window = window
        # 设置关闭事件
        window.closeEvent = self.handle_close_event
        
        # 设置上下分隔条的比例 (splitter)
        self.splitter.setStretchFactor(0, 5)  # 上面部分占5
        self.splitter.setStretchFactor(1, 5)  # 下面部分占5
        
        # 设置上下分隔条的比例 (splitter_2)
        self.splitter_2.setStretchFactor(0, 5)  # 上面部分占5
        self.splitter_2.setStretchFactor(1, 5)  # 下面部分占5
        
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

        # 在UI初始化完成后，立即加载当天的持仓数据
        self.load_positions()

    def save_current_tasks(self):
        """保存当前任务列表状态"""
        try:
            '''# 添加完整的调用栈日志
            import traceback
            stack = traceback.extract_stack()
            # 打印完整的调用栈
            self.logger.info("保存任务调用栈:")
            for frame in stack[:-1]:  # 不包含当前函数
                self.logger.info(f"  {frame.filename}:{frame.lineno} in {frame.name}")
            '''
            table = self.tableWidget_2
            tasks_data = []
            
            for row in range(table.rowCount()):
                # 获取股票代码，如果为空则跳过
                stock_code_item = table.item(row, 0)
                if not stock_code_item:
                    continue
                stock_code = stock_code_item.text()
                
                # 获取策略选择下拉框的值
                strategy_container = table.cellWidget(row, 7)
                strategy = strategy_container.combo.currentText() if strategy_container else "规则任务"
                
                # 获取其他字段，使用get方法安全地获取值
                stock_name_item = table.item(row, 1)
                init_volume_item = table.item(row, 2)
                init_cost_item = table.item(row, 3)
                buy_date_item = table.item(row, 4)
                hold_days_item = table.item(row, 5)
                base_price_item = table.item(row, 6)
                status_item = table.item(row, 9)
                
                task = {
                    'stock_code': stock_code,
                    'stock_name': stock_name_item.text() if stock_name_item else '',
                    'init_volume': int(init_volume_item.text()) if init_volume_item else 0,
                    'init_cost': float(init_cost_item.text()) if init_cost_item else 0.0,
                    'buy_date': buy_date_item.text() if buy_date_item else datetime.now().strftime('%Y-%m-%d'),
                    'hold_days': int(hold_days_item.text()) if hold_days_item else 0,
                    'base_price': float(base_price_item.text()) if base_price_item else 0.0,
                    'strategy': strategy,
                    'status': status_item.text() if status_item else '未运行',
                    'params': self.task_manager.get_task_params(stock_code)
                }
                tasks_data.append(task)
            
            # 获取当前选择的日期
            selected_date = self.dateEdit.date().toPyDate().strftime('%Y-%m-%d')
            
            # 构建保存路径
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            tasks_dir = os.path.join(current_dir, 'data', 'tasks')
            os.makedirs(tasks_dir, exist_ok=True)
            
            # 构建文件名
            file_name = f'tasks_{selected_date}.xlsx'
            file_path = os.path.join(tasks_dir, file_name)
            
            # 将任务数据转换为DataFrame
            df = pd.DataFrame(tasks_data)
            
            # 处理params字段，将字典转换为字符串
            if 'params' in df.columns:
                df['params'] = df['params'].apply(lambda x: json.dumps(x) if isinstance(x, dict) else x)
            
            # 保存到Excel文件
            df.to_excel(file_path, index=False)
            
            # 在保存成功后再次记录调用来源
            #caller = stack[-2]  # 获取调用者的信息
            #self.logger.info(f"任务保存成功，共{len(tasks_data)}个任务 - 来自: {caller.filename}:{caller.lineno} in {caller.name}")
            self.logger.info(f"任务保存成功，共{len(tasks_data)}个任务")
            
        except Exception as e:
            self.logger.error(f"保存任务失败: {str(e)}")
    
    def setup_task_list(self, window):
        """设置任务列表"""
        # 断开cellChanged信号
        try:
            self.tableWidget_2.cellChanged.disconnect(self.on_cell_editing)
        except (TypeError, RuntimeError):
            pass
            
        #try:
        if True:
            # 设置表格样式，添加标题和边框
            self.tableWidget_2.setStyleSheet("""
                QTableWidget {
                    border: 1px solid #CCCCCC;
                    background-color: white;
                    font-family: "Microsoft YaHei";
                    font-size: 12pt;
                }
                QHeaderView::section {
                    background-color: #E8E8E8;
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
                }
                QPushButton {
                    font-family: "Microsoft YaHei";
                    font-size: 12pt;
                }
            """)
            
            # 设置表头
            headers = ['代码', '名称', '初始股数', '初始成本', '买入日期', 
                      '持有交易日', '基准价格', '策略', '参数', '状态', '操作']
            self.tableWidget_2.setColumnCount(len(headers))
            self.tableWidget_2.setHorizontalHeaderLabels(headers)
            
            # 设置表格属性
            self.tableWidget_2.setShowGrid(False)
            self.tableWidget_2.verticalHeader().setVisible(False)
            self.tableWidget_2.setEditTriggers(self.tableWidget_2.DoubleClicked)
            
            # 设置行高
            self.tableWidget_2.verticalHeader().setDefaultSectionSize(70)
            
            # 设置列宽
            header = self.tableWidget_2.horizontalHeader()
            
            # 除了状态列(索引9)保持固定宽度外，其他列都设为可交互调整，操作列设为拉伸
            for i in range(len(headers)):
                if i == 9:  # 状态列固定
                    header.setSectionResizeMode(i, header.Fixed)
                    self.tableWidget_2.setColumnWidth(i, 120)
                elif i == 10:  # 操作列设为拉伸模式
                    header.setSectionResizeMode(i, header.Stretch)
                else:  # 其他列都可以拖拉调整
                    header.setSectionResizeMode(i, header.Interactive)
                    # 设置各列的初始宽度
                    if i == 0:  # 代码列
                        self.tableWidget_2.setColumnWidth(i, 120)
                    elif i == 1:  # 名称列
                        self.tableWidget_2.setColumnWidth(i, 120)
                    elif i == 2:  # 初始股数列
                        self.tableWidget_2.setColumnWidth(i, 120)
                    elif i == 3:  # 初始成本列
                        self.tableWidget_2.setColumnWidth(i, 120)
                    elif i == 4:  # 买入日期列
                        self.tableWidget_2.setColumnWidth(i, 120)
                    elif i == 5:  # 持有交易日列
                        self.tableWidget_2.setColumnWidth(i, 100)
                    elif i == 6:  # 基准价格列
                        self.tableWidget_2.setColumnWidth(i, 120)
                    elif i == 7:  # 策略列
                        self.tableWidget_2.setColumnWidth(i, 150)
                    elif i == 8:  # 参数列
                        self.tableWidget_2.setColumnWidth(i, 200)
            
            # 添加右键菜单
            self.tableWidget_2.setContextMenuPolicy(Qt.CustomContextMenu)
            self.tableWidget_2.customContextMenuRequested.connect(self.show_task_context_menu)
            
            # 连接列宽调整信号，确保状态列和操作列保持固定宽度
            self.tableWidget_2.horizontalHeader().sectionResized.connect(self.on_column_resized)
            
            '''# 加载已保存的任务
            tasks = self.task_manager.load_tasks()
            self.saved_tasks = {task['stock_code']: task for task in tasks}
            
            # 将任务添加到表格中
            table = self.tableWidget_2
            
            # 阻止所有信号
            table.blockSignals(True)
            
            try:
                # 如果有持仓数据，检查并添加任务
                if self.positions:
                    for stock_code, stock_data in self.positions.items():
                        if stock_data['volume'] > 0:
                            row = table.rowCount()
                            table.insertRow(row)
                            
                            # 优先使用已保存的任务数据，如果没有则使用默认值
                            task_data = self.saved_tasks.get(stock_code, {})
                            
                            # 设置基本信息
                            items = [
                                stock_data['stock_code'],
                                stock_data['stock_name'],
                                str(task_data.get('init_volume', stock_data['volume'])),
                                f"{task_data.get('init_cost', stock_data['open_price']):.2f}",
                                task_data.get('buy_date', datetime.now().strftime('%Y-%m-%d')),
                                str(self.task_manager.calculate_hold_days(task_data.get('buy_date', datetime.now().strftime('%Y-%m-%d')))),
                                f"{task_data.get('base_price', task_data.get('init_cost', stock_data['open_price'])):.3f}",  # 基准价格
                            ]

                            for col, value in enumerate(items):
                                item = QTableWidgetItem(str(value))
                                item.setTextAlignment(Qt.AlignCenter)
                                # 设置前两列和基准价格列不可编辑
                                if col < 2 or col == 6:  # 6是基准价格列
                                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                                table.setItem(row, col, item)
                            
                            # 添加策略选择下拉框
                            strategy_container = self.create_strategy_combo()
                            if stock_code in self.saved_tasks:
                                strategy_container.combo.setCurrentText(self.saved_tasks[stock_code].get('strategy', '规则任务'))
                            strategy_container.combo.currentTextChanged.connect(
                                lambda text, r=row: self.on_strategy_changed(r, text))
                            table.setCellWidget(row, 7, strategy_container)
                            
                            # 添加参数设置按钮和其他控件
                            self.setup_task_row_widgets(row, stock_code)
                            
                            # 确保任务参数被正确加载和显示
                            if 'params' in task_data:
                                self.task_manager.update_task_params(stock_code, task_data['params'])
                                # 在阻止信号的情况下更新按钮文本
                                self.update_param_button_text(row, stock_code)
                            else:
                                # 设置默认参数
                                default_params = {
                                    'sell_times': 3,
                                    'max_days': 2,
                                    'up_threshold': 1.0,
                                    'down_threshold': 1.0
                                }
                                self.task_manager.update_task_params(stock_code, default_params)
                                # 在阻止信号的情况下更新按钮文本
                                self.update_param_button_text(row, stock_code)
                            
                            # 记录日志
                            self.logger.info(f"添加任务：{stock_code} {stock_data['stock_name']}")
            finally:
                # 恢复信号
                table.blockSignals(False)
                
                # 在所有任务加载完成后，只保存一次
                self.save_current_tasks()
        finally:
            # 重新连接cellChanged信号
            try:
                self.tableWidget_2.cellChanged.connect(self.on_cell_editing)
            except (TypeError, RuntimeError):
                pass'''

    def show_task_context_menu(self, pos):
        """显示任务列表右键菜单"""
        menu = QMenu()
        delete_action = menu.addAction("删除任务")
        action = menu.exec_(self.tableWidget_2.mapToGlobal(pos))
        if action == delete_action:
            row = self.tableWidget_2.rowAt(pos.y())
            if row >= 0:
                # 获取股票代码
                stock_code = self.tableWidget_2.item(row, 0).text()
                # 从 task_params 中删除
                self.task_manager.delete_task(stock_code)
                # 从表格中删除行
                self.tableWidget_2.removeRow(row)
                # 记录日志
                self.logger.info(f"删除任务：{stock_code}")
                # 保存到文件
                self.save_current_tasks()
        
    def update_position_list(self, asset, positions):
        """更新持仓列表"""
        try:
            # 保存每日初始持仓数据
            self.save_daily_positions(positions)
            
            # 保存数据
            self.positions = positions
            
            # 将字典转换为列表（如果是字典的话）
            positions_list = []
            if isinstance(positions, dict):
                positions_list = [positions[code] for code in positions]
            else:
                positions_list = positions
            
            # 设置表格行数
            self.tableWidget.setRowCount(len(positions_list))
            
            # 填充数据
            for row, stock_data in enumerate(positions_list):
                # 计算持仓比例，避免除以0
                if asset and asset.get('total_asset', 0) > 0:
                    position_ratio = int(stock_data['volume'] * stock_data['open_price'] / asset['total_asset'] * 100)
                else:
                    position_ratio = 0
                
                # 创建仓位条
                position_bar = QProgressBar()
                position_bar.setValue(position_ratio)
                position_bar.setFixedSize(100, 20)
                position_bar.setTextVisible(True)
                position_bar.setFormat(f"{position_ratio}%")
                
                # 创建仓位条容器
                position_container = QWidget()
                position_layout = QHBoxLayout(position_container)
                position_layout.addWidget(position_bar)
                position_layout.setContentsMargins(5, 0, 5, 0)
                position_layout.setAlignment(Qt.AlignCenter)
                
                # 设置仓位条到表格
                self.tableWidget.setCellWidget(row, 0, position_container)
                
                # 填充其他数据
                # 去掉股票代码后缀，只显示6位数字
                stock_code_display = stock_data['stock_code'].split('.')[0] if '.' in stock_data['stock_code'] else stock_data['stock_code']
                data = [
                    stock_code_display,
                    stock_data['stock_name'],
                    str(stock_data['volume']),
                    str(stock_data['can_use_volume']),
                    f"{stock_data['open_price']:.2f}",
                    str(stock_data['market_value'])
                ]
                
                # 添加数据到表格
                for col, value in enumerate(data):
                    item = QTableWidgetItem(str(value))
                    item.setTextAlignment(Qt.AlignCenter)
                    self.tableWidget.setItem(row, col + 1, item)
            
            # 每次持仓更新时都检查任务列表
            self.update_task_list(positions)
            
        except Exception as e:
            self.logger.error(f"更新持仓列表失败：{str(e)}")

    def save_daily_positions(self, positions):
        """保存每日初始持仓数据"""
        try:
            # 获取当前日期
            today = datetime.now().strftime('%Y-%m-%d')
            
            # 构建保存路径
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(current_dir, 'data', 'positions')
            os.makedirs(data_dir, exist_ok=True)
            
            # 构建文件名
            file_name = f'positions_{today}.csv'
            file_path = os.path.join(data_dir, file_name)
            
            # 如果文件已存在，直接返回
            if os.path.exists(file_path):
                return
            
            # 将持仓数据转换为列表
            positions_list = []
            if isinstance(positions, dict):
                positions_list = [positions[code] for code in positions]
            else:
                positions_list = positions
            
            # 添加日期字段
            for pos in positions_list:
                pos['date'] = today
            
            # 保存为CSV文件
            df = pd.DataFrame(positions_list)
            df.to_csv(file_path, index=False, encoding='utf-8')
            
            self.logger.info(f"保存{today}持仓数据成功")
            
        except Exception as e:
            self.logger.error(f"保存每日持仓数据失败：{str(e)}")

    def get_previous_trading_day(self):
        """获取上一个交易日"""
        current_date = datetime.now()
        previous_date = current_date
        
        while True:
            previous_date = previous_date - timedelta(days=1)
            if is_tradeday(previous_date):  # 假设is_trading_day是从外部导入的函数
                return previous_date.strftime('%Y-%m-%d')

    def determine_buy_date(self, stock_data):
        """确定股票的买入日期"""
        stock_code = stock_data['stock_code']
        
        if stock_code in self.saved_tasks:
            return self.saved_tasks[stock_code].get('buy_date')
        
        # 对于新增的股票，直接使用当天日期
        return datetime.now().strftime('%Y-%m-%d')

    def update_task_list(self, positions):
        """更新任务列表"""
        if True:#try:
            table = self.tableWidget_2
            selected_date = self.dateEdit.date().toPyDate()
            
            # 清空表格
            table.setRowCount(0)
            
            # 阻止所有信号
            table.blockSignals(True)
            
            #try:
            if True:
                # 遍历持仓数据
                for stock_code, stock_data in positions.items():
                    if stock_data['volume'] > 0:
                        # 如果股票不在表格中，添加新行
                        if not self.is_stock_in_table(stock_code):
                            row = table.rowCount()
                            table.insertRow(row)
                            
                            # 优先使用已保存的任务数据中的买入日期
                            if stock_code in self.saved_tasks:
                                buy_date = self.saved_tasks[stock_code].get('buy_date')
                                if buy_date:
                                    try:
                                        # 使用parse_date_auto处理日期
                                        buy_date_obj = parse_date_auto(buy_date)
                                        #self.logger.info(f"[{stock_code}] 从saved_tasks获取的买入日期: {buy_date}, 类型: {type(buy_date_obj)}")
                                        buy_date = buy_date_obj.strftime('%Y-%m-%d')
                                    except ValueError:
                                        # 如果日期格式不正确，根据可用数量判断买入日期
                                        if stock_data['can_use_volume'] > 0:
                                            # 可用数量大于0，说明是上一个交易日买入的
                                            prev_date = selected_date - timedelta(days=1)
                                            while not is_tradeday(prev_date):
                                                prev_date = prev_date - timedelta(days=1)
                                            buy_date = prev_date.strftime('%Y-%m-%d')
                                            buy_date_obj = prev_date
                                        else:
                                            # 可用数量为0，说明是当天买入的
                                            buy_date = selected_date.strftime('%Y-%m-%d')
                                            buy_date_obj = selected_date
                            else:
                                # 如果没有买入日期，根据可用数量判断
                                if stock_data['can_use_volume'] > 0:
                                    # 可用数量大于0，说明是上一个交易日买入的
                                    prev_date = selected_date - timedelta(days=1)
                                    while not is_tradeday(prev_date):
                                        prev_date = prev_date - timedelta(days=1)
                                    buy_date = prev_date.strftime('%Y-%m-%d')
                                    buy_date_obj = prev_date
                                else:
                                    # 可用数量为0，说明是当天买入的
                                    buy_date = selected_date.strftime('%Y-%m-%d')
                                    buy_date_obj = selected_date
                            
                            # 计算持有交易日（从买入日期到选择的回测日期）
                            hold_days = 0
                            check_date = buy_date_obj.date() if isinstance(buy_date_obj, datetime) else buy_date_obj
                            self.logger.info(f"[{stock_code}] 开始计算持有天数:")
                            self.logger.info(f"买入日期: {check_date}")
                            self.logger.info(f"选择的日期: {selected_date}")
                            
                            # 从买入日期的下一天开始计算
                            check_date += timedelta(days=1)
                            self.logger.info(f"开始检查的日期: {check_date}")
                            
                            # 如果选择日期不是交易日，找到最近的前一个交易日
                            end_date = selected_date
                            while not is_tradeday(end_date):
                                end_date = end_date - timedelta(days=1)
                                self.logger.info(f"选择日期 {selected_date} 不是交易日，使用前一个交易日 {end_date}")
                            
                            while check_date <= end_date:
                                if is_tradeday(check_date):
                                    hold_days += 1
                                    self.logger.info(f"交易日 {check_date}, 当前持有天数: {hold_days}")
                                else:
                                    self.logger.info(f"非交易日 {check_date}, 跳过")
                                check_date += timedelta(days=1)
                            
                            # 如果买入日期和选择日期是同一天，持有天数应该是0
                            if isinstance(buy_date_obj, datetime):
                                buy_date_obj = buy_date_obj.date()
                            if buy_date_obj == selected_date:
                                hold_days = 0
                                self.logger.info("买入日期和选择日期是同一天，持有天数设为0")
                            
                            self.logger.info(f"[{stock_code}] 最终持有天数: {hold_days}")
                            
                            # 更新持有交易日
                            hold_days_item = QTableWidgetItem(str(hold_days))
                            hold_days_item.setFlags(hold_days_item.flags() & ~Qt.ItemIsEditable)
                            hold_days_item.setTextAlignment(Qt.AlignCenter)
                            table.setItem(row, 5, hold_days_item)
                            
                            # 获取初始成本
                            init_cost = stock_data['open_price']
                            
                            # 设置基本信息
                            items = [
                                stock_code,
                                stock_data['stock_name'],
                                str(stock_data['volume']),
                                f"{init_cost:.3f}",
                                buy_date,
                                str(hold_days),  # 使用新计算的持有天数
                                f"{init_cost:.3f}",
                            ]

                            for col, value in enumerate(items):
                                item = QTableWidgetItem(str(value))
                                item.setTextAlignment(Qt.AlignCenter)
                                # 设置前两列和基准价格列不可编辑
                                if col < 2 or col == 6:  # 6是基准价格列
                                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                                table.setItem(row, col, item)
                            
                            # 添加策略选择下拉框
                            strategy_container = self.create_strategy_combo()
                            if stock_code in self.saved_tasks:
                                strategy_container.combo.setCurrentText(self.saved_tasks[stock_code].get('strategy', '规则任务'))
                            strategy_container.combo.currentTextChanged.connect(
                                lambda text, r=row: self.on_strategy_changed(r, text))
                            table.setCellWidget(row, 7, strategy_container)
                            
                            # 添加参数设置按钮和其他控件
                            self.setup_task_row_widgets(row, stock_code)
            #finally:
            #   # 恢复信号
            #   table.blockSignals(False)
            
            table.blockSignals(False)

            # 在所有任务加载完成后，保存一次任务列表
            self.save_current_tasks()
            #self.logger.info("任务列表更新完成，已保存到文件")
        
        #except Exception as e:
        #    self.logger.error(f"更新任务列表失败: {str(e)}")

    def parse_date_auto(self, date_str):
        for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S'):
            try:
                return datetime.strptime(str(date_str), fmt)
            except Exception:
                continue
        raise ValueError(f"无法识别的日期格式: {date_str}")


    def is_stock_in_table(self, stock_code):
        """检查股票是否已在任务列表中"""
        table = self.tableWidget_2
        for row in range(table.rowCount()):
            if table.item(row, 0).text() == stock_code:
                return True
        return False
    
    def setup_task_row_widgets(self, row, stock_code):
        """设置任务行的控件"""
        table = self.tableWidget_2

        # 添加参数设置按钮
        param_button = QPushButton("设置参数")
        param_button.setStyleSheet("""
            QPushButton { 
                color: blue; 
                text-decoration: underline; 
                text-align: center;
                padding: 5px;
                height: 70px;
                margin: 0px;
                min-height: 70px;
                max-height: 70px;
            }
        """)
        param_button.setFixedHeight(70)
        param_button.clicked.connect(lambda checked, r=row, sc=stock_code: self.show_parameter_dialog(r, sc))
        table.setCellWidget(row, 8, param_button)
        
        # 获取当前参数
        current_params = self.task_manager.get_task_params(stock_code)
        
        # 如果有参数，立即更新按钮文本
        if current_params:
            self.update_param_button_text(row, stock_code)
        else:
            # 使用保存的默认参数
            self.task_manager.update_task_params(stock_code, self.default_params)
            # 立即更新按钮文本显示默认参数
            param_text = f"{self.default_params['sell_times']}次 {self.default_params['max_days']}交易日\n↑{self.default_params['up_threshold']}% ↓{self.default_params['down_threshold']}%"
            param_button.setText(param_text)
            #self.logger.info(f"[{stock_code}] 设置默认参数: {self.default_params}")

        # 设置状态，添加颜色样式
        status_item = QTableWidgetItem("未运行")
        status_item.setTextAlignment(Qt.AlignCenter)
        # 设置默认颜色为黑色
        status_item.setForeground(Qt.black)
        table.setItem(row, 9, status_item)
        
        # 添加操作按钮
        operation_widget = self.create_operation_buttons(row)
        table.setCellWidget(row, 10, operation_widget)

    def show_parameter_dialog(self, row, stock_code):
        """显示参数设置对话框"""
        dialog = ParameterDialog(self.tableWidget_2)
        
        # 加载当前参数
        current_params = self.task_manager.get_task_params(stock_code)
        
        # 处理单个参数值的情况
        def get_param_value(param_name, default_value):
            value = current_params.get(param_name, default_value)
            if isinstance(value, (int, float)):
                return str(value)
            elif isinstance(value, list):
                return ",".join(map(str, value))
            return str(default_value)
        
        dialog.sell_times_input.setText(get_param_value('sell_times', 3))
        dialog.max_days_input.setText(get_param_value('max_days', 2))
        dialog.up_threshold_input.setText(get_param_value('up_threshold', 1.0))
        dialog.down_threshold_input.setText(get_param_value('down_threshold', 1.0))
        
        if dialog.exec_() == QDialog.Accepted:
            # 断开cellChanged信号，避免触发保存
            try:
                self.tableWidget_2.cellChanged.disconnect(self.on_cell_editing)
            except (TypeError, RuntimeError):
                pass
            
            try:
                # 保存新参数
                new_params = {
                    'sell_times': dialog.parse_input(dialog.sell_times_input.text(), int),
                    'max_days': dialog.parse_input(dialog.max_days_input.text(), int),
                    'up_threshold': dialog.parse_input(dialog.up_threshold_input.text(), float),
                    'down_threshold': dialog.parse_input(dialog.down_threshold_input.text(), float)
                }
                self.task_manager.update_task_params(stock_code, new_params)
                # 更新按钮显示，这里不会触发保存
                self.update_param_button_text(row, stock_code)
                # 保存一下
                
                self.save_current_tasks()
            finally:
                # 重新连接cellChanged信号
                try:
                    self.tableWidget_2.cellChanged.connect(self.on_cell_editing)
                except (TypeError, RuntimeError):
                    pass
    
    def start_next_backtest(self, stock_code, stock_data, strategy_name, buy_date, start_date, end_date):
        """启动下一个回测"""
        if self.current_param_index <= self.total_combinations:
            param_set = self.parameter_combinations[self.current_param_index - 1]  # 索引减1
            
            # 创建回测线程
            backtest_thread = BacktestThread(
                self.backtest_manager, 
                stock_code, 
                stock_data,
                strategy_name, 
                param_set,
                buy_date, 
                start_date, 
                end_date,
                self.current_param_index,  # 使用当前索引作为回测次数
                self.total_combinations
            )
            
            # 连接信号
            backtest_thread.finished.connect(lambda code, success, p=param_set: self.on_backtest_finished(code, p))
            backtest_thread.progress.connect(lambda code, record, p=param_set: self.on_backtest_progress(code, record, p))
            backtest_thread.trade_record_signal.connect(self.update_trade_record)
            
            # 保存线程引用
            thread_key = f"{stock_code}_{self.current_param_index}"
            self.backtest_threads[thread_key] = backtest_thread
            
            # 启动线程
            backtest_thread.start()
            
            # 如果用户点击了停止按钮，则中断后续回测
            if not backtest_thread.is_running:
                return

    def start_task(self, row):
        """启动回测任务"""
        try:
            # 清空交易记录表格
            self.tableWidget_3.setRowCount(0)
            
            # 获取任务信息
            table = self.tableWidget_2
            stock_code = table.item(row, 0).text()
            buy_date = table.item(row, 4).text()
            init_volume = int(table.item(row, 2).text())
            
            # 获取策略信息
            strategy_container = table.cellWidget(row, 7)
            strategy_name = strategy_container.combo.currentText()
            
            # 获取参数组合
            params = self.task_manager.get_task_params(stock_code)
            
            # 生成参数组合
            parameter_combinations = []
            sell_times = params.get('sell_times', [3])
            max_days = params.get('max_days', [2])
            up_threshold = params.get('up_threshold', [1.0])
            down_threshold = params.get('down_threshold', [1.0])
            
            # 确保所有参数都是列表
            if not isinstance(sell_times, list):
                sell_times = [sell_times]
            if not isinstance(max_days, list):
                max_days = [max_days]
            if not isinstance(up_threshold, list):
                up_threshold = [up_threshold]
            if not isinstance(down_threshold, list):
                down_threshold = [down_threshold]
            
            # 生成所有参数组合
            for st in sell_times:
                for md in max_days:
                    for ut in up_threshold:
                        for dt in down_threshold:
                            parameter_combinations.append({
                                'sell_times': st,
                                'max_days': md,
                                'up_threshold': ut,
                                'down_threshold': dt
                            })
            
            # 显示参数组合数量
            total_combinations = len(parameter_combinations)
            reply = QMessageBox.question(
                self.tableWidget_2,
                "确认启动回测",
                f"共发现 {total_combinations} 组参数组合，是否开始回测？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.No:
                return
            
            # 获取股票数据
            stock_data = {
                'stock_code': stock_code,
                'stock_name': table.item(row, 1).text(),  # 从表格中获取股票名称
                'volume': init_volume,  # 总持仓量
                'can_use_volume': self.positions.get(stock_code, {}).get('can_use_volume', init_volume),  # 从持仓列表获取可用持仓量
                'init_volume': init_volume,  # 添加初始持仓量字段
                'open_price': float(table.item(row, 3).text())
            }
            
            # 获取日期范围
            start_date = self.dateEdit.date().toPyDate()
            end_date = datetime.now().date()
            
            # 更新UI状态
            #status_item = table.item(row, 9)
            #status_item.setText("运行中")
            #status_item.setForeground(Qt.red)
            
            # 更新按钮状态
            operation_widget = table.cellWidget(row, 10)
            if operation_widget:
                start_button = operation_widget.layout().itemAt(0).widget()
                stop_button = operation_widget.layout().itemAt(1).widget()
                start_button.setEnabled(False)
                stop_button.setEnabled(True)
            
            # 为每个参数组合创建回测线程
            self.current_param_index = 1  # 从1开始计数
            self.parameter_combinations = parameter_combinations
            self.total_combinations = len(parameter_combinations)
            
            # 保存当前任务信息
            self.current_stock_code = stock_code
            self.current_stock_data = stock_data
            self.current_strategy_name = strategy_name
            self.current_buy_date = buy_date
            self.current_start_date = start_date
            self.current_end_date = end_date

            # 更新状态为运行中
            self.update_backtest_status(stock_code, "运行中")
            
            # 启动第一个回测
            self.start_next_backtest(
                stock_code, 
                stock_data,
                strategy_name, 
                buy_date, 
                start_date, 
                end_date
            )
            
        except Exception as e:
            self.logger.error(f"启动回测任务失败: {str(e)}", exc_info=True)
            
    def on_backtest_finished(self, stock_code, params):
        """回测完成处理"""
        try:
            # 显示回测结果
            self.show_backtest_result(stock_code, params)
            
            # 增加索引并启动下一个回测
            self.current_param_index += 1
            
            # 检查是否还有下一个回测，并且当前线程仍在运行
            if self.current_param_index <= self.total_combinations:
                # 获取当前线程
                thread_key = f"{stock_code}_{self.current_param_index - 1}"
                if thread_key in self.backtest_threads:
                    current_thread = self.backtest_threads[thread_key]
                    # 如果线程仍在运行，才启动下一个回测
                    if current_thread.is_running:
                        # 更新状态为运行中
                        self.update_backtest_status(stock_code, "运行中")
                        # 启动下一个回测
                        self.start_next_backtest(
                            self.current_stock_code,
                            self.current_stock_data,
                            self.current_strategy_name,
                            self.current_buy_date,
                            self.current_start_date,
                            self.current_end_date
                        )
                    else:
                        # 如果线程已停止，更新状态为未运行
                        self.update_backtest_status(stock_code, "未运行")
            else:
                # 所有回测都完成了，更新状态为已完成
                self.update_backtest_status(stock_code, "已完成")
            
        except Exception as e:
            self.logger.error(f"处理回测完成失败: {str(e)}")
            
    def show_backtest_result(self, stock_code, params):
        """显示回测结果"""
        try:
            # 从回测记录中获取最新的一条记录
            records_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'backtest_records')
            file_path = os.path.join(records_dir, "backtest_records.xlsx")
            
            if not os.path.exists(file_path):
                self.logger.warning("未找到回测记录文件")
                return
                
            # 读取Excel文件，指定引擎
            df = pd.read_excel(file_path, engine='openpyxl')
            
            # 找到该股票的最新回测记录
            stock_records = df[df['stock_code'] == stock_code]
            if stock_records.empty:
                self.logger.warning(f"未找到股票 {stock_code} 的回测记录")
                return
                
            record = stock_records.iloc[-1].to_dict()  # 获取最新的一条记录
            
            # 显示回测结果
            result_text = f"[{record['stock_code']}] 初始: {record['initial_cash']:,.2f}元/{record['initial_volume']}股@{record['initial_price']:.2f} | 最终: {record['final_cash']:,.2f}元/{record['final_volume']}股@{record['final_price']:.2f} | 收益率: {record['return_rate']:.2f}% | 交易: {record['trade_count']}次"
            self.textEdit.append(result_text)
            
        except Exception as e:
            self.logger.error(f"显示回测结果失败: {str(e)}")
            
    def on_backtest_progress(self, stock_code, trade_record, params):
        """处理回测进度"""
        # 在交易记录中添加参数信息
        trade_record['params'] = params
        self.update_trade_record(trade_record)

    def calculate_profit(self, stock_code):
        """计算盈亏"""
        try:
            # 获取所有交易记录
            table = self.tableWidget_3
            total_profit = 0
            records = []
            
            #self.logger.info(f"[{stock_code}] 开始计算盈亏，表格行数={table.rowCount()}")
            
            # 获取初始数据
            task_table = self.tableWidget_2
            init_cost = None
            init_volume = None
            for row in range(task_table.rowCount()):
                if task_table.item(row, 0).text() == stock_code:
                    init_cost = float(task_table.item(row, 3).text())
                    init_volume = int(task_table.item(row, 2).text())
                    #self.logger.info(f"[{stock_code}] 找到初始数据：成本={init_cost}, 持仓={init_volume}")
                    break
            
            if init_cost is None or init_volume is None:
                self.logger.error(f"[{stock_code}] 未找到初始数据")
                return
            
            # 按时间顺序处理交易记录
            for row in range(table.rowCount()):
                try:
                    record_stock_code = table.item(row, 1).text()
                    if record_stock_code == stock_code:
                        record = {
                            'time': table.item(row, 3).text(),
                            'type': table.item(row, 2).text(),
                            'price': float(table.item(row, 8).text()),  # 成交均价
                            'volume': int(table.item(row, 7).text()),
                            'reason': table.item(row, 10).text()
                        }
                        records.append(record)
                except Exception as e:
                    self.logger.error(f"[{stock_code}] 处理交易记录出错: {str(e)}")
                    continue
            
            # 按时间排序
            records.sort(key=lambda x: x['time'])
            
            # 计算持仓和盈亏
            current_volume = init_volume
            current_cost = init_cost
            total_profit = 0
            
            for record in records:
                if record['type'] == '买入':
                    # 更新持仓成本
                    total_cost = current_volume * current_cost + record['volume'] * record['price']
                    current_volume += record['volume']
                    current_cost = total_cost / current_volume if current_volume > 0 else 0
                elif record['type'] == '卖出':
                    # 计算卖出部分的盈亏
                    profit = (record['price'] - current_cost) * record['volume']
                    total_profit += profit
                    current_volume -= record['volume']
            
            # 获取最后一个交易日的收盘价
            last_price = None
            if hasattr(self, 'backtest_manager') and self.backtest_manager:
                engine = self.backtest_manager.engines.get(stock_code)
                if engine and hasattr(engine, 'data') and not engine.data.empty:
                    try:
                        # 直接从engine.data获取最后一个有效价格
                        last_price = engine.data['lastPrice'].iloc[-1]
                        #self.logger.info(f"[{stock_code}] 从回测引擎获取最后一个价格：{last_price}")
                    except Exception as e:
                        self.logger.error(f"[{stock_code}] 从回测引擎获取收盘价失败：{str(e)}")
                else:
                    self.logger.error(f"[{stock_code}] 回测引擎数据为空或不存在")
            else:
                self.logger.error(f"[{stock_code}] 回测管理器不存在")
            
            if last_price is None:
                self.logger.error(f"[{stock_code}] 无法获取最后一个交易日的收盘价")
                return
            
            # 计算剩余持仓的市值
            if current_volume > 0:
                remaining_value = current_volume * last_price
                remaining_cost = current_volume * current_cost
                remaining_profit = remaining_value - remaining_cost
                total_profit += remaining_profit
#                self.logger.info(f"[{stock_code}] 计算剩余持仓盈亏：数量={current_volume}, 成本={current_cost}, 收盘价={last_price}, 盈亏={remaining_profit:.2f}")
            
            # 生成盈亏报告
            report = f"股票代码：{stock_code}\n"
            report += f"初始持仓：{init_volume}股\n"
            report += f"初始成本：{init_cost:.2f}元\n"
            report += f"当前持仓：{current_volume}股\n"
            report += f"当前成本：{current_cost:.2f}元\n"
            report += f"最后交易日收盘价：{last_price:.2f}元\n"
            report += f"总盈亏：{total_profit:.2f}元\n"
            
            # 显示报告
            self.textEdit.setPlainText(report)
            
        except Exception as e:
            self.logger.error(f"[{stock_code}] 计算盈亏出错: {str(e)}", exc_info=True)
            self.textEdit.setPlainText(f"计算盈亏出错：{str(e)}")

    def symbol2stock(self, symbol):
        """将股票代码转换为QMT识别的格式"""
        symbol = symbol.strip()
        
        if '.SZ' in symbol or '.SH' in symbol or '.BJ' in symbol:
            return symbol
        
        symbol = symbol.zfill(6)
        
        if symbol.startswith(('0', '1', '3')):
            return f"{symbol}.SZ"  # 深交所
        elif symbol.startswith(('5', '6')):
            return f"{symbol}.SH"  # 上交所
        elif symbol.startswith(('4', '8', '920')):
            return f"{symbol}.BJ"  # 北交所
        else:
            raise ValueError(f"无效的股票代码: {symbol}")

    def stop_task(self, row):
        """停止回测任务"""
        try:
            table = self.tableWidget_2
            stock_code = table.item(row, 0).text()
            
            # 停止当前回测线程
            if hasattr(self, 'backtest_threads'):
                thread_key = f"{stock_code}_{self.current_param_index}"
                if thread_key in self.backtest_threads:
                    backtest_thread = self.backtest_threads[thread_key]
                    backtest_thread.stop()
                    # 从字典中移除已停止的线程
                    del self.backtest_threads[thread_key]
            
            # 更新状态为"未运行"
            self.update_backtest_status(stock_code, "未运行")
            
            self.logger.info(f"停止回测任务：{stock_code}")
            
        except Exception as e:
            self.logger.error(f"停止回测任务失败：{str(e)}")

    def create_operation_buttons(self, row):
        """创建操作按钮"""
        widget = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(5, 2, 5, 2)
        
        # 创建启动按钮
        start_button = QPushButton("回测")
        start_button.setFixedWidth(50)
        start_button.clicked.connect(lambda checked, r=row: self.start_task(r))
        
        # 创建停止按钮
        stop_button = QPushButton("停止")
        stop_button.setFixedWidth(50)
        stop_button.clicked.connect(lambda checked, r=row: self.stop_task(r))
        
        # 创建分析按钮
        analyze_button = QPushButton("分析")
        analyze_button.setFixedWidth(50)
        analyze_button.clicked.connect(lambda checked, r=row: self.analyze_task(r))
        
        # 根据任务状态设置按钮状态
        status = self.tableWidget_2.item(row, 9).text()
        if status == "运行中":
            start_button.setEnabled(False)
            stop_button.setEnabled(True)
        else:
            start_button.setEnabled(True)
            stop_button.setEnabled(False)
        
        layout.addWidget(start_button)
        layout.addWidget(stop_button)
        layout.addWidget(analyze_button)
        widget.setLayout(layout)
        return widget

    def analyze_task(self, row):
        """分析任务"""
        if True:#try:
            # 获取任务信息
            table = self.tableWidget_2
            stock_code = table.item(row, 0).text()
            stock_name = table.item(row, 1).text()
            init_volume = int(table.item(row, 2).text())
            
            # 获取选择的日期
            selected_date = self.dateEdit.date().toPyDate()
            
            # 获取股票数据
            stock_data = {
                'stock_code': stock_code,
                'stock_name': stock_name,
                'volume': init_volume,
                'can_use_volume': self.positions.get(stock_code, {}).get('can_use_volume', init_volume),
                'init_volume': init_volume,
                'open_price': float(table.item(row, 3).text())
            }
            
            # 获取日期范围
            start_date = selected_date
            end_date = datetime.now().date()
            
            # 创建回测引擎
            engine = BacktestEngine(stock_code=stock_code)
            engine.set_logger(self.logger)
            
            # 加载历史数据
            self.logger.info(f"开始加载{stock_code}的历史数据...")
            success = engine.load_data(start_date, end_date)
            if success and engine.data is not None and not engine.data.empty:
                self.logger.info(f"成功加载数据，开始分析...")
                # 统计tick数据个数
                tick_count = len(engine.data)
                
                # 显示统计结果
                self.textEdit.append(f"\n{stock_code} {selected_date}至今 tick数据异动统计：")
                self.textEdit.append(f"总tick数：{tick_count}")
                
                # 输出第一条tick数据
                first_tick = engine.data.iloc[0]
                self.logger.info(f"处理第一条tick数据...")
                self.textEdit.append("\n第一条tick数据：")
                self.textEdit.append(f"时间：{first_tick.name}")
                self.textEdit.append(f"最新价：{first_tick['lastPrice']:.2f}")
                self.textEdit.append(f"成交量：{first_tick['volume']}")
                self.textEdit.append(f"买一价：{first_tick['bidPrice'][0]:.2f}")
                self.textEdit.append(f"卖一价：{first_tick['askPrice'][0]:.2f}")
                self.textEdit.append(f"买一量：{first_tick['bidVol'][0]}")
                self.textEdit.append(f"卖一量：{first_tick['askVol'][0]}")

                #输出最后一条tick数据
                last_tick = engine.data.iloc[-1]
                self.logger.info(f"处理最后一条tick数据...")
                self.textEdit.append("\n最后一条tick数据：")
                self.textEdit.append(f"时间：{last_tick.name}")
                self.textEdit.append(f"最新价：{last_tick['lastPrice']:.2f}")
                self.textEdit.append(f"成交量：{last_tick['volume']}")
                self.textEdit.append(f"买一价：{last_tick['bidPrice'][0]:.2f}")
                self.textEdit.append(f"卖一价：{last_tick['askPrice'][0]:.2f}")
                self.textEdit.append(f"买一量：{last_tick['bidVol'][0]}")
                self.textEdit.append(f"卖一量：{last_tick['askVol'][0]}")

                self.textEdit.append("\n")

                # 初始化涨停标记和上一个tick的数据
                is_up_limit = False
                is_first_up_limit = False
                up_limit_price = 0
                prev_tick = None

                self.logger.info(f"开始遍历tick数据...")
                # 遍历所有tick数据
                for i, (index, tick) in enumerate(engine.data.iterrows()):
                    if True:#try:
                        # 剔除掉非交易时间的数据，tick.name是字符串类型，格式为：20250509145702
                        #交易时段是9:30-11:30,13:00-15:00
                        # 获取交易时段，是字符串类型，格式为：20250509145702
                        trade_time = tick.name[8:14]
                        if trade_time < '093000' or trade_time > '150000':
                            continue

                        if trade_time == '093000':
                            is_up_limit = False
                            is_first_up_limit= False
                            up_limit_price = 0
                            prev_tick = None
                        
                        # 第一个卖一价和卖一量都是0的标记为涨停，获得最新价为涨停板，随后的涨停板不再输出，直到出现卖一价和卖一量不是0的为止，此时输出开板
                        if tick['askPrice'][0] == 0 and tick['askVol'][0] == 0:
                            #只输出连续涨停的第一个涨停板
                            if not is_up_limit:
                                is_up_limit = True
                                is_first_up_limit = True
                                up_limit_price = tick['lastPrice']
                                # 输出当前封单量
                                current_seal = tick['bidVol'][0] * tick['lastPrice']
                                # 根据股票代码设置价格精度
                                price_precision = 3 if stock_code.startswith(('5', '1')) else 2
                                if trade_time == '093000':
                                    tmp_string = '开盘涨停'
                                else:
                                    tmp_string = '涨停'
                                # 格式化时间戳
                                time_str = f"{tick.name[8:10]}:{tick.name[10:12]}:{tick.name[12:14]}"
                                self.textEdit.append(f"{time_str} {tmp_string} 最新价：{tick['lastPrice']:.{price_precision}f}，封单量：{tick['bidVol'][0]}手，金额{current_seal/100:.2f}万")
                            
                            # 检查涨停板加单和撤单
                            if prev_tick is not None and not is_first_up_limit:
                                if prev_tick['bidVol'][0] > 0:
                                    bid_vol_diff = tick['bidVol'][0] - prev_tick['bidVol'][0]
                                    bid_vol_ratio = bid_vol_diff / prev_tick['bidVol'][0]
                                    if bid_vol_diff > 0:  # 加单
                                        add_amount = bid_vol_diff * tick['lastPrice']
                                        if add_amount > 1000000 or bid_vol_ratio > 0.5:  # 大于100万或者大于50%
                                            # 格式化时间戳
                                            time_str = f"{tick.name[8:10]}:{tick.name[10:12]}:{tick.name[12:14]}"
                                            if trade_time == '150000':
                                                self.textEdit.append(f"{time_str} 收盘封涨停板：当前封单：{tick['bidVol'][0]}手，金额+{add_amount/100:.2f}万")
                                            else:
                                                self.textEdit.append(f"{time_str} 涨停加单：{bid_vol_diff}手，金额+{add_amount/100:.2f}万，增加比例{bid_vol_ratio*100:.2f}%，当前封单：{tick['bidVol'][0]}手")
                                    elif bid_vol_diff < 0 :#and tick['lastPrice'] < up_limit_price:  # 撤单
                                        cancel_amount = abs(bid_vol_diff) * tick['lastPrice']
                                        if cancel_amount > 1000000 or bid_vol_ratio < -0.5:  # 大于100万或者小于-50%
                                            # 格式化时间戳
                                            time_str = f"{tick.name[8:10]}:{tick.name[10:12]}:{tick.name[12:14]}"
                                            self.textEdit.append(f"{time_str} 涨停撤单：{abs(bid_vol_diff)}手，金额-{cancel_amount/100:.2f}万，减少比例{abs(bid_vol_ratio)*100:.2f}%，当前封单：{tick['bidVol'][0]}手")
                            
                            is_first_up_limit = False
                        
                        else:
                            # 如果当前不是涨停板，则输出开板
                            if is_up_limit and tick['lastPrice'] < up_limit_price:
                                # 根据股票代码设置价格精度
                                price_precision = 3 if stock_code.startswith(('5', '1')) else 2
                                # 格式化时间戳
                                time_str = f"{tick.name[8:10]}:{tick.name[10:12]}:{tick.name[12:14]}"
                                self.textEdit.append(f"{time_str} 开板 最新价：{tick['lastPrice']:.{price_precision}f}")
                                is_up_limit = False
                        
                        # 更新上一个tick的数据
                        prev_tick = tick
                        
                        # 每处理1000条数据强制刷新一次UI
                        if i % 1000 == 0:
                            QApplication.processEvents()
                            
                    #except Exception as e:
                    #    self.logger.error(f"处理tick数据时出错：{str(e)}")
                    #    continue
                
                self.logger.info(f"tick数据分析完成")
                # 强制刷新UI
                QApplication.processEvents()
                
            else:
                self.textEdit.append(f"未获取到{stock_code}的tick数据")
                # 强制刷新UI
                QApplication.processEvents()
            
        #except Exception as e:
        #    self.logger.error(f"分析任务失败：{str(e)}")
        #    self.textEdit.append(f"分析任务失败：{str(e)}")
        #    # 强制刷新UI
        #    QApplication.processEvents()

    def setup_trade_record(self):
        """设置交易记录表格"""
        # 设置表头
        headers = ['订单编号', '股票代码', '订单类型', '委托时间', '委托价格', 
                  '委托数量', '状态', '成交数量', '成交均价', '策略名称', '交易原因']
        self.tableWidget_3.setColumnCount(len(headers))
        self.tableWidget_3.setHorizontalHeaderLabels(headers)
        
        # 设置表格属性
        self.tableWidget_3.setShowGrid(False)
        self.tableWidget_3.verticalHeader().setVisible(False)
        self.tableWidget_3.setEditTriggers(self.tableWidget_3.NoEditTriggers)
        
        # 设置列宽
        header = self.tableWidget_3.horizontalHeader()
        for i in range(len(headers)):
            if i == 3:  # 委托时间列
                header.setSectionResizeMode(i, header.Interactive)  # 设置为可调整
                self.tableWidget_3.setColumnWidth(i, 200)  # 设置初始宽度为200
            else:
                header.setSectionResizeMode(i, header.Stretch)  # 其他列自动拉伸

    def update_trade_record(self, record):
        """更新交易记录"""
        try:
            table = self.tableWidget_3
            row = table.rowCount()
            table.insertRow(row)
            
            # 处理时间格式
            time_str = record.get('time', '')
            if isinstance(time_str, (datetime, pd.Timestamp)):
                time_str = time_str.strftime('%m-%d %H:%M:%S')
            
            # 准备数据
            items = [
                str(record.get('order_id', '')),
                record.get('stock_code', ''),
                record.get('type', ''),
                time_str,
                f"{record.get('price', 0):.3f}",
                str(record.get('volume', 0)),
                record.get('order_status', '已成交'),
                str(record.get('volume', 0)),
                f"{record.get('price', 0):.3f}",
                record.get('strategy_name', ''),
                record.get('reason', '')
            ]
            
            # 添加数据到表格
            for col, value in enumerate(items):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row, col, item)
            
            # 滚动到最新记录
            table.scrollToBottom()
            
        except Exception as e:
            self.logger.error(f"更新交易记录失败: {str(e)}")

    def on_strategy_changed(self, row, text):
        """策略改变时的处理"""
        stock_code = self.tableWidget_2.item(row, 0).text()
        self.logger.info(f"[{stock_code}] 的策略改为: {text}")
        self.save_current_tasks()

    def on_cell_editing(self, row, column):
        """单元格编辑中的处理"""
        table = self.tableWidget_2
        
        # 只处理可编辑列（初始股数、初始成本、买入日期）
        if column in [2, 3, 4]:
            try:
                new_value = table.item(row, column).text()
                stock_code = table.item(row, 0).text()
                column_names = ["", "", "初始股数", "初始成本", "买入日期"]
                
                # 验证输入值
                if column == 2:  # 初始股数
                    int(new_value)  # 验证是否为整数
                elif column == 3:  # 初始成本
                    float(new_value)  # 验证是否为浮点数
                elif column == 4:  # 买入日期
                    # 使用parse_date_auto处理多种日期格式
                    buy_date_obj = parse_date_auto(new_value)
                    
                    # 使用回测选择的日期来计算持有天数
                    selected_date = self.dateEdit.date().toPyDate()
                    
                    # 计算持有交易日（从买入日期到选择的回测日期）
                    hold_days = 0
                    check_date = buy_date_obj.date() if isinstance(buy_date_obj, datetime) else buy_date_obj
                    self.logger.info(f"[{stock_code}] 开始计算持有天数: buy_date_obj={check_date}({type(check_date)}), selected_date={selected_date}({type(selected_date)})")
                    
                    # 从买入日期的下一天开始计算
                    check_date += timedelta(days=1)
                    
                    while check_date <= selected_date:
                        if is_tradeday(check_date):
                            hold_days += 1
                        check_date += timedelta(days=1)
                    
                    # 如果买入日期和选择日期是同一天，持有天数应该是0
                    if isinstance(buy_date_obj, datetime):
                        buy_date_obj = buy_date_obj.date()
                    if buy_date_obj == selected_date:
                        hold_days = 0
                        self.logger.info("买入日期和选择日期是同一天，持有天数设为0")
                    
                    # 更新持有交易日
                    hold_days_item = QTableWidgetItem(str(hold_days))
                    hold_days_item.setFlags(hold_days_item.flags() & ~Qt.ItemIsEditable)
                    hold_days_item.setTextAlignment(Qt.AlignCenter)
                    table.setItem(row, 5, hold_days_item)
                    
                    # 将日期格式化为标准格式
                    new_value = buy_date_obj.strftime('%Y-%m-%d')
                    table.item(row, column).setText(new_value)
                
                # 记录日志
                self.logger.info(f"[{stock_code}] 的{column_names[column]}修改为: {new_value}")
                # 保存任务
                self.save_current_tasks()
                
            except ValueError as e:
                # 如果输入值无效，恢复原值并提示错误
                self.logger.error(f"输入值无效: {str(e)}")
                QMessageBox.warning(
                    table,
                    "输入错误",
                    "请输入有效的值:\n初始股数需为整数\n初始成本需为数字\n买入日期格式为YYYY-MM-DD或YYYY/MM/DD",
                    QMessageBox.Ok
                )
                # 触发表格刷新
                #self.update_task_list(self.positions)

    def update_param_button_text(self, row, stock_code):
        """更新参数按钮的文本"""
        try:
            # 获取参数
            params = self.task_manager.get_task_params(stock_code)
            
            if not params:
                return
            
            # 更新按钮文本，使用更简洁的格式
            up_operation = params.get('up_operation', '卖出')
            down_operation = params.get('down_operation', '买入')
            cycle_times = params.get('cycle_times', 0)
            if cycle_times > 0:
                param_text = (f"{params['sell_times']}笔 循环{cycle_times}次 {params['max_days']}交易日\n"
                             f"↑{params['up_threshold']}%({up_operation}) ↓{params['down_threshold']}%({down_operation})")
            else:
                param_text = (f"{params['sell_times']}笔 {params['max_days']}交易日\n"
                             f"↑{params['up_threshold']}%({up_operation}) ↓{params['down_threshold']}%({down_operation})")
                         
            # 获取参数按钮并更新文本
            param_button = self.tableWidget_2.cellWidget(row, 8)
            if param_button:
                param_button.setText(param_text)
                # 在这里保存任务
                #self.save_current_tasks()
            
        except Exception as e:
            self.logger.error(f"更新参数按钮文本失败: {str(e)}", exc_info=True)

    def create_strategy_combo(self):
        """创建策略选择下拉框"""
        # 创建一个容器widget来包含combo box
        container = QWidget()
        layout = QVBoxLayout(container)
        
        combo = QComboBox()
        combo.addItems(["规则任务", "万能策略"])
        combo.setStyleSheet("""
            QComboBox {
                font-family: "Microsoft YaHei";
                font-size: 12pt;
                padding: 5px;
            }
        """)
        
        # 设置布局属性
        layout.addWidget(combo)
        layout.setAlignment(Qt.AlignCenter)  # 垂直居中
        layout.setContentsMargins(0, 0, 0, 0)  # 移除边距
        
        # 设置容器的属性
        container.combo = combo  # 保存combo box的引用
        
        return container

    def update_status_bar(self):
        """更新状态栏显示"""
        try:
            if not hasattr(self, 'task_manager'):
                return
                
            # 获取所有订阅的股票的显示文本
            displays = self.task_manager.price_displays
            if not displays:
                self.statusBar.showMessage("等待行情数据...")
                return
            
            # 直接使用格式化好的显示文本
            display_text = " | ".join([
                f"{code} {display}" 
                for code, display in displays.items()
            ])
            
            # 更新状态栏
            self.statusBar.showMessage(display_text)
            
        except Exception as e:
            self.logger.error(f"更新状态栏出错: {str(e)}")

    def set_task_manager(self, task_manager):
        """设置任务管理器"""
        self.task_manager = task_manager
        # 连接更新UI的信号
        self.task_manager.update_task_ui.connect(self.update_task_field)
        # 连接任务列表更新信号
        self.task_manager.tasks_updated.connect(lambda: self.update_task_list(self.positions))

    def update_task_field(self, stock_code, field_name, value):
        """更新任务列表中的特定字段"""
        table = self.tableWidget_2
        for row in range(table.rowCount()):
            if table.item(row, 0).text() == stock_code:
                if field_name == 'base_price':
                    # 更新基准价格列（第7列）
                    item = QTableWidgetItem(str(value))
                    item.setTextAlignment(Qt.AlignCenter)
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    table.setItem(row, 6, item)
                    
                    # 每次基准价格更新后，保存到文件
                    self.save_current_tasks()
                    self.logger.info(f"[{stock_code}]更新基准价为：{value}")
                    
                break

    def has_running_tasks(self):
        """检查是否有正在运行的任务"""
        table = self.tableWidget_2
        for row in range(table.rowCount()):
            status_item = table.item(row, 9)  # 状态列
            if status_item and status_item.text() == "运行中":
                return True
        return False

    def handle_close_event(self, event):
        """处理窗口关闭事件"""
        if self.has_running_tasks():
            reply = QMessageBox.warning(
                self.window,
                "警告",
                "还有正在运行的任务，请先停止所有任务再退出程序。",
                QMessageBox.Ok
            )
            event.ignore()  # 阻止关闭
        else:
            event.accept()  # 允许关闭

    def update_task_buttons(self, row):
        """更新任务按钮状态"""
        table = self.tableWidget_2
        operation_widget = table.cellWidget(row, 10)
        if operation_widget:
            start_button = operation_widget.layout().itemAt(0).widget()
            stop_button = operation_widget.layout().itemAt(1).widget()
            
            # 根据任务状态更新按钮状态
            status = table.item(row, 9).text()
            if status == "运行中":
                start_button.setEnabled(False)  # 运行中时禁用启动按钮
                stop_button.setEnabled(True)    # 运行中时启用停止按钮
            else:
                start_button.setEnabled(True)   # 未运行时启用启动按钮
                stop_button.setEnabled(False)   # 未运行时禁用停止按钮

    def append_log(self, text):
        """添加日志到文本框"""
        try:
            if hasattr(self, 'textEdit') and self.textEdit is not None:
                self.textEdit.append(str(text))
        except Exception as e:
            print(f"添加日志失败: {str(e)}")
            # 如果UI更新失败，记录到控制台
            print(f"日志内容: {text}")

    def load_positions(self):
        """加载选定日期的持仓数据"""
        if True:#try:
            # 获取选择的日期
            selected_date = self.dateEdit.date().toPyDate().strftime('%Y-%m-%d')
            
            # 构建文件路径
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            
            # 先加载持仓数据
            positions_file = os.path.join(current_dir, 'data', 'positions', f'positions_{selected_date}.csv')
            if not os.path.exists(positions_file):
                self.logger.warning(f"未找到{selected_date}的持仓数据")
                return
            
            # 读取CSV文件，尝试不同的编码格式
            try:
                df = pd.read_csv(positions_file, encoding='utf-8')
            except UnicodeDecodeError:
                try:
                    df = pd.read_csv(positions_file, encoding='gbk')
                except UnicodeDecodeError:
                    df = pd.read_csv(positions_file, encoding='gb18030')
            
            # 更新持仓数据
            positions = df.to_dict('records')
            positions = {pos['stock_code']: pos for pos in positions}
            self.positions = positions
            
            # 再加载任务备份
            tasks_file = os.path.join(current_dir, 'data', 'tasks', f'tasks_{selected_date}.xlsx')
            if os.path.exists(tasks_file):
                # 重置加载标志并加载任务备份
                self.task_manager.reset_load_flag()
                tasks = self.task_manager.load_tasks(tasks_file)
                self.saved_tasks = {task['stock_code']: task for task in tasks}
                self.logger.info(f"成功加载{selected_date}的任务备份")
            
            # 清空持仓表格
            self.tableWidget.setRowCount(0)
            # 清空任务列表表格
            self.tableWidget_2.setRowCount(0)
            
            # 计算总市值用于计算仓位比例
            total_market_value = df['market_value'].sum()
            
            # 填充持仓数据
            for index, row in df.iterrows():
                self.tableWidget.insertRow(index)
                
                # 计算仓位比例
                position_ratio = int(row['market_value'] / total_market_value * 100) if total_market_value > 0 else 0
                
                # 创建仓位条
                position_bar = QProgressBar()
                position_bar.setValue(position_ratio)
                position_bar.setFixedSize(100, 20)
                position_bar.setTextVisible(True)
                position_bar.setFormat(f"{position_ratio}%")
                
                # 创建仓位条容器
                position_container = QWidget()
                position_layout = QHBoxLayout(position_container)
                position_layout.addWidget(position_bar)
                position_layout.setContentsMargins(5, 0, 5, 0)
                position_layout.setAlignment(Qt.AlignCenter)
                
                # 设置仓位条到表格
                self.tableWidget.setCellWidget(index, 0, position_container)
                
                # 准备数据
                items = [
                    row['stock_code'],
                    row['stock_name'],
                    str(row['volume']),
                    str(row['can_use_volume']),
                    f"{row['open_price']:.3f}",
                    str(row['market_value'])
                ]
                
                # 添加数据到表格
                for col, value in enumerate(items):
                    item = QTableWidgetItem(str(value))
                    item.setTextAlignment(Qt.AlignCenter)
                    self.tableWidget.setItem(index, col + 1, item)
            
            self.logger.info(f"成功加载{selected_date}的持仓数据")
            
            # 更新任务列表
            self.update_task_list(positions)
            
        #except Exception as e:
        #    self.logger.error(f"加载持仓数据失败：{str(e)}")

    def create_batch_operation_buttons(self):
        """创建批量操作按钮"""
        # 创建容器widget
        widget = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(5, 5, 5, 5)
        
        # 创建全部启动按钮
        start_all_button = QPushButton("全部启动")
        start_all_button.setFixedWidth(100)
        start_all_button.clicked.connect(self.start_all_tasks)
        
        # 创建全部暂停按钮
        stop_all_button = QPushButton("全部暂停")
        stop_all_button.setFixedWidth(100)
        stop_all_button.clicked.connect(self.stop_all_tasks)
        
        # 设置按钮样式
        button_style = """
            QPushButton {
                font-family: "Microsoft YaHei";
                font-size: 12pt;
                padding: 5px;
                background-color: #f0f0f0;
                border: 1px solid #dcdcdc;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
            }
            QPushButton:pressed {
                background-color: #d0d0d0;
            }
        """
        start_all_button.setStyleSheet(button_style)
        stop_all_button.setStyleSheet(button_style)
        
        # 添加按钮到布局
        layout.addWidget(start_all_button)
        layout.addWidget(stop_all_button)
        layout.addStretch()  # 添加弹性空间，使按钮靠左对齐
        
        widget.setLayout(layout)
        return widget

    def update_backtest_status(self, stock_code, status):
        """更新回测状态"""
        try:
            table = self.tableWidget_2
            for row in range(table.rowCount()):
                if table.item(row, 0).text() == stock_code:
                    status_item = table.item(row, 9)
                    if status == "已完成":
                        status_item.setText("已完成")
                        status_item.setForeground(Qt.green)
                    elif status == "运行中":
                        # 显示当前回测进度
                        status_text = f"运行中({self.current_param_index}/{self.total_combinations})"
                        status_item.setText(status_text)
                        status_item.setForeground(Qt.red)
                    else:
                        status_item.setText(status)
                        status_item.setForeground(Qt.black)
                        
                    # 更新按钮状态
                    operation_widget = table.cellWidget(row, 10)
                    if operation_widget:
                        start_button = operation_widget.layout().itemAt(0).widget()
                        stop_button = operation_widget.layout().itemAt(1).widget()
                        if status == "已完成" or status == "未运行":
                            start_button.setEnabled(True)
                            stop_button.setEnabled(False)
                        elif status == "运行中":
                            start_button.setEnabled(False)
                            stop_button.setEnabled(True)
                            
                    break
                    
        except Exception as e:
            self.logger.error(f"更新回测状态失败: {str(e)}")
            self.logger.error(f"更新回测状态失败: {str(e)}")

    def analyze_limit_up(self):
        """分析涨停股票"""
        try:
            # 获取选择的日期
            selected_date = self.dateEdit.date().toPyDate()
            
            # 弹出文件选择对话框
            file_dialog = QFileDialog()
            file_dialog.setFileMode(QFileDialog.ExistingFile)
            file_dialog.setNameFilter("Excel文件 (*.xlsx *.xls)")
            
            if file_dialog.exec_():
                # 获取选择的文件路径
                excel_file = file_dialog.selectedFiles()[0]
            else:
                self.logger.warning("未选择文件")
                return
                
            # 读取Excel文件
            try:
                df = pd.read_excel(excel_file)
                # 将日期列转换为日期格式，去掉时间部分
                if '日期' in df.columns:
                    df['日期'] = pd.to_datetime(df['日期']).dt.date
                self.logger.info(f"Excel文件列名: {df.columns.tolist()}")
            except Exception as e:
                self.logger.error(f"读取Excel文件失败: {str(e)}")
                return
                
            # 分析结果
            self.textEdit.append(f"\n=== {selected_date} 首板分析 ===")
            # 创建新的DataFrame来存储分析结果
            new_df = pd.DataFrame(columns=df.columns.tolist() + ['选中时间','首次涨停时间', '涨停加单', '涨停撤单', '开板','最后开板时间'])
            # 遍历每只股票
            for _, row in df.iterrows():
                try:
                    # 将股票代码转换为字符串并补齐6位
                    stock_code = str(row['股票代码']).zfill(6)
                    # 根据股票代码添加市场后缀
                    if stock_code.startswith(('0', '1', '3')):
                        stock_code = f"{stock_code}.SZ"
                    elif stock_code.startswith(('5', '6')):
                        stock_code = f"{stock_code}.SH"
                    elif stock_code.startswith(('4', '8', '920')):
                        stock_code = f"{stock_code}.BJ"
                    
                    stock_name = row['股票名称']
                    date = row['日期']
                    # 把date转换为日期类型，确保没有时间部分
                    if isinstance(date, str):
                        date = pd.to_datetime(date).date()
                    elif isinstance(date, pd.Timestamp):
                        date = date.date()
                    self.logger.info(f"处理股票: {stock_code}, 名称: {stock_name}, 日期: {date}")
                    # end_date 
                    end_date = datetime.now().date()
                    
                    # 创建回测引擎
                    engine = BacktestEngine(stock_code=stock_code)
                    engine.set_logger(self.logger)
                    
                    # 加载历史数据
                    success = engine.load_data(date, end_date)
                    if success and engine.data is not None and not engine.data.empty:
                        # 统计tick数据个数
                        tick_count = len(engine.data)
                        
                        # 显示统计结果
                        self.textEdit.append(f"\n{stock_code} {selected_date}至今 tick数据异动统计：")
                        self.textEdit.append(f"总tick数：{tick_count}")
                        
                        # 初始化涨停标记和上一个tick的数据
                        is_up_limit = False
                        is_first_up_limit = False
                        up_limit_price = 0
                        prev_tick = None
                        selected_time = None
                        first_up_limit_time = None
                        last_limit_up_open_time = None
                        limit_up_increase = False
                        limit_up_decrease = False
                        limit_up_open = False
                        selected_rate = 0
                        if stock_code.startswith(('3')):
                            selected_rate = 17
                        else:
                            selected_rate = 8.5

                        # 遍历所有tick数据
                        for index, tick in engine.data.iterrows():
                            # 剔除掉非交易时间的数据，tick.name是字符串类型，格式为：20250509145702
                            #交易时段是9:30-11:30,13:00-15:00
                            # 获取交易时段，是字符串类型，格式为：20250509145702
                            trade_day = tick.name[0:8]
                            date_str = date.strftime("%Y%m%d")
                            #print(f"trade_day: {trade_day}, date_str: {date_str}")
                            if trade_day != date_str:
                                break
                            trade_time = tick.name[8:14]
                            if trade_time < '093000' or trade_time > '150000':
                                is_up_limit = False
                                is_first_up_limit= False
                                up_limit_price = 0
                                prev_tick = None
                                continue

                            if selected_time is None:
                                last_price = tick['lastPrice']
                                last_close = tick['lastClose']
                                if (last_price - last_close) / last_close > selected_rate / 100:
                                    selected_time = tick.name = f"{tick.name[8:10]}:{tick.name[10:12]}:{tick.name[12:14]}"
                            
                            # 第一个卖一价和卖一量都是0的标记为涨停，获得最新价为涨停板，随后的涨停板不再输出，直到出现卖一价和卖一量不是0的为止，此时输出开板
                            if tick['askPrice'][0] == 0 and tick['askVol'][0] == 0:
                                #只输出连续涨停的第一个涨停板
                                if not is_up_limit:
                                    is_up_limit = True
                                    is_first_up_limit = True
                                    up_limit_price = tick['lastPrice']
                                    # 输出当前封单量
                                    current_seal = tick['bidVol'][0] * tick['lastPrice']
                                    # 根据股票代码设置价格精度
                                    price_precision = 3 if stock_code.startswith(('5', '1')) else 2
                                    if trade_time == '093000':
                                        tmp_string = '开盘涨停'
                                    else:
                                        tmp_string = '涨停'
                                    self.textEdit.append(f"{tick.name} {tmp_string} 最新价：{tick['lastPrice']:.{price_precision}f}，封单量：{tick['bidVol'][0]}手，金额{current_seal/100:.2f}万")
                                    # 将时间格式从 20250507110815 转换为 11:08:15
                                    if first_up_limit_time is None:
                                        # 检查时间格式
                                        if ':' in tick.name:  # 已经是 HH:MM:SS 格式
                                            first_up_limit_time = tick.name
                                        else:  # 是 20250507110815 格式
                                            time_str = tick.name[8:14]  # 获取时间部分 110815
                                            if len(time_str) == 6:  # 确保时间字符串长度正确
                                                first_up_limit_time = f"{time_str[0:2]}:{time_str[2:4]}:{time_str[4:6]}"
                                            else:
                                                self.logger.error(f"时间字符串格式错误: {time_str}")
                                                first_up_limit_time = None
                                
                                # 检查涨停板加单和撤单
                                if prev_tick is not None and not is_first_up_limit:
                                    if prev_tick['bidVol'][0] > 0:
                                        bid_vol_diff = tick['bidVol'][0] - prev_tick['bidVol'][0]
                                        bid_vol_ratio = bid_vol_diff / prev_tick['bidVol'][0]
                                        if bid_vol_diff > 0:  # 加单
                                            add_amount = bid_vol_diff * tick['lastPrice']
                                            if add_amount > 1000000 or bid_vol_ratio > 0.5:  # 大于100万或者大于50%
                                                if trade_time == '150000':
                                                    self.textEdit.append(f"{tick.name} 收盘封涨停板：当前封单：{tick['bidVol'][0]}手，金额+{add_amount/100:.2f}万")
                                                    limit_up_increase = True
                                                else:
                                                    self.textEdit.append(f"{tick.name} 涨停XXX加单：{bid_vol_diff}手，金额+{add_amount/100:.2f}万，增加比例{bid_vol_ratio*100:.2f}%，当前封单：{tick['bidVol'][0]}手")
                                        elif bid_vol_diff < 0: # and tick['lastPrice'] < up_limit_price:  # 撤单
                                            self.textEdit.append(f"{tick['lastPrice']},{up_limit_price}")
                                            cancel_amount = abs(bid_vol_diff) * tick['lastPrice']
                                            if cancel_amount > 1000000 or bid_vol_ratio < -0.5:  # 大于100万或者小于-50%
                                                self.textEdit.append(f"{tick.name} 涨停XXX撤单：{abs(bid_vol_diff)}手，金额-{cancel_amount/100:.2f}万，减少比例{abs(bid_vol_ratio)*100:.2f}%，当前封单：{tick['bidVol'][0]}手")
                                                limit_up_decrease = True
                                
                                is_first_up_limit = False
                            
                            else:
                                # 如果当前不是涨停板，则输出开板
                                if is_up_limit and tick['lastPrice'] < up_limit_price:
                                    # 根据股票代码设置价格精度
                                    price_precision = 3 if stock_code.startswith(('5', '1')) else 2
                                    self.textEdit.append(f"{tick.name} 开板 最新价：{tick['lastPrice']:.{price_precision}f}")
                                    is_up_limit = False
                                    limit_up_open = True
                                    # 从tick.name中提取时间部分，格式为20250507110815
                                    time_str = tick.name[8:14]  # 获取时间部分 110815
                                    self.logger.info(f"原始时间字符串: {tick.name}, 提取的时间部分: {time_str}")
                                    if len(time_str) == 6:  # 确保时间字符串长度正确
                                        last_limit_up_open_time = f"{time_str[0:2]}:{time_str[2:4]}:{time_str[4:6]}"
                                # 更新上一个tick的数据
                                prev_tick = tick
                        
                        # 强制刷新UI
                        QApplication.processEvents()
                        
                        # 创建新的行数据
                        new_row = row.copy()
                        new_row['选中时间'] = selected_time
                        new_row['首次涨停时间'] = first_up_limit_time
                        new_row['涨停加单'] = limit_up_increase
                        new_row['涨停撤单'] = limit_up_decrease
                        new_row['开板'] = limit_up_open
                        new_row['最后开板时间'] = last_limit_up_open_time
                        # 使用concat添加新行，并重置索引
                        new_df = pd.concat([new_df, pd.DataFrame([new_row])], ignore_index=True)
                        
                    else:
                        self.textEdit.append(f"未获取到{stock_code}的tick数据")
                        # 强制刷新UI
                        QApplication.processEvents()
                
                except Exception as e:
                    self.logger.error(f"分析{stock_code}失败: {str(e)}")
                    # 即使分析失败，也添加一行数据，但标记为失败
                    try:
                        new_row = row.copy()
                        new_row['选中时间'] = None
                        new_row['首次涨停时间'] = None
                        new_row['涨停加单'] = False
                        new_row['涨停撤单'] = False
                        new_row['开板'] = False
                        new_row['最后开板时间'] = None
                        new_df = pd.concat([new_df, pd.DataFrame([new_row])], ignore_index=True)
                    except Exception as e2:
                        self.logger.error(f"添加失败记录时出错: {str(e2)}")
                    continue
            
            self.textEdit.append("\n=== 分析完成 ===\n")
            # 把new_df保存到excel，确保日期列没有时间部分
            new_excel_file = excel_file.replace('.xlsx', '_new.xlsx')
            if '日期' in new_df.columns:
                new_df['日期'] = pd.to_datetime(new_df['日期']).dt.date
            new_df.to_excel(new_excel_file, index=False)
        except Exception as e:
            self.logger.error(f"首板分析失败: {str(e)}")

    def show_version_dialog(self):
        """显示版本信息对话框"""
        try:
            from ui.dialogs import VersionDialog
            dialog = VersionDialog(self.window)
            dialog.exec_()
        except Exception as e:
            self.logger.error(f"显示版本对话框失败: {str(e)}")
            QMessageBox.critical(self.window, "错误", f"显示版本对话框失败: {str(e)}")

    def show_read_before_use_dialog(self):
        """显示使用前必读对话框"""
        try:
            from ui.dialogs import ReadBeforeUseDialog
            dialog = ReadBeforeUseDialog(self.window)
            dialog.exec_()
        except Exception as e:
            self.logger.error(f"显示使用前必读对话框失败: {str(e)}")
            QMessageBox.critical(self.window, "错误", f"显示使用前必读对话框失败: {str(e)}")

    def on_column_resized(self, logicalIndex, oldSize, newSize):
        """处理列宽调整事件，确保状态列保持固定宽度"""
        try:
            # 确保状态列(索引9)保持固定宽度120
            if logicalIndex == 9:  # 状态列
                self.tableWidget_2.setColumnWidth(logicalIndex, 120)
                return
        except Exception as e:
            self.logger.error(f"列宽调整处理出错: {str(e)}")
            # 即使出错也要确保固定列保持固定宽度
            try:
                if logicalIndex == 9:
                    self.tableWidget_2.setColumnWidth(logicalIndex, 120)
            except:
                pass