#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
清理 all_a_stock_info.json 中的 concepts 字段
如果 concepts 中包含"暂无概念"，则只保留"暂无概念"，删除其他所有内容
"""

import json
import os
from typing import Dict, Any

def clean_concepts(data: Dict[str, Any]) -> int:
    """
    清理 concepts 字段
    如果包含"暂无概念"，则只保留"暂无概念"
    
    Args:
        data: JSON 数据字典
        
    Returns:
        清理的股票数量
    """
    cleaned_count = 0
    
    for stock_code, stock_info in data.items():
        if not isinstance(stock_info, dict):
            continue
            
        concepts = stock_info.get('concepts', [])
        if not isinstance(concepts, list):
            continue
        
        # 检查是否包含"暂无概念"
        if "暂无概念" in concepts:
            # 只保留"暂无概念"
            stock_info['concepts'] = ["暂无概念"]
            cleaned_count += 1
            print(f"已清理 {stock_code}: concepts 只保留'暂无概念'")
    
    return cleaned_count

def main():
    """主函数"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    json_file = os.path.join(current_dir, "data", "all_a_stock_info.json")
    
    # 检查文件是否存在
    if not os.path.exists(json_file):
        print(f"错误: 文件 {json_file} 不存在")
        return
    
    # 先备份原文件
    backup_file = json_file + ".backup"
    print(f"正在备份原文件到 {backup_file}...")
    try:
        import shutil
        shutil.copy2(json_file, backup_file)
        print("备份完成")
    except Exception as e:
        print(f"备份失败: {e}")
        print("取消操作，原文件未修改")
        return
    
    print(f"正在读取 {json_file}...")
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"读取文件失败: {e}")
        return
    
    print(f"共找到 {len(data)} 个股票记录")
    print("开始清理 concepts 字段...")
    
    # 清理数据
    cleaned_count = clean_concepts(data)
    
    if cleaned_count == 0:
        print("没有需要清理的数据")
        return
    
    # 保存清理后的数据
    print(f"正在保存清理后的数据到 {json_file}...")
    try:
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"完成！共清理了 {cleaned_count} 个股票的 concepts 字段")
        print(f"原文件已备份到 {backup_file}")
    except Exception as e:
        print(f"保存文件失败: {e}")
        print("请检查备份文件")

if __name__ == "__main__":
    main()

