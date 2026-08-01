#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
生成更多60分钟K线数据或调整配置参数
"""

import sys
import os
from datetime import date, timedelta

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def generate_more_kline_data():
    """生成更多60分钟K线数据"""
    print("=== 生成更多60分钟K线数据 ===")
    
    try:
        from core.stock_analyzer import StockAnalyzer
        from ui.enhanced_historical_cache import get_enhanced_cache
        from utils.trading_day import is_tradeday
        
        # 测试股票代码
        stock_code = "000061"  # 农产品
        
        print(f"测试股票: {stock_code}")
        print("-" * 50)
        
        # 方案1：分析更多日期的数据
        print("方案1：分析更多日期的数据...")
        
        # 计算最近几个交易日
        target_dates = []
        current_date = date.today()
        found_trading_days = 0
        max_search_days = 60
        search_count = 0
        
        while found_trading_days < 10 and search_count < max_search_days:  # 尝试分析10个交易日
            if is_tradeday(current_date):
                target_dates.append(current_date)
                found_trading_days += 1
            
            current_date -= timedelta(days=1)
            search_count += 1
        
        print(f"找到 {len(target_dates)} 个交易日: {[d.strftime('%Y-%m-%d') for d in target_dates]}")
        
        # 分析每个交易日
        stock_analyzer = StockAnalyzer()
        success_count = 0
        
        for analysis_date in target_dates:
            try:
                print(f"分析 {analysis_date}...")
                result = stock_analyzer.analyze_stock(stock_code, analysis_date)
                
                if not result.get('error'):
                    success_count += 1
                    print(f"  ✓ 成功，tick数据条数: {result.get('total_ticks', 0)}")
                else:
                    print(f"  ✗ 失败: {result.get('error')}")
                    
            except Exception as e:
                print(f"  ✗ 异常: {e}")
        
        print(f"成功分析了 {success_count} 个交易日")
        
        # 检查生成的K线数据总量
        print(f"\n检查生成的K线数据总量...")
        enhanced_cache = get_enhanced_cache()
        
        # 获取所有日期的K线数据
        all_kline_data = enhanced_cache.get_multiple_days_kline_data(stock_code, target_dates)
        
        print(f"总K线数据条数: {len(all_kline_data)}")
        
        if len(all_kline_data) > 0:
            print(f"时间范围: {all_kline_data.index.min()} 到 {all_kline_data.index.max()}")
            print(f"数据列: {list(all_kline_data.columns)}")
            
            # 检查是否足够用于股价位置分析
            if len(all_kline_data) >= 21:
                print(f"✓ 数据量足够（{len(all_kline_data)} >= 21）")
            else:
                print(f"✗ 数据量不足（{len(all_kline_data)} < 21），需要调整配置参数")
                
                # 方案2：调整配置参数
                print(f"\n方案2：调整配置参数...")
                from core.price_position_analyzer import PricePositionAnalyzer
                
                # 创建自定义配置的分析器
                custom_config = {
                    'lookback_period': 10,  # 减少到10
                    'rsi_period': 10,       # 减少到10
                    'bb_std_dev': 2.0,
                    'low_threshold': 0.15,
                    'high_threshold': 0.85,
                    'rsi_oversold': 30,
                    'rsi_overbought': 70
                }
                
                position_analyzer = PricePositionAnalyzer(custom_config)
                
                # 测试股价位置分析
                current_price = 9.0
                position_result = position_analyzer.analyze_price_position(stock_code, current_price)
                
                print(f"调整配置后的位置分析结果:")
                print(f"  位置: {position_result.get('position_chinese', '未知')}")
                print(f"  置信度: {position_result.get('confidence', 0):.2f}")
                print(f"  判断理由: {position_result.get('reasoning', '无')}")
                
                # 检查是否使用了新的算法
                indicators = position_result.get('indicators', {})
                if 'bollinger_bands' in indicators and 'rsi' in indicators:
                    print(f"  ✓ 使用了新的布林带+RSI算法")
                    bb = indicators['bollinger_bands']
                    rsi = indicators['rsi']
                    print(f"    布林带百分比: {bb.get('percent_b', 0):.3f}")
                    print(f"    RSI值: {rsi.get('value', 0):.1f}")
                else:
                    print(f"  ✗ 仍未使用新的算法")
        
        print(f"\n=== 完成 ===")
        
    except Exception as e:
        print(f"✗ 生成数据失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    generate_more_kline_data()
