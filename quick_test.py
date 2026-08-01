#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
快速测试简化阈值计算
"""

def quick_test():
    """快速测试"""
    print("=== 快速测试简化阈值计算 ===")
    
    try:
        # 测试导入
        from ui.simplified_threshold_calculator import calculate_simplified_thresholds
        print("✓ 模块导入成功")
        
        # 测试配置参数
        config_params = {
            'volume_threshold_multiplier': 30.0,
            'bid_vol_multiplier': 30.0,
            'ask_vol_multiplier': 30.0,
            'min_volume_threshold': 100,
            'use_simplified_thresholds': True
        }
        print("✓ 配置参数设置成功")
        
        # 测试股票代码
        test_stock = '000001'
        print(f"测试股票: {test_stock}")
        
        # 设置超时
        import signal
        
        def timeout_handler(signum, frame):
            raise TimeoutError("测试超时")
        
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(30)  # 30秒超时
        
        try:
            thresholds = calculate_simplified_thresholds(test_stock, config_params)
            signal.alarm(0)  # 取消超时
            
            print(f"✓ 计算成功:")
            print(f"  成交量阈值: {thresholds.get('volume_threshold', 0):.0f}")
            print(f"  买一量阈值: {thresholds.get('bid_vol_threshold', 0):.0f}")
            print(f"  卖一量阈值: {thresholds.get('ask_vol_threshold', 0):.0f}")
            print(f"  交易日数: {thresholds.get('trading_days_count', 0)}")
            return True
            
        except TimeoutError:
            print("✗ 测试超时（30秒）")
            return False
            
    except Exception as e:
        print(f"✗ 测试失败: {e}")
        return False


if __name__ == "__main__":
    success = quick_test()
    if success:
        print("🎉 简化方案工作正常！")
    else:
        print("⚠️ 简化方案有问题，需要检查。")
