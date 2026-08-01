from strategies.base_strategy import BaseStrategy
from datetime import datetime, timedelta
from utils.trading_day import is_tradeday
from brokers.capital_flow import get_capital_flow
import math
import pandas as pd
import time

class ModerateStrategy(BaseStrategy):
    def __init__(self, task_info, log_pipe, control_pipe):
        super().__init__(task_info, log_pipe, control_pipe)
        self.last_check_date = None  # 添加上次检查日期
        self.current_cycle = 0  # 当前循环次数
        self.max_cycles = self.params.get('cycle_times', 0)  # 最大循环次数
        self.should_stop = False  # 添加停止标志
        # 从任务信息中获取等待状态
        self.waiting_for_threshold = task_info.get('waiting_for_threshold', False)
        
        # 智能卖出等待状态
        self.waiting_for_sell = task_info.get('waiting_for_sell', False)  # 是否在等待卖出
        self.highest_price = task_info.get('highest_price', 0.0)  # 等待期间的最高价格
        self.wait_start_time = task_info.get('wait_start_time', None)  # 等待开始时间
        self.enable_smart_sell = self.params.get('enable_smart_sell', True)  # 是否启用智能卖出
        self.sell_drop_threshold = self.params.get('sell_drop_threshold', 0.002)  # 下落阈值
        self.sell_timeout = self.params.get('sell_timeout', 14400)  # 超时时间（秒）
        
        # 智能买入等待状态
        self.waiting_for_buy = task_info.get('waiting_for_buy', False)  # 是否在等待买入
        self.lowest_price = task_info.get('lowest_price', 0.0)  # 等待期间的最低价格
        self.buy_wait_start_time = task_info.get('buy_wait_start_time', None)  # 买入等待开始时间
        self.enable_smart_buy = self.params.get('enable_smart_buy', True)  # 是否启用智能买入
        self.buy_rebound_threshold = self.params.get('buy_rebound_threshold', 0.002)  # 反弹阈值
        self.buy_timeout = self.params.get('buy_timeout', 14400)  # 买入超时时间（秒）
        
        # 如果恢复等待状态，重置开始时间
        if self.waiting_for_sell and self.wait_start_time:
            self.wait_start_time = time.time()
        if self.waiting_for_buy and self.buy_wait_start_time:
            self.buy_wait_start_time = time.time()
        
        if self.waiting_for_threshold:
            self.log_pipe.send(f"[{self.stock_code}] 策略启动，等待价格回到阈值范围内")

        # 有 rules 且未配置涨跌操作时：交易仅由主进程图表规则驱动，子进程不跑基准价阈值带
        self._suppress_threshold_status_update = self._rules_only_subprocess_mode()

    def _rules_only_subprocess_mode(self):
        """params 含 rules 且未出现 up_operation/down_operation 键时视为纯规则任务（与旧版「万能+阈值」区分）。"""
        p = self.params if isinstance(self.params, dict) else {}
        rules = p.get('rules')
        if not isinstance(rules, list) or len(rules) == 0:
            return False
        if 'up_operation' in p or 'down_operation' in p:
            return False
        return True

    def _get_price_precision(self, stock_code):
        """根据股票代码确定价格精度"""
        if stock_code.startswith('51') or stock_code.startswith('52') or stock_code.startswith('56') or stock_code.startswith('58'):
            # ETF基金，价格精度为0.001（3位小数）
            return 3
        elif stock_code.startswith('688'):
            # 科创板，价格精度为0.01（2位小数）
            return 2
        else:
            # 其他股票，价格精度为0.01（2位小数）
            return 2

    def _on_tick(self, tick_data):
        """处理tick数据"""
        # 检查是否应该停止
        if self.should_stop:
            return []

        if self._rules_only_subprocess_mode():
            return []
        
        try:
            current_price = tick_data['lastPrice']
            # 获取买卖盘数据（五档）
            ask_prices = tick_data.get('askPrice', [current_price] * 5)  # 卖档价格
            bid_prices = tick_data.get('bidPrice', [current_price] * 5)  # 买档价格
            ask_vols = tick_data.get('askVol', [0] * 5)  # 卖档量
            bid_vols = tick_data.get('bidVol', [0] * 5)  # 买档量
            
            # 添加调试日志
            #self.log_pipe.send(f"[{self.stock_code}] 收到tick数据: 当前价={current_price:.3f}, 基准价={self.base_price:.3f}, 上限阈值={self.params['up_threshold']}%")
            
            signals = []
            
            # 检查是否在等待价格回到阈值范围内
            if self.waiting_for_threshold:
                if self._check_price_back_in_range(current_price):
                    self.waiting_for_threshold = False
                    self.log_pipe.send(f"[{self.stock_code}] 价格已回到阈值范围内，开始正常监控")
                else:
                    # 仍在等待，不执行任何操作
                    return signals
            
            # 计算每次交易数量
            if 'trade_volume' in self.params:
                # 新版本：使用每笔操作股数
                volume = self.params['trade_volume']
            else:
                # 旧版本：使用分仓笔数（保持兼容性）
                volume = math.ceil(self.init_volume/self.params.get('sell_times', 999) / 100 ) * 100
            
            # 规则任务（含历史“万能策略”文案）不需要检查持仓，因为它支持无持仓场景
            strategy_name = (self.task_info.get('strategy', '') or '')
            is_rule_task = strategy_name.startswith('万能') or strategy_name.startswith('规则')
            # 只有当策略类型不是规则任务时才检查持仓
            if not is_rule_task:
                # 检查可用持仓是否为0
                if self.init_volume <= 0:
                    try:
                        self.log_pipe.send(f"[{self.stock_code}] 可用持仓为0，策略结束")
                    except (EOFError, BrokenPipeError, OSError):
                        pass
                    # 通知任务管理器更新任务状态为已完成
                    try:
                        self.log_pipe.send(('update_task_status', {
                            'stock_code': self.stock_code,
                            'status': '已完成',
                            'reason': '可用持仓为0'
                        }))
                    except (EOFError, BrokenPipeError, OSError):
                        pass
                    # 主动发送停止信号，确保策略进程正确退出
                    try:
                        self.control_pipe.send('stop')
                    except (EOFError, BrokenPipeError, OSError):
                        pass
                    return signals
            
            # 根据股票类型确定价格精度
            price_precision = self._get_price_precision(self.stock_code)
            
            # 计算上下限阈值，使用动态价格精度
            # 当阈值为0时，使用基准价作为阈值价格
            if self.params['up_threshold'] == 0.0:
                up_threshold_price = round(self.base_price, price_precision)
            else:
                up_threshold_price = round(self.base_price * (1 + self.params['up_threshold'] / 100), price_precision)
            
            if self.params['down_threshold'] == 0.0:
                down_threshold_price = round(self.base_price, price_precision)
            else:
                down_threshold_price = round(self.base_price * (1 - self.params['down_threshold'] / 100), price_precision)
            
            # 添加阈值调试日志
            #self.log_pipe.send(f"[{self.stock_code}] 阈值计算: 基准价={self.base_price:.3f}, 上涨阈值={up_threshold_price:.3f}, 下跌阈值={down_threshold_price:.3f}")
            
            # 逐 tick 会刷屏；纯规则模式已在上方 return，此处仅旧版阈值任务可能执行到
            
            # 获取操作参数，默认为传统行为
            up_operation = self.params.get('up_operation', '卖出')
            down_operation = self.params.get('down_operation', '买入')
            
            # 用当前价格与阈值比较
            #self.log_pipe.send(f"[{self.stock_code}] 价格比较: 当前价={current_price:.3f}, 上涨阈值={up_threshold_price:.3f}, 下跌阈值={down_threshold_price:.3f}")
            # 当阈值为0时，使用>=比较；否则使用>比较
            if (self.params['up_threshold'] == 0.0 and current_price >= up_threshold_price) or (self.params['up_threshold'] > 0.0 and current_price > up_threshold_price):
                #self.log_pipe.send(f"[{self.stock_code}] 触发上涨条件！当前价={current_price:.3f} > 上涨阈值={up_threshold_price:.3f}")
                # 计算实际涨幅
                if self.params['up_threshold'] == 0.0:
                    actual_increase = 0.0  # 阈值为0时，涨跌幅为0
                else:
                    actual_increase = ((current_price - self.base_price) / self.base_price * 100)
                
                # 首先检查循环次数限制
                if self.max_cycles > 0 and self.current_cycle >= self.max_cycles:
                    self.log_pipe.send(f"[{self.stock_code}] 已达到最大循环次数{self.max_cycles}，停止触发操作")
                    return signals
                
                # 获取操作类型
                up_operation = self.params.get('up_operation', '卖出')
                #self.log_pipe.send(f"[{self.stock_code}] 上涨操作类型: {up_operation}")
                
                # 根据操作类型执行相应逻辑
                if up_operation == '卖出':
                    # 当阈值为0时直接触发，否则只有当实际涨幅大于0.01%时才触发卖出操作
                    if self.params['up_threshold'] == 0.0 or actual_increase > 0.01:
                        # 检查是否启用智能卖出
                        if self.enable_smart_sell:
                            # 检查是否已经在等待卖出
                            if self.waiting_for_sell:
                                # 更新最高价格
                                if current_price > self.highest_price:
                                    self.highest_price = current_price
                                    self.log_pipe.send(f"[{self.stock_code}] 等待卖出中，更新最高价格: {self.highest_price:.{price_precision}f}")
                                    
                                    # 发送等待状态更新
                                    self.log_pipe.send(('update_waiting_state', {
                                        'stock_code': self.stock_code,
                                        'waiting_state': self.get_waiting_state()
                                    }))
                            
                            # 检查是否应该执行卖出
                            should_sell = False
                            sell_reason = ""
                            
                            # 检查是否下落超过阈值
                            if current_price < self.highest_price * (1 - self.sell_drop_threshold):
                                should_sell = True
                                sell_reason = f"价格从最高点{self.highest_price:.{price_precision}f}下落{(self.highest_price - current_price)/self.highest_price*100:.2f}%，触发卖出"
                            
                            # 检查是否超时
                            elif self.wait_start_time and (time.time() - self.wait_start_time) > self.sell_timeout:
                                should_sell = True
                                sell_reason = f"等待卖出超时{self.sell_timeout}秒，以当前价格{current_price:.{price_precision}f}卖出"
                            
                            # 检查是否跌破基准价（保护性卖出）
                            elif current_price < self.base_price:
                                should_sell = True
                                sell_reason = f"价格跌破基准价{self.base_price:.{price_precision}f}，保护性卖出"
                            
                            if should_sell:
                                # 执行卖出
                                signal = {
                                    'type': 'sell',
                                    'price': current_price,
                                    'volume': volume,
                                    'reason': sell_reason,
                                    'askPrice': ask_prices,
                                    'bidPrice': bid_prices,
                                    'askVol': ask_vols,
                                    'bidVol': bid_vols,
                                    'time': tick_data['time'].strftime('%Y-%m-%d %H:%M:%S')
                                }
                                signals.append(signal)
                                
                                # 更新基准价
                                old_base_price = self.base_price
                                self.base_price = current_price
                                self.log_pipe.send(f"[{self.stock_code}] 智能卖出完成，基准价从 {old_base_price:.{price_precision}f} 更新为 {current_price:.{price_precision}f}")
                                self.log_pipe.send(('update_base_price', {
                                    'stock_code': self.stock_code,
                                    'base_price': current_price
                                }))
                                
                                # 重置等待状态
                                self.waiting_for_sell = False
                                self.highest_price = 0.0
                                self.wait_start_time = None
                                
                                # 发送等待状态更新（清除状态）
                                self.log_pipe.send(('update_waiting_state', {
                                    'stock_code': self.stock_code,
                                    'waiting_state': self.get_waiting_state()
                                }))
                                
                                # 增加循环次数
                                self.current_cycle += 1
                                self.log_pipe.send(f"[{self.stock_code}] 智能卖出触发，第{self.current_cycle}次循环")
                                
                                # 检查是否应该停止任务
                                if self._check_and_stop_if_needed():
                                    return signals
                            else:
                                # 开始等待卖出
                                self.waiting_for_sell = True
                                self.highest_price = current_price
                                self.wait_start_time = time.time()
                                self.log_pipe.send(f"[{self.stock_code}] 上涨触发智能卖出等待，当前价格: {current_price:.{price_precision}f}，等待更高价格或下落{self.sell_drop_threshold*100:.1f}%")
                                
                                # 发送等待状态更新
                                self.log_pipe.send(('update_waiting_state', {
                                    'stock_code': self.stock_code,
                                    'waiting_state': self.get_waiting_state()
                                }))
                        else:
                            # 传统卖出：立即执行
                            signal = {
                                'type': 'sell',
                                'price': current_price,
                                'volume': volume,
                                'reason': f'上涨{actual_increase:.2f}%触发卖出',
                                'askPrice': ask_prices,
                                'bidPrice': bid_prices,
                                'askVol': ask_vols,
                                'bidVol': bid_vols,
                                'time': tick_data['time'].strftime('%Y-%m-%d %H:%M:%S')
                            }
                            signals.append(signal)
                            
                            # 更新基准价
                            old_base_price = self.base_price
                            self.base_price = current_price
                            self.log_pipe.send(f"[{self.stock_code}] 上涨触发卖出操作，基准价从 {old_base_price:.{price_precision}f} 更新为 {current_price:.{price_precision}f}")
                            self.log_pipe.send(('update_base_price', {
                                'stock_code': self.stock_code,
                                'base_price': current_price
                            }))
                            
                            # 增加循环次数
                            self.current_cycle += 1
                            self.log_pipe.send(f"[{self.stock_code}] 上涨卖出触发，第{self.current_cycle}次循环")
                            
                            # 检查是否应该停止任务
                            if self._check_and_stop_if_needed():
                                return signals
                elif up_operation == '买入':
                    # 当阈值为0时直接触发，否则只有当实际涨幅大于0.01%时才触发买入操作
                    if self.params['up_threshold'] == 0.0 or actual_increase > 0.01:
                        # 上涨时买入
                        signal = {
                            'type': 'buy',
                            'price': current_price,
                            'volume': volume,
                            'reason': f'上涨{actual_increase:.2f}%触发买入',
                            'askPrice': ask_prices,
                            'bidPrice': bid_prices,
                            'askVol': ask_vols,
                            'bidVol': bid_vols,
                            'time': tick_data['time'].strftime('%Y-%m-%d %H:%M:%S')
                        }
                        signals.append(signal)
                        # 更新基准价
                        old_base_price = self.base_price
                        self.base_price = current_price
                        self.log_pipe.send(f"[{self.stock_code}] 上涨触发买入操作，基准价从 {old_base_price:.{price_precision}f} 更新为 {current_price:.{price_precision}f}")
                        self.log_pipe.send(f"[{self.stock_code}] 调试：准备发送update_base_price消息到任务管理器")
                        self.log_pipe.send(('update_base_price', {
                            'stock_code': self.stock_code,
                            'base_price': current_price
                        }))
                        self.log_pipe.send(f"[{self.stock_code}] 调试：已发送update_base_price消息到任务管理器")
                        # 增加循环次数（只有实际交易操作才增加）
                        self.current_cycle += 1
                        self.log_pipe.send(f"[{self.stock_code}] 上涨买入触发，第{self.current_cycle}次循环")
                        
                        # 检查是否应该停止任务
                        if self._check_and_stop_if_needed():
                            return signals
                elif up_operation == '不动':
                    # 上涨时不动，只更新基准价，不消耗循环次数
                    old_base_price = self.base_price
                    self.base_price = current_price
                    self.log_pipe.send(f"[{self.stock_code}] 上涨触发不动操作，基准价从 {old_base_price:.{price_precision}f} 更新为 {current_price:.{price_precision}f}")
                    #self.log_pipe.send(f"[{self.stock_code}] 调试：准备发送update_base_price信号到task_manager")
                    self.log_pipe.send(('update_base_price', {
                        'stock_code': self.stock_code,
                        'base_price': current_price
                    }))
                    #self.log_pipe.send(f"[{self.stock_code}] 调试：已发送update_base_price信号")
                    #self.log_pipe.send(f"[{self.stock_code}] 不动操作不消耗循环次数，当前循环次数: {self.current_cycle}/{self.max_cycles}")
                    
            elif (self.params['down_threshold'] == 0.0 and current_price <= down_threshold_price) or (self.params['down_threshold'] > 0.0 and current_price < down_threshold_price):
                #self.log_pipe.send(f"[{self.stock_code}] 触发下跌条件！当前价={current_price:.3f} < 下跌阈值={down_threshold_price:.3f}")
                # 计算实际跌幅
                if self.params['down_threshold'] == 0.0:
                    actual_decrease = 0.0  # 阈值为0时，涨跌幅为0
                else:
                    actual_decrease = ((self.base_price - current_price) / self.base_price * 100)
                
                # 首先检查循环次数限制
                if self.max_cycles > 0 and self.current_cycle >= self.max_cycles:
                    self.log_pipe.send(f"[{self.stock_code}] 已达到最大循环次数{self.max_cycles}，停止触发操作")
                    return signals
                
                # 获取操作类型
                down_operation = self.params.get('down_operation', '买入')
                #self.log_pipe.send(f"[{self.stock_code}] 下跌操作类型: {down_operation}")
                
                # 根据操作类型执行相应逻辑
                if down_operation == '买入':
                    # 当阈值为0时直接触发，否则只有当实际跌幅大于0.01%时才触发买入操作
                    if self.params['down_threshold'] == 0.0 or actual_decrease > 0.01:
                        # 检查是否启用智能买入
                        if self.enable_smart_buy:
                            # 检查是否已经在等待买入
                            if self.waiting_for_buy:
                                # 更新最低价格
                                if current_price < self.lowest_price:
                                    self.lowest_price = current_price
                                    self.log_pipe.send(f"[{self.stock_code}] 等待买入中，更新最低价格: {self.lowest_price:.{price_precision}f}")
                                    
                                    # 发送等待状态更新
                                    self.log_pipe.send(('update_waiting_state', {
                                        'stock_code': self.stock_code,
                                        'waiting_state': self.get_waiting_state()
                                    }))
                                
                                # 检查是否应该执行买入
                                should_buy = False
                                buy_reason = ""
                                
                                # 检查是否反弹超过阈值
                                if current_price > self.lowest_price * (1 + self.buy_rebound_threshold):
                                    should_buy = True
                                    buy_reason = f"价格从最低点{self.lowest_price:.{price_precision}f}反弹{(current_price - self.lowest_price)/self.lowest_price*100:.2f}%，触发买入"
                                
                                # 检查是否超时
                                elif self.buy_wait_start_time and (time.time() - self.buy_wait_start_time) > self.buy_timeout:
                                    should_buy = True
                                    buy_reason = f"等待买入超时{self.buy_timeout}秒，以当前价格{current_price:.{price_precision}f}买入"
                                
                                # 检查是否涨破基准价（保护性买入）
                                elif current_price > self.base_price:
                                    should_buy = True
                                    buy_reason = f"价格涨破基准价{self.base_price:.{price_precision}f}，保护性买入"
                                
                                if should_buy:
                                    # 执行买入
                                    signal = {
                                        'type': 'buy',
                                        'price': current_price,
                                        'volume': volume,
                                        'reason': buy_reason,
                                        'askPrice': ask_prices,
                                        'bidPrice': bid_prices,
                                        'askVol': ask_vols,
                                        'bidVol': bid_vols,
                                        'time': tick_data['time'].strftime('%Y-%m-%d %H:%M:%S')
                                    }
                                    signals.append(signal)
                                    
                                    # 更新基准价
                                    old_base_price = self.base_price
                                    self.base_price = current_price
                                    self.log_pipe.send(f"[{self.stock_code}] 智能买入完成，基准价从 {old_base_price:.{price_precision}f} 更新为 {current_price:.{price_precision}f}")
                                    self.log_pipe.send(('update_base_price', {
                                        'stock_code': self.stock_code,
                                        'base_price': current_price
                                    }))
                                    
                                    # 重置等待状态
                                    self.waiting_for_buy = False
                                    self.lowest_price = 0.0
                                    self.buy_wait_start_time = None
                                    
                                    # 发送等待状态更新（清除状态）
                                    self.log_pipe.send(('update_waiting_state', {
                                        'stock_code': self.stock_code,
                                        'waiting_state': self.get_waiting_state()
                                    }))
                                    
                                    # 增加循环次数
                                    self.current_cycle += 1
                                    self.log_pipe.send(f"[{self.stock_code}] 智能买入触发，第{self.current_cycle}次循环")
                                    
                                    # 检查是否应该停止任务
                                    if self._check_and_stop_if_needed():
                                        return signals
                            else:
                                # 开始等待买入
                                self.waiting_for_buy = True
                                self.lowest_price = current_price
                                self.buy_wait_start_time = time.time()
                                self.log_pipe.send(f"[{self.stock_code}] 下跌触发智能买入等待，当前价格: {current_price:.{price_precision}f}，等待更低价格或反弹{self.buy_rebound_threshold*100:.1f}%")
                                
                                # 发送等待状态更新
                                self.log_pipe.send(('update_waiting_state', {
                                    'stock_code': self.stock_code,
                                    'waiting_state': self.get_waiting_state()
                                }))
                        else:
                            # 传统买入：立即执行
                            signal = {
                                'type': 'buy',
                                'price': current_price,
                                'volume': volume,
                                'reason': f'下跌{actual_decrease:.2f}%触发买入',
                                'askPrice': ask_prices,
                                'bidPrice': bid_prices,
                                'askVol': ask_vols,
                                'bidVol': bid_vols,
                                'time': tick_data['time'].strftime('%Y-%m-%d %H:%M:%S')
                            }
                            signals.append(signal)
                            
                            # 更新基准价
                            old_base_price = self.base_price
                            self.base_price = current_price
                            self.log_pipe.send(f"[{self.stock_code}] 下跌触发买入操作，基准价从 {old_base_price:.{price_precision}f} 更新为 {current_price:.{price_precision}f}")
                            self.log_pipe.send(('update_base_price', {
                                'stock_code': self.stock_code,
                                'base_price': current_price
                            }))
                            
                            # 增加循环次数
                            self.current_cycle += 1
                            self.log_pipe.send(f"[{self.stock_code}] 下跌买入触发，第{self.current_cycle}次循环")
                            
                            # 检查是否应该停止任务
                            if self._check_and_stop_if_needed():
                                return signals
                elif down_operation == '卖出':
                    # 当阈值为0时直接触发，否则只有当实际跌幅大于0.01%时才触发卖出操作
                    if self.params['down_threshold'] == 0.0 or actual_decrease > 0.01:
                        # 传统行为：下跌时卖出
                        signal = {
                            'type': 'sell',
                            'price': current_price,
                            'volume': volume,
                            'reason': f'下跌{actual_decrease:.2f}%触发卖出',
                            'askPrice': ask_prices,
                            'bidPrice': bid_prices,
                            'askVol': ask_vols,
                            'bidVol': bid_vols,
                            'time': tick_data['time'].strftime('%Y-%m-%d %H:%M:%S')
                        }
                        signals.append(signal)
                        # 更新基准价
                        old_base_price = self.base_price
                        self.base_price = current_price
                        self.log_pipe.send(f"[{self.stock_code}] 下跌触发卖出操作，基准价从 {old_base_price:.{price_precision}f} 更新为 {current_price:.{price_precision}f}")
                        self.log_pipe.send(('update_base_price', {
                            'stock_code': self.stock_code,
                            'base_price': current_price
                        }))
                        # 增加循环次数（只有实际交易操作才增加）
                        self.current_cycle += 1
                        self.log_pipe.send(f"[{self.stock_code}] 下跌卖出触发，第{self.current_cycle}次循环")
                        
                        # 检查是否应该停止任务
                        if self._check_and_stop_if_needed():
                            return signals
                elif down_operation == '不动':
                    # 下跌时不动，只更新基准价，不消耗循环次数
                    old_base_price = self.base_price
                    self.base_price = current_price
                    self.log_pipe.send(f"[{self.stock_code}] 下跌触发不动操作，基准价从 {old_base_price:.{price_precision}f} 更新为 {current_price:.{price_precision}f}")
                    #self.log_pipe.send(f"[{self.stock_code}] 调试：准备发送update_base_price信号到task_manager")
                    self.log_pipe.send(('update_base_price', {
                        'stock_code': self.stock_code,
                        'base_price': current_price
                    }))
                    #self.log_pipe.send(f"[{self.stock_code}] 调试：已发送update_base_price信号")
                    #self.log_pipe.send(f"[{self.stock_code}] 不动操作不消耗循环次数，当前循环次数: {self.current_cycle}/{self.max_cycles}")
                    
            else:
                # 价格在阈值范围内，不操作
                #self.log_pipe.send(f"[{self.stock_code}] 当前价格{current_price:.{price_precision}f}在阈值范围内[{down_threshold_price:.{price_precision}f}, {up_threshold_price:.{price_precision}f}]")
                pass
            
            return signals
            
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 策略处理出错: {str(e)}")
            return []
    
    def _check_price_back_in_range(self, current_price):
        """检查价格是否已回到阈值范围内"""
        try:
            # 根据股票类型确定价格精度
            price_precision = self._get_price_precision(self.stock_code)
            
            # 计算上下限阈值，使用动态价格精度
            if self.params['up_threshold'] == 0.0:
                up_threshold_price = round(self.base_price, price_precision)
            else:
                up_threshold_price = round(self.base_price * (1 + self.params['up_threshold'] / 100), price_precision)
            
            if self.params['down_threshold'] == 0.0:
                down_threshold_price = round(self.base_price, price_precision)
            else:
                down_threshold_price = round(self.base_price * (1 - self.params['down_threshold'] / 100), price_precision)
            
            # 检查价格是否在阈值范围内
            return down_threshold_price <= current_price <= up_threshold_price
            
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 检查价格是否回到阈值范围失败: {str(e)}")
            return True  # 出错时返回True，继续正常监控
    
    def get_waiting_state(self):
        """获取等待状态，用于保存到任务信息中"""
        return {
            'waiting_for_sell': self.waiting_for_sell,
            'highest_price': self.highest_price,
            'wait_start_time': self.wait_start_time,
            'waiting_for_buy': self.waiting_for_buy,
            'lowest_price': self.lowest_price,
            'buy_wait_start_time': self.buy_wait_start_time
        }
    
    def _check_and_stop_if_needed(self):
        """检查是否应该停止任务（当cycle_times=0且已执行一次操作时）"""
        if self.max_cycles == 0 and self.current_cycle >= 1:
            self.logger.info(f"[{self.stock_code}] 循环次数为0，执行一次后结束")
            # 设置停止标志，阻止后续tick处理
            self.should_stop = True
            # 直接发送停止信号，让进程正常退出
            try:
                self.control_pipe.send('stop')
            except (EOFError, BrokenPipeError, OSError):
                pass
            return True
        return False