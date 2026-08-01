#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
股票筛选结果回测程序
导入CSV文件，对每只股票进行回测：
1. 以信号触发日期的下一个交易日的开盘价作为买入价
2. 计算每天的10日线重合点价格 = 前9日的收盘价之和 / 9
3. 如果当天的最低价 > 10日线重合点价格，继续看下一天
4. 如果当天的最低价 <= 10日线重合点价格，卖出价格 = min(10日线重合点价格, 当天最高价)
5. 计算获利比例
"""

import sys
import os
import pandas as pd
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Tuple
import time
import logging
import warnings
import json
import csv

# 抑制Qt和log4cplus的警告/错误信息
os.environ.setdefault('QT_LOGGING_RULES', '*.debug=false')
warnings.filterwarnings("ignore")

# 添加项目根目录到路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QTableWidget, QTableWidgetItem, QLabel, QMessageBox,
                             QProgressBar, QHeaderView, QApplication, QFileDialog,
                             QRadioButton, QButtonGroup, QGroupBox)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont

try:
    import xtquant.xtdata as xtdata
    from utils.trading_day import is_tradeday
except ImportError as e:
    print(f"导入模块失败: {e}")

# 配置日志
_LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_LOGS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(_LOGS_DIR, 'backtest_stock_filter.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class BacktestThread(QThread):
    """回测线程"""
    
    # 信号：更新进度
    progress_updated = pyqtSignal(int, int, str)  # (当前数量, 总数, 当前股票代码)
    # 信号：回测结果
    result_ready = pyqtSignal(dict)  # (回测结果字典)
    # 信号：回测完成
    finished = pyqtSignal(list)  # (所有回测结果列表)
    # 信号：错误信息
    error_occurred = pyqtSignal(str)  # (错误信息)
    
    def __init__(self, stocks_data: List[Dict], strategy: int = 1, parent=None):
        super().__init__(parent)
        self.stocks_data = stocks_data  # [{'code': str, 'name': str, 'sector': str, 'signal_date': str}, ...]
        self.strategy = strategy  # 1: 最低价策略, 2: 收盘价策略, 3: 最高价策略
        self.is_running = True
        
    def stop(self):
        """停止回测"""
        self.is_running = False
    
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
    
    def _parse_date(self, date_str: str) -> Optional[date]:
        """解析日期字符串，支持多种格式
        
        Args:
            date_str: 日期字符串
            
        Returns:
            解析后的日期对象，如果解析失败返回None
        """
        if not date_str or not date_str.strip():
            return None
        
        date_str = date_str.strip()
        
        # 尝试多种日期格式
        date_formats = [
            '%Y-%m-%d',      # 2025-11-20
            '%Y/%m/%d',      # 2025/11/20
            '%Y.%m.%d',      # 2025.11.20
            '%Y年%m月%d日',  # 2025年11月20日
            '%Y%m%d',        # 20251120
        ]
        
        for fmt in date_formats:
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue
        
        return None
    
    def _get_next_trading_day(self, base_date: date) -> Optional[date]:
        """获取指定日期之后的下一个交易日"""
        check_date = base_date + timedelta(days=1)
        max_search_days = 10  # 最多查找10天
        
        for _ in range(max_search_days):
            if is_tradeday(check_date):
                return check_date
            check_date += timedelta(days=1)
        
        return None
    
    def _get_daily_data(self, stock_code: str, start_date: date, end_date: date) -> Optional[pd.DataFrame]:
        """获取日线数据"""
        try:
            full_stock_code = self._get_full_stock_code(stock_code)
            
            startdate = start_date.strftime("%Y%m%d") + "000000"
            enddate = end_date.strftime("%Y%m%d") + "235959"
            
            # 下载历史数据
            try:
                xtdata.download_history_data(full_stock_code, '1d', startdate, enddate)
                time.sleep(0.05)  # 短暂等待
            except Exception as e:
                logger.error(f"[{stock_code}] 下载历史数据失败: {str(e)}", exc_info=True)
                return None
            
            # 获取历史行情数据
            try:
                df = xtdata.get_market_data_ex([], [full_stock_code], period='1d',
                                             start_time=startdate,
                                             end_time=enddate,
                                             count=-1)
            except Exception as e:
                logger.error(f"[{stock_code}] 获取市场数据失败: {str(e)}", exc_info=True)
                return None
            
            if df is None:
                return None
            
            if full_stock_code not in df:
                return None
            
            if len(df[full_stock_code]) == 0:
                return None
            
            # 转换为DataFrame
            stock_data = df[full_stock_code]
            
            daily_data = pd.DataFrame({
                'time': stock_data['time'],
                'open': stock_data['open'],
                'high': stock_data['high'],
                'low': stock_data['low'],
                'close': stock_data['close'],
                'volume': stock_data['volume']
            })
            
            # 设置time为索引并转换为日期
            daily_data.set_index('time', inplace=True)
            
            # 转换时间戳为日期
            time_index = pd.to_datetime(daily_data.index, unit='ms')
            if time_index.tz is None:
                time_index = time_index.tz_localize('UTC').tz_convert('Asia/Shanghai')
            else:
                time_index = time_index.tz_convert('Asia/Shanghai')
            
            date_series = pd.Series(time_index, index=daily_data.index)
            daily_data['date'] = date_series.dt.date
            
            # 筛选交易日数据
            daily_data = daily_data[daily_data['date'].apply(is_tradeday)]
            
            # 按日期排序
            daily_data = daily_data.sort_values('date')
            
            return daily_data
            
        except Exception as e:
            logger.error(f"[{stock_code}] 获取日线数据异常: {str(e)}", exc_info=True)
            return None
    
    def _backtest_stock(self, stock_info: Dict) -> Dict:
        """回测单只股票
        
        Args:
            stock_info: 股票信息 {'code': str, 'name': str, 'sector': str, 'signal_date': str}
        
        Returns:
            回测结果字典
        """
        stock_code = stock_info['code']
        stock_name = stock_info['name']
        sector = stock_info.get('sector', '')
        signal_date_str = stock_info.get('signal_date', '')
        
        result = {
            'code': stock_code,
            'name': stock_name,
            'sector': sector,
            'signal_date': signal_date_str,
            'strategy': self.strategy,  # 策略编号
            'buy_date': '',
            'buy_price': 0.0,
            'sell_date': '',
            'sell_price': 0.0,
            'profit_ratio': 0.0,
            'status': '未达到卖点',
            'error': ''
        }
        
        try:
            # 解析信号触发日期
            if not signal_date_str:
                result['error'] = '信号触发日期为空'
                return result
            
            signal_date = self._parse_date(signal_date_str)
            if signal_date is None:
                result['error'] = f'信号触发日期格式错误: {signal_date_str}'
                return result
            
            # 获取下一个交易日作为买入日期
            buy_date = self._get_next_trading_day(signal_date)
            if buy_date is None:
                result['error'] = f'无法找到信号触发日期({signal_date_str})之后的下一个交易日'
                return result
            
            # 获取日线数据（从买入日期往前推30天，到当前日期）
            end_date = date.today()
            start_date = buy_date - timedelta(days=30)  # 往前推30天以确保有足够数据计算10日线
            
            daily_data = self._get_daily_data(stock_code, start_date, end_date)
            if daily_data is None or daily_data.empty:
                result['error'] = '无法获取日线数据'
                return result
            
            # 找到买入日期的数据
            buy_data = daily_data[daily_data['date'] == buy_date]
            if buy_data.empty:
                result['error'] = f'无法找到买入日期({buy_date})的数据'
                return result
            
            buy_price = float(buy_data.iloc[0]['open'])
            result['buy_date'] = buy_date.strftime('%Y-%m-%d')
            result['buy_price'] = buy_price
            
            # 从买入日期之后开始检查
            after_buy_data = daily_data[daily_data['date'] > buy_date].copy()
            if after_buy_data.empty:
                result['error'] = '买入日期之后没有交易日数据'
                return result
            
            # 按日期排序
            after_buy_data = after_buy_data.sort_values('date')
            
            # 查找卖出点：根据策略执行不同的卖出条件
            sell_date = None
            sell_price = None
            
            # 重置索引以便使用位置索引
            daily_data_reset = daily_data.reset_index(drop=True)
            
            for idx, row in after_buy_data.iterrows():
                trade_date = row['date']
                low_price = float(row['low'])
                high_price = float(row['high'])
                close_price = float(row['close'])
                
                # 计算当天的10日线重合点价格 = 前9日的收盘价之和 / 9
                # 找到当天在daily_data_reset中的位置
                date_list = daily_data_reset['date'].tolist()
                try:
                    date_position = date_list.index(trade_date)
                except ValueError:
                    continue
                
                # 需要前9天的收盘价（不包括当天）
                if date_position < 9:
                    # 数据不足，无法计算10日线重合点
                    continue
                
                # 获取前9天的收盘价（不包括当天）
                prev_9_closes = daily_data_reset['close'].iloc[date_position - 9:date_position].tolist()
                
                if len(prev_9_closes) != 9:
                    continue
                
                # 计算10日线重合点价格 = 前9日的收盘价之和 / 9
                ma10_intersection = sum(prev_9_closes) / 9
                
                # 根据策略执行不同的卖出条件
                should_sell = False
                
                if self.strategy == 1:
                    # 策略一：当最低价 <= 10日线重合点价格时卖出
                    if low_price <= ma10_intersection:
                        should_sell = True
                        sell_price = min(ma10_intersection, high_price)
                elif self.strategy == 2:
                    # 策略二：当收盘价 <= 10日线重合点价格时卖出
                    if close_price <= ma10_intersection:
                        should_sell = True
                        sell_price = close_price
                elif self.strategy == 3:
                    # 策略三：当最高价 <= 10日线重合点价格时卖出
                    if high_price <= ma10_intersection:
                        should_sell = True
                        sell_price = close_price
                
                if should_sell:
                    sell_date = trade_date
                    break
            
            # 如果找到卖出点
            if sell_date is not None and sell_price is not None:
                result['sell_date'] = sell_date.strftime('%Y-%m-%d')
                result['sell_price'] = sell_price
                result['profit_ratio'] = ((sell_price - buy_price) / buy_price) * 100
                result['status'] = '已卖出'
            else:
                # 没有找到卖出点，使用最后一个交易日的数据
                last_row = after_buy_data.iloc[-1]
                last_date = last_row['date']
                last_close = float(last_row['close'])
                
                result['sell_date'] = last_date.strftime('%Y-%m-%d')
                result['sell_price'] = last_close
                result['profit_ratio'] = ((last_close - buy_price) / buy_price) * 100
                result['status'] = '未达到卖点'
            
            return result
            
        except Exception as e:
            logger.error(f"[{stock_code}] 回测过程出错: {str(e)}", exc_info=True)
            result['error'] = f'回测异常: {str(e)}'
            return result
    
    def run(self):
        """运行回测"""
        results = []
        
        try:
            total_stocks = len(self.stocks_data)
            
            for idx, stock_info in enumerate(self.stocks_data):
                if not self.is_running:
                    break
                
                stock_code = stock_info['code']
                
                # 回测股票
                result = self._backtest_stock(stock_info)
                results.append(result)
                
                # 更新进度
                self.progress_updated.emit(idx + 1, total_stocks, stock_code)
                
                # 发送结果
                self.result_ready.emit(result)
                
                # 短暂延迟，避免请求过快
                time.sleep(0.1)
            
            self.finished.emit(results)
            
        except Exception as e:
            self.error_occurred.emit(f"回测过程出错: {str(e)}")
            self.finished.emit(results)


class BacktestDialog(QDialog):
    """回测对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("股票筛选结果回测")
        self.setMinimumSize(1200, 800)
        self.resize(1400, 900)
        
        self.backtest_thread = None
        self.results = []  # 存储所有回测结果
        
        self.setup_ui()
    
    def setup_ui(self):
        """设置UI"""
        layout = QVBoxLayout()
        
        # 文件选择区域
        file_group = QHBoxLayout()
        file_group.addWidget(QLabel("选择CSV文件："))
        self.file_path_label = QLabel("未选择文件")
        file_group.addWidget(self.file_path_label)
        
        self.select_file_button = QPushButton("选择文件")
        self.select_file_button.clicked.connect(self.select_file)
        file_group.addWidget(self.select_file_button)
        
        self.start_button = QPushButton("开始回测")
        self.start_button.clicked.connect(self.start_backtest)
        self.start_button.setEnabled(False)
        file_group.addWidget(self.start_button)
        
        self.stop_button = QPushButton("停止回测")
        self.stop_button.clicked.connect(self.stop_backtest)
        self.stop_button.setEnabled(False)
        file_group.addWidget(self.stop_button)
        
        file_group.addStretch()
        layout.addLayout(file_group)
        
        # 策略选择区域
        strategy_group = QGroupBox("回测策略选择")
        strategy_layout = QHBoxLayout()
        
        self.strategy_button_group = QButtonGroup()
        self.strategy1_radio = QRadioButton("策略一：最低价≤10日线时卖出，卖出价=min(10日线,最高价)")
        self.strategy1_radio.setChecked(True)  # 默认选中策略一
        self.strategy_button_group.addButton(self.strategy1_radio, 1)
        strategy_layout.addWidget(self.strategy1_radio)
        
        self.strategy2_radio = QRadioButton("策略二：收盘价≤10日线时卖出，卖出价=收盘价")
        self.strategy_button_group.addButton(self.strategy2_radio, 2)
        strategy_layout.addWidget(self.strategy2_radio)
        
        self.strategy3_radio = QRadioButton("策略三：最高价≤10日线时卖出，卖出价=收盘价")
        self.strategy_button_group.addButton(self.strategy3_radio, 3)
        strategy_layout.addWidget(self.strategy3_radio)
        
        strategy_layout.addStretch()
        strategy_group.setLayout(strategy_layout)
        layout.addWidget(strategy_group)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        
        # 状态标签
        self.status_label = QLabel("请选择CSV文件开始回测")
        layout.addWidget(self.status_label)
        
        # 汇总统计标签
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("font-weight: bold; font-size: 12pt; color: #0066cc;")
        layout.addWidget(self.summary_label)
        
        # 结果表格
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(12)
        self.result_table.setHorizontalHeaderLabels([
            "策略", "股票代码", "股票名称", "所属板块", "信号触发日期", 
            "买入日期", "买入价", "卖出日期", "卖出价", 
            "获利比例(%)", "状态", "错误信息"
        ])
        
        # 设置表格属性
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(9, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(10, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(11, QHeaderView.Stretch)
        
        layout.addWidget(self.result_table)
        
        # 下载按钮
        download_layout = QHBoxLayout()
        download_layout.addStretch()
        self.download_button = QPushButton("下载结果")
        self.download_button.clicked.connect(self.download_results)
        self.download_button.setEnabled(False)
        download_layout.addWidget(self.download_button)
        layout.addLayout(download_layout)
        
        self.setLayout(layout)
    
    def select_file(self):
        """选择CSV文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择CSV文件",
            "",
            "CSV文件 (*.csv);;所有文件 (*.*)"
        )
        
        if file_path:
            self.file_path = file_path
            self.file_path_label.setText(os.path.basename(file_path))
            self.start_button.setEnabled(True)
            logger.info(f"选择文件: {file_path}")
    
    def load_csv_file(self, file_path: str) -> List[Dict]:
        """加载CSV文件"""
        try:
            # 尝试不同的编码
            encodings = ['utf-8-sig', 'utf-8', 'gbk', 'gb2312', 'gb18030']
            df = None
            
            for encoding in encodings:
                try:
                    df = pd.read_csv(file_path, encoding=encoding)
                    logger.info(f"成功使用 {encoding} 编码读取文件")
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue
            
            if df is None:
                raise Exception("无法读取文件，尝试了所有编码格式")
            
            if df.empty:
                raise Exception("文件为空")
            
            # 检查必需的列
            required_columns = ['股票代码', '股票名称']
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                raise Exception(f"缺少必需的列: {', '.join(missing_columns)}")
            
            # 确定信号触发日期列名（可能是"信号触发日期"或"放量反包日期"）
            signal_date_column = None
            if '信号触发日期' in df.columns:
                signal_date_column = '信号触发日期'
            elif '放量反包日期' in df.columns:
                signal_date_column = '放量反包日期'
            else:
                raise Exception("找不到信号触发日期列（需要'信号触发日期'或'放量反包日期'）")
            
            # 转换为字典列表
            stocks_data = []
            for _, row in df.iterrows():
                stock_data = {
                    'code': str(row['股票代码']).strip().zfill(6),
                    'name': str(row['股票名称']).strip(),
                    'sector': str(row.get('所属板块', '')).strip(),
                    'signal_date': str(row[signal_date_column]).strip() if pd.notna(row[signal_date_column]) else ''
                }
                stocks_data.append(stock_data)
            
            logger.info(f"成功加载 {len(stocks_data)} 只股票")
            return stocks_data
            
        except Exception as e:
            logger.error(f"加载CSV文件失败: {str(e)}", exc_info=True)
            raise
    
    def start_backtest(self):
        """开始回测"""
        try:
            if not hasattr(self, 'file_path'):
                QMessageBox.warning(self, "错误", "请先选择CSV文件")
                return
            
            # 加载CSV文件
            try:
                stocks_data = self.load_csv_file(self.file_path)
            except Exception as e:
                QMessageBox.warning(self, "错误", f"加载CSV文件失败: {str(e)}")
                return
            
            if not stocks_data:
                QMessageBox.warning(self, "错误", "CSV文件中没有数据")
                return
            
            # 清空结果
            self.results = []
            self.result_table.setRowCount(0)
            self.summary_label.setText("")
            
            # 获取选中的策略
            selected_strategy = self.strategy_button_group.checkedId()
            if selected_strategy <= 0:
                selected_strategy = 1  # 默认策略一
            
            strategy_names = {1: "策略一", 2: "策略二", 3: "策略三"}
            strategy_name = strategy_names.get(selected_strategy, "策略一")
            
            # 更新状态
            self.status_label.setText(f"开始回测 {len(stocks_data)} 只股票（{strategy_name}）...")
            
            # 禁用开始按钮，启用停止按钮
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.download_button.setEnabled(False)
            
            # 创建回测线程
            self.backtest_thread = BacktestThread(stocks_data, selected_strategy, self)
            self.backtest_thread.progress_updated.connect(self.on_progress_updated)
            self.backtest_thread.result_ready.connect(self.on_result_ready)
            self.backtest_thread.finished.connect(self.on_finished)
            self.backtest_thread.error_occurred.connect(self.on_error)
            
            # 启动线程
            self.backtest_thread.start()
            
        except Exception as e:
            logger.error(f"启动回测失败: {str(e)}", exc_info=True)
            QMessageBox.warning(self, "错误", f"启动回测失败: {str(e)}")
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
    
    def stop_backtest(self):
        """停止回测"""
        try:
            if self.backtest_thread and self.backtest_thread.isRunning():
                self.backtest_thread.stop()
                self.status_label.setText("正在停止回测...")
                logger.info("用户点击停止回测")
        except Exception as e:
            logger.error(f"停止回测失败: {str(e)}", exc_info=True)
    
    def on_progress_updated(self, current: int, total: int, stock_code: str):
        """更新进度"""
        progress = int((current / total) * 100) if total > 0 else 0
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.status_label.setText(f"正在回测: {stock_code} ({current}/{total})")
    
    def update_summary(self):
        """更新汇总统计信息"""
        if not self.results:
            self.summary_label.setText("")
            return
        
        total_count = len(self.results)
        success_results = [r for r in self.results if not r.get('error')]
        success_count = len(success_results)
        error_count = total_count - success_count
        
        # 按策略统计
        strategy_stats = {}
        strategy_names = {1: "策略一", 2: "策略二", 3: "策略三"}
        
        for result in self.results:
            strategy_num = result.get('strategy', 1)
            if strategy_num not in strategy_stats:
                strategy_stats[strategy_num] = {'total': 0, 'success': 0, 'profit_ratios': []}
            
            strategy_stats[strategy_num]['total'] += 1
            if not result.get('error'):
                strategy_stats[strategy_num]['success'] += 1
                if result.get('profit_ratio', 0) != 0:
                    strategy_stats[strategy_num]['profit_ratios'].append(result['profit_ratio'])
        
        # 构建汇总文本
        summary_parts = [f"总股票数 {total_count} | 成功 {success_count} | 失败 {error_count}"]
        
        if success_count > 0:
            # 总体平均获利比例
            profit_ratios = [r['profit_ratio'] for r in success_results if r.get('profit_ratio', 0) != 0]
            if profit_ratios:
                avg_profit_ratio = sum(profit_ratios) / len(profit_ratios)
                summary_parts.append(f"平均获利比例 {avg_profit_ratio:.2f}%")
        
        # 按策略显示统计
        if len(strategy_stats) > 0:
            strategy_parts = []
            for strategy_num in sorted(strategy_stats.keys()):
                stats = strategy_stats[strategy_num]
                strategy_name = strategy_names.get(strategy_num, f"策略{strategy_num}")
                part = f"{strategy_name}: {stats['success']}/{stats['total']}"
                if stats['profit_ratios']:
                    avg = sum(stats['profit_ratios']) / len(stats['profit_ratios'])
                    part += f" (平均{avg:.2f}%)"
                strategy_parts.append(part)
            if strategy_parts:
                summary_parts.append(" | " + " | ".join(strategy_parts))
        
        summary_text = "汇总统计：" + " | ".join(summary_parts)
        self.summary_label.setText(summary_text)
    
    def on_result_ready(self, result: Dict):
        """处理回测结果"""
        # 添加到结果列表
        self.results.append(result)
        
        # 添加到表格
        row = self.result_table.rowCount()
        self.result_table.insertRow(row)
        
        # 策略名称
        strategy_num = result.get('strategy', 1)
        strategy_names = {1: "策略一", 2: "策略二", 3: "策略三"}
        strategy_name = strategy_names.get(strategy_num, "策略一")
        
        self.result_table.setItem(row, 0, QTableWidgetItem(strategy_name))
        self.result_table.setItem(row, 1, QTableWidgetItem(result['code']))
        self.result_table.setItem(row, 2, QTableWidgetItem(result['name']))
        self.result_table.setItem(row, 3, QTableWidgetItem(result['sector']))
        self.result_table.setItem(row, 4, QTableWidgetItem(result['signal_date']))
        self.result_table.setItem(row, 5, QTableWidgetItem(result['buy_date']))
        self.result_table.setItem(row, 6, QTableWidgetItem(f"{result['buy_price']:.2f}" if result['buy_price'] > 0 else ""))
        self.result_table.setItem(row, 7, QTableWidgetItem(result['sell_date']))
        self.result_table.setItem(row, 8, QTableWidgetItem(f"{result['sell_price']:.2f}" if result['sell_price'] > 0 else ""))
        self.result_table.setItem(row, 9, QTableWidgetItem(f"{result['profit_ratio']:.2f}" if result['profit_ratio'] != 0 else ""))
        self.result_table.setItem(row, 10, QTableWidgetItem(result['status']))
        self.result_table.setItem(row, 11, QTableWidgetItem(result.get('error', '')))
        
        # 滚动到最新行
        self.result_table.scrollToItem(self.result_table.item(row, 1))
        
        # 更新汇总统计
        self.update_summary()
    
    def on_finished(self, results: List[Dict]):
        """回测完成"""
        total_count = len(results)
        success_count = sum(1 for r in results if not r.get('error'))
        error_count = total_count - success_count
        
        info_text = f"回测完成，共 {total_count} 只股票（成功: {success_count}, 失败: {error_count}）"
        self.status_label.setText(info_text)
        
        # 更新汇总统计
        self.update_summary()
        
        # 重新启用开始按钮，禁用停止按钮
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.download_button.setEnabled(True)
        
        logger.info(info_text)
    
    def on_error(self, error_msg: str):
        """处理错误"""
        QMessageBox.warning(self, "错误", error_msg)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
    
    def download_results(self):
        """下载回测结果"""
        if not self.results:
            QMessageBox.information(self, "提示", "没有结果可下载")
            return
        
        try:
            # 创建保存目录
            history_dir = os.path.join(os.path.dirname(__file__), 'history_data')
            os.makedirs(history_dir, exist_ok=True)
            
            # 生成文件名
            filename = f"回测结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            file_path = os.path.join(history_dir, filename)
            
            # 保存为CSV（将策略编号转换为策略名称）
            strategy_names = {1: "策略一", 2: "策略二", 3: "策略三"}
            results_for_csv = []
            for r in self.results:
                r_copy = r.copy()
                strategy_num = r_copy.get('strategy', 1)
                # 添加策略名称字段
                r_copy['策略'] = strategy_names.get(strategy_num, "策略一")
                # 保留strategy字段以便后续处理，或者可以删除它
                results_for_csv.append(r_copy)
            
            df = pd.DataFrame(results_for_csv)
            # 重新排列列的顺序，将策略放在前面
            if '策略' in df.columns:
                cols = ['策略'] + [col for col in df.columns if col != '策略']
                df = df[cols]
            df.to_csv(file_path, index=False, encoding='utf-8-sig')
            
            # 计算汇总统计（按策略分别统计）
            total_count = len(self.results)
            success_results = [r for r in self.results if not r.get('error')]
            success_count = len(success_results)
            error_count = total_count - success_count
            
            summary_info = f"总股票数: {total_count} | 成功: {success_count} | 失败: {error_count}"
            if success_count > 0:
                profit_ratios = [r['profit_ratio'] for r in success_results if r.get('profit_ratio', 0) != 0]
                if profit_ratios:
                    avg_profit_ratio = sum(profit_ratios) / len(profit_ratios)
                    summary_info += f" | 平均获利比例: {avg_profit_ratio:.2f}%"
            
            # 按策略统计
            strategy_stats = {}
            for result in self.results:
                strategy_num = result.get('strategy', 1)
                if strategy_num not in strategy_stats:
                    strategy_stats[strategy_num] = {'total': 0, 'success': 0, 'profit_ratios': []}
                strategy_stats[strategy_num]['total'] += 1
                if not result.get('error'):
                    strategy_stats[strategy_num]['success'] += 1
                    if result.get('profit_ratio', 0) != 0:
                        strategy_stats[strategy_num]['profit_ratios'].append(result['profit_ratio'])
            
            if strategy_stats:
                strategy_parts = []
                for strategy_num in sorted(strategy_stats.keys()):
                    stats = strategy_stats[strategy_num]
                    strategy_name = strategy_names.get(strategy_num, f"策略{strategy_num}")
                    part = f"{strategy_name}: {stats['success']}/{stats['total']}"
                    if stats['profit_ratios']:
                        avg = sum(stats['profit_ratios']) / len(stats['profit_ratios'])
                        part += f" (平均{avg:.2f}%)"
                    strategy_parts.append(part)
                if strategy_parts:
                    summary_info += "\n按策略统计: " + " | ".join(strategy_parts)
            
            logger.info(f"回测结果已保存到: {file_path}")
            self.status_label.setText(f"回测结果已保存到: {file_path}")
            QMessageBox.information(self, "保存成功", f"回测结果已保存到:\n{file_path}\n共 {len(self.results)} 条记录\n\n{summary_info}")
            
        except Exception as e:
            QMessageBox.warning(self, "错误", f"保存失败: {str(e)}")
            logger.error(f"保存回测结果时出错: {str(e)}", exc_info=True)


def main():
    app = QApplication(sys.argv)
    
    dialog = BacktestDialog()
    dialog.show()
    
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()

