#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
涨停板分布程序
根据输入的文本筛选股票（从行业、概念、板块中匹配），然后查看这些股票在历史涨停板数据中的分布情况
"""

import sys
import os
import json
import pandas as pd
from datetime import datetime
from typing import List, Dict, Set, Tuple
import glob
import re

# 添加项目根目录到路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QTableWidget, QTableWidgetItem, QLabel, QMessageBox,
                             QHeaderView, QApplication, QLineEdit, QFileDialog, QDateEdit)
from PyQt5.QtCore import Qt, QDate


def load_stock_info() -> Dict:
    """从data目录下的all_a_stock_info.json加载所有股票信息"""
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, 'data', 'all_a_stock_info.json')
        if not os.path.exists(json_path):
            return {}
        
        with open(json_path, 'r', encoding='utf-8') as f:
            stock_info_dict = json.load(f)
        
        return stock_info_dict
    except Exception as e:
        print(f"加载股票信息失败: {e}")
        return {}


def filter_stocks_by_text(stock_info_dict: Dict, search_text: str) -> List[Tuple[str, str]]:
    """根据输入的文本筛选股票
    
    Args:
        stock_info_dict: 股票信息字典
        search_text: 搜索文本（匹配行业、概念、板块）
    
    Returns:
        List[Tuple[str, str]]: [(股票代码, 股票名称), ...]
    """
    if not search_text or not search_text.strip():
        return []
    
    search_text = search_text.strip()
    matched_stocks = []
    
    for code, stock_info in stock_info_dict.items():
        code = str(code).zfill(6)
        name = stock_info.get('name', '未知')
        
        # 检查行业
        industry = stock_info.get('industry', '')
        if industry and search_text in str(industry):
            matched_stocks.append((code, name))
            continue
        
        # 检查概念
        concepts = stock_info.get('concepts', [])
        if concepts and isinstance(concepts, list):
            for concept in concepts:
                if concept and search_text in str(concept):
                    matched_stocks.append((code, name))
                    break
            else:
                continue
            continue
        
        # 检查板块
        plates = stock_info.get('plates', [])
        if plates and isinstance(plates, list):
            for plate in plates:
                if plate and search_text in str(plate):
                    matched_stocks.append((code, name))
                    break
    
    # 去重（按代码）
    seen_codes = set()
    unique_stocks = []
    for code, name in matched_stocks:
        if code not in seen_codes:
            seen_codes.add(code)
            unique_stocks.append((code, name))
    
    return unique_stocks


def parse_date_from_filename(filename: str) -> str:
    """从文件名中提取日期。

    支持两种格式：
    - YYYY-MM-DD.json（新格式，程序每日自动生成）
    - Table_YYYYMMDD.xls（旧格式，仅作兼容）
    """
    # 新格式：YYYY-MM-DD.json
    m_json = re.match(r'(\\d{4})-(\\d{2})-(\\d{2})\\.json$', filename)
    if m_json:
        return f"{m_json.group(1)}-{m_json.group(2)}-{m_json.group(3)}"

    # 旧格式：Table_YYYYMMDD.xls
    match = re.search(r'Table_(\\d{8})\\.xls', filename)
    if match:
        date_str = match.group(1)
        year = date_str[:4]
        month = date_str[4:6]
        day = date_str[6:8]
        return f"{year}-{month}-{day}"
    return ""


def load_limit_up_data(history_data_dir: str, start_date: str = None) -> Dict[str, Set[str]]:
    """加载 history_data 下的涨停板数据。

    新逻辑：
    - 优先使用每天自动生成的 JSON 文件（YYYY-MM-DD.json）；
    - 仅在某个日期没有 JSON 时，才兼容读取 Table_YYYYMMDD.xls（旧文件）。
    """
    limit_up_data: Dict[str, Set[str]] = {}

    # 如果提供了起始日期，转换为datetime对象用于比较
    start_date_obj = None
    if start_date:
        try:
            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
        except ValueError:
            print(f"起始日期格式错误: {start_date}，将忽略日期过滤")
            start_date_obj = None

    # 1) 先从 JSON 文件加载（YYYY-MM-DD.json）
    from utils.limit_up_day_path import list_limit_up_day_json_files

    for _date_key, json_file in list_limit_up_day_json_files(history_data_dir):
        try:
            filename = os.path.basename(json_file)
            date_str = parse_date_from_filename(filename)

            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 如果 JSON 内部有 date 字段，以内部为准
            if isinstance(data, dict) and data.get('date'):
                date_str = data['date']

            if not date_str:
                continue

            if start_date_obj:
                try:
                    file_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    if file_date < start_date_obj:
                        continue
                except ValueError:
                    continue

            stocks = data.get('limit_up_stocks') or []
            codes: Set[str] = set()
            for item in stocks:
                code_raw = str(item.get('code', '')).strip()
                clean = ''.join(c for c in code_raw if c.isdigit())
                if len(clean) == 6:
                    codes.add(clean.zfill(6))

            if codes:
                if date_str not in limit_up_data:
                    limit_up_data[date_str] = set()
                limit_up_data[date_str].update(codes)

        except Exception as e:
            print(f"处理涨停板 JSON 文件 {json_file} 时出错: {e}")
            continue

    # 2) 再兼容老的 Table_*.xls（仅在该日期没有 JSON 数据时作为补充）
    pattern = os.path.join(history_data_dir, 'Table_*.xls')
    xls_files = glob.glob(pattern)

    for xls_file in xls_files:
        try:
            filename = os.path.basename(xls_file)
            date_str = parse_date_from_filename(filename)
            if not date_str:
                continue

            if start_date_obj:
                try:
                    file_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    if file_date < start_date_obj:
                        continue
                except ValueError:
                    continue

            # 如果该日期已经有 JSON 数据，就不再用 Table 文件
            if date_str in limit_up_data:
                continue

            df = None
            last_error = None

            encodings = ['gbk', 'gb2312', 'gb18030', 'utf-8', 'utf-8-sig']
            for encoding in encodings:
                try:
                    df = pd.read_csv(xls_file, sep='\\t', encoding=encoding, dtype=str)
                    if df is not None and not df.empty:
                        break
                except Exception as e:
                    last_error = e
                    continue

            if df is None or df.empty:
                print(f"读取文件失败或文件为空 {xls_file}: {last_error}")
                continue

            code_col = None
            for col in df.columns:
                col_str = str(col).strip()
                if '代码' in col_str or 'code' in col_str.lower():
                    code_col = col
                    break

            if code_col is None:
                if len(df.columns) >= 2:
                    code_col = df.columns[1]
                elif len(df.columns) >= 1:
                    code_col = df.columns[0]
                else:
                    continue

            stock_codes: Set[str] = set()
            for _, row in df.iterrows():
                code = str(row.get(code_col, '')).strip()
                m = re.search(r'["\'](\\d{6})["\']', code)
                if m:
                    code_clean = m.group(1)
                else:
                    code_clean = ''.join(c for c in code if c.isdigit())
                if code_clean and len(code_clean) == 6:
                    stock_codes.add(code_clean.zfill(6))

            if stock_codes:
                limit_up_data[date_str] = stock_codes

        except Exception as e:
            print(f"处理 Table 文件 {xls_file} 时出错: {e}")
            continue

    return limit_up_data


def generate_distribution_table(matched_stocks: List[Tuple[str, str]], 
                                limit_up_data: Dict[str, Set[str]]) -> List[Dict]:
    """生成分布表格数据
    
    Args:
        matched_stocks: 匹配的股票列表 [(code, name), ...]
        limit_up_data: 涨停板数据 {日期: {股票代码集合}}
    
    Returns:
        List[Dict]: [{'日期': date, '数量': count, '涨停股票': 'code1 name1;code2 name2;...'}, ...]
    """
    # 创建股票代码到名称的映射
    stock_dict = {code: name for code, name in matched_stocks}
    matched_codes = set(stock_dict.keys())
    
    # 统计每只股票在整个时间段内的涨停次数
    stock_limit_up_count = {}
    for date_str, limit_up_codes in limit_up_data.items():
        for code in limit_up_codes:
            if code in matched_codes:
                stock_limit_up_count[code] = stock_limit_up_count.get(code, 0) + 1
    
    # 收集所有在涨停板数据中出现过的匹配股票代码
    all_limit_up_codes = set()
    for date_str, limit_up_codes in limit_up_data.items():
        for code in limit_up_codes:
            if code in matched_codes:
                all_limit_up_codes.add(code)
    
    # 生成结果列表
    results = []
    
    # 按日期排序
    sorted_dates = sorted(limit_up_data.keys())
    
    for date_str in sorted_dates:
        limit_up_codes = limit_up_data[date_str]
        
        # 找出匹配的股票中哪些在当天涨停了
        matched_limit_up = []
        for code in limit_up_codes:
            if code in matched_codes:
                name = stock_dict.get(code, '未知')
                # 如果涨停次数>1，在名称后加上次数
                count = stock_limit_up_count.get(code, 0)
                if count > 1:
                    matched_limit_up.append(f"{code} {name}({count})")
                else:
                    matched_limit_up.append(f"{code} {name}")
        
        # 如果当天有匹配的股票涨停，添加到结果中
        if matched_limit_up:
            results.append({
                '日期': date_str,
                '数量': len(matched_limit_up),
                '涨停股票': ';'.join(matched_limit_up)
            })
    
    # 在最后添加一行，显示从未涨停的股票
    never_limit_up_codes = matched_codes - all_limit_up_codes
    if never_limit_up_codes:
        never_limit_up_stocks = []
        for code in sorted(never_limit_up_codes):
            name = stock_dict.get(code, '未知')
            never_limit_up_stocks.append(f"{code} {name}")
        
        results.append({
            '日期': '未涨停股票',
            '数量': len(never_limit_up_codes),
            '涨停股票': ';'.join(never_limit_up_stocks)
        })
    
    return results


class LimitUpDistributionDialog(QDialog):
    """涨停板分布对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("涨停板分布分析")
        self.setMinimumSize(800, 600)
        self.resize(1000, 700)
        
        # 去掉右上角的问号（帮助按钮）
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        
        # 存储数据
        self.stock_info_dict = {}
        self.current_results = []
        self.matched_stocks = []  # 存储匹配的股票列表
        self.limit_up_data = {}  # 存储涨停板数据
        
        self.setup_ui()
        self.load_stock_info()
    
    def setup_ui(self):
        """设置UI"""
        layout = QVBoxLayout()
        
        # 搜索区域
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("搜索文本（匹配行业、概念、板块）："))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入关键词，如：人工智能、新能源等...")
        self.search_input.returnPressed.connect(self.search_stocks)
        search_layout.addWidget(self.search_input)
        
        self.search_button = QPushButton("搜索")
        self.search_button.clicked.connect(self.search_stocks)
        search_layout.addWidget(self.search_button)
        
        layout.addLayout(search_layout)
        
        # 起始日期选择区域
        date_layout = QHBoxLayout()
        date_layout.addWidget(QLabel("起始日期："))
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate(2025, 11, 24))  # 默认设置为2025年11月24日
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        date_layout.addWidget(self.start_date_edit)
        date_layout.addWidget(QLabel("（只计算起始日期及之后的数据）"))
        date_layout.addStretch()
        layout.addLayout(date_layout)
        
        # 匹配股票信息标签
        self.matched_stocks_label = QLabel("匹配的股票：0 只")
        layout.addWidget(self.matched_stocks_label)
        
        # 结果表格
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(3)
        self.result_table.setHorizontalHeaderLabels(["日期", "数量", "涨停股票"])
        
        # 设置表格属性
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        
        layout.addWidget(self.result_table)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        
        self.export_button = QPushButton("导出CSV")
        self.export_button.clicked.connect(self.export_csv)
        self.export_button.setEnabled(False)
        button_layout.addWidget(self.export_button)
        
        self.export_all_stocks_button = QPushButton("导出所有股票（按涨停次数排序）")
        self.export_all_stocks_button.clicked.connect(self.export_all_stocks)
        self.export_all_stocks_button.setEnabled(False)
        button_layout.addWidget(self.export_all_stocks_button)
        
        button_layout.addStretch()
        
        layout.addLayout(button_layout)
        
        # 状态标签
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)
        
        self.setLayout(layout)
    
    def load_stock_info(self):
        """加载股票信息"""
        self.status_label.setText("正在加载股票信息...")
        QApplication.processEvents()
        
        self.stock_info_dict = load_stock_info()
        
        if self.stock_info_dict:
            self.status_label.setText(f"已加载 {len(self.stock_info_dict)} 只股票信息")
        else:
            self.status_label.setText("加载股票信息失败")
            QMessageBox.warning(self, "错误", "无法加载股票信息文件")
    
    def search_stocks(self):
        """搜索股票并生成分布表"""
        search_text = self.search_input.text().strip()
        
        if not search_text:
            QMessageBox.warning(self, "提示", "请输入搜索文本")
            return
        
        if not self.stock_info_dict:
            QMessageBox.warning(self, "错误", "股票信息未加载")
            return
        
        self.status_label.setText("正在搜索股票...")
        QApplication.processEvents()
        
        # 筛选股票
        matched_stocks = filter_stocks_by_text(self.stock_info_dict, search_text)
        
        if not matched_stocks:
            QMessageBox.information(self, "提示", f"未找到包含 '{search_text}' 的股票")
            self.matched_stocks_label.setText(f"匹配的股票：0 只")
            self.result_table.setRowCount(0)
            self.export_button.setEnabled(False)
            self.export_all_stocks_button.setEnabled(False)
            self.status_label.setText("")
            return
        
        self.matched_stocks_label.setText(f"匹配的股票：{len(matched_stocks)} 只")
        self.status_label.setText("正在加载涨停板数据...")
        QApplication.processEvents()
        
        # 获取起始日期
        start_date = None
        if self.start_date_edit.date().isValid():
            start_date = self.start_date_edit.date().toString("yyyy-MM-dd")
        
        # 加载涨停板数据
        current_dir = os.path.dirname(os.path.abspath(__file__))
        history_data_dir = os.path.join(current_dir, 'history_data')
        
        if not os.path.exists(history_data_dir):
            QMessageBox.warning(self, "错误", f"history_data目录不存在: {history_data_dir}")
            return
        
        limit_up_data = load_limit_up_data(history_data_dir, start_date)
        
        if not limit_up_data:
            QMessageBox.warning(self, "错误", "未找到任何涨停板数据文件（Table_*.xls）")
            return
        
        self.status_label.setText("正在生成分布表...")
        QApplication.processEvents()
        
        # 保存数据供导出使用
        self.matched_stocks = matched_stocks
        self.limit_up_data = limit_up_data
        
        # 生成分布表
        self.current_results = generate_distribution_table(matched_stocks, limit_up_data)
        
        # 显示结果
        self.display_results()
        
        self.status_label.setText(f"完成！找到 {len(self.current_results)} 个交易日有匹配的股票涨停")
        self.export_button.setEnabled(True)
        self.export_all_stocks_button.setEnabled(True)
    
    def display_results(self):
        """显示结果表格"""
        self.result_table.setRowCount(len(self.current_results))
        
        for row_idx, result in enumerate(self.current_results):
            # 日期列
            date_item = QTableWidgetItem(result['日期'])
            self.result_table.setItem(row_idx, 0, date_item)
            
            # 数量列
            count_item = QTableWidgetItem(str(result.get('数量', 0)))
            self.result_table.setItem(row_idx, 1, count_item)
            
            # 涨停股票列
            stocks_item = QTableWidgetItem(result['涨停股票'])
            self.result_table.setItem(row_idx, 2, stocks_item)
    
    def export_csv(self):
        """导出CSV文件"""
        if not self.current_results:
            QMessageBox.warning(self, "提示", "没有数据可导出")
            return
        
        # 选择保存路径
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存CSV文件",
            f"涨停板分布_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "CSV文件 (*.csv)"
        )
        
        if not file_path:
            return
        
        try:
            # 转换为DataFrame并保存
            df = pd.DataFrame(self.current_results)
            df.to_csv(file_path, index=False, encoding='utf-8-sig')
            
            QMessageBox.information(self, "成功", f"已导出到：\n{file_path}")
            self.status_label.setText(f"已导出到：{file_path}")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"导出失败：{str(e)}")
    
    def export_all_stocks(self):
        """导出所有匹配的股票，按涨停次数降序排列"""
        if not self.matched_stocks:
            QMessageBox.warning(self, "提示", "没有股票数据可导出")
            return
        
        # 统计每只股票的涨停次数
        stock_limit_up_count = {}
        for date_str, limit_up_codes in self.limit_up_data.items():
            for code in limit_up_codes:
                stock_limit_up_count[code] = stock_limit_up_count.get(code, 0) + 1
        
        # 构建股票列表，包含涨停次数
        stock_list = []
        for code, name in self.matched_stocks:
            count = stock_limit_up_count.get(code, 0)
            stock_list.append({
                '股票代码': code,
                '股票名称': name,
                '涨停次数': count
            })
        
        # 按涨停次数降序排列
        stock_list.sort(key=lambda x: x['涨停次数'], reverse=True)
        
        # 选择保存路径
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存CSV文件",
            f"所有股票_按涨停次数排序_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "CSV文件 (*.csv)"
        )
        
        if not file_path:
            return
        
        try:
            # 转换为DataFrame并保存
            df = pd.DataFrame(stock_list)
            df.to_csv(file_path, index=False, encoding='utf-8-sig')
            
            QMessageBox.information(self, "成功", f"已导出 {len(stock_list)} 只股票到：\n{file_path}")
            self.status_label.setText(f"已导出所有股票到：{file_path}")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"导出失败：{str(e)}")


def main():
    app = QApplication(sys.argv)
    
    dialog = LimitUpDistributionDialog()
    dialog.show()
    
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()

