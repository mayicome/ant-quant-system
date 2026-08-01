from strategies.base_strategy import BaseStrategy
from datetime import datetime, timedelta
from utils.trading_day import is_tradeday
from brokers.capital_flow import get_capital_flow
import math
import pandas as pd
import time

class AggressiveStrategy(BaseStrategy):
    def __init__(self, task_info, log_pipe, control_pipe):
        super().__init__(task_info, log_pipe, control_pipe)

    def _on_tick(self, tick_data):
        """处理tick数据"""
        try:
            current_price = tick_data['lastPrice']
            # 获取买卖盘数据（五档）
            ask_prices = tick_data.get('askPrice', [current_price] * 5)  # 卖档价格
            bid_prices = tick_data.get('bidPrice', [current_price] * 5)  # 买档价格
            ask_vols = tick_data.get('askVol', [0] * 5)  # 卖档量
            bid_vols = tick_data.get('bidVol', [0] * 5)  # 买档量
            
            signals = []
            
            # 计算每次卖出数量
            if 'trade_volume' in self.params:
                # 新版本：使用每笔操作股数
                volume = self.params['trade_volume']
            else:
                # 旧版本：使用分仓笔数（保持兼容性）
                volume = math.ceil(self.init_volume/self.params.get('sell_times', 999) / 100 ) * 100
            
            # 计算上下限阈值价格
            up_threshold_price = round(self.base_price * (1 + self.params['up_threshold'] / 100), 2)
            down_threshold_price = round(self.base_price * (1 - self.params['down_threshold'] / 100), 2)
            
            # 添加调试日志
            self.log_pipe.send(f"[{self.stock_code}] 收到tick数据: 当前价={current_price:.3f}, 基准价={self.base_price:.3f}, 上限阈值={up_threshold_price:.3f}, 下限阈值={down_threshold_price:.3f}")
            
            # 检查是否触发止损
            if current_price <= down_threshold_price:
                # 检查是否是回测模式
                is_backtest = hasattr(self, 'is_backtest') and self.is_backtest
                trade_direction = 'sell'
                if is_backtest:
                    # 回测模式下，默认资金流条件成立
                    self.log_pipe.send(f"[{self.stock_code}] 回测模式：跳过资金流检查，默认条件成立")
                    trade_direction = 'buy'
                else:
                    # 实盘模式下，检查资金流
                    found, main_flow, main_rate = get_capital_flow(self.stock_code)
                    if not (found and main_flow > 10000000 and main_rate > 0.05):
                        #self.log_pipe.send(f"[{self.stock_code}] 主力净流入{main_flow}，占比{main_rate}，触发反向操作信号。当前盈亏：{profit_percent:.2f}%")
                        trade_direction = 'buy'
                    #else:
                    #    self.log_pipe.send(f"[{self.stock_code}] 主力净流入{main_flow}，占比{main_rate}，触发止损信号。当前盈亏：{profit_percent:.2f}%")
                            
                signal = {
                    'type': trade_direction,
                    'price': current_price,
                    'volume': volume,
                    'reason': '反向操作' if trade_direction == 'buy' else '止损',
                    'askPrice': ask_prices,
                    'bidPrice': bid_prices,
                    'askVol': ask_vols,
                    'bidVol': bid_vols,
                    'time': tick_data['time'].strftime('%Y-%m-%d %H:%M:%S')  # 转换为易读的字符串格式
                }
                signals.append(signal)
                # 直接更新策略中的基准价
                old_base_price = self.base_price
                self.base_price = current_price
                # 同时通知任务管理器更新基准价
                self.log_pipe.send(f"[{self.stock_code}] 触发反向操作信号，基准价从 {old_base_price:.3f} 更新为 {current_price:.3f}")
                self.log_pipe.send(('update_base_price', {
                    'stock_code': self.stock_code,
                    'base_price': current_price
                }))
                if trade_direction == 'buy':
                    self.log_pipe.send(f"[{self.stock_code}] 触发反向操作信号: {signal}。当前价格{current_price:.3f} <= 下限阈值{down_threshold_price:.3f}")
                #else:
                #    self.log_pipe.send(f"[{self.stock_code}] 触发止损信号: {signal}。当前盈亏：{profit_percent:.2f}%")
            
            # 右侧逻辑 不止盈，提高基准价
            elif current_price > up_threshold_price:
                self.log_pipe.send(f"[{self.stock_code}] 当前价格{current_price:.3f} > 上限阈值{up_threshold_price:.3f}，提高基准价")
                # 直接更新策略中的基准价
                old_base_price = self.base_price
                self.base_price = current_price
                # 同时通知任务管理器更新基准价
                self.log_pipe.send(f"[{self.stock_code}] 上涨触发提高基准价，基准价从 {old_base_price:.3f} 更新为 {current_price:.3f}")
                self.log_pipe.send(('update_base_price', {
                    'stock_code': self.stock_code,
                    'base_price': current_price
                }))
                #self.log_pipe.send(f"[{self.stock_code}] 触发提高基准价（不止盈）信号。当前盈亏：{profit_percent:.2f}%")
            else:
                # 价格在阈值范围内，不操作
                self.log_pipe.send(f"[{self.stock_code}] 当前价格{current_price:.3f}在阈值范围内[{down_threshold_price:.3f}, {up_threshold_price:.3f}]")
                
            return signals
            
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 策略处理出错: {str(e)}")
            return []
