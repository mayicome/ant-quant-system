import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta
import json
import os


class PricePositionAnalyzer:
    """股价位置分析器"""
    
    def __init__(self, config_params=None):
        """
        初始化分析器
        
        Args:
            config_params: 配置参数字典
        """
        self.config_params = config_params or {}
        # 添加缓存机制，避免重复加载数据
        self._kline_data_cache = {}
    
    def analyze_price_position(self, stock_code: str, current_price: float, 
                             analysis_date: Optional[str] = None) -> Dict:
        """
        分析股价位置
        
        Args:
            stock_code: 股票代码
            current_price: 当前价格
            analysis_date: 分析日期（可选）
            
        Returns:
            分析结果字典
        """
        try:
            # 检查缓存中是否已有该股票的数据
            cache_key = f"{stock_code}_{analysis_date}" if analysis_date else stock_code
            if cache_key in self._kline_data_cache:
                print(f"✓ 使用缓存的K线数据: {stock_code}")
                kline_data = self._kline_data_cache[cache_key]
            else:
                # 加载历史数据
                kline_data = self._load_historical_data(stock_code)
                if kline_data is not None and not kline_data.empty:
                    # 缓存数据
                    self._kline_data_cache[cache_key] = kline_data
                    print(f"✓ 已缓存K线数据: {stock_code}")
            
            if kline_data is None or kline_data.empty:
                return self._get_default_position_result("无法获取历史数据")
            
            # 检查数据量是否足够
            if len(kline_data) < 21:
                return self._get_default_position_result(f"历史数据不足（当前{len(kline_data)}条，需要至少21条）")
            
            # 评估市场位置
            position_result = self._assess_market_position(kline_data, current_price)
            
            return position_result
            
        except Exception as e:
            print(f"股价位置分析出错: {e}")
            return self._get_default_position_result(f"分析过程出错: {str(e)}")
    
    def clear_cache(self):
        """清除缓存数据"""
        self._kline_data_cache.clear()
        print("✓ 已清除股价位置分析器缓存")
    
    def _assess_market_position(self, price_data: pd.DataFrame, current_price: float) -> Dict:
        """
        评估当前市场的波段位置
        
        Args:
            price_data: 包含历史价格数据的DataFrame
            current_price: 当前价格
            
        Returns:
            Dict: 包含判断结果和相关信息
        """
        try:
            # 获取配置参数
            lookback_period = self.config_params.get('lookback_period', 20)
            rsi_period = self.config_params.get('rsi_period', 14)
            bb_std_dev = self.config_params.get('bb_std_dev', 2.0)
            low_threshold = self.config_params.get('low_threshold', 0.15)
            high_threshold = self.config_params.get('high_threshold', 0.85)
            rsi_oversold = self.config_params.get('rsi_oversold', 30)
            rsi_overbought = self.config_params.get('rsi_overbought', 70)
            
            # 确保有足够的数据
            if len(price_data) < lookback_period + 1:
                return self._get_default_position_result("历史数据不足")
            
            # 获取收盘价数据
            if 'close' in price_data.columns:
                closes = price_data['close'].values
            elif 'lastPrice' in price_data.columns:
                closes = price_data['lastPrice'].values
            else:
                return self._get_default_position_result("缺少价格数据")
            
            # 使用最新的数据
            recent_closes = closes[-lookback_period-1:]
            
            # 1. 计算技术指标
            # 计算布林带
            bb_dict = self._calculate_bollinger_bands(recent_closes, lookback_period, bb_std_dev)
            current_lower_band = bb_dict['lower_band'][-1]
            current_upper_band = bb_dict['upper_band'][-1]
            current_middle_band = bb_dict['middle_band'][-1]
            
            # 布林带百分比 (%b) = (最新收盘价 - 布林带下轨) / (布林带上轨 - 布林带下轨)
            if (current_upper_band - current_lower_band) > 0:
                percent_b = (current_price - current_lower_band) / (current_upper_band - current_lower_band)
            else:
                percent_b = 0.5
            
            # 计算RSI
            rsi_values = self._calculate_rsi(recent_closes, rsi_period)
            current_rsi = rsi_values[-1] if len(rsi_values) > 0 else 50
            
            # 2. 定义判断逻辑
            is_potential_low = False
            is_potential_high = False
            reasoning = ""
            
            # 可能低位的条件：价格处于布林带下部，且RSI处于超卖区域
            if percent_b < low_threshold and current_rsi < rsi_oversold:
                is_potential_low = True
                reasoning = f"价格处于布林带下部{low_threshold*100}%区域(%b={percent_b:.2f})且RSI超卖({current_rsi:.1f})"
            
            # 可能高位的条件：价格处于布林带上部，且RSI处于超买区域
            elif percent_b > high_threshold and current_rsi > rsi_overbought:
                is_potential_high = True
                reasoning = f"价格处于布林带上部{(1-high_threshold)*100}%区域(%b={percent_b:.2f})且RSI超买({current_rsi:.1f})"
            
            else:
                reasoning = "价格处于中性区域，未检测到极端高位或低位信号"
            
            # 3. 确定位置和置信度
            if is_potential_low:
                position = 'low'
                position_chinese = '低位'
                confidence = min(0.9, (low_threshold - percent_b) / low_threshold + (rsi_oversold - current_rsi) / rsi_oversold)
            elif is_potential_high:
                position = 'high'
                position_chinese = '高位'
                confidence = min(0.9, (percent_b - high_threshold) / (1 - high_threshold) + (current_rsi - rsi_overbought) / (100 - rsi_overbought))
            else:
                position = 'medium'
                position_chinese = '中位'
                confidence = 0.5
            
            # 确保置信度在合理范围内
            confidence = max(0.1, min(0.9, confidence))
            
            return {
                'position': position,
                'position_chinese': position_chinese,
                'confidence': confidence,
                'reasoning': reasoning,
                'indicators': {
                    'bollinger_bands': {
                        'upper_band': current_upper_band,
                        'middle_band': current_middle_band,
                        'lower_band': current_lower_band,
                        'percent_b': percent_b
                    },
                    'rsi': {
                        'value': current_rsi,
                        'oversold': rsi_oversold,
                        'overbought': rsi_overbought
                    }
                }
            }
            
        except Exception as e:
            print(f"评估市场位置时出错: {e}")
            return self._get_default_position_result(f"评估出错: {str(e)}")
    
    def _calculate_bollinger_bands(self, prices: np.ndarray, period: int = 20, std_dev: float = 2.0) -> Dict:
        """
        计算布林带
        
        Args:
            prices: 价格数组
            period: 移动平均周期
            std_dev: 标准差倍数
            
        Returns:
            Dict: 包含上轨、中轨、下轨的字典
        """
        try:
            if len(prices) < period:
                return {
                    'upper_band': [prices[-1]] * len(prices),
                    'middle_band': [prices[-1]] * len(prices),
                    'lower_band': [prices[-1]] * len(prices)
                }
            
            # 计算移动平均
            middle_band = []
            upper_band = []
            lower_band = []
            
            for i in range(len(prices)):
                if i < period - 1:
                    # 数据不足时使用当前价格
                    middle_band.append(prices[i])
                    upper_band.append(prices[i])
                    lower_band.append(prices[i])
                else:
                    # 计算移动平均和标准差
                    window = prices[i-period+1:i+1]
                    ma = np.mean(window)
                    std = np.std(window)
                    
                    middle_band.append(ma)
                    upper_band.append(ma + std_dev * std)
                    lower_band.append(ma - std_dev * std)
            
            return {
                'upper_band': upper_band,
                'middle_band': middle_band,
                'lower_band': lower_band
            }
            
        except Exception as e:
            print(f"计算布林带时出错: {e}")
            return {
                'upper_band': [prices[-1]] * len(prices),
                'middle_band': [prices[-1]] * len(prices),
                'lower_band': [prices[-1]] * len(prices)
            }
    
    def _calculate_rsi(self, prices: np.ndarray, period: int = 14) -> List[float]:
        """
        计算RSI指标
        
        Args:
            prices: 价格数组
            period: RSI计算周期
            
        Returns:
            List[float]: RSI值列表
        """
        try:
            if len(prices) < period + 1:
                return [50.0] * len(prices)  # 数据不足时返回中性值
            
            rsi_values = []
            
            for i in range(len(prices)):
                if i < period:
                    rsi_values.append(50.0)  # 初始值设为中性
                else:
                    # 计算价格变化
                    changes = np.diff(prices[i-period:i+1])
                    
                    # 分离上涨和下跌
                    gains = np.where(changes > 0, changes, 0)
                    losses = np.where(changes < 0, -changes, 0)
                    
                    # 计算平均上涨和下跌
                    avg_gain = np.mean(gains)
                    avg_loss = np.mean(losses)
                    
                    # 计算RSI
                    if avg_loss == 0:
                        rsi = 100.0
                    else:
                        rs = avg_gain / avg_loss
                        rsi = 100.0 - (100.0 / (1.0 + rs))
                    
                    rsi_values.append(rsi)
            
            return rsi_values
            
        except Exception as e:
            print(f"计算RSI时出错: {e}")
            return [50.0] * len(prices)
    
    def _load_historical_data(self, stock_code: str) -> Optional[pd.DataFrame]:
        """从统一缓存加载60分钟K线历史数据"""
        try:
            # 尝试使用统一缓存
            try:
                from ui.unified_historical_cache import get_unified_cache
                unified_cache = get_unified_cache()
                
                # 获取最近21个交易日的60分钟K线数据（优化：只获取够用的数据）
                from datetime import date, timedelta
                from utils.trading_day import is_tradeday
                
                # 计算最近21个交易日（根据实际分析需求优化）
                target_dates = []
                current_date = date.today()
                found_trading_days = 0
                max_search_days = 42  # 最多往前找42天（约2倍，确保能找到21个交易日）
                search_count = 0
                
                while found_trading_days < 21 and search_count < max_search_days:
                    if is_tradeday(current_date):
                        target_dates.append(current_date)
                        found_trading_days += 1
                    
                    current_date -= timedelta(days=1)
                    search_count += 1
                
                # 获取60分钟K线数据
                kline_data = unified_cache.get_multiple_days_kline_data(stock_code, target_dates)
                
                if not kline_data.empty:
                    print(f"✓ 从统一缓存加载了 {stock_code} 的 {len(kline_data)} 条60分钟K线数据（优化：只获取21个交易日）")
                    
                    # 检查K线数据是否充足（至少需要21条用于分析）
                    if len(kline_data) >= 21:
                        return kline_data
                    else:
                        print(f"⚠️ 统一缓存中的60分钟K线数据不足21条（当前{len(kline_data)}条），历史数据不够，无法进行准确分析")
                        # 返回现有数据，即使不足21条
                        return kline_data
                
            except ImportError:
                print("警告: 无法导入统一缓存模块")
            except Exception as e:
                print(f"统一缓存加载失败: {e}")
            
            return None
            
        except Exception as e:
            print(f"加载历史数据时出错: {e}")
            return None
    
    def _get_default_position_result(self, reason: str) -> Dict:
        """获取默认位置结果"""
        return {
            'position': 'medium',
            'position_chinese': '中位',
            'confidence': 0.3,
            'reasoning': reason,
            'indicators': {},
            'current_price': 0,
            'analysis_date': None,
            'stock_code': ''
        }
    
    def is_low_position(self, position_result: Dict) -> bool:
        """判断是否为低位"""
        return position_result.get('position') == 'low'
    
    def is_high_position(self, position_result: Dict) -> bool:
        """判断是否为高位"""
        return position_result.get('position') == 'high'
    
    def is_medium_position(self, position_result: Dict) -> bool:
        """判断是否为中位"""
        return position_result.get('position') == 'medium'
