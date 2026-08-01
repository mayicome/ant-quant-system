#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
自动添加主力净流入工具
为指定的CSV文件添加主力净流入列，并自动填充数据
"""

import csv
import os
import sys
import re
from datetime import datetime


class InflowAdder:
    def __init__(self):
        self.parse_inflow_value = self._parse_inflow_value
        self.load_inflow_file = self._load_inflow_file
    
    def _parse_inflow_value(self, value_str):
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
    
    def _load_inflow_file(self, date_str, base_dir=None):
        """加载指定日期的主力净流入文件

        base_dir: 若传入，则为 history_data 所在目录的绝对路径；否则沿用相对路径 history_data/（兼容旧调用）
        """
        # 日期格式转换：2025-11-19 -> 20251119
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            date_suffix = date_obj.strftime("%Y%m%d")
        except ValueError:
            # 如果日期格式不对，尝试其他格式
            try:
                date_obj = datetime.strptime(date_str, "%Y/%m/%d")
                date_suffix = date_obj.strftime("%Y%m%d")
            except ValueError:
                print(f"警告：无法解析日期格式: {date_str}")
                return {}

        from utils.main_force_inflow_path import resolve_flow_csv_path

        if base_dir:
            inflow_file = resolve_flow_csv_path(date_suffix, base_dir)
        else:
            inflow_file = resolve_flow_csv_path(date_suffix, "history_data")

        if not inflow_file:
            print(f"提示：未找到主力净流入文件: 个股主力净流入_{date_suffix}.csv")
            return {}

        inflow_dict = {}

        def _pick_col(fieldnames, *candidates):
            names = [str(c).strip() for c in (fieldnames or [])]
            for cand in candidates:
                for n in names:
                    if n == cand or cand in n:
                        return n
            return None

        try:
            last_err = None
            rows = None
            used_enc = None
            for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
                try:
                    with open(inflow_file, "r", encoding=enc, newline="") as f:
                        reader = csv.DictReader(f)
                        fieldnames = reader.fieldnames or []
                        rows = list(reader)
                        used_enc = enc
                        break
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    rows = None
            if rows is None:
                print(f"读取主力净流入文件出错: {last_err}")
                return {}

            code_col = _pick_col(fieldnames, "代码")
            inflow_col = _pick_col(
                fieldnames,
                "今日主力净流入-净额",
                "今日主力净流入",
                "主力净流入-净额",
                "主力净流入",
            )
            if not code_col or not inflow_col:
                print(
                    f"警告：CSV 缺少代码/主力净流入列（file={os.path.basename(inflow_file)} "
                    f"cols={list(fieldnames)}）"
                )
                return {}

            for row in rows:
                code = str(row.get(code_col) or "").strip()
                inflow_str = str(row.get(inflow_col) or "").strip()
                if code and code.isdigit():
                    code = code.zfill(6)
                if code and inflow_str:
                    inflow_value = self.parse_inflow_value(inflow_str)
                    if inflow_value is not None:
                        inflow_dict[code] = {
                            "value": inflow_value,
                            "display": inflow_str,
                        }
        except Exception as e:
            print(f"读取主力净流入文件出错: {e}")
            return {}

        return inflow_dict
    
    def detect_encoding(self, file_path):
        """检测文件编码"""
        encodings = ['utf-8-sig', 'utf-8', 'gbk', 'gb2312', 'gb18030']
        
        for encoding in encodings:
            try:
                with open(file_path, 'r', encoding=encoding) as f:
                    # 尝试读取第一行
                    f.readline()
                return encoding
            except (UnicodeDecodeError, UnicodeError):
                continue
            except Exception:
                continue
        
        return None
    
    def add_inflow_column(self, file_path):
        """为文件添加主力净流入列"""
        if not os.path.exists(file_path):
            print(f"错误：文件不存在: {file_path}")
            return False
        
        print(f"正在处理文件: {file_path}")
        
        # 检测文件编码
        encoding = self.detect_encoding(file_path)
        if encoding is None:
            print("错误：无法检测文件编码")
            return False
        
        print(f"检测到文件编码: {encoding}")
        
        # 读取文件数据
        rows = []
        fieldnames = None
        date_column = None
        
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames
                
                if fieldnames is None:
                    print("错误：无法读取文件列名")
                    return False
                
                print(f"文件列名: {', '.join(fieldnames)}")
                
                # 检查日期列名
                if 'near_limit_date' in fieldnames:
                    date_column = 'near_limit_date'
                elif 'limit_date' in fieldnames:
                    date_column = 'limit_date'
                else:
                    print(f"错误：未找到日期列（near_limit_date 或 limit_date）")
                    print(f"文件列名: {', '.join(fieldnames)}")
                    return False
                
                # 检查代码列
                if 'code' not in fieldnames:
                    print("错误：未找到代码列（code）")
                    return False
                
                # 读取所有行
                for row in reader:
                    rows.append(row)
            
            if not rows:
                print("警告：文件中没有数据行")
                return False
            
            print(f"读取到 {len(rows)} 行数据")
            
            # 获取所有唯一的日期
            dates = set([row.get(date_column, '').strip() for row in rows if row.get(date_column, '').strip()])
            print(f"发现 {len(dates)} 个不同的日期")
            
            # 为每个日期加载主力净流入数据
            date_inflow_dict = {}
            for date in dates:
                if date:
                    print(f"正在加载日期 {date} 的主力净流入数据...")
                    date_inflow_dict[date] = self.load_inflow_file(date)
                    count = len(date_inflow_dict[date])
                    print(f"  加载了 {count} 条主力净流入记录")
            
            # 添加或更新主力净流入列
            inflow_column = '主力净流入'
            if inflow_column not in fieldnames:
                fieldnames = list(fieldnames) + [inflow_column]
                print(f"添加新列: {inflow_column}")
            else:
                print(f"更新现有列: {inflow_column}")
            
            # 填充主力净流入数据
            filled_count = 0
            empty_count = 0
            
            for row in rows:
                code_raw = row.get('code', '')
                code = str(code_raw).strip()
                if code and code.isdigit():
                    code = code.zfill(6)
                date = row.get(date_column, '').strip()
                
                if code and date:
                    inflow_info = date_inflow_dict.get(date, {}).get(code)
                    if inflow_info:
                        row[inflow_column] = inflow_info['display']
                        filled_count += 1
                    else:
                        row[inflow_column] = ""
                        empty_count += 1
                else:
                    row[inflow_column] = ""
                    empty_count += 1
            
            print(f"填充完成：成功 {filled_count} 条，未找到 {empty_count} 条")
            
            # 保存回原文件
            print(f"正在保存文件...")
            with open(file_path, 'w', encoding=encoding, newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            
            print(f"完成！文件已保存: {file_path}")
            return True
            
        except Exception as e:
            print(f"处理文件时出错: {str(e)}")
            import traceback
            traceback.print_exc()
            return False


def main():
    """主函数"""
    if len(sys.argv) > 1:
        # 命令行参数模式
        file_path = sys.argv[1]
    else:
        # 交互式输入模式
        print("=" * 60)
        print("自动添加主力净流入工具")
        print("=" * 60)
        print()
        file_path = input("请输入要处理的CSV文件路径（或拖拽文件到此窗口）: ").strip().strip('"')
    
    if not file_path:
        print("错误：未指定文件路径")
        return
    
    # 处理文件
    adder = InflowAdder()
    success = adder.add_inflow_column(file_path)
    
    if success:
        print("\n处理成功！")
    else:
        print("\n处理失败！")
        sys.exit(1)


if __name__ == "__main__":
    main()

