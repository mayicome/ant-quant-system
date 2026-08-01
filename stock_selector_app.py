#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
独立选股程序 - 根据板块组合筛选股票
可以独立运行，不依赖主程序
"""

import os
import sys
import json
import csv
from typing import List, Dict, Set
from PyQt5.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, 
                             QWidget, QLabel, QLineEdit, QPushButton, QTableWidget, 
                             QTableWidgetItem, QMessageBox, QFileDialog, QSpinBox, 
                             QFormLayout, QHeaderView, QTextEdit, QProgressBar)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QIcon


class FilterWorker(QThread):
    """筛选工作线程"""
    finished = pyqtSignal(list)  # 筛选结果
    progress = pyqtSignal(str)  # 进度信息
    
    def __init__(self, stock_info_dict: Dict, input_plates: List[str], min_match_count: int):
        super().__init__()
        self.stock_info_dict = stock_info_dict
        self.input_plates = input_plates
        self.min_match_count = min_match_count
    
    def _get_stock_name(self, stock_code: str) -> str:
        """获取股票名称"""
        # 确保股票代码是字符串
        stock_code = str(stock_code).strip()
        
        # 从 stock_info_dict 获取名称
        try:
            stock_info = self.stock_info_dict.get(stock_code, {})
            # 如果完整代码没有，尝试去掉后缀
            if not stock_info:
                code_clean = stock_code.split('.')[0]
                stock_info = self.stock_info_dict.get(code_clean, {})
            
            name = stock_info.get('name', '')
            if name:
                return name
        except Exception:
            pass
        
        # 如果获取不到，返回默认值
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
            
            # 确保股票代码是字符串
            stock_code = str(stock_code).strip()
            
            # 合并股票的行业、概念、板块
            stock_plates_set = set()
            
            # 添加行业（如果有）
            industry = stock_info.get('industry', '')
            if industry:
                stock_plates_set.add(industry)
            
            # 添加概念（如果有）
            concepts = stock_info.get('concepts', [])
            if concepts:
                if isinstance(concepts, list):
                    stock_plates_set.update(concepts)
                elif isinstance(concepts, str):
                    # 如果是字符串，尝试分割
                    stock_plates_set.update([c.strip() for c in concepts.split(',') if c.strip()])
            
            # 添加板块（如果有）
            plates = stock_info.get('plates', [])
            if plates:
                if isinstance(plates, list):
                    stock_plates_set.update(plates)
                elif isinstance(plates, str):
                    # 如果是字符串，尝试分割
                    stock_plates_set.update([p.strip() for p in plates.split(',') if p.strip()])
            
            # 计算交集
            intersection = stock_plates_set & input_plates_set
            match_count = len(intersection)
            
            # 判断是否满足条件
            if match_count >= self.min_match_count:
                # 获取股票名称
                stock_name = self._get_stock_name(stock_code)
                
                results.append({
                    'code': stock_code,  # 确保是字符串
                    'name': stock_name,
                    'match_count': match_count,
                    'matched_plates': list(intersection)
                })
        
        self.progress.emit(f"筛选完成，找到 {len(results)} 只股票")
        self.finished.emit(results)


class StockSelectorApp(QMainWindow):
    """独立选股程序主窗口"""
    
    def __init__(self):
        super().__init__()
        self.stock_info_dict = {}
        self.filter_results = []
        self.filter_worker = None
        
        # 获取程序所在目录
        self.app_dir = os.path.dirname(os.path.abspath(__file__))
        
        self.init_ui()
        self.load_stock_info()
    
    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle("选股器 - 板块组合筛选工具")
        self.setMinimumSize(1000, 700)
        
        # 创建中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        layout = QVBoxLayout()
        central_widget.setLayout(layout)
        
        # 标题
        title_label = QLabel("股票筛选工具")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)
        
        # 输入区域
        input_layout = QFormLayout()
        
        # 板块输入
        self.plates_input = QLineEdit()
        self.plates_input.setPlaceholderText("例如: 航天航空,广东板块,工业母机")
        self.plates_input.setMinimumWidth(500)
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
        help_text.setMaximumHeight(100)
        help_text.setPlainText(
            "使用说明：\n"
            "• 板块组合：用逗号分隔多个板块名称（例如：航天航空,广东板块,工业母机）\n"
            "• 匹配数量：1=包含任意一个板块即可，2=包含任意两个板块，3=包含任意三个板块，以此类推\n"
            "• 每只股票会合并其行业、概念、板块信息进行匹配\n"
            "• 筛选结果可以导出为CSV文件"
        )
        input_layout.addRow("", help_text)
        
        layout.addLayout(input_layout)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        
        self.filter_btn = QPushButton("开始筛选")
        self.filter_btn.setMinimumHeight(35)
        self.filter_btn.clicked.connect(self.start_filter)
        button_layout.addWidget(self.filter_btn)
        
        self.export_btn = QPushButton("导出CSV")
        self.export_btn.setMinimumHeight(35)
        self.export_btn.clicked.connect(self.export_to_csv)
        self.export_btn.setEnabled(False)
        button_layout.addWidget(self.export_btn)
        
        button_layout.addStretch()
        
        # 状态标签
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet("color: #666; font-size: 12px;")
        button_layout.addWidget(self.status_label)
        
        layout.addLayout(button_layout)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        # 结果表格
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(4)
        self.result_table.setHorizontalHeaderLabels(["股票代码", "股票名称", "匹配数量", "匹配的板块"])
        
        # 设置列宽
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        
        self.result_table.setAlternatingRowColors(True)
        self.result_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.result_table.setEditTriggers(QTableWidget.NoEditTriggers)
        
        # 设置表格样式
        self.result_table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #ddd;
                gridline-color: #eee;
            }
            QTableWidget::item {
                padding: 5px;
            }
            QHeaderView::section {
                background-color: #f0f0f0;
                padding: 8px;
                border: 1px solid #ddd;
                font-weight: bold;
            }
        """)
        
        layout.addWidget(self.result_table)
        
        # 结果统计
        self.result_count_label = QLabel("")
        self.result_count_label.setAlignment(Qt.AlignRight)
        self.result_count_label.setStyleSheet("color: #666; font-size: 11px; padding: 5px;")
        layout.addWidget(self.result_count_label)
    
    def load_stock_info(self):
        """加载股票信息"""
        # 尝试多个可能的路径
        json_paths = [
            os.path.join(self.app_dir, 'data', 'all_a_stock_info.json'),
            os.path.join(self.app_dir, 'all_a_stock_info.json'),
        ]
        
        json_path = None
        for path in json_paths:
            if os.path.exists(path):
                json_path = path
                break
        
        if not json_path:
            QMessageBox.critical(
                self, 
                "错误", 
                f"股票信息文件不存在！\n\n"
                f"请确保以下位置之一存在 all_a_stock_info.json 文件：\n"
                f"• {json_paths[0]}\n"
                f"• {json_paths[1]}\n"
                f"• {json_paths[2]}"
            )
            self.status_label.setText("加载失败：文件不存在")
            return False
        
        try:
            self.status_label.setText("正在加载股票信息...")
            
            # 加载JSON文件
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 确保所有股票代码都是字符串
            self.stock_info_dict = {}
            for code, info in data.items():
                # 确保代码是字符串
                code_str = str(code).strip()
                self.stock_info_dict[code_str] = info
            
            count = len(self.stock_info_dict)
            self.status_label.setText(f"已加载 {count} 只股票信息")
            self.result_count_label.setText(f"共 {count} 只股票")
            
            QMessageBox.information(
                self, 
                "加载成功", 
                f"成功加载 {count} 只股票信息！\n\n可以开始筛选了。"
            )
            return True
        except Exception as e:
            QMessageBox.critical(
                self, 
                "错误", 
                f"加载股票信息失败：\n{str(e)}\n\n请检查文件格式是否正确。"
            )
            self.status_label.setText("加载失败")
            return False
    
    def start_filter(self):
        """开始筛选"""
        # 检查是否已加载股票信息
        if not self.stock_info_dict:
            QMessageBox.warning(self, "提示", "请先加载股票信息文件")
            return
        
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
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # 不确定进度
        
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
            # 确保股票代码是字符串
            stock_code = str(result['code']).strip()
            
            # 股票代码
            code_item = QTableWidgetItem(stock_code)
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
        self.result_count_label.setText(f"筛选结果：{len(results)} 只股票")
        self.progress_bar.setVisible(False)
    
    def export_to_csv(self):
        """导出为CSV文件"""
        if not self.filter_results:
            QMessageBox.warning(self, "提示", "没有可导出的数据")
            return
        
        # 选择保存路径
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出CSV文件",
            f"选股结果_{len(self.filter_results)}只.csv",
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
                    # 确保股票代码是字符串
                    stock_code = str(result['code']).strip()
                    plates_text = ', '.join(result['matched_plates'])
                    writer.writerow([
                        stock_code,
                        result['name'] or '未知',
                        result['match_count'],
                        plates_text
                    ])
            
            QMessageBox.information(
                self, 
                "成功", 
                f"已导出 {len(sorted_results)} 只股票到：\n{file_path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导出失败：\n{str(e)}")


def main():
    """主函数"""
    app = QApplication(sys.argv)
    app.setStyle('Fusion')  # 使用Fusion样式，更现代
    
    # 创建主窗口
    window = StockSelectorApp()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()

