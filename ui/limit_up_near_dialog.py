#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
接近涨停股票筛选对话框
筛选最近10个交易日内有接近涨停（75%涨停幅度），且该日最高价不低于之前和之后最高价的股票
"""

import sys
import os
import pandas as pd
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Tuple
import time
import logging

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QTableWidget, QTableWidgetItem, QLabel, QMessageBox,
                             QFileDialog, QProgressBar, QHeaderView)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont

# 添加项目根目录到路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
        logging.FileHandler(os.path.join(_LOGS_DIR, 'limit_up_near_filter.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class LimitUpNearFilterThread(QThread):
    """接近涨停筛选线程"""
    
    # 信号：更新进度
    progress_updated = pyqtSignal(int, int, str)  # (当前数量, 总数, 当前股票代码)
    # 信号：找到符合条件的股票
    stock_found = pyqtSignal(str, str, str, str)  # (股票代码, 股票名称, 接近涨停日期, 收盘价)
    # 信号：筛选完成
    finished = pyqtSignal(int)  # (找到的股票数量)
    # 信号：错误信息
    error_occurred = pyqtSignal(str)  # (错误信息)
    # 信号：调试信息（用于显示统计）
    debug_info = pyqtSignal(str)  # (调试信息)
    
    def __init__(self, stock_list: List[Tuple[str, str]], trading_dates: List[date], parent=None):
        super().__init__(parent)
        self.stock_list = stock_list
        self.trading_dates = trading_dates
        self.is_running = True
        
    def stop(self):
        """停止筛选"""
        self.is_running = False
        
    def _get_limit_ratio(self, stock_code: str, as_of_date=None) -> float:
        """获取股票的涨停幅度"""
        try:
            from utils.limit_ratio import get_limit_ratio

            stock_name = get_stock_name(stock_code) or ""
            return get_limit_ratio(stock_code, stock_name, as_of_date)
        except Exception:
            return 0.10
    
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
    
    def _calculate_technical_indicators(self, daily_data: pd.DataFrame) -> pd.DataFrame:
        """计算技术指标：布林线上轨
        
        注意：计算布林线需要至少20个交易日的数据。
        在 _get_daily_data 中，我们获取了60天的数据以确保有足够的历史数据来计算布林线。
        """
        try:
            # 确保数据按日期排序
            daily_data = daily_data.sort_values('date').copy()
            
            # 计算布林线
            # 中轨：MA20（20日移动平均）
            # 使用 min_periods=1 确保即使数据不足20天也能计算（但精度会降低）
            daily_data['MA20'] = daily_data['close'].rolling(window=20, min_periods=1).mean()
            # 标准差
            daily_data['STD20'] = daily_data['close'].rolling(window=20, min_periods=1).std()
            # 上轨 = 中轨 + 2 * 标准差
            daily_data['BOLL_UPPER'] = daily_data['MA20'] + 2.0 * daily_data['STD20']
            
            return daily_data
            
        except Exception as e:
            logger.error(f"计算技术指标失败: {str(e)}", exc_info=True)
            return daily_data
    
    def _get_daily_data(self, stock_code: str, include_prev_day: bool = True) -> Optional[pd.DataFrame]:
        """获取日线数据
        
        Args:
            stock_code: 股票代码
            include_prev_day: 是否包含前一个交易日（用于计算涨停价）
                注意：当 include_prev_day=True 时，返回所有获取的数据（约60天），
                用于计算布林线等技术指标；当 include_prev_day=False 时，只返回最近10个交易日的数据。
        """
        try:
            full_stock_code = self._get_full_stock_code(stock_code)
            
            # 计算日期范围（往前推更多天数以确保获取到足够的交易日和前一个交易日，以及计算布林线需要的数据）
            # 布林线需要至少20个交易日的数据，所以需要获取足够的历史数据
            end_date = self.trading_dates[-1] if self.trading_dates else date.today()
            start_date = end_date - timedelta(days=60)  # 往前推60天，确保包含前一个交易日和足够的数据计算布林线（约40-45个交易日）
            
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
            
            # 转换时间戳为日期（考虑时区：先转换为UTC，再转换为东八区）
            time_index = pd.to_datetime(daily_data.index, unit='ms')
            # 对于DatetimeIndex，直接使用时区方法
            if time_index.tz is None:
                # 如果没有时区信息，先设置为UTC，再转换为东八区
                time_index = time_index.tz_localize('UTC').tz_convert('Asia/Shanghai')
            else:
                # 如果已有时区信息，直接转换为东八区
                time_index = time_index.tz_convert('Asia/Shanghai')
            # 取东八区的日期：将DatetimeIndex转换为Series，使用原始索引，然后取日期
            date_series = pd.Series(time_index, index=daily_data.index)
            daily_data['date'] = date_series.dt.date
            
            # 筛选交易日数据
            daily_data = daily_data[daily_data['date'].apply(is_tradeday)]
            
            # 按日期排序
            daily_data = daily_data.sort_values('date')
            
            # 计算技术指标（布林线）
            daily_data = self._calculate_technical_indicators(daily_data)
            
            # 如果只需要最近10个交易日的数据，进行筛选
            if not include_prev_day and len(daily_data) > 0:
                # 只保留在trading_dates范围内的数据
                daily_data = daily_data[daily_data['date'].isin(self.trading_dates)]
            
            return daily_data
            
        except Exception as e:
            logger.error(f"[{stock_code}] 获取日线数据异常: {str(e)}", exc_info=True)
            return None
    
    def _check_stock(self, stock_code: str, stock_name: str) -> Optional[Tuple[str, str]]:
        """检查股票是否符合条件"""
        try:
            # 获取日线数据（包含前一个交易日）
            # 注意：include_prev_day=True 时，会返回所有获取的数据（约60天），
            # 这样才有足够的历史数据来计算布林线（需要至少20个交易日）
            daily_data = self._get_daily_data(stock_code, include_prev_day=True)
            if daily_data is None or daily_data.empty:
                return None
            
            # 筛选出最近10个交易日的数据
            trading_dates_set = set(self.trading_dates)
            recent_data = daily_data[daily_data['date'].isin(trading_dates_set)]
            
            if recent_data.empty:
                return None
            
            # 获取最后一个交易日（用于排除）
            last_trading_date = recent_data['date'].max()
            
            # 按日期从早到晚排序，确保从早往后查找
            recent_data_sorted = recent_data.sort_values('date')
            
            # 遍历最近10个交易日，查找第一个接近涨停（排除最后一个交易日）
            for idx, row in recent_data_sorted.iterrows():
                trade_date = row['date']
                
                # 排除最后一个交易日，因为无法判断后续是否有更高的价格
                if trade_date >= last_trading_date:
                    continue
                
                close_price = row['close']
                high_price = row['high']
                
                # 从全部数据中找到前一个交易日
                prev_trading_days = daily_data[daily_data['date'] < trade_date]
                if prev_trading_days.empty:
                    continue  # 没有前一日数据，跳过
                
                prev_row = prev_trading_days.iloc[-1]  # 获取最后一个（最近的）前一个交易日
                prev_close = prev_row['close']
                prev_date = prev_row['date']
                
                limit_ratio = self._get_limit_ratio(stock_code, trade_date)
                
                # 计算接近涨停价（75%涨停幅度）
                # 例如：10%涨停的股票，接近涨停价 = 前收盘 * (1 + 0.10 * 0.75) = 前收盘 * 1.075
                near_limit_price = prev_close * (1 + limit_ratio * 0.75)
                
                # 判断是否接近涨停：收盘价 >= 接近涨停价
                # 允许0.01的误差，因为价格可能有微小的浮动
                if close_price < near_limit_price - 0.01:
                    continue  # 不满足接近涨停条件，继续查找
                
                # 找到第一个接近涨停，立即检查其他条件
                
                # 1. 检查收盘价是否 >= 布林线上轨
                # 获取当天的布林线上轨
                day_data = daily_data[daily_data['date'] == trade_date]
                if day_data.empty:
                    continue
                
                boll_upper = day_data['BOLL_UPPER'].iloc[0]
                # 如果收盘价 < 布林线上轨，不满足条件，直接返回None
                # 允许0.01的误差，因为价格可能有微小的浮动
                if close_price < boll_upper - 0.01:
                    return None
                
                # 2. 检查该日的最高价是否不低于之前和之后的最高价
                # 获取该日之前的交易日（在10个交易日内）的最高价
                before_day_data = recent_data_sorted[recent_data_sorted['date'] < trade_date]
                has_before_data = not before_day_data.empty
                max_high_before = before_day_data['high'].max() if has_before_data else None
                
                # 获取该日之后的交易日（包括最近10个交易日之后的数据）的最高价
                after_day_data = daily_data[daily_data['date'] > trade_date]
                if after_day_data.empty:
                    continue  # 如果该日后没有数据，跳过（不应该出现，因为已经排除了最后一个交易日）
                
                max_high_after = after_day_data['high'].max()
                
                # 如果该日的最高价 < 之前的最高价 或 < 之后的最高价，不满足条件
                # 允许0.01的误差，因为价格可能有微小的浮动
                # 如果之前没有数据，只检查之后的；如果之后没有数据，只检查之前的（但我们已经排除了最后一个交易日）
                if (has_before_data and high_price < max_high_before - 0.01) or (high_price < max_high_after - 0.01):
                    return None
                
                # 如果所有条件都满足，符合条件，返回结果（不再继续查找）
                return (trade_date.strftime('%Y-%m-%d'), f"{close_price:.2f}")
            
            return None
            
        except Exception as e:
            logger.error(f"[{stock_code}] 检查过程中出错: {str(e)}", exc_info=True)
            return None
    
    def run(self):
        """运行筛选"""
        found_count = 0
        
        try:
            total = len(self.stock_list)
            for idx, (stock_code, stock_name) in enumerate(self.stock_list):
                if not self.is_running:
                    break
                
                # 更新进度
                self.progress_updated.emit(idx + 1, total, stock_code)
                
                # 检查股票
                result = self._check_stock(stock_code, stock_name)
                if result:
                    near_limit_date, close_price = result
                    self.stock_found.emit(stock_code, stock_name, near_limit_date, close_price)
                    found_count += 1
                    # 每找到10只股票，发送一次调试信息
                    if found_count % 10 == 0:
                        self.debug_info.emit(f"已找到 {found_count} 只符合条件的股票")
                
                # 每100只股票发送一次统计信息
                if (idx + 1) % 100 == 0:
                    self.debug_info.emit(f"已筛选 {idx + 1}/{total} 只股票，找到 {found_count} 只符合条件的股票")
                
                # 短暂延迟，避免请求过快
                time.sleep(0.1)
            
            self.finished.emit(found_count)
            
        except Exception as e:
            self.error_occurred.emit(f"筛选过程出错: {str(e)}")
            self.finished.emit(found_count)


class LimitUpNearDialog(QDialog):
    """接近涨停股票筛选对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("10日内接近涨停股票筛选")
        self.setMinimumSize(800, 600)
        self.resize(1000, 700)
        
        # 去掉右上角的问号（帮助按钮）
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        
        self.filter_thread = None
        self.result_stocks = []  # 存储筛选结果
        
        self.setup_ui()
        
    def setup_ui(self):
        """设置UI"""
        layout = QVBoxLayout()
        
        # 说明标签
        info_label = QLabel("筛选条件：最近10个交易日内有接近涨停板（收盘价达到涨停比率的75%，例如10%涨停的股票收盘价达到7.5%以上），该日收盘价大于等于布林线上轨，且该日的最高价不低于之前和之后的最高价")
        info_label.setWordWrap(True)
        font = QFont()
        font.setPointSize(10)
        info_label.setFont(font)
        layout.addWidget(info_label)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        
        self.start_button = QPushButton("开始运行")
        self.start_button.clicked.connect(self.start_filter)
        button_layout.addWidget(self.start_button)
        
        self.stop_button = QPushButton("结束")
        self.stop_button.clicked.connect(self.stop_filter)
        self.stop_button.setEnabled(False)
        button_layout.addWidget(self.stop_button)
        
        self.save_button = QPushButton("保存")
        self.save_button.clicked.connect(self.save_results)
        self.save_button.setEnabled(False)
        button_layout.addWidget(self.save_button)
        
        button_layout.addStretch()
        layout.addLayout(button_layout)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        
        # 状态标签
        self.status_label = QLabel("就绪")
        layout.addWidget(self.status_label)
        
        # 结果表格
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(4)
        self.result_table.setHorizontalHeaderLabels(["股票代码", "股票名称", "接近涨停日期", "收盘价"])
        
        # 设置表格属性
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        
        layout.addWidget(self.result_table)
        
        self.setLayout(layout)
    
    def _get_trading_dates(self) -> List[date]:
        """获取最近10个交易日"""
        try:
            current_time = datetime.now()
            current_date = current_time.date()
            current_hour = current_time.hour
            
            # 判断是否包含当天
            # 如果是交易日的下午3点以后，包含当天；否则不包含
            include_today = False
            if is_tradeday(current_date):
                if current_hour >= 15:
                    include_today = True
            
            trading_dates = []
            search_date = current_date if include_today else current_date - timedelta(days=1)
            found_days = 0
            max_search_days = 30  # 最多往前找30天
            
            while found_days < 10 and max_search_days > 0:
                if is_tradeday(search_date):
                    trading_dates.append(search_date)
                    found_days += 1
                search_date -= timedelta(days=1)
                max_search_days -= 1
            
            # 按时间顺序排列（从早到晚）
            trading_dates.sort()
            
            return trading_dates
            
        except Exception as e:
            return []
    
    def _load_stock_list(self) -> List[Tuple[str, str]]:
        """从CSV文件加载股票列表"""
        csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'all_a_stocks.csv')
        if not os.path.exists(csv_path):
            QMessageBox.warning(self, "错误", f"找不到股票列表文件: {csv_path}")
            return []
        
        # 尝试多种编码方式
        encodings = ['utf-8', 'gbk', 'gb2312', 'gb18030', 'utf-8-sig']
        df = None
        last_error = None
        
        for encoding in encodings:
            try:
                df = pd.read_csv(csv_path, encoding=encoding)
                break  # 成功读取，跳出循环
            except (UnicodeDecodeError, UnicodeError) as e:
                last_error = e
                continue  # 尝试下一个编码
            except Exception as e:
                last_error = e
                continue
        
        if df is None:
            QMessageBox.warning(self, "错误", f"加载股票列表失败: 无法使用常见编码（utf-8, gbk, gb2312, gb18030）读取文件\n最后错误: {str(last_error)}")
            return []
        
        try:
            stock_list = []
            
            for _, row in df.iterrows():
                stock_code = str(row['证券代码']).zfill(6)  # 确保是6位数字
                # 剔除5开头的股票代码
                if stock_code.startswith('5'):
                    continue
                stock_name = str(row['证券简称'])
                stock_list.append((stock_code, stock_name))
            
            return stock_list
            
        except Exception as e:
            QMessageBox.warning(self, "错误", f"解析股票列表失败: {str(e)}")
            return []
    
    def start_filter(self):
        """开始筛选"""
        try:
            # 加载股票列表
            stock_list = self._load_stock_list()
            if not stock_list:
                return
            
            # 获取交易日列表
            trading_dates = self._get_trading_dates()
            if len(trading_dates) < 10:
                QMessageBox.warning(self, "警告", f"无法获取足够的交易日（只找到{len(trading_dates)}个）")
                return
            
            # 清空结果
            self.result_stocks = []
            self.result_table.setRowCount(0)
            
            # 创建筛选线程
            self.filter_thread = LimitUpNearFilterThread(stock_list, trading_dates, self)
            self.filter_thread.progress_updated.connect(self.on_progress_updated)
            self.filter_thread.stock_found.connect(self.on_stock_found)
            self.filter_thread.finished.connect(self.on_finished)
            self.filter_thread.error_occurred.connect(self.on_error)
            self.filter_thread.debug_info.connect(self.on_debug_info)
            
            # 更新按钮状态
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.save_button.setEnabled(False)
            
            # 启动线程
            self.filter_thread.start()
            
        except Exception as e:
            QMessageBox.warning(self, "错误", f"启动筛选失败: {str(e)}")
    
    def stop_filter(self):
        """停止筛选"""
        if self.filter_thread and self.filter_thread.isRunning():
            self.filter_thread.stop()
            self.filter_thread.wait()
            self.status_label.setText("已停止")
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
    
    def on_progress_updated(self, current: int, total: int, stock_code: str):
        """更新进度"""
        progress = int((current / total) * 100) if total > 0 else 0
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.status_label.setText(f"正在筛选: {stock_code} ({current}/{total})")
    
    def on_stock_found(self, stock_code: str, stock_name: str, near_limit_date: str, close_price: str):
        """找到符合条件的股票"""
        # 添加到结果列表
        self.result_stocks.append({
            'code': stock_code,
            'name': stock_name,
            'near_limit_date': near_limit_date,
            'close_price': close_price
        })
        
        # 添加到表格
        row = self.result_table.rowCount()
        self.result_table.insertRow(row)
        
        self.result_table.setItem(row, 0, QTableWidgetItem(stock_code))
        self.result_table.setItem(row, 1, QTableWidgetItem(stock_name))
        self.result_table.setItem(row, 2, QTableWidgetItem(near_limit_date))
        self.result_table.setItem(row, 3, QTableWidgetItem(close_price))
        
        # 滚动到最新添加的行，确保用户能看到
        self.result_table.scrollToItem(self.result_table.item(row, 0))
        
        # 更新状态标签显示最新找到的股票
        self.status_label.setText(f"找到符合条件的股票: {stock_code} {stock_name} (接近涨停日期: {near_limit_date}, 收盘价: {close_price})")
    
    def on_finished(self, count: int):
        """筛选完成"""
        self.status_label.setText(f"筛选完成，找到 {count} 只符合条件的股票")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.save_button.setEnabled(True)
    
    def on_error(self, error_msg: str):
        """处理错误"""
        QMessageBox.warning(self, "错误", error_msg)
    
    def on_debug_info(self, info: str):
        """处理调试信息"""
        # 在状态标签中显示调试信息
        self.status_label.setText(info)
    
    def save_results(self):
        """保存结果"""
        if not self.result_stocks:
            QMessageBox.information(self, "提示", "没有可保存的结果")
            return
        
        try:
            # 选择保存路径
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "保存结果",
                f"接近涨停股票_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                "CSV文件 (*.csv);;所有文件 (*.*)"
            )
            
            if not file_path:
                return
            
            # 保存为CSV
            df = pd.DataFrame(self.result_stocks)
            df.to_csv(file_path, index=False, encoding='utf-8-sig')
            
            QMessageBox.information(self, "成功", f"结果已保存到: {file_path}")
            
        except Exception as e:
            QMessageBox.warning(self, "错误", f"保存失败: {str(e)}")
    
    def closeEvent(self, event):
        """关闭事件"""
        if self.filter_thread and self.filter_thread.isRunning():
            reply = QMessageBox.question(
                self,
                "确认",
                "筛选正在进行中，确定要关闭吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                self.stop_filter()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

