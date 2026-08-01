#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CSV股票筛选工具
选择并导入一个CSV文件，根据概念、行业、板块筛选股票，并导出结果
"""

import sys
import os
import pandas as pd
from PyQt5.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QFileDialog, QTextEdit, QLabel, 
                             QMessageBox, QTableWidget, QTableWidgetItem, 
                             QHeaderView, QGroupBox)
from PyQt5.QtCore import Qt


class CSVStockFilterDialog(QDialog):
    """CSV股票筛选对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CSV股票筛选工具")
        self.setMinimumSize(1000, 700)
        self.resize(1200, 800)
        
        # 去掉右上角的问号（帮助按钮）
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        
        self.df = None  # 存储导入的CSV数据
        self.filtered_df = None  # 存储筛选后的数据
        
        self.setup_ui()
    
    def setup_ui(self):
        """设置UI"""
        layout = QVBoxLayout()
        
        # 说明标签
        info_label = QLabel("功能说明：导入包含code、name、概念、行业、板块等列的CSV文件，输入一组名称，筛选出概念、行业、板块中包含这些名称的股票")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        # 文件选择区域
        file_group = QGroupBox("文件操作")
        file_layout = QHBoxLayout()
        
        self.import_button = QPushButton("导入CSV文件")
        self.import_button.clicked.connect(self.import_csv)
        file_layout.addWidget(self.import_button)
        
        self.file_label = QLabel("未选择文件")
        file_layout.addWidget(self.file_label)
        
        file_layout.addStretch()
        file_group.setLayout(file_layout)
        layout.addWidget(file_group)
        
        # 筛选条件区域
        filter_group = QGroupBox("筛选条件")
        filter_layout = QVBoxLayout()
        
        filter_layout.addWidget(QLabel("输入要筛选的名称（每行一个，支持在概念、行业、板块中匹配）："))
        
        self.name_input = QTextEdit()
        self.name_input.setPlaceholderText("请输入要筛选的名称，每行一个\n例如：\n人工智能\n新能源\n芯片")
        self.name_input.setMaximumHeight(150)
        filter_layout.addWidget(self.name_input)
        
        self.filter_button = QPushButton("开始筛选")
        self.filter_button.clicked.connect(self.filter_stocks)
        self.filter_button.setEnabled(False)
        filter_layout.addWidget(self.filter_button)
        
        filter_group.setLayout(filter_layout)
        layout.addWidget(filter_group)
        
        # 结果显示区域
        result_group = QGroupBox("筛选结果")
        result_layout = QVBoxLayout()
        
        self.result_count_label = QLabel("结果数量：0")
        result_layout.addWidget(self.result_count_label)
        
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(5)
        self.result_table.setHorizontalHeaderLabels(["代码", "名称", "概念", "行业", "板块"])
        
        # 设置表格属性
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        
        result_layout.addWidget(self.result_table)
        
        self.export_button = QPushButton("导出结果")
        self.export_button.clicked.connect(self.export_results)
        self.export_button.setEnabled(False)
        result_layout.addWidget(self.export_button)
        
        result_group.setLayout(result_layout)
        layout.addWidget(result_group)
        
        self.setLayout(layout)
    
    def import_csv(self):
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
            
            # 更新文件标签
            self.file_label.setText(f"已选择: {os.path.basename(file_path)}")
            
            # 尝试多种编码方式
            encodings = ['utf-8-sig', 'utf-8', 'gbk', 'gb2312', 'gb18030']
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
            
            # 检查必需的列
            required_cols = []
            code_col = None
            name_col = None
            concept_col = None
            industry_col = None
            plate_col = None
            
            # 查找代码列
            for col in df.columns:
                col_str = str(col).strip().lower()
                if col_str in ['code', '代码', '股票代码', '证券代码']:
                    code_col = col
                    break
            
            # 查找名称列
            for col in df.columns:
                col_str = str(col).strip().lower()
                if col_str in ['name', '名称', '股票名称', '证券名称']:
                    name_col = col
                    break
            
            # 查找概念列
            for col in df.columns:
                col_str = str(col).strip().lower()
                if col_str in ['概念', 'concept', 'concepts']:
                    concept_col = col
                    break
            
            # 查找行业列
            for col in df.columns:
                col_str = str(col).strip().lower()
                if col_str in ['行业', 'industry']:
                    industry_col = col
                    break
            
            # 查找板块列
            for col in df.columns:
                col_str = str(col).strip().lower()
                if col_str in ['板块', 'plate', 'plates']:
                    plate_col = col
                    break
            
            if not code_col:
                QMessageBox.warning(self, "错误", "CSV文件中未找到代码列（code、代码、股票代码等）")
                return
            
            if not name_col:
                QMessageBox.warning(self, "错误", "CSV文件中未找到名称列（name、名称、股票名称等）")
                return
            
            # 保存找到的列名
            self.code_col = code_col
            self.name_col = name_col
            self.concept_col = concept_col
            self.industry_col = industry_col
            self.plate_col = plate_col
            
            # 保存数据
            self.df = df
            
            # 显示列信息
            found_cols = []
            if code_col:
                found_cols.append(f"代码: {code_col}")
            if name_col:
                found_cols.append(f"名称: {name_col}")
            if concept_col:
                found_cols.append(f"概念: {concept_col}")
            else:
                found_cols.append("概念: 未找到")
            if industry_col:
                found_cols.append(f"行业: {industry_col}")
            else:
                found_cols.append("行业: 未找到")
            if plate_col:
                found_cols.append(f"板块: {plate_col}")
            else:
                found_cols.append("板块: 未找到")
            
            QMessageBox.information(self, "导入成功", f"成功导入 {len(df)} 条记录\n\n找到的列：\n" + "\n".join(found_cols))
            
            # 启用筛选按钮
            self.filter_button.setEnabled(True)
            
        except Exception as e:
            QMessageBox.warning(self, "错误", f"导入文件时出错: {str(e)}")
            import traceback
            traceback.print_exc()
    
    def filter_stocks(self):
        """筛选股票"""
        try:
            if self.df is None or self.df.empty:
                QMessageBox.warning(self, "错误", "请先导入CSV文件")
                return
            
            # 获取输入的名称列表
            name_text = self.name_input.toPlainText().strip()
            if not name_text:
                QMessageBox.warning(self, "错误", "请输入要筛选的名称")
                return
            
            # 解析名称列表（每行一个）
            search_names = []
            for line in name_text.split('\n'):
                name = line.strip()
                if name:
                    search_names.append(name)
            
            if not search_names:
                QMessageBox.warning(self, "错误", "请输入至少一个要筛选的名称")
                return
            
            # 筛选逻辑：概念、行业、板块中的任意一个包含这些名称中的任意一个
            filtered_rows = []
            
            for idx, row in self.df.iterrows():
                matched = False
                
                # 检查概念列
                if self.concept_col and self.concept_col in row.index:
                    concept_value = str(row[self.concept_col]) if pd.notna(row[self.concept_col]) else ''
                    if concept_value:
                        # 概念可能是分号分隔的字符串
                        concepts = [c.strip() for c in str(concept_value).split(';')]
                        for concept in concepts:
                            for search_name in search_names:
                                if search_name in concept:
                                    matched = True
                                    break
                            if matched:
                                break
                
                # 检查行业列
                if not matched and self.industry_col and self.industry_col in row.index:
                    industry_value = str(row[self.industry_col]) if pd.notna(row[self.industry_col]) else ''
                    if industry_value:
                        for search_name in search_names:
                            if search_name in industry_value:
                                matched = True
                                break
                
                # 检查板块列
                if not matched and self.plate_col and self.plate_col in row.index:
                    plate_value = str(row[self.plate_col]) if pd.notna(row[self.plate_col]) else ''
                    if plate_value:
                        # 板块可能是分号分隔的字符串
                        plates = [p.strip() for p in str(plate_value).split(';')]
                        for plate in plates:
                            for search_name in search_names:
                                if search_name in plate:
                                    matched = True
                                    break
                            if matched:
                                break
                
                if matched:
                    filtered_rows.append(row)
            
            # 创建筛选后的DataFrame
            if filtered_rows:
                self.filtered_df = pd.DataFrame(filtered_rows)
            else:
                self.filtered_df = pd.DataFrame(columns=self.df.columns)
            
            # 显示结果
            self.display_results()
            
            # 更新结果数量标签
            self.result_count_label.setText(f"结果数量：{len(self.filtered_df)}")
            
            # 启用导出按钮
            self.export_button.setEnabled(len(self.filtered_df) > 0)
            
            if len(self.filtered_df) == 0:
                QMessageBox.information(self, "提示", "未找到匹配的股票")
            
        except Exception as e:
            QMessageBox.warning(self, "错误", f"筛选时出错: {str(e)}")
            import traceback
            traceback.print_exc()
    
    def display_results(self):
        """显示筛选结果"""
        try:
            if self.filtered_df is None or self.filtered_df.empty:
                self.result_table.setRowCount(0)
                return
            
            # 设置行数
            self.result_table.setRowCount(len(self.filtered_df))
            
            # 填充数据
            for row_idx, (_, row) in enumerate(self.filtered_df.iterrows()):
                # 代码（格式化为6位，不足6位前面补0）
                code_value = str(row[self.code_col]) if pd.notna(row[self.code_col]) else ''
                if code_value:
                    # 提取数字部分并格式化为6位
                    code_clean = ''.join(c for c in code_value if c.isdigit())
                    if code_clean:
                        code_value = code_clean.zfill(6)
                self.result_table.setItem(row_idx, 0, QTableWidgetItem(code_value))
                
                # 名称
                name_value = str(row[self.name_col]) if pd.notna(row[self.name_col]) else ''
                self.result_table.setItem(row_idx, 1, QTableWidgetItem(name_value))
                
                # 概念
                if self.concept_col and self.concept_col in row.index:
                    concept_value = str(row[self.concept_col]) if pd.notna(row[self.concept_col]) else ''
                else:
                    concept_value = ''
                self.result_table.setItem(row_idx, 2, QTableWidgetItem(concept_value))
                
                # 行业
                if self.industry_col and self.industry_col in row.index:
                    industry_value = str(row[self.industry_col]) if pd.notna(row[self.industry_col]) else ''
                else:
                    industry_value = ''
                self.result_table.setItem(row_idx, 3, QTableWidgetItem(industry_value))
                
                # 板块
                if self.plate_col and self.plate_col in row.index:
                    plate_value = str(row[self.plate_col]) if pd.notna(row[self.plate_col]) else ''
                else:
                    plate_value = ''
                self.result_table.setItem(row_idx, 4, QTableWidgetItem(plate_value))
            
        except Exception as e:
            QMessageBox.warning(self, "错误", f"显示结果时出错: {str(e)}")
            import traceback
            traceback.print_exc()
    
    def export_results(self):
        """导出筛选结果"""
        try:
            if self.filtered_df is None or self.filtered_df.empty:
                QMessageBox.warning(self, "错误", "没有可导出的数据")
                return
            
            # 选择保存位置
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "保存筛选结果",
                "",
                "CSV文件 (*.csv);;所有文件 (*.*)"
            )
            
            if not file_path:
                return
            
            # 确保文件扩展名是.csv
            if not file_path.lower().endswith('.csv'):
                file_path += '.csv'
            
            # 保存为CSV（格式化代码列为6位）
            try:
                export_df = self.filtered_df.copy()
                if self.code_col in export_df.columns:
                    def format_code(code):
                        if pd.isna(code):
                            return ''
                        code_str = str(code)
                        # 提取数字部分并格式化为6位
                        code_clean = ''.join(c for c in code_str if c.isdigit())
                        if code_clean:
                            return code_clean.zfill(6)
                        return code_str
                    export_df[self.code_col] = export_df[self.code_col].apply(format_code)
                
                export_df.to_csv(file_path, index=False, encoding='utf-8-sig')
                QMessageBox.information(self, "成功", f"结果已导出到:\n{file_path}\n\n共 {len(self.filtered_df)} 条记录")
            except Exception as e:
                QMessageBox.warning(self, "错误", f"导出文件时出错: {str(e)}")
                import traceback
                traceback.print_exc()
            
        except Exception as e:
            QMessageBox.warning(self, "错误", f"导出时出错: {str(e)}")
            import traceback
            traceback.print_exc()


def main():
    app = QApplication(sys.argv)
    
    dialog = CSVStockFilterDialog()
    dialog.show()
    
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()

