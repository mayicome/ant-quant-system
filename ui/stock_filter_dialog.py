#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
选股器对话框 - 根据板块组合筛选股票
"""

import os
import json
import csv
from typing import List, Dict, Set, Tuple
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                             QMessageBox, QFileDialog, QSpinBox, QFormLayout,
                             QHeaderView, QTextEdit)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont
from utils.logger import Logger


class FilterWorker(QThread):
    """筛选工作线程"""
    finished = pyqtSignal(list)  # 筛选结果
    progress = pyqtSignal(str)  # 进度信息
    
    def __init__(self, stock_info_dict: Dict, input_plates: List[str], min_match_count: int):
        super().__init__()
        self.stock_info_dict = stock_info_dict
        self.input_plates = input_plates
        self.min_match_count = min_match_count
        # 初始化股票名称缓存
        self._stock_name_cache = {}
    
    def _get_stock_name(self, stock_code: str) -> str:
        """获取股票名称"""
        if stock_code in self._stock_name_cache:
            return self._stock_name_cache[stock_code]
        
        try:
            from utils.stock_info_manager import get_stock_name
            name = get_stock_name(stock_code)
            self._stock_name_cache[stock_code] = name
            return name
        except Exception:
            self._stock_name_cache[stock_code] = '未知名称'
            return '未知名称'
    
    def run(self):
        """执行筛选"""
        results = []
        input_plates_set = set(self.input_plates)
        
        total = len(self.stock_info_dict)
        processed = 0
        
        for stock_code, stock_info in self.stock_info_dict.items():
            processed += 1
            if processed % 100 == 0:
                self.progress.emit(f"正在筛选... {processed}/{total}")
            
            # 合并股票的行业、概念、板块
            stock_plates_set = set()
            
            # 添加行业（如果有）
            industry = stock_info.get('industry', '')
            if industry:
                stock_plates_set.add(industry)
            
            # 添加概念（如果有）
            concepts = stock_info.get('concepts', [])
            if concepts:
                stock_plates_set.update(concepts)
            
            # 添加板块（如果有）
            plates = stock_info.get('plates', [])
            if plates:
                stock_plates_set.update(plates)
            
            # 计算交集
            intersection = stock_plates_set & input_plates_set
            match_count = len(intersection)
            
            # 判断是否满足条件
            if match_count >= self.min_match_count:
                # 获取股票名称
                stock_name = self._get_stock_name(stock_code)
                
                results.append({
                    'code': stock_code,
                    'name': stock_name,
                    'match_count': match_count,
                    'matched_plates': list(intersection)
                })
        
        self.progress.emit(f"筛选完成，找到 {len(results)} 只股票")
        self.finished.emit(results)


class StockFilterDialog(QDialog):
    """选股器对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.logger = Logger()
        self.stock_info_dict = {}
        self.filter_results = []
        self.filter_worker = None
        
        self.init_ui()
        self.load_stock_info()
    
    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle("选股器 - 板块组合筛选")
        self.setMinimumSize(800, 600)
        
        layout = QVBoxLayout()
        
        # 输入区域
        input_layout = QFormLayout()
        
        # 板块输入
        self.plates_input = QLineEdit()
        self.plates_input.setPlaceholderText("例如: 航天航空,广东板块,工业母机")
        self.plates_input.setMinimumWidth(400)
        input_layout.addRow("板块组合:", self.plates_input)
        
        # 匹配数量
        self.match_count_spin = QSpinBox()
        self.match_count_spin.setMinimum(1)
        self.match_count_spin.setMaximum(100)
        self.match_count_spin.setValue(1)
        self.match_count_spin.setToolTip("1=包含任意一个板块即可\n2=包含任意两个板块\n3=包含任意三个板块\n以此类推")
        input_layout.addRow("匹配数量:", self.match_count_spin)
        
        # 说明文本
        help_text = QTextEdit()
        help_text.setReadOnly(True)
        help_text.setMaximumHeight(80)
        help_text.setPlainText(
            "说明：\n"
            "• 板块组合：用逗号分隔多个板块名称（例如：航天航空,广东板块,工业母机）\n"
            "• 匹配数量：1=包含任意一个板块即可，2=包含任意两个板块，3=包含任意三个板块，以此类推\n"
            "• 每只股票会合并其行业、概念、板块信息进行匹配"
        )
        input_layout.addRow("", help_text)
        
        layout.addLayout(input_layout)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        
        self.filter_btn = QPushButton("开始筛选")
        self.filter_btn.clicked.connect(self.start_filter)
        button_layout.addWidget(self.filter_btn)
        
        self.export_btn = QPushButton("导出CSV")
        self.export_btn.clicked.connect(self.export_to_csv)
        self.export_btn.setEnabled(False)
        button_layout.addWidget(self.export_btn)
        
        button_layout.addStretch()
        
        # 状态标签
        self.status_label = QLabel("就绪")
        button_layout.addWidget(self.status_label)
        
        layout.addLayout(button_layout)
        
        # 结果表格
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(4)
        self.result_table.setHorizontalHeaderLabels(["股票代码", "股票名称", "匹配数量", "匹配的板块"])
        self.result_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.result_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.result_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.result_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.result_table.setAlternatingRowColors(True)
        self.result_table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.result_table)
        
        self.setLayout(layout)
    
    def load_stock_info(self):
        """加载股票信息"""
        data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
        json_path = os.path.join(data_dir, 'all_a_stock_info.json')
        if not os.path.exists(json_path):
            QMessageBox.warning(self, "错误", f"股票信息文件不存在: {json_path}")
            return False
        
        try:
            self.status_label.setText("正在加载股票信息...")
            with open(json_path, 'r', encoding='utf-8') as f:
                self.stock_info_dict = json.load(f)
            self.status_label.setText(f"已加载 {len(self.stock_info_dict)} 只股票信息")
            self.logger.info(f"成功加载 {len(self.stock_info_dict)} 只股票信息")
            return True
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载股票信息失败: {str(e)}")
            self.logger.error(f"加载股票信息失败: {str(e)}", exc_info=True)
            self.status_label.setText("加载失败")
            return False
    
    def start_filter(self):
        """开始筛选"""
        # 获取输入
        plates_text = self.plates_input.text().strip()
        if not plates_text:
            QMessageBox.warning(self, "提示", "请输入板块组合")
            return
        
        # 解析板块列表
        input_plates = [plate.strip() for plate in plates_text.split(',') if plate.strip()]
        if not input_plates:
            QMessageBox.warning(self, "提示", "请输入有效的板块名称")
            return
        
        min_match_count = self.match_count_spin.value()
        
        # 检查是否已有工作线程在运行
        if self.filter_worker and self.filter_worker.isRunning():
            QMessageBox.warning(self, "提示", "筛选正在进行中，请稍候...")
            return
        
        # 禁用按钮
        self.filter_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.status_label.setText("正在筛选...")
        
        # 清空结果表格
        self.result_table.setRowCount(0)
        
        # 创建工作线程
        self.filter_worker = FilterWorker(self.stock_info_dict, input_plates, min_match_count)
        self.filter_worker.finished.connect(self.on_filter_finished)
        self.filter_worker.progress.connect(self.on_filter_progress)
        self.filter_worker.start()
    
    def on_filter_progress(self, message: str):
        """筛选进度更新"""
        self.status_label.setText(message)
    
    def on_filter_finished(self, results: List[Dict]):
        """筛选完成"""
        self.filter_results = results
        
        # 按匹配数量降序排序
        results.sort(key=lambda x: x['match_count'], reverse=True)
        
        # 更新表格
        self.result_table.setRowCount(len(results))
        for row, result in enumerate(results):
            # 股票代码
            code_item = QTableWidgetItem(result['code'])
            code_item.setTextAlignment(Qt.AlignCenter)
            self.result_table.setItem(row, 0, code_item)
            
            # 股票名称
            name_item = QTableWidgetItem(result['name'] or '未知')
            self.result_table.setItem(row, 1, name_item)
            
            # 匹配数量
            match_item = QTableWidgetItem(str(result['match_count']))
            match_item.setTextAlignment(Qt.AlignCenter)
            self.result_table.setItem(row, 2, match_item)
            
            # 匹配的板块
            plates_text = ', '.join(result['matched_plates'])
            plates_item = QTableWidgetItem(plates_text)
            self.result_table.setItem(row, 3, plates_item)
        
        # 启用按钮
        self.filter_btn.setEnabled(True)
        self.export_btn.setEnabled(len(results) > 0)
        self.status_label.setText(f"筛选完成，找到 {len(results)} 只股票")
    
    def export_to_csv(self):
        """导出为CSV文件"""
        if not self.filter_results:
            QMessageBox.warning(self, "提示", "没有可导出的数据")
            return
        
        # 选择保存路径
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出CSV文件",
            "选股结果.csv",
            "CSV文件 (*.csv)"
        )
        
        if not file_path:
            return
        
        try:
            # 按匹配数量降序排序
            sorted_results = sorted(self.filter_results, key=lambda x: x['match_count'], reverse=True)
            
            # 写入CSV
            with open(file_path, 'w', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f)
                # 写入表头
                writer.writerow(['股票代码', '股票名称', '匹配数量', '匹配的板块'])
                # 写入数据
                for result in sorted_results:
                    plates_text = ', '.join(result['matched_plates'])
                    writer.writerow([
                        result['code'],
                        result['name'] or '未知',
                        result['match_count'],
                        plates_text
                    ])
            
            QMessageBox.information(self, "成功", f"已导出 {len(sorted_results)} 只股票到:\n{file_path}")
            self.logger.info(f"导出选股结果到: {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导出失败: {str(e)}")
            self.logger.error(f"导出CSV失败: {str(e)}", exc_info=True)


if __name__ == '__main__':
    import sys
    from PyQt5.QtWidgets import QApplication
    
    app = QApplication(sys.argv)
    
    dialog = StockFilterDialog()
    dialog.show()
    
    sys.exit(app.exec_())

