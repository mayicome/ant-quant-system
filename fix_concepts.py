#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复 all_a_stock_info.json 中的概念数据问题
将以"该公司暂无概念题材数据"开头的概念替换为"暂无概念"
"""

import json
import os

def fix_concepts_in_file(file_path):
    """修复JSON文件中的概念数据"""
    print(f"正在读取文件: {file_path}")
    
    # 读取JSON文件
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    print(f"共找到 {len(data)} 只股票")
    
    # 统计修复数量
    fixed_count = 0
    stocks_fixed = []
    
    # 遍历所有股票
    for stock_code, stock_info in data.items():
        if 'concepts' in stock_info and isinstance(stock_info['concepts'], list):
            # 检查并修复concepts数组
            original_concepts = stock_info['concepts'].copy()
            fixed = False
            
            for i, concept in enumerate(stock_info['concepts']):
                if isinstance(concept, str) and concept.startswith('该公司暂无概念题材数据'):
                    stock_info['concepts'][i] = '暂无概念'
                    fixed = True
            
            # 如果修复了，记录
            if fixed:
                fixed_count += 1
                stocks_fixed.append(stock_code)
                print(f"修复股票 {stock_code}: {original_concepts[:3]}... -> {stock_info['concepts'][:3]}...")
    
    print(f"\n共修复 {fixed_count} 只股票的概念数据")
    
    # 创建备份
    backup_path = file_path + '.backup'
    print(f"\n正在创建备份文件: {backup_path}")
    with open(backup_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    # 保存修复后的文件
    print(f"正在保存修复后的文件: {file_path}")
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print("\n修复完成！")
    print(f"备份文件已保存为: {backup_path}")
    
    return fixed_count, stocks_fixed

if __name__ == '__main__':
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(current_dir, 'data', 'all_a_stock_info.json')
    
    if not os.path.exists(file_path):
        print(f"错误：找不到文件 {file_path}")
        exit(1)
    
    try:
        fixed_count, stocks_fixed = fix_concepts_in_file(file_path)
        print(f"\n成功修复 {fixed_count} 只股票的概念数据")
    except Exception as e:
        print(f"修复过程中出现错误: {e}")
        import traceback
        traceback.print_exc()
        exit(1)

