#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
统一历史数据缓存管理器
合并普通缓存和增强缓存的功能，避免数据重复，提高效率
"""

import os
import json
import pickle
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Any, Tuple
import hashlib


class UnifiedHistoricalDataCache:
    """统一历史数据缓存管理器"""
    
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
        # 统一股票代码为6位数字，避免 .SH/.SZ 后缀导致的重复缓存文件
        normalized_code = stock_code or ""
        try:
            # 去除市场后缀，例如 002256.SZ -> 002256
            if '.' in normalized_code:
                normalized_code = normalized_code.split('.')[0]
            # 仅保留数字字符
            only_digits = ''.join(ch for ch in normalized_code if ch.isdigit())
            # 若得到6位数字则采用，否则回退到原始安全化代码
            if len(only_digits) == 6:
                safe_stock_code = only_digits
            else:
                safe_stock_code = stock_code.replace('/', '_').replace('\\', '_')
        except Exception:
            safe_stock_code = stock_code.replace('/', '_').replace('\\', '_')
        # 统一使用enhanced格式，但提供兼容接口
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
                
                if not period_data.empty:
                    # 计算K线数据
                    kline = {
                        'time': start_datetime,
                        'open': period_data['lastPrice'].iloc[0],
                        'high': period_data['lastPrice'].max(),
                        'low': period_data['lastPrice'].min(),
                        'close': period_data['lastPrice'].iloc[-1],
                        'volume': period_data['volume'].iloc[-1] - period_data['volume'].iloc[0],
                        'bidPrice': period_data['bidPrice'].iloc[-1],
                        'askPrice': period_data['askPrice'].iloc[-1],
                        'bidVol': period_data['bidVol'].iloc[-1],
                        'askVol': period_data['askVol'].iloc[-1]
                    }
                    kline_data = pd.concat([kline_data, pd.DataFrame([kline])], ignore_index=True)
            
            if not kline_data.empty:
                kline_data.set_index('time', inplace=True)
            
            return kline_data
            
        except Exception as e:
            print(f"聚合K线数据时出错: {e}")
            return pd.DataFrame()
    
    def save_daily_data(self, stock_code: str, daily_stats: List[Dict[str, Any]], 
                       tick_data: Optional[pd.DataFrame] = None) -> bool:
        """
        保存每日统计数据到缓存（兼容普通缓存和增强缓存）
        
        Args:
            stock_code: 股票代码
            daily_stats: 每日统计数据列表
            tick_data: tick数据（可选，用于生成K线）
            
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
                # 确保日期格式正确
                if isinstance(daily_stat['date'], str):
                    # 如果是字符串，转换为date对象
                    try:
                        date_obj = datetime.strptime(daily_stat['date'], '%Y-%m-%d').date()
                    except:
                        print(f"警告: 无法解析日期字符串: {daily_stat['date']}")
                        continue
                else:
                    date_obj = daily_stat['date']
                
                date_key = self._get_data_key(date_obj)

                # 先读取已有条目，默认保留已有的K线数据，避免被无tick的写入覆盖
                existing_entry = existing_data.get(date_key, {}) if isinstance(existing_data, dict) else {}

                # 基础统计数据（兼容普通缓存）——以新统计覆盖旧统计，但不主动清除已有K线
                cache_entry = dict(existing_entry)
                cache_entry.update({
                    'avg_volume_per_tick': daily_stat['avg_volume_per_tick'],
                    'avg_bid_vol_change': daily_stat['avg_bid_vol_change'],
                    'avg_ask_vol_change': daily_stat['avg_ask_vol_change'],
                    'daily_volume': daily_stat['daily_volume'],
                    'data_points': daily_stat['data_points'],
                    'cached_at': datetime.now().isoformat()
                })
                
                # 如果有tick数据，生成K线数据（增强缓存功能）
                if tick_data is not None:
                    # 确保tick_data是DataFrame格式
                    if isinstance(tick_data, dict):
                        # 如果是字典，转换为DataFrame
                        tick_df = pd.DataFrame(tick_data)
                    elif hasattr(tick_data, 'to_dict'):
                        # 如果是DataFrame，直接使用
                        tick_df = tick_data
                    else:
                        tick_df = None
                    
                    # 统一时间索引：若存在'time'列则转为Datetime并设为索引
                    if tick_df is not None and not tick_df.empty:
                        try:
                            if 'time' in tick_df.columns:
                                tick_df = tick_df.copy()
                                tick_df['time'] = pd.to_datetime(tick_df['time'])
                                tick_df.set_index('time', inplace=True)
                            elif not isinstance(tick_df.index, pd.DatetimeIndex):
                                # 兼容其他可能的时间列命名
                                for candidate_col in ['timestamp', 'Timestamp', 'datatime', 'date_time']:
                                    if candidate_col in tick_df.columns:
                                        tick_df = tick_df.copy()
                                        tick_df['time'] = pd.to_datetime(tick_df[candidate_col])
                                        tick_df.set_index('time', inplace=True)
                                        break
                        except Exception:
                            pass
                    
                    # 初始化daily_tick_data
                    daily_tick_data = pd.DataFrame()
                    
                    if tick_df is not None and not tick_df.empty and isinstance(tick_df.index, pd.DatetimeIndex):
                        # 更稳健的按日筛选：使用起止时间区间，兼容时区
                        start_ts = pd.Timestamp(date_obj)
                        end_ts = start_ts + pd.Timedelta(days=1)
                        if tick_df.index.tz is not None:
                            try:
                                start_ts = start_ts.tz_localize(tick_df.index.tz)
                                end_ts = end_ts.tz_localize(tick_df.index.tz)
                            except Exception:
                                # 若已本地化，转换到相同tz
                                start_ts = start_ts.tz_convert(tick_df.index.tz)
                                end_ts = end_ts.tz_convert(tick_df.index.tz)
                        daily_tick_data = tick_df[(tick_df.index >= start_ts) & (tick_df.index < end_ts)]
                    
                    if not daily_tick_data.empty:
                        kline_data = self.aggregate_tick_to_60min_kline(daily_tick_data)
                        if kline_data is None or kline_data.empty:
                            print(f"警告: 当日tick存在但无法聚合出60分钟K线: {date_key}")
                        if not kline_data.empty:
                            # 转换K线数据为可序列化格式
                            kline_list = []
                            for idx, row in kline_data.iterrows():
                                kline_dict = {
                                    'time': idx.isoformat(),
                                    'open': float(row['open']),
                                    'high': float(row['high']),
                                    'low': float(row['low']),
                                    'close': float(row['close']),
                                    'volume': int(row['volume']),
                                    'bidPrice': float(row['bidPrice']),
                                    'askPrice': float(row['askPrice']),
                                    'bidVol': int(row['bidVol']),
                                    'askVol': int(row['askVol'])
                                }
                                kline_list.append(kline_dict)
                            
                            cache_entry['kline_60min'] = kline_list
                            cache_entry['kline_count'] = len(kline_list)
                            try:
                                print(f"[调试] 缓存写入 {date_key} 的60分钟K线条数: {len(kline_list)}")
                            except Exception:
                                pass
                        else:
                            # 即使聚合失败，也将daily_tick_data的点数记录下来，便于排查
                            cache_entry['kline_60min'] = []
                            cache_entry['kline_count'] = 0
                            try:
                                print(f"[调试] 缓存写入 {date_key} 的60分钟K线条数: 0 (聚合失败)")
                            except Exception:
                                pass
                
                existing_data[date_key] = cache_entry
            
            # 为避免并发覆盖：写入前再次加载最新文件并合并
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, 'r', encoding='utf-8') as _rf:
                        _latest = json.load(_rf)
                    # 后写优先：我们这次构建的键覆盖旧键
                    _latest.update(existing_data)
                    existing_data = _latest
                except Exception:
                    pass

            # 保存到文件并尽量落盘
            try:
                # 确保所有数据都是JSON可序列化的
                def convert_numpy_types(obj):
                    """转换numpy类型为Python原生类型"""
                    import numpy as np
                    if isinstance(obj, np.integer):
                        return int(obj)
                    elif isinstance(obj, np.floating):
                        return float(obj)
                    elif isinstance(obj, np.ndarray):
                        return obj.tolist()
                    elif isinstance(obj, dict):
                        return {key: convert_numpy_types(value) for key, value in obj.items()}
                    elif isinstance(obj, list):
                        return [convert_numpy_types(item) for item in obj]
                    else:
                        return obj
                
                # 转换所有数据
                serializable_data = convert_numpy_types(existing_data)
                
                with open(cache_file, 'w', encoding='utf-8') as f:
                    json.dump(serializable_data, f, ensure_ascii=False, indent=2)
                    try:
                        f.flush()
                        os.fsync(f.fileno())
                    except Exception:
                        pass
            except Exception as e:
                print(f"保存缓存文件时出错: {e}")
                # 尝试使用更安全的序列化方式
                try:
                    import pickle
                    pickle_file = cache_file.replace('.json', '.pkl')
                    with open(pickle_file, 'wb') as f:
                        pickle.dump(existing_data, f)
                    print(f"已使用pickle格式保存缓存: {pickle_file}")
                except Exception as pickle_error:
                    print(f"pickle保存也失败: {pickle_error}")
                    return False
            
            # 写入后校验：随机抽查本次写入的日期键是否存在且kline_count>0（如存在daily_tick_data）
            try:
                with open(cache_file, 'r', encoding='utf-8') as _vf:
                    _verify = json.load(_vf)
                for daily_stat in daily_stats:
                    v_date = daily_stat['date'] if not isinstance(daily_stat['date'], str) else datetime.strptime(daily_stat['date'], '%Y-%m-%d').date()
                    v_key = self._get_data_key(v_date)
                    v_entry = _verify.get(v_key, {})
                    if 'kline_60min' in v_entry:
                        v_cnt = v_entry.get('kline_count', 0)
                        print(f"[调试] 写入校验 {v_key}: kline_count={v_cnt}")
            except Exception:
                pass

            print(f"✓ 统一缓存保存成功: {stock_code} ({len(daily_stats)} 个交易日)")
            return True
            
        except Exception as e:
            print(f"✗ 统一缓存保存失败: {str(e)}")
            return False
    
    def load_daily_data(self, stock_code: str, target_dates: List[date]) -> Tuple[Dict, List[date]]:
        """
        加载每日统计数据（兼容普通缓存接口）
        
        Args:
            stock_code: 股票代码
            target_dates: 目标日期列表
            
        Returns:
            Tuple[Dict, List[date]]: (缓存数据字典, 缺失日期列表)
        """
        try:
            cache_file = self._get_cache_filename(stock_code)
            
            if not os.path.exists(cache_file):
                return {}, target_dates
            
            # 尝试加载缓存文件，如果损坏则删除并重新开始
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                print(f"缓存文件损坏，删除并重新开始: {e}")
                try:
                    os.remove(cache_file)
                    print(f"已删除损坏的缓存文件: {cache_file}")
                except Exception as del_error:
                    print(f"删除损坏缓存文件失败: {del_error}")
                return {}, target_dates
            
            # 检查哪些日期有数据
            available_dates = set(cache_data.keys())
            target_date_keys = {self._get_data_key(date) for date in target_dates}
            
            # 找到缺失的日期
            missing_dates = [date for date in target_dates if self._get_data_key(date) not in available_dates]
            
            # 提取可用的数据
            available_data = {}
            for date_key in target_date_keys:
                if date_key in cache_data:
                    available_data[date_key] = cache_data[date_key]
            
            # 将缓存数据转换为正确的格式
            formatted_data = {}
            for date_key, data in available_data.items():
                # 从date_key解析日期
                try:
                    # _get_data_key返回的是YYYY-MM-DD格式
                    date_obj = datetime.strptime(date_key, '%Y-%m-%d').date()
                    formatted_data[date_obj] = data
                except Exception as e:
                    print(f"警告: 无法解析缓存日期键: {date_key}, 错误: {e}")
                    continue
            
            return formatted_data, missing_dates
            
        except Exception as e:
            print(f"加载统一缓存数据失败: {str(e)}")
            return {}, target_dates
    
    def get_60min_kline_data(self, stock_code: str, target_date: date) -> Optional[pd.DataFrame]:
        """
        获取60分钟K线数据（兼容增强缓存接口）
        
        Args:
            stock_code: 股票代码
            target_date: 目标日期
            
        Returns:
            60分钟K线数据DataFrame
        """
        try:
            cache_file = self._get_cache_filename(stock_code)
            
            if not os.path.exists(cache_file):
                return None
            
            # 尝试加载缓存文件，如果损坏则删除并重新开始
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                print(f"缓存文件损坏，删除并重新开始: {e}")
                try:
                    os.remove(cache_file)
                    print(f"已删除损坏的缓存文件: {cache_file}")
                except Exception as del_error:
                    print(f"删除损坏缓存文件失败: {del_error}")
                return None
            
            date_key = self._get_data_key(target_date)
            if date_key not in cache_data:
                return None
            
            daily_data = cache_data[date_key]
            if 'kline_60min' not in daily_data:
                return None
            
            # 转换K线数据为DataFrame
            kline_list = daily_data['kline_60min']
            if not kline_list:
                return None
            
            kline_data = pd.DataFrame(kline_list)
            kline_data['time'] = pd.to_datetime(kline_data['time'])
            kline_data.set_index('time', inplace=True)
            
            return kline_data
            
        except Exception as e:
            print(f"获取K线数据失败: {str(e)}")
            return None
    
    def get_multiple_days_kline_data(self, stock_code: str, target_dates: List[date]) -> pd.DataFrame:
        """
        获取多个日期的60分钟K线数据（兼容增强缓存接口）
        
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
                print(f"⚠️ 统一缓存中的60分钟K线数据总量不足21条（当前{total_kline_count}条），历史数据不够，可能影响分析准确性")
            
            return combined_data
        else:
            return pd.DataFrame()
    
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
                    print(f"✓ 已清除 {stock_code} 的统一缓存")
                return True
            else:
                # 清除所有缓存
                if os.path.exists(self.cache_dir):
                    for filename in os.listdir(self.cache_dir):
                        if filename.endswith('_enhanced_historical_data.json'):
                            file_path = os.path.join(self.cache_dir, filename)
                            os.remove(file_path)
                    print(f"✓ 已清除所有统一历史数据缓存")
                return True
                
        except Exception as e:
            print(f"✗ 清除缓存失败: {str(e)}")
            return False


# 全局缓存实例
_global_unified_cache = None

def get_unified_cache() -> UnifiedHistoricalDataCache:
    """获取全局统一缓存实例"""
    global _global_unified_cache
    if _global_unified_cache is None:
        _global_unified_cache = UnifiedHistoricalDataCache()
    return _global_unified_cache

# 兼容性接口
def get_cache() -> UnifiedHistoricalDataCache:
    """兼容普通缓存接口"""
    return get_unified_cache()

def get_enhanced_cache() -> UnifiedHistoricalDataCache:
    """兼容增强缓存接口"""
    return get_unified_cache()
