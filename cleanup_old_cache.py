#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
清理旧版缓存文件脚本
删除所有 *_historical_data.json 文件，只保留 *_enhanced_historical_data.json 文件
"""

import os
import glob
from pathlib import Path

def cleanup_old_cache():
    """清理旧版缓存文件"""
    print("开始清理旧版缓存文件...")
    
    # 查找所有缓存目录
    cache_dirs = [
        "cache/historical_data",
        "cache/enhanced_historical_data"
    ]
    
    total_deleted = 0
    total_size_freed = 0
    
    for cache_dir in cache_dirs:
        if not os.path.exists(cache_dir):
            print(f"缓存目录不存在: {cache_dir}")
            continue
            
        print(f"\n检查目录: {cache_dir}")
        
        # 查找所有 *_historical_data.json 文件（不包括 *_enhanced_historical_data.json）
        pattern = os.path.join(cache_dir, "*_historical_data.json")
        old_cache_files = glob.glob(pattern)
        
        # 过滤掉增强缓存文件
        old_cache_files = [f for f in old_cache_files if not f.endswith('_enhanced_historical_data.json')]
        
        if not old_cache_files:
            print(f"  没有找到旧版缓存文件")
            continue
            
        print(f"  找到 {len(old_cache_files)} 个旧版缓存文件:")
        
        for file_path in old_cache_files:
            try:
                # 获取文件大小
                file_size = os.path.getsize(file_path)
                total_size_freed += file_size
                
                # 删除文件
                os.remove(file_path)
                total_deleted += 1
                
                # 显示删除的文件
                filename = os.path.basename(file_path)
                print(f"    ✓ 已删除: {filename} ({file_size:,} 字节)")
                
            except Exception as e:
                print(f"    ✗ 删除失败: {os.path.basename(file_path)} - {e}")
    
    print(f"\n清理完成!")
    print(f"总共删除了 {total_deleted} 个旧版缓存文件")
    print(f"释放了 {total_size_freed:,} 字节的存储空间")
    
    if total_deleted > 0:
        print(f"\n现在系统将只使用统一缓存 (*_enhanced_historical_data.json)")
        print("所有历史数据都将保存在统一缓存中，包含成交量数据和60分钟K线数据")
    else:
        print(f"\n没有找到需要清理的旧版缓存文件")

if __name__ == "__main__":
    # 确认操作
    print("此脚本将删除所有旧版的 historical_data.json 缓存文件")
    print("这些文件已经被统一缓存 (enhanced_historical_data.json) 替代")
    print("删除后，系统将只使用统一缓存")
    
    response = input("\n确定要继续吗？(y/N): ").strip().lower()
    if response in ['y', 'yes']:
        cleanup_old_cache()
    else:
        print("操作已取消")
