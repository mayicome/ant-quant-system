#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
增强版历史数据缓存管理器
用于缓存和加载历史数据，包括tick数据和60分钟K线数据
"""

import os
import json
import pickle
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Any, Tuple
import hashlib


class EnhancedHistoricalDataCache:
    """增强版历史数据缓存管理器"""
    
    def __init__(self, cache_dir: str = "cache/historical_data"):
        """
        初始化缓存管理器
        
        Args:
            cache_dir: 缓存文件目录
        """
        self.cache_dir = cache_dir
        self._ensure_cache_dir()
    
    def _ensure_cache_dir(self):
        """确保缓存目录存在"""
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir, exist_ok=True)
    
    def _get_cache_filename(self, stock_code: str) -> str:
        """
        获取股票代码对应的缓存文件名
        
        Args:
            stock_code: 股票代码
            
        Returns:
            缓存文件路径
        """
        # 使用股票代码作为文件名，避免特殊字符问题
        safe_stock_code = stock_code.replace('/', '_').replace('\\', '_')
        # 使用os.path.join确保路径分隔符正确
        return os.path.join(self.cache_dir, f"{safe_stock_code}_enhanced_historical_data.json")
    
    def _get_data_key(self, target_date: date) -> str:
        """
        获取数据键值（日期字符串）
        
        Args:
            target_date: 目标日期
            
        Returns:
            日期字符串键值
        """
        return target_date.strftime('%Y-%m-%d')
    
    def aggregate_tick_to_60min_kline(self, tick_data: pd.DataFrame) -> pd.DataFrame:
        """
        将tick数据聚合为60分钟K线数据
        
        Args:
            tick_data: tick数据DataFrame或字典列表
            
        Returns:
            60分钟K线数据DataFrame
        """
        try:
            # 如果输入是字典列表，转换为DataFrame
            if isinstance(tick_data, list):
                if not tick_data:
                    return pd.DataFrame()
                tick_data = pd.DataFrame(tick_data)
            
            if tick_data.empty:
                return pd.DataFrame()
            
            # 确保时间列存在并设置为索引
            if 'time' in tick_data.columns:
                tick_data = tick_data.copy()
                tick_data['time'] = pd.to_datetime(tick_data['time'])
                tick_data.set_index('time', inplace=True)
            elif not isinstance(tick_data.index, pd.DatetimeIndex):
                print("警告: tick_data中缺少'time'列，无法聚合K线")
                return pd.DataFrame()
            
            # 处理买卖盘口数据（可能是列表格式）
            if 'bidPrice' in tick_data.columns:
                tick_data['bidPrice'] = tick_data['bidPrice'].apply(lambda x: x[0] if isinstance(x, list) and len(x) > 0 else x)
            if 'askPrice' in tick_data.columns:
                tick_data['askPrice'] = tick_data['askPrice'].apply(lambda x: x[0] if isinstance(x, list) and len(x) > 0 else x)
            if 'bidVol' in tick_data.columns:
                tick_data['bidVol'] = tick_data['bidVol'].apply(lambda x: x[0] if isinstance(x, list) and len(x) > 0 else x)
            if 'askVol' in tick_data.columns:
                tick_data['askVol'] = tick_data['askVol'].apply(lambda x: x[0] if isinstance(x, list) and len(x) > 0 else x)
            
            # 按照A股交易时间段划分60分钟K线
            # 上午：9:30-10:30, 10:30-11:30
            # 下午：13:00-14:00, 14:00-15:00
            
            kline_data = pd.DataFrame()
            
            # 定义交易时间段
            trading_periods = [
                ('09:30', '10:30'),  # 第一个小时
                ('10:30', '11:30'),  # 第二个小时
                ('13:00', '14:00'),  # 下午第一个小时
                ('14:00', '15:00')   # 下午第二个小时
            ]
            
            for start_time, end_time in trading_periods:
                # 创建时间范围（确保时区一致）
                base_date = tick_data.index.date[0]
                start_datetime = pd.Timestamp(base_date).replace(
                    hour=int(start_time.split(':')[0]), 
                    minute=int(start_time.split(':')[1])
                ).tz_localize(tick_data.index.tz)
                end_datetime = pd.Timestamp(base_date).replace(
                    hour=int(end_time.split(':')[0]), 
                    minute=int(end_time.split(':')[1])
                ).tz_localize(tick_data.index.tz)
                
                # 筛选该时间段的数据
                period_data = tick_data[(tick_data.index >= start_datetime) & (tick_data.index <= end_datetime)]
                
                if len(period_data) > 0:
                    # 计算该时间段的OHLCV
                    period_kline = {
                        'time': start_datetime,
                        'open': period_data['lastPrice'].iloc[0],
                        'high': period_data['lastPrice'].max(),
                        'low': period_data['lastPrice'].min(),
                        'close': period_data['lastPrice'].iloc[-1],
                        'volume': period_data['volume'].sum(),
                        'bidPrice': period_data['bidPrice'].iloc[-1],
                        'askPrice': period_data['askPrice'].iloc[-1],
                        'bidVol': period_data['bidVol'].iloc[-1],
                        'askVol': period_data['askVol'].iloc[-1]
                    }
                    
                    # 添加到结果中
                    kline_data = pd.concat([kline_data, pd.DataFrame([period_kline])], ignore_index=True)
            
            # 设置时间索引
            if len(kline_data) > 0:
                kline_data['time'] = pd.to_datetime(kline_data['time'])
                kline_data.set_index('time', inplace=True)
            
            return kline_data
            
        except Exception as e:
            print(f"聚合60分钟K线数据时出错: {e}")
            return pd.DataFrame()
    
    def save_daily_data_with_kline(self, stock_code: str, daily_data: List[Dict[str, Any]]) -> bool:
        """
        保存每日数据到缓存，包括tick数据和60分钟K线数据
        
        Args:
            stock_code: 股票代码
            daily_data: 每日数据列表，每个元素包含date、tick_data等
            
        Returns:
            是否保存成功
        """
        try:
            cache_file = self._get_cache_filename(stock_code)
            
            # 读取现有缓存数据
            existing_data = {}
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        existing_data = json.load(f)
                except (json.JSONDecodeError, FileNotFoundError):
                    existing_data = {}
            
            # 更新缓存数据
            for daily_item in daily_data:
                date_key = self._get_data_key(daily_item['date'])
                
                # 保存统计数据
                cache_item = {
                    'avg_volume_per_tick': daily_item.get('avg_volume_per_tick', 0),
                    'avg_bid_vol_change': daily_item.get('avg_bid_vol_change', 0),
                    'avg_ask_vol_change': daily_item.get('avg_ask_vol_change', 0),
                    'daily_volume': daily_item.get('daily_volume', 0),
                    'data_points': daily_item.get('data_points', 0),
                    'cached_at': datetime.now().isoformat()
                }
                
                            # 如果有tick数据，保存60分钟K线数据
                if 'tick_data' in daily_item and daily_item['tick_data'] is not None:
                    tick_df = pd.DataFrame(daily_item['tick_data'])
                    if len(tick_df) > 0:
                        # 聚合为60分钟K线
                        kline_data = self.aggregate_tick_to_60min_kline(tick_df)
                        if len(kline_data) > 0:
                            # 将K线数据转换为可序列化的格式
                            kline_records = []
                            for idx, row in kline_data.iterrows():
                                kline_records.append({
                                    'time': idx.isoformat(),
                                    'open': float(row['open']) if pd.notna(row['open']) else None,
                                    'high': float(row['high']) if pd.notna(row['high']) else None,
                                    'low': float(row['low']) if pd.notna(row['low']) else None,
                                    'close': float(row['close']) if pd.notna(row['close']) else None,
                                    'volume': int(row['volume']) if pd.notna(row['volume']) else 0,
                                    'bidPrice': float(row['bidPrice']) if pd.notna(row['bidPrice']) else None,
                                    'askPrice': float(row['askPrice']) if pd.notna(row['askPrice']) else None,
                                    'bidVol': int(row['bidVol']) if pd.notna(row['bidVol']) else 0,
                                    'askVol': int(row['askVol']) if pd.notna(row['askVol']) else 0
                                })
                            cache_item['kline_60min'] = kline_records
                            cache_item['kline_count'] = len(kline_records)
                
                existing_data[date_key] = cache_item
            
            # 保存到文件
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, ensure_ascii=False, indent=2)
            
            print(f"✓ 已缓存 {stock_code} 的 {len(daily_data)} 个交易日数据（包含60分钟K线）")
            return True
            
        except Exception as e:
            print(f"✗ 缓存数据失败: {str(e)}")
            return False
    
    def load_daily_data(self, stock_code: str, target_dates: List[date]) -> Tuple[Dict[date, Dict[str, Any]], List[date]]:
        """
        从缓存加载每日统计数据
        
        Args:
            stock_code: 股票代码
            target_dates: 目标日期列表
            
        Returns:
            (cached_data, missing_dates): 缓存数据字典和缺失日期列表
        """
        cached_data = {}
        missing_dates = []
        
        try:
            cache_file = self._get_cache_filename(stock_code)
            
            if not os.path.exists(cache_file):
                print(f"缓存文件不存在: {cache_file}")
                return cached_data, missing_dates
            
            # 读取缓存数据
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            # 检查每个目标日期
            for target_date in target_dates:
                date_key = self._get_data_key(target_date)
                
                if date_key in cache_data:
                    cached_item = cache_data[date_key]
                    # 转换回日期对象
                    cached_data[target_date] = {
                        'avg_volume_per_tick': cached_item.get('avg_volume_per_tick', 0),
                        'avg_bid_vol_change': cached_item.get('avg_bid_vol_change', 0),
                        'avg_ask_vol_change': cached_item.get('avg_ask_vol_change', 0),
                        'daily_volume': cached_item.get('daily_volume', 0),
                        'data_points': cached_item.get('data_points', 0),
                        'cached_at': cached_item.get('cached_at', 'unknown'),
                        'kline_60min': cached_item.get('kline_60min', []),
                        'kline_count': cached_item.get('kline_count', 0)
                    }
                else:
                    missing_dates.append(target_date)
            
            if cached_data:
                print(f"✓ 从缓存加载了 {stock_code} 的 {len(cached_data)} 个交易日数据")
            
            if missing_dates:
                print(f"缓存中缺失 {len(missing_dates)} 个交易日数据")
            
            return cached_data, missing_dates
            
        except Exception as e:
            print(f"✗ 加载缓存数据失败: {str(e)}")
            return cached_data, missing_dates
    
    def get_60min_kline_data(self, stock_code: str, target_date: date) -> Optional[pd.DataFrame]:
        """
        获取指定日期的60分钟K线数据
        
        Args:
            stock_code: 股票代码
            target_date: 目标日期
            
        Returns:
            60分钟K线数据DataFrame，如果不存在则返回None
        """
        try:
            cache_file = self._get_cache_filename(stock_code)
            
            if not os.path.exists(cache_file):
                return None
            
            # 读取缓存数据
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            date_key = self._get_data_key(target_date)
            
            if date_key in cache_data and 'kline_60min' in cache_data[date_key]:
                kline_records = cache_data[date_key]['kline_60min']
                if kline_records:
                    # 转换为DataFrame
                    df = pd.DataFrame(kline_records)
                    df['time'] = pd.to_datetime(df['time'])
                    df.set_index('time', inplace=True)
                    return df
            
            return None
            
        except Exception as e:
            print(f"获取60分钟K线数据时出错: {e}")
            return None
    
    def get_multiple_days_kline_data(self, stock_code: str, target_dates: List[date]) -> pd.DataFrame:
        """
        获取多个日期的60分钟K线数据
        
        Args:
            stock_code: 股票代码
            target_dates: 目标日期列表
            
        Returns:
            合并后的60分钟K线数据DataFrame
        """
        all_kline_data = []
        total_kline_count = 0
        
        for target_date in target_dates:
            kline_data = self.get_60min_kline_data(stock_code, target_date)
            if kline_data is not None and not kline_data.empty:
                all_kline_data.append(kline_data)
                total_kline_count += len(kline_data)
        
        if all_kline_data:
            combined_data = pd.concat(all_kline_data, axis=0).sort_index()
            
            # 检查总K线数量是否充足
            if total_kline_count < 21:  # 至少需要21条K线用于分析
                print(f"⚠️ 缓存中的60分钟K线数据总量不足21条（当前{total_kline_count}条），历史数据不够，可能影响分析准确性")
            
            return combined_data
        else:
            return pd.DataFrame()
    
    def get_cache_info(self, stock_code: str) -> Dict[str, Any]:
        """
        获取缓存信息
        
        Args:
            stock_code: 股票代码
            
        Returns:
            缓存信息字典
        """
        try:
            cache_file = self._get_cache_filename(stock_code)
            
            if not os.path.exists(cache_file):
                return {'exists': False, 'file_size': 0, 'dates_count': 0}
            
            # 获取文件大小
            file_size = os.path.getsize(cache_file)
            
            # 读取缓存数据
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            # 统计信息
            dates_count = len(cache_data)
            dates_with_kline = sum(1 for date_data in cache_data.values() if 'kline_60min' in date_data)
            
            return {
                'exists': True,
                'file_size': file_size,
                'dates_count': dates_count,
                'dates_with_kline': dates_with_kline,
                'file_path': cache_file
            }
            
        except Exception as e:
            return {'exists': False, 'error': str(e)}
    
    def clear_cache(self, stock_code: Optional[str] = None) -> bool:
        """
        清除缓存
        
        Args:
            stock_code: 股票代码，如果为None则清除所有缓存
            
        Returns:
            是否清除成功
        """
        try:
            if stock_code:
                # 清除特定股票的缓存
                cache_file = self._get_cache_filename(stock_code)
                if os.path.exists(cache_file):
                    os.remove(cache_file)
                    print(f"✓ 已清除 {stock_code} 的增强缓存")
                return True
            else:
                # 清除所有增强缓存
                if os.path.exists(self.cache_dir):
                    for filename in os.listdir(self.cache_dir):
                        if filename.endswith('_enhanced_historical_data.json'):
                            file_path = os.path.join(self.cache_dir, filename)
                            os.remove(file_path)
                    print(f"✓ 已清除所有增强历史数据缓存")
                return True
                
        except Exception as e:
            print(f"✗ 清除缓存失败: {str(e)}")
            return False


# 全局缓存实例
_global_enhanced_cache = None

def get_enhanced_cache() -> EnhancedHistoricalDataCache:
    """获取全局增强缓存实例"""
    global _global_enhanced_cache
    if _global_enhanced_cache is None:
        _global_enhanced_cache = EnhancedHistoricalDataCache()
    return _global_enhanced_cache
