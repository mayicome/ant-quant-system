#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
历史数据缓存管理器
用于缓存和加载历史成交量数据，避免重复获取
"""

import os
import json
import pickle
from datetime import datetime, date
from typing import Dict, List, Optional, Any
import hashlib


class HistoricalDataCache:
    """历史数据缓存管理器"""
    
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
        return os.path.join(self.cache_dir, f"{safe_stock_code}_historical_data.json")
    
    def _get_data_key(self, target_date: date) -> str:
        """
        获取数据键值（日期字符串）
        
        Args:
            target_date: 目标日期
            
        Returns:
            日期字符串键值
        """
        return target_date.strftime('%Y-%m-%d')
    
    def save_daily_data(self, stock_code: str, daily_stats: List[Dict[str, Any]]) -> bool:
        """
        保存每日统计数据到缓存
        
        Args:
            stock_code: 股票代码
            daily_stats: 每日统计数据列表
            
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
            for daily_stat in daily_stats:
                date_key = self._get_data_key(daily_stat['date'])
                existing_data[date_key] = {
                    'avg_volume_per_tick': daily_stat['avg_volume_per_tick'],
                    'avg_bid_vol_change': daily_stat['avg_bid_vol_change'],
                    'avg_ask_vol_change': daily_stat['avg_ask_vol_change'],
                    'daily_volume': daily_stat['daily_volume'],
                    'data_points': daily_stat['data_points'],
                    'cached_at': datetime.now().isoformat()
                }
            
            # 保存到文件
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, ensure_ascii=False, indent=2)
            
            print(f"✓ 已缓存 {stock_code} 的 {len(daily_stats)} 个交易日数据")
            return True
            
        except Exception as e:
            print(f"✗ 缓存数据失败: {str(e)}")
            return False
    
    def load_daily_data(self, stock_code: str, target_dates: List[date]) -> tuple[Dict[date, Dict[str, Any]], List[date]]:
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
                        'avg_volume_per_tick': cached_item['avg_volume_per_tick'],
                        'avg_bid_vol_change': cached_item['avg_bid_vol_change'],
                        'avg_ask_vol_change': cached_item['avg_ask_vol_change'],
                        'daily_volume': cached_item['daily_volume'],
                        'data_points': cached_item['data_points'],
                        'cached_at': cached_item.get('cached_at', 'unknown')
                    }
                else:
                    missing_dates.append(target_date)
            
            if cached_data:
                print(f"✓ 从缓存加载了 {stock_code} 的 {len(cached_data)} 个交易日数据")
            
            if missing_dates:
                print(f"⚠ 缓存中缺少 {stock_code} 的 {len(missing_dates)} 个交易日数据: {[d.strftime('%Y-%m-%d') for d in missing_dates]}")
            
        except Exception as e:
            print(f"✗ 加载缓存数据失败: {str(e)}")
            missing_dates = target_dates
        
        return cached_data, missing_dates
    
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
                return {'exists': False, 'file_size': 0, 'cached_dates': 0}
            
            # 获取文件信息
            file_size = os.path.getsize(cache_file)
            
            # 读取缓存数据
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            return {
                'exists': True,
                'file_size': file_size,
                'cached_dates': len(cache_data),
                'date_range': {
                    'earliest': min(cache_data.keys()) if cache_data else None,
                    'latest': max(cache_data.keys()) if cache_data else None
                }
            }
            
        except Exception as e:
            print(f"✗ 获取缓存信息失败: {str(e)}")
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
                    print(f"✓ 已清除 {stock_code} 的缓存")
                return True
            else:
                # 清除所有缓存
                if os.path.exists(self.cache_dir):
                    for filename in os.listdir(self.cache_dir):
                        if filename.endswith('_historical_data.json'):
                            file_path = os.path.join(self.cache_dir, filename)
                            os.remove(file_path)
                    print(f"✓ 已清除所有历史数据缓存")
                return True
                
        except Exception as e:
            print(f"✗ 清除缓存失败: {str(e)}")
            return False


# 全局缓存实例
_global_cache = None

def get_cache() -> HistoricalDataCache:
    """获取全局缓存实例"""
    global _global_cache
    if _global_cache is None:
        _global_cache = HistoricalDataCache()
    return _global_cache
