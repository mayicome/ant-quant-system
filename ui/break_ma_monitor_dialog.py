#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
跌破均线买入信号监控程序
监控股票池中的股票，当价格首次跌破或等于MA5、MA10、MA20时发出买入信号
"""

import sys
import os
import pandas as pd
from datetime import datetime, date, timedelta, timezone, time as datetime_time
from typing import List, Dict, Optional, Tuple, Set
import time
import logging
from collections import defaultdict

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QTableWidget, QTableWidgetItem, QLabel, QMessageBox,
                             QFileDialog, QHeaderView, QLineEdit, QApplication)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QObject, QTimer
from PyQt5.QtGui import QFont
import winsound  # Windows系统声音

# 添加项目根目录到路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.trading_day import REFERENCE_SWITCH_TIME

try:
    import xtquant.xtdata as xtdata
    from utils.trading_day import is_tradeday
    from utils.stock_info_manager import get_stock_name
except ImportError as e:
    print(f"导入模块失败: {e}")

# 配置日志
_LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(_LOGS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(_LOGS_DIR, 'break_ma_monitor.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class BreakAlertDialog(QDialog):
    """跌破提醒弹窗"""
    
    _instance_count = 0  # 类变量，用于记录弹窗实例数量，用于错开位置
    
    def __init__(self, stock_code: str, stock_name: str, ma_type: str, 
                 last_price: float, break_time: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("跌破均线提醒")
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.Dialog | Qt.MSWindowsFixedSizeDialogHint)
        
        # 设置弹窗大小
        self.setFixedSize(400, 200)
        
        # 错开弹窗位置，避免重叠
        BreakAlertDialog._instance_count += 1
        offset = (BreakAlertDialog._instance_count - 1) * 50  # 每个弹窗偏移50像素
        if parent:
            parent_pos = parent.pos()
            self.move(parent_pos.x() + 50 + offset, parent_pos.y() + 50 + offset)
        else:
            # 如果没有父窗口，使用屏幕中心
            from PyQt5.QtWidgets import QDesktopWidget
            desktop = QDesktopWidget()
            screen_center = desktop.availableGeometry().center()
            self.move(screen_center.x() - 200 + offset, screen_center.y() - 100 + offset)
        
        # 布局
        layout = QVBoxLayout()
        
        # 标题
        title_label = QLabel("⚠️ 跌破均线提醒")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)
        
        # 股票信息
        info_text = f"股票代码: {stock_code}\n股票名称: {stock_name}\n跌破均线: {ma_type}\n最新价: {last_price:.2f}\n时间: {break_time}"
        info_label = QLabel(info_text)
        info_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(info_label)
        
        # 关闭按钮
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)
        
        self.setLayout(layout)
        
        # 10秒后自动关闭
        self.auto_close_timer = QTimer()
        self.auto_close_timer.setSingleShot(True)
        self.auto_close_timer.timeout.connect(self.close)
        self.auto_close_timer.start(10000)  # 10秒 = 10000毫秒
    
    def closeEvent(self, event):
        """关闭时停止定时器"""
        if hasattr(self, 'auto_close_timer'):
            self.auto_close_timer.stop()
        # 减少实例计数
        BreakAlertDialog._instance_count = max(0, BreakAlertDialog._instance_count - 1)
        event.accept()


class BreakMAMonitorDialog(QDialog):
    """跌破均线监控对话框"""
    
    def __init__(self, qmt_adapter=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("跌破均线买入信号监控")
        self.setMinimumSize(1000, 700)
        self.resize(1200, 800)
        
        # 去掉右上角的问号（帮助按钮）
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        
        self.qmt_adapter = qmt_adapter
        self.stock_pool = set()  # 股票池，存储(股票代码, 股票名称)元组
        self.stock_ma_data = {}  # 存储每个股票的MA数据: {stock_code: {'MA5': float, 'MA10': float, 'MA20': float, 'MA5_intersection': float, 'MA10_intersection': float, 'MA20_intersection': float, 'daily_close': float}}
        self.break_records = []  # 存储跌破记录
        self.break_flags = defaultdict(set)  # 记录每个股票已经跌破的均线: {stock_code: {MA5, MA10, MA20}}
        self.is_monitoring = False
        self._is_simulating = False  # 模拟测试标志
        self.stock_latest_prices = {}  # 存储每只股票的最新价: {stock_code: float}
        
        self.setup_ui()
        
        # 连接tick数据信号
        if self.qmt_adapter and hasattr(self.qmt_adapter, 'tick_data_signal'):
            self.qmt_adapter.tick_data_signal.connect(self.on_tick_data)
        else:
            logger.error(f"tick_data_signal信号不存在或qmt_adapter为空")
    
    def setup_ui(self):
        """设置UI"""
        layout = QVBoxLayout()
        
        # 说明标签
        info_label = QLabel("功能说明：监控股票池中的股票，当价格首次跌破或等于MA5、MA10、MA20时发出买入信号")
        info_label.setWordWrap(True)
        font = QFont()
        font.setPointSize(10)
        info_label.setFont(font)
        layout.addWidget(info_label)
        
        # 股票池管理区域
        pool_layout = QHBoxLayout()
        pool_label = QLabel("股票池:")
        pool_layout.addWidget(pool_label)
        
        self.pool_count_label = QLabel("0 只股票")
        pool_layout.addWidget(self.pool_count_label)
        
        self.import_button = QPushButton("从CSV导入")
        self.import_button.clicked.connect(self.import_stocks_from_csv)
        pool_layout.addWidget(self.import_button)
        
        self.clear_button = QPushButton("清空股票池")
        self.clear_button.clicked.connect(self.clear_stock_pool)
        pool_layout.addWidget(self.clear_button)
        
        pool_layout.addStretch()
        layout.addLayout(pool_layout)
        
        # 控制按钮区域
        button_layout = QHBoxLayout()
        
        self.start_button = QPushButton("开始监控")
        self.start_button.clicked.connect(self.start_monitor)
        button_layout.addWidget(self.start_button)
        
        self.stop_button = QPushButton("停止监控")
        self.stop_button.clicked.connect(self.stop_monitor)
        self.stop_button.setEnabled(False)
        button_layout.addWidget(self.stop_button)
        
        self.save_button = QPushButton("导出结果")
        self.save_button.clicked.connect(self.save_results)
        self.save_button.setEnabled(False)
        button_layout.addWidget(self.save_button)
        
        self.simulate_button = QPushButton("模拟测试")
        self.simulate_button.clicked.connect(self.start_simulate_test)
        button_layout.addWidget(self.simulate_button)
        
        button_layout.addStretch()
        layout.addLayout(button_layout)
        
        # 状态标签
        self.status_label = QLabel("就绪 - 请先导入股票池")
        layout.addWidget(self.status_label)
        
        # 股票信息表格（显示实时监控信息）
        stock_info_label = QLabel("股票池实时信息:")
        stock_info_label.setFont(font)
        layout.addWidget(stock_info_label)
        
        self.stock_info_table = QTableWidget()
        self.stock_info_table.setColumnCount(6)
        self.stock_info_table.setHorizontalHeaderLabels([
            "股票代码", "股票名称", "最新价", "MA5", "MA10", "MA20"
        ])
        
        # 设置表格属性
        stock_info_header = self.stock_info_table.horizontalHeader()
        stock_info_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        stock_info_header.setSectionResizeMode(1, QHeaderView.Stretch)
        stock_info_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        stock_info_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        stock_info_header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        stock_info_header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        
        # 设置表格只读
        self.stock_info_table.setEditTriggers(QTableWidget.NoEditTriggers)
        
        layout.addWidget(self.stock_info_table)
        
        # 结果表格标签
        result_label = QLabel("跌破记录:")
        result_label.setFont(font)
        layout.addWidget(result_label)
        
        # 结果表格
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(6)
        self.result_table.setHorizontalHeaderLabels([
            "股票代码", "股票名称", "跌破均线", "首次跌破时间", "最新价", "日线价"
        ])
        
        # 设置表格属性
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        
        layout.addWidget(self.result_table)
        
        self.setLayout(layout)
    
    def import_stocks_from_csv(self):
        """从CSV文件导入股票池"""
        try:
            file_path, _ = QFileDialog.getOpenFileName(
                self, "选择CSV文件", "", "CSV文件 (*.csv);;所有文件 (*.*)"
            )
            
            if not file_path:
                return
            
            # 尝试多种编码方式
            encodings = ['utf-8', 'gbk', 'gb2312', 'gb18030', 'utf-8-sig']
            df = None
            last_error = None
            
            for encoding in encodings:
                try:
                    df = pd.read_csv(file_path, encoding=encoding)
                    break
                except (UnicodeDecodeError, UnicodeError) as e:
                    last_error = e
                    continue
                except Exception as e:
                    last_error = e
                    continue
            
            if df is None:
                QMessageBox.warning(self, "错误", f"读取CSV文件失败: 无法使用常见编码读取文件\n最后错误: {str(last_error)}")
                return
            
            # 解析股票代码和名称
            imported_count = 0
            try:
                # 尝试不同的列名
                code_col = None
                name_col = None
                
                for col in df.columns:
                    col_lower = str(col).lower()
                    if '代码' in col or 'code' in col_lower or '证券代码' in col:
                        code_col = col
                    if '名称' in col or 'name' in col_lower or '证券简称' in col or '简称' in col:
                        name_col = col
                
                if code_col is None:
                    QMessageBox.warning(self, "错误", "CSV文件中找不到股票代码列（请确保列名包含'代码'或'code'）")
                    return
                
                for _, row in df.iterrows():
                    try:
                        stock_code = str(row[code_col]).strip().zfill(6)
                        # 剔除5开头的股票代码
                        if stock_code.startswith('5'):
                            continue
                        
                        if name_col:
                            stock_name = str(row[name_col]).strip()
                        else:
                            # 如果没有名称列，尝试从stock_info_manager获取
                            stock_name = get_stock_name(stock_code) or stock_code
                        
                        if len(stock_code) == 6 and stock_code.isdigit():
                            self.stock_pool.add((stock_code, stock_name))
                            imported_count += 1
                    except Exception as e:
                        logger.warning(f"解析行数据失败: {str(e)}")
                        continue
                
                self.pool_count_label.setText(f"{len(self.stock_pool)} 只股票")
                QMessageBox.information(self, "成功", f"成功导入 {imported_count} 只股票\n当前股票池共有 {len(self.stock_pool)} 只股票")
                
            except Exception as e:
                QMessageBox.warning(self, "错误", f"解析CSV文件失败: {str(e)}")
                
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导入股票池失败: {str(e)}")
            logger.error(f"导入股票池失败: {str(e)}", exc_info=True)
    
    def clear_stock_pool(self):
        """清空股票池"""
        if QMessageBox.question(self, "确认", "确定要清空股票池吗？") == QMessageBox.Yes:
            self.stock_pool.clear()
            self.stock_ma_data.clear()
            self.break_flags.clear()
            self.break_records.clear()
            self.stock_latest_prices.clear()
            self.result_table.setRowCount(0)
            self.stock_info_table.setRowCount(0)
            self.pool_count_label.setText("0 只股票")
            self.status_label.setText("股票池已清空")
    
    def start_monitor(self):
        """开始监控"""
        if not self.stock_pool:
            QMessageBox.warning(self, "错误", "请先导入股票池")
            return
        
        if self.is_monitoring:
            QMessageBox.warning(self, "提示", "监控已在进行中")
            return
        
        try:
            self.status_label.setText("正在获取日线数据并计算均线...")
            self.start_button.setEnabled(False)
            
            # 获取日线数据并计算MA
            self._load_ma_data()
            
            if not self.stock_ma_data:
                QMessageBox.warning(self, "错误", "未能获取到任何股票的均线数据")
                self.start_button.setEnabled(True)
                return
            
            # 订阅股票（需要转换为完整代码格式）
            stock_codes = [self._get_full_stock_code(code) for code in self.stock_ma_data.keys()]
            if self.qmt_adapter:
                self.qmt_adapter.update_subscribe_stocks(stock_codes)
            else:
                logger.error("qmt_adapter为空，无法订阅")
            
            # 重置跌破标志
            self.break_flags.clear()
            self.break_records.clear()
            self.result_table.setRowCount(0)
            self.stock_latest_prices.clear()
            
            # 初始化股票信息表格
            self._init_stock_info_table()
            
            self.is_monitoring = True
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.status_label.setText(f"监控中 - 已订阅 {len(stock_codes)} 只股票")
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"启动监控失败: {str(e)}")
            logger.error(f"启动监控失败: {str(e)}", exc_info=True)
            self.start_button.setEnabled(True)
    
    def stop_monitor(self):
        """停止监控"""
        self.is_monitoring = False
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.status_label.setText("监控已停止")
    
    def _load_ma_data(self):
        """加载日线数据并计算MA5、MA10、MA20"""
        self.stock_ma_data.clear()
        
        total = len(self.stock_pool)
        processed = 0
        
        for stock_code, stock_name in self.stock_pool:
            try:
                ma_data = self._calculate_ma(stock_code)
                if ma_data:
                    self.stock_ma_data[stock_code] = ma_data
                    self.stock_ma_data[stock_code]['stock_name'] = stock_name
                
                processed += 1
                if processed % 10 == 0:
                    self.status_label.setText(f"正在计算均线: {processed}/{total}")
                    QApplication.processEvents()
                
                time.sleep(0.05)  # 避免请求过快
                
            except Exception as e:
                logger.warning(f"[{stock_code}] 计算MA失败: {str(e)}")
                continue
        
        logger.info(f"成功计算 {len(self.stock_ma_data)} 只股票的均线数据")
    
    def _calculate_ma(self, stock_code: str) -> Optional[Dict]:
        """计算股票的MA5、MA10、MA20
        
        注意：在交易时段内（15:00之后），如果数据包含今天，MA的计算应该包含今天的数据
        """
        try:
            full_stock_code = self._get_full_stock_code(stock_code)
            
            # 获取最近30个交易日的数据（确保能计算MA20）
            end_date = date.today()
            start_date = end_date - timedelta(days=60)  # 往前推60天确保有足够数据
            
            startdate = start_date.strftime("%Y%m%d") + "000000"
            enddate = end_date.strftime("%Y%m%d") + "235959"
            
            # 下载历史数据
            try:
                xtdata.download_history_data(full_stock_code, '1d', startdate, enddate)
                time.sleep(0.05)
            except Exception as e:
                logger.warning(f"[{stock_code}] 下载历史数据失败: {str(e)}")
                return None
            
            # 获取历史行情数据
            try:
                df = xtdata.get_market_data_ex([], [full_stock_code], period='1d',
                                             start_time=startdate,
                                             end_time=enddate,
                                             count=-1)
            except Exception as e:
                logger.warning(f"[{stock_code}] 获取市场数据失败: {str(e)}")
                return None
            
            if df is None or full_stock_code not in df or len(df[full_stock_code]) == 0:
                return None
            
            # 转换为DataFrame
            stock_data = df[full_stock_code]
            daily_df = pd.DataFrame({
                'time': stock_data['time'],
                'close': stock_data['close'],
            })
            
            if daily_df.empty or len(daily_df) < 20:
                return None
            
            # 判断df的最后一个交易日是否是今天
            today = date.today()
            latest_timestamp = daily_df.iloc[-1]['time']
            
            # 转换时间戳（毫秒）为日期
            if isinstance(latest_timestamp, (int, float)):
                # 如果是时间戳（毫秒），转换为日期
                try:
                    latest_date = datetime.fromtimestamp(latest_timestamp / 1000).date()
                except:
                    try:
                        latest_date = pd.to_datetime(latest_timestamp, unit='ms').date()
                    except:
                        latest_date = None
            elif hasattr(latest_timestamp, 'date'):
                latest_date = latest_timestamp.date()
            else:
                try:
                    latest_date = pd.to_datetime(latest_timestamp).date()
                except:
                    latest_date = None
            
            is_df_contains_today = (latest_date == today) if latest_date is not None else False
            
            # 判断当前时间段和是否是交易日
            now = datetime.now()
            current_time = now.time()
            is_trading_day = is_tradeday(today) if 'is_tradeday' in globals() else (today.weekday() < 5)
            is_trading_day_15_24 = is_trading_day and REFERENCE_SWITCH_TIME <= current_time
            
            # 计算MA（根据时间段决定是否包含今天）
            # 根据key_price_calculator.py的逻辑：
            # - 15:00之后：取最近4天（包括今天）的收盘价，计算平均值
            # - 15:00之前：取最近4天（不包含今天）的收盘价，计算平均值
            if is_df_contains_today and is_trading_day and current_time < REFERENCE_SWITCH_TIME:
                # df包含今天的数据，但在15:00之前，排除今天的数据来计算MA
                daily_df_for_ma = daily_df.iloc[:-1].copy()
                # 计算MA（使用标准rolling）
                daily_df_for_ma['MA5'] = daily_df_for_ma['close'].rolling(window=5).mean()
                daily_df_for_ma['MA10'] = daily_df_for_ma['close'].rolling(window=10).mean()
                daily_df_for_ma['MA20'] = daily_df_for_ma['close'].rolling(window=20).mean()
                
                # 对于今天的MA5，使用最近4天（不包含今天）的收盘价计算平均值
                if len(daily_df_for_ma) >= 4:
                    recent_4_closes = daily_df_for_ma['close'].iloc[-4:].tolist()
                    today_ma5 = sum(recent_4_closes) / 4
                    ma5 = today_ma5
                else:
                    ma5 = daily_df_for_ma['MA5'].iloc[-1]
                
                # 获取最后一个交易日的数据
                last_row = daily_df_for_ma.iloc[-1]
                ma10 = last_row['MA10']
                ma20 = last_row['MA20']
                daily_close = daily_df.iloc[-1]['close']  # 今天的收盘价
            else:
                # 不包含今天的数据，或15:00之后（今天的数据已确定），直接使用
                daily_df_for_ma = daily_df.copy()
                
                # 计算MA
                daily_df_for_ma['MA5'] = daily_df_for_ma['close'].rolling(window=5).mean()
                daily_df_for_ma['MA10'] = daily_df_for_ma['close'].rolling(window=10).mean()
                daily_df_for_ma['MA20'] = daily_df_for_ma['close'].rolling(window=20).mean()
                
                # 如果是15:00之后且包含今天，今天的MA5使用最近4天（包括今天）的收盘价计算平均值
                if is_df_contains_today and is_trading_day_15_24:
                    if len(daily_df_for_ma) >= 4:
                        recent_4_closes = daily_df_for_ma['close'].iloc[-4:].tolist()
                        today_ma5 = sum(recent_4_closes) / 4
                        ma5 = today_ma5
                    else:
                        ma5 = daily_df_for_ma['MA5'].iloc[-1]
                else:
                    ma5 = daily_df_for_ma['MA5'].iloc[-1]
                
                # 获取最后一个交易日的数据
                last_row = daily_df_for_ma.iloc[-1]
                ma10 = last_row['MA10']
                ma20 = last_row['MA20']
                daily_close = last_row['close']
            
            # 检查是否有有效值
            if pd.isna(ma5) or pd.isna(ma10) or pd.isna(ma20):
                return None
            
            # 计算与均线重合的价格（需要传入完整df和当前时间信息）
            ma5_intersection = self._calculate_ma_intersection_price(daily_df, 5, daily_close, is_trading_day, current_time)
            ma10_intersection = self._calculate_ma_intersection_price(daily_df, 10, daily_close, is_trading_day, current_time)
            ma20_intersection = self._calculate_ma_intersection_price(daily_df, 20, daily_close, is_trading_day, current_time)
            
            # 如果无法计算重合价格，使用当前均线值作为备选
            if ma5_intersection is None:
                ma5_intersection = ma5
            if ma10_intersection is None:
                ma10_intersection = ma10
            if ma20_intersection is None:
                ma20_intersection = ma20
            
            return {
                'MA5': float(ma5),
                'MA10': float(ma10),
                'MA20': float(ma20),
                'MA5_intersection': float(ma5_intersection),
                'MA10_intersection': float(ma10_intersection),
                'MA20_intersection': float(ma20_intersection),
                'daily_close': float(daily_close)
            }
            
        except Exception as e:
            logger.error(f"[{stock_code}] 计算MA异常: {str(e)}", exc_info=True)
            return None
    
    def _get_full_stock_code(self, stock_code: str) -> str:
        """获取完整的股票代码（带市场后缀）"""
        if '.' in stock_code:
            return stock_code
        
        if stock_code.startswith(('0', '1', '3')):
            return f"{stock_code}.SZ"
        elif stock_code.startswith('6'):
            return f"{stock_code}.SH"
        elif stock_code.startswith(('8', '4', '920')):
            return f"{stock_code}.BJ"
        else:
            return stock_code
    
    def _calculate_ma_intersection_price(self, df, period, prev_close, is_trading_day, current_time):
        """
        计算与均线重合的可能的最新价
        
        业务逻辑：
        - 交易日的15:00-24:00：获取到今天的历史数据，以到今天为止的四天的收盘价的均价作为5日线重合点
        - 交易日的00:00-15:00：获取到前一交易日的历史数据，以到前一交易日为止的四天的收盘价的均价作为5日线重合点
        - 非交易日：获取到前一交易日的历史数据，以到前一交易日为止的四天的收盘价的均价作为5日线重合点
        
        注意：5日线重合点是前4天的收盘价的平均值（除以4），不是5天除以4。
        
        Args:
            df: 历史数据DataFrame
            period: 均线周期（例如5表示5日线）
            prev_close: 昨收盘价
            is_trading_day: 是否是交易日
            current_time: 当前时间
            
        Returns:
            float: 与均线重合的价格，如果无法计算则返回None
        """
        try:
            # 需要前(period-1)天的数据来计算重合点
            # 例如5日线需要前4天的数据
            days_needed = period - 1
            if len(df) < days_needed:
                return None
            
            today = date.today()
            
            # 判断df的最后一个交易日是否是今天
            latest_timestamp = df.iloc[-1]['time']
            if isinstance(latest_timestamp, (int, float)):
                try:
                    latest_date = datetime.fromtimestamp(latest_timestamp / 1000).date()
                except:
                    try:
                        latest_date = pd.to_datetime(latest_timestamp, unit='ms').date()
                    except:
                        latest_date = None
            elif hasattr(latest_timestamp, 'date'):
                latest_date = latest_timestamp.date()
            else:
                try:
                    latest_date = pd.to_datetime(latest_timestamp).date()
                except:
                    latest_date = None
            is_df_contains_today = (latest_date == today) if latest_date is not None else False
            
            # 判断当前时间段
            is_trading_day_15_24 = is_trading_day and REFERENCE_SWITCH_TIME <= current_time
            is_trading_day_0_15 = is_trading_day and current_time < REFERENCE_SWITCH_TIME
            is_non_trading_day = not is_trading_day
            
            # 根据时间段获取前(period-1)天的收盘价
            if is_trading_day_15_24:
                # 交易日的15:00-24:00：获取到今天的历史数据，以到今天为止的四天的收盘价的均价
                # 需要df包含今天的数据
                if not is_df_contains_today:
                    return None
                
                # 获取最近(period-1)天的收盘价，包括今天
                # 例如5日线：取最近4天（包括今天）
                if len(df) >= days_needed:
                    # df包含今天，取最近days_needed天的数据（包括今天）
                    recent_closes = df['close'].iloc[-days_needed:].tolist()
                else:
                    # 数据不够
                    return None
            elif is_trading_day_0_15 or is_non_trading_day:
                # 交易日的00:00-15:00或非交易日：获取到前一交易日的历史数据，以到前一交易日为止的四天的收盘价的均价
                # 不包含今天的数据
                if is_df_contains_today:
                    # df包含今天的数据，排除今天
                    if len(df) > 1:
                        recent_closes = df['close'].iloc[:-1].tolist()
                    else:
                        return None
                else:
                    # df不包含今天的数据，直接使用
                    recent_closes = df['close'].iloc[:].tolist()
                
                # 取最近days_needed天的数据
                if len(recent_closes) >= days_needed:
                    recent_closes = recent_closes[-days_needed:]
                else:
                    return None
            else:
                return None
            
            # 计算前(period-1)天的收盘价的平均值
            if len(recent_closes) == days_needed:
                intersection_price = sum(recent_closes) / days_needed
                
                # 验证计算结果：检查是否在合理范围内
                if intersection_price > 0 and intersection_price < prev_close * 2:
                    return intersection_price
            
            return None
            
        except Exception as e:
            # 如果计算失败，返回None
            logger.debug(f"计算均线重合价格失败: {str(e)}")
            return None
    
    def on_tick_data(self, tick_data: Dict):
        """处理tick数据回调"""
        # 模拟测试时允许处理，即使is_monitoring为False
        if not self.is_monitoring and not self._is_simulating:
            return
        
        try:
            stock_code = tick_data.get('stock_code', '')
            if not stock_code:
                return
            
            # 统一股票代码格式（去掉后缀，只保留6位数字）
            if '.' in stock_code:
                stock_code = stock_code.split('.')[0]
            
            # 检查是否在监控列表中
            if stock_code not in self.stock_ma_data:
                logger.debug(f"on_tick_data: 股票 {stock_code} 不在监控列表中")
                return
            
            # 获取最新价
            last_price = tick_data.get('lastPrice', 0)
            if last_price <= 0:
                logger.debug(f"on_tick_data: 价格无效 {last_price}")
                return
            
            # 更新最新价
            self.stock_latest_prices[stock_code] = last_price
            
            # 更新股票信息表格中的最新价
            self._update_stock_info_table_price(stock_code, last_price)
            
            # 获取MA数据
            ma_data = self.stock_ma_data[stock_code]
            # 使用与均线重合的价格来判断
            ma5_intersection = ma_data.get('MA5_intersection', ma_data['MA5'])
            ma10_intersection = ma_data.get('MA10_intersection', ma_data['MA10'])
            ma20_intersection = ma_data.get('MA20_intersection', ma_data['MA20'])
            # 保留原始均线值用于显示
            ma5 = ma_data['MA5']
            ma10 = ma_data['MA10']
            ma20 = ma_data['MA20']
            daily_close = ma_data['daily_close']
            
            # 获取时间
            tick_time = tick_data.get('time')
            if isinstance(tick_time, datetime):
                time_str = tick_time.strftime('%Y-%m-%d %H:%M:%S')
            else:
                time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # 检查是否跌破均线（使用与均线重合的价格）
            broken_mas = []
            
            logger.debug(f"on_tick_data: {stock_code} 价格={last_price:.2f}, MA5重合价={ma5_intersection:.2f}, MA10重合价={ma10_intersection:.2f}, MA20重合价={ma20_intersection:.2f}")
            logger.debug(f"on_tick_data: {stock_code} 已跌破标志={self.break_flags[stock_code]}")
            
            # 检查MA5
            if 'MA5' not in self.break_flags[stock_code] and last_price <= ma5_intersection:
                broken_mas.append('MA5')
                self.break_flags[stock_code].add('MA5')
            
            # 检查MA10
            if 'MA10' not in self.break_flags[stock_code] and last_price <= ma10_intersection:
                broken_mas.append('MA10')
                self.break_flags[stock_code].add('MA10')
            
            # 检查MA20
            if 'MA20' not in self.break_flags[stock_code] and last_price <= ma20_intersection:
                broken_mas.append('MA20')
                self.break_flags[stock_code].add('MA20')
            
            # 如果有跌破的均线，记录
            if broken_mas:
                for ma_type in broken_mas:
                    record = {
                        'stock_code': stock_code,
                        'stock_name': ma_data.get('stock_name', stock_code),
                        'ma_type': ma_type,
                        'break_time': time_str,
                        'last_price': last_price,
                        'daily_close': daily_close
                    }
                    self.break_records.append(record)
                    self._add_record_to_table(record)
                    logger.info(f"[{stock_code}] 首次跌破{ma_type}: 最新价={last_price:.2f}, {ma_type}={ma5 if ma_type=='MA5' else (ma10 if ma_type=='MA10' else ma20):.2f}")
                    
                    # 发出声音提醒
                    self._play_alert_sound()
                    
                    # 显示弹窗提醒
                    self._show_alert_dialog(record)
        
        except Exception as e:
            logger.error(f"处理tick数据失败: {str(e)}", exc_info=True)
    
    def _init_stock_info_table(self):
        """初始化股票信息表格"""
        try:
            self.stock_info_table.setRowCount(0)
            
            for stock_code, ma_data in self.stock_ma_data.items():
                row = self.stock_info_table.rowCount()
                self.stock_info_table.insertRow(row)
                
                stock_name = ma_data.get('stock_name', stock_code)
                # 使用与均线重合的价格来显示（这是实际用于判断的值）
                ma5_intersection = ma_data.get('MA5_intersection', ma_data['MA5'])
                ma10_intersection = ma_data.get('MA10_intersection', ma_data['MA10'])
                ma20_intersection = ma_data.get('MA20_intersection', ma_data['MA20'])
                latest_price = self.stock_latest_prices.get(stock_code, 0.0)
                
                # 设置单元格数据
                self.stock_info_table.setItem(row, 0, QTableWidgetItem(stock_code))
                self.stock_info_table.setItem(row, 1, QTableWidgetItem(stock_name))
                
                # 最新价（如果没有则显示"-"）
                price_item = QTableWidgetItem(f"{latest_price:.2f}" if latest_price > 0 else "-")
                self.stock_info_table.setItem(row, 2, price_item)
                
                # 显示与均线重合的价格（这是实际用于判断是否跌破的值）
                self.stock_info_table.setItem(row, 3, QTableWidgetItem(f"{ma5_intersection:.2f}"))
                self.stock_info_table.setItem(row, 4, QTableWidgetItem(f"{ma10_intersection:.2f}"))
                self.stock_info_table.setItem(row, 5, QTableWidgetItem(f"{ma20_intersection:.2f}"))
                
                # 根据价格与均线的关系设置颜色
                if latest_price > 0:
                    self._update_stock_info_table_price(stock_code, latest_price)
            
            logger.info(f"已初始化股票信息表格，共 {self.stock_info_table.rowCount()} 只股票")
            
        except Exception as e:
            logger.error(f"初始化股票信息表格失败: {str(e)}", exc_info=True)
    
    def _update_stock_info_table_price(self, stock_code: str, last_price: float):
        """更新股票信息表格中的最新价"""
        try:
            # 查找对应的行
            for row in range(self.stock_info_table.rowCount()):
                item = self.stock_info_table.item(row, 0)
                if item and item.text() == stock_code:
                    # 更新最新价
                    price_item = self.stock_info_table.item(row, 2)
                    if price_item:
                        price_item.setText(f"{last_price:.2f}")
                    else:
                        price_item = QTableWidgetItem(f"{last_price:.2f}")
                        self.stock_info_table.setItem(row, 2, price_item)
                    
                    # 获取MA值用于颜色判断（从ma_data中获取重合价格，因为表格中显示的就是重合价格）
                    ma_data = self.stock_ma_data.get(stock_code)
                    if ma_data:
                        try:
                            # 使用与均线重合的价格来判断颜色
                            ma5_intersection = ma_data.get('MA5_intersection', ma_data['MA5'])
                            ma10_intersection = ma_data.get('MA10_intersection', ma_data['MA10'])
                            ma20_intersection = ma_data.get('MA20_intersection', ma_data['MA20'])
                            
                            # 根据价格与均线的关系设置背景色
                            # 如果价格低于某个均线，用浅红色背景；否则用浅绿色背景
                            from PyQt5.QtGui import QColor
                            
                            if last_price <= ma20_intersection:
                                # 价格最低，用红色
                                price_item.setBackground(QColor(255, 200, 200))
                            elif last_price <= ma10_intersection:
                                # 价格在MA10和MA20之间，用橙色
                                price_item.setBackground(QColor(255, 220, 180))
                            elif last_price <= ma5_intersection:
                                # 价格在MA5和MA10之间，用黄色
                                price_item.setBackground(QColor(255, 255, 200))
                            else:
                                # 价格高于所有均线，用绿色
                                price_item.setBackground(QColor(200, 255, 200))
                        except (ValueError, KeyError) as e:
                            logger.debug(f"更新颜色失败: {str(e)}")
                            pass
                    
                    break
                    
        except Exception as e:
            logger.error(f"更新股票信息表格价格失败: {str(e)}", exc_info=True)
    
    def _add_record_to_table(self, record: Dict):
        """添加记录到表格（最新记录插入到第一行）"""
        try:
            # 将新记录插入到第一行（row=0），实现倒序排列
            row = 0
            self.result_table.insertRow(row)
            
            self.result_table.setItem(row, 0, QTableWidgetItem(record['stock_code']))
            self.result_table.setItem(row, 1, QTableWidgetItem(record['stock_name']))
            self.result_table.setItem(row, 2, QTableWidgetItem(record['ma_type']))
            self.result_table.setItem(row, 3, QTableWidgetItem(record['break_time']))
            self.result_table.setItem(row, 4, QTableWidgetItem(f"{record['last_price']:.2f}"))
            self.result_table.setItem(row, 5, QTableWidgetItem(f"{record['daily_close']:.2f}"))
            
            # 启用保存按钮
            if not self.save_button.isEnabled():
                self.save_button.setEnabled(True)
        
        except Exception as e:
            logger.error(f"添加记录到表格失败: {str(e)}", exc_info=True)
    
    def save_results(self):
        """保存结果到CSV文件"""
        if not self.break_records:
            QMessageBox.warning(self, "提示", "没有可保存的记录")
            return
        
        try:
            file_path, _ = QFileDialog.getSaveFileName(
                self, "保存结果", f"跌破均线记录_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                "CSV文件 (*.csv);;所有文件 (*.*)"
            )
            
            if not file_path:
                return
            
            # 创建DataFrame，按首次跌破时间倒序排列（最新的在前）
            # 反转列表，使最新的记录在前
            reversed_records = list(reversed(self.break_records))
            df = pd.DataFrame(reversed_records)
            df.to_csv(file_path, index=False, encoding='utf-8-sig')
            
            QMessageBox.information(self, "成功", f"已保存 {len(self.break_records)} 条记录到:\n{file_path}")
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存失败: {str(e)}")
            logger.error(f"保存结果失败: {str(e)}", exc_info=True)
    
    def start_simulate_test(self):
        """启动模拟测试"""
        if not self.stock_ma_data:
            QMessageBox.warning(self, "错误", "请先导入股票池并开始监控（加载MA数据）")
            return
        
        if not self.is_monitoring:
            QMessageBox.warning(self, "提示", "请先点击'开始监控'按钮")
            return
        
        # 确认对话框
        reply = QMessageBox.question(
            self, "确认", 
            "模拟测试将生成虚拟的tick数据来测试跌破均线监控功能。\n"
            "这将模拟价格逐步下跌，触发跌破信号。\n\n"
            "是否继续？",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # 在后台线程中运行模拟测试
        from PyQt5.QtCore import QThread
        
        class SimulateThread(QThread):
            def __init__(self, dialog):
                super().__init__()
                self.dialog = dialog
            
            def run(self):
                self.dialog._run_simulate_test()
        
        self.simulate_thread = SimulateThread(self)
        self.simulate_thread.start()
        self.status_label.setText("模拟测试进行中...")
        self.simulate_button.setEnabled(False)
    
    def _run_simulate_test(self):
        """执行模拟测试（在后台线程中运行）"""
        try:
            import time
            from datetime import datetime, timezone, timedelta
            
            # 设置模拟测试标志
            self._is_simulating = True
            
            # 清空之前的跌破标志，以便重新测试
            self.break_flags.clear()
            for stock_code in self.stock_ma_data.keys():
                self.break_flags[stock_code] = set()
            
            logger.info("开始模拟测试...")
            
            # 测试场景1：价格高于所有均线（不应该触发）
            logger.info("测试场景1: 价格高于所有均线")
            for stock_code in self.stock_ma_data.keys():
                ma_data = self.stock_ma_data[stock_code]
                high_price = max(ma_data['MA5'], ma_data['MA10'], ma_data['MA20']) * 1.05
                logger.info(f"  [{stock_code}] 模拟价格: {high_price:.2f} (高于所有均线)")
                self._simulate_tick_data(stock_code, high_price)
                time.sleep(0.1)
            
            time.sleep(1)
            
            # 测试场景2：价格跌破MA5
            logger.info("测试场景2: 价格跌破MA5")
            for stock_code in self.stock_ma_data.keys():
                ma_data = self.stock_ma_data[stock_code]
                break_price = ma_data['MA5'] * 0.99  # 低于MA5
                logger.info(f"  [{stock_code}] 模拟价格: {break_price:.2f} (跌破MA5={ma_data['MA5']:.2f})")
                self._simulate_tick_data(stock_code, break_price)
                time.sleep(0.1)
            
            time.sleep(1)
            
            # 测试场景3：价格跌破MA10
            logger.info("测试场景3: 价格跌破MA10")
            for stock_code in self.stock_ma_data.keys():
                ma_data = self.stock_ma_data[stock_code]
                break_price = ma_data['MA10'] * 0.99  # 低于MA10
                logger.info(f"  [{stock_code}] 模拟价格: {break_price:.2f} (跌破MA10={ma_data['MA10']:.2f})")
                self._simulate_tick_data(stock_code, break_price)
                time.sleep(0.1)
            
            time.sleep(1)
            
            # 测试场景4：价格跌破MA20
            logger.info("测试场景4: 价格跌破MA20")
            for stock_code in self.stock_ma_data.keys():
                ma_data = self.stock_ma_data[stock_code]
                break_price = ma_data['MA20'] * 0.99  # 低于MA20
                logger.info(f"  [{stock_code}] 模拟价格: {break_price:.2f} (跌破MA20={ma_data['MA20']:.2f})")
                self._simulate_tick_data(stock_code, break_price)
                time.sleep(0.1)
            
            time.sleep(1)
            
            # 测试场景5：重复跌破（不应该再次触发）
            logger.info("测试场景5: 重复跌破MA5（应该不触发）")
            for stock_code in self.stock_ma_data.keys():
                ma_data = self.stock_ma_data[stock_code]
                break_price = ma_data['MA5'] * 0.98  # 再次低于MA5
                logger.info(f"  [{stock_code}] 模拟价格: {break_price:.2f} (再次跌破MA5，应该不触发)")
                self._simulate_tick_data(stock_code, break_price)
                time.sleep(0.1)
            
            logger.info(f"模拟测试完成，共触发 {len(self.break_records)} 条跌破记录")
            
            # 清除模拟测试标志
            self._is_simulating = False
            
            # 在主线程中更新UI
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(0, self._on_simulate_test_finished)
            
        except Exception as e:
            logger.error(f"模拟测试失败: {str(e)}", exc_info=True)
            # 清除模拟测试标志
            self._is_simulating = False
            from PyQt5.QtCore import QTimer
            error_msg = str(e)
            QTimer.singleShot(0, lambda: self._on_simulate_test_error(error_msg))
    
    def _simulate_tick_data(self, stock_code: str, last_price: float):
        """模拟tick数据并调用on_tick_data（通过QTimer.singleShot，确保在主线程执行）"""
        try:
            from datetime import datetime, timezone, timedelta
            tick_time = datetime.now(timezone(timedelta(hours=8)))
            
            # 创建模拟的tick数据字典
            tick_data = {
                'stock_code': stock_code,
                'lastPrice': last_price,
                'time': tick_time
            }
            
            logger.debug(f"_simulate_tick_data: 创建tick数据 {stock_code}, 价格 {last_price}")
            
            # 使用QTimer.singleShot在主线程中调用on_tick_data
            # 使用functools.partial确保tick_data被正确传递
            from PyQt5.QtCore import QTimer
            from functools import partial
            QTimer.singleShot(0, partial(self.on_tick_data, tick_data))
            
            # 等待一小段时间，确保回调被执行
            # 使用QApplication.processEvents()来确保事件被处理
            from PyQt5.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                app.processEvents()
            
        except Exception as e:
            logger.error(f"模拟tick数据失败: {str(e)}", exc_info=True)
    
    def _on_simulate_test_finished(self):
        """模拟测试完成后的UI更新"""
        self.status_label.setText(f"模拟测试完成 - 共触发 {len(self.break_records)} 条跌破记录")
        self.simulate_button.setEnabled(True)
        if self.break_records:
            self.save_button.setEnabled(True)
        QMessageBox.information(self, "完成", f"模拟测试完成！\n共触发 {len(self.break_records)} 条跌破记录")
    
    def _on_simulate_test_error(self, error_msg: str):
        """模拟测试出错后的UI更新"""
        self.status_label.setText(f"模拟测试失败: {error_msg}")
        self.simulate_button.setEnabled(True)
        QMessageBox.critical(self, "错误", f"模拟测试失败:\n{error_msg}")
    
    def _play_alert_sound(self):
        """播放提醒声音"""
        try:
            # 使用Windows系统声音（SystemExclamation）
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception as e:
            logger.warning(f"播放声音失败: {str(e)}")
    
    def _show_alert_dialog(self, record: Dict):
        """显示提醒弹窗"""
        try:
            alert_dialog = BreakAlertDialog(
                stock_code=record['stock_code'],
                stock_name=record['stock_name'],
                ma_type=record['ma_type'],
                last_price=record['last_price'],
                break_time=record['break_time'],
                parent=self
            )
            # 显示弹窗（非模态，不阻塞主窗口）
            alert_dialog.show()
            # 确保弹窗显示在最前面
            alert_dialog.raise_()
            alert_dialog.activateWindow()
        except Exception as e:
            logger.error(f"显示提醒弹窗失败: {str(e)}", exc_info=True)
    
    def closeEvent(self, event):
        """关闭对话框时停止监控"""
        if self.is_monitoring:
            self.stop_monitor()
        event.accept()

