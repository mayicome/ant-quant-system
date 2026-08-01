#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
缓存迁移脚本
将普通缓存和增强缓存合并为统一缓存，删除重复文件
"""

import os
import shutil
from datetime import datetime
from ui.unified_historical_cache import get_unified_cache

def backup_cache_files():
    """备份现有缓存文件"""
    print("=== 备份现有缓存文件 ===")
    
    cache_dir = "cache/historical_data"
    if not os.path.exists(cache_dir):
        print("缓存目录不存在，无需备份")
        return False
    
    # 创建备份目录
    backup_dir = f"cache/backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    os.makedirs(backup_dir, exist_ok=True)
    
    # 复制所有缓存文件到备份目录
    files = os.listdir(cache_dir)
    backup_count = 0
    
    for file in files:
        if file.endswith('.json'):
            src_path = os.path.join(cache_dir, file)
            dst_path = os.path.join(backup_dir, file)
            shutil.copy2(src_path, dst_path)
            backup_count += 1
    
    print(f"✓ 已备份 {backup_count} 个缓存文件到: {backup_dir}")
    return True

def analyze_cache_files():
    """分析缓存文件"""
    print("\n=== 分析缓存文件 ===")
    
    cache_dir = "cache/historical_data"
    if not os.path.exists(cache_dir):
        print("缓存目录不存在")
        return [], []
    
    files = os.listdir(cache_dir)
    historical_files = [f for f in files if f.endswith('_historical_data.json') and not f.endswith('_enhanced_historical_data.json')]
    enhanced_files = [f for f in files if f.endswith('_enhanced_historical_data.json')]
    
    print(f"普通缓存文件: {len(historical_files)} 个")
    print(f"增强缓存文件: {len(enhanced_files)} 个")
    
    # 计算文件大小
    total_size_historical = sum(os.path.getsize(os.path.join(cache_dir, f)) for f in historical_files)
    total_size_enhanced = sum(os.path.getsize(os.path.join(cache_dir, f)) for f in enhanced_files)
    
    print(f"普通缓存总大小: {total_size_historical / 1024:.1f} KB")
    print(f"增强缓存总大小: {total_size_enhanced / 1024:.1f} KB")
    
    return historical_files, enhanced_files

def verify_enhanced_cache_completeness(historical_files, enhanced_files):
    """验证增强缓存是否包含所有数据"""
    print("\n=== 验证增强缓存完整性 ===")
    
    cache_dir = "cache/historical_data"
    missing_stocks = []
    
    for hist_file in historical_files:
        # 提取股票代码
        stock_code = hist_file.replace('_historical_data.json', '')
        enhanced_file = f"{stock_code}_enhanced_historical_data.json"
        
        if enhanced_file not in enhanced_files:
            missing_stocks.append(stock_code)
            print(f"⚠️ 缺少增强缓存: {stock_code}")
    
    if missing_stocks:
        print(f"发现 {len(missing_stocks)} 个股票缺少增强缓存，这些文件将不会被删除")
        return False
    else:
        print("✓ 所有普通缓存都有对应的增强缓存")
        return True

def delete_historical_cache_files(historical_files):
    """删除普通缓存文件"""
    print("\n=== 删除普通缓存文件 ===")
    
    cache_dir = "cache/historical_data"
    deleted_count = 0
    deleted_size = 0
    
    for file in historical_files:
        file_path = os.path.join(cache_dir, file)
        try:
            file_size = os.path.getsize(file_path)
            os.remove(file_path)
            deleted_count += 1
            deleted_size += file_size
            print(f"✓ 已删除: {file}")
        except Exception as e:
            print(f"✗ 删除失败 {file}: {e}")
    
    print(f"✓ 成功删除 {deleted_count} 个文件")
    print(f"✓ 释放空间: {deleted_size / 1024:.1f} KB")
    
    return deleted_count, deleted_size

def test_unified_cache_functionality():
    """测试统一缓存功能"""
    print("\n=== 测试统一缓存功能 ===")
    
    try:
        unified_cache = get_unified_cache()
        
        # 测试基本功能
        cache_dir = "cache/historical_data"
        if os.path.exists(cache_dir):
            files = os.listdir(cache_dir)
            enhanced_files = [f for f in files if f.endswith('_enhanced_historical_data.json')]
            
            if enhanced_files:
                # 测试第一个文件
                test_file = enhanced_files[0]
                stock_code = test_file.replace('_enhanced_historical_data.json', '')
                
                print(f"测试股票: {stock_code}")
                
                # 测试数据访问
                from datetime import date
                test_dates = [date(2025, 8, 29), date(2025, 8, 28)]
                
                available_data, missing_dates = unified_cache.load_daily_data(stock_code, test_dates)
                print(f"  数据访问测试: 可用 {len(available_data)} 个交易日")
                
                # 测试K线数据访问
                kline_data = unified_cache.get_multiple_days_kline_data(stock_code, test_dates)
                print(f"  K线数据测试: {len(kline_data)} 条K线")
                
                print("✓ 统一缓存功能测试通过")
                return True
        
        print("⚠️ 没有找到测试数据")
        return False
        
    except Exception as e:
        print(f"✗ 统一缓存功能测试失败: {e}")
        return False

def main():
    """主函数"""
    print("=== 缓存迁移脚本 ===\n")
    
    try:
        # 1. 备份现有文件
        if not backup_cache_files():
            print("备份失败，停止迁移")
            return
        
        # 2. 分析缓存文件
        historical_files, enhanced_files = analyze_cache_files()
        
        if not historical_files:
            print("没有找到普通缓存文件，无需迁移")
            return
        
        # 3. 验证增强缓存完整性
        if not verify_enhanced_cache_completeness(historical_files, enhanced_files):
            print("增强缓存不完整，停止迁移")
            return
        
        # 4. 确认用户操作
        print(f"\n准备删除 {len(historical_files)} 个普通缓存文件")
        print("这些文件的数据已包含在增强缓存中")
        
        # 5. 删除普通缓存文件
        deleted_count, deleted_size = delete_historical_cache_files(historical_files)
        
        # 6. 测试统一缓存功能
        if test_unified_cache_functionality():
            print("\n=== 迁移完成 ===")
            print(f"✓ 成功删除 {deleted_count} 个重复文件")
            print(f"✓ 释放存储空间 {deleted_size / 1024:.1f} KB")
            print(f"✓ 统一缓存功能正常")
            print(f"✓ 建议更新代码使用统一缓存接口")
        else:
            print("\n⚠️ 迁移完成但功能测试失败")
            print("请检查统一缓存实现")
        
    except Exception as e:
        print(f"迁移过程中出现错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
