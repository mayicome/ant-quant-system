#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
主力净流入数据获取对话框
从CSV文件导入股票代码、名称和日期，获取从指定日期以来的所有交易日的主力净流入数据
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
    from utils.trading_day import is_tradeday
    import akshare as ak
except ImportError as e:
    print(f"导入模块失败: {e}")
    ak = None

# 配置日志
_LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(_LOGS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(_LOGS_DIR, 'capital_flow.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class CapitalFlowFetchThread(QThread):
    """主力净流入数据获取线程"""
    
    # 信号：更新进度
    progress_updated = pyqtSignal(int, int, str)  # (当前数量, 总数, 当前股票代码)
    # 信号：获取到数据
    data_fetched = pyqtSignal(str, str, str, list)  # (股票代码, 股票名称, 开始日期, 数据列表)
    # 信号：获取完成
    finished = pyqtSignal(int)  # (成功获取的股票数量)
    # 信号：错误信息
    error_occurred = pyqtSignal(str)  # (错误信息)
    
    def __init__(self, stock_list: List[Dict], parent=None):
        super().__init__(parent)
        self.stock_list = stock_list  # 每个元素包含: {'code': str, 'name': str, 'date': str}
        self.is_running = True
        
    def stop(self):
        """停止获取"""
        self.is_running = False
        
    def _get_trading_dates(self, start_date: date, end_date: date) -> List[date]:
        """获取指定日期范围内的所有交易日"""
        trading_dates = []
        current_date = start_date
        
        while current_date <= end_date:
            if is_tradeday(current_date):
                trading_dates.append(current_date)
            current_date += timedelta(days=1)
        
        return trading_dates
    
    def run(self):
        """运行线程"""
        if ak is None:
            self.error_occurred.emit("akshare模块未安装或导入失败")
            return
        
        success_count = 0
        total = len(self.stock_list)
        
        for idx, stock_info in enumerate(self.stock_list):
            if not self.is_running:
                break
            
            stock_code = stock_info['code']
            stock_name = stock_info['name']
            start_date_str = stock_info['date']
            
            try:
                # 解析开始日期
                if isinstance(start_date_str, str):
                    # 尝试多种日期格式
                    date_formats = ['%Y-%m-%d', '%Y/%m/%d', '%Y%m%d']
                    start_date_obj = None
                    for fmt in date_formats:
                        try:
                            start_date_obj = datetime.strptime(start_date_str, fmt).date()
                            break
                        except ValueError:
                            continue
                    
                    if start_date_obj is None:
                        logger.warning(f"[{stock_code}] 无法解析日期: {start_date_str}")
                        self.progress_updated.emit(idx + 1, total, f"{stock_code} - 日期解析失败")
                        continue
                else:
                    start_date_obj = start_date_str
                
                # 结束日期为今天
                end_date_obj = date.today()
                
                # 获取交易日列表
                trading_dates = self._get_trading_dates(start_date_obj, end_date_obj)
                
                if not trading_dates:
                    logger.warning(f"[{stock_code}] 指定日期范围内没有交易日")
                    self.progress_updated.emit(idx + 1, total, f"{stock_code} - 无交易日")
                    continue
                
                # 使用akshare获取资金流向数据
                try:
                    # stock_capital_flow_em 参数：symbol为股票代码（6位数字），start_date和end_date为日期字符串
                    # 使用东方财富数据源（函数名中的em对应East Money）
                    df = ak.stock_capital_flow_em(
                        symbol=stock_code,
                        start_date=start_date_obj.strftime('%Y-%m-%d'),
                        end_date=end_date_obj.strftime('%Y-%m-%d')
                    )
                    
                    if df is None or df.empty:
                        logger.warning(f"[{stock_code}] 未获取到资金流向数据（可能是非交易日）")
                        self.progress_updated.emit(idx + 1, total, f"{stock_code} - 无数据")
                        continue
                    
                    # 处理数据：转换为列表格式
                    capital_flow_data = []
                    
                    # 查找日期列和主力净流入列
                    # 新接口返回的列名：'日期', '主力净流入-净额(万元)', '主力净流入-占比(%)'
                    date_col = None
                    main_flow_col = None
                    
                    for col in df.columns:
                        col_str = str(col)
                        if '日期' in col_str or 'date' in col_str.lower() or '时间' in col_str:
                            date_col = col
                        # 匹配新接口的列名：主力净流入-净额(万元)
                        if '主力净流入' in col_str and ('净额' in col_str or '万元' in col_str):
                            main_flow_col = col
                        # 兼容旧格式
                        elif '主力净流入' in col_str or ('main' in col_str.lower() and 'inflow' in col_str.lower()):
                            main_flow_col = col
                    
                    if date_col is None or main_flow_col is None:
                        logger.warning(f"[{stock_code}] 未找到日期列或主力净流入列，可用列: {list(df.columns)}")
                        self.progress_updated.emit(idx + 1, total, f"{stock_code} - 列名不匹配")
                        continue
                    
                    # 遍历数据行
                    for _, row in df.iterrows():
                        trade_date = row[date_col]
                        main_flow = row[main_flow_col]
                        
                        # 转换为日期对象
                        if isinstance(trade_date, str):
                            try:
                                trade_date_obj = pd.to_datetime(trade_date).date()
                            except:
                                continue
                        else:
                            trade_date_obj = pd.to_datetime(trade_date).date()
                        
                        # 转换为数值（万元）
                        # 新接口返回的数据已经是万元单位，直接使用
                        try:
                            if isinstance(main_flow, str):
                                # 移除可能的单位符号和逗号
                                main_flow = main_flow.replace('万', '').replace('元', '').replace(',', '').replace('，', '').replace('(', '').replace(')', '').strip()
                            main_flow_value = float(main_flow)
                            
                            # 新接口返回的数据已经是万元单位，不需要转换
                            # 但如果数值很大（可能是以元为单位），则转换为万元
                            if abs(main_flow_value) > 1000000:  # 如果数值很大，可能是以元为单位
                                main_flow_value = main_flow_value / 10000
                            
                            capital_flow_data.append({
                                'date': trade_date_obj.strftime('%Y-%m-%d'),
                                'main_net_inflow': round(main_flow_value, 2)
                            })
                        except (ValueError, TypeError) as e:
                            logger.warning(f"[{stock_code}] 转换主力净流入数值失败: {e}")
                            continue
                    
                    # 按日期排序
                    capital_flow_data.sort(key=lambda x: x['date'])
                    
                    # 发送数据信号
                    self.data_fetched.emit(stock_code, stock_name, start_date_str, capital_flow_data)
                    success_count += 1
                    
                    logger.info(f"[{stock_code}] 成功获取 {len(capital_flow_data)} 条资金流向数据")
                    
                except Exception as e:
                    logger.error(f"[{stock_code}] 获取资金流向数据失败: {str(e)}")
                    self.progress_updated.emit(idx + 1, total, f"{stock_code} - 获取失败: {str(e)[:30]}")
                    continue
                
            except Exception as e:
                logger.error(f"[{stock_code}] 处理失败: {str(e)}")
                self.progress_updated.emit(idx + 1, total, f"{stock_code} - 处理失败")
                continue
            
            # 更新进度
            self.progress_updated.emit(idx + 1, total, stock_code)
            
            # 添加延迟，避免请求过快
            time.sleep(0.5)
        
        # 发送完成信号
        self.finished.emit(success_count)


class CapitalFlowDialog(QDialog):
    """主力净流入数据获取对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("主力净流入数据获取")
        self.setMinimumSize(1000, 700)
        self.resize(1200, 800)
        
        # 去掉右上角的问号（帮助按钮）
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        
        self.fetch_thread = None
        self.stock_list = []  # 存储导入的股票列表
        self.result_data = {}  # 存储获取的数据 {stock_code: [数据列表]}
        
        self.setup_ui()
        
    def setup_ui(self):
        """设置UI"""
        layout = QVBoxLayout()
        
        # 说明标签
        info_label = QLabel("功能说明：从CSV文件导入股票代码、名称和日期，获取从指定日期以来的所有交易日的主力净流入数据")
        info_label.setWordWrap(True)
        font = QFont()
        font.setPointSize(10)
        info_label.setFont(font)
        layout.addWidget(info_label)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        
        self.import_button = QPushButton("导入文件")
        self.import_button.clicked.connect(self.import_file)
        button_layout.addWidget(self.import_button)
        
        self.start_button = QPushButton("开始获取")
        self.start_button.clicked.connect(self.start_fetch)
        self.start_button.setEnabled(False)
        button_layout.addWidget(self.start_button)
        
        self.stop_button = QPushButton("停止")
        self.stop_button.clicked.connect(self.stop_fetch)
        self.stop_button.setEnabled(False)
        button_layout.addWidget(self.stop_button)
        
        self.save_button = QPushButton("保存结果")
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
        self.status_label = QLabel("就绪 - 请先导入文件")
        layout.addWidget(self.status_label)
        
        # 股票列表表格
        stock_list_label = QLabel("导入的股票列表：")
        layout.addWidget(stock_list_label)
        
        self.stock_table = QTableWidget()
        self.stock_table.setColumnCount(3)
        self.stock_table.setHorizontalHeaderLabels(["股票代码", "股票名称", "开始日期"])
        
        # 设置表格属性
        header = self.stock_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        
        layout.addWidget(self.stock_table)
        
        # 结果表格
        result_label = QLabel("获取结果：")
        layout.addWidget(result_label)
        
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(4)
        self.result_table.setHorizontalHeaderLabels(["股票代码", "股票名称", "日期", "主力净流入(万元)"])
        
        # 设置表格属性
        result_header = self.result_table.horizontalHeader()
        result_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        result_header.setSectionResizeMode(1, QHeaderView.Stretch)
        result_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        result_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        
        layout.addWidget(self.result_table)
        
        self.setLayout(layout)
    
    def import_file(self):
        """导入CSV文件"""
        try:
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "选择CSV文件",
                "",
                "CSV文件 (*.csv);;所有文件 (*.*)"
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
                QMessageBox.warning(self, "错误", f"读取文件失败: 无法使用常见编码读取文件\n最后错误: {str(last_error)}")
                return
            
            # 查找股票代码、名称和日期列
            code_col = None
            name_col = None
            date_col = None
            
            # 可能的列名
            code_names = ['代码', '股票代码', 'code', '证券代码', '股票代码']
            name_names = ['名称', '股票名称', 'name', '证券名称', '股票名称']
            date_names = ['日期', '接近涨停日期', 'date', '开始日期', '涨停日期', 'near_limit_date']
            
            for col in df.columns:
                col_str = str(col).strip()
                if col_str in code_names or '代码' in col_str:
                    code_col = col
                if col_str in name_names or ('名称' in col_str and '代码' not in col_str):
                    name_col = col
                if col_str in date_names or '日期' in col_str:
                    date_col = col
            
            if code_col is None:
                QMessageBox.warning(self, "错误", f"未找到股票代码列，可用列: {list(df.columns)}")
                return
            
            if date_col is None:
                QMessageBox.warning(self, "错误", f"未找到日期列，可用列: {list(df.columns)}")
                return
            
            # 解析数据
            self.stock_list = []
            for _, row in df.iterrows():
                stock_code = str(row[code_col]).strip().zfill(6)  # 确保是6位数字
                stock_name = str(row[name_col]).strip() if name_col else ""
                stock_date = str(row[date_col]).strip()
                
                self.stock_list.append({
                    'code': stock_code,
                    'name': stock_name,
                    'date': stock_date
                })
            
            # 更新股票列表表格
            self.update_stock_table()
            
            # 启用开始按钮
            self.start_button.setEnabled(len(self.stock_list) > 0)
            
            self.status_label.setText(f"已导入 {len(self.stock_list)} 只股票")
            QMessageBox.information(self, "成功", f"成功导入 {len(self.stock_list)} 只股票")
            
        except Exception as e:
            QMessageBox.warning(self, "错误", f"导入文件失败: {str(e)}")
            logger.error(f"导入文件失败: {str(e)}")
    
    def update_stock_table(self):
        """更新股票列表表格"""
        self.stock_table.setRowCount(len(self.stock_list))
        
        for idx, stock_info in enumerate(self.stock_list):
            self.stock_table.setItem(idx, 0, QTableWidgetItem(stock_info['code']))
            self.stock_table.setItem(idx, 1, QTableWidgetItem(stock_info['name']))
            self.stock_table.setItem(idx, 2, QTableWidgetItem(stock_info['date']))
    
    def start_fetch(self):
        """开始获取数据"""
        if not self.stock_list:
            QMessageBox.warning(self, "提示", "请先导入股票列表")
            return
        
        if ak is None:
            QMessageBox.warning(self, "错误", "akshare模块未安装或导入失败")
            return
        
        # 清空之前的结果
        self.result_data = {}
        self.result_table.setRowCount(0)
        
        # 创建并启动线程
        self.fetch_thread = CapitalFlowFetchThread(self.stock_list, self)
        self.fetch_thread.progress_updated.connect(self.on_progress_updated)
        self.fetch_thread.data_fetched.connect(self.on_data_fetched)
        self.fetch_thread.finished.connect(self.on_finished)
        self.fetch_thread.error_occurred.connect(self.on_error)
        
        self.fetch_thread.start()
        
        # 更新UI状态
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.import_button.setEnabled(False)
        self.status_label.setText("正在获取数据...")
    
    def stop_fetch(self):
        """停止获取"""
        if self.fetch_thread and self.fetch_thread.isRunning():
            self.fetch_thread.stop()
            self.fetch_thread.wait()
            self.status_label.setText("已停止")
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.import_button.setEnabled(True)
    
    def on_progress_updated(self, current: int, total: int, stock_code: str):
        """处理进度更新"""
        progress = int((current / total) * 100) if total > 0 else 0
        self.progress_bar.setValue(progress)
        self.status_label.setText(f"正在获取: {stock_code} ({current}/{total})")
    
    def on_data_fetched(self, stock_code: str, stock_name: str, start_date: str, data_list: list):
        """处理获取到的数据"""
        # 存储数据
        self.result_data[stock_code] = {
            'name': stock_name,
            'start_date': start_date,
            'data': data_list
        }
        
        # 更新结果表格
        current_row = self.result_table.rowCount()
        for data_item in data_list:
            self.result_table.insertRow(current_row)
            self.result_table.setItem(current_row, 0, QTableWidgetItem(stock_code))
            self.result_table.setItem(current_row, 1, QTableWidgetItem(stock_name))
            self.result_table.setItem(current_row, 2, QTableWidgetItem(data_item['date']))
            self.result_table.setItem(current_row, 3, QTableWidgetItem(str(data_item['main_net_inflow'])))
            current_row += 1
    
    def on_finished(self, success_count: int):
        """处理完成"""
        self.status_label.setText(f"完成！成功获取 {success_count} 只股票的数据")
        self.progress_bar.setValue(100)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.import_button.setEnabled(True)
        self.save_button.setEnabled(success_count > 0)
        
        QMessageBox.information(self, "完成", f"数据获取完成！\n成功: {success_count} 只股票")
    
    def on_error(self, error_msg: str):
        """处理错误"""
        QMessageBox.warning(self, "错误", error_msg)
    
    def save_results(self):
        """保存结果"""
        if not self.result_data:
            QMessageBox.information(self, "提示", "没有可保存的结果")
            return
        
        try:
            # 选择保存路径
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "保存结果",
                f"主力净流入数据_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                "CSV文件 (*.csv);;所有文件 (*.*)"
            )
            
            if not file_path:
                return
            
            # 准备保存的数据
            save_data = []
            for stock_code, stock_info in self.result_data.items():
                for data_item in stock_info['data']:
                    save_data.append({
                        '股票代码': stock_code,
                        '股票名称': stock_info['name'],
                        '开始日期': stock_info['start_date'],
                        '日期': data_item['date'],
                        '主力净流入(万元)': data_item['main_net_inflow']
                    })
            
            # 保存为CSV
            df = pd.DataFrame(save_data)
            df.to_csv(file_path, index=False, encoding='utf-8-sig')
            
            QMessageBox.information(self, "成功", f"结果已保存到: {file_path}")
            
        except Exception as e:
            QMessageBox.warning(self, "错误", f"保存失败: {str(e)}")
            logger.error(f"保存失败: {str(e)}")
    
    def closeEvent(self, event):
        """关闭事件"""
        if self.fetch_thread and self.fetch_thread.isRunning():
            reply = QMessageBox.question(
                self,
                "确认",
                "数据获取正在进行中，确定要关闭吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                self.stop_fetch()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()


if __name__ == '__main__':
    from PyQt5.QtWidgets import QApplication
    
    app = QApplication(sys.argv)
    dialog = CapitalFlowDialog()
    dialog.show()
    sys.exit(app.exec_())

