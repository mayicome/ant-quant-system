from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                              QSpinBox, QDoubleSpinBox, QDialogButtonBox, 
                              QComboBox, QLineEdit, QMessageBox, QCheckBox,
                              QTimeEdit, QPushButton, QInputDialog, QTextEdit,
                              QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
                              QDateEdit, QApplication, QTabWidget, QScrollArea, QWidget,
                              QFrame, QFileDialog, QGroupBox, QFormLayout)
from PyQt5.QtCore import Qt, QTime, QThread, pyqtSignal, QDate, QTimer
from PyQt5.QtGui import QFont, QColor
import logging
from datetime import datetime, time, date, timedelta
import time as time_module
from utils.trading_day import is_tradeday
import pandas as pd
import configparser
import os


class CreateUniversalStrategyDialog(QDialog):
    """新建规则任务对话框（兼容旧类名）"""
    
    def __init__(self, parent=None, qmt_adapter=None):
        super().__init__(parent)
        self.qmt_adapter = qmt_adapter
        self.setWindowTitle("新建规则任务")
        self.setModal(True)
        self.resize(400, 300)
        
        # 设置字体
        font = QFont("Microsoft YaHei", 9)
        self.setFont(font)
        
        # 创建布局
        layout = QVBoxLayout(self)
        
        # 创建表单布局
        form_layout = QFormLayout()
        
        # 股票代码输入
        self.stock_code = QLineEdit()
        self.stock_code.setPlaceholderText("请输入6位股票代码，如：000001")
        self.stock_code.setMaxLength(6)
        self.stock_code.textChanged.connect(self.on_stock_code_changed)
        form_layout.addRow("股票代码:", self.stock_code)
        
        # 股票名称显示
        self.stock_name_label = QLabel("")
        self.stock_name_label.setStyleSheet("color: #666; font-style: italic;")
        form_layout.addRow("股票名称:", self.stock_name_label)
        
        # 基准价格输入
        self.base_price = QDoubleSpinBox()
        self.base_price.setRange(0.01, 9999.99)
        self.base_price.setDecimals(2)
        self.base_price.setSuffix(" 元")
        self.base_price.setValue(10.00)
        self.base_price.valueChanged.connect(self.update_threshold_prices)
        form_layout.addRow("基准价格:", self.base_price)
        
        # 获取当前价格按钮
        self.get_price_btn = QPushButton("获取当前价格")
        self.get_price_btn.setEnabled(False)
        self.get_price_btn.clicked.connect(self.get_current_price)
        form_layout.addRow("", self.get_price_btn)
        
        # 上涨阈值输入
        up_threshold_layout = QHBoxLayout()
        self.up_threshold = QDoubleSpinBox()
        self.up_threshold.setRange(0.0, 50.0)
        self.up_threshold.setDecimals(2)
        self.up_threshold.setSingleStep(0.01)
        self.up_threshold.setSuffix(" %")
        self.up_threshold.setValue(3.0)
        self.up_threshold.valueChanged.connect(self.update_threshold_prices)
        up_threshold_layout.addWidget(self.up_threshold)
        
        # 上涨阈值对应的价格显示
        self.up_price_label = QLabel("价格: --")
        self.up_price_label.setStyleSheet("color: red; font-weight: bold;")
        up_threshold_layout.addWidget(self.up_price_label)
        
        form_layout.addRow("上涨阈值:", up_threshold_layout)
        
        # 下跌阈值输入
        down_threshold_layout = QHBoxLayout()
        self.down_threshold = QDoubleSpinBox()
        self.down_threshold.setRange(0.0, 50.0)
        self.down_threshold.setDecimals(2)
        self.down_threshold.setSingleStep(0.01)
        self.down_threshold.setSuffix(" %")
        self.down_threshold.setValue(3.5)
        self.down_threshold.valueChanged.connect(self.update_threshold_prices)
        down_threshold_layout.addWidget(self.down_threshold)
        
        # 下跌阈值对应的价格显示
        self.down_price_label = QLabel("价格: --")
        self.down_price_label.setStyleSheet("color: green; font-weight: bold;")
        down_threshold_layout.addWidget(self.down_price_label)
        
        form_layout.addRow("下跌阈值:", down_threshold_layout)
        
        # 上涨操作选择
        self.up_operation = QComboBox()
        self.up_operation.addItems(["卖出", "买入", "不动"])
        self.up_operation.setCurrentText("卖出")
        form_layout.addRow("上涨操作:", self.up_operation)
        
        # 下跌操作选择
        self.down_operation = QComboBox()
        self.down_operation.addItems(["买入", "卖出", "不动"])
        self.down_operation.setCurrentText("买入")
        form_layout.addRow("下跌操作:", self.down_operation)
        
        # 每笔操作股数
        volume_layout = QHBoxLayout()
        self.trade_volume = QSpinBox()
        self.trade_volume.setRange(100, 999900)
        self.trade_volume.setValue(1000)
        self.trade_volume.setSingleStep(100)
        self.trade_volume.setSuffix(" 股")
        self.trade_volume.setToolTip("每次操作的股数，最小100股，必须是100的倍数")
        volume_layout.addWidget(self.trade_volume)
        
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
        form_layout.addRow("每笔操作股数:", volume_layout)
        
        # 循环次数
        self.cycle_times = QSpinBox()
        self.cycle_times.setRange(0, 999)
        self.cycle_times.setValue(0)
        self.cycle_times.setSuffix(" 次")
        self.cycle_times.setToolTip("0表示无限制")
        form_layout.addRow("循环次数:", self.cycle_times)
        
        # 智能卖出配置
        self.smart_sell_group = QGroupBox("上涨时智能卖出配置")
        smart_sell_layout = QFormLayout()
        self.smart_sell_group.setLayout(smart_sell_layout)
        
        # 启用智能卖出
        self.enable_smart_sell = QCheckBox("启用智能卖出")
        self.enable_smart_sell.setChecked(True)
        self.enable_smart_sell.setToolTip("上涨操作为卖出时，等待更高价格或下跌时再卖出")
        smart_sell_layout.addRow(self.enable_smart_sell)
        
        # 下落阈值
        self.sell_drop_threshold = QDoubleSpinBox()
        self.sell_drop_threshold.setRange(0.001, 5.0)
        self.sell_drop_threshold.setValue(0.2)
        self.sell_drop_threshold.setSingleStep(0.001)
        self.sell_drop_threshold.setSuffix("%")
        self.sell_drop_threshold.setDecimals(3)
        self.sell_drop_threshold.setToolTip("从最高价格下落多少百分比时执行卖出")
        smart_sell_layout.addRow("下落阈值:", self.sell_drop_threshold)
        
        # 超时时间
        self.sell_timeout = QSpinBox()
        self.sell_timeout.setRange(5, 99999)
        self.sell_timeout.setValue(14400)
        self.sell_timeout.setSuffix(" 秒")
        self.sell_timeout.setToolTip("等待卖出的最大时间，超时后以当前价格卖出")
        smart_sell_layout.addRow("超时时间:", self.sell_timeout)
        
        form_layout.addRow(self.smart_sell_group)
        
        # 智能买入配置
        self.smart_buy_group = QGroupBox("下跌时智能买入配置")
        smart_buy_layout = QFormLayout()
        self.smart_buy_group.setLayout(smart_buy_layout)
        
        # 启用智能买入
        self.enable_smart_buy = QCheckBox("启用智能买入")
        self.enable_smart_buy.setChecked(True)
        self.enable_smart_buy.setToolTip("下跌操作为买入时，等待更低价格或反弹时再买入")
        smart_buy_layout.addRow(self.enable_smart_buy)
        
        # 反弹阈值
        self.buy_rebound_threshold = QDoubleSpinBox()
        self.buy_rebound_threshold.setRange(0.001, 5.0)
        self.buy_rebound_threshold.setValue(0.2)
        self.buy_rebound_threshold.setSingleStep(0.001)
        self.buy_rebound_threshold.setSuffix("%")
        self.buy_rebound_threshold.setDecimals(3)
        self.buy_rebound_threshold.setToolTip("从最低价格反弹多少百分比时执行买入")
        smart_buy_layout.addRow("反弹阈值:", self.buy_rebound_threshold)
        
        # 超时时间
        self.buy_timeout = QSpinBox()
        self.buy_timeout.setRange(5, 99999)
        self.buy_timeout.setValue(14400)
        self.buy_timeout.setSuffix(" 秒")
        self.buy_timeout.setToolTip("等待买入的最大时间，超时后以当前价格买入")
        smart_buy_layout.addRow("超时时间:", self.buy_timeout)
        
        form_layout.addRow(self.smart_buy_group)
        layout.addLayout(form_layout)
        
        # 连接操作变化信号，控制智能配置的显示
        self.up_operation.currentTextChanged.connect(self.update_smart_config_visibility)
        self.down_operation.currentTextChanged.connect(self.update_smart_config_visibility)
        self.update_smart_config_visibility()  # 初始化显示状态
        
        # 添加说明文本
        info_label = QLabel("说明：此功能用于为指定股票创建规则任务，请注意正确选择上涨和下跌的操作方向。")
        info_label.setStyleSheet("color: #666; font-size: 8pt; padding: 10px;")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        # 按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
        # 存储当前价格
        self.current_price = None
    
    def update_smart_config_visibility(self):
        """根据操作类型控制智能配置的显示"""
        up_operation = self.up_operation.currentText()
        down_operation = self.down_operation.currentText()
        
        # 只有当上涨操作为"卖出"时才显示智能卖出配置
        self.smart_sell_group.setVisible(up_operation == '卖出')
        
        # 只有当下跌操作为"买入"时才显示智能买入配置
        self.smart_buy_group.setVisible(down_operation == '买入')
        
    def on_stock_code_changed(self, text):
        """股票代码改变时的处理"""
        if len(text) == 6:
            # 启用获取价格按钮
            self.get_price_btn.setEnabled(True)
            
            # 尝试获取股票名称
            try:
                from utils.stock_info_manager import get_stock_info_manager
                stock_manager = get_stock_info_manager()
                stock_name = stock_manager.get_stock_name(text)
                if stock_name:
                    self.stock_name_label.setText(stock_name)
                else:
                    self.stock_name_label.setText("未知股票")
            except Exception as e:
                self.stock_name_label.setText("获取股票名称失败")
        else:
            self.get_price_btn.setEnabled(False)
            self.stock_name_label.setText("")
    
    def _get_full_stock_code(self, stock_code):
        """补齐股票代码后缀"""
        if len(stock_code) == 6:
            if stock_code.startswith(('0', '1', '3')):
                return f"{stock_code}.SZ"
            elif stock_code.startswith(('5', '6')):
                return f"{stock_code}.SH"
            elif stock_code.startswith(('4', '8', '920')):
                return f"{stock_code}.BJ"
        return stock_code
    
    def auto_get_current_price(self, stock_code):
        """自动获取当前价格"""
        try:
            # 检查QMT适配器是否可用
            if not self.qmt_adapter:
                return
            
            # 补齐股票代码后缀
            full_stock_code = self._get_full_stock_code(stock_code)
            if not full_stock_code:
                return
            
            # 使用网关订阅系统添加股票到订阅列表
            if self.qmt_adapter:
                self.qmt_adapter.ensure_subscribed(full_stock_code)
            
            # 等待一段时间获取数据
            import time as time_module
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
                    self.current_price = cached_latest_price
                    self.base_price.setValue(self.current_price)
                    # 更新阈值价格显示
                    self.update_threshold_prices()
                else:
                    print(f"无法获取股票 {stock_code} 的价格数据")
            else:
                print("任务管理器或价格缓存不可用")
                
        except Exception as e:
            print(f"自动获取价格失败: {e}")
    
    def get_current_price(self):
        """手动获取当前价格"""
        stock_code = self.stock_code.text().strip()
        if len(stock_code) == 6:
            self.auto_get_current_price(stock_code)
    
    def update_threshold_prices(self):
        """更新阈值对应的价格显示"""
        try:
            base_price = self.base_price.value()
            if base_price > 0:
                # 计算上涨阈值对应的价格
                up_threshold = self.up_threshold.value()
                if up_threshold == 0.0:
                    up_price = base_price  # 阈值为0时，使用基准价
                    self.up_price_label.setText(f"价格: {up_price:.3f} (基准价)")
                else:
                    up_price = base_price * (1 + up_threshold / 100)
                    self.up_price_label.setText(f"价格: {up_price:.3f}")
                
                # 计算下跌阈值对应的价格
                down_threshold = self.down_threshold.value()
                if down_threshold == 0.0:
                    down_price = base_price  # 阈值为0时，使用基准价
                    self.down_price_label.setText(f"价格: {down_price:.3f} (基准价)")
                else:
                    down_price = base_price * (1 - down_threshold / 100)
                    self.down_price_label.setText(f"价格: {down_price:.3f}")
            else:
                self.up_price_label.setText("价格: --")
                self.down_price_label.setText("价格: --")
        except Exception as e:
            self.up_price_label.setText("价格: --")
            self.down_price_label.setText("价格: --")
    
    def get_values(self):
        """获取对话框的值"""
        return {
            'stock_code': self.stock_code.text().strip(),
            'stock_name': self.stock_name_label.text(),
            'base_price': self.base_price.value(),
            'up_threshold': self.up_threshold.value(),
            'down_threshold': self.down_threshold.value(),
            'up_operation': self.up_operation.currentText(),
            'down_operation': self.down_operation.currentText(),
            'trade_volume': self.trade_volume.value(),
            'cycle_times': self.cycle_times.value(),
            'enable_smart_sell': self.enable_smart_sell.isChecked(),
            'sell_drop_threshold': self.sell_drop_threshold.value() / 100,  # 转换为小数
            'sell_timeout': self.sell_timeout.value(),
            'enable_smart_buy': self.enable_smart_buy.isChecked(),
            'buy_rebound_threshold': self.buy_rebound_threshold.value() / 100,  # 转换为小数
            'buy_timeout': self.buy_timeout.value()
        }
    
    def accept(self):
        """确认创建"""
        values = self.get_values()
        
        # 验证输入
        if len(values['stock_code']) != 6:
            QMessageBox.warning(self, "警告", "请输入完整的6位股票代码")
            return
        
        if values['base_price'] <= 0:
            QMessageBox.warning(self, "警告", "基准价格必须大于0")
            return
        
        if values['up_threshold'] < 0 or values['down_threshold'] < 0:
            QMessageBox.warning(self, "警告", "阈值不能为负数")
            return
        
        # 确认创建
        reply = QMessageBox.question(
            self, 
            "确认创建", 
            f"确认创建规则任务？\n\n"
            f"股票代码: {values['stock_code']}\n"
            f"股票名称: {values['stock_name']}\n"
            f"基准价格: {values['base_price']:.2f} 元\n"
            f"上涨阈值: {values['up_threshold']:.2f}%\n"
            f"下跌阈值: {values['down_threshold']:.2f}%\n"
            f"上涨操作: {values['up_operation']}\n"
            f"下跌操作: {values['down_operation']}\n"
            f"每笔操作股数: {values['trade_volume']} 股\n"
            f"循环次数: {values['cycle_times']} 次",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            super().accept()
    
    def quick_select_volume(self, ratio):
        """快速选择每笔操作股数"""
        try:
            # 检查QMT适配器是否可用
            if not self.qmt_adapter:
                QMessageBox.warning(self, "警告", "无法获取账户信息")
                return
            
            # 获取可用资金
            available_cash = self._get_available_cash()
            if available_cash is None:
                QMessageBox.warning(self, "警告", "无法获取账户资金信息")
                return
            
            base_price = self.base_price.value()
            
            if base_price <= 0:
                QMessageBox.warning(self, "警告", "请先设置基准价格")
                return
            
            # 计算可买入数量（100股为单位）
            # 考虑手续费等成本，假设总费率为0.13%
            fee_rate = 0.0013
            
            # 计算实际可买入数量
            max_amount = available_cash * ratio
            max_shares = int(max_amount / (base_price * (1 + fee_rate)))
            
            # 调整为100股的倍数
            max_shares = (max_shares // 100) * 100
            
            # 确保不超过最大限制
            max_shares = min(max_shares, 999900)
            
            # 确保不小于最小值
            max_shares = max(max_shares, 100)
            
            self.trade_volume.setValue(max_shares)
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"计算股数时发生错误: {str(e)}")
    
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
