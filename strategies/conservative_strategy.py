from strategies.base_strategy import BaseStrategy
from datetime import datetime, timedelta
from utils.trading_day import is_tradeday
import math
import pandas as pd

class ConservativeStrategy(BaseStrategy):
    def __init__(self, task_info, log_pipe, control_pipe):
        super().__init__(task_info, log_pipe, control_pipe)
        # 初始化交易计数器
        self.trade_count = 0
        # 记录已完成的交易次数
        self.completed_trades = 0
        # 添加止盈提前下单相关状态
        self.take_profit_order_id = None  # 止盈提前下单的订单ID
        self.take_profit_order_price = None  # 止盈提前下单的价格
        self.take_profit_order_base_price = None # 记录止盈提前下单时的基准价

    def _cancel_take_profit_order(self):
        """撤消止盈提前下单"""
        if self.take_profit_order_id:
            try:
                self.log_pipe.send(f"[{self.stock_code}] 撤消止盈提前下单，订单ID: {self.take_profit_order_id}")
                cancel_signal = {
                    'type': 'cancel',
                    'order_id': self.take_profit_order_id,
                    'reason': '撤消止盈提前下单',
                    'time': datetime.now()
                }
                self.send_trade_signal([cancel_signal])
                self.take_profit_order_id = None
                self.take_profit_order_price = None
                self.take_profit_order_base_price = None # 撤销时重置基准价
            except Exception as e:
                try:
                    self.log_pipe.send(f"[{self.stock_code}] 撤消止盈提前下单失败: {str(e)}")
                except (EOFError, BrokenPipeError, OSError):
                    pass

    def _place_take_profit_order(self, price, volume):
        """下止盈提前下单"""
        try:
            self.log_pipe.send(f"[{self.stock_code}] 下止盈提前下单: 价格={price}, 数量={volume}")
            
            signal = {
                'type': 'sell',
                'price': price,
                'volume': volume,
                'reason': '止盈提前下单',
                'time': datetime.now()
            }
            
            # 发送止盈提前下单信号
            self.send_trade_signal([signal])
            
            # 记录止盈提前下单信息（订单ID会在回调中设置）
            self.take_profit_order_price = price
            self.take_profit_order_base_price = self.base_price # 记录下单时的基准价
            
        except Exception as e:
            try:
                self.log_pipe.send(f"[{self.stock_code}] 下止盈提前下单失败: {str(e)}")
            except (EOFError, BrokenPipeError, OSError):
                pass

    def _on_tick(self, tick_data):
        """处理tick数据"""
        try:
            current_price = tick_data['lastPrice']            
            # 获取买卖盘数据（五档）
            ask_prices = tick_data.get('askPrices', [current_price] * 5)  # 卖档价格
            bid_prices = tick_data.get('bidPrices', [current_price] * 5)  # 买档价格
            ask_vols = tick_data.get('askVols', [0] * 5)  # 卖档量
            bid_vols = tick_data.get('bidVols', [0] * 5)  # 买档量

            signals = []
            
            # 检查是否已完成所有交易次数
            if self.completed_trades >= self.params['sell_times']:
                try:
                    self.log_pipe.send(f"[{self.stock_code}] 已完成所有{self.params['sell_times']}次交易，策略结束")
                except (EOFError, BrokenPipeError, OSError):
                    pass
                # 通知任务管理器更新任务状态为已完成
                try:
                    self.log_pipe.send(('update_task_status', {
                        'stock_code': self.stock_code,
                        'status': '已完成',
                        'reason': f'已完成{self.params["sell_times"]}次交易'
                    }))
                except (EOFError, BrokenPipeError, OSError):
                    pass
                # 主动发送停止信号，确保策略进程正确退出
                try:
                    self.control_pipe.send('stop')
                except (EOFError, BrokenPipeError, OSError):
                    pass
                return signals
            
            # 计算每次卖出数量
            if 'trade_volume' in self.params:
                # 新版本：使用每笔操作股数
                volume = self.params['trade_volume']
            else:
                # 旧版本：使用分仓笔数（保持兼容性）
                volume = math.ceil(self.init_volume/self.params['sell_times'] / 100) * 100
            
            # 计算上下限阈值价格
            up_threshold_price = round(self.base_price * (1 + self.params['up_threshold'] / 100), 2)
            down_threshold_price = round(self.base_price * (1 - self.params['down_threshold'] / 100), 2)
            
            try:
                self.log_pipe.send(f"[{self.stock_code}] 收到tick数据: 当前价={current_price:.3f}, 基准价={self.base_price:.3f}, 上限阈值={up_threshold_price:.3f}, 下限阈值={down_threshold_price:.3f}, 已完成交易次数={self.completed_trades}/{self.params['sell_times']}")
            except (EOFError, BrokenPipeError, OSError):
                pass
            
            # 止盈提前下单逻辑
            if current_price >= self.base_price:
                # 当前价格大于等于基准价，考虑止盈提前下单
                # 只有当没有止盈提前下单，或者基准价发生变化时才下单
                if (not self.take_profit_order_id or 
                    self.take_profit_order_base_price != self.base_price):
                    # 如果之前有订单，先撤单
                    if self.take_profit_order_id:
                        self._cancel_take_profit_order()
                    # 下止盈提前下单
                    self._place_take_profit_order(up_threshold_price, volume)
                    
                # 检查是否需要撤单（价格远离止盈位置）
                elif self.take_profit_order_id:
                    price_diff_ratio = abs(current_price - up_threshold_price) / up_threshold_price
                    if price_diff_ratio > 0.02:  # 价格偏离止盈位置超过2%
                        try:
                            self.log_pipe.send(f"[{self.stock_code}] 价格偏离止盈位置{price_diff_ratio*100:.1f}%，撤消止盈提前下单")
                        except (EOFError, BrokenPipeError, OSError):
                            pass
                        self._cancel_take_profit_order()
            
            else:
                # 当前价格小于基准价，撤消止盈提前下单
                if self.take_profit_order_id:
                    try:
                        self.log_pipe.send(f"[{self.stock_code}] 价格低于基准价，撤消止盈提前下单")
                    except (EOFError, BrokenPipeError, OSError):
                        pass
                    self._cancel_take_profit_order()
            
            # 检查是否触发止损（价格跌破下限阈值）
            if current_price < down_threshold_price:
                try:
                    self.log_pipe.send(f"[{self.stock_code}] 当前价格{current_price:.3f} < 下限阈值{down_threshold_price:.3f}，触发止损")
                except (EOFError, BrokenPipeError, OSError):
                    pass
                
                signal = {
                    'type': 'sell',
                    'price': current_price,
                    'volume': volume,
                    'reason': '止损',
                    'askPrice': ask_prices,
                    'bidPrice': bid_prices,
                    'askVol': ask_vols,
                    'bidVol': bid_vols,
                    'time': tick_data['time'].strftime('%Y-%m-%d %H:%M:%S')
                }
                signals.append(signal)
                
                # 更新交易计数和调整阈值
                old_completed_trades = self.completed_trades
                self.completed_trades += 1
                try:
                    self.log_pipe.send(f"[{self.stock_code}] 止损交易计数更新: {old_completed_trades} -> {self.completed_trades}/{self.params['sell_times']}")
                except (EOFError, BrokenPipeError, OSError):
                    pass
                
                # 更新基准价
                old_base_price = self.base_price
                self.base_price = current_price
                # 重置止盈提前下单的基准价状态，因为基准价已更新
                self.take_profit_order_base_price = None
                try:
                    self.log_pipe.send(f"[{self.stock_code}] 止盈提前单成交，基准价从 {old_base_price:.3f} 更新为 {current_price:.3f}")
                    self.log_pipe.send(('update_base_price', {
                        'stock_code': self.stock_code,
                        'base_price': current_price
                    }))
                except (EOFError, BrokenPipeError, OSError):
                    pass
                
                # 检查是否已完成所有交易次数
                if self.completed_trades >= self.params['sell_times']:
                    try:
                        self.log_pipe.send(f"[{self.stock_code}] 已完成所有{self.params['sell_times']}次交易，策略结束")
                    except (EOFError, BrokenPipeError, OSError):
                        pass
                    try:
                        self.log_pipe.send(('update_task_status', {
                            'stock_code': self.stock_code,
                            'status': '已完成',
                            'reason': f'已完成{self.params["sell_times"]}次交易'
                        }))
                    except (EOFError, BrokenPipeError, OSError):
                        pass
                    try:
                        self.control_pipe.send('stop')
                    except (EOFError, BrokenPipeError, OSError):
                        pass
            
            # 移除原有的止盈触发逻辑，改为只在止盈提前单成交时处理
            # 检查是否触发止盈（价格突破上限阈值）
            '''elif current_price > up_threshold_price:
                try:
                    self.log_pipe.send(f"[{self.stock_code}] 当前价格{current_price:.3f} > 上限阈值{up_threshold_price:.3f}，触发止盈")
                except (EOFError, BrokenPipeError, OSError):
                    pass
                
                # 如果有止盈提前下单，撤单并立即成交
                if self.take_profit_order_id:
                    self._cancel_take_profit_order()
                
                signal = {
                    'type': 'sell',
                    'price': current_price,
                    'volume': volume,
                    'reason': '止盈',
                    'askPrice': ask_prices,
                    'bidPrice': bid_prices,
                    'askVol': ask_vols,
                    'bidVol': bid_vols,
                    'time': tick_data['time'].strftime('%Y-%m-%d %H:%M:%S')
                }
                signals.append(signal)
                
                # 更新交易计数
                old_completed_trades = self.completed_trades
                self.completed_trades += 1
                try:
                    self.log_pipe.send(f"[{self.stock_code}] 止盈交易计数更新: {old_completed_trades} -> {self.completed_trades}/{self.params['sell_times']}")
                except (EOFError, BrokenPipeError, OSError):
                    pass
                
                # 更新基准价
                self.base_price = current_price
                try:
                    self.log_pipe.send(('update_base_price', {
                        'stock_code': self.stock_code,
                        'base_price': current_price
                    }))
                except (EOFError, BrokenPipeError, OSError):
                    pass
                
                # 检查是否已完成所有交易次数
                if self.completed_trades >= self.params['sell_times']:
                    try:
                        self.log_pipe.send(f"[{self.stock_code}] 已完成所有{self.params['sell_times']}次交易，策略结束")
                    except (EOFError, BrokenPipeError, OSError):
                        pass
                    try:
                        self.log_pipe.send(('update_task_status', {
                            'stock_code': self.stock_code,
                            'status': '已完成',
                            'reason': f'已完成{self.params["sell_times"]}次交易'
                        }))
                    except (EOFError, BrokenPipeError, OSError):
                        pass
                    try:
                        self.control_pipe.send('stop')
                    except (EOFError, BrokenPipeError, OSError):
                        pass
            '''
            return signals
            
        except Exception as e:
            try:
                self.log_pipe.send(f"[{self.stock_code}] 策略处理出错: {str(e)}")
                import traceback
                self.log_pipe.send(f"[{self.stock_code}] 错误堆栈：{traceback.format_exc()}")
            except (EOFError, BrokenPipeError, OSError):
                pass
            return []

    def run(self):
        """运行策略"""
        try:
            while True:
                try:
                    message = self.control_pipe.recv()
                    if isinstance(message, tuple) and len(message) == 2:
                        cmd, data = message
                        if cmd == 'stop':
                            self.logger.info(f"[{self.stock_code}] 收到停止信号，策略进程退出")
                            break
                        elif cmd == 'tick':
                            # 处理行情数据
                            signals = self.on_tick(data)
                            if signals:
                                self.send_trade_signal(signals)
                        elif cmd == 'update_base_price':
                            if isinstance(data, dict):
                                self.base_price = data['base_price']
                            else:
                                self.base_price = data
                        elif cmd == 'update_params':
                            # 处理参数更新
                            self.update_params(data)
                            self.logger.info(f"[{self.stock_code}] 策略参数已更新: {data}")
                        elif cmd == 'take_profit_order_response':
                            # 处理止盈提前下单响应
                            if isinstance(data, dict):
                                order_id = data.get('order_id')
                                executed_price = data.get('price')
                                executed_volume = data.get('volume', 0)
                                status = data.get('status', '')
                                
                                # 如果是止盈提前单成交
                                if (order_id == self.take_profit_order_id and 
                                    status == '成交' and 
                                    executed_price and executed_volume > 0):
                                    try:
                                        self.log_pipe.send(f"[{self.stock_code}] 止盈提前单成交: 订单ID={order_id}, 成交价={executed_price}, 成交量={executed_volume}")
                                    except (EOFError, BrokenPipeError, OSError):
                                        pass
                                    
                                    # 更新交易计数
                                    old_completed_trades = self.completed_trades
                                    self.completed_trades += 1
                                    try:
                                        self.log_pipe.send(f"[{self.stock_code}] 止盈交易计数更新: {old_completed_trades} -> {self.completed_trades}/{self.params['sell_times']}")
                                    except (EOFError, BrokenPipeError, OSError):
                                        pass
                                    
                                    # 更新基准价为成交价
                                    old_base_price = self.base_price
                                    self.base_price = executed_price
                                    # 重置止盈提前下单的基准价状态，因为基准价已更新
                                    self.take_profit_order_base_price = None
                                    try:
                                        self.log_pipe.send(f"[{self.stock_code}] 止盈提前单成交，基准价从 {old_base_price:.3f} 更新为 {executed_price:.3f}")
                                        self.log_pipe.send(('update_base_price', {
                                            'stock_code': self.stock_code,
                                            'base_price': executed_price
                                        }))
                                    except (EOFError, BrokenPipeError, OSError):
                                        pass
                                    
                                    # 清除止盈提前下单状态
                                    self.take_profit_order_id = None
                                    self.take_profit_order_price = None
                                    
                                    # 检查是否已完成所有交易次数
                                    if self.completed_trades >= self.params['sell_times']:
                                        try:
                                            self.log_pipe.send(f"[{self.stock_code}] 已完成所有{self.params['sell_times']}次交易，策略结束")
                                        except (EOFError, BrokenPipeError, OSError):
                                            pass
                                        try:
                                            self.log_pipe.send(('update_task_status', {
                                                'stock_code': self.stock_code,
                                                'status': '已完成',
                                                'reason': f'已完成{self.params["sell_times"]}次交易'
                                            }))
                                        except (EOFError, BrokenPipeError, OSError):
                                            pass
                                        try:
                                            self.control_pipe.send('stop')
                                        except (EOFError, BrokenPipeError, OSError):
                                            pass
                                    
                                else:
                                    # 普通的下单响应
                                    self.take_profit_order_id = order_id
                                    self.take_profit_order_price = executed_price
                                    try:
                                        self.log_pipe.send(f"[{self.stock_code}] 收到止盈提前下单响应: 订单ID={order_id}, 价格={executed_price}")
                                    except (EOFError, BrokenPipeError, OSError):
                                        pass
                        else:
                            self.logger.info(f"[{self.stock_code}] 收到未知命令：{cmd}")
                    elif message == 'stop':
                        self.logger.info(f"[{self.stock_code}] 收到停止信号，策略进程退出")
                        break
                    else:
                        self.logger.info(f"[{self.stock_code}] 收到未知消息：{message}")
                    
                except (EOFError, BrokenPipeError) as pipe_error:
                    # 管道关闭时直接退出，不再尝试发送日志
                    self.logger.warning(f"[{self.stock_code}] 控制管道已关闭: {str(pipe_error)}")
                    break
                    
        except Exception as e:
            try:
                self.log_pipe.send(f"[{self.stock_code}] 策略运行出错: {str(e)}")
                import traceback
                self.log_pipe.send(f"[{self.stock_code}] 错误堆栈：{traceback.format_exc()}")
            except (EOFError, BrokenPipeError, OSError):
                pass 