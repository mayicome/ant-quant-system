#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
清除缓存并重新测试
"""

import sys
import os
from datetime import date

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def clear_and_test():
    """清除缓存并重新测试"""
    print("=== 清除缓存并重新测试 ===")
    
    try:
        from ui.enhanced_historical_cache import get_enhanced_cache
        
        # 测试股票代码
        stock_code = "000061"  # 农产品
        
        print(f"测试股票: {stock_code}")
        print("-" * 50)
        
        # 1. 清除增强缓存
        print("1. 清除增强缓存...")
        enhanced_cache = get_enhanced_cache()
        enhanced_cache.clear_cache(stock_code)
        
        # 2. 重新运行测试
        print("\n2. 重新运行测试...")
        from test_multi_day_kline import test_multi_day_kline
        test_multi_day_kline()
        
        print(f"\n=== 测试完成 ===")
        
    except Exception as e:
        print(f"✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    clear_and_test()
