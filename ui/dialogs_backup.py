from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                              QSpinBox, QDoubleSpinBox, QDialogButtonBox, 
                              QComboBox, QLineEdit, QMessageBox, QCheckBox,
                              QTimeEdit, QPushButton, QInputDialog, QTextEdit,
                              QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
                              QDateEdit, QApplication, QTabWidget, QScrollArea, QWidget,
                              QFrame, QFileDialog)
from PyQt5.QtCore import Qt, QTime, QThread, pyqtSignal, QDate, QTimer
from PyQt5.QtGui import QFont, QColor
import logging
from datetime import datetime, time, date, timedelta
import time as time_module
from utils.trading_day import is_tradeday
import pandas as pd
import configparser
import os

class VolumeEditDialog(QDialog):
    """数量编辑对话框"""
    def __init__(self, parent=None, current_volume=0, task_type=''):
        super().__init__(parent)
        self.current_volume = current_volume
        self.task_type = task_type
        self.setWindowTitle("编辑数量")
        # 去掉右上角的问号（帮助按钮）
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout()
        
        # 创建统一的字体
        font = self.font()
        font.setPointSize(10)  # 设置字体大小为10pt
        
        # 数量输入
        volume_layout = QHBoxLayout()
        volume_label = QLabel("数量(股):")
        volume_label.setFont(font)
        volume_layout.addWidget(volume_label)
        self.volume_spin = QSpinBox()
        self.volume_spin.setRange(100, 1000000)
        self.volume_spin.setSingleStep(100)
        self.volume_spin.setValue(self.current_volume)
        self.volume_spin.setFont(font)
        volume_layout.addWidget(self.volume_spin)
        layout.addLayout(volume_layout)
        
        # 确定取消按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        # 设置按钮字体
        for button in buttons.buttons():
            button.setFont(font)
        layout.addWidget(buttons)
        
        self.setLayout(layout)
        
    def get_volume(self):
        """获取编辑后的数量"""
        return self.volume_spin.value()

class ParameterDialog(QDialog):
    """参数设置对话框"""
    def __init__(self, parent=None, current_params=None, base_price=None):
        super().__init__(parent)
        self.current_params = current_params or {}
        self.base_price = base_price or 0.0
        self.setWindowTitle("策略参数设置")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout()
        
        # 创建统一的字体
        font = self.font()
        font.setPointSize(10)  # 设置字体大小为10pt
        
        # 第一行：分仓笔数
        sell_times_layout = QHBoxLayout()
        sell_times_label = QLabel("分仓笔数")
        sell_times_label.setFont(font)
        sell_times_layout.addWidget(sell_times_label)
        self.sell_times = QSpinBox()
        self.sell_times.setRange(1, 100)
        self.sell_times.setValue(self.current_params.get('sell_times', 3))
        self.sell_times.setFont(font)
        sell_times_layout.addWidget(self.sell_times)
        layout.addLayout(sell_times_layout)
        
        # 第二行：循环次数
        cycle_times_layout = QHBoxLayout()
        cycle_times_label = QLabel("循环次数")
        cycle_times_label.setFont(font)
        cycle_times_layout.addWidget(cycle_times_label)
        self.cycle_times = QSpinBox()
        self.cycle_times.setRange(0, 9999)
        self.cycle_times.setValue(self.current_params.get('cycle_times', 0))
        self.cycle_times.setFont(font)
        cycle_times_layout.addWidget(self.cycle_times)
        layout.addLayout(cycle_times_layout)
        
        # 第三行：基准价格（只读显示）
        base_price_layout = QHBoxLayout()
        base_price_label = QLabel("基准价格")
        base_price_label.setFont(font)
        base_price_layout.addWidget(base_price_label)
        self.base_price_label = QLabel(f"{self.base_price:.3f}")
        self.base_price_label.setFont(font)
        self.base_price_label.setStyleSheet("color: gray; background-color: #f0f0f0; padding: 2px; border: 1px solid #ccc;")
        base_price_layout.addWidget(self.base_price_label)
        layout.addLayout(base_price_layout)
        
        # 第四行：清仓设置
        clear_layout = QHBoxLayout()
        
        # 清仓复选框
        self.enable_clear = QCheckBox("今日清仓")
        clear_time = self.current_params.get('clear_time', '00:00:00')
        self.enable_clear.setChecked(clear_time != '00:00:00')  # 根据现有参数设置复选框状态
        self.enable_clear.setFont(font)
        clear_layout.addWidget(self.enable_clear)
        
        # 清仓时间标签
        clear_time_label = QLabel("指定清仓时间:")
        clear_time_label.setFont(font)
        clear_layout.addWidget(clear_time_label)
        
        # 时间选择控件
        self.clear_time = QTimeEdit()
        self.clear_time.setDisplayFormat("HH:mm:ss")
        self.clear_time.setFont(font)
        
        # 根据现有参数设置时间
        if clear_time != '00:00:00':
            try:
                from datetime import datetime
                time_obj = datetime.strptime(clear_time, '%H:%M:%S').time()
                self.clear_time.setTime(QTime(time_obj.hour, time_obj.minute, time_obj.second))
            except:
                self.clear_time.setTime(QTime(14, 30, 0))  # 默认14:30:00
        else:
            self.clear_time.setTime(QTime(14, 30, 0))  # 默认14:30:00
            
        self.clear_time.setEnabled(self.enable_clear.isChecked())  # 根据复选框状态设置启用状态
        clear_layout.addWidget(self.clear_time)
        
        # 连接复选框信号
        self.enable_clear.toggled.connect(self.clear_time.setEnabled)
        
        layout.addLayout(clear_layout)
        
        # 第四行：涨跌幅阈值
        threshold_layout = QHBoxLayout()
        
        # 上涨阈值
        up_layout = QVBoxLayout()
        up_label = QLabel("上涨阈值(%)")
        up_label.setFont(font)
        up_layout.addWidget(up_label)
        self.up_threshold = QDoubleSpinBox()
        self.up_threshold.setRange(0.1, 20.0)
        self.up_threshold.setDecimals(2)
        self.up_threshold.setValue(self.current_params.get('up_threshold', 5.0))
        self.up_threshold.setFont(font)
        self.up_threshold.valueChanged.connect(self.update_threshold_prices)
        up_layout.addWidget(self.up_threshold)
        
        # 上涨阈值对应的价格显示
        self.up_price_label = QLabel("价格: --")
        self.up_price_label.setFont(font)
        self.up_price_label.setStyleSheet("color: red; font-weight: bold;")
        up_layout.addWidget(self.up_price_label)
        
        threshold_layout.addLayout(up_layout)
        
        # 下跌阈值
        down_layout = QVBoxLayout()
        down_label = QLabel("下跌阈值(%)")
        down_label.setFont(font)
        down_layout.addWidget(down_label)
        self.down_threshold = QDoubleSpinBox()
        self.down_threshold.setRange(0.1, 20.0)
        self.down_threshold.setDecimals(2)
        self.down_threshold.setValue(self.current_params.get('down_threshold', 3.0))
        self.down_threshold.setFont(font)
        self.down_threshold.valueChanged.connect(self.update_threshold_prices)
        down_layout.addWidget(self.down_threshold)
        
        # 下跌阈值对应的价格显示
        self.down_price_label = QLabel("价格: --")
        self.down_price_label.setFont(font)
        self.down_price_label.setStyleSheet("color: green; font-weight: bold;")
        down_layout.addWidget(self.down_price_label)
        
        threshold_layout.addLayout(down_layout)
        
        layout.addLayout(threshold_layout)
        
        # 第五行：操作设置
        operation_layout = QHBoxLayout()
        
        # 上涨操作
        up_operation_layout = QVBoxLayout()
        up_operation_label = QLabel("上涨操作")
        up_operation_label.setFont(font)
        up_operation_layout.addWidget(up_operation_label)
        self.up_operation = QComboBox()
        self.up_operation.addItems(["买入", "卖出", "不动"])
        up_operation_value = self.current_params.get('up_operation', '卖出')
        up_operation_index = self.up_operation.findText(up_operation_value)
        if up_operation_index >= 0:
            self.up_operation.setCurrentIndex(up_operation_index)
        self.up_operation.setFont(font)
        up_operation_layout.addWidget(self.up_operation)
        operation_layout.addLayout(up_operation_layout)
        
        # 下跌操作
        down_operation_layout = QVBoxLayout()
        down_operation_label = QLabel("下跌操作")
        down_operation_label.setFont(font)
        down_operation_layout.addWidget(down_operation_label)
        self.down_operation = QComboBox()
        self.down_operation.addItems(["买入", "卖出", "不动"])
        down_operation_value = self.current_params.get('down_operation', '买入')
        down_operation_index = self.down_operation.findText(down_operation_value)
        if down_operation_index >= 0:
            self.down_operation.setCurrentIndex(down_operation_index)
        self.down_operation.setFont(font)
        down_operation_layout.addWidget(self.down_operation)
        operation_layout.addLayout(down_operation_layout)
        
        layout.addLayout(operation_layout)
        
        # 确定取消按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        # 设置按钮字体
        for button in buttons.buttons():
            button.setFont(font)
        layout.addWidget(buttons)
        
        self.setLayout(layout)
        
        # 初始化价格显示
        self.update_threshold_prices()
        
    def update_threshold_prices(self):
        """更新阈值对应的价格显示"""
        try:
            base_price = self.base_price
            if base_price > 0:
                # 计算上涨阈值对应的价格
                up_threshold = self.up_threshold.value()
                up_price = base_price * (1 + up_threshold / 100)
                self.up_price_label.setText(f"价格: {up_price:.3f}")
                
                # 计算下跌阈值对应的价格
                down_threshold = self.down_threshold.value()
                down_price = base_price * (1 - down_threshold / 100)
                self.down_price_label.setText(f"价格: {down_price:.3f}")
            else:
                self.up_price_label.setText("价格: --")
                self.down_price_label.setText("价格: --")
        except Exception as e:
            self.up_price_label.setText("价格: --")
            self.down_price_label.setText("价格: --")

    def get_params(self):
        """获取参数"""
        # 根据复选框状态确定清仓时间
        if self.enable_clear.isChecked():
            clear_time = self.clear_time.time().toString("HH:mm:ss")
        else:
            clear_time = "00:00:00"  # 表示不清仓
        
        return {
            'sell_times': self.sell_times.value(),
            'cycle_times': self.cycle_times.value(),
            'clear_time': clear_time,
            'up_threshold': self.up_threshold.value(),
            'down_threshold': self.down_threshold.value(),
            'up_operation': self.up_operation.currentText(),
            'down_operation': self.down_operation.currentText()
        }

class NightSellParameterDialog(QDialog):
    """夜市卖出参数设置对话框"""
    def __init__(self, parent=None, positions=None, qmt_adapter=None):
        super().__init__(parent)
        self.positions = positions or {}
        self.qmt_adapter = qmt_adapter
        self.setWindowTitle("夜市卖出参数设置")
        # 去掉右上角的问号（帮助按钮）
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout()
        
        # 创建统一的字体
        font = self.font()
        font.setPointSize(10)  # 设置字体大小为10pt
        
        # 第一行：股票代码选择
        code_layout = QHBoxLayout()
        code_label = QLabel("股票代码")
        code_label.setFont(font)
        code_layout.addWidget(code_label)
        self.stock_combo = QComboBox()
        self.stock_combo.setPlaceholderText("请选择股票")
        self.stock_combo.setFont(font)
        
        # 从持仓中加载股票选项
        for stock_code, stock_data in self.positions.items():
            if stock_data.get('volume', 0) > 0:  # 只显示有持仓的股票
                stock_name = stock_data.get('stock_name', '')
                display_text = f"{stock_code} {stock_name}"
                self.stock_combo.addItem(display_text, stock_code)
        
        self.stock_combo.currentIndexChanged.connect(self.on_stock_changed)
        code_layout.addWidget(self.stock_combo)
        layout.addLayout(code_layout)
        
        # 第二行：卖出价格
        price_layout = QHBoxLayout()
        price_label = QLabel("卖出价格")
        price_label.setFont(font)
        price_layout.addWidget(price_label)
        
        # 价格显示标签
        self.sell_price_label = QLabel("选择股票后自动获取")
        self.sell_price_label.setFont(font)
        self.sell_price_label.setStyleSheet("color: gray;")
        price_layout.addWidget(self.sell_price_label)
        
        # 手动设置按钮
        self.set_price_btn = QPushButton("手动设置")
        self.set_price_btn.setFont(font)
        self.set_price_btn.clicked.connect(self.set_manual_price)
        self.set_price_btn.setEnabled(False)
        price_layout.addWidget(self.set_price_btn)
        
        layout.addLayout(price_layout)
        
        # 第三行：卖出数量
        volume_layout = QHBoxLayout()
        volume_label = QLabel("卖出数量(股)")
        volume_label.setFont(font)
        volume_layout.addWidget(volume_label)
        self.sell_volume = QSpinBox()
        self.sell_volume.setRange(100, 1000000)
        self.sell_volume.setSingleStep(100)
        self.sell_volume.setFont(font)
        volume_layout.addWidget(self.sell_volume)
        layout.addLayout(volume_layout)
        
        # 提示信息
        info_label = QLabel("提示：夜市卖出将以跌停板价格或成本价下单，确保次日开盘时优先成交")
        info_label.setFont(font)
        info_label.setStyleSheet("color: blue; font-style: italic;")
        layout.addWidget(info_label)
        
        # 确定取消按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        # 设置按钮字体
        for button in buttons.buttons():
            button.setFont(font)
        layout.addWidget(buttons)
        
        self.setLayout(layout)
        
        # 存储卖出价格
        self.sell_price = None
        
        # 初始化卖出数量和价格
        self.on_stock_changed()
        
    def on_stock_changed(self):
        """股票选择改变时的处理"""
        current_index = self.stock_combo.currentIndex()
        if current_index >= 0:
            stock_code = self.stock_combo.currentData()
            if stock_code in self.positions:
                stock_data = self.positions[stock_code]
                max_volume = stock_data.get('volume', 0)
                self.sell_volume.setMaximum(max_volume)
                self.sell_volume.setValue(max_volume)  # 默认设置为最大可卖数量
                
                # 启用手动获取按钮
                self.set_price_btn.setEnabled(True)
                
                # 自动获取卖出价格
                self.auto_get_sell_price(stock_code, stock_data)
        else:
            # 重置状态
            self.sell_price = None
            self.sell_price_label.setText("选择股票后自动获取")
            self.sell_price_label.setStyleSheet("color: gray;")
            self.set_price_btn.setEnabled(False)
    
    def auto_get_sell_price(self, stock_code, stock_data):
        """自动获取卖出价格"""
        try:
            # 检查QMT适配器是否可用
            if not self.qmt_adapter:
                self.sell_price_label.setText("QMT连接不可用")
                self.sell_price_label.setStyleSheet("color: red;")
                return
            
            # 检查当前时间，判断是否在交易时间内
            current_time = datetime.now().time()
            trading_end_time = time(15, 0, 0)  # 15:00:00
            
            # 检查是否是交易日且在15:00前
            is_trading_day_today = is_tradeday(datetime.now().date())
            is_before_close = current_time < trading_end_time and is_trading_day_today
            
            # 显示正在获取状态
            if is_before_close:
                self.sell_price_label.setText("正在获取当前价格（交易时间内）...")
            else:
                self.sell_price_label.setText("正在自动获取价格...")
            self.sell_price_label.setStyleSheet("color: orange;")
            
            # 获取成本价
            cost_price = stock_data.get('open_price', 0)
            
            # 尝试获取跌停板价格
            limit_down_price = self._get_limit_down_price(stock_code)
            
            # 如果在交易时间内，提醒用户
            if is_before_close and limit_down_price:
                reply = QMessageBox.question(
                    self, 
                    "交易时间提醒", 
                    f"当前时间 {current_time.strftime('%H:%M:%S')} 在交易时间内（15:00前），\n"
                    f"获取到的价格可能不是收盘价。\n\n"
                    f"是否继续使用当前价格计算跌停板价格？\n"
                    f"（建议在15:00后下单以确保使用收盘价）",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )
                
                if reply == QMessageBox.No:
                    self.sell_price_label.setText("用户取消，请在15:00后重新获取")
                    self.sell_price_label.setStyleSheet("color: orange;")
                    return
            
            # 选择更优的价格（优先使用跌停板价格，如果没有则使用成本价）
            if limit_down_price and limit_down_price > 0:
                self.sell_price = limit_down_price
                if is_before_close:
                    self.sell_price_label.setText(f"跌停板价格: {limit_down_price:.2f} (基于当前价)")
                else:
                    self.sell_price_label.setText(f"跌停板价格: {limit_down_price:.2f}")
                self.sell_price_label.setStyleSheet("color: green; font-weight: bold;")
            elif cost_price > 0:
                self.sell_price = cost_price
                self.sell_price_label.setText(f"成本价: {cost_price:.2f}")
                self.sell_price_label.setStyleSheet("color: blue; font-weight: bold;")
            else:
                self.sell_price_label.setText("无法获取价格，请手动获取")
                self.sell_price_label.setStyleSheet("color: orange;")
            
        except Exception as e:
            self.sell_price_label.setText("自动获取失败，请手动获取")
            self.sell_price_label.setStyleSheet("color: red;")
    
    def _get_limit_down_price(self, stock_code):
        """获取跌停板价格"""
        try:
            # 补齐股票代码后缀
            full_stock_code = self._get_full_stock_code(stock_code)
            
            # 使用现有的订阅系统添加股票到订阅列表
            if hasattr(self.qmt_adapter, 'subscribe_thread') and self.qmt_adapter.subscribe_thread:
                if hasattr(self.qmt_adapter.subscribe_thread, '_stock_codes'):
                    current_subscribed = self.qmt_adapter.subscribe_thread._stock_codes.copy()
                    if full_stock_code not in current_subscribed:
                        new_subscribed = current_subscribed | {full_stock_code}
                        self.qmt_adapter.subscribe_thread.update_subscribe_list(list(new_subscribed), force_update=True)
            
            # 等待一段时间获取数据
            time_module.sleep(1.5)  # 等待1.5秒获取数据
            
            # 检查缓存中是否有数据
            if (hasattr(self.qmt_adapter, 'task_manager') and 
                self.qmt_adapter.task_manager and 
                hasattr(self.qmt_adapter.task_manager, 'latest_prices')):
                
                # 查找缓存中的价格数据
                cached_latest_price = None
                
                # 尝试多种股票代码格式
                possible_codes = [stock_code, full_stock_code]
                if stock_code.startswith('00') or stock_code.startswith('30'):
                    possible_codes.append(f"{stock_code}.SZ")
                elif stock_code.startswith('60') or stock_code.startswith('68'):
                    possible_codes.append(f"{stock_code}.SH")
                
                for code in possible_codes:
                    if code in self.qmt_adapter.task_manager.latest_prices:
                        cached_latest_price = self.qmt_adapter.task_manager.latest_prices[code]
                        break
                
                if cached_latest_price is not None and cached_latest_price > 0:
                    # 计算跌停板价格（基于当天收盘价计算次日跌停板）
                    return self.calculate_limit_down_price(stock_code, cached_latest_price)
            
            return None
            
        except Exception as e:
            return None
    
    def _get_full_stock_code(self, stock_code):
        """获取完整的股票代码"""
        if len(stock_code) == 6:
            if stock_code.startswith(('0', '1', '3')):
                return f"{stock_code}.SZ"
            elif stock_code.startswith(('5', '6')):
                return f"{stock_code}.SH"
            elif stock_code.startswith(('4', '8', '920')):
                return f"{stock_code}.BJ"
        return stock_code
    
    def calculate_limit_down_price(self, stock_code, close_price):
        """计算跌停板价格"""
        try:
            # 统一的涨跌停比例判定逻辑
            # 优先识别 ST 与 *ST（5%）
            try:
                from utils.stock_info_manager import get_stock_name
                stock_name = get_stock_name(stock_code) or ""
            except Exception:
                stock_name = ""

            if ('ST' in stock_name.upper()):
                limit_ratio = 0.05
            elif stock_code.startswith('300') or stock_code.startswith('688'):
                # 创业板和科创板 20%
                limit_ratio = 0.20
            elif stock_code.startswith('8') or stock_code.startswith('4') or stock_code.startswith('920'):
                # 北交所 30%
                limit_ratio = 0.30
            else:
                # 其他主板 10%
                limit_ratio = 0.10

            limit_down_price = close_price * (1 - limit_ratio)
            
            # 根据股票类型确定价格精度
            # 使用统一精度工具确定四舍五入位数
            try:
                from core.utils.security_type import SecurityTypeUtil
                precision = SecurityTypeUtil.get_price_precision(stock_code)
            except Exception:
                precision = 2
            result = round(limit_down_price, precision)
            
            return result
                
        except Exception as e:
            return close_price * 0.9  # 默认按10%计算
        
    def get_sell_params(self):
        """获取卖出参数"""
        stock_code = self.stock_combo.currentData() if self.stock_combo.currentIndex() >= 0 else ""
        return {
            'stock_code': stock_code,
            'sell_volume': self.sell_volume.value(),
            'sell_type': '限价',
            'sell_price': self.sell_price if self.sell_price else 0.0
        }
    
    def set_manual_price(self):
        """手动设置卖出价格"""
        try:
            current_index = self.stock_combo.currentIndex()
            if current_index < 0:
                QMessageBox.warning(self, "警告", "请先选择股票")
                return
            
            stock_code = self.stock_combo.currentData()
            stock_data = self.positions.get(stock_code, {})
            
            # 弹出输入对话框
            price, ok = QInputDialog.getDouble(
                self, 
                "手动设置卖出价格", 
                "请输入卖出价格:", 
                0.0,  # 默认值
                0.0,  # 最小值
                999999.99,  # 最大值
                2  # 小数位数
            )
            
            if not ok:  # 用户取消
                return
            
            # 检查输入是否为有效数字
            if price <= 0:
                QMessageBox.warning(self, "警告", "请输入有效的正数价格")
                return
            
            # 更新显示
            self.sell_price = price
            self.sell_price_label.setText(f"卖出价格: {price:.2f}")
            self.sell_price_label.setStyleSheet("color: blue; font-weight: bold;")
            QMessageBox.information(self, "成功", f"已手动设置卖出价格为: {price:.2f}")
            
        except Exception as e:
            self.sell_price_label.setText("手动设置失败")
            self.sell_price_label.setStyleSheet("color: red;")
            QMessageBox.critical(self, "错误", f"手动设置卖出价格失败：{str(e)}")

class NightBuyParameterDialog(QDialog):
    """夜市买入参数设置对话框"""
    def __init__(self, parent=None, qmt_adapter=None):
        super().__init__(parent)
        self.qmt_adapter = qmt_adapter
        self.setWindowTitle("夜市买入参数设置")
        # 去掉右上角的问号（帮助按钮）
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout()
        
        # 创建统一的字体
        font = self.font()
        font.setPointSize(10)  # 设置字体大小为10pt
        
        # 第一行：股票代码
        code_layout = QHBoxLayout()
        code_label = QLabel("股票代码")
        code_label.setFont(font)
        code_layout.addWidget(code_label)
        self.stock_code = QLineEdit()
        self.stock_code.setPlaceholderText("请输入股票代码")
        self.stock_code.setFont(font)
        # 添加文本变化事件，实现自动获取价格
        self.stock_code.textChanged.connect(self.on_stock_code_changed)
        code_layout.addWidget(self.stock_code)
        layout.addLayout(code_layout)
        
        # 第二行：涨停板价格显示
        price_layout = QHBoxLayout()
        price_label = QLabel("买入价格")
        price_label.setFont(font)
        price_layout.addWidget(price_label)
        
        # 涨停板价格显示
        self.limit_up_price_label = QLabel("输入股票代码后自动获取")
        self.limit_up_price_label.setFont(font)
        self.limit_up_price_label.setStyleSheet("color: gray;")
        price_layout.addWidget(self.limit_up_price_label)
        
        # 手动设置按钮
        self.set_price_btn = QPushButton("手动设置")
        self.set_price_btn.setFont(font)
        self.set_price_btn.clicked.connect(self.set_manual_price)
        self.set_price_btn.setEnabled(False)  # 默认禁用
        price_layout.addWidget(self.set_price_btn)
        
        layout.addLayout(price_layout)
        
        # 第三行：买入数量
        volume_layout = QVBoxLayout()
        volume_label = QLabel("买入数量(股)")
        volume_label.setFont(font)
        volume_layout.addWidget(volume_label)
        
        # 数量输入框
        self.buy_volume = QSpinBox()
        self.buy_volume.setRange(100, 1000000)
        self.buy_volume.setSingleStep(100)
        self.buy_volume.setFont(font)
        volume_layout.addWidget(self.buy_volume)
        
        # 快速选择按钮布局
        quick_select_layout = QHBoxLayout()
        
        # 全仓按钮
        self.full_position_btn = QPushButton("全仓")
        self.full_position_btn.setFont(font)
        self.full_position_btn.clicked.connect(lambda: self.quick_select_volume(1.0))
        quick_select_layout.addWidget(self.full_position_btn)
        
        # 半仓按钮
        self.half_position_btn = QPushButton("半仓")
        self.half_position_btn.setFont(font)
        self.half_position_btn.clicked.connect(lambda: self.quick_select_volume(0.5))
        quick_select_layout.addWidget(self.half_position_btn)
        
        # 1/3仓按钮
        self.third_position_btn = QPushButton("1/3仓")
        self.third_position_btn.setFont(font)
        self.third_position_btn.clicked.connect(lambda: self.quick_select_volume(1/3))
        quick_select_layout.addWidget(self.third_position_btn)
        
        # 1/4仓按钮
        self.quarter_position_btn = QPushButton("1/4仓")
        self.quarter_position_btn.setFont(font)
        self.quarter_position_btn.clicked.connect(lambda: self.quick_select_volume(0.25))
        quick_select_layout.addWidget(self.quarter_position_btn)
        
        volume_layout.addLayout(quick_select_layout)
        layout.addLayout(volume_layout)
        
        # 提示信息
        info_label = QLabel("提示：夜市买入将以涨停板价格下单，确保次日开盘时优先成交")
        info_label.setFont(font)
        info_label.setStyleSheet("color: blue; font-style: italic;")
        layout.addWidget(info_label)
        
        # 确定取消按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        # 设置按钮字体
        for button in buttons.buttons():
            button.setFont(font)
        layout.addWidget(buttons)
        
        self.setLayout(layout)
        
        # 存储涨停板价格
        self.limit_up_price = None
        self.auto_get_timer = None  # 自动获取定时器
        
    def on_stock_code_changed(self):
        """股票代码变化时的处理"""
        stock_code = self.stock_code.text().strip()
        
        if len(stock_code) == 6:  # 输入完整的6位股票代码
            # 启用手动设置按钮
            self.set_price_btn.setEnabled(True)
            
            # 延迟1秒后自动获取价格，避免频繁请求
            if self.auto_get_timer:
                self.auto_get_timer.stop()
            
            from PyQt5.QtCore import QTimer
            self.auto_get_timer = QTimer()
            self.auto_get_timer.setSingleShot(True)
            self.auto_get_timer.timeout.connect(lambda: self.auto_get_limit_up_price(stock_code))
            self.auto_get_timer.start(1000)  # 1秒后自动获取
        else:
            # 重置状态
            self.limit_up_price = None
            self.limit_up_price_label.setText("输入股票代码后自动获取")
            self.limit_up_price_label.setStyleSheet("color: gray;")
            self.set_price_btn.setEnabled(False)
    
    def auto_get_limit_up_price(self, stock_code):
        """自动获取涨停板价格"""
        try:
            # 检查QMT适配器是否可用
            if not self.qmt_adapter:
                self.limit_up_price_label.setText("QMT连接不可用")
                self.limit_up_price_label.setStyleSheet("color: red;")
                return
            
            # 检查当前时间，判断是否在交易时间内
            current_time = datetime.now().time()
            trading_end_time = time(15, 0, 0)  # 15:00:00
            
            # 检查是否是交易日且在15:00前
            is_trading_day_today = is_tradeday(datetime.now().date())
            is_before_close = current_time < trading_end_time and is_trading_day_today
            
            # 显示正在获取状态
            if is_before_close:
                self.limit_up_price_label.setText("正在获取当前价格（交易时间内）...")
            else:
                self.limit_up_price_label.setText("正在自动获取价格...")
            self.limit_up_price_label.setStyleSheet("color: orange;")
            
            # 补齐股票代码后缀
            full_stock_code = self._get_full_stock_code(stock_code)
            
            # 使用现有的订阅系统添加股票到订阅列表
            if hasattr(self.qmt_adapter, 'subscribe_thread') and self.qmt_adapter.subscribe_thread:
                if hasattr(self.qmt_adapter.subscribe_thread, '_stock_codes'):
                    current_subscribed = self.qmt_adapter.subscribe_thread._stock_codes.copy()
                    if full_stock_code not in current_subscribed:
                        new_subscribed = current_subscribed | {full_stock_code}
                        self.qmt_adapter.subscribe_thread.update_subscribe_list(list(new_subscribed), force_update=True)
            
            # 等待一段时间获取数据
            time_module.sleep(1.5)  # 等待1.5秒获取数据
            
            # 检查缓存中是否有数据
            if (hasattr(self.qmt_adapter, 'task_manager') and 
                self.qmt_adapter.task_manager and 
                hasattr(self.qmt_adapter.task_manager, 'latest_prices')):
                
                # 查找缓存中的价格数据
                cached_latest_price = None
                
                # 尝试多种股票代码格式
                possible_codes = [stock_code, full_stock_code]
                if stock_code.startswith('00') or stock_code.startswith('30'):
                    possible_codes.append(f"{stock_code}.SZ")
                elif stock_code.startswith('60') or stock_code.startswith('68'):
                    possible_codes.append(f"{stock_code}.SH")
                
                for code in possible_codes:
                    if code in self.qmt_adapter.task_manager.latest_prices:
                        cached_latest_price = self.qmt_adapter.task_manager.latest_prices[code]
                        break
                
                if cached_latest_price is not None and cached_latest_price > 0:
                    # 如果在交易时间内，提醒用户
                    if is_before_close:
                        reply = QMessageBox.question(
                            self, 
                            "交易时间提醒", 
                            f"当前时间 {current_time.strftime('%H:%M:%S')} 在交易时间内（15:00前），\n"
                            f"获取到的价格 {cached_latest_price:.2f} 可能不是收盘价。\n\n"
                            f"是否继续使用当前价格计算涨停板价格？\n"
                            f"（建议在15:00后下单以确保使用收盘价）",
                            QMessageBox.Yes | QMessageBox.No,
                            QMessageBox.No
                        )
                        
                        if reply == QMessageBox.No:
                            self.limit_up_price_label.setText("用户取消，请在15:00后重新获取")
                            self.limit_up_price_label.setStyleSheet("color: orange;")
                            return
                    
                    # 计算涨停板价格（基于当前价格计算次日涨停板）
                    limit_up_price = self.calculate_limit_up_price(stock_code, cached_latest_price)
                    self.limit_up_price = limit_up_price
                    
                    # 更新显示
                    if is_before_close:
                        self.limit_up_price_label.setText(f"涨停板价格: {limit_up_price:.2f} (基于当前价)")
                    else:
                        self.limit_up_price_label.setText(f"涨停板价格: {limit_up_price:.2f}")
                    self.limit_up_price_label.setStyleSheet("color: red; font-weight: bold;")
                else:
                    self.limit_up_price_label.setText("无法自动获取价格，请手动获取")
                    self.limit_up_price_label.setStyleSheet("color: orange;")
            else:
                self.limit_up_price_label.setText("无法自动获取价格，请手动获取")
                self.limit_up_price_label.setStyleSheet("color: orange;")
            
        except Exception as e:
            self.limit_up_price_label.setText("自动获取失败，请手动获取")
            self.limit_up_price_label.setStyleSheet("color: red;")
    
    def _get_full_stock_code(self, stock_code):
        """获取完整的股票代码"""
        if len(stock_code) == 6:
            if stock_code.startswith(('0', '1', '3')):
                return f"{stock_code}.SZ"
            elif stock_code.startswith(('5', '6')):
                return f"{stock_code}.SH"
            elif stock_code.startswith(('4', '8', '920')):
                return f"{stock_code}.BJ"
        return stock_code
        
    def get_buy_params(self):
        """获取买入参数"""
        return {
            'stock_code': self.stock_code.text(),
            'buy_volume': self.buy_volume.value(),
            'buy_type': '限价',
            'buy_price': self.limit_up_price if self.limit_up_price else 0.0
        }
    
    def set_manual_price(self):
        """手动设置涨停板价格"""
        try:
            stock_code = self.stock_code.text().strip()
            if not stock_code:
                QMessageBox.warning(self, "警告", "请先输入股票代码")
                return
            
            # 弹出输入对话框
            price, ok = QInputDialog.getDouble(
                self, 
                "手动设置涨停板价格", 
                "请输入涨停板价格:", 
                0.0,  # 默认值
                0.0,  # 最小值
                999999.99,  # 最大值
                2  # 小数位数
            )
            
            if not ok:  # 用户取消
                return
            
            # 检查输入是否为有效数字
            if price <= 0:
                QMessageBox.warning(self, "警告", "请输入有效的正数价格")
                return
            
            # 更新显示
            self.limit_up_price = price
            self.limit_up_price_label.setText(f"涨停板价格: {price:.2f}")
            self.limit_up_price_label.setStyleSheet("color: red; font-weight: bold;")
            QMessageBox.information(self, "成功", f"已手动设置涨停板价格为: {price:.2f}")
            
        except Exception as e:
            self.limit_up_price_label.setText("手动设置失败")
            self.limit_up_price_label.setStyleSheet("color: red;")
            QMessageBox.critical(self, "错误", f"手动设置涨停板价格失败：{str(e)}")
    
    def calculate_limit_up_price(self, stock_code, close_price):
        """计算涨停板价格"""
        try:
            # 统一的涨跌停比例判定逻辑
            # 优先识别 ST 与 *ST（5%）
            try:
                from utils.stock_info_manager import get_stock_name
                stock_name = get_stock_name(stock_code) or ""
            except Exception:
                stock_name = ""

            if ('ST' in stock_name.upper()):
                limit_ratio = 0.05
            elif stock_code.startswith('300') or stock_code.startswith('688'):
                # 创业板和科创板 20%
                limit_ratio = 0.20
            elif stock_code.startswith('8') or stock_code.startswith('4') or stock_code.startswith('920'):
                # 北交所 30%
                limit_ratio = 0.30
            else:
                # 其他主板 10%
                limit_ratio = 0.10

            limit_up_price = close_price * (1 + limit_ratio)
            
            # 根据股票类型确定价格精度
            # 使用统一精度工具确定四舍五入位数
            try:
                from core.utils.security_type import SecurityTypeUtil
                precision = SecurityTypeUtil.get_price_precision(stock_code)
            except Exception:
                precision = 2
            result = round(limit_up_price, precision)
            
            return result
                
        except Exception as e:
            return close_price * 1.1  # 默认按10%计算 

    def quick_select_volume(self, ratio):
        """快速选择买入数量"""
        try:
            # 检查是否有涨停板价格
            if not self.limit_up_price or self.limit_up_price <= 0:
                QMessageBox.warning(self, "警告", "请先获取涨停板价格")
                return
            
            # 获取可用资金
            available_cash = self._get_available_cash()
            if available_cash is None: # 检查是否真的获取到了资金信息
                QMessageBox.warning(self, "警告", "无法获取可用资金信息")
                return
            
            # 计算可买入数量（考虑手续费等）
            # 假设手续费为0.0003（万三），印花税为0.001（千分之一）
            fee_rate = 0.0003 + 0.001  # 总费率约0.13%
            
            # 计算实际可买入数量
            max_amount = available_cash * ratio
            max_shares = int(max_amount / (self.limit_up_price * (1 + fee_rate)))
            
            # 确保数量是100的倍数
            max_shares = (max_shares // 100) * 100
            
            if max_shares >= 100:
                self.buy_volume.setValue(max_shares)
                QMessageBox.information(self, "成功", f"已设置{ratio*100:.0f}%仓位：{max_shares}股")
            else:
                QMessageBox.warning(self, "警告", f"可用资金不足，无法买入{ratio*100:.0f}%仓位")
                
        except Exception as e:
            QMessageBox.critical(self, "错误", f"计算买入数量失败：{str(e)}")
    
    def _get_available_cash(self):
        """获取可用资金"""
        try:
            # 检查QMT适配器是否可用
            if not self.qmt_adapter:
                return None
            
            # 从QMT适配器获取资产信息
            asset_info = self.qmt_adapter.get_asset()
            if asset_info and 'cash' in asset_info:
                available_cash = asset_info['cash']
                return available_cash
            
            # 尝试从任务管理器获取资金信息
            if (hasattr(self.qmt_adapter, 'task_manager') and 
                self.qmt_adapter.task_manager and 
                hasattr(self.qmt_adapter.task_manager, 'get_available_cash')):
                available_cash = self.qmt_adapter.task_manager.get_available_cash()
                return available_cash
            
            # 如果都无法获取，返回None
            return None
            
        except Exception as e:
            return None

class VersionDialog(QDialog):
    """版本信息对话框"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("版本信息")
        self.setFixedSize(400, 300)
        # 去掉右上角的问号（帮助按钮）
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout()
        
        # 创建统一的字体
        font = self.font()
        font.setPointSize(12)  # 设置字体大小为12pt
        
        # 标题
        title_label = QLabel("蚂蚁量化交易策略系统")
        title_label.setFont(font)
        title_label.setStyleSheet("font-size: 16pt; font-weight: bold; color: #2E86AB; margin: 20px;")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)
        
        # 版本号
        version_label = QLabel("版本号: V3.0")
        version_label.setFont(font)
        version_label.setStyleSheet("font-size: 14pt; font-weight: bold; color: #A23B72; margin: 10px;")
        version_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(version_label)
        
        # 分隔线
        line = QLabel()
        line.setStyleSheet("border: 1px solid #CCCCCC; margin: 10px;")
        line.setFixedHeight(1)
        layout.addWidget(line)
        
        # 版本信息
        info_text = """
        <div style="margin: 20px; line-height: 1.6;">
        <p><strong>主要功能：</strong></p>
        <ul>
            <li>万能策略 - 支持上涨/下跌操作配置</li>
            <li>夜市交易策略 - 支持夜市买入/卖出</li>
            <li>实时行情监控</li>
            <li>智能交易执行</li>
            <li>持仓管理</li>
            <li>交易记录</li>
        </ul>
        
        <p><strong>更新内容：</strong></p>
        <ul>
            <li>优化策略参数设置</li>
            <li>增强夜市交易功能</li>
            <li>改进用户界面体验</li>
            <li>提升系统稳定性</li>
        </ul>
        </div>
        """
        
        info_label = QLabel(info_text)
        info_label.setFont(font)
        info_label.setStyleSheet("color: #333333;")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        # 确定按钮
        ok_button = QPushButton("确定")
        ok_button.setFont(font)
        ok_button.setStyleSheet("""
            QPushButton {
                background-color: #2E86AB;
                color: white;
                border: none;
                padding: 8px 20px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1E6A8B;
            }
            QPushButton:pressed {
                background-color: #0D4A6B;
            }
        """)
        ok_button.clicked.connect(self.accept)
        
        # 按钮布局
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(ok_button)
        button_layout.addStretch()
        layout.addLayout(button_layout)
        
        self.setLayout(layout)

class ReadBeforeUseDialog(QDialog):
    """使用前必读对话框"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("使用前必读")
        self.setFixedSize(500, 350)
        # 去掉右上角的问号（帮助按钮）
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout()
        
        # 创建统一的字体
        font = self.font()
        font.setPointSize(12)  # 设置字体大小为12pt
        
        '''# 标题
        title_label = QLabel("使用前必读")
        title_label.setFont(font)
        title_label.setStyleSheet("font-size: 16pt; font-weight: bold; color: #D32F2F; margin: 20px;")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)'''
        
        # 警告图标和文本
        warning_text = """
        <div style="margin: 20px; line-height: 1.8; text-align: center;">
        <p style="font-size: 14pt; font-weight: bold; color: #D32F2F;">
        ⚠️ 重要声明 ⚠️
        </p>
        <p style="font-size: 12pt; color: #333333;">
        本程序代码开源，仅供参考。
        </p>
        <p style="font-size: 12pt; color: #333333;">
        继续使用本程序，代表您同意使用本程序的后果完全由您本人自行承担。
        </p>
        </div>
        """
        
        warning_label = QLabel(warning_text)
        warning_label.setFont(font)
        warning_label.setStyleSheet("color: #333333;")
        warning_label.setWordWrap(True)
        layout.addWidget(warning_label)
        
        # 我同意按钮
        agree_button = QPushButton("我同意")
        agree_button.setFont(font)
        agree_button.setStyleSheet("""
            QPushButton {
                background-color: #D32F2F;
                color: white;
                border: none;
                padding: 10px 25px;
                border-radius: 5px;
                font-weight: bold;
                font-size: 12pt;
            }
            QPushButton:hover {
                background-color: #B71C1C;
            }
            QPushButton:pressed {
                background-color: #8E0000;
            }
        """)
        agree_button.clicked.connect(self.accept)
        
        # 我不同意按钮
        disagree_button = QPushButton("我不同意")
        disagree_button.setFont(font)
        disagree_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                padding: 10px 25px;
                border-radius: 5px;
                font-weight: bold;
                font-size: 12pt;
            }
            QPushButton:hover {
                background-color: #45A049;
            }
            QPushButton:pressed {
                background-color: #3D8B40;
            }
        """)
        disagree_button.clicked.connect(self.disagree_and_exit)
        
        # 按钮布局
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(agree_button)
        button_layout.addWidget(disagree_button)
        button_layout.addStretch()
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
        
    def disagree_and_exit(self):
        """我不同意，关闭对话框"""
        # 只是关闭对话框，不退出程序
        self.reject()


class DailyAnalysisDialog(QDialog):
    """当日分析对话框"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("当日分析")
        self.resize(1200, 900)  # 设置更大的初始大小，但允许拉伸
        self.setMinimumSize(800, 600)  # 设置更大的最小大小
        # 去掉右上角的问号（帮助按钮）
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.analysis_thread = None
        # 初始化默认配置参数（在开始分析时会重新读取）
        self.config_params = self.get_default_config()
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout()
        
        # 创建统一的字体
        font = self.font()
        font.setPointSize(10)
        
        # 股票代码和日期输入区域
        input_layout = QHBoxLayout()
        
        # 股票代码
        stock_label = QLabel("股票代码:")
        stock_label.setFont(font)
        input_layout.addWidget(stock_label)
        
        self.stock_input = QLineEdit()
        self.stock_input.setFont(font)
        self.stock_input.setPlaceholderText("请输入股票代码，如：000001")
        self.stock_input.setMaximumWidth(200)
        input_layout.addWidget(self.stock_input)
        
        # 日期选择
        date_label = QLabel("分析日期:")
        date_label.setFont(font)
        input_layout.addWidget(date_label)
        
        self.date_edit = QDateEdit()
        self.date_edit.setFont(font)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(QDate.currentDate())  # 设置初始值为当天
        self.date_edit.setCalendarPopup(True)  # 允许弹出日历选择
        self.date_edit.setMaximumWidth(150)
        input_layout.addWidget(self.date_edit)
        
        # 分析按钮
        self.analyze_button = QPushButton("开始分析")
        self.analyze_button.setFont(font)
        self.analyze_button.clicked.connect(self.start_analysis)
        self.analyze_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45A049;
            }
            QPushButton:pressed {
                background-color: #3D8B40;
            }
            QPushButton:disabled {
                background-color: #CCCCCC;
                color: #666666;
            }
        """)
        input_layout.addWidget(self.analyze_button)
        
        input_layout.addStretch()
        layout.addLayout(input_layout)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        # 分析结果显示区域
        result_label = QLabel("分析结果:")
        result_label.setFont(font)
        result_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        layout.addWidget(result_label)
        
        self.result_text = QTextEdit()
        self.result_text.setFont(font)
        self.result_text.setReadOnly(True)
        self.result_text.setStyleSheet("""
            QTextEdit {
                border: 1px solid #CCCCCC;
                background-color: white;
                font-family: "Microsoft YaHei";
                font-size: 10pt;
            }
        """)
        self.result_text.setMinimumHeight(120)  # 设置文本框的最小高度
        layout.addWidget(self.result_text)
        
        # 涨停板详情表格
        detail_label = QLabel("涨停板详情:")
        detail_label.setFont(font)
        detail_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        layout.addWidget(detail_label)
        
        self.detail_table = QTableWidget()
        self.detail_table.setFont(font)
        self.detail_table.setColumnCount(5)
        self.detail_table.setHorizontalHeaderLabels(["时间", "状态", "价格", "成交量", "买一量"])
        self.detail_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.detail_table.setMinimumHeight(200)  # 设置更大的最小高度
        layout.addWidget(self.detail_table)
        
        # 买一量异常变化表格
        abnormal_label = QLabel("买一量异常变化:")
        abnormal_label.setFont(font)
        abnormal_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        layout.addWidget(abnormal_label)
        
        self.abnormal_table = QTableWidget()
        self.abnormal_table.setFont(font)
        self.abnormal_table.setColumnCount(6)
        self.abnormal_table.setHorizontalHeaderLabels(["时间", "变化类型", "原因", "变化前", "变化后", "变化量"])
        self.abnormal_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.abnormal_table.setMinimumHeight(150)  # 设置更大的最小高度
        layout.addWidget(self.abnormal_table)
        
        # 主力行为分析表格
        main_force_label = QLabel("主力行为分析:")
        main_force_label.setFont(font)
        main_force_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        layout.addWidget(main_force_label)
        
        self.main_force_table = QTableWidget()
        self.main_force_table.setFont(font)
        self.main_force_table.setColumnCount(6)
        self.main_force_table.setHorizontalHeaderLabels(["时间", "行为类型", "强度", "成交量", "价格变化", "特征描述"])
        
        # 设置滚动条策略，确保垂直滚动条在需要时可见
        self.main_force_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.main_force_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # 设置列宽：前5列使用固定宽度，最后一列拉伸填充剩余空间
        header = self.main_force_table.horizontalHeader()
        header.setStretchLastSection(False)  # 关闭默认的最后一列拉伸
        
        # 前5列使用固定宽度
        column_widths = [120, 100, 80, 100, 100]  # 时间、行为类型、强度、成交量、价格变化的宽度
        for i in range(5):
            header.setSectionResizeMode(i, header.Fixed)
            self.main_force_table.setColumnWidth(i, column_widths[i])
        
        # 最后一列（特征描述）设置为拉伸模式，填充剩余空间
        header.setSectionResizeMode(5, header.Stretch)
        
        self.main_force_table.setMinimumHeight(180)  # 设置更大的最小高度
        layout.addWidget(self.main_force_table)
        
        # 导出按钮
        self.export_button = QPushButton("导出Tick数据")
        self.export_button.setFont(font)
        self.export_button.clicked.connect(self.export_tick_data)
        self.export_button.setEnabled(False)  # 初始状态禁用
        self.export_button.setStyleSheet("""
            QPushButton {
                background-color: #FF9800;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #F57C00;
            }
            QPushButton:pressed {
                background-color: #E65100;
            }
            QPushButton:disabled {
                background-color: #CCCCCC;
                color: #666666;
            }
        """)
        
        # 关闭按钮
        close_button = QPushButton("关闭")
        close_button.setFont(font)
        close_button.clicked.connect(self.accept)
        close_button.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:pressed {
                background-color: #0D47A1;
            }
        """)
        
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(self.export_button)
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
        
    def get_default_config(self):
        """获取默认配置参数"""
        # 默认配置参数
        default_config = {
            # 买一量异常变化检测参数
            'limit_up_bid_vol_threshold': 100000,
            'normal_bid_vol_threshold': 50000,
            'trade_volume_ratio': 0.8,
            
            # 主力行为分析参数 - 吸筹
            'accumulation_volume_threshold': 10000,
            'accumulation_price_change': 0,
            'accumulation_bid_vol_change': 50000,
            'accumulation_pressure_ratio': 1.5,
            'accumulation_strong_threshold': 50000,
            'accumulation_medium_threshold': 20000,
            
            # 主力行为分析参数 - 出货
            'distribution_volume_threshold': 10000,
            'distribution_price_change': 0,
            'distribution_ask_vol_change': 50000,
            'distribution_pressure_ratio': 0.7,
            'distribution_strong_threshold': 50000,
            'distribution_medium_threshold': 20000,
            
            # 主力行为分析参数 - 洗盘
            'wash_volume_threshold': 15000,
            'wash_price_change_threshold': 0.01,
            'wash_vol_change_diff': 10000,
            'wash_strong_threshold': 30000,
            'wash_medium_threshold': 20000,
            
            # 主力行为分析参数 - 护盘
            'support_bid_vol_change': 100000,
            'support_volume_threshold': 5000,
            
            # 主力行为分析参数 - 砸盘
            'smash_volume_threshold': 20000,
            'smash_price_change_threshold': -0.02,
            'smash_ask_vol_change': 100000
        }
        
        return default_config
        
    def load_config(self):
        """加载配置文件"""
        import configparser
        import os
        
        config = configparser.ConfigParser()
        config_file = os.path.join('data', 'config.ini')
        
        # 获取默认配置
        default_config = self.get_default_config()
        
        try:
            if os.path.exists(config_file):
                config.read(config_file, encoding='utf-8')
                if 'Today Analyse' in config:
                    section = config['Today Analyse']
                    for key in default_config:
                        if key in section:
                            try:
                                if isinstance(default_config[key], int):
                                    default_config[key] = section.getint(key)
                                elif isinstance(default_config[key], float):
                                    default_config[key] = section.getfloat(key)
                                else:
                                    default_config[key] = section.get(key)
                            except (ValueError, TypeError):
                                # 如果转换失败，使用默认值
                                pass
        except Exception as e:
            print(f"加载配置文件失败: {e}")
        
        return default_config
        
    def start_analysis(self):
        """开始分析"""
        stock_code = self.stock_input.text().strip()
        if not stock_code:
            QMessageBox.warning(self, "警告", "请输入股票代码")
            return
            
        # 验证股票代码格式
        if not (stock_code.isdigit() and len(stock_code) == 6):
            QMessageBox.warning(self, "警告", "请输入正确的6位股票代码")
            return
            
        # 在开始分析时重新读取配置参数
        self.config_params = self.load_config()
            
        # 禁用分析按钮和导出按钮
        self.analyze_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # 显示忙碌状态
        
        # 清空之前的结果
        self.result_text.clear()
        self.detail_table.setRowCount(0)
        self.abnormal_table.setRowCount(0)
        self.main_force_table.setRowCount(0)
        
        # 获取选择的日期
        selected_date = self.date_edit.date().toPyDate()
        
        # 创建分析线程，使用最新读取的配置参数
        self.analysis_thread = AnalysisThread(stock_code, selected_date, self.config_params)
        self.analysis_thread.analysis_complete.connect(self.on_analysis_complete)
        self.analysis_thread.analysis_error.connect(self.on_analysis_error)
        self.analysis_thread.progress_update.connect(self.progress_bar.setValue)
        self.analysis_thread.start()
        
    def on_analysis_complete(self, result):
        """分析完成回调"""
        self.progress_bar.setVisible(False)
        self.analyze_button.setEnabled(True)
        
        # 保存分析结果用于导出
        self.current_analysis_result = result
        
        # 启用导出按钮
        self.export_button.setEnabled(True)
        
        # 显示分析结果
        self.display_results(result)
        
    def on_analysis_error(self, error_msg):
        """分析错误回调"""
        self.progress_bar.setVisible(False)
        self.analyze_button.setEnabled(True)
        self.export_button.setEnabled(False)
        QMessageBox.critical(self, "错误", f"分析失败: {error_msg}")
        
    def display_results(self, result):
        """显示分析结果"""
        # 显示基本统计信息
        self.result_text.append(f"=== {result['stock_code']} {result['date']}分析结果 ===\n")
        self.result_text.append(f"数据时间范围: 9:15-14:57 (包含集合竞价阶段，排除尾盘集合竞价)\n")
        self.result_text.append(f"涨停板占比: {result['limit_up_percentage']:.2f}%\n")
        self.result_text.append(f"涨停板持续时间: {result['limit_up_duration']}\n")
        self.result_text.append(f"开板次数: {result['open_count']}\n")
        self.result_text.append(f"封板次数: {result['seal_count']}\n")
        self.result_text.append(f"买一量异常变化次数: {result['abnormal_changes_count']}\n")
        self.result_text.append(f"主力行为分析次数: {result['main_force_actions_count']}\n")
        
        # 显示涨停板详情
        if result['limit_up_details']:
            self.detail_table.setRowCount(len(result['limit_up_details']))
            for i, detail in enumerate(result['limit_up_details']):
                self.detail_table.setItem(i, 0, QTableWidgetItem(detail['time']))
                self.detail_table.setItem(i, 1, QTableWidgetItem(detail['status']))
                self.detail_table.setItem(i, 2, QTableWidgetItem(f"{detail['price']:.2f}"))
                self.detail_table.setItem(i, 3, QTableWidgetItem(str(detail['volume'])))
                self.detail_table.setItem(i, 4, QTableWidgetItem(str(detail.get('bid_vol', 0))))
        else:
            self.detail_table.setRowCount(1)
            self.detail_table.setItem(0, 0, QTableWidgetItem("无涨停板数据"))
            self.detail_table.setItem(0, 1, QTableWidgetItem(""))
            self.detail_table.setItem(0, 2, QTableWidgetItem(""))
            self.detail_table.setItem(0, 3, QTableWidgetItem(""))
            self.detail_table.setItem(0, 4, QTableWidgetItem(""))
        
        # 显示买一量异常变化
        if result['abnormal_changes']:
            self.abnormal_table.setRowCount(len(result['abnormal_changes']))
            for i, change in enumerate(result['abnormal_changes']):
                self.abnormal_table.setItem(i, 0, QTableWidgetItem(change['time']))
                self.abnormal_table.setItem(i, 1, QTableWidgetItem(change['type']))
                self.abnormal_table.setItem(i, 2, QTableWidgetItem(change.get('reason', '')))
                self.abnormal_table.setItem(i, 3, QTableWidgetItem(str(change['before'])))
                self.abnormal_table.setItem(i, 4, QTableWidgetItem(str(change['after'])))
                self.abnormal_table.setItem(i, 5, QTableWidgetItem(str(change['change'])))
        else:
            self.abnormal_table.setRowCount(1)
            self.abnormal_table.setItem(0, 0, QTableWidgetItem("无异常变化"))
            self.abnormal_table.setItem(0, 1, QTableWidgetItem(""))
            self.abnormal_table.setItem(0, 2, QTableWidgetItem(""))
            self.abnormal_table.setItem(0, 3, QTableWidgetItem(""))
            self.abnormal_table.setItem(0, 4, QTableWidgetItem(""))
            self.abnormal_table.setItem(0, 5, QTableWidgetItem(""))
        
        # 显示主力行为分析
        if result['main_force_actions']:
            self.main_force_table.setRowCount(len(result['main_force_actions']))
            for i, action in enumerate(result['main_force_actions']):
                self.main_force_table.setItem(i, 0, QTableWidgetItem(action['time']))
                self.main_force_table.setItem(i, 1, QTableWidgetItem(action['type']))
                self.main_force_table.setItem(i, 2, QTableWidgetItem(action['intensity']))
                self.main_force_table.setItem(i, 3, QTableWidgetItem(str(action['volume'])))
                self.main_force_table.setItem(i, 4, QTableWidgetItem(f"{action['price_change']:.3f}"))
                self.main_force_table.setItem(i, 5, QTableWidgetItem(action['description']))
        else:
            self.main_force_table.setRowCount(1)
            self.main_force_table.setItem(0, 0, QTableWidgetItem("无主力行为"))
            self.main_force_table.setItem(0, 1, QTableWidgetItem(""))
            self.main_force_table.setItem(0, 2, QTableWidgetItem(""))
            self.main_force_table.setItem(0, 3, QTableWidgetItem(""))
            self.main_force_table.setItem(0, 4, QTableWidgetItem(""))
            self.main_force_table.setItem(0, 5, QTableWidgetItem(""))

    def export_tick_data(self):
        """导出Tick数据"""
        try:
            if not hasattr(self, 'current_analysis_result') or not self.current_analysis_result:
                QMessageBox.warning(self, "警告", "请先进行股票分析")
                return
            
            # 获取股票代码和日期
            stock_code = self.current_analysis_result['stock_code']
            date_str = self.current_analysis_result['date']
            
            # 将日期字符串转换为datetime.date对象
            from datetime import datetime
            date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
            
            # 选择保存文件路径
            default_filename = f"{stock_code}_{date_str}_tick_data.csv"
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "保存Tick数据",
                default_filename,
                "CSV文件 (*.csv);;Excel文件 (*.xlsx)"
            )
            
            if not file_path:
                return  # 用户取消了保存
            
            # 从回测引擎获取原始数据
            from core.backtest_engine import BacktestEngine
            from utils.logger import Logger
            
            engine = BacktestEngine(stock_code=stock_code)
            engine.set_logger(Logger())
            
            # 加载数据
            success = engine.load_data(date_obj, date_obj)
            if not success or engine.data is None or engine.data.empty:
                QMessageBox.warning(self, "警告", "无法获取股票数据")
                return
            
            # 准备导出数据
            export_data = []
            for idx, row in engine.data.iterrows():
                # 格式化时间
                time_str = str(idx)
                if len(time_str) >= 14:
                    formatted_time = f"{time_str[:4]}-{time_str[4:6]}-{time_str[6:8]} {time_str[8:10]}:{time_str[10:12]}:{time_str[12:14]}"
                else:
                    formatted_time = time_str
                
                # 获取买卖盘口数据
                bid_prices = row.get('bidPrice', [])
                bid_vols = row.get('bidVol', [])
                ask_prices = row.get('askPrice', [])
                ask_vols = row.get('askVol', [])
                
                # 计算涨停板状态
                is_limit_up = (ask_prices[0] == 0) if isinstance(ask_prices, list) and len(ask_prices) > 0 else False
                
                export_data.append({
                    '时间': formatted_time,
                    '最新价': row.get('lastPrice', 0),
                    '成交量': row.get('volume', 0),
                    '成交额': row.get('amount', 0),
                    '买一价': bid_prices[0] if isinstance(bid_prices, list) and len(bid_prices) > 0 else 0,
                    '买一量': bid_vols[0] if isinstance(bid_vols, list) and len(bid_vols) > 0 else 0,
                    '买二价': bid_prices[1] if isinstance(bid_prices, list) and len(bid_prices) > 1 else 0,
                    '买二量': bid_vols[1] if isinstance(bid_vols, list) and len(bid_vols) > 1 else 0,
                    '买三价': bid_prices[2] if isinstance(bid_prices, list) and len(bid_prices) > 2 else 0,
                    '买三量': bid_vols[2] if isinstance(bid_vols, list) and len(bid_vols) > 2 else 0,
                    '买四价': bid_prices[3] if isinstance(bid_prices, list) and len(bid_prices) > 3 else 0,
                    '买四量': bid_vols[3] if isinstance(bid_vols, list) and len(bid_vols) > 3 else 0,
                    '买五价': bid_prices[4] if isinstance(bid_prices, list) and len(bid_prices) > 4 else 0,
                    '买五量': bid_vols[4] if isinstance(bid_vols, list) and len(bid_vols) > 4 else 0,
                    '卖一价': ask_prices[0] if isinstance(ask_prices, list) and len(ask_prices) > 0 else 0,
                    '卖一量': ask_vols[0] if isinstance(ask_vols, list) and len(ask_vols) > 0 else 0,
                    '卖二价': ask_prices[1] if isinstance(ask_prices, list) and len(ask_prices) > 1 else 0,
                    '卖二量': ask_vols[1] if isinstance(ask_vols, list) and len(ask_vols) > 1 else 0,
                    '卖三价': ask_prices[2] if isinstance(ask_prices, list) and len(ask_prices) > 2 else 0,
                    '卖三量': ask_vols[2] if isinstance(ask_vols, list) and len(ask_vols) > 2 else 0,
                    '卖四价': ask_prices[3] if isinstance(ask_prices, list) and len(ask_prices) > 3 else 0,
                    '卖四量': ask_vols[3] if isinstance(ask_vols, list) and len(ask_vols) > 3 else 0,
                    '卖五价': ask_prices[4] if isinstance(ask_prices, list) and len(ask_prices) > 4 else 0,
                    '卖五量': ask_vols[4] if isinstance(ask_prices, list) and len(ask_vols) > 4 else 0,
                    '涨停板': '是' if is_limit_up else '否'
                })
            
            # 创建DataFrame并导出
            import pandas as pd
            df = pd.DataFrame(export_data)
            
            if file_path.endswith('.xlsx'):
                # 尝试导出为Excel文件
                try:
                    df.to_excel(file_path, index=False, engine='openpyxl')
                except ImportError:
                    # 如果没有安装openpyxl，回退到CSV格式
                    csv_path = file_path.replace('.xlsx', '.csv')
                    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
                    QMessageBox.information(self, "导出成功", 
                                          f"由于未安装openpyxl库，已自动导出为CSV格式:\n{csv_path}")
                    return
                except Exception as e:
                    # 其他Excel导出错误，回退到CSV格式
                    csv_path = file_path.replace('.xlsx', '.csv')
                    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
                    QMessageBox.information(self, "导出成功", 
                                          f"Excel导出失败，已自动导出为CSV格式:\n{csv_path}")
                    return
            else:
                # 导出为CSV文件
                df.to_csv(file_path, index=False, encoding='utf-8-sig')
            
            QMessageBox.information(self, "导出成功", f"Tick数据已成功导出到:\n{file_path}")
            
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出Tick数据时发生错误:\n{str(e)}")


class AnalysisThread(QThread):
    """分析线程"""
    analysis_complete = pyqtSignal(dict)
    analysis_error = pyqtSignal(str)
    progress_update = pyqtSignal(int)
    
    def __init__(self, stock_code, analysis_date, config_params=None):
        super().__init__()
        self.stock_code = stock_code
        self.analysis_date = analysis_date
        self.config_params = config_params or {}
        
    def run(self):
        """运行分析"""
        try:
            from core.backtest_engine import BacktestEngine
            from utils.logger import Logger
            
            self.progress_update.emit(10)
            
            # 创建回测引擎
            engine = BacktestEngine(stock_code=self.stock_code)
            engine.set_logger(Logger())
            
            self.progress_update.emit(20)
            
            # 使用选择的日期
            selected_date = self.analysis_date
            
            # 加载指定日期的数据
            success = engine.load_data(selected_date, selected_date)
            if not success or engine.data is None or engine.data.empty:
                self.analysis_error.emit(f"无法获取{selected_date.strftime('%Y-%m-%d')}的tick数据")
                return
                
            self.progress_update.emit(50)
            
            # 分析涨停板情况
            result = self.analyze_limit_up_data(engine.data)
            
            self.progress_update.emit(80)
            
            # 添加基本信息
            result['stock_code'] = self.stock_code
            result['date'] = selected_date.strftime('%Y-%m-%d')
            result['total_ticks'] = len(engine.data)
            
            self.progress_update.emit(100)
            
            # 发送结果
            self.analysis_complete.emit(result)
            
        except Exception as e:
            import traceback
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            self.analysis_error.emit(error_msg)
            
    def analyze_limit_up_data(self, data):
        """分析涨停板数据"""
        # 涨停板判断条件：卖一价和卖一量必须为0
        limit_up_ask_price = 0
        limit_up_ask_vol = 0
        
        # 计算涨停板状态
        # 对于集合竞价阶段（9:15-9:25）和尾盘集合竞价阶段（14:57-15:00），使用不同的判断逻辑
        def is_limit_up_condition(row):
            time_str = str(row.name)  # 获取时间索引
            if len(time_str) >= 10:  # 确保时间格式正确
                hour = int(time_str[8:10])
                minute = int(time_str[10:12])
                
                # 集合竞价阶段（9:15-9:25）
                if hour == 9 and 15 <= minute <= 25:
                    # 集合竞价阶段，涨停板判断可能不同
                    # 检查是否有卖盘（askPrice和askVol）
                    ask_price = row.get('askPrice', [0])[0] if isinstance(row.get('askPrice', []), list) and len(row.get('askPrice', [])) > 0 else 0
                    ask_vol = row.get('askVol', [0])[0] if isinstance(row.get('askVol', []), list) and len(row.get('askVol', [])) > 0 else 0
                    return ask_price == limit_up_ask_price and ask_vol == limit_up_ask_vol
                # 尾盘集合竞价阶段（14:57-15:00）
                elif hour == 14 and minute >= 57:
                    # 尾盘集合竞价阶段，不进行涨停板判断
                    return False
                elif hour == 15 and minute == 0:
                    # 15:00，不进行涨停板判断
                    return False
                else:
                    # 连续竞价阶段，使用原有逻辑
                    ask_price = row.get('askPrice', [0])[0] if isinstance(row.get('askPrice', []), list) and len(row.get('askPrice', [])) > 0 else 0
                    ask_vol = row.get('askVol', [0])[0] if isinstance(row.get('askVol', []), list) and len(row.get('askVol', [])) > 0 else 0
                    return ask_price == limit_up_ask_price and ask_vol == limit_up_ask_vol
            else:
                # 如果时间格式不正确，使用原有逻辑
                ask_price = row.get('askPrice', [0])[0] if isinstance(row.get('askPrice', []), list) and len(row.get('askPrice', [])) > 0 else 0
                ask_vol = row.get('askVol', [0])[0] if isinstance(row.get('askVol', []), list) and len(row.get('askVol', [])) > 0 else 0
                return ask_price == limit_up_ask_price and ask_vol == limit_up_ask_vol
        
        # 应用涨停板判断条件
        data['is_limit_up'] = data.apply(is_limit_up_condition, axis=1)
        
        # 统计涨停板时间点，排除尾盘集合竞价阶段
        limit_up_count = 0
        total_count = 0
        for idx, is_limit_up in data['is_limit_up'].items():
            # 检查是否为尾盘集合竞价阶段
            time_str = str(idx)
            if len(time_str) >= 10:
                hour = int(time_str[8:10])
                minute = int(time_str[10:12])
                # 排除尾盘集合竞价阶段（14:57-15:00）和15:00之后的数据
                if (hour == 14 and minute >= 57) or hour >= 15:
                    continue
            
            total_count += 1
            if is_limit_up:
                limit_up_count += 1
        
        limit_up_percentage = (limit_up_count / total_count * 100) if total_count > 0 else 0
        
        # 计算涨停板持续时间，排除尾盘集合竞价阶段
        limit_up_duration = "0分钟"
        if limit_up_count > 0:
            # 计算连续涨停板的时间段
            limit_up_periods = []
            current_period_start = None
            
            for i, (idx, is_limit_up) in enumerate(data['is_limit_up'].items()):
                # 检查是否为尾盘集合竞价阶段
                time_str = str(idx)
                if len(time_str) >= 10:
                    hour = int(time_str[8:10])
                    minute = int(time_str[10:12])
                    # 排除尾盘集合竞价阶段（14:57-15:00）和15:00之后的数据
                    if (hour == 14 and minute >= 57) or hour >= 15:
                        continue
                
                if is_limit_up and current_period_start is None:
                    current_period_start = idx
                elif not is_limit_up and current_period_start is not None:
                    limit_up_periods.append((current_period_start, data.index[i-1]))
                    current_period_start = None
                    
            # 处理最后一个涨停板期间
            if current_period_start is not None:
                limit_up_periods.append((current_period_start, data.index[-1]))
                
            # 计算总持续时间
            total_duration_minutes = 0
            for start, end in limit_up_periods:
                start_time = pd.to_datetime(start)
                end_time = pd.to_datetime(end)
                duration = (end_time - start_time).total_seconds() / 60
                total_duration_minutes += duration
                
            limit_up_duration = f"{int(total_duration_minutes)}分钟"
        
        # 统计开板和封板次数
        open_count = 0
        seal_count = 0
        limit_up_details = []
        
        # 检测状态变化，排除尾盘集合竞价阶段
        prev_is_limit_up = False
        for i, (idx, row) in enumerate(data.iterrows()):
            # 检查是否为尾盘集合竞价阶段
            time_str = str(idx)
            if len(time_str) >= 10:
                hour = int(time_str[8:10])
                minute = int(time_str[10:12])
                # 排除尾盘集合竞价阶段（14:57-15:00）和15:00之后的数据
                if (hour == 14 and minute >= 57) or hour >= 15:
                    continue
            
            is_limit_up = row['is_limit_up']
            
            if is_limit_up and not prev_is_limit_up:
                # 封板
                seal_count += 1
                limit_up_details.append({
                    'time': pd.to_datetime(idx).strftime('%H:%M:%S'),
                    'status': '封板',
                    'price': row['lastPrice'],
                    'volume': row.get('volume', 0),
                    'bid_vol': row.get('bidVol', [0])[0] if isinstance(row.get('bidVol', []), list) and len(row.get('bidVol', [])) > 0 else 0
                })
            elif not is_limit_up and prev_is_limit_up:
                # 开板
                open_count += 1
                limit_up_details.append({
                    'time': pd.to_datetime(idx).strftime('%H:%M:%S'),
                    'status': '开板',
                    'price': row['lastPrice'],
                    'volume': row.get('volume', 0),
                    'bid_vol': row.get('bidVol', [0])[0] if isinstance(row.get('bidVol', []), list) and len(row.get('bidVol', [])) > 0 else 0
                })
                
            prev_is_limit_up = is_limit_up
        
        # 分析封单量异常变化
        abnormal_changes = self.analyze_bid_volume_changes(data)
        
        # 分析主力行为
        main_force_actions = self.analyze_main_force_behavior(data)
        
        return {
            'limit_up_count': limit_up_count,
            'limit_up_percentage': limit_up_percentage,
            'limit_up_duration': limit_up_duration,
            'open_count': open_count,
            'seal_count': seal_count,
            'limit_up_details': limit_up_details,
            'abnormal_changes': abnormal_changes,
            'abnormal_changes_count': len(abnormal_changes),
            'main_force_actions': main_force_actions,
            'main_force_actions_count': len(main_force_actions)
        }
    
    def analyze_bid_volume_changes(self, data):
        """分析买一量异常变化"""
        abnormal_changes = []
        
        # 获取买一量数据和相关数据，排除尾盘集合竞价阶段
        bid_volumes = []
        for idx, row in data.iterrows():
            # 检查是否为尾盘集合竞价阶段
            time_str = str(idx)
            if len(time_str) >= 10:
                hour = int(time_str[8:10])
                minute = int(time_str[10:12])
                # 排除尾盘集合竞价阶段（14:57-15:00）和15:00之后的数据
                if (hour == 14 and minute >= 57) or hour >= 15:
                    continue
            
            bid_vol = row.get('bidVol', [0])[0] if isinstance(row.get('bidVol', []), list) and len(row.get('bidVol', [])) > 0 else 0
            volume = row.get('volume', 0)  # 成交量
            last_price = row.get('lastPrice', 0)  # 最新价
            bid_volumes.append({
                'time': pd.to_datetime(idx),
                'bid_vol': bid_vol,
                'volume': volume,
                'last_price': last_price,
                'is_limit_up': row['is_limit_up']
            })
        
        if len(bid_volumes) < 2:
            return abnormal_changes
        
        # 检测异常变化
        for i in range(1, len(bid_volumes)):
            prev_data = bid_volumes[i-1]
            curr_data = bid_volumes[i]
            
            prev_vol = prev_data['bid_vol']
            curr_vol = curr_data['bid_vol']
            curr_time = curr_data['time']
            curr_is_limit_up = curr_data['is_limit_up']
            
            # 计算变化量
            change = curr_vol - prev_vol
            
            # 计算成交量变化（用于判断是否成交）
            volume_change = curr_data['volume'] - prev_data['volume']
            
            # 从配置参数获取异常变化阈值
            limit_up_threshold = self.config_params.get('limit_up_bid_vol_threshold', 100000)
            normal_threshold = self.config_params.get('normal_bid_vol_threshold', 50000)
            
            # 判断是否为异常变化
            is_abnormal = False
            change_type = ""
            reason = ""
            
            if curr_is_limit_up:
                # 涨停板时的异常变化
                if abs(change) >= limit_up_threshold:
                    is_abnormal = True
                    if change > 0:
                        change_type = "涨停板买一量增加"
                        reason = "新增买单"
                    else:
                        change_type = "涨停板买一量减少"
                        # 分析减少原因
                        if volume_change > 0:
                            # 有成交量，需要判断是成交还是撤单
                            # 如果买一量减少量远大于成交量，说明主要是撤单
                            # 如果买一量减少量与成交量相近，可能是成交
                            trade_volume_ratio = self.config_params.get('trade_volume_ratio', 0.8)
                            if abs(change) <= volume_change * (1 + trade_volume_ratio):  # 买一量减少量不超过成交量的1.8倍
                                reason = "成交"
                            else:
                                reason = "撤单"
                        else:
                            reason = "撤单"
            else:
                # 非涨停板时的异常变化
                if abs(change) >= normal_threshold:
                    is_abnormal = True
                    if change > 0:
                        change_type = "买一量增加"
                        reason = "新增买单"
                    else:
                        change_type = "买一量减少"
                        # 分析减少原因
                        if volume_change > 0:
                            # 有成交量，需要判断是成交还是撤单
                            # 如果买一量减少量远大于成交量，说明主要是撤单
                            # 如果买一量减少量与成交量相近，可能是成交
                            trade_volume_ratio = self.config_params.get('trade_volume_ratio', 0.8)
                            if abs(change) <= volume_change * (1 + trade_volume_ratio):  # 买一量减少量不超过成交量的1.8倍
                                reason = "成交"
                            else:
                                reason = "撤单"
                        else:
                            reason = "撤单"
            
            if is_abnormal:
                abnormal_changes.append({
                    'time': curr_time.strftime('%H:%M:%S'),
                    'type': change_type,
                    'reason': reason,
                    'before': prev_vol,
                    'after': curr_vol,
                    'change': change
                })
        
        return abnormal_changes
    
    def analyze_main_force_behavior(self, data):
        """分析主力行为（吸筹/出货）"""
        main_force_actions = []
        
        # 获取关键数据，排除尾盘集合竞价阶段
        behavior_data = []
        for idx, row in data.iterrows():
            # 检查是否为尾盘集合竞价阶段
            time_str = str(idx)
            if len(time_str) >= 10:
                hour = int(time_str[8:10])
                minute = int(time_str[10:12])
                # 排除尾盘集合竞价阶段（14:57-15:00）和15:00之后的数据
                if (hour == 14 and minute >= 57) or hour >= 15:
                    continue
            
            # 获取买卖盘口数据
            bid_price = row.get('bidPrice', [0])[0] if isinstance(row.get('bidPrice', []), list) and len(row.get('bidPrice', [])) > 0 else 0
            ask_price = row.get('askPrice', [0])[0] if isinstance(row.get('askPrice', []), list) and len(row.get('askPrice', [])) > 0 else 0
            bid_vol = row.get('bidVol', [0])[0] if isinstance(row.get('bidVol', []), list) and len(row.get('bidVol', [])) > 0 else 0
            ask_vol = row.get('askVol', [0])[0] if isinstance(row.get('askVol', []), list) and len(row.get('askVol', [])) > 0 else 0
            volume = row.get('volume', 0)
            last_price = row.get('lastPrice', 0)
            
            behavior_data.append({
                'time': pd.to_datetime(idx),
                'bid_price': bid_price,
                'ask_price': ask_price,
                'bid_vol': bid_vol,
                'ask_vol': ask_vol,
                'volume': volume,
                'last_price': last_price,
                'is_limit_up': row['is_limit_up']
            })
        
        if len(behavior_data) < 2:
            return main_force_actions
        
        # 分析主力行为
        for i in range(1, len(behavior_data)):
            prev_data = behavior_data[i-1]
            curr_data = behavior_data[i]
            
            # 计算关键指标
            volume_change = curr_data['volume'] - prev_data['volume']
            price_change = curr_data['last_price'] - prev_data['last_price']
            bid_vol_change = curr_data['bid_vol'] - prev_data['bid_vol']
            ask_vol_change = curr_data['ask_vol'] - prev_data['ask_vol']
            
            # 计算买卖压力比
            bid_pressure = curr_data['bid_vol'] if curr_data['bid_vol'] > 0 else 1
            ask_pressure = curr_data['ask_vol'] if curr_data['ask_vol'] > 0 else 1
            pressure_ratio = bid_pressure / ask_pressure
            
            # 判断主力行为
            action_type = ""
            intensity = ""
            description = ""
            
            # 吸筹特征判断（排除涨停板情况）
            accumulation_volume_threshold = self.config_params.get('accumulation_volume_threshold', 10000)
            accumulation_price_change = self.config_params.get('accumulation_price_change', 0)
            accumulation_bid_vol_change = self.config_params.get('accumulation_bid_vol_change', 50000)
            accumulation_pressure_ratio = self.config_params.get('accumulation_pressure_ratio', 1.5)
            accumulation_strong_threshold = self.config_params.get('accumulation_strong_threshold', 50000)
            accumulation_medium_threshold = self.config_params.get('accumulation_medium_threshold', 20000)
            
            if (not curr_data['is_limit_up'] and  # 排除涨停板情况
                volume_change > accumulation_volume_threshold and  # 成交量放大
                price_change > accumulation_price_change and      # 价格上涨
                bid_vol_change > accumulation_bid_vol_change and  # 买盘增加
                pressure_ratio > accumulation_pressure_ratio):     # 买盘压力大于卖盘
                
                action_type = "吸筹"
                if volume_change > accumulation_strong_threshold:
                    intensity = "强烈"
                elif volume_change > accumulation_medium_threshold:
                    intensity = "中等"
                else:
                    intensity = "轻微"
                    
                description = f"成交量放大{volume_change}手，价格上涨{price_change:.3f}元，买盘增加{bid_vol_change}手"
            
            # 出货特征判断
            elif (volume_change > self.config_params.get('distribution_volume_threshold', 10000) and  # 成交量放大
                  price_change < self.config_params.get('distribution_price_change', 0) and      # 价格下跌
                  ask_vol_change > self.config_params.get('distribution_ask_vol_change', 50000) and  # 卖盘增加
                  pressure_ratio < self.config_params.get('distribution_pressure_ratio', 0.7)):     # 卖盘压力大于买盘
                
                action_type = "出货"
                if volume_change > self.config_params.get('distribution_strong_threshold', 50000):
                    intensity = "强烈"
                elif volume_change > self.config_params.get('distribution_medium_threshold', 20000):
                    intensity = "中等"
                else:
                    intensity = "轻微"
                    
                description = f"成交量放大{volume_change}手，价格下跌{abs(price_change):.3f}元，卖盘增加{ask_vol_change}手"
            
            # 扫货特征判断（涨停板封板前）
            elif (not curr_data['is_limit_up'] and  # 还未涨停
                  volume_change > self.config_params.get('wash_volume_threshold', 15000) and  # 成交量较大
                  price_change > 0.001 and  # 价格明显上涨（超过0.1%）
                  bid_vol_change > 15000 and  # 买盘大幅增加
                  pressure_ratio > 1.05):  # 买盘压力明显大于卖盘
                
                action_type = "扫货"
                if volume_change > self.config_params.get('wash_strong_threshold', 30000):
                    intensity = "强烈"
                elif volume_change > self.config_params.get('wash_medium_threshold', 20000):
                    intensity = "中等"
                else:
                    intensity = "轻微"
                    
                description = f"封板前扫货，成交量放大{volume_change}手，价格上涨{price_change:.3f}元，买盘增加{bid_vol_change}手"
            
            # 洗盘特征判断（价格变化不大的震荡）
            elif (volume_change > self.config_params.get('wash_volume_threshold', 15000) and  # 成交量较大
                  abs(price_change) < self.config_params.get('wash_price_change_threshold', 0.01) and  # 价格变化不大
                  abs(bid_vol_change - ask_vol_change) < self.config_params.get('wash_vol_change_diff', 10000)):  # 买卖盘变化相近
                
                # 判断是否为扫货（洗盘的特殊情况：接近涨停板时的洗盘）
                if (not curr_data['is_limit_up'] and  # 还未涨停
                    price_change > 0 and  # 价格上涨
                    bid_vol_change > ask_vol_change):  # 买盘增加大于卖盘增加
                    action_type = "扫货"
                    description = f"封板前扫货，成交量放大{volume_change}手，价格上涨{price_change:.3f}元，买盘增加{bid_vol_change}手"
                else:
                    action_type = "洗盘"
                    description = f"成交量放大{volume_change}手，价格变化{price_change:.3f}元，买卖盘变化相近"
                
                if volume_change > self.config_params.get('wash_strong_threshold', 30000):
                    intensity = "强烈"
                elif volume_change > self.config_params.get('wash_medium_threshold', 20000):
                    intensity = "中等"
                else:
                    intensity = "轻微"
            
            # 护盘特征判断（涨停板时）
            elif (curr_data['is_limit_up'] and
                  bid_vol_change > self.config_params.get('support_bid_vol_change', 100000) and  # 买盘大幅增加
                  volume_change < self.config_params.get('support_volume_threshold', 5000)):      # 成交量不大
                
                action_type = "护盘"
                intensity = "强烈"
                description = f"涨停板护盘，买盘增加{bid_vol_change}手，成交量{volume_change}手"
            
            # 砸盘特征判断
            elif (volume_change > self.config_params.get('smash_volume_threshold', 20000) and  # 成交量很大
                  price_change < self.config_params.get('smash_price_change_threshold', -0.02) and   # 价格大幅下跌
                  ask_vol_change > self.config_params.get('smash_ask_vol_change', 100000)):  # 卖盘大幅增加
                
                action_type = "砸盘"
                intensity = "强烈"
                description = f"成交量放大{volume_change}手，价格大跌{abs(price_change):.3f}元，卖盘增加{ask_vol_change}手"
            
            if action_type:
                main_force_actions.append({
                    'time': curr_data['time'].strftime('%H:%M:%S'),
                    'type': action_type,
                    'intensity': intensity,
                    'volume': volume_change,
                    'price_change': price_change,
                    'description': description
                })
        
        return main_force_actions


class RecentAnalysisDialog(QDialog):
    """近期分析对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("近期分析")
        self.resize(1400, 800)
        self.setMinimumSize(1000, 600)
        
        # 设置UI
        self.setup_ui()
        
        # 创建状态更新定时器
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_status_display)
        self.status_timer.setInterval(2000)  # 每2秒更新一次，减少UI负担
        
        # 初始化默认阈值配置（在开始分析时会重新读取）
        self.up_thresholds = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0]
        self.down_thresholds = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0]
        
    def load_config(self):
        """加载配置参数"""
        self.config = configparser.ConfigParser()
        config_file = os.path.join('data', 'config.ini')
        
        if os.path.exists(config_file):
            self.config.read(config_file, encoding='utf-8')
            
        # 获取阈值配置
        if 'Recent Days' in self.config:
            up_thresholds_str = self.config.get('Recent Days', 'up_thresholds', fallback='0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0')
            down_thresholds_str = self.config.get('Recent Days', 'down_thresholds', fallback='0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0')
            
            self.up_thresholds = [float(x.strip()) for x in up_thresholds_str.split(',')]
            self.down_thresholds = [float(x.strip()) for x in down_thresholds_str.split(',')]
        else:
            # 默认值
            self.up_thresholds = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0]
            self.down_thresholds = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0]
    
    def setup_ui(self):
        """设置UI界面"""
        layout = QVBoxLayout()
        
        # 设置字体
        font = QFont()
        font.setPointSize(10)
        
        # 股票代码输入区域
        input_layout = QHBoxLayout()
        stock_label = QLabel("股票代码:")
        stock_label.setFont(font)
        input_layout.addWidget(stock_label)
        
        self.stock_input = QLineEdit()
        self.stock_input.setFont(font)
        self.stock_input.setPlaceholderText("请输入股票代码，如：000001")
        self.stock_input.setMaximumWidth(200)
        input_layout.addWidget(self.stock_input)
        
        # 分析按钮
        self.analyze_button = QPushButton("开始分析")
        self.analyze_button.setFont(font)
        self.analyze_button.clicked.connect(self.start_analysis)
        self.analyze_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        input_layout.addWidget(self.analyze_button)
        
        # 取消按钮
        self.cancel_button = QPushButton("取消分析")
        self.cancel_button.setFont(font)
        self.cancel_button.clicked.connect(self.cancel_analysis)
        self.cancel_button.setVisible(False)
        self.cancel_button.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #d32f2f;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        input_layout.addWidget(self.cancel_button)
        
        input_layout.addStretch()
        layout.addLayout(input_layout)
        
        # 进度条和状态信息
        progress_layout = QVBoxLayout()
        
        # 状态标签
        self.status_label = QLabel("准备就绪")
        self.status_label.setFont(font)
        self.status_label.setStyleSheet("color: #666666; font-style: italic;")
        self.status_label.setVisible(False)
        progress_layout.addWidget(self.status_label)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFormat("分析进度: %p%")
        progress_layout.addWidget(self.progress_bar)
        
        layout.addLayout(progress_layout)
        
        # 结果显示表格
        self.result_table = QTableWidget()
        self.result_table.setFont(font)
        self.result_table.setAlternatingRowColors(True)
        self.result_table.setMinimumHeight(600)
        layout.addWidget(self.result_table)
        
        self.setLayout(layout)
    
    def closeEvent(self, event):
        """关闭事件处理"""
        # 如果有分析线程在运行，先取消它
        if hasattr(self, 'analysis_thread') and self.analysis_thread and self.analysis_thread.isRunning():
            self.analysis_thread.requestInterruption()
            if not self.analysis_thread.wait(2000):  # 等待2秒
                self.analysis_thread.terminate()
                self.analysis_thread.wait(1000)
        
        # 停止定时器
        if hasattr(self, 'status_timer'):
            self.status_timer.stop()
        
        event.accept()
    
    def hideEvent(self, event):
        """隐藏事件处理"""
        # 停止定时器以节省资源
        if hasattr(self, 'status_timer'):
            self.status_timer.stop()
        event.accept()
    
    def showEvent(self, event):
        """显示事件处理"""
        # 重新启动定时器
        if hasattr(self, 'status_timer'):
            self.status_timer.start()
        event.accept()
    
    def start_analysis(self):
        """开始分析"""
        stock_code = self.stock_input.text().strip()
        if not stock_code:
            QMessageBox.warning(self, "警告", "请输入股票代码")
            return
        
        # 在开始分析时重新读取配置参数
        self.load_config()
        
        # 禁用分析按钮，启用取消按钮，显示进度条和状态
        self.analyze_button.setEnabled(False)
        self.cancel_button.setVisible(True)
        self.status_label.setVisible(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        
        # 更新状态
        self.status_label.setText("正在获取历史数据...")
        
        # 启动状态更新定时器
        self.status_timer.start()
        
        # 创建分析线程，使用最新读取的阈值参数
        self.analysis_thread = RecentAnalysisThread(stock_code, self.up_thresholds, self.down_thresholds)
        self.analysis_thread.analysis_complete.connect(self.on_analysis_complete)
        self.analysis_thread.analysis_error.connect(self.on_analysis_error)
        self.analysis_thread.progress_update.connect(self.on_progress_update)
        self.analysis_thread.status_update.connect(self.on_status_update)
        
        # 确保线程在完成后自动清理
        self.analysis_thread.finished.connect(self.analysis_thread.deleteLater)
        
        # 启动线程
        self.analysis_thread.start()
    
    def on_analysis_complete(self, results):
        """分析完成回调"""
        # 停止状态更新定时器
        self.status_timer.stop()
        
        self.analyze_button.setEnabled(True)
        self.cancel_button.setVisible(False)
        self.status_label.setVisible(False)
        self.progress_bar.setVisible(False)
        self.display_results(results)
    
    def on_analysis_error(self, error_msg):
        """分析错误回调"""
        # 停止状态更新定时器
        self.status_timer.stop()
        
        self.analyze_button.setEnabled(True)
        self.cancel_button.setVisible(False)
        self.status_label.setVisible(False)
        self.progress_bar.setVisible(False)
        QMessageBox.critical(self, "错误", error_msg)
    
    def cancel_analysis(self):
        """取消分析"""
        if hasattr(self, 'analysis_thread') and self.analysis_thread and self.analysis_thread.isRunning():
            reply = QMessageBox.question(
                self, 
                "确认取消", 
                "确定要取消当前的分析吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                # 请求中断线程
                self.analysis_thread.requestInterruption()
                
                # 等待线程结束，但设置超时
                if not self.analysis_thread.wait(3000):  # 等待3秒
                    # 如果超时，强制终止
                    self.analysis_thread.terminate()
                    self.analysis_thread.wait(1000)  # 再等待1秒
                
                # 停止状态更新定时器
                self.status_timer.stop()
                
                # 重置UI状态
                self.analyze_button.setEnabled(True)
                self.cancel_button.setVisible(False)
                self.status_label.setVisible(False)
                self.progress_bar.setVisible(False)
                
                QMessageBox.information(self, "已取消", "分析已取消")
    
    def on_progress_update(self, progress):
        """进度更新回调"""
        self.progress_bar.setValue(progress)
        
        # 根据进度更新状态信息，更频繁地更新
        if progress <= 10:
            self.status_label.setText("正在获取历史数据...")
        elif progress <= 20:
            self.status_label.setText("正在初始化分析参数...")
        elif progress <= 40:
            self.status_label.setText("正在分析交易策略...")
        elif progress <= 70:
            self.status_label.setText("正在计算收益率...")
        elif progress <= 90:
            self.status_label.setText("正在整理分析结果...")
        else:
            self.status_label.setText("分析完成！")
        
        # 强制处理事件，确保UI更新，但不要过于频繁
        if progress % 5 == 0:  # 每5%更新一次，减少UI阻塞
            QApplication.processEvents()
    
    def on_status_update(self, status_msg):
        """状态更新回调"""
        self.status_label.setText(status_msg)
        # 强制处理事件，确保UI更新
        QApplication.processEvents()
    
    def update_status_display(self):
        """定期更新状态显示，保持UI响应"""
        if hasattr(self, 'analysis_thread') and self.analysis_thread and self.analysis_thread.isRunning():
            # 如果分析线程正在运行，强制处理事件，但限制频率
            QApplication.processEvents()
    
    def display_results(self, results):
        """显示分析结果"""
        # 清空表格
        self.result_table.clear()
        
        if not results or not results['strategies']:
            self.result_table.setRowCount(1)
            self.result_table.setColumnCount(1)
            self.result_table.setHorizontalHeaderLabels(["结果"])
            self.result_table.setItem(0, 0, QTableWidgetItem("无分析结果"))
            return
        
        strategies = results['strategies']
        dates = results['dates']
        
        # 设置表格列数和标题
        columns = ["策略组合", "总收益率(%)", "交易次数"] + [f"{date}" for date in dates]
        self.result_table.setColumnCount(len(columns))
        self.result_table.setHorizontalHeaderLabels(columns)
        
        # 设置表格行数
        self.result_table.setRowCount(len(strategies))
        
        # 填充数据
        for row, (strategy_key, strategy_data) in enumerate(strategies.items()):
            # 策略组合
            self.result_table.setItem(row, 0, QTableWidgetItem(strategy_key))
            
            # 总收益率
            total_return = strategy_data['total_return']
            total_return_item = QTableWidgetItem(f"{total_return:.2f}")
            if total_return > 0:
                total_return_item.setBackground(QColor(255, 255, 200))  # 浅黄色
            elif total_return < 0:
                total_return_item.setBackground(QColor(255, 200, 200))  # 浅红色
            self.result_table.setItem(row, 1, total_return_item)
            
            # 交易次数
            trade_count = strategy_data['trade_count']
            self.result_table.setItem(row, 2, QTableWidgetItem(str(trade_count)))
            
            # 每日收益率
            daily_returns = strategy_data['daily_returns']
            for col, date in enumerate(dates):
                if date in daily_returns:
                    daily_return = daily_returns[date]
                    daily_item = QTableWidgetItem(f"{daily_return:.2f}")
                    if daily_return > 0:
                        daily_item.setBackground(QColor(255, 255, 200))
                    elif daily_return < 0:
                        daily_item.setBackground(QColor(255, 200, 200))
                    self.result_table.setItem(row, col + 3, daily_item)
                else:
                    self.result_table.setItem(row, col + 3, QTableWidgetItem("-"))
        
        # 调整列宽
        self.result_table.resizeColumnsToContents()


class RecentAnalysisThread(QThread):
    """近期分析线程"""
    
    analysis_complete = pyqtSignal(dict)
    analysis_error = pyqtSignal(str)
    progress_update = pyqtSignal(int)
    status_update = pyqtSignal(str)  # 新增状态更新信号
    
    def __init__(self, stock_code, up_thresholds, down_thresholds):
        super().__init__()
        self.stock_code = stock_code
        self.up_thresholds = up_thresholds
        self.down_thresholds = down_thresholds
    
    def run(self):
        """运行分析"""
        try:
            # 发送初始进度
            self.progress_update.emit(5)
            
            # 一次性获取所有可用日期和数据
            available_dates, daily_data_cache = self.get_available_dates_and_data()
            if not available_dates:
                self.analysis_error.emit("无法获取该股票的历史数据")
                return
            
            # 发送数据获取完成进度
            self.progress_update.emit(15)
            
            # 初始化策略结果字典
            strategies = {}
            for up_threshold in self.up_thresholds:
                for down_threshold in self.down_thresholds:
                    strategy_key = f"上涨{up_threshold}% 下跌{down_threshold}%"
                    strategies[strategy_key] = {
                        'total_return': 0,
                        'trade_count': 0,
                        'daily_returns': {}
                    }
            
            # 计算总工作量
            total_work = len(available_dates) * len(self.up_thresholds) * len(self.down_thresholds)
            current_work = 0
            
            # 逐日分析（使用缓存的数据）
            total_days = len(available_dates)
            start_time = time_module.time()  # 记录开始时间
            max_analysis_time = 300  # 最大分析时间5分钟
            
            for day_index, date in enumerate(available_dates):
                # 检查是否被取消
                if self.isInterruptionRequested():
                    return
                
                # 检查是否超时
                if time_module.time() - start_time > max_analysis_time:
                    self.analysis_error.emit("分析超时，请尝试减少分析范围或稍后重试")
                    return
                
                # 每处理一个日期就更新一次进度
                day_progress = 20 + int((day_index / total_days) * 10)  # 20-30%的进度范围
                self.progress_update.emit(day_progress)
                
                # 发送更详细的状态信息
                status_msg = f"正在分析 {date.strftime('%Y-%m-%d')} 的数据..."
                self.status_update.emit(status_msg)
                    
                try:
                    # 从缓存获取当日数据
                    daily_data = daily_data_cache.get(date)
                    if daily_data is None or len(daily_data) == 0:
                        # 跳过没有数据的日期，但更新进度
                        current_work += len(self.up_thresholds) * len(self.down_thresholds)
                        progress = 30 + int((current_work / total_work) * 65)  # 30-95%的进度范围
                        self.progress_update.emit(progress)
                        continue
                    
                    # 用所有阈值组合分析该日数据
                    for up_threshold in self.up_thresholds:
                        for down_threshold in self.down_thresholds:
                            # 检查是否被取消
                            if self.isInterruptionRequested():
                                return
                            
                            # 检查是否超时
                            if time_module.time() - start_time > max_analysis_time:
                                self.analysis_error.emit("分析超时，请尝试减少分析范围或稍后重试")
                                return
                                
                            strategy_key = f"上涨{up_threshold}% 下跌{down_threshold}%"
                            
                            # 分析当日交易
                            daily_return, trade_count = self.simulate_daily_trading(
                                daily_data, up_threshold, down_threshold
                            )
                            
                            # 更新策略结果
                            strategies[strategy_key]['daily_returns'][date.strftime('%Y-%m-%d')] = daily_return
                            strategies[strategy_key]['total_return'] += daily_return
                            strategies[strategy_key]['trade_count'] += trade_count
                            
                            # 更新进度（每3个策略更新一次，进一步提高响应性）
                            current_work += 1
                            if current_work % 3 == 0 or current_work == total_work:
                                progress = 30 + int((current_work / total_work) * 65)  # 30-95%的进度范围
                                self.progress_update.emit(progress)
                                # 添加小延迟，让UI线程有机会处理事件
                                self.msleep(1)
                    
                except Exception as e:
                    print(f"分析{date}时出错: {e}")
                    # 即使出错也要更新进度
                    current_work += len(self.up_thresholds) * len(self.down_thresholds)
                    progress = 15 + int((current_work / total_work) * 80)
                    self.progress_update.emit(progress)
                    continue
            
            # 检查是否被取消
            if self.isInterruptionRequested():
                return
                
            # 发送完成进度
            self.progress_update.emit(100)
            
            # 返回结果
            results = {
                'stock_code': self.stock_code,
                'strategies': strategies,
                'dates': [date.strftime('%Y-%m-%d') for date in available_dates]
            }
            
            self.analysis_complete.emit(results)
            
        except Exception as e:
            self.analysis_error.emit(f"分析过程中出现错误: {str(e)}")
    
    def get_available_dates_and_data(self):
        """按天获取最近30个交易日的数据"""
        try:
            from core.backtest_engine import BacktestEngine
            from utils.logger import Logger
            from datetime import date, timedelta
            from utils.trading_day import is_tradeday
            
            # 发送进度更新
            self.progress_update.emit(6)
            
            # 创建回测引擎
            engine = BacktestEngine(stock_code=self.stock_code)
            engine.set_logger(Logger())
            
            # 发送进度更新
            self.progress_update.emit(8)
            
            # 计算最近30个交易日
            available_dates = []
            daily_data_cache = {}
            
            # 从今天开始往前找30个交易日
            current_date = date.today()
            found_trading_days = 0
            max_search_days = 60  # 最多往前找60天
            search_count = 0
            
            while found_trading_days < 30 and search_count < max_search_days:
                if is_tradeday(current_date):
                    available_dates.append(current_date)
                    found_trading_days += 1
                
                current_date -= timedelta(days=1)
                search_count += 1
            
            # 按时间倒序排列（最新的在前）
            available_dates.reverse()
            
            # 发送进度更新
            self.progress_update.emit(10)
            self.status_update.emit("开始获取历史数据...")
            
            # 逐个日期获取数据
            total_dates = len(available_dates)
            for i, target_date in enumerate(available_dates):
                # 检查是否被取消
                if self.isInterruptionRequested():
                    return [], {}
                    
                try:
                    # 发送进度更新
                    progress = 10 + int((i / total_dates) * 10)  # 10-20%的进度范围
                    self.progress_update.emit(progress)
                    self.status_update.emit(f"正在获取 {target_date.strftime('%Y-%m-%d')} 的数据...")
                    
                    # 获取单日数据（添加超时保护）
                    try:
                        success = engine.load_data(target_date, target_date, save_to_file=False)
                        if success and engine.data is not None and len(engine.data) > 0:
                            daily_data_cache[target_date] = engine.data.copy()
                            print(f"成功获取 {target_date} 的数据，共 {len(engine.data)} 条记录")
                        else:
                            print(f"获取 {target_date} 的数据失败或为空")
                            daily_data_cache[target_date] = None
                    except Exception as load_error:
                        print(f"加载 {target_date} 数据时出现异常: {load_error}")
                        daily_data_cache[target_date] = None
                    
                    # 添加小延迟，让UI线程有机会处理事件
                    self.msleep(50)  # 增加延迟到50ms，给UI更多响应时间
                        
                except Exception as e:
                    print(f"获取 {target_date} 数据时出错: {e}")
                    daily_data_cache[target_date] = None
                
                # 清理内存，避免内存占用过大
                if hasattr(engine, 'data'):
                    del engine.data
                    engine.data = None
                
                # 添加小延迟，让UI线程有机会处理事件
                self.msleep(10)
            
            # 过滤掉没有数据的日期
            filtered_dates = [date for date in available_dates if daily_data_cache.get(date) is not None]
            filtered_cache = {date: daily_data_cache[date] for date in filtered_dates}
            
            print(f"成功获取 {len(filtered_dates)} 个交易日的数据")
            return filtered_dates, filtered_cache
            
        except Exception as e:
            print(f"获取可用日期和数据时出错: {e}")
            return [], {}
    

    
    def simulate_daily_trading(self, data, up_threshold, down_threshold):
        """模拟当日交易"""
        if len(data) == 0:
            return 0, 0
        
        # 获取开盘价
        open_price = None
        for idx, row in data.iterrows():
            time_str = str(idx)
            if len(time_str) >= 10:
                hour = int(time_str[8:10])
                minute = int(time_str[10:12])
                # 找到第一个9:30的数据作为开盘价
                if hour == 9 and minute >= 30:
                    open_price = row.get('lastPrice', 0)
                    break
        
        if open_price is None or open_price == 0:
            return 0, 0
        
        # 转换为百分比
        up_threshold_pct = up_threshold / 100.0
        down_threshold_pct = down_threshold / 100.0
        
        # 模拟交易
        current_price = open_price
        buy_price = None
        total_return = 0
        trade_count = 0
        
        for idx, row in data.iterrows():
            time_str = str(idx)
            if len(time_str) >= 10:
                hour = int(time_str[8:10])
                minute = int(time_str[10:12])
                # 只分析9:30-14:57的数据
                if (hour == 9 and minute >= 30) or (hour >= 10 and hour < 14) or (hour == 14 and minute < 57):
                    last_price = row.get('lastPrice', 0)
                    if last_price == 0:
                        continue
                    
                    # 计算价格变化百分比
                    price_change_pct = (last_price - current_price) / current_price
                    
                    if buy_price is None:
                        # 没有持仓，检查是否买入
                        if price_change_pct <= -down_threshold_pct:
                            buy_price = last_price
                            trade_count += 1
                    else:
                        # 有持仓，检查是否卖出
                        if price_change_pct >= up_threshold_pct:
                            # 卖出，计算收益
                            sell_return = (last_price - buy_price) / buy_price * 100
                            total_return += sell_return
                            buy_price = None
                            trade_count += 1
                    
                    # 更新当前价格
                    current_price = last_price
        
        return total_return, trade_count


class ParameterSettingsDialog(QDialog):
    """参数设置对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("参数设置")
        self.resize(800, 600)
        self.setMinimumSize(600, 400)
        
        # 加载配置
        self.load_config()
        
        # 设置UI
        self.setup_ui()
        
    def load_config(self):
        """加载配置文件"""
        import configparser
        import os
        
        self.config = configparser.ConfigParser()
        config_file = os.path.join('data', 'config.ini')
        
        if os.path.exists(config_file):
            self.config.read(config_file, encoding='utf-8')
        else:
            # 如果配置文件不存在，创建默认配置
            self.create_default_config()
            
    def create_default_config(self):
        """创建默认配置"""
        self.config['Account'] = {
            'account_id': '',
            'path_qmt': ''
        }
        self.config['DEFAULT'] = {
            'sell_times': '2',
            'max_days': '2',
            'up_threshold': '3',
            'down_threshold': '3.5'
        }
        self.config['Today Analyse'] = {
            'limit_up_bid_vol_threshold': '100000',
            'normal_bid_vol_threshold': '50000',
            'trade_volume_ratio': '0.8',
            'accumulation_volume_threshold': '10000',
            'accumulation_price_change': '0',
            'accumulation_bid_vol_change': '50000',
            'accumulation_pressure_ratio': '1.5',
            'accumulation_strong_threshold': '50000',
            'accumulation_medium_threshold': '20000',
            'distribution_volume_threshold': '10000',
            'distribution_price_change': '0',
            'distribution_ask_vol_change': '50000',
            'distribution_pressure_ratio': '0.7',
            'distribution_strong_threshold': '50000',
            'distribution_medium_threshold': '20000',
            'wash_volume_threshold': '15000',
            'wash_price_change_threshold': '0.01',
            'wash_vol_change_diff': '10000',
            'wash_strong_threshold': '30000',
            'wash_medium_threshold': '20000',
            'support_bid_vol_change': '100000',
            'support_volume_threshold': '5000',
            'smash_volume_threshold': '20000',
            'smash_price_change_threshold': '-0.02',
            'smash_ask_vol_change': '100000'
        }
        self.config['Recent Days'] = {
            'up_thresholds': '0.1, 0.3, 0.5, 1',
            'down_thresholds': '0.1, 0.3, 0.5, 1'
        }
        
    def setup_ui(self):
        """设置UI界面"""
        layout = QVBoxLayout()
        
        # 创建选项卡控件
        tab_widget = QTabWidget()
        
        # 账户设置选项卡
        account_tab = self.create_account_tab()
        tab_widget.addTab(account_tab, "账户设置")
        
        # 默认参数选项卡
        default_tab = self.create_default_tab()
        tab_widget.addTab(default_tab, "默认参数")
        
        # 当日分析参数选项卡
        today_tab = self.create_today_analysis_tab()
        tab_widget.addTab(today_tab, "当日分析参数")
        
        # 近期分析参数选项卡
        recent_tab = self.create_recent_analysis_tab()
        tab_widget.addTab(recent_tab, "近期分析参数")
        
        layout.addWidget(tab_widget)
        
        # 按钮布局
        button_layout = QHBoxLayout()
        
        # 保存按钮
        save_button = QPushButton("保存")
        save_button.clicked.connect(self.save_config)
        button_layout.addWidget(save_button)
        
        # 取消按钮
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)
        
        # 重置按钮
        reset_button = QPushButton("重置")
        reset_button.clicked.connect(self.reset_config)
        button_layout.addWidget(reset_button)
        
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
        
    def create_account_tab(self):
        """创建账户设置选项卡"""
        widget = QWidget()
        layout = QVBoxLayout()
        
        # 账户ID
        account_id_layout = QHBoxLayout()
        account_id_label = QLabel("账户ID:")
        self.account_id_edit = QLineEdit()
        self.account_id_edit.setText(self.config.get('Account', 'account_id', fallback=''))
        account_id_layout.addWidget(account_id_label)
        account_id_layout.addWidget(self.account_id_edit)
        layout.addLayout(account_id_layout)
        
        # QMT路径
        qmt_path_layout = QHBoxLayout()
        qmt_path_label = QLabel("QMT路径:")
        self.qmt_path_edit = QLineEdit()
        self.qmt_path_edit.setText(self.config.get('Account', 'path_qmt', fallback=''))
        qmt_path_button = QPushButton("浏览")
        qmt_path_button.clicked.connect(self.browse_qmt_path)
        qmt_path_layout.addWidget(qmt_path_label)
        qmt_path_layout.addWidget(self.qmt_path_edit)
        qmt_path_layout.addWidget(qmt_path_button)
        layout.addLayout(qmt_path_layout)
        
        layout.addStretch()
        widget.setLayout(layout)
        return widget
        
    def create_default_tab(self):
        """创建默认参数选项卡"""
        widget = QWidget()
        layout = QVBoxLayout()
        
        # 卖出次数
        sell_times_layout = QHBoxLayout()
        sell_times_label = QLabel("卖出次数:")
        self.sell_times_edit = QSpinBox()
        self.sell_times_edit.setRange(1, 10)
        self.sell_times_edit.setValue(int(self.config.get('DEFAULT', 'sell_times', fallback='2')))
        sell_times_layout.addWidget(sell_times_label)
        sell_times_layout.addWidget(self.sell_times_edit)
        layout.addLayout(sell_times_layout)
        
        # 最大天数
        max_days_layout = QHBoxLayout()
        max_days_label = QLabel("最大天数:")
        self.max_days_edit = QSpinBox()
        self.max_days_edit.setRange(1, 30)
        self.max_days_edit.setValue(int(self.config.get('DEFAULT', 'max_days', fallback='2')))
        max_days_layout.addWidget(max_days_label)
        max_days_layout.addWidget(self.max_days_edit)
        layout.addLayout(max_days_layout)
        
        # 上涨阈值
        up_threshold_layout = QHBoxLayout()
        up_threshold_label = QLabel("上涨阈值(%):")
        self.up_threshold_edit = QDoubleSpinBox()
        self.up_threshold_edit.setRange(0.1, 10.0)
        self.up_threshold_edit.setSingleStep(0.1)
        self.up_threshold_edit.setValue(float(self.config.get('DEFAULT', 'up_threshold', fallback='3.0')))
        up_threshold_layout.addWidget(up_threshold_label)
        up_threshold_layout.addWidget(self.up_threshold_edit)
        layout.addLayout(up_threshold_layout)
        
        # 下跌阈值
        down_threshold_layout = QHBoxLayout()
        down_threshold_label = QLabel("下跌阈值(%):")
        self.down_threshold_edit = QDoubleSpinBox()
        self.down_threshold_edit.setRange(0.1, 10.0)
        self.down_threshold_edit.setSingleStep(0.1)
        self.down_threshold_edit.setValue(float(self.config.get('DEFAULT', 'down_threshold', fallback='3.5')))
        down_threshold_layout.addWidget(down_threshold_label)
        down_threshold_layout.addWidget(self.down_threshold_edit)
        layout.addLayout(down_threshold_layout)
        
        layout.addStretch()
        widget.setLayout(layout)
        return widget
        
    def create_today_analysis_tab(self):
        """创建当日分析参数选项卡"""
        widget = QWidget()
        layout = QVBoxLayout()
        
        # 创建滚动区域
        scroll_area = QScrollArea()
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout()
        
        # 买一量异常变化检测参数
        scroll_layout.addWidget(QLabel("买一量异常变化检测参数:"))
        
        limit_up_bid_vol_threshold_layout = QHBoxLayout()
        limit_up_bid_vol_threshold_label = QLabel("涨停板买一量阈值:")
        self.limit_up_bid_vol_threshold_edit = QSpinBox()
        self.limit_up_bid_vol_threshold_edit.setRange(0, 1000000)
        self.limit_up_bid_vol_threshold_edit.setValue(int(self.config.get('Today Analyse', 'limit_up_bid_vol_threshold', fallback='100000')))
        limit_up_bid_vol_threshold_layout.addWidget(limit_up_bid_vol_threshold_label)
        limit_up_bid_vol_threshold_layout.addWidget(self.limit_up_bid_vol_threshold_edit)
        scroll_layout.addLayout(limit_up_bid_vol_threshold_layout)
        
        normal_bid_vol_threshold_layout = QHBoxLayout()
        normal_bid_vol_threshold_label = QLabel("正常买一量阈值:")
        self.normal_bid_vol_threshold_edit = QSpinBox()
        self.normal_bid_vol_threshold_edit.setRange(0, 1000000)
        self.normal_bid_vol_threshold_edit.setValue(int(self.config.get('Today Analyse', 'normal_bid_vol_threshold', fallback='50000')))
        normal_bid_vol_threshold_layout.addWidget(normal_bid_vol_threshold_label)
        normal_bid_vol_threshold_layout.addWidget(self.normal_bid_vol_threshold_edit)
        scroll_layout.addLayout(normal_bid_vol_threshold_layout)
        
        trade_volume_ratio_layout = QHBoxLayout()
        trade_volume_ratio_label = QLabel("交易量比例:")
        self.trade_volume_ratio_edit = QDoubleSpinBox()
        self.trade_volume_ratio_edit.setRange(0, 2)
        self.trade_volume_ratio_edit.setSingleStep(0.1)
        self.trade_volume_ratio_edit.setValue(float(self.config.get('Today Analyse', 'trade_volume_ratio', fallback='0.8')))
        trade_volume_ratio_layout.addWidget(trade_volume_ratio_label)
        trade_volume_ratio_layout.addWidget(self.trade_volume_ratio_edit)
        scroll_layout.addLayout(trade_volume_ratio_layout)
        
        # 主力行为分析参数 - 吸筹
        scroll_layout.addWidget(QLabel("主力行为分析参数 - 吸筹:"))
        
        accumulation_volume_threshold_layout = QHBoxLayout()
        accumulation_volume_threshold_label = QLabel("吸筹成交量阈值:")
        self.accumulation_volume_threshold_edit = QSpinBox()
        self.accumulation_volume_threshold_edit.setRange(0, 1000000)
        self.accumulation_volume_threshold_edit.setValue(int(self.config.get('Today Analyse', 'accumulation_volume_threshold', fallback='10000')))
        accumulation_volume_threshold_layout.addWidget(accumulation_volume_threshold_label)
        accumulation_volume_threshold_layout.addWidget(self.accumulation_volume_threshold_edit)
        scroll_layout.addLayout(accumulation_volume_threshold_layout)
        
        accumulation_pressure_ratio_layout = QHBoxLayout()
        accumulation_pressure_ratio_label = QLabel("吸筹压力比例:")
        self.accumulation_pressure_ratio_edit = QDoubleSpinBox()
        self.accumulation_pressure_ratio_edit.setRange(0, 10)
        self.accumulation_pressure_ratio_edit.setSingleStep(0.1)
        self.accumulation_pressure_ratio_edit.setValue(float(self.config.get('Today Analyse', 'accumulation_pressure_ratio', fallback='1.5')))
        accumulation_pressure_ratio_layout.addWidget(accumulation_pressure_ratio_label)
        accumulation_pressure_ratio_layout.addWidget(self.accumulation_pressure_ratio_edit)
        scroll_layout.addLayout(accumulation_pressure_ratio_layout)
        
        # 主力行为分析参数 - 出货
        scroll_layout.addWidget(QLabel("主力行为分析参数 - 出货:"))
        
        distribution_volume_threshold_layout = QHBoxLayout()
        distribution_volume_threshold_label = QLabel("出货成交量阈值:")
        self.distribution_volume_threshold_edit = QSpinBox()
        self.distribution_volume_threshold_edit.setRange(0, 1000000)
        self.distribution_volume_threshold_edit.setValue(int(self.config.get('Today Analyse', 'distribution_volume_threshold', fallback='10000')))
        distribution_volume_threshold_layout.addWidget(distribution_volume_threshold_label)
        distribution_volume_threshold_layout.addWidget(self.distribution_volume_threshold_edit)
        scroll_layout.addLayout(distribution_volume_threshold_layout)
        
        distribution_pressure_ratio_layout = QHBoxLayout()
        distribution_pressure_ratio_label = QLabel("出货压力比例:")
        self.distribution_pressure_ratio_edit = QDoubleSpinBox()
        self.distribution_pressure_ratio_edit.setRange(0, 10)
        self.distribution_pressure_ratio_edit.setSingleStep(0.1)
        self.distribution_pressure_ratio_edit.setValue(float(self.config.get('Today Analyse', 'distribution_pressure_ratio', fallback='0.7')))
        distribution_pressure_ratio_layout.addWidget(distribution_pressure_ratio_label)
        distribution_pressure_ratio_layout.addWidget(self.distribution_pressure_ratio_edit)
        scroll_layout.addLayout(distribution_pressure_ratio_layout)
        
        # 主力行为分析参数 - 洗盘
        scroll_layout.addWidget(QLabel("主力行为分析参数 - 洗盘:"))
        
        wash_volume_threshold_layout = QHBoxLayout()
        wash_volume_threshold_label = QLabel("洗盘成交量阈值:")
        self.wash_volume_threshold_edit = QSpinBox()
        self.wash_volume_threshold_edit.setRange(0, 1000000)
        self.wash_volume_threshold_edit.setValue(int(self.config.get('Today Analyse', 'wash_volume_threshold', fallback='15000')))
        wash_volume_threshold_layout.addWidget(wash_volume_threshold_label)
        wash_volume_threshold_layout.addWidget(self.wash_volume_threshold_edit)
        scroll_layout.addLayout(wash_volume_threshold_layout)
        
        wash_price_change_threshold_layout = QHBoxLayout()
        wash_price_change_threshold_label = QLabel("洗盘价格变化阈值:")
        self.wash_price_change_threshold_edit = QDoubleSpinBox()
        self.wash_price_change_threshold_edit.setRange(-1, 1)
        self.wash_price_change_threshold_edit.setSingleStep(0.01)
        self.wash_price_change_threshold_edit.setValue(float(self.config.get('Today Analyse', 'wash_price_change_threshold', fallback='0.01')))
        wash_price_change_threshold_layout.addWidget(wash_price_change_threshold_label)
        wash_price_change_threshold_layout.addWidget(self.wash_price_change_threshold_edit)
        scroll_layout.addLayout(wash_price_change_threshold_layout)
        
        # 主力行为分析参数 - 护盘
        scroll_layout.addWidget(QLabel("主力行为分析参数 - 护盘:"))
        
        support_bid_vol_change_layout = QHBoxLayout()
        support_bid_vol_change_label = QLabel("护盘买一量变化:")
        self.support_bid_vol_change_edit = QSpinBox()
        self.support_bid_vol_change_edit.setRange(0, 1000000)
        self.support_bid_vol_change_edit.setValue(int(self.config.get('Today Analyse', 'support_bid_vol_change', fallback='100000')))
        support_bid_vol_change_layout.addWidget(support_bid_vol_change_label)
        support_bid_vol_change_layout.addWidget(self.support_bid_vol_change_edit)
        scroll_layout.addLayout(support_bid_vol_change_layout)
        
        support_volume_threshold_layout = QHBoxLayout()
        support_volume_threshold_label = QLabel("护盘成交量阈值:")
        self.support_volume_threshold_edit = QSpinBox()
        self.support_volume_threshold_edit.setRange(0, 1000000)
        self.support_volume_threshold_edit.setValue(int(self.config.get('Today Analyse', 'support_volume_threshold', fallback='5000')))
        support_volume_threshold_layout.addWidget(support_volume_threshold_label)
        support_volume_threshold_layout.addWidget(self.support_volume_threshold_edit)
        scroll_layout.addLayout(support_volume_threshold_layout)
        
        # 主力行为分析参数 - 砸盘
        scroll_layout.addWidget(QLabel("主力行为分析参数 - 砸盘:"))
        
        smash_volume_threshold_layout = QHBoxLayout()
        smash_volume_threshold_label = QLabel("砸盘成交量阈值:")
        self.smash_volume_threshold_edit = QSpinBox()
        self.smash_volume_threshold_edit.setRange(0, 1000000)
        self.smash_volume_threshold_edit.setValue(int(self.config.get('Today Analyse', 'smash_volume_threshold', fallback='20000')))
        smash_volume_threshold_layout.addWidget(smash_volume_threshold_label)
        smash_volume_threshold_layout.addWidget(self.smash_volume_threshold_edit)
        scroll_layout.addLayout(smash_volume_threshold_layout)
        
        smash_price_change_threshold_layout = QHBoxLayout()
        smash_price_change_threshold_label = QLabel("砸盘价格变化阈值:")
        self.smash_price_change_threshold_edit = QDoubleSpinBox()
        self.smash_price_change_threshold_edit.setRange(-1, 1)
        self.smash_price_change_threshold_edit.setSingleStep(0.01)
        self.smash_price_change_threshold_edit.setValue(float(self.config.get('Today Analyse', 'smash_price_change_threshold', fallback='-0.02')))
        smash_price_change_threshold_layout.addWidget(smash_price_change_threshold_label)
        smash_price_change_threshold_layout.addWidget(self.smash_price_change_threshold_edit)
        scroll_layout.addLayout(smash_price_change_threshold_layout)
        
        smash_ask_vol_change_layout = QHBoxLayout()
        smash_ask_vol_change_label = QLabel("砸盘卖一量变化:")
        self.smash_ask_vol_change_edit = QSpinBox()
        self.smash_ask_vol_change_edit.setRange(0, 1000000)
        self.smash_ask_vol_change_edit.setValue(int(self.config.get('Today Analyse', 'smash_ask_vol_change', fallback='100000')))
        smash_ask_vol_change_layout.addWidget(smash_ask_vol_change_label)
        smash_ask_vol_change_layout.addWidget(self.smash_ask_vol_change_edit)
        scroll_layout.addLayout(smash_ask_vol_change_layout)
        
        scroll_layout.addStretch()
        scroll_widget.setLayout(scroll_layout)
        scroll_area.setWidget(scroll_widget)
        scroll_area.setWidgetResizable(True)
        
        layout.addWidget(scroll_area)
        widget.setLayout(layout)
        return widget
        
    def create_recent_analysis_tab(self):
        """创建近期分析参数选项卡"""
        widget = QWidget()
        layout = QVBoxLayout()
        
        # 上涨阈值列表
        up_thresholds_layout = QHBoxLayout()
        up_thresholds_label = QLabel("上涨阈值列表(%):")
        self.up_thresholds_edit = QLineEdit()
        self.up_thresholds_edit.setText(self.config.get('Recent Days', 'up_thresholds', fallback='0.1, 0.3, 0.5, 1'))
        up_thresholds_layout.addWidget(up_thresholds_label)
        up_thresholds_layout.addWidget(self.up_thresholds_edit)
        layout.addLayout(up_thresholds_layout)
        
        # 下跌阈值列表
        down_thresholds_layout = QHBoxLayout()
        down_thresholds_label = QLabel("下跌阈值列表(%):")
        self.down_thresholds_edit = QLineEdit()
        self.down_thresholds_edit.setText(self.config.get('Recent Days', 'down_thresholds', fallback='0.1, 0.3, 0.5, 1'))
        down_thresholds_layout.addWidget(down_thresholds_label)
        down_thresholds_layout.addWidget(self.down_thresholds_edit)
        layout.addLayout(down_thresholds_layout)
        
        # 说明文本
        info_label = QLabel("注意: 阈值列表使用逗号分隔，例如: 0.1, 0.3, 0.5, 1")
        info_label.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(info_label)
        
        layout.addStretch()
        widget.setLayout(layout)
        return widget
        
    def browse_qmt_path(self):
        """浏览QMT路径"""
        from PyQt5.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(self, "选择QMT路径")
        if path:
            self.qmt_path_edit.setText(path)
            
    def save_config(self):
        """保存配置"""
        try:
            # 更新配置对象
            self.config['Account']['account_id'] = self.account_id_edit.text()
            self.config['Account']['path_qmt'] = self.qmt_path_edit.text()
            
            self.config['DEFAULT']['sell_times'] = str(self.sell_times_edit.value())
            self.config['DEFAULT']['max_days'] = str(self.max_days_edit.value())
            self.config['DEFAULT']['up_threshold'] = str(self.up_threshold_edit.value())
            self.config['DEFAULT']['down_threshold'] = str(self.down_threshold_edit.value())
            
            self.config['Today Analyse']['limit_up_bid_vol_threshold'] = str(self.limit_up_bid_vol_threshold_edit.value())
            self.config['Today Analyse']['normal_bid_vol_threshold'] = str(self.normal_bid_vol_threshold_edit.value())
            self.config['Today Analyse']['trade_volume_ratio'] = str(self.trade_volume_ratio_edit.value())
            self.config['Today Analyse']['accumulation_volume_threshold'] = str(self.accumulation_volume_threshold_edit.value())
            self.config['Today Analyse']['accumulation_pressure_ratio'] = str(self.accumulation_pressure_ratio_edit.value())
            self.config['Today Analyse']['distribution_volume_threshold'] = str(self.distribution_volume_threshold_edit.value())
            self.config['Today Analyse']['distribution_pressure_ratio'] = str(self.distribution_pressure_ratio_edit.value())
            self.config['Today Analyse']['wash_volume_threshold'] = str(self.wash_volume_threshold_edit.value())
            self.config['Today Analyse']['wash_price_change_threshold'] = str(self.wash_price_change_threshold_edit.value())
            self.config['Today Analyse']['support_bid_vol_change'] = str(self.support_bid_vol_change_edit.value())
            self.config['Today Analyse']['support_volume_threshold'] = str(self.support_volume_threshold_edit.value())
            self.config['Today Analyse']['smash_volume_threshold'] = str(self.smash_volume_threshold_edit.value())
            self.config['Today Analyse']['smash_price_change_threshold'] = str(self.smash_price_change_threshold_edit.value())
            self.config['Today Analyse']['smash_ask_vol_change'] = str(self.smash_ask_vol_change_edit.value())
            
            self.config['Recent Days']['up_thresholds'] = self.up_thresholds_edit.text()
            self.config['Recent Days']['down_thresholds'] = self.down_thresholds_edit.text()
            
            # 保存到文件
            import os
            config_file = os.path.join('data', 'config.ini')
            os.makedirs(os.path.dirname(config_file), exist_ok=True)
            
            with open(config_file, 'w', encoding='utf-8') as f:
                self.config.write(f)
                
            QMessageBox.information(self, "成功", "参数设置已保存")
            self.accept()
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存配置失败: {str(e)}")
            
    def reset_config(self):
        """重置配置"""
        reply = QMessageBox.question(self, "确认重置", "确定要重置所有参数为默认值吗？", 
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.create_default_config()
            self.load_config()
            self.setup_ui()
            QMessageBox.information(self, "成功", "参数已重置为默认值")


class RadarStockSelectionDialog(QDialog):
    """雷达选股对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("雷达选股")
        self.resize(1200, 1000)  # 增加窗口高度
        self.setMinimumSize(600, 600)
        
        # 备选股票池文件路径
        self.candidate_pool_file = os.path.join('data', 'candidate_stock_pool.csv')
        
        # 初始化配置参数
        self.config_params = self.get_default_config()
        
        # 设置UI
        self.setup_ui()
        
        # 加载备选股票池
        self.load_candidate_pool()
        
    def setup_ui(self):
        """设置UI"""
        layout = QVBoxLayout()
        
        # 备选股票池区域
        candidate_group = QFrame()
        candidate_group.setFrameStyle(QFrame.StyledPanel)
        candidate_layout = QVBoxLayout()
        
        candidate_header_layout = QHBoxLayout()
        candidate_label = QLabel("备选股票池:")
        candidate_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        candidate_header_layout.addWidget(candidate_label)
        
        self.candidate_count_label = QLabel("共 0 只股票")
        candidate_header_layout.addWidget(self.candidate_count_label)
        candidate_header_layout.addStretch()
        
        # 添加操作按钮
        self.add_stock_button = QPushButton("添加股票")
        self.add_stock_button.setMinimumSize(80, 30)
        self.add_stock_button.clicked.connect(self.add_stock)
        candidate_header_layout.addWidget(self.add_stock_button)
        
        self.delete_stock_button = QPushButton("删除选中")
        self.delete_stock_button.setMinimumSize(80, 30)
        self.delete_stock_button.clicked.connect(self.delete_selected_stocks)
        candidate_header_layout.addWidget(self.delete_stock_button)
        
        self.import_from_file_button = QPushButton("从文件导入")
        self.import_from_file_button.setMinimumSize(80, 30)
        self.import_from_file_button.clicked.connect(self.import_from_file)
        candidate_header_layout.addWidget(self.import_from_file_button)
        
        self.clear_all_button = QPushButton("一键清空")
        self.clear_all_button.setMinimumSize(80, 30)
        self.clear_all_button.clicked.connect(self.clear_all_stocks)
        candidate_header_layout.addWidget(self.clear_all_button)
        
        candidate_layout.addLayout(candidate_header_layout)
        
        # 备选股票池表格
        self.candidate_table = QTableWidget()
        self.candidate_table.setColumnCount(4)
        self.candidate_table.setHorizontalHeaderLabels(["股票代码", "股票名称", "所属市场", "上市日期"])
        self.candidate_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.candidate_table.setMinimumHeight(200)
        
        # 设置表格可编辑
        self.candidate_table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        self.candidate_table.itemChanged.connect(self.on_candidate_table_changed)
        
        candidate_layout.addWidget(self.candidate_table)
        
        candidate_group.setLayout(candidate_layout)
        layout.addWidget(candidate_group)
        
        # 满足条件的股票列表区域
        result_group = QFrame()
        result_group.setFrameStyle(QFrame.StyledPanel)
        result_layout = QVBoxLayout()
        
        result_header_layout = QHBoxLayout()
        result_label = QLabel("满足至少一个条件的股票列表:")
        result_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        result_header_layout.addWidget(result_label)
        
        self.result_count_label = QLabel("已分析 0 只股票")
        result_header_layout.addWidget(self.result_count_label)
        result_header_layout.addStretch()
        
        result_layout.addLayout(result_header_layout)
        
        # 满足条件的股票表格
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(10)
        self.result_table.setHorizontalHeaderLabels(["股票代码", "股票名称", "当前价格", "涨跌幅", "成交量", "吸筹次数", "出货次数", "洗盘次数", "护盘次数", "砸盘次数"])
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.result_table.setMinimumHeight(200)
        result_layout.addWidget(self.result_table)
        
        result_group.setLayout(result_layout)
        layout.addWidget(result_group)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.start_button = QPushButton("开始雷达选股")
        self.start_button.setMinimumSize(120, 40)
        self.start_button.clicked.connect(self.start_radar_selection)
        button_layout.addWidget(self.start_button)
        
        self.close_button = QPushButton("关闭")
        self.close_button.setMinimumSize(80, 40)
        self.close_button.clicked.connect(self.close)
        button_layout.addWidget(self.close_button)
        
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
    
    def get_default_config(self):
        """获取默认配置"""
        return {
            'accumulation_volume_threshold': 10000,
            'accumulation_price_change': 0,
            'accumulation_bid_vol_change': 50000,
            'accumulation_pressure_ratio': 1.5,
            'accumulation_strong_threshold': 50000,
            'accumulation_medium_threshold': 20000,
            'distribution_volume_threshold': 10000,
            'distribution_price_change': 0,
            'distribution_ask_vol_change': 50000,
            'distribution_pressure_ratio': 0.7,
            'distribution_strong_threshold': 50000,
            'distribution_medium_threshold': 20000,
            'wash_volume_threshold': 15000,
            'wash_price_change_threshold': 0.01,
            'wash_vol_change_diff': 10000,
            'wash_strong_threshold': 30000,
            'wash_medium_threshold': 20000,
            'support_bid_vol_change': 100000,
            'support_volume_threshold': 5000,
            'smash_volume_threshold': 20000,
            'smash_price_change_threshold': -0.02,
            'smash_ask_vol_change': 100000
        }
    
    def load_config(self):
        """加载配置文件"""
        import configparser
        import os
        
        config = configparser.ConfigParser()
        config_file = os.path.join('data', 'config.ini')
        
        # 获取默认配置
        default_config = self.get_default_config()
        
        try:
            if os.path.exists(config_file):
                config.read(config_file, encoding='utf-8')
                if 'Today Analyse' in config:
                    section = config['Today Analyse']
                    for key in default_config:
                        if key in section:
                            try:
                                if isinstance(default_config[key], int):
                                    default_config[key] = section.getint(key)
                                elif isinstance(default_config[key], float):
                                    default_config[key] = section.getfloat(key)
                                else:
                                    default_config[key] = section.get(key)
                            except (ValueError, TypeError):
                                # 如果转换失败，使用默认值
                                pass
        except Exception as e:
            print(f"加载配置文件失败: {e}")
        
        return default_config
        
    def start_radar_selection(self):
        """开始雷达选股"""
        try:
            # 在开始分析时重新读取配置参数
            self.config_params = self.load_config()
            
            # 获取备选股票池中的股票
            candidate_stocks = self.get_candidate_stocks()
            
            if not candidate_stocks:
                QMessageBox.warning(self, "警告", "备选股票池为空，请先添加股票")
                return
            
            self.start_button.setEnabled(False)
            self.start_button.setText("分析中...")
            
            # 清空结果表格
            self.result_table.setRowCount(0)
            analyzed_count = 0
            qualified_count = 0
            
            # 逐个分析股票
            for i, stock in enumerate(candidate_stocks):
                # 更新进度
                progress = int((i + 1) / len(candidate_stocks) * 100)
                self.start_button.setText(f"分析中... {progress}%")
                QApplication.processEvents()  # 更新UI
                
                # 分析股票行为
                analysis_result = self.analyze_stock_behavior(stock['code'], stock['name'])
                analyzed_count += 1
                
                if analysis_result:
                    # 检查是否有至少一个非零的主力行为
                    behavior_counts = analysis_result.get('behavior_counts', {})
                    total_behaviors = sum(behavior_counts.values())
                    
                    if total_behaviors > 0:
                        stock.update(analysis_result)
                        qualified_count += 1
                        
                        # 添加到结果表格
                        self.add_result_row(stock)
                
                # 更新已分析数量
                self.result_count_label.setText(f"已分析 {analyzed_count} 只股票，满足条件 {qualified_count} 只")
                QApplication.processEvents()  # 更新UI
            
            self.start_button.setText("分析完成")
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"分析过程中发生错误: {str(e)}")
        finally:
            self.start_button.setEnabled(True)
            self.start_button.setText("开始雷达选股")
    
    def add_result_row(self, stock):
        """添加一行到结果表格"""
        row_index = self.result_table.rowCount()
        self.result_table.insertRow(row_index)
        
        # 显示完整分析结果
        self.result_table.setItem(row_index, 0, QTableWidgetItem(stock['code']))
        self.result_table.setItem(row_index, 1, QTableWidgetItem(stock['name']))
        self.result_table.setItem(row_index, 2, QTableWidgetItem(f"{stock['price']:.2f}"))
        self.result_table.setItem(row_index, 3, QTableWidgetItem(f"{stock['change_pct']:.2f}%"))
        self.result_table.setItem(row_index, 4, QTableWidgetItem(f"{stock['volume']:,}"))
        
        # 显示主力行为统计
        behavior_counts = stock.get('behavior_counts', {})
        self.result_table.setItem(row_index, 5, QTableWidgetItem(str(behavior_counts.get('accumulation', 0))))
        self.result_table.setItem(row_index, 6, QTableWidgetItem(str(behavior_counts.get('distribution', 0))))
        self.result_table.setItem(row_index, 7, QTableWidgetItem(str(behavior_counts.get('wash', 0))))
        self.result_table.setItem(row_index, 8, QTableWidgetItem(str(behavior_counts.get('support', 0))))
        self.result_table.setItem(row_index, 9, QTableWidgetItem(str(behavior_counts.get('smash', 0))))
    

    
    def get_stock_market(self, stock_code):
        """根据股票代码判断市场"""
        if stock_code.startswith('6'):
            return 'sh'
        elif stock_code.startswith('0'):
            return 'sz'
        elif stock_code.startswith('3'):
            return 'sz_gem'
        elif stock_code.startswith('688'):
            return 'sh_star'
        else:
            return 'unknown'
    
    def analyze_stock_behavior(self, stock_code, stock_name):
        """分析单只股票的主力行为"""
        try:
            from core.backtest_engine import BacktestEngine
            from utils.logger import Logger
            from datetime import datetime
            
            # 创建回测引擎
            engine = BacktestEngine(stock_code=stock_code)
            engine.set_logger(Logger())
            
            # 获取今天的日期
            today = datetime.now().date()
            
            # 加载今天的数据
            success = engine.load_data(today, today)
            if not success or engine.data is None or engine.data.empty:
                return None
            
            # 计算涨停板状态
            engine.data['is_limit_up'] = (engine.data['askPrice'].apply(lambda x: x[0] == 0)) & (engine.data['askVol'].apply(lambda x: x[0] == 0))
            
            # 分析主力行为
            behavior_analysis = self.analyze_main_force_behavior(engine.data)
            
            # 统计各种行为次数
            behavior_counts = {
                'accumulation': 0,  # 吸筹
                'distribution': 0,  # 出货
                'wash': 0,          # 洗盘
                'support': 0,       # 护盘
                'smash': 0          # 砸盘
            }
            
            for behavior in behavior_analysis:
                behavior_type = behavior['type']
                if behavior_type == '吸筹':
                    behavior_counts['accumulation'] += 1
                elif behavior_type == '出货':
                    behavior_counts['distribution'] += 1
                elif behavior_type == '洗盘':
                    behavior_counts['wash'] += 1
                elif behavior_type == '护盘':
                    behavior_counts['support'] += 1
                elif behavior_type == '砸盘':
                    behavior_counts['smash'] += 1
            
            # 调试信息：打印配置参数和检测到的行为数量
            print(f"股票 {stock_code} 分析结果:")
            print(f"  配置参数: accumulation_volume_threshold={self.config_params.get('accumulation_volume_threshold')}, "
                  f"distribution_volume_threshold={self.config_params.get('distribution_volume_threshold')}, "
                  f"smash_volume_threshold={self.config_params.get('smash_volume_threshold')}")
            print(f"  检测到的行为: 吸筹={behavior_counts['accumulation']}, 出货={behavior_counts['distribution']}, "
                  f"洗盘={behavior_counts['wash']}, 护盘={behavior_counts['support']}, 砸盘={behavior_counts['smash']}")
            print(f"  总行为数量: {len(behavior_analysis)}")
            
            # 获取当前价格和涨跌幅
            if len(engine.data) > 0:
                latest_data = engine.data.iloc[-1]
                current_price = latest_data.get('lastPrice', 0)
                
                # 计算涨跌幅（需要前一日收盘价，这里简化处理）
                change_pct = 0  # TODO: 计算实际涨跌幅
                volume = latest_data.get('volume', 0)
            else:
                current_price = 0
                change_pct = 0
                volume = 0
            
            return {
                'price': current_price,
                'change_pct': change_pct,
                'volume': volume,
                'behavior_counts': behavior_counts
            }
            
        except Exception as e:
            print(f"分析股票 {stock_code} 时出错: {str(e)}")
            return None
    
    def analyze_main_force_behavior(self, data):
        """分析主力行为（吸筹/出货）"""
        main_force_actions = []
        
        # 获取关键数据，排除尾盘集合竞价阶段
        behavior_data = []
        for idx, row in data.iterrows():
            # 检查是否为尾盘集合竞价阶段
            time_str = str(idx)
            if len(time_str) >= 10:
                hour = int(time_str[8:10])
                minute = int(time_str[10:12])
                # 排除尾盘集合竞价阶段（14:57-15:00）和15:00之后的数据
                if (hour == 14 and minute >= 57) or hour >= 15:
                    continue
            
            # 获取买卖盘口数据
            bid_price = row.get('bidPrice', [0])[0] if isinstance(row.get('bidPrice', []), list) and len(row.get('bidPrice', [])) > 0 else 0
            ask_price = row.get('askPrice', [0])[0] if isinstance(row.get('askPrice', []), list) and len(row.get('askPrice', [])) > 0 else 0
            bid_vol = row.get('bidVol', [0])[0] if isinstance(row.get('bidVol', []), list) and len(row.get('bidVol', [])) > 0 else 0
            ask_vol = row.get('askVol', [0])[0] if isinstance(row.get('askVol', []), list) and len(row.get('askVol', [])) > 0 else 0
            volume = row.get('volume', 0)
            last_price = row.get('lastPrice', 0)
            
            behavior_data.append({
                'time': pd.to_datetime(idx),
                'bid_price': bid_price,
                'ask_price': ask_price,
                'bid_vol': bid_vol,
                'ask_vol': ask_vol,
                'volume': volume,
                'last_price': last_price,
                'is_limit_up': row.get('is_limit_up', False)
            })
        
        if len(behavior_data) < 2:
            return main_force_actions
        
        # 调试信息：打印数据点数量
        print(f"  分析数据点数量: {len(behavior_data)}")
        
        # 分析主力行为
        for i in range(1, len(behavior_data)):
            prev_data = behavior_data[i-1]
            curr_data = behavior_data[i]
            
            # 计算关键指标
            volume_change = curr_data['volume'] - prev_data['volume']
            price_change = curr_data['last_price'] - prev_data['last_price']
            bid_vol_change = curr_data['bid_vol'] - prev_data['bid_vol']
            ask_vol_change = curr_data['ask_vol'] - prev_data['ask_vol']
            
            # 计算买卖压力比
            bid_pressure = curr_data['bid_vol'] if curr_data['bid_vol'] > 0 else 1
            ask_pressure = curr_data['ask_vol'] if curr_data['ask_vol'] > 0 else 1
            pressure_ratio = bid_pressure / ask_pressure
            
            # 判断主力行为
            action_type = ""
            intensity = ""
            description = ""
            
            # 吸筹特征判断（排除涨停板情况）
            accumulation_volume_threshold = self.config_params.get('accumulation_volume_threshold', 10000)
            accumulation_price_change = self.config_params.get('accumulation_price_change', 0)
            accumulation_bid_vol_change = self.config_params.get('accumulation_bid_vol_change', 50000)
            accumulation_pressure_ratio = self.config_params.get('accumulation_pressure_ratio', 1.5)
            accumulation_strong_threshold = self.config_params.get('accumulation_strong_threshold', 50000)
            accumulation_medium_threshold = self.config_params.get('accumulation_medium_threshold', 20000)
            
            if (not curr_data['is_limit_up'] and  # 排除涨停板情况
                volume_change > accumulation_volume_threshold and  # 成交量放大
                price_change > accumulation_price_change and      # 价格上涨
                bid_vol_change > accumulation_bid_vol_change and  # 买盘增加
                pressure_ratio > accumulation_pressure_ratio):     # 买盘压力大于卖盘
                
                action_type = "吸筹"
                if volume_change > accumulation_strong_threshold:
                    intensity = "强烈"
                elif volume_change > accumulation_medium_threshold:
                    intensity = "中等"
                else:
                    intensity = "轻微"
                    
                description = f"成交量放大{volume_change}手，价格上涨{price_change:.3f}元，买盘增加{bid_vol_change}手"
            
            # 出货特征判断
            elif (volume_change > self.config_params.get('distribution_volume_threshold', 10000) and  # 成交量放大
                  price_change < self.config_params.get('distribution_price_change', 0) and      # 价格下跌
                  ask_vol_change > self.config_params.get('distribution_ask_vol_change', 50000) and  # 卖盘增加
                  pressure_ratio < self.config_params.get('distribution_pressure_ratio', 0.7)):     # 卖盘压力大于买盘
                
                action_type = "出货"
                if volume_change > self.config_params.get('distribution_strong_threshold', 50000):
                    intensity = "强烈"
                elif volume_change > self.config_params.get('distribution_medium_threshold', 20000):
                    intensity = "中等"
                else:
                    intensity = "轻微"
                    
                description = f"成交量放大{volume_change}手，价格下跌{abs(price_change):.3f}元，卖盘增加{ask_vol_change}手"
            
            # 扫货特征判断（涨停板封板前）
            elif (not curr_data['is_limit_up'] and  # 还未涨停
                  volume_change > self.config_params.get('wash_volume_threshold', 15000) and  # 成交量较大
                  price_change > 0.001 and  # 价格明显上涨（超过0.1%）
                  bid_vol_change > 15000 and  # 买盘大幅增加
                  pressure_ratio > 1.05):  # 买盘压力明显大于卖盘
                
                action_type = "扫货"
                if volume_change > self.config_params.get('wash_strong_threshold', 30000):
                    intensity = "强烈"
                elif volume_change > self.config_params.get('wash_medium_threshold', 20000):
                    intensity = "中等"
                else:
                    intensity = "轻微"
                    
                description = f"封板前扫货，成交量放大{volume_change}手，价格上涨{price_change:.3f}元，买盘增加{bid_vol_change}手"
            
            # 洗盘特征判断（价格变化不大的震荡）
            elif (volume_change > self.config_params.get('wash_volume_threshold', 15000) and  # 成交量较大
                  abs(price_change) < self.config_params.get('wash_price_change_threshold', 0.01) and  # 价格变化不大
                  abs(bid_vol_change - ask_vol_change) < self.config_params.get('wash_vol_change_diff', 10000)):  # 买卖盘变化相近
                
                # 判断是否为扫货（洗盘的特殊情况：接近涨停板时的洗盘）
                if (not curr_data['is_limit_up'] and  # 还未涨停
                    price_change > 0 and  # 价格上涨
                    bid_vol_change > ask_vol_change):  # 买盘增加大于卖盘增加
                    action_type = "扫货"
                    description = f"封板前扫货，成交量放大{volume_change}手，价格上涨{price_change:.3f}元，买盘增加{bid_vol_change}手"
                else:
                    action_type = "洗盘"
                    description = f"成交量放大{volume_change}手，价格变化{price_change:.3f}元，买卖盘变化相近"
                
                if volume_change > self.config_params.get('wash_strong_threshold', 30000):
                    intensity = "强烈"
                elif volume_change > self.config_params.get('wash_medium_threshold', 20000):
                    intensity = "中等"
                else:
                    intensity = "轻微"
            
            # 护盘特征判断（涨停板时）
            elif (curr_data['is_limit_up'] and
                  bid_vol_change > self.config_params.get('support_bid_vol_change', 100000) and  # 买盘大幅增加
                  volume_change < self.config_params.get('support_volume_threshold', 5000)):      # 成交量不大
                
                action_type = "护盘"
                intensity = "强烈"
                description = f"涨停板护盘，买盘增加{bid_vol_change}手，成交量{volume_change}手"
            
            # 砸盘特征判断
            elif (volume_change > self.config_params.get('smash_volume_threshold', 20000) and  # 成交量很大
                  price_change < self.config_params.get('smash_price_change_threshold', -0.02) and   # 价格大幅下跌
                  ask_vol_change > self.config_params.get('smash_ask_vol_change', 100000)):  # 卖盘大幅增加
                
                action_type = "砸盘"
                intensity = "强烈"
                description = f"成交量放大{volume_change}手，价格大跌{abs(price_change):.3f}元，卖盘增加{ask_vol_change}手"
            
            if action_type:
                main_force_actions.append({
                    'time': curr_data['time'].strftime('%H:%M:%S'),
                    'type': action_type,
                    'intensity': intensity,
                    'volume': volume_change,
                    'price_change': price_change,
                    'description': description
                })
        
        return main_force_actions
    
    def add_stock(self):
        """添加股票到备选股票池"""
        # 添加新行
        row_index = self.candidate_table.rowCount()
        self.candidate_table.insertRow(row_index)
        
        # 创建可编辑的表格项
        code_item = QTableWidgetItem("")
        name_item = QTableWidgetItem("")
        market_item = QTableWidgetItem("")
        date_item = QTableWidgetItem("")
        
        # 设置表格项
        self.candidate_table.setItem(row_index, 0, code_item)
        self.candidate_table.setItem(row_index, 1, name_item)
        self.candidate_table.setItem(row_index, 2, market_item)
        self.candidate_table.setItem(row_index, 3, date_item)
        
        # 选中新添加的行并开始编辑
        self.candidate_table.selectRow(row_index)
        self.candidate_table.editItem(code_item)
        
        # 更新计数
        self.update_candidate_count()
        
        # 保存备选股票池
        self.save_candidate_pool()
    
    def delete_selected_stocks(self):
        """删除选中的股票"""
        selected_rows = set()
        for item in self.candidate_table.selectedItems():
            selected_rows.add(item.row())
        
        if not selected_rows:
            QMessageBox.information(self, "提示", "请先选择要删除的股票")
            return
        
        # 从后往前删除，避免索引变化
        for row in sorted(selected_rows, reverse=True):
            self.candidate_table.removeRow(row)
        
        # 更新计数
        self.update_candidate_count()
        
        # 保存备选股票池
        self.save_candidate_pool()
    
    def clear_all_stocks(self):
        """一键清空备选股票池"""
        if self.candidate_table.rowCount() == 0:
            QMessageBox.information(self, "提示", "备选股票池已经是空的")
            return
        
        # 确认对话框
        reply = QMessageBox.question(self, "确认清空", 
                                   "确定要清空所有备选股票吗？此操作不可撤销。",
                                   QMessageBox.Yes | QMessageBox.No,
                                   QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            # 清空表格
            self.candidate_table.setRowCount(0)
            
            # 更新计数
            self.update_candidate_count()
            
            # 保存备选股票池
            self.save_candidate_pool()
            
            QMessageBox.information(self, "提示", "备选股票池已清空")
    
    def on_candidate_table_changed(self, item):
        """当备选股票池表格内容改变时"""
        row = item.row()
        col = item.column()
        
        if col == 0:  # 股票代码列
            # 当股票代码改变时，自动填充其他信息
            stock_code = item.text().strip()
            if stock_code:
                # 尝试从all_a_stocks.csv中获取股票信息
                stock_info = self.get_stock_info_from_csv(stock_code)
                if stock_info:
                    # 自动填充股票名称、市场和上市日期
                    self.candidate_table.setItem(row, 1, QTableWidgetItem(stock_info['name']))
                    self.candidate_table.setItem(row, 2, QTableWidgetItem(stock_info['market']))
                    self.candidate_table.setItem(row, 3, QTableWidgetItem(stock_info['list_date']))
        
        # 更新计数
        self.update_candidate_count()
        
        # 保存备选股票池
        self.save_candidate_pool()
    
    def update_candidate_count(self):
        """更新备选股票池计数"""
        count = self.candidate_table.rowCount()
        self.candidate_count_label.setText(f"共 {count} 只股票")
    
    def get_stock_info_from_csv(self, stock_code):
        """从CSV文件中获取股票信息"""
        try:
            import pandas as pd
            import os
            
            # 补齐股票代码为6位
            stock_code = str(stock_code).zfill(6)
            
            # 读取股票列表
            stocks_file = os.path.join('data', 'all_a_stocks.csv')
            if not os.path.exists(stocks_file):
                return None
            
            # 读取CSV文件
            stocks_df = pd.read_csv(stocks_file, encoding='utf-8')
            
            # 查找匹配的股票
            for _, row in stocks_df.iterrows():
                if str(row['证券代码']).zfill(6) == stock_code:
                    return {
                        'name': row['证券简称'],
                        'market': self.get_stock_market(stock_code),
                        'list_date': row['上市日期']
                    }
            
            return None
            
        except Exception as e:
            print(f"获取股票信息时出错: {str(e)}")
            return None
    
    def get_candidate_stocks(self):
        """获取备选股票池中的所有股票"""
        stocks = []
        for row in range(self.candidate_table.rowCount()):
            code_item = self.candidate_table.item(row, 0)
            name_item = self.candidate_table.item(row, 1)
            market_item = self.candidate_table.item(row, 2)
            date_item = self.candidate_table.item(row, 3)
            
            if code_item and code_item.text().strip():
                stocks.append({
                    'code': code_item.text().strip(),
                    'name': name_item.text().strip() if name_item else "",
                    'market': market_item.text().strip() if market_item else "",
                    'list_date': date_item.text().strip() if date_item else ""
                })
        
        return stocks
    
    def import_from_file(self):
        """从文件导入股票代码"""
        try:
            # 打开文件选择对话框
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "选择股票代码文件",
                "",
                "文本文件 (*.txt);;所有文件 (*)"
            )
            
            if not file_path:
                return  # 用户取消选择
            
            # 读取文件内容
            imported_codes = set()  # 使用set来去重
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line_num, line in enumerate(f, 1):
                        line = line.strip()
                        if line:  # 跳过空行
                            # 清理股票代码（去除空格、制表符等）
                            stock_code = line.strip()
                            if stock_code:
                                imported_codes.add(stock_code)
            except UnicodeDecodeError:
                # 如果UTF-8失败，尝试GBK编码
                with open(file_path, 'r', encoding='gbk') as f:
                    for line_num, line in enumerate(f, 1):
                        line = line.strip()
                        if line:  # 跳过空行
                            stock_code = line.strip()
                            if stock_code:
                                imported_codes.add(stock_code)
            
            if not imported_codes:
                QMessageBox.warning(self, "警告", "文件中没有找到有效的股票代码")
                return
            
            # 获取当前表格中已有的股票代码（用于去重）
            existing_codes = set()
            for row in range(self.candidate_table.rowCount()):
                code_item = self.candidate_table.item(row, 0)
                if code_item and code_item.text().strip():
                    existing_codes.add(code_item.text().strip())
            
            # 过滤出新的股票代码
            new_codes = imported_codes - existing_codes
            
            if not new_codes:
                QMessageBox.information(self, "提示", "所有股票代码都已存在于备选股票池中")
                return
            
            # 添加新的股票代码到表格
            added_count = 0
            for stock_code in sorted(new_codes):
                # 添加新行
                row_index = self.candidate_table.rowCount()
                self.candidate_table.insertRow(row_index)
                
                # 创建表格项
                code_item = QTableWidgetItem(stock_code)
                name_item = QTableWidgetItem("")
                market_item = QTableWidgetItem("")
                date_item = QTableWidgetItem("")
                
                # 设置表格项
                self.candidate_table.setItem(row_index, 0, code_item)
                self.candidate_table.setItem(row_index, 1, name_item)
                self.candidate_table.setItem(row_index, 2, market_item)
                self.candidate_table.setItem(row_index, 3, date_item)
                
                # 尝试自动填充股票信息
                stock_info = self.get_stock_info_from_csv(stock_code)
                if stock_info:
                    self.candidate_table.setItem(row_index, 1, QTableWidgetItem(stock_info['name']))
                    self.candidate_table.setItem(row_index, 2, QTableWidgetItem(stock_info['market']))
                    self.candidate_table.setItem(row_index, 3, QTableWidgetItem(stock_info['list_date']))
                
                added_count += 1
            
            # 更新计数
            self.update_candidate_count()
            
            # 保存备选股票池
            self.save_candidate_pool()
            
            # 显示导入结果
            QMessageBox.information(
                self, 
                "导入完成", 
                f"成功导入 {added_count} 只股票代码\n"
                f"跳过 {len(imported_codes) - len(new_codes)} 只重复的股票代码"
            )
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导入文件时出错: {str(e)}")
    
    def load_candidate_pool(self):
        """从文件加载备选股票池"""
        try:
            if not os.path.exists(self.candidate_pool_file):
                return  # 文件不存在，使用空的备选股票池
            
            import pandas as pd
            
            # 读取CSV文件，将股票代码列作为字符串处理
            df = pd.read_csv(self.candidate_pool_file, encoding='utf-8', dtype={'股票代码': str})
            
            # 清空当前表格
            self.candidate_table.setRowCount(0)
            
            # 添加股票到表格
            for _, row in df.iterrows():
                row_index = self.candidate_table.rowCount()
                self.candidate_table.insertRow(row_index)
                
                # 确保股票代码是6位字符串格式
                stock_code = str(row['股票代码']).zfill(6)
                
                # 创建表格项
                code_item = QTableWidgetItem(stock_code)
                name_item = QTableWidgetItem(str(row['股票名称']))
                market_item = QTableWidgetItem(str(row['所属市场']))
                date_item = QTableWidgetItem(str(row['上市日期']))
                
                # 设置表格项
                self.candidate_table.setItem(row_index, 0, code_item)
                self.candidate_table.setItem(row_index, 1, name_item)
                self.candidate_table.setItem(row_index, 2, market_item)
                self.candidate_table.setItem(row_index, 3, date_item)
            
            # 更新计数
            self.update_candidate_count()
            
        except Exception as e:
            print(f"加载备选股票池失败: {str(e)}")
    
    def save_candidate_pool(self):
        """保存备选股票池到文件"""
        try:
            import pandas as pd
            
            # 获取当前表格中的所有股票
            stocks_data = []
            for row in range(self.candidate_table.rowCount()):
                code_item = self.candidate_table.item(row, 0)
                name_item = self.candidate_table.item(row, 1)
                market_item = self.candidate_table.item(row, 2)
                date_item = self.candidate_table.item(row, 3)
                
                if code_item and code_item.text().strip():
                    # 确保股票代码是6位字符串格式
                    stock_code = code_item.text().strip().zfill(6)
                    
                    stocks_data.append({
                        '股票代码': stock_code,
                        '股票名称': name_item.text().strip() if name_item else "",
                        '所属市场': market_item.text().strip() if market_item else "",
                        '上市日期': date_item.text().strip() if date_item else ""
                    })
            
            # 创建DataFrame并保存到CSV文件
            df = pd.DataFrame(stocks_data)
            
            # 确保data目录存在
            os.makedirs(os.path.dirname(self.candidate_pool_file), exist_ok=True)
            
            # 保存到文件，确保股票代码列作为字符串保存
            df.to_csv(self.candidate_pool_file, index=False, encoding='utf-8')
            
        except Exception as e:
            print(f"保存备选股票池失败: {str(e)}")
            QMessageBox.warning(self, "警告", f"保存备选股票池失败: {str(e)}")