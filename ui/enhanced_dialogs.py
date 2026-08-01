#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
增强版历史数据获取函数
支持保存tick数据和60分钟K线数据到增强缓存
"""

import os
import pandas as pd
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Any


def get_enhanced_historical_data(stock_code: str, days: int = 30, base_date: date = None) -> List[Dict[str, Any]]:
    """
    获取增强版历史数据（包含tick数据和60分钟K线数据）
    
    Args:
        stock_code: 股票代码
        days: 获取天数
        base_date: 基准日期，如果为None则使用当前日期
        
    Returns:
        每日统计数据列表（包含60分钟K线数据）
    """
    print(f"开始获取 {stock_code} 的增强历史数据...")
    
    # 获取交易日历
    try:
        from utils.trading_day import is_tradeday
        
        # 计算最近N个交易日
        if base_date is None:
            current_date = datetime.now().date()
        else:
            current_date = base_date
            
        trading_dates = []
        found_days = 0
        search_count = 0
        max_search_days = days * 2  # 最多往前找2倍的天数
        
        while found_days < days and search_count < max_search_days:
            if is_tradeday(current_date):
                trading_dates.append(current_date)
                found_days += 1
            current_date -= timedelta(days=1)
            search_count += 1
        
        trading_dates.reverse()  # 按时间顺序排列
        print(f"基于基准日期 {base_date} 获取到 {len(trading_dates)} 个交易日: {[d.strftime('%Y-%m-%d') for d in trading_dates]}")
        
    except ImportError:
        print("警告: 无法导入chncal模块，使用简单日期计算")
        today = datetime.now().date()
        trading_dates = []
        current_date = today
        
        for i in range(days):
            current_date = today - timedelta(days=i+1)
            if current_date.weekday() < 5:  # 周一到周五
                trading_dates.append(current_date)
        
        trading_dates.reverse()
        print(f"获取到 {len(trading_dates)} 个交易日: {[d.strftime('%Y-%m-%d') for d in trading_dates]}")
    
    # 尝试从增强缓存加载数据
    cached_data = {}
    try:
        from ui.enhanced_historical_cache import get_enhanced_cache
        enhanced_cache = get_enhanced_cache()
        cached_data, missing_dates = enhanced_cache.load_daily_data(stock_code, trading_dates)
        
        # 如果所有数据都在缓存中，直接返回
        if not missing_dates and cached_data:
            print(f"✓ 所有数据都从增强缓存加载完成")
            return [{'date': target_date, **data} for target_date, data in cached_data.items()]
        
        # 如果部分数据在缓存中，只获取缺失的数据
        if cached_data:
            print(f"✓ 从增强缓存加载了 {len(cached_data)} 个交易日数据，需要获取 {len(missing_dates)} 个交易日数据")
            trading_dates = missing_dates
        else:
            print(f"增强缓存中没有数据，需要获取所有 {len(trading_dates)} 个交易日数据")
            
    except ImportError:
        print("警告: 无法导入增强缓存模块，将直接获取数据")
        cached_data = {}
    
    # 初始化回测引擎
    try:
        from core.backtest_engine import BacktestEngine
        from utils.logger import Logger
        engine = BacktestEngine(stock_code=stock_code)
        engine.set_logger(Logger())
    except ImportError:
        print("错误: 无法导入BacktestEngine")
        return []
    
    daily_stats = []
    
    # 获取每个交易日的数据
    for i, target_date in enumerate(trading_dates):
        try:
            success = engine.load_data(target_date, target_date)
            print(f"  尝试加载 {target_date} 数据: success={success}, data_empty={engine.data is None or engine.data.empty if engine.data is not None else 'None'}")
            
            if success and engine.data is not None and not engine.data.empty:
                # 保存tick数据
                tick_data = engine.data.to_dict('records')
                
                # 计算全天成交量（QMT返回的是累计成交量，需要计算差值）
                daily_volume = 0
                if not engine.data.empty:
                    # 获取第一个和最后一个tick的成交量
                    first_volume = engine.data['volume'].iloc[0] if len(engine.data) > 0 else 0
                    last_volume = engine.data['volume'].iloc[-1] if len(engine.data) > 0 else 0
                    
                    # 计算全天成交量（最后一个tick的累计成交量 - 第一个tick的累计成交量）
                    daily_volume = max(0, last_volume - first_volume)
                    
                    print(f"  成交量计算: 第一个tick={first_volume:.0f}, 最后一个tick={last_volume:.0f}, 全天成交量={daily_volume:.0f}")
                
                # 计算每个tick的平均成交量
                # 全天约4800个tick (4小时 * 60分钟 * 60秒 / 3秒)
                ticks_per_day = 4 * 60 * 60 // 3  # 4800
                avg_volume_per_tick = daily_volume / ticks_per_day if ticks_per_day > 0 else 0
                
                # 计算买一卖一量变化（仍然需要tick间变化）
                bid_vols = [row.get('bidVol', [0])[0] if isinstance(row.get('bidVol', []), list) and len(row.get('bidVol', [])) > 0 else 0 
                           for _, row in engine.data.iterrows()]
                ask_vols = [row.get('askVol', [0])[0] if isinstance(row.get('askVol', []), list) and len(row.get('askVol', [])) > 0 else 0 
                           for _, row in engine.data.iterrows()]
                
                # 计算tick间的买一卖一量变化
                bid_vol_changes = []
                ask_vol_changes = []
                for j in range(1, len(bid_vols)):
                    bid_vol_changes.append(abs(bid_vols[j] - bid_vols[j-1]))
                    ask_vol_changes.append(abs(ask_vols[j] - ask_vols[j-1]))
                
                daily_stat = {
                    'date': target_date,
                    'avg_volume_per_tick': avg_volume_per_tick,
                    'avg_bid_vol_change': sum(bid_vol_changes) / len(bid_vol_changes) if bid_vol_changes else 0,
                    'avg_ask_vol_change': sum(ask_vol_changes) / len(ask_vol_changes) if ask_vol_changes else 0,
                    'daily_volume': daily_volume,
                    'data_points': len(bid_vol_changes),
                    'tick_data': tick_data  # 保存tick数据
                }
                daily_stats.append(daily_stat)
                print(f"  ✓ {target_date} 数据成功，全天成交量: {daily_volume:.0f}, 每tick平均成交量: {avg_volume_per_tick:.0f}, 平均买一量变化: {sum(bid_vol_changes) / len(bid_vol_changes) if bid_vol_changes else 0:.0f}, tick数据点数: {len(tick_data)}")
            else:
                print(f"  ✗ {target_date} 数据失败")
        except Exception as e:
            print(f"  ✗ {target_date} 处理出错: {str(e)}")
            continue
    
    # 保存新获取的数据到增强缓存
    if daily_stats:
        try:
            enhanced_cache.save_daily_data_with_kline(stock_code, daily_stats)
        except Exception as e:
            print(f"警告: 保存增强缓存失败: {str(e)}")
    
    # 合并缓存数据和新获取的数据
    all_stats = []
    
    # 添加缓存数据
    for date, data in cached_data.items():
        all_stats.append({'date': date, **data})
    
    # 添加新获取的数据
    all_stats.extend(daily_stats)
    
    # 按日期排序
    all_stats.sort(key=lambda x: x['date'])
    
    print(f"增强历史数据获取完成，总共 {len(all_stats)} 个交易日的数据")
    return all_stats


def get_60min_kline_data(stock_code: str, target_dates: List[date]) -> pd.DataFrame:
    """
    获取指定日期的60分钟K线数据
    
    Args:
        stock_code: 股票代码
        target_dates: 目标日期列表
        
    Returns:
        60分钟K线数据DataFrame
    """
    try:
        from ui.enhanced_historical_cache import get_enhanced_cache
        enhanced_cache = get_enhanced_cache()
        
        kline_data = enhanced_cache.get_multiple_days_kline_data(stock_code, target_dates)
        
        if not kline_data.empty:
            print(f"✓ 获取到 {stock_code} 的 {len(kline_data)} 条60分钟K线数据")
        else:
            print(f"未找到 {stock_code} 的60分钟K线数据")
        
        return kline_data
        
    except ImportError:
        print("警告: 无法导入增强缓存模块")
        return pd.DataFrame()
    except Exception as e:
        print(f"获取60分钟K线数据时出错: {e}")
        return pd.DataFrame()


def calculate_enhanced_relative_thresholds(stock_code: str, current_data: pd.DataFrame = None, 
                                         config_params: Dict = None, base_date: date = None) -> Dict[str, Any]:
    """
    基于日线数据计算简化的阈值
    
    Args:
        stock_code: 股票代码
        current_data: 当前数据（可选）
        config_params: 配置参数（可选）
        base_date: 基准日期（可选）
        
    Returns:
        相对阈值字典
    """
    try:
        print(f"使用简化阈值计算方法（基于日线数据）")
        from ui.simplified_threshold_calculator import calculate_simplified_thresholds
        return calculate_simplified_thresholds(stock_code, config_params, base_date)
        
    except Exception as e:
        print(f"计算增强相对阈值时出错: {e}")
        raise Exception(f"计算增强相对阈值时出错: {e}")
