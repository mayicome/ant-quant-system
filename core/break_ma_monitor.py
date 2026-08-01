#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
跌破均线买入信号监控类
独立运行，自己连接QMT并订阅股票tick数据，避免与主窗口订阅冲突
监控股票池中的股票，当价格首次跌破或等于MA5、MA10、MA20时发出买入信号
"""

import sys
import os
import pandas as pd
from datetime import datetime, date, timedelta, timezone, time as datetime_time
from typing import List, Dict, Optional, Set
from collections import defaultdict
import logging
import threading
import queue
import time

# 添加项目根目录到路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import xtquant.xtdata as xtdata
    from utils.trading_day import is_tradeday, REFERENCE_SWITCH_TIME
    from utils.stock_info_manager import get_stock_name
    from utils.logger import Logger
except ImportError as e:
    print(f"导入模块失败: {e}")

xtdata.enable_hello = False


class BreakMAMonitor:
    """跌破均线监控类 - 独立运行，自己管理QMT订阅"""
    
    def __init__(self):
        """初始化监控类"""
        self.logger = Logger(mode='live')
        self.stock_pool = set()  # 股票池，存储(股票代码, 股票名称)元组
        self.stock_ma_data = {}  # 存储每个股票的MA数据: {stock_code: {'MA5': float, 'MA10': float, 'MA20': float, 'MA5_intersection': float, 'MA10_intersection': float, 'MA20_intersection': float, 'daily_close': float, 'stock_name': str}}
        self.break_records = []  # 存储跌破记录
        self.break_flags = defaultdict(set)  # 记录每个股票已经跌破的均线: {stock_code: {MA5, MA10, MA20}}
        self.is_monitoring = False
        
        # 订阅相关
        self.subscribe_seq = 0  # 订阅序列号
        self.subscribed_stocks = set()  # 已订阅的股票代码（完整格式，如"000001.SZ"）
        self._lock = threading.Lock()  # 订阅锁
        
        # 启动订阅线程
        self.subscribe_thread = None
        self._running = True
        
        self.logger.info("跌破均线监控器已初始化（独立运行模式）")
    
    def add_stocks_from_csv(self, csv_path: str) -> int:
        """
        从CSV文件添加股票到股票池
        
        Args:
            csv_path: CSV文件路径
            
        Returns:
            成功添加的股票数量
        """
        try:
            # 尝试多种编码方式
            encodings = ['utf-8', 'gbk', 'gb2312', 'gb18030', 'utf-8-sig']
            df = None
            
            for encoding in encodings:
                try:
                    df = pd.read_csv(csv_path, encoding=encoding)
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue
                except Exception:
                    continue
            
            if df is None:
                self.logger.error(f"读取CSV文件失败: {csv_path}")
                return 0
            
            # 解析股票代码和名称
            imported_count = 0
            try:
                # 尝试不同的列名
                code_col = None
                name_col = None
                
                for col in df.columns:
                    col_lower = str(col).lower()
                    if '代码' in col or 'code' in col_lower or '证券代码' in col:
                        code_col = col
                    if '名称' in col or 'name' in col_lower or '证券简称' in col or '简称' in col:
                        name_col = col
                
                if code_col is None:
                    self.logger.error("CSV文件中找不到股票代码列")
                    return 0
                
                for _, row in df.iterrows():
                    try:
                        stock_code = str(row[code_col]).strip().zfill(6)
                        # 剔除5开头的股票代码
                        if stock_code.startswith('5'):
                            continue
                        
                        if name_col:
                            stock_name = str(row[name_col]).strip()
                        else:
                            # 如果没有名称列，尝试从stock_info_manager获取
                            stock_name = get_stock_name(stock_code) or stock_code
                        
                        if len(stock_code) == 6 and stock_code.isdigit():
                            self.stock_pool.add((stock_code, stock_name))
                            imported_count += 1
                    except Exception as e:
                        self.logger.warning(f"解析行数据失败: {str(e)}")
                        continue
                
                self.logger.info(f"从CSV文件导入 {imported_count} 只股票，当前股票池共有 {len(self.stock_pool)} 只股票")
                return imported_count
                
            except Exception as e:
                self.logger.error(f"解析CSV文件失败: {str(e)}")
                return 0
                
        except Exception as e:
            self.logger.error(f"导入股票池失败: {str(e)}", exc_info=True)
            return 0
    
    def add_stock(self, stock_code: str, stock_name: str = None):
        """
        添加单只股票到股票池
        
        Args:
            stock_code: 股票代码（6位数字）
            stock_name: 股票名称（可选）
        """
        if not stock_name:
            stock_name = get_stock_name(stock_code) or stock_code
        
        # 统一股票代码格式（去掉后缀，只保留6位数字）
        if '.' in stock_code:
            stock_code = stock_code.split('.')[0]
        
        if len(stock_code) == 6 and stock_code.isdigit() and not stock_code.startswith('5'):
            self.stock_pool.add((stock_code, stock_name))
            self.logger.info(f"添加股票到监控池: {stock_code} {stock_name}")
    
    def clear_stock_pool(self):
        """清空股票池"""
        self.stock_pool.clear()
        self.stock_ma_data.clear()
        self.break_flags.clear()
        self.logger.info("股票池已清空")
    
    def _get_full_stock_code(self, stock_code: str) -> str:
        """获取完整的股票代码（带市场后缀）"""
        if '.' in stock_code:
            return stock_code
        
        if stock_code.startswith(('0', '1', '3')):
            return f"{stock_code}.SZ"
        elif stock_code.startswith('6'):
            return f"{stock_code}.SH"
        elif stock_code.startswith(('8', '4', '920')):
            return f"{stock_code}.BJ"
        else:
            return stock_code
    
    def _calculate_ma(self, stock_code: str) -> Optional[Dict]:
        """
        计算股票的MA5、MA10、MA20
        与GUI程序使用相同的计算方法，确保一致性
        """
        try:
            full_stock_code = self._get_full_stock_code(stock_code)
            
            # 获取最近30个交易日的数据（确保能计算MA20）
            end_date = date.today()
            start_date = end_date - timedelta(days=60)  # 往前推60天确保有足够数据
            
            startdate = start_date.strftime("%Y%m%d") + "000000"
            enddate = end_date.strftime("%Y%m%d") + "235959"
            
            # 下载历史数据
            try:
                xtdata.download_history_data(full_stock_code, '1d', startdate, enddate)
                time.sleep(0.05)
            except Exception as e:
                self.logger.warning(f"[{stock_code}] 下载历史数据失败: {str(e)}")
                return None
            
            # 获取历史行情数据
            try:
                df = xtdata.get_market_data_ex([], [full_stock_code], period='1d',
                                             start_time=startdate,
                                             end_time=enddate,
                                             count=-1)
            except Exception as e:
                self.logger.warning(f"[{stock_code}] 获取市场数据失败: {str(e)}")
                return None
            
            if df is None or full_stock_code not in df or len(df[full_stock_code]) == 0:
                return None
            
            # 转换为DataFrame
            stock_data = df[full_stock_code]
            daily_df = pd.DataFrame({
                'time': stock_data['time'],
                'close': stock_data['close'],
            })
            
            if daily_df.empty or len(daily_df) < 20:
                return None
            
            # 判断df的最后一个交易日是否是今天
            today = date.today()
            latest_timestamp = daily_df.iloc[-1]['time']
            
            # 转换时间戳（毫秒）为日期
            if isinstance(latest_timestamp, (int, float)):
                # 如果是时间戳（毫秒），转换为日期
                try:
                    latest_date = datetime.fromtimestamp(latest_timestamp / 1000).date()
                except:
                    try:
                        latest_date = pd.to_datetime(latest_timestamp, unit='ms').date()
                    except:
                        latest_date = None
            elif hasattr(latest_timestamp, 'date'):
                latest_date = latest_timestamp.date()
            else:
                try:
                    latest_date = pd.to_datetime(latest_timestamp).date()
                except:
                    latest_date = None
            
            is_df_contains_today = (latest_date == today) if latest_date is not None else False
            
            # 判断当前时间段和是否是交易日
            now = datetime.now()
            current_time = now.time()
            is_trading_day = is_tradeday(today) if 'is_tradeday' in globals() else (today.weekday() < 5)
            is_trading_day_15_24 = is_trading_day and REFERENCE_SWITCH_TIME <= current_time
            
            # 计算MA（根据时间段决定是否包含今天）
            # 根据key_price_calculator.py的逻辑：
            # - 15:00之后：取最近4天（包括今天）的收盘价，计算平均值
            # - 15:00之前：取最近4天（不包含今天）的收盘价，计算平均值
            if is_df_contains_today and is_trading_day and current_time < REFERENCE_SWITCH_TIME:
                # df包含今天的数据，但在15:00之前，排除今天的数据来计算MA
                daily_df_for_ma = daily_df.iloc[:-1].copy()
                # 计算MA（使用标准rolling）
                daily_df_for_ma['MA5'] = daily_df_for_ma['close'].rolling(window=5).mean()
                daily_df_for_ma['MA10'] = daily_df_for_ma['close'].rolling(window=10).mean()
                daily_df_for_ma['MA20'] = daily_df_for_ma['close'].rolling(window=20).mean()
                
                # 对于今天的MA5，使用最近4天（不包含今天）的收盘价计算平均值
                if len(daily_df_for_ma) >= 4:
                    recent_4_closes = daily_df_for_ma['close'].iloc[-4:].tolist()
                    today_ma5 = sum(recent_4_closes) / 4
                    ma5 = today_ma5
                else:
                    ma5 = daily_df_for_ma['MA5'].iloc[-1]
                
                # 获取最后一个交易日的数据
                last_row = daily_df_for_ma.iloc[-1]
                ma10 = last_row['MA10']
                ma20 = last_row['MA20']
                daily_close = daily_df.iloc[-1]['close']  # 今天的收盘价
            else:
                # 不包含今天的数据，或15:00之后（今天的数据已确定），直接使用
                daily_df_for_ma = daily_df.copy()
                
                # 计算MA
                daily_df_for_ma['MA5'] = daily_df_for_ma['close'].rolling(window=5).mean()
                daily_df_for_ma['MA10'] = daily_df_for_ma['close'].rolling(window=10).mean()
                daily_df_for_ma['MA20'] = daily_df_for_ma['close'].rolling(window=20).mean()
                
                # 如果是15:00之后且包含今天，今天的MA5使用最近4天（包括今天）的收盘价计算平均值
                if is_df_contains_today and is_trading_day_15_24:
                    if len(daily_df_for_ma) >= 4:
                        recent_4_closes = daily_df_for_ma['close'].iloc[-4:].tolist()
                        today_ma5 = sum(recent_4_closes) / 4
                        ma5 = today_ma5
                    else:
                        ma5 = daily_df_for_ma['MA5'].iloc[-1]
                else:
                    ma5 = daily_df_for_ma['MA5'].iloc[-1]
                
                # 获取最后一个交易日的数据
                last_row = daily_df_for_ma.iloc[-1]
                ma10 = last_row['MA10']
                ma20 = last_row['MA20']
                daily_close = last_row['close']
            
            # 检查是否有有效值
            if pd.isna(ma5) or pd.isna(ma10) or pd.isna(ma20):
                return None
            
            # 计算与均线重合的价格
            # 使用与key_price_calculator.py相同的逻辑
            ma5_intersection = self._calculate_ma_intersection_price(daily_df, 5, daily_close, is_trading_day, current_time)
            ma10_intersection = self._calculate_ma_intersection_price(daily_df, 10, daily_close, is_trading_day, current_time)
            ma20_intersection = self._calculate_ma_intersection_price(daily_df, 20, daily_close, is_trading_day, current_time)
            
            # 如果无法计算重合价格，使用当前均线值作为备选
            if ma5_intersection is None:
                ma5_intersection = ma5
            if ma10_intersection is None:
                ma10_intersection = ma10
            if ma20_intersection is None:
                ma20_intersection = ma20
            
            return {
                'MA5': float(ma5),
                'MA10': float(ma10),
                'MA20': float(ma20),
                'MA5_intersection': float(ma5_intersection),
                'MA10_intersection': float(ma10_intersection),
                'MA20_intersection': float(ma20_intersection),
                'daily_close': float(daily_close)
            }
            
        except Exception as e:
            self.logger.error(f"[{stock_code}] 计算MA异常: {str(e)}", exc_info=True)
            return None
    
    def _calculate_ma_intersection_price(self, df, period, prev_close, is_trading_day, current_time):
        """
        计算与均线重合的可能的最新价
        
        业务逻辑：
        - 交易日的15:00-24:00：获取到今天的历史数据，以到今天为止的四天的收盘价的均价作为5日线重合点
        - 交易日的00:00-15:00：获取到前一交易日的历史数据，以到前一交易日为止的四天的收盘价的均价作为5日线重合点
        - 非交易日：获取到前一交易日的历史数据，以到前一交易日为止的四天的收盘价的均价作为5日线重合点
        
        注意：5日线重合点是前4天的收盘价的平均值（除以4），不是5天除以4。
        
        Args:
            df: 历史数据DataFrame
            period: 均线周期（例如5表示5日线）
            prev_close: 昨收盘价
            is_trading_day: 是否是交易日
            current_time: 当前时间
            
        Returns:
            float: 与均线重合的价格，如果无法计算则返回None
        """
        try:
            # 需要前(period-1)天的数据来计算重合点
            # 例如5日线需要前4天的数据
            days_needed = period - 1
            if len(df) < days_needed:
                return None
            
            today = date.today()
            
            # 判断df的最后一个交易日是否是今天
            latest_timestamp = df.iloc[-1]['time']
            if isinstance(latest_timestamp, (int, float)):
                try:
                    latest_date = datetime.fromtimestamp(latest_timestamp / 1000).date()
                except:
                    try:
                        latest_date = pd.to_datetime(latest_timestamp, unit='ms').date()
                    except:
                        latest_date = None
            elif hasattr(latest_timestamp, 'date'):
                latest_date = latest_timestamp.date()
            else:
                try:
                    latest_date = pd.to_datetime(latest_timestamp).date()
                except:
                    latest_date = None
            is_df_contains_today = (latest_date == today) if latest_date is not None else False
            
            # 判断当前时间段
            is_trading_day_15_24 = is_trading_day and REFERENCE_SWITCH_TIME <= current_time
            is_trading_day_0_15 = is_trading_day and current_time < REFERENCE_SWITCH_TIME
            is_non_trading_day = not is_trading_day
            
            # 根据时间段获取前(period-1)天的收盘价
            if is_trading_day_15_24:
                # 交易日的15:00-24:00：获取到今天的历史数据，以到今天为止的四天的收盘价的均价
                # 需要df包含今天的数据
                if not is_df_contains_today:
                    return None
                
                # 获取最近(period-1)天的收盘价，包括今天
                # 例如5日线：取最近4天（包括今天）
                if len(df) >= days_needed:
                    # df包含今天，取最近days_needed天的数据（包括今天）
                    recent_closes = df['close'].iloc[-days_needed:].tolist()
                else:
                    # 数据不够
                    return None
            elif is_trading_day_0_15 or is_non_trading_day:
                # 交易日的00:00-15:00或非交易日：获取到前一交易日的历史数据，以到前一交易日为止的四天的收盘价的均价
                # 不包含今天的数据
                if is_df_contains_today:
                    # df包含今天的数据，排除今天
                    if len(df) > 1:
                        recent_closes = df['close'].iloc[:-1].tolist()
                    else:
                        return None
                else:
                    # df不包含今天的数据，直接使用
                    recent_closes = df['close'].iloc[:].tolist()
                
                # 取最近days_needed天的数据
                if len(recent_closes) >= days_needed:
                    recent_closes = recent_closes[-days_needed:]
                else:
                    return None
            else:
                return None
            
            # 计算前(period-1)天的收盘价的平均值
            if len(recent_closes) == days_needed:
                intersection_price = sum(recent_closes) / days_needed
                
                # 验证计算结果：检查是否在合理范围内
                if intersection_price > 0 and intersection_price < prev_close * 2:
                    return intersection_price
            
            return None
            
        except Exception as e:
            # 如果计算失败，返回None
            self.logger.debug(f"计算均线重合价格失败: {str(e)}")
            return None
    
    def _load_ma_data(self):
        """
        加载日线数据并计算MA
        使用与GUI程序相同的计算方法，确保一致性
        """
        self.stock_ma_data.clear()
        
        self.logger.info(f"获取 {len(self.stock_pool)} 只股票的日线数据...")
        
        success_count = 0
        for stock_code, stock_name in self.stock_pool:
            try:
                # 统一股票代码格式
                clean_code = stock_code
                if '.' in clean_code:
                    clean_code = clean_code.split('.')[0]
                
                # 使用与GUI程序相同的计算方法
                ma_result = self._calculate_ma(clean_code)
                
                if ma_result is None:
                    self.logger.warning(f"[{stock_code}] 计算MA失败")
                    continue
                
                # 保存结果
                self.stock_ma_data[clean_code] = {
                    'MA5': ma_result['MA5'],
                    'MA10': ma_result['MA10'],
                    'MA20': ma_result['MA20'],
                    'MA5_intersection': ma_result['MA5_intersection'],
                    'MA10_intersection': ma_result['MA10_intersection'],
                    'MA20_intersection': ma_result['MA20_intersection'],
                    'daily_close': ma_result['daily_close'],
                    'stock_name': stock_name
                }
                
                success_count += 1
                
            except Exception as e:
                self.logger.warning(f"[{stock_code}] 获取均线数据失败: {str(e)}")
                continue
        
        self.logger.info(f"成功获取 {success_count} 只股票的均线数据")
    
    def _quote_callback(self, quote):
        """行情回调函数，处理tick数据"""
        if not self.is_monitoring:
            return
        
        try:
            # 处理多个股票的行情数据
            if isinstance(quote, (list, tuple)):
                for q in quote:
                    self._process_single_quote(q)
            else:
                self._process_single_quote(quote)
        except Exception as e:
            self.logger.error(f"处理行情回调失败: {str(e)}", exc_info=True)
    
    def simulate_tick(self, stock_code: str, last_price: float, tick_time: datetime = None):
        """
        模拟tick数据（用于测试）
        
        Args:
            stock_code: 股票代码（6位数字或带后缀）
            last_price: 最新价
            tick_time: 时间（可选，默认为当前时间）
        """
        if not tick_time:
            tick_time = datetime.now(timezone(timedelta(hours=8)))
        
        # 创建模拟的quote对象
        class MockQuote:
            def __init__(self, code, price, time):
                self.stock_code = code
                self.code = code
                self.lastPrice = price
                self.time = int(time.timestamp() * 1000)
        
        quote = MockQuote(stock_code, last_price, tick_time)
        self._process_single_quote(quote)
    
    def _process_single_quote(self, quote):
        """处理单个股票的行情数据"""
        try:
            # 尝试多种方式获取股票代码
            stock_code = None
            if hasattr(quote, 'stock_code'):
                stock_code = quote.stock_code
            elif hasattr(quote, 'code'):
                stock_code = quote.code
            elif isinstance(quote, dict):
                stock_code = quote.get('stock_code') or quote.get('code')
            
            if not stock_code:
                return
            
            # 统一股票代码格式（去掉后缀，只保留6位数字）
            clean_code = stock_code
            if '.' in clean_code:
                clean_code = clean_code.split('.')[0]
            
            # 检查是否在监控列表中
            if clean_code not in self.stock_ma_data:
                return
            
            # 获取最新价
            if hasattr(quote, 'lastPrice'):
                last_price = quote.lastPrice
            elif isinstance(quote, dict):
                last_price = quote.get('lastPrice', 0)
            else:
                return
            
            if last_price <= 0:
                return
            
            # 获取MA数据
            ma_data = self.stock_ma_data[clean_code]
            # 使用与均线重合的价格来判断
            ma5_intersection = ma_data.get('MA5_intersection', ma_data['MA5'])
            ma10_intersection = ma_data.get('MA10_intersection', ma_data['MA10'])
            ma20_intersection = ma_data.get('MA20_intersection', ma_data['MA20'])
            # 保留原始均线值用于显示
            ma5 = ma_data['MA5']
            ma10 = ma_data['MA10']
            ma20 = ma_data['MA20']
            daily_close = ma_data['daily_close']
            stock_name = ma_data.get('stock_name', clean_code)
            
            # 获取时间
            tick_time = None
            if hasattr(quote, 'time'):
                tick_time = quote.time
            elif isinstance(quote, dict):
                tick_time = quote.get('time')
            
            if isinstance(tick_time, (int, float)) and tick_time > 0:
                # 转换为北京时间
                dt = datetime.fromtimestamp(tick_time/1000, timezone(timedelta(hours=8)))
                time_str = dt.strftime('%Y-%m-%d %H:%M:%S')
            else:
                time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # 检查是否跌破均线（使用与均线重合的价格）
            broken_mas = []
            
            # 检查MA5
            if 'MA5' not in self.break_flags[clean_code] and last_price <= ma5_intersection:
                broken_mas.append('MA5')
                self.break_flags[clean_code].add('MA5')
            
            # 检查MA10
            if 'MA10' not in self.break_flags[clean_code] and last_price <= ma10_intersection:
                broken_mas.append('MA10')
                self.break_flags[clean_code].add('MA10')
            
            # 检查MA20
            if 'MA20' not in self.break_flags[clean_code] and last_price <= ma20_intersection:
                broken_mas.append('MA20')
                self.break_flags[clean_code].add('MA20')
            
            # 如果有跌破的均线，记录
            if broken_mas:
                for ma_type in broken_mas:
                    record = {
                        'stock_code': clean_code,
                        'stock_name': stock_name,
                        'ma_type': ma_type,
                        'break_time': time_str,
                        'last_price': last_price,
                        'daily_close': daily_close
                    }
                    self.break_records.append(record)
                    
                    # 记录日志（显示重合价格和原始均线值）
                    if ma_type == 'MA5':
                        ma_intersection = ma5_intersection
                        ma_value = ma5
                    elif ma_type == 'MA10':
                        ma_intersection = ma10_intersection
                        ma_value = ma10
                    else:
                        ma_intersection = ma20_intersection
                        ma_value = ma20
                    self.logger.info(f"[{clean_code} {stock_name}] 首次跌破{ma_type}: 最新价={last_price:.2f}, {ma_type}重合价={ma_intersection:.2f}, {ma_type}均线值={ma_value:.2f}, 日线价={daily_close:.2f}")
        
        except Exception as e:
            self.logger.error(f"处理tick数据失败: {str(e)}", exc_info=True)
    
    def _update_subscribe(self):
        """更新订阅列表（在独立线程中运行）"""
        while self._running:
            try:
                if not self.is_monitoring:
                    time.sleep(1)
                    continue
                
                # 获取需要订阅的股票代码（完整格式）
                stock_codes = set()
                for stock_code in self.stock_ma_data.keys():
                    full_code = self._get_full_stock_code(stock_code)
                    stock_codes.add(full_code)
                
                if not stock_codes:
                    time.sleep(1)
                    continue
                
                # 检查是否需要更新订阅
                with self._lock:
                    if stock_codes == self.subscribed_stocks and self.subscribe_seq > 0:
                        # 订阅列表没有变化，且已有有效订阅，跳过
                        time.sleep(5)
                        continue
                    
                    # 需要更新订阅
                    # 先取消旧订阅
                    if self.subscribe_seq > 0:
                        try:
                            xtdata.unsubscribe_quote(self.subscribe_seq)
                            self.logger.info(f"取消旧订阅: seq={self.subscribe_seq}")
                        except Exception as e:
                            self.logger.warning(f"取消订阅失败: {str(e)}")
                        self.subscribe_seq = 0
                        time.sleep(1)
                    
                    # 订阅新股票列表
                    try:
                        self.logger.info(f"开始订阅股票: {list(stock_codes)}")
                        self.subscribe_seq = xtdata.subscribe_whole_quote(
                            list(stock_codes),
                            callback=self._quote_callback
                        )
                        
                        if self.subscribe_seq > 0:
                            self.subscribed_stocks = stock_codes.copy()
                            self.logger.info(f"订阅成功: seq={self.subscribe_seq}, 股票数量={len(stock_codes)}")
                        else:
                            self.logger.error(f"订阅失败: seq={self.subscribe_seq}")
                    except Exception as e:
                        self.logger.error(f"订阅股票失败: {str(e)}", exc_info=True)
                        self.subscribe_seq = 0
                
                # 等待一段时间再检查
                time.sleep(10)
                
            except Exception as e:
                self.logger.error(f"订阅线程异常: {str(e)}", exc_info=True)
                time.sleep(5)
    
    def start_monitor(self):
        """开始监控"""
        if not self.stock_pool:
            self.logger.warning("股票池为空，无法开始监控")
            return False
        
        if self.is_monitoring:
            self.logger.warning("监控已在进行中")
            return False
        
        try:
            self.logger.info("开始获取日线数据并计算均线...")
            
            # 获取日线数据并计算MA
            self._load_ma_data()
            
            if not self.stock_ma_data:
                self.logger.error("未能获取到任何股票的均线数据")
                return False
            
            # 重置跌破标志
            self.break_flags.clear()
            for stock_code in self.stock_ma_data.keys():
                self.break_flags[stock_code] = set()
            
            # 启动订阅线程
            if self.subscribe_thread is None or not self.subscribe_thread.is_alive():
                self._running = True
                self.subscribe_thread = threading.Thread(target=self._update_subscribe, daemon=True)
                self.subscribe_thread.start()
                self.logger.info("订阅线程已启动")
            
            self.is_monitoring = True
            self.logger.info(f"开始监控 {len(self.stock_ma_data)} 只股票的跌破均线信号")
            return True
            
        except Exception as e:
            self.logger.error(f"启动监控失败: {str(e)}", exc_info=True)
            return False
    
    def stop_monitor(self):
        """停止监控"""
        self.is_monitoring = False
        self._running = False
        
        # 取消订阅
        with self._lock:
            if self.subscribe_seq > 0:
                try:
                    xtdata.unsubscribe_quote(self.subscribe_seq)
                    self.logger.info(f"已取消订阅: seq={self.subscribe_seq}")
                except Exception as e:
                    self.logger.warning(f"取消订阅失败: {str(e)}")
                self.subscribe_seq = 0
                self.subscribed_stocks.clear()
        
        self.logger.info("已停止监控")
    
    def get_break_records(self) -> List[Dict]:
        """获取跌破记录"""
        return self.break_records.copy()
    
    def clear_break_records(self):
        """清空跌破记录"""
        self.break_records.clear()
        self.logger.info("已清空跌破记录")
    
    def test_with_historical_ticks(self, stock_code: str, test_date: date = None):
        """
        使用历史tick数据测试（用于非交易时段调试）
        
        Args:
            stock_code: 股票代码
            test_date: 测试日期（默认为最近的交易日）
        """
        try:
            import xtquant.xtdata as xtdata
            from core.backtest_engine import BacktestEngine
            
            if not test_date:
                # 获取最近的交易日
                from datetime import date, timedelta
                from utils.trading_day import is_tradeday
                test_date = date.today()
                for i in range(10):
                    if is_tradeday(test_date):
                        break
                    test_date -= timedelta(days=1)
            
            self.logger.info(f"使用历史tick数据测试: {stock_code} {test_date}")
            
            # 检查股票是否在监控列表中
            clean_code = stock_code
            if '.' in clean_code:
                clean_code = clean_code.split('.')[0]
            
            if clean_code not in self.stock_ma_data:
                self.logger.warning(f"股票 {stock_code} 不在监控列表中，先加载MA数据")
                # 临时添加股票
                self.add_stock(clean_code)
                self._load_ma_data()
                if clean_code not in self.stock_ma_data:
                    self.logger.error(f"无法加载股票 {stock_code} 的MA数据")
                    return False
            
            # 加载历史tick数据
            engine = BacktestEngine(stock_code=self._get_full_stock_code(clean_code))
            engine.set_logger(self.logger)
            
            self.logger.info(f"加载 {test_date} 的历史tick数据...")
            success = engine.load_data(test_date, test_date)
            
            if not success or engine.data is None or engine.data.empty:
                self.logger.error(f"无法加载 {stock_code} 的历史tick数据")
                return False
            
            self.logger.info(f"成功加载 {len(engine.data)} 条tick数据")
            
            # 设置监控状态（但不启动实际订阅）
            self.is_monitoring = True
            
            # 重置跌破标志
            if clean_code not in self.break_flags:
                self.break_flags[clean_code] = set()
            else:
                self.break_flags[clean_code].clear()
            
            # 遍历历史tick数据
            break_count = 0
            for idx, row in engine.data.iterrows():
                # 获取价格
                last_price = row.get('lastPrice', 0)
                if last_price <= 0:
                    continue
                
                # 获取时间
                if isinstance(idx, (int, float)):
                    tick_time = datetime.fromtimestamp(idx/1000, timezone(timedelta(hours=8)))
                else:
                    tick_time = datetime.now(timezone(timedelta(hours=8)))
                
                # 记录触发前的记录数
                before_count = len(self.break_records)
                
                # 模拟tick
                self.simulate_tick(clean_code, last_price, tick_time)
                
                # 检查是否有新的跌破记录
                if len(self.break_records) > before_count:
                    break_count += 1
            
            self.logger.info(f"测试完成，共触发 {break_count} 次跌破信号")
            return True
            
        except Exception as e:
            self.logger.error(f"使用历史tick数据测试失败: {str(e)}", exc_info=True)
            return False

