#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
简化的阈值计算器
使用日线数据替代tick数据，提高计算效率
"""

import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Any
import time


def get_daily_volume_data(stock_code: str, days: int = 30, base_date: date = None) -> List[Dict[str, Any]]:
    """
    获取日线成交量数据
    
    Args:
        stock_code: 股票代码
        days: 获取天数
        base_date: 基准日期，如果为None则使用当前日期
        
    Returns:
        每日成交量数据列表
    """
    # print(f"开始获取 {stock_code} 的日线成交量数据...")
    
    try:
        import xtquant.xtdata as xtdata
        from utils.trading_day import is_tradeday
        
        # 计算交易日范围
        if base_date is None:
            current_date = datetime.now().date()
        else:
            current_date = base_date
            
        # 往前推足够的天数以确保获取到足够的交易日
        # 考虑到周末和节假日，需要更多天数来确保获取到足够的交易日
        start_date = current_date - timedelta(days=days * 3)
        
        # 转换为QMT需要的格式
        startdate = start_date.strftime("%Y%m%d") + "000000"
        enddate = current_date.strftime("%Y%m%d") + "235959"
        
        # 确保股票代码格式正确（添加市场后缀）
        full_stock_code = stock_code
        if len(stock_code) == 6:
            if stock_code.startswith('00') or stock_code.startswith('30'):
                full_stock_code = f"{stock_code}.SZ"  # 深市
            elif stock_code.startswith('60') or stock_code.startswith('68'):
                full_stock_code = f"{stock_code}.SH"  # 沪市
        
        # print(f"使用完整股票代码: {full_stock_code}")
        
        # 下载历史数据
        try:
            xtdata.download_history_data(full_stock_code, '1d', startdate, enddate)
            time.sleep(0.1)  # 等待下载完成
        except Exception as e:
            raise Exception(f"下载历史数据失败: {str(e)}")
        
        # 获取历史行情数据
        try:
            df = xtdata.get_market_data_ex([], [full_stock_code], period='1d', 
                                         start_time=startdate, 
                                         end_time=enddate, 
                                         count=-1)
        except Exception as e:
            raise Exception(f"获取历史行情数据失败: {str(e)}")
        
        if full_stock_code not in df or len(df[full_stock_code]) == 0:
            raise Exception(f"未获取到股票{full_stock_code}的数据或数据为空")
        
        # QMT返回的数据结构是字典，键是字段名，值是数据数组
        # 需要转换为DataFrame格式
        stock_data = df[full_stock_code]
        
        # 创建DataFrame，使用time字段作为索引
        daily_data = pd.DataFrame({
            'time': stock_data['time'],
            'open': stock_data['open'],
            'high': stock_data['high'],
            'low': stock_data['low'],
            'close': stock_data['close'],
            'volume': stock_data['volume'],
            'amount': stock_data['amount']
        })
        
        # 设置time为索引
        daily_data.set_index('time', inplace=True)
        
        if daily_data.empty:
            raise Exception(f"股票{stock_code}的日线数据为空")
        
        # print(f"✓ 获取到 {len(daily_data)} 条日线数据")
        
        # 筛选交易日数据并计算每tick平均成交量
        daily_stats = []
        ticks_per_day = 4 * 60 * 60 // 3  # 4800个tick每天
        
        # 处理DataFrame数据，索引是时间戳
        for idx, row in daily_data.iterrows():
            try:
                # 索引是时间戳，转换为日期
                trade_date = pd.to_datetime(idx, unit='ms').date()
                
                # 检查是否为交易日
                if not is_tradeday(trade_date):
                    continue
                
                # 获取成交量
                volume = row.get('volume', 0)
                if volume <= 0:
                    continue
                
                # 计算每tick平均成交量
                avg_volume_per_tick = volume / ticks_per_day
                
                daily_stat = {
                    'date': trade_date,
                    'daily_volume': volume,
                    'avg_volume_per_tick': avg_volume_per_tick
                }
                
                daily_stats.append(daily_stat)
                
            except Exception as e:
                continue
        
        # 按日期排序并取最近的N个交易日
        daily_stats.sort(key=lambda x: x['date'])
        
        # 如果获取到的交易日数量不足，尝试获取更多历史数据
        if len(daily_stats) < days:
            print(f"⚠️ 只获取到 {len(daily_stats)} 个交易日，少于请求的 {days} 个交易日")
            print(f"   这可能是因为历史数据不足或节假日较多")
        
        # 取最近的N个交易日，如果不足N个则取全部
        daily_stats = daily_stats[-days:] if len(daily_stats) > days else daily_stats
        
        # print(f"✓ 成功获取 {len(daily_stats)} 个交易日的日线数据")
        return daily_stats
        
    except ImportError as e:
        raise Exception(f"导入模块失败: {e}")
    except Exception as e:
        raise Exception(f"获取日线数据时出错: {e}")


def _process_daily_data_from_df(daily_df: pd.DataFrame, days: int = 30, base_date: date = None) -> List[Dict]:
    """
    从已加载的DataFrame中处理日线数据
    
    Args:
        daily_df: 已加载的日线数据DataFrame
        days: 需要的交易日数
        base_date: 基准日期
        
    Returns:
        处理后的日线数据列表
    """
    try:
        from utils.trading_day import is_tradeday
        
        if daily_df.empty:
            return []
        
        # 筛选交易日数据并计算每tick平均成交量
        daily_stats = []
        ticks_per_day = 4 * 60 * 60 // 3  # 4800个tick每天
        
        for idx, row in daily_df.iterrows():
            # 检查是否为交易日
            if isinstance(idx, str):
                try:
                    trade_date = datetime.strptime(idx, '%Y%m%d').date()
                except:
                    continue
            else:
                trade_date = idx.date() if hasattr(idx, 'date') else idx
            
            # 检查是否为交易日
            if not is_tradeday(trade_date):
                continue
            
            # 获取成交量数据
            volume = row.get('volume', 0)
            if volume > 0:
                # 计算每tick平均成交量
                avg_volume_per_tick = volume / ticks_per_day
                daily_stats.append({
                    'date': trade_date,
                    'daily_volume': volume,
                    'avg_volume_per_tick': avg_volume_per_tick
                })
        
        # 按日期排序
        daily_stats.sort(key=lambda x: x['date'])
        
        # 取最近的N个交易日，如果不足N个则取全部
        daily_stats = daily_stats[-days:] if len(daily_stats) > days else daily_stats
        
        return daily_stats
        
    except Exception as e:
        print(f"处理日线数据时出错: {e}")
        return []


def calculate_simplified_thresholds(stock_code: str, config_params: Dict = None, base_date: date = None, daily_df: pd.DataFrame = None) -> Dict[str, Any]:
    """
    基于日线数据计算简化的阈值
    
    Args:
        stock_code: 股票代码
        config_params: 配置参数
        base_date: 基准日期
        daily_df: 可选的已加载日线数据，避免重复加载
        
    Returns:
        阈值字典
    """
    try:
        # 如果提供了已加载的日线数据，直接使用；否则重新获取
        if daily_df is not None and not daily_df.empty:
            # 使用已加载的日线数据，避免重复加载
            daily_stats = _process_daily_data_from_df(daily_df, days=30, base_date=base_date)
        else:
            # 获取日线数据
            daily_stats = get_daily_volume_data(stock_code, days=30, base_date=base_date)
        
        if not daily_stats:
            raise Exception(f"无法获取股票 {stock_code} 的日线数据，请检查QMT连接或股票代码是否正确")
        
        # 计算平均每tick成交量
        avg_volumes = [stat['avg_volume_per_tick'] for stat in daily_stats if stat['avg_volume_per_tick'] > 0]
        
        if not avg_volumes:
            raise Exception(f"股票 {stock_code} 的日线数据中没有有效的成交量数据")
        
        # 计算基准值
        base_volume_per_tick = sum(avg_volumes) / len(avg_volumes)
        
        # 从配置获取倍数参数
        volume_multiplier = config_params.get('volume_threshold_multiplier', 30.0) if config_params else 30.0
        bid_multiplier = config_params.get('bid_vol_multiplier', 30.0) if config_params else 30.0
        ask_multiplier = config_params.get('ask_vol_multiplier', 30.0) if config_params else 30.0
        min_volume_threshold = config_params.get('min_volume_threshold', 100) if config_params else 100
        
        # 计算阈值
        volume_threshold = max(base_volume_per_tick * volume_multiplier, min_volume_threshold)
        
        # 买一量和卖一量变化阈值基于每tick平均成交量计算
        # 使用配置的系数来计算买一和卖一阈值
        bid_vol_threshold = max(base_volume_per_tick * bid_multiplier, min_volume_threshold)
        ask_vol_threshold = max(base_volume_per_tick * ask_multiplier, min_volume_threshold)
        
        # 计算平均每日成交量
        avg_daily_volume = sum([stat['daily_volume'] for stat in daily_stats]) / len(daily_stats)
        
        # print(f"✓ 基于 {len(daily_stats)} 个交易日日线数据计算简化阈值:")
        # print(f"  请求交易日数: 30 个，实际获取: {len(daily_stats)} 个")
        # print(f"  平均每日成交量: {avg_daily_volume:.0f}手")
        # print(f"  平均每tick成交量: {base_volume_per_tick:.2f}")
        # print(f"  成交量阈值: {volume_threshold:.0f}")
        # print(f"  买一量变化阈值: {bid_vol_threshold:.0f}")
        # print(f"  卖一量变化阈值: {ask_vol_threshold:.0f}")
        
        return {
            'volume_threshold': volume_threshold,
            'bid_vol_threshold': bid_vol_threshold,
            'ask_vol_threshold': ask_vol_threshold,
            'trading_days_count': len(daily_stats),
            'avg_daily_volume': avg_daily_volume,
            'use_dynamic': True,
            'base_volume_per_tick': base_volume_per_tick,
            'volume_multiplier': volume_multiplier,
            'bid_multiplier': bid_multiplier,
            'ask_multiplier': ask_multiplier,
            'calculation_method': 'simplified_daily',
            'daily_stats': daily_stats  # 添加日线数据供复用
        }
        
    except Exception as e:
        raise Exception(f"计算简化阈值时出错: {e}")


def test_simplified_calculation():
    """测试简化阈值计算"""
    print("=== 测试简化阈值计算 ===")
    
    # 测试股票
    test_stocks = ['000001', '000002', '600000']
    
    for stock_code in test_stocks:
        print(f"\n--- 测试股票: {stock_code} ---")
        
        # 模拟配置参数
        config_params = {
            'volume_threshold_multiplier': 30.0,
            'bid_vol_multiplier': 30.0,
            'ask_vol_multiplier': 30.0,
            'min_volume_threshold': 100
        }
        
        # 计算阈值
        thresholds = calculate_simplified_thresholds(stock_code, config_params)
        
        print(f"结果: {thresholds}")


if __name__ == "__main__":
    test_simplified_calculation()
