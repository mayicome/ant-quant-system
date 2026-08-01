from strategies.base_strategy import BaseStrategy
from datetime import datetime, timedelta, time
from utils.trading_day import is_tradeday
from brokers.capital_flow import get_capital_flow
import math
import pandas as pd
import time as time_module
import threading

# 夜市开始时间常量
NIGHT_MARKET_TIMES = {
    'start': datetime.now().replace(hour=19, minute=29, second=59, microsecond=900000),  # 19:29:59.8
    'end': datetime.now().replace(hour=19, minute=30, second=1, microsecond=100000),     # 19:30:01.1
    'extended': datetime.now().replace(hour=19, minute=30, second=10, microsecond=0)  # 19:30:10
}

# 早盘时间常量
MORNING_CLEAR_TIME = datetime.now().replace(hour=9, minute=14, second=59, microsecond=0)  # 9:14:59 清空tick数据
MORNING_CHECK_TIME = datetime.now().replace(hour=9, minute=19, second=55, microsecond=0)  # 9:19:55 检查涨跌停板

def get_tonight_start_time():
    """获取当天的夜市开始时间"""
    return NIGHT_MARKET_TIMES['start']

def get_tonight_end_time():
    """获取当天的夜市结束时间"""
    return NIGHT_MARKET_TIMES['end']

def get_tonight_extended_time():
    """获取当天的夜市延长时间"""
    return NIGHT_MARKET_TIMES['extended']

def get_morning_clear_time():
    """获取当天的早盘清空tick数据时间"""
    return MORNING_CLEAR_TIME

def get_morning_check_time():
    """获取当天的早盘检查涨跌停板时间"""
    return MORNING_CHECK_TIME

class NightMarketStrategy(BaseStrategy):
    """夜市策略"""
    
    def __init__(self, task_info, log_pipe, control_pipe):
        # 在调用父类初始化之前，补全所有普通策略需要的参数字段
        # 添加停止标志
        self.stop_requested = False
        
        # 补全普通策略需要的字段
        task_info['strategy'] = task_info.get('strategy', '夜市')
        task_info['base_price'] = task_info.get('base_price', 0)
        task_info['init_cost'] = task_info.get('init_cost', 0)
        task_info['buy_date'] = task_info.get('buy_date', datetime.now().strftime('%Y-%m-%d'))
        task_info['hold_days'] = task_info.get('hold_days', 0)
        
        # 调用父类初始化
        super().__init__(task_info, log_pipe, control_pipe)
        
        # 设置策略名称属性
        self.strategy = task_info['strategy']
        
        # 涨停板检查相关
        self.check_timer = None
        self.tick_data = []
        self.limit_up_price = None
        self.limit_down_price = None
        
        # 跌停板检查相关
        self.limit_down_check_timer = None
        self.limit_down_tick_data = []
        
        # 直接使用任务中的数量和基准价格，不再从params中获取
        self.trade_volume = self.init_volume  # 直接使用表格中的数量
        self.trade_price = self.base_price    # 直接使用表格中的基准价格
        
        # 夜市策略特有的初始化
        self.order_success = False
        self.max_retry_times = 30000  # 最大重试次数
        self.retry_interval = 0.001    # 重试间隔（秒）
        self.current_retry = 0
        self.last_check_date = None  # 添加上次检查日期
        self.latest_tick_data = None  # 保存最新的tick数据
        self.tick_lock = threading.Lock()  # tick数据锁
        self.check_timer = None  # 涨停板检查定时器
        self.order_id = None

    def run(self):
        """运行夜市策略"""
        try:
            #self.log_pipe.send(f"[{self.stock_code}] 开始夜市策略：{self.strategy}")
            
            # 启动监听订单响应的线程
            response_thread = threading.Thread(target=self._listen_order_response, daemon=True)
            response_thread.start()
            #self.log_pipe.send(f"[{self.stock_code}] 订单响应监听线程已启动")
            
            # 根据策略类型确定委托参数
            if '卖出' in self.strategy:
                self.log_pipe.send(f"[{self.stock_code}] 执行夜市卖出策略")
                # 执行卖出策略
                self._execute_night_sell()
            elif '买入' in self.strategy:
                self.log_pipe.send(f"[{self.stock_code}] 执行夜市买入策略")
                # 执行买入策略
                self._execute_night_buy()
            else:
                self.log_pipe.send(f"[{self.stock_code}] 未知的夜市类型：{self.strategy}")
            
            # 检查是否是交易日
            is_trading_day_today = is_tradeday(datetime.now().date())
            
            # 等待策略完成或收到停止信号
            while not self.stop_requested:
                time_module.sleep(0.1)
                
                if is_trading_day_today:
                    # 交易日：委托成功后停止
                    if self.order_success:
                        self.log_pipe.send(f"[{self.stock_code}] 交易日委托成功，夜市策略执行完成")
                        break
                else:
                    # 非交易日：持续运行，不依赖委托成功状态
                    # 因为非交易日可能是虚拟委托，需要持续委托直到下一个交易日
                    if self.stop_requested:
                        self.log_pipe.send(f"[{self.stock_code}] 非交易日收到停止信号，退出策略")
                        break
                    # 继续运行，定期重新委托
            
            self.log_pipe.send(f"[{self.stock_code}] 夜市策略结束")
            
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 夜市策略执行出错：{str(e)}")
            import traceback
            self.log_pipe.send(f"[{self.stock_code}] 错误详情：{traceback.format_exc()}")
    
    def _listen_order_response(self):
        """监听订单响应"""
        try:
            #self.log_pipe.send(f"[{self.stock_code}] 开始监听订单响应")
            while True:  # 改为无限循环，即使订单成功也继续监听
                try:
                    if self.control_pipe.poll(1):
                        try:
                            message = self.control_pipe.recv()
                            
                            if isinstance(message, tuple) and len(message) == 2:
                                cmd, data = message
                                
                                if cmd == 'order_response':
                                    self.on_order_response(data)
                                elif cmd == 'order_status':
                                    # 处理订单状态更新
                                    self.on_order_status(data)
                                elif cmd == 'tick':
                                    # 处理行情数据
                                    self._on_tick(data)
                                elif cmd == 'update_base_price':
                                    # 更新基准价格
                                    if isinstance(data, dict):
                                        self.base_price = data['base_price']
                                    else:
                                        self.base_price = data
                                    self.log_pipe.send(f"[{self.stock_code}] 更新基准价格：{self.base_price}")
                                elif cmd == 'stop':
                                    self.log_pipe.send(f"[{self.stock_code}] 收到停止信号")
                                    self.stop_requested = True
                                    return
                                else:
                                    self.log_pipe.send(f"[{self.stock_code}] 收到未知命令: {cmd}")
                            elif message == 'stop':
                                self.log_pipe.send(f"[{self.stock_code}] 收到停止信号")
                                self.stop_requested = True
                                return
                            else:
                                self.log_pipe.send(f"[{self.stock_code}] 收到非订单响应消息: {message}")
                                
                        except Exception as e:
                            self.log_pipe.send(f"[{self.stock_code}] 监听订单响应出错：{str(e)}")
                            break
                    time_module.sleep(0.1)
                except (EOFError, BrokenPipeError, OSError):
                    # 管道已关闭，退出监听
                    break
                except Exception as e:
                    # 其他错误，记录但不退出
                    try:
                        self.log_pipe.send(f"[{self.stock_code}] 监听循环出错：{str(e)}")
                    except:
                        pass
                    time_module.sleep(0.1)
            
            try:
                self.log_pipe.send(f"[{self.stock_code}] 订单响应监听结束，order_success={self.order_success}")
            except:
                pass
        except Exception as e:
            try:
                self.log_pipe.send(f"[{self.stock_code}] 订单响应监听线程出错：{str(e)}")
            except:
                pass
    
    def _execute_night_sell(self):
        """运行卖出策略"""
        try:
            #self.log_pipe.send(f"[{self.stock_code}] 开始执行夜市卖出策略")
            
            # 根据策略名称判断委托方式
            if '市价' in self.strategy:
                sell_type = '市价'
                sell_price = 0
            else:
                sell_type = '限价'
                sell_price = self.base_price  # 使用基准价作为限价
            
            #self.log_pipe.send(f"[{self.stock_code}] 夜市卖出：数量={self.trade_volume}, 方式={sell_type}, 价格={sell_price}")
            
            # 检查是否为交易日
            current_date = datetime.now().date()
            is_trading_day = is_tradeday(current_date)
            
            if is_trading_day:
                # 交易日：检查当前时间是否在允许下单的时间段内
                current_time = datetime.now()
                start_time = get_tonight_start_time()
                morning_start_time = current_time.replace(hour=0, minute=0, second=0, microsecond=0)  # 0:00:00
                morning_end_time = current_time.replace(hour=9, minute=16, second=0, microsecond=0)   # 9:16:00
                
                # 判断是否在允许下单的时间段内
                is_night_time = current_time >= start_time  # 19:29:59.9以后
                is_morning_time = morning_start_time <= current_time <= morning_end_time  # 0:00:00到9:16:00之间
                
                if is_night_time:
                    # 夜市时间：立即开始高频下单尝试
                    self.log_pipe.send(f"[{self.stock_code}] 当前为夜市时间，开始高频下单尝试")
                elif is_morning_time:
                    # 早盘时间：立即开始低频下单尝试
                    self.log_pipe.send(f"[{self.stock_code}] 当前为早盘时间(0:00-9:16)，开始低频下单尝试")
                else:
                    # 其他时间：等待到夜市开始时间
                    self.log_pipe.send(f"[{self.stock_code}] 今日为交易日，等待夜市开始时间：{start_time.strftime('%H:%M:%S.%f')}")
                    while datetime.now() < start_time and not self.stop_requested:
                        time_module.sleep(0.001)
                        # 检查停止标志
                        if self.stop_requested:
                            self.log_pipe.send(f"[{self.stock_code}] 收到停止信号，退出等待")
                            return
                    self.log_pipe.send(f"[{self.stock_code}] 夜市开始时间已到，开始高频下单尝试")
            else:
                # 非交易日：直接开始以低速下单
                self.log_pipe.send(f"[{self.stock_code}] 今日为非交易日，直接开始低速下单")

            # 循环尝试下单直到成功
            while not self.stop_requested:
                try:
                    # 检查停止标志
                    if self.stop_requested:
                        #self.log_pipe.send(f"[{self.stock_code}] 收到停止信号，退出重试循环")
                        break
                    
                    # 检查是否是交易日
                    current_date = datetime.now().date()
                    is_trading_day_today = is_tradeday(current_date)
                    
                    # 根据当前时间和是否为交易日动态调整重试间隔
                    current_time = datetime.now()
                    start_time = get_tonight_start_time()
                    end_time = get_tonight_end_time()
                    extended_time = get_tonight_extended_time()
                    morning_start_time = current_time.replace(hour=0, minute=0, second=0, microsecond=0)  # 0:00:00
                    morning_end_time = current_time.replace(hour=9, minute=16, second=0, microsecond=0)   # 9:16:00
                    
                    if not is_trading_day_today:
                        # 非交易日：使用低频尝试，不依赖委托成功状态
                        retry_interval = 6.0
                        self.log_pipe.send(f"[{self.stock_code}] 非交易日低频尝试，间隔{retry_interval}秒（虚拟委托模式）")
                    elif morning_start_time <= current_time <= morning_end_time:
                        # 早盘时间(0:00-9:16)：使用低频尝试
                        retry_interval = 6.0
                        self.log_pipe.send(f"[{self.stock_code}] 早盘时间低频尝试，间隔{retry_interval}秒")
                    elif current_time >= start_time and current_time < end_time:
                        # 交易日19:29:59.5-19:30:00 高频尝试
                        retry_interval = 0.001
                        self.log_pipe.send(f"[{self.stock_code}] 高频尝试阶段，间隔{retry_interval}秒")
                    elif current_time < extended_time:
                        # 交易日19:30:00-19:30:10 中频尝试
                        retry_interval = 0.1
                        self.log_pipe.send(f"[{self.stock_code}] 中频尝试阶段，间隔{retry_interval}秒")
                    else:
                        # 交易日19:30:10以后以及白天时段 低频尝试
                        retry_interval = 6.0
                        self.log_pipe.send(f"[{self.stock_code}] 低频尝试阶段，间隔{retry_interval}秒")
                    
                    # 生成卖出信号
                    signal = {
                        'type': 'sell',
                        'price': sell_price if sell_type == '限价' else 0,
                        'volume': self.trade_volume,
                        'reason': f'夜市',
                        'time': datetime.now(),
                        # 添加必要的价格数据
                        'askPrice': [sell_price if sell_type == '限价' else 0] * 5,  # 卖档价格
                        'bidPrice': [sell_price if sell_type == '限价' else 0] * 5,  # 买档价格
                        'askVol': [0] * 5,  # 卖档量
                        'bidVol': [0] * 5   # 买档量
                    }
                    
                    # 发送交易信号
                    self.log_pipe.send(f"[{self.stock_code}] 发送卖出信号：{signal}")
                    self.send_trade_signal([signal])
                    
                    # 等待重试间隔
                    time_module.sleep(retry_interval)
                    
                    # 检查是否应该停止（仅在交易日检查委托成功状态）
                    if is_trading_day_today and self.order_success:
                        self.log_pipe.send(f"[{self.stock_code}] 交易日委托成功，停止重试")
                        break
                    elif not is_trading_day_today:
                        # 非交易日：继续循环，不检查委托成功状态
                        self.log_pipe.send(f"[{self.stock_code}] 非交易日继续虚拟委托模式")
                        continue
                    
                except Exception as e:
                    self.log_pipe.send(f"[{self.stock_code}] 夜市卖出策略执行出错：{str(e)}")
                    time_module.sleep(1)  # 出错时等待1秒再重试
            
            if self.order_success:
                self.log_pipe.send(f"[{self.stock_code}] 夜市卖出成功")
                # 委托成功后立即结束策略
                self.log_pipe.send(f"[{self.stock_code}] 夜市卖出成功，策略结束")
                return
            else:
                # 区分停止信号和真正失败的情况
                if self.stop_requested:
                    self.log_pipe.send(f"[{self.stock_code}] 收到停止信号，策略结束")
                else:
                    self.log_pipe.send(f"[{self.stock_code}] 夜市卖出失败，已达到最大重试次数")
                    # 委托失败后也结束策略
                    self.log_pipe.send(f"[{self.stock_code}] 夜市卖出失败，策略结束")
                return
                
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 夜市卖出策略执行出错：{str(e)}")
            import traceback
            self.log_pipe.send(f"[{self.stock_code}] 错误详情：{traceback.format_exc()}")
    
    def _execute_night_buy(self):
        """运行买入策略"""
        try:
            #self.log_pipe.send(f"[{self.stock_code}] 开始执行夜市买入策略")
            
            # 根据策略名称判断委托方式
            if '市价' in self.strategy:
                buy_type = '市价'
                buy_price = 0
            else:
                buy_type = '限价'
                buy_price = self.base_price  # 使用基准价作为限价
            
            #self.log_pipe.send(f"[{self.stock_code}] 夜市买入：数量={self.trade_volume}, 方式={buy_type}, 价格={buy_price}")
            
            # 检查是否为交易日
            current_date = datetime.now().date()
            is_trading_day = is_tradeday(current_date)
            
            if is_trading_day:
                # 交易日：检查当前时间是否在允许下单的时间段内
                current_time = datetime.now()
                start_time = get_tonight_start_time()
                morning_start_time = current_time.replace(hour=0, minute=0, second=0, microsecond=0)  # 0:00:00
                morning_end_time = current_time.replace(hour=9, minute=16, second=0, microsecond=0)   # 9:16:00
                
                # 判断是否在允许下单的时间段内
                is_night_time = current_time >= start_time  # 19:29:59.9以后
                is_morning_time = morning_start_time <= current_time <= morning_end_time  # 0:00:00到9:16:00之间
                
                if is_night_time:
                    # 夜市时间：立即开始高频下单尝试
                    self.log_pipe.send(f"[{self.stock_code}] 当前为夜市时间，开始高频下单尝试")
                elif is_morning_time:
                    # 早盘时间：立即开始低频下单尝试
                    self.log_pipe.send(f"[{self.stock_code}] 当前为早盘时间(0:00-9:16)，开始低频下单尝试")
                else:
                    # 其他时间：等待到夜市开始时间
                    self.log_pipe.send(f"[{self.stock_code}] 今日为交易日，等待夜市开始时间：{start_time.strftime('%H:%M:%S.%f')}")
                    while datetime.now() < start_time and not self.stop_requested:
                        time_module.sleep(0.001)
                        # 检查停止标志
                        if self.stop_requested:
                            self.log_pipe.send(f"[{self.stock_code}] 收到停止信号，退出等待")
                            return
                    self.log_pipe.send(f"[{self.stock_code}] 夜市开始时间已到，开始高频下单尝试")
            else:
                # 非交易日：直接开始以低速下单
                self.log_pipe.send(f"[{self.stock_code}] 今日为非交易日，直接开始低速下单")

            # 循环尝试下单直到成功
            while not self.stop_requested:
                try:
                    # 检查停止标志
                    if self.stop_requested:
                        self.log_pipe.send(f"[{self.stock_code}] 收到停止信号，退出重试循环")
                        break
                    
                    # 检查是否是交易日
                    current_date = datetime.now().date()
                    is_trading_day_today = is_tradeday(current_date)
                    
                    # 根据当前时间和是否为交易日动态调整重试间隔
                    current_time = datetime.now()
                    start_time = get_tonight_start_time()
                    end_time = get_tonight_end_time()
                    extended_time = get_tonight_extended_time()
                    morning_start_time = current_time.replace(hour=0, minute=0, second=0, microsecond=0)  # 0:00:00
                    morning_end_time = current_time.replace(hour=9, minute=16, second=0, microsecond=0)   # 9:16:00

                    if not is_trading_day_today:
                        # 非交易日：使用低频尝试
                        retry_interval = 6.0
                        self.log_pipe.send(f"[{self.stock_code}] 非交易日低频尝试，间隔{retry_interval}秒")
                    elif morning_start_time <= current_time <= morning_end_time:
                        # 早盘时间(0:00-9:16)：使用低频尝试
                        retry_interval = 6.0
                        self.log_pipe.send(f"[{self.stock_code}] 早盘时间低频尝试，间隔{retry_interval}秒")
                    elif current_time >= start_time and current_time < end_time:
                        # 交易日19:29:59.5-19:30:00 高频尝试
                        retry_interval = 0.001
                        self.log_pipe.send(f"[{self.stock_code}] 高频尝试阶段，间隔{retry_interval}秒")
                    elif current_time < extended_time:
                        # 交易日19:30:00-19:30:10 中频尝试
                        retry_interval = 0.1
                        self.log_pipe.send(f"[{self.stock_code}] 中频尝试阶段，间隔{retry_interval}秒")
                    else:
                        # 交易日19:30:10以后以及白天时段 低频尝试
                        retry_interval = 6.0
                        self.log_pipe.send(f"[{self.stock_code}] 低频尝试阶段，间隔{retry_interval}秒")
                    
                    # 生成买入信号
                    signal = {
                        'type': 'buy',
                        'price': buy_price if buy_type == '限价' else 0,
                        'volume': self.trade_volume,
                        'reason': f'夜市',
                        'time': datetime.now(),
                        # 添加必要的价格数据
                        'askPrice': [buy_price if buy_type == '限价' else 0] * 5,  # 卖档价格
                        'bidPrice': [buy_price if buy_type == '限价' else 0] * 5,  # 买档价格
                        'askVol': [0] * 5,  # 卖档量
                        'bidVol': [0] * 5   # 买档量
                    }
                    
                    # 发送交易信号
                    self.log_pipe.send(f"[{self.stock_code}] 发送买入信号：{signal}")
                    self.send_trade_signal([signal])
                    
                    # 等待订单响应
                    wait_time = 0
                    while not self.order_success and wait_time < retry_interval and not self.stop_requested:
                        time_module.sleep(0.001)  # 更短的检查间隔，提高响应速度
                        wait_time += 0.001
                        
                        # 如果收到成功响应，立即退出循环
                        if self.order_success:
                            break
                        
                        # 检查停止标志
                        if self.stop_requested:
                            self.log_pipe.send(f"[{self.stock_code}] 收到停止信号，退出等待循环")
                            break
                    
                    # 如果收到成功响应，立即退出重试循环
                    if self.order_success:
                        self.log_pipe.send(f"[{self.stock_code}] 收到成功响应，停止重试")
                        break
                    
                    # 如果收到停止信号，立即退出重试循环
                    if self.stop_requested:
                        self.log_pipe.send(f"[{self.stock_code}] 收到停止信号，退出重试循环")
                        break
                    
                    # 如果没有收到成功响应，增加重试次数
                    if not self.order_success:
                        self.current_retry += 1
                        self.log_pipe.send(f"[{self.stock_code}] 第{self.current_retry}次尝试夜市买入")
                        
                        # 如果达到最大重试次数，停止重试
                        if self.current_retry >= self.max_retry_times:
                            self.log_pipe.send(f"[{self.stock_code}] 已达到最大重试次数{self.max_retry_times}，停止重试")
                            break
                    
                except Exception as e:
                    self.log_pipe.send(f"[{self.stock_code}] 夜市买入出错：{str(e)}")
                    time_module.sleep(retry_interval)
                    self.current_retry += 1
            
            if self.order_success:
                self.log_pipe.send(f"[{self.stock_code}] 夜市买入成功")
                # 委托成功后立即结束策略
                self.log_pipe.send(f"[{self.stock_code}] 夜市买入成功，策略结束")
                return
            else:
                # 区分停止信号和真正失败的情况
                if self.stop_requested:
                    self.log_pipe.send(f"[{self.stock_code}] 收到停止信号，策略结束")
                else:
                    self.log_pipe.send(f"[{self.stock_code}] 夜市买入失败，已达到最大重试次数")
                    # 委托失败后也结束策略
                    self.log_pipe.send(f"[{self.stock_code}] 夜市买入失败，策略结束")
                return
                
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 夜市买入策略执行出错：{str(e)}")
            import traceback
            self.log_pipe.send(f"[{self.stock_code}] 错误详情：{traceback.format_exc()}")

    def _on_tick(self, tick_data):
        """处理tick数据"""
        try:
            # 输出tick数据内容
            #self.log_pipe.send(f"[{self.stock_code}] 收到tick数据：{tick_data}")
            
            # 保存最新的tick数据用于涨停板检查
            with self.tick_lock:
                self.latest_tick_data = tick_data
            
            # 夜市不需要实时tick数据，直接返回空列表
            return []
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 处理tick数据出错：{str(e)}")
            return []

    def on_order_response(self, order_info):
        """处理订单响应"""
        try:
            #self.log_pipe.send(f"[{self.stock_code}] 收到订单响应: {order_info}")
            
            order_status = order_info.get('order_status', '')
            order_id = order_info.get('order_id', '')
            order_type = order_info.get('type', '')
            order_price = order_info.get('price', 0)
            order_volume = order_info.get('volume', 0)
            
            #self.log_pipe.send(f"[{self.stock_code}] 订单详情: 订单号={order_id}, 类型={order_type}, 价格={order_price}, 数量={order_volume}, 状态={order_status}")
            
            # 检查是否是交易日
            is_trading_day_today = is_tradeday(datetime.now().date())
            
            # 只保存订单ID用于撤单，不进行成功判断
            if order_id:
                self.order_id = order_id
                #self.log_pipe.send(f"[{self.stock_code}] 保存订单ID用于撤单：{order_id}")
                
                # 在非交易日，不设置委托成功标志，因为可能是虚拟委托
                if is_trading_day_today:
                    # 交易日：正常处理委托成功状态
                    if order_status in ['已报', '已成', '部分成交']:
                        self.order_success = True
                        self.log_pipe.send(f"[{self.stock_code}] 交易日委托成功，状态：{order_status}")
                else:
                    # 非交易日：记录虚拟委托，但不设置成功标志
                    self.log_pipe.send(f"[{self.stock_code}] 非交易日虚拟委托，状态：{order_status}，将继续定期重新委托")
                
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 处理订单响应出错：{str(e)}")

    def on_order_status(self, order_status_info):
        """处理订单状态更新"""
        try:
            self.log_pipe.send(f"[{self.stock_code}] 收到订单状态更新: {order_status_info}")
            
            order_id = order_status_info.get('order_id', '')
            status = order_status_info.get('status', '')
            price = order_status_info.get('price', 0)
            volume = order_status_info.get('volume', 0)
            order_type = order_status_info.get('type', '')
            
            # 检查是否是交易日
            is_trading_day_today = is_tradeday(datetime.now().date())
            
            if order_id:
                self.order_id = order_id
                self.log_pipe.send(f"[{self.stock_code}] 保存订单ID：{order_id}")
                
                # 根据状态更新委托成功标志
                if status in ['已报', '未报']:
                    if is_trading_day_today:
                        # 交易日：设置委托成功
                        self.order_success = True
                        self.log_pipe.send(f"[{self.stock_code}] 交易日委托成功，状态：{status}")
                    else:
                        # 非交易日：记录状态但不设置成功标志，继续定期重新委托
                        self.log_pipe.send(f"[{self.stock_code}] 非交易日委托状态：{status}，将继续定期重新委托")
                elif status in ['委托失败', '废单']:
                    self.log_pipe.send(f"[{self.stock_code}] 委托失败，将继续重试")
                    # 不设置成功标志，继续重试
                elif status in ['已撤', '部撤']:
                    self.log_pipe.send(f"[{self.stock_code}] 订单已撤单，将继续重试")
                    # 不设置成功标志，继续重试
                else:
                    self.log_pipe.send(f"[{self.stock_code}] 订单状态：{status}")
            else:
                self.log_pipe.send(f"[{self.stock_code}] 订单状态更新中没有订单ID")
                
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 处理订单状态更新出错：{str(e)}")

    def _start_limit_up_check_timer(self):
        """启动涨停板检查定时器"""
        try:
            # 计算到9点14分55秒的时间（清空tick数据）
            now = datetime.now()
            clear_time = get_morning_clear_time()
            
            # 如果今天已经过了清空时间，设置为明天
            if now >= clear_time:
                clear_time += timedelta(days=1)
            
            # 计算到9点17分55秒的时间（检查涨停板）
            check_time = get_morning_check_time()
            if now >= check_time:
                check_time += timedelta(days=1)
            
            # 计算等待时间（秒）
            clear_wait_seconds = (clear_time - now).total_seconds()
            check_wait_seconds = (check_time - now).total_seconds()
            
            self.log_pipe.send(f"[{self.stock_code}] 涨停板检查定时器将在 {check_time.strftime('%Y-%m-%d %H:%M:%S')} 执行，等待 {check_wait_seconds:.1f} 秒")
            self.log_pipe.send(f"[{self.stock_code}] 将在 {clear_time.strftime('%Y-%m-%d %H:%M:%S')} 清空tick数据，等待 {clear_wait_seconds:.1f} 秒")
            
            # 创建清空tick数据的定时器
            clear_timer = threading.Timer(clear_wait_seconds, self._clear_tick_data)
            clear_timer.daemon = True
            clear_timer.start()
            
            # 创建涨停板检查定时器
            self.check_timer = threading.Timer(check_wait_seconds, self._check_limit_up_and_cancel)
            self.check_timer.daemon = True
            self.check_timer.start()
            
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 启动涨停板检查定时器失败：{str(e)}")
            import traceback
            self.log_pipe.send(f"[{self.stock_code}] 错误详情：{traceback.format_exc()}")
    
    def _clear_tick_data(self):
        """清空tick数据"""
        try:
            clear_time = get_morning_clear_time()
            self.log_pipe.send(f"[{self.stock_code}] {clear_time.strftime('%H:%M:%S')}清空tick数据，准备接收新的实时数据")
            with self.tick_lock:
                self.latest_tick_data = None
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 清空tick数据失败：{str(e)}")
    
    def _check_limit_up_and_cancel(self):
        """检查是否封涨停板，如果没有则撤单"""
        try:
            self.log_pipe.send(f"[{self.stock_code}] 开始检查涨停板状态")
            
            # 获取最新的tick数据
            with self.tick_lock:
                tick_data = self.latest_tick_data
            
            if not tick_data:
                self.log_pipe.send(f"[{self.stock_code}] 没有tick数据，等待新的实时数据...")
                # 等待一段时间，让新的tick数据到达
                time_module.sleep(5)
                # 再次尝试获取tick数据
                with self.tick_lock:
                    tick_data = self.latest_tick_data
                
                if not tick_data:
                    self.log_pipe.send(f"[{self.stock_code}] 仍然没有tick数据，无法检查涨停板状态")
                    return
            
            # 获取最新价和昨收盘
            latest_price = tick_data.get('lastPrice', 0)
            pre_close = tick_data.get('lastClose', 0)
            
            if latest_price <= 0:
                self.log_pipe.send(f"[{self.stock_code}] 最新价格无效：{latest_price}")
                return
            
            if pre_close <= 0:
                self.log_pipe.send(f"[{self.stock_code}] 昨收盘价格无效：{pre_close}")
                return
            
            # 根据股票代码判断涨停幅度
            if self.stock_code.startswith(('300', '301')):  # 创业板
                limit_up_ratio = 1.2  # 20%
                board_type = "创业板"
            elif self.stock_code.startswith(('688', '689')):  # 科创板
                limit_up_ratio = 1.2  # 20%
                board_type = "科创板"
            else:  # 主板
                limit_up_ratio = 1.1  # 10%
                board_type = "主板"
            
            # 计算涨停价
            limit_up_price = round(pre_close * limit_up_ratio, 2)
            
            self.log_pipe.send(f"[{self.stock_code}] {board_type}，最新价：{latest_price}，昨收盘：{pre_close}，涨停价：{limit_up_price}（涨幅{int((limit_up_ratio-1)*100)}%）")
            
            # 检查夜市价格是否接近涨停价
            # 如果夜市价格接近涨停价（允许0.01元误差），才检查涨停板
            if abs(self.base_price - limit_up_price) <= 0.01:
                self.log_pipe.send(f"[{self.stock_code}] 夜市价格{self.base_price}接近涨停价{limit_up_price}，检查涨停板状态")
                
                # 检查是否封涨停板
                price_diff = abs(latest_price - limit_up_price)
                is_limit_up = price_diff <= 0.01
                
                if is_limit_up:
                    self.log_pipe.send(f"[{self.stock_code}] 检测到涨停板趋势（价格达到涨停价），保持夜市")
                else:
                    self.log_pipe.send(f"[{self.stock_code}] 未检测到涨停板趋势（价格未达到涨停价，差距{price_diff:.3f}元），准备撤单")
                    
                    # 如果有订单ID，发送撤单信号
                    if self.order_id:
                        self._cancel_order()
                    else:
                        self.log_pipe.send(f"[{self.stock_code}] 没有订单ID，无法撤单")
            else:
                # 夜市价格不是涨停价，检查价格是否合理
                self.log_pipe.send(f"[{self.stock_code}] 夜市价格{self.base_price}不是涨停价，检查价格合理性")
                
                # 如果最新价远低于委托价格，可能市场情绪不好，考虑撤单
                price_diff_ratio = (self.base_price - latest_price) / self.base_price
                
                if price_diff_ratio > 0.05:  # 如果最新价比委托价格低5%以上
                    self.log_pipe.send(f"[{self.stock_code}] 最新价比委托价格低{price_diff_ratio*100:.1f}%，市场情绪可能不好，准备撤单")
                    
                    # 如果有订单ID，发送撤单信号
                    if self.order_id:
                        self._cancel_order()
                    else:
                        self.log_pipe.send(f"[{self.stock_code}] 没有订单ID，无法撤单")
                else:
                    self.log_pipe.send(f"[{self.stock_code}] 价格合理，保持夜市（最新价{latest_price}，委托价{self.base_price}，价差{price_diff_ratio*100:.1f}%）")
                    
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 检查涨停板状态失败：{str(e)}")
    
    def _start_limit_down_check_timer(self):
        """启动跌停板检查定时器"""
        try:
            # 计算到9点14分55秒的时间（清空tick数据）
            now = datetime.now()
            clear_time = get_morning_clear_time()
            
            # 如果今天已经过了清空时间，设置为明天
            if now >= clear_time:
                clear_time += timedelta(days=1)
            
            # 计算到9点17分55秒的时间（检查跌停板）
            check_time = get_morning_check_time()
            if now >= check_time:
                check_time += timedelta(days=1)
            
            # 计算等待时间（秒）
            clear_wait_seconds = (clear_time - now).total_seconds()
            check_wait_seconds = (check_time - now).total_seconds()
            
            self.log_pipe.send(f"[{self.stock_code}] 跌停板检查定时器将在 {check_time.strftime('%Y-%m-%d %H:%M:%S')} 执行，等待 {check_wait_seconds:.1f} 秒")
            self.log_pipe.send(f"[{self.stock_code}] 将在 {clear_time.strftime('%Y-%m-%d %H:%M:%S')} 清空tick数据，等待 {clear_wait_seconds:.1f} 秒")
            
            # 创建清空tick数据的定时器
            clear_timer = threading.Timer(clear_wait_seconds, self._clear_tick_data)
            clear_timer.daemon = True
            clear_timer.start()
            
            # 创建跌停板检查定时器
            self.check_timer = threading.Timer(check_wait_seconds, self._check_limit_down_and_cancel)
            self.check_timer.daemon = True
            self.check_timer.start()
            
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 启动跌停板检查定时器失败：{str(e)}")
            import traceback
            self.log_pipe.send(f"[{self.stock_code}] 错误详情：{traceback.format_exc()}")
    
    def _check_limit_down_and_cancel(self):
        """检查是否封跌停板，如果没有则撤单（用于夜市卖出）"""
        try:
            self.log_pipe.send(f"[{self.stock_code}] 开始检查跌停板状态")
            
            # 获取最新的tick数据
            with self.tick_lock:
                tick_data = self.latest_tick_data
            
            if not tick_data:
                self.log_pipe.send(f"[{self.stock_code}] 没有tick数据，等待新的实时数据...")
                # 等待一段时间，让新的tick数据到达
                time_module.sleep(5)
                # 再次尝试获取tick数据
                with self.tick_lock:
                    tick_data = self.latest_tick_data
                
                if not tick_data:
                    self.log_pipe.send(f"[{self.stock_code}] 仍然没有tick数据，无法检查跌停板状态")
                    return
            
            # 获取最新价和昨收盘
            latest_price = tick_data.get('lastPrice', 0)
            pre_close = tick_data.get('lastClose', 0)
            
            if latest_price <= 0:
                self.log_pipe.send(f"[{self.stock_code}] 最新价格无效：{latest_price}")
                return
            
            if pre_close <= 0:
                self.log_pipe.send(f"[{self.stock_code}] 昨收盘价格无效：{pre_close}")
                return
            
            # 根据股票代码判断跌停幅度
            if self.stock_code.startswith(('300', '301')):  # 创业板
                limit_down_ratio = 0.8  # -20%
                board_type = "创业板"
            elif self.stock_code.startswith(('688', '689')):  # 科创板
                limit_down_ratio = 0.8  # -20%
                board_type = "科创板"
            else:  # 主板
                limit_down_ratio = 0.9  # -10%
                board_type = "主板"
            
            # 计算跌停价
            limit_down_price = round(pre_close * limit_down_ratio, 2)
            
            self.log_pipe.send(f"[{self.stock_code}] {board_type}，最新价：{latest_price}，昨收盘：{pre_close}，跌停价：{limit_down_price}（跌幅{int((1-limit_down_ratio)*100)}%）")
            
            # 检查夜市价格是否接近跌停价
            # 如果夜市价格接近跌停价（允许0.01元误差），才检查跌停板
            if abs(self.base_price - limit_down_price) <= 0.01:
                self.log_pipe.send(f"[{self.stock_code}] 夜市价格{self.base_price}接近跌停价{limit_down_price}，检查跌停板状态")
                
                # 检查是否封跌停板
                price_diff = abs(latest_price - limit_down_price)
                is_limit_down = price_diff <= 0.01
                
                if is_limit_down:
                    self.log_pipe.send(f"[{self.stock_code}] 检测到跌停板趋势（价格达到跌停价），保持夜市")
                else:
                    self.log_pipe.send(f"[{self.stock_code}] 未检测到跌停板趋势（价格未达到跌停价，差距{price_diff:.3f}元），准备撤单")
                    
                    # 如果有订单ID，发送撤单信号
                    if self.order_id:
                        self._cancel_order()
                    else:
                        self.log_pipe.send(f"[{self.stock_code}] 没有订单ID，无法撤单")
            else:
                # 夜市价格不是跌停价，检查价格是否合理
                self.log_pipe.send(f"[{self.stock_code}] 夜市价格{self.base_price}不是跌停价，检查价格合理性")
                
                # 如果最新价远高于委托价格，可能市场情绪好，考虑撤单
                price_diff_ratio = (latest_price - self.base_price) / self.base_price
                
                if price_diff_ratio > 0.05:  # 如果最新价比委托价格高5%以上
                    self.log_pipe.send(f"[{self.stock_code}] 最新价比委托价格高{price_diff_ratio*100:.1f}%，市场情绪可能较好，考虑撤单")
                    
                    # 如果有订单ID，发送撤单信号
                    if self.order_id:
                        self._cancel_order()
                    else:
                        self.log_pipe.send(f"[{self.stock_code}] 没有订单ID，无法撤单")
                else:
                    self.log_pipe.send(f"[{self.stock_code}] 价格合理，保持夜市（最新价{latest_price}，委托价{self.base_price}，价差{price_diff_ratio*100:.1f}%）")
                    
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 检查跌停板状态失败：{str(e)}")

    def _cancel_order(self):
        """撤单"""
        try:
            self.log_pipe.send(f"[{self.stock_code}] 发送撤单信号，订单ID：{self.order_id}")
            
            # 发送撤单信号
            cancel_signal = {
                'type': 'cancel',
                'order_id': self.order_id,
                'reason': '未封涨停板撤单',
                'time': datetime.now()
            }
            
            self.send_trade_signal([cancel_signal])
            
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 发送撤单信号失败：{str(e)}")

    def _wait_for_limit_up_check(self):
        """等待涨停板检查完成"""
        try:
            self.log_pipe.send(f"[{self.stock_code}] 涨停板检查定时器已启动，策略继续运行等待检查完成...")
            
            # 检查定时器状态
            if self.check_timer:
                self.log_pipe.send(f"[{self.stock_code}] 定时器对象存在：{self.check_timer}")
                if self.check_timer.is_alive():
                    self.log_pipe.send(f"[{self.stock_code}] 定时器正在运行，策略继续运行等待完成...")
                    # 不阻塞策略进程，让策略继续运行，但检查停止标志
                    while self.check_timer.is_alive() and not self.stop_requested:
                        time_module.sleep(0.1)
                        if self.stop_requested:
                            #self.log_pipe.send(f"[{self.stock_code}] 收到停止信号，退出等待涨停板检查")
                            break
                else:
                    #self.log_pipe.send(f"[{self.stock_code}] 定时器未运行")
                    pass
            else:
                self.log_pipe.send(f"[{self.stock_code}] 定时器对象不存在")
                
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 等待涨停板检查时出错：{str(e)}")
            import traceback
            self.log_pipe.send(f"[{self.stock_code}] 错误详情：{traceback.format_exc()}")

    def _wait_for_limit_down_check(self):
        """等待跌停板检查完成"""
        try:
            self.log_pipe.send(f"[{self.stock_code}] 跌停板检查定时器已启动，策略继续运行等待检查完成...")
            
            # 检查定时器状态
            if self.check_timer:
                self.log_pipe.send(f"[{self.stock_code}] 定时器对象存在：{self.check_timer}")
                if self.check_timer.is_alive():
                    self.log_pipe.send(f"[{self.stock_code}] 定时器正在运行，策略继续运行等待完成...")
                    # 不阻塞策略进程，让策略继续运行，但检查停止标志
                    while self.check_timer.is_alive() and not self.stop_requested:
                        time_module.sleep(0.1)
                        if self.stop_requested:
                            #self.log_pipe.send(f"[{self.stock_code}] 收到停止信号，退出等待跌停板检查")
                            break
                else:
                    self.log_pipe.send(f"[{self.stock_code}] 定时器未运行")
            else:
                self.log_pipe.send(f"[{self.stock_code}] 定时器对象不存在")
                
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 等待跌停板检查时出错：{str(e)}")
            import traceback
            self.log_pipe.send(f"[{self.stock_code}] 错误详情：{traceback.format_exc()}")

    def _wait_until_morning(self):
        """等待直到第二天早上9点19分55秒以后再停止"""
        try:
            self.log_pipe.send(f"[{self.stock_code}] 等待直到第二天早上9点19分55秒以后再停止")
            
            # 计算到第二天早上9点19分55秒的时间
            now = datetime.now()
            target_time = now.replace(hour=9, minute=19, second=55, microsecond=0)
            
            # 如果今天已经过了9点19分55秒，设置为明天
            if now >= target_time:
                target_time += timedelta(days=1)
            
            # 计算等待时间（秒）
            wait_seconds = (target_time - now).total_seconds()
            
            self.log_pipe.send(f"[{self.stock_code}] 等待 {wait_seconds:.1f} 秒，直到第二天早上9点19分55秒以后再停止")
            
            # 分段等待，每0.1秒检查一次停止标志
            waited_time = 0
            while waited_time < wait_seconds and not self.stop_requested:
                time_module.sleep(0.1)
                waited_time += 0.1
                if self.stop_requested:
                    self.log_pipe.send(f"[{self.stock_code}] 收到停止信号，退出等待")
                    break
            
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 等待直到第二天早上9点19分55秒以后再停止时出错：{str(e)}")
