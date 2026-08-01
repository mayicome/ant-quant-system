#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主力净流入查询工具
从选中的股票列表中查找指定日期的主力净流入
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import csv
import os
from datetime import datetime
import re


class InflowLookupApp:
    def __init__(self, root):
        self.root = root
        self.root.title("主力净流入查询工具")
        self.root.geometry("800x600")
        
        # 数据存储
        self.selected_file = None
        self.result_data = []
        
        # 创建界面
        self.create_widgets()
        
    def create_widgets(self):
        # 文件选择区域
        file_frame = ttk.LabelFrame(self.root, text="文件选择", padding=10)
        file_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.file_label = ttk.Label(file_frame, text="未选择文件")
        self.file_label.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(file_frame, text="选择股票文件", command=self.select_file).pack(side=tk.LEFT, padx=5)
        ttk.Button(file_frame, text="处理数据", command=self.process_data).pack(side=tk.LEFT, padx=5)
        ttk.Button(file_frame, text="保存结果", command=self.save_result).pack(side=tk.LEFT, padx=5)
        
        # 结果显示区域
        result_frame = ttk.LabelFrame(self.root, text="处理结果", padding=10)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 创建表格
        columns = ("代码", "名称", "日期", "主力净流入")
        self.tree = ttk.Treeview(result_frame, columns=columns, show="headings", height=20)
        
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=150)
        
        # 滚动条
        scrollbar = ttk.Scrollbar(result_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 状态栏
        self.status_label = ttk.Label(self.root, text="就绪", relief=tk.SUNKEN)
        self.status_label.pack(fill=tk.X, side=tk.BOTTOM)
        
    def select_file(self):
        """选择股票文件"""
        filename = filedialog.askopenfilename(
            title="选择股票文件",
            filetypes=[("CSV文件", "*.csv"), ("所有文件", "*.*")],
            initialdir="history_data"
        )
        
        if filename:
            self.selected_file = filename
            self.file_label.config(text=os.path.basename(filename))
            self.status_label.config(text=f"已选择文件: {os.path.basename(filename)}")
            
    def parse_inflow_value(self, value_str):
        """解析主力净流入值（如"9.61亿" -> 961000000，"-3.31亿" -> -331000000）"""
        if not value_str or value_str.strip() == '':
            return None
        
        # 移除可能的空格和特殊字符
        value_str = value_str.strip()
        
        # 尝试匹配数字和单位（支持负数）
        # 格式可能是：9.61亿、8.12亿、-3.31亿、2693.10万、-2693.10万等
        pattern = r'(-?\d+\.?\d*)([万亿])'
        match = re.search(pattern, value_str)
        
        if match:
            number = float(match.group(1))
            unit = match.group(2)
            
            if unit == '亿':
                return number * 100000000
            elif unit == '万':
                return number * 10000
        
        return None
    
    def format_inflow_value(self, value):
        """格式化主力净流入值显示"""
        if value is None:
            return ""
        
        if abs(value) >= 100000000:
            return f"{value/100000000:.2f}亿"
        elif abs(value) >= 10000:
            return f"{value/10000:.2f}万"
        else:
            return f"{value:.2f}"
    
    def load_inflow_file(self, date_str):
        """加载指定日期的主力净流入文件"""
        # 日期格式转换：2025-11-19 -> 20251119
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        date_suffix = date_obj.strftime("%Y%m%d")

        from utils.main_force_inflow_path import resolve_flow_csv_path
        from auto_add_inflow import InflowAdder

        # 与选股导出共用同一套按列名解析逻辑，避免 CSV 增列后下标错位
        return InflowAdder()._load_inflow_file(date_str)
    
    def process_data(self):
        """处理数据"""
        if not self.selected_file:
            messagebox.showwarning("警告", "请先选择股票文件")
            return
        
        if not os.path.exists(self.selected_file):
            messagebox.showerror("错误", "文件不存在")
            return
        
        self.status_label.config(text="正在处理数据...")
        self.root.update()
        
        try:
            # 读取股票文件，尝试多种编码
            stocks = []
            date_column = None
            
            # 尝试多种编码方式
            encodings = ['utf-8-sig', 'utf-8', 'gbk', 'gb2312', 'gb18030']
            fieldnames = None
            used_encoding = None
            
            # 先确定正确的编码和列名
            for encoding in encodings:
                try:
                    with open(self.selected_file, 'r', encoding=encoding) as f:
                        reader = csv.DictReader(f)
                        fieldnames = reader.fieldnames
                        if fieldnames:
                            used_encoding = encoding
                            break
                except (UnicodeDecodeError, UnicodeError):
                    continue
                except Exception as e:
                    print(f"尝试编码 {encoding} 时出错: {e}")
                    continue
            
            if fieldnames is None:
                messagebox.showerror("错误", "无法读取文件，请检查文件编码")
                return
            
            # 调试：显示列名
            print(f"文件列名: {fieldnames}")
            print(f"使用的编码: {used_encoding}")
            
            # 检查日期列名
            if 'near_limit_date' in fieldnames:
                date_column = 'near_limit_date'
            elif 'limit_date' in fieldnames:
                date_column = 'limit_date'
            else:
                error_msg = f"未找到日期列（near_limit_date 或 limit_date）\n文件列名: {', '.join(fieldnames)}"
                messagebox.showerror("错误", error_msg)
                return
            
            # 使用确定的编码读取数据
            with open(self.selected_file, 'r', encoding=used_encoding) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    code = row.get('code', '').strip()
                    name = row.get('name', '').strip()
                    date = row.get(date_column, '').strip()
                    
                    # 调试：打印前几行数据
                    if len(stocks) < 3:
                        print(f"读取行: code={code}, name={name}, date={date}")
                    
                    if code and date:
                        stocks.append({
                            'code': code,
                            'name': name,
                            'date': date
                        })
            
            if not stocks:
                messagebox.showwarning("警告", f"未找到有效的股票数据\n文件列名: {', '.join(fieldnames) if fieldnames else '未知'}")
                return
            
            # 获取所有唯一的日期
            dates = set([stock['date'] for stock in stocks])
            
            # 为每个日期加载主力净流入数据
            date_inflow_dict = {}
            for date in dates:
                date_inflow_dict[date] = self.load_inflow_file(date)
            
            # 处理结果
            self.result_data = []
            self.tree.delete(*self.tree.get_children())
            
            for stock in stocks:
                code = stock['code']
                name = stock['name']
                date = stock['date']
                
                # 查找主力净流入
                inflow_info = date_inflow_dict.get(date, {}).get(code)
                
                if inflow_info:
                    inflow_value = inflow_info['value']
                    inflow_display = inflow_info['display']
                else:
                    inflow_value = None
                    inflow_display = ""
                
                # 添加到结果数据
                self.result_data.append({
                    'code': code,
                    'name': name,
                    'date': date,
                    'inflow': inflow_display
                })
                
                # 添加到表格
                self.tree.insert("", tk.END, values=(
                    code,
                    name,
                    date,
                    inflow_display
                ))
            
            self.status_label.config(text=f"处理完成，共 {len(self.result_data)} 条记录")
            messagebox.showinfo("完成", f"处理完成，共 {len(self.result_data)} 条记录")
            
        except Exception as e:
            messagebox.showerror("错误", f"处理数据时出错: {str(e)}")
            self.status_label.config(text="处理失败")
    
    def save_result(self):
        """保存结果"""
        if not self.result_data:
            messagebox.showwarning("警告", "没有可保存的数据")
            return
        
        filename = filedialog.asksaveasfilename(
            title="保存结果",
            defaultextension=".csv",
            filetypes=[("CSV文件", "*.csv"), ("所有文件", "*.*")],
            initialdir="history_data"
        )
        
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8-sig', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=['code', 'name', 'date', 'inflow'])
                    writer.writeheader()
                    writer.writerows(self.result_data)
                
                messagebox.showinfo("成功", f"结果已保存到: {filename}")
                self.status_label.config(text=f"已保存到: {os.path.basename(filename)}")
            except Exception as e:
                messagebox.showerror("错误", f"保存文件时出错: {str(e)}")


def main():
    root = tk.Tk()
    app = InflowLookupApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

