#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
验证股价位置判断是否使用了60分钟K线数据和新的算法
"""

import sys
import os
from datetime import date

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def verify_price_position_usage():
    """验证股价位置判断的使用情况"""
    print("=== 验证股价位置判断使用情况 ===")
    
    try:
        from core.price_position_analyzer import PricePositionAnalyzer
        from core.stock_analyzer import StockAnalyzer
        
        # 测试股票代码
        stock_code = "000061"  # 农产品
        analysis_date = date(2025, 8, 29)
        
        print(f"测试股票: {stock_code}")
        print(f"分析日期: {analysis_date}")
        print("-" * 50)
        
        # 1. 测试股价位置分析器
        print("1. 测试股价位置分析器...")
        position_analyzer = PricePositionAnalyzer()
        
        # 分析股价位置
        current_price = 5.85  # 假设当前价格
        position_result = position_analyzer.analyze_price_position(stock_code, current_price)
        
        print(f"   位置分析结果:")
        print(f"     位置: {position_result.get('position_chinese', '未知')}")
        print(f"     置信度: {position_result.get('confidence', 0):.2f}")
        print(f"     判断理由: {position_result.get('reasoning', '无')}")
        
        # 检查是否使用了新的算法
        indicators = position_result.get('indicators', {})
        if 'bollinger_bands' in indicators and 'rsi' in indicators:
            print(f"   ✓ 使用了新的布林带+RSI算法")
            bb = indicators['bollinger_bands']
            rsi = indicators['rsi']
            print(f"     布林带百分比: {bb.get('percent_b', 0):.3f}")
            print(f"     RSI值: {rsi.get('value', 0):.1f}")
        else:
            print(f"   ✗ 未使用新的算法")
        
        # 2. 测试股票分析器中的集成
        print(f"\n2. 测试股票分析器中的集成...")
        stock_analyzer = StockAnalyzer()
        
        # 分析股票（这会调用股价位置分析器）
        result = stock_analyzer.analyze_stock(stock_code, analysis_date)
        
        if result.get('error'):
            print(f"   ✗ 分析失败: {result.get('error')}")
            return
        
        # 检查主力行为分析结果
        main_force_analysis = result.get('main_force_analysis', {})
        price_position = main_force_analysis.get('price_position')
        
        if price_position:
            print(f"   ✓ 股价位置分析已集成到主力行为分析中")
            print(f"     位置: {price_position.get('position_chinese', '未知')}")
            print(f"     置信度: {price_position.get('confidence', 0):.2f}")
            print(f"     判断理由: {price_position.get('reasoning', '无')}")
        else:
            print(f"   ✗ 股价位置分析未集成到主力行为分析中")
        
        # 3. 检查主力行为判断是否使用了位置信息
        print(f"\n3. 检查主力行为判断...")
        behavior_counts = result.get('behavior_counts', {})
        
        if behavior_counts:
            print(f"   主力行为统计:")
            print(f"     吸筹: {behavior_counts.get('accumulation', 0)} 次")
            print(f"     出货: {behavior_counts.get('distribution', 0)} 次")
            print(f"     洗盘: {behavior_counts.get('wash', 0)} 次")
            print(f"     护盘: {behavior_counts.get('support', 0)} 次")
            print(f"     砸盘: {behavior_counts.get('smash', 0)} 次")
            print(f"     拉升: {behavior_counts.get('lift', 0)} 次")
            print(f"     扫货: {behavior_counts.get('sweep', 0)} 次")
        
        # 4. 验证60分钟K线数据的使用
        print(f"\n4. 验证60分钟K线数据使用...")
        try:
            from ui.enhanced_historical_cache import get_enhanced_cache
            enhanced_cache = get_enhanced_cache()
            
            # 检查缓存文件是否存在
            cache_info = enhanced_cache.get_cache_info(stock_code)
            if cache_info.get('exists', False):
                print(f"   ✓ 增强缓存文件存在")
                print(f"     文件路径: {cache_info.get('file_path', 'N/A')}")
                print(f"     文件大小: {cache_info.get('file_size', 0)} 字节")
                print(f"     包含K线的日期数: {cache_info.get('dates_with_kline', 0)}")
                
                # 尝试获取60分钟K线数据
                from datetime import timedelta
                from utils.trading_day import is_tradeday
                
                # 计算最近几个交易日
                target_dates = []
                current_date = date.today()
                found_trading_days = 0
                max_search_days = 30
                search_count = 0
                
                while found_trading_days < 5 and search_count < max_search_days:
                    if is_tradeday(current_date):
                        target_dates.append(current_date)
                        found_trading_days += 1
                    
                    current_date -= timedelta(days=1)
                    search_count += 1
                
                kline_data = enhanced_cache.get_multiple_days_kline_data(stock_code, target_dates)
                
                if not kline_data.empty:
                    print(f"   ✓ 成功加载60分钟K线数据")
                    print(f"     K线数据条数: {len(kline_data)}")
                    print(f"     数据列: {list(kline_data.columns)}")
                    
                    # 检查数据格式
                    if 'close' in kline_data.columns:
                        print(f"   ✓ 包含收盘价数据")
                        print(f"     价格范围: {kline_data['close'].min():.3f} - {kline_data['close'].max():.3f}")
                else:
                    print(f"   ✗ 60分钟K线数据为空")
            else:
                print(f"   ✗ 增强缓存文件不存在")
                
        except Exception as e:
            print(f"   ✗ 验证60分钟K线数据时出错: {e}")
        
        print(f"\n=== 验证完成 ===")
        
    except Exception as e:
        print(f"✗ 验证失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    verify_price_position_usage()
