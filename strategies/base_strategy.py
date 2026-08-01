import time
from utils.trading_day import is_tradeday
import pandas as pd
from datetime import datetime
from utils.logger import Logger

class BaseStrategy:
    def __init__(self, task_info, log_pipe, control_pipe):
        self.task_info = task_info
        self.stock_code = task_info['stock_code']
        self.params = task_info['params']
        self.log_pipe = log_pipe
        self.control_pipe = control_pipe
        self.buy_date = task_info.get('buy_date')
        
        # 添加基本属性定义
        self.init_volume = task_info.get('init_volume', 0)
        self.base_price = task_info.get('base_price', 0)
        self.init_cost = task_info.get('init_cost', 0)
        
        # 清仓时间参数，00:00:00表示不清仓
        self.clear_time = self.params.get('clear_time', '00:00:00')
        
        # 记录是否已经触发过今日清仓
        self._clear_triggered_today = False
        self._last_clear_date = None
        
        # 创建日志包装器
        class LogWrapper:
            def __init__(self, pipe, prefix):
                self.pipe = pipe
                self.prefix = prefix
                
            def info(self, msg):
                try:
                    self.pipe.send(f"[{self.prefix}] {msg}")
                except (EOFError, BrokenPipeError, OSError):
                    # 管道已关闭，不再发送日志
                    pass
                
            def error(self, msg, exc_info=False):
                try:
                    self.pipe.send(f"[{self.prefix}] ERROR: {msg}")
                except (EOFError, BrokenPipeError, OSError):
                    # 管道已关闭，不再发送日志
                    pass
                
            def warning(self, msg):
                try:
                    self.pipe.send(f"[{self.prefix}] WARNING: {msg}")
                except (EOFError, BrokenPipeError, OSError):
                    # 管道已关闭，不再发送日志
                    pass
                
            def send(self, message):
                try:
                    self.pipe.send(message)
                except (EOFError, BrokenPipeError, OSError):
                    # 管道已关闭，不再发送日志
                    pass
        
        self.logger = LogWrapper(log_pipe, self.stock_code)
        
        # 初始化清仓检查
        self._check_clear_time_on_init()

    def _check_clear_time_on_init(self):
        """初始化时检查清仓时间"""
        try:
            # 如果不清仓，直接返回
            if self.clear_time == '00:00:00':
                return
                
            # 解析清仓时间
            from datetime import datetime, time
            clear_hour, clear_minute, clear_second = map(int, self.clear_time.split(':'))
            clear_time_obj = time(clear_hour, clear_minute, clear_second)
            
            # 获取当前时间
            current_time = datetime.now().time()
            
            # 如果当前时间已经过了清仓时间，标记为已触发
            if current_time >= clear_time_obj:
                self._clear_triggered_today = True
                self._last_clear_date = datetime.now().date()
                self.logger.info(f"当前时间已过清仓时间 {self.clear_time}，今日不再清仓")
            else:
                self.logger.info(f"设置今日清仓时间: {self.clear_time}")
                
        except Exception as e:
            self.logger.error(f"初始化时检查清仓时间出错：{str(e)}")

    def update_params(self, new_params):
        """更新策略参数"""
        try:
            old_clear_time = self.clear_time
            self.params.update(new_params)
            self.task_info['params'].update(new_params)  # 同时更新task_info中的参数
            self.clear_time = self.params.get('clear_time', '00:00:00')
            
            # 如果清仓时间被修改，重新检查
            if old_clear_time != self.clear_time:
                self._clear_triggered_today = False
                self._last_clear_date = None
                self._check_clear_time_on_init()
                
        except Exception as e:
            self.logger.error(f"更新参数出错：{str(e)}")

    def check_clear_time(self, tick_data):
        """检查是否到达清仓时间"""
        try:
            # 如果不清仓，直接返回False
            if self.clear_time == '00:00:00':
                return False
            
            # 从tick数据中获取当前时间
            current_time = tick_data['time']
            current_date = current_time.date()
            
            # 如果是新的一天，重置触发标志
            if self._last_clear_date != current_date:
                self._clear_triggered_today = False
                self._last_clear_date = current_date
                self.logger.info(f"新的一天开始，重置清仓触发标志 (日期: {current_date})")
            
            # 如果今天已经触发过清仓，不再触发
            if self._clear_triggered_today:
                return False
            
            # 解析清仓时间
            from datetime import time
            clear_hour, clear_minute, clear_second = map(int, self.clear_time.split(':'))
            clear_time_obj = time(clear_hour, clear_minute, clear_second)
            
            # 检查是否到达清仓时间
            current_time_obj = current_time.time()
            
            # 每10分钟记录一次清仓时间检查日志
            if not hasattr(self, '_last_clear_check_log'):
                self._last_clear_check_log = None
            if (self._last_clear_check_log is None or 
                (current_time_obj.minute % 10 == 0 and current_time_obj.second < 5)):
                self.logger.info(f"清仓时间检查: 当前时间 {current_time_obj.strftime('%H:%M:%S')}, 清仓时间 {self.clear_time}, 已触发: {self._clear_triggered_today}")
                self._last_clear_check_log = current_time_obj
            
            if current_time_obj >= clear_time_obj:
                # 标记为已触发
                self._clear_triggered_today = True
                self.logger.info(f"到达清仓时间 {self.clear_time}，触发清仓信号")
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查清仓时间出错：{str(e)}")
            return False

    def is_trading_time(self, tick_time):
        """判断是否在交易时段内"""
        try:
            # tick_time 已经是 datetime 对象，直接使用
            # 获取时间部分
            time = tick_time.time()
            
            # 判断是否在交易时段内
            morning_start = datetime.strptime('09:30:00', '%H:%M:%S').time()
            morning_end = datetime.strptime('11:30:00', '%H:%M:%S').time()
            afternoon_start = datetime.strptime('13:00:00', '%H:%M:%S').time()
            afternoon_end = datetime.strptime('15:00:00', '%H:%M:%S').time()
            
            return (morning_start <= time <= morning_end) or (afternoon_start <= time < afternoon_end) #15:00:00的就不处理了。
            
        except Exception as e:
            self.logger.error(f"判断交易时段出错：{str(e)}")
            return False

    def on_tick(self, tick_data):
        """处理tick数据"""
        # 检查是否有should_stop标志（子类可以设置此标志）
        if hasattr(self, 'should_stop') and self.should_stop:
            return []
        
        try:
            # 检查是否到了9:30:00，只有9:30:00之后才开始执行策略逻辑
            # 避免在集合竞价阶段（9:15-9:30）执行策略
            # 注意：夜市策略不受此限制，因为夜市策略有自己的运行逻辑
            strategy_name = self.task_info.get('strategy', '')
            is_night_market = '夜市' in strategy_name or 'NightMarket' in str(type(self))
            
            tick_time = tick_data.get('time')
            if tick_time and not is_night_market:
                from datetime import time as dt_time
                current_time = tick_time.time() if hasattr(tick_time, 'time') else tick_time
                trading_start_time = dt_time(9, 30, 0)
                
                # 如果还没到9:30:00，不执行策略逻辑（但清仓检查仍然执行）
                if current_time < trading_start_time:
                    # 记录一次日志，表示正在等待9:30:00
                    if not hasattr(self, '_waiting_for_930_logged'):
                        self.logger.info(f"[{self.stock_code}] 等待9:30:00开始执行策略，当前时间: {current_time.strftime('%H:%M:%S')}")
                        self._waiting_for_930_logged = True
                    # 只执行清仓检查，不执行策略逻辑
                    if self.check_clear_time(tick_data):
                        # 清仓逻辑继续执行
                        current_price = tick_data['lastPrice']
                        ask_prices = tick_data.get('askPrice', [current_price] * 5)
                        bid_prices = tick_data.get('bidPrice', [current_price] * 5)
                        ask_vols = tick_data.get('askVol', [0] * 5)
                        bid_vols = tick_data.get('bidVol', [0] * 5)
                        
                        if ask_vols[0] == 0:
                            self.logger.info(f"[{self.stock_code}] 涨停不发卖出信号，继续等待到15:00")
                        else:
                            signal = {
                                'type': 'sell',
                                'price': current_price,
                                'volume': 0,
                                'reason': f'今日清仓({self.clear_time})',
                                'askPrice': ask_prices,
                                'bidPrice': bid_prices,
                                'askVol': ask_vols,
                                'bidVol': bid_vols,
                                'time': tick_data['time']
                            }
                            return [signal]
                    return []
                else:
                    # 已经到9:30:00了，记录一次日志
                    if not hasattr(self, '_trading_started_logged'):
                        self.logger.info(f"[{self.stock_code}] 已到9:30:00，开始执行策略逻辑")
                        self._trading_started_logged = True
                
            # 检查清仓时间
            if self.check_clear_time(tick_data):
                current_price = tick_data['lastPrice']
                # 获取买卖盘数据（五档）
                ask_prices = tick_data.get('askPrice', [current_price] * 5)  # 卖档价格
                bid_prices = tick_data.get('bidPrice', [current_price] * 5)  # 买档价格
                ask_vols = tick_data.get('askVol', [0] * 5)  # 卖档量
                bid_vols = tick_data.get('bidVol', [0] * 5)  # 买档量
                
                self.logger.info(f"tick_data: {tick_data}")
                self.logger.info(f"[{self.stock_code}] 当前价格：{current_price}, 卖档价格：{ask_prices}, 买档价格：{bid_prices}, 卖档量：{ask_vols}, 买档量：{bid_vols}")
                
                # 检查是否涨停（卖一量为0）
                if ask_vols[0] == 0:
                    self.logger.info(f"[{self.stock_code}] 涨停不发卖出信号，继续等待到15:00")
                else:
                    # 生成卖出信号
                    signal = {
                        'type': 'sell',
                        'price': current_price,
                        'volume': 0,  # 设置为0，让回测管理器使用全部可用持仓
                        'reason': f'今日清仓({self.clear_time})',
                        'askPrice': ask_prices,
                        'bidPrice': bid_prices,
                        'askVol': ask_vols,
                        'bidVol': bid_vols,
                        'time': tick_data['time']  # 直接使用时间对象
                    }
                    return [signal]
            
            # 如果不是因为清仓时间触发卖出，则调用子类的具体实现
            signals = self._on_tick(tick_data)
            
            # 统一发送阈值信息给状态栏（纯规则任务子进程可关闭，避免误导性上下沿）
            if not getattr(self, '_suppress_threshold_status_update', False):
                try:
                    current_price = tick_data['lastPrice']
                    # 根据股票类型确定价格精度
                    from core.utils.security_type import SecurityTypeUtil
                    price_precision = SecurityTypeUtil.get_price_precision(self.stock_code)
                    
                    # 计算上下限阈值价格，使用动态精度
                    up_threshold_price = round(self.base_price * (1 + self.params['up_threshold'] / 100), price_precision)
                    down_threshold_price = round(self.base_price * (1 - self.params['down_threshold'] / 100), price_precision)
                    
                    self.log_pipe.send(('update_thresholds', {
                        'stock_code': self.stock_code,
                        'current_price': current_price,
                        'up_threshold': up_threshold_price,
                        'down_threshold': down_threshold_price
                    }))
                except (EOFError, BrokenPipeError, OSError):
                    pass
                except Exception:
                    # 阈值发送失败不影响策略运行
                    pass
            
            return signals
            
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 策略处理出错: {str(e)}")
            return []

    def _on_tick(self, tick_data):
        """子类必须实现的tick处理方法"""
        raise NotImplementedError("子类必须实现此方法")

    def run(self):
        """运行策略"""
        try:
            # 启动定时检查线程
            import threading
            timer_thread = threading.Thread(target=self._timer_check_loop, daemon=True)
            timer_thread.start()
            
            while True:
                try:
                    # 添加超时机制，避免阻塞太久
                    import select
                    import sys
                    import platform
                    
                    # 检查管道是否有数据可读
                    message = None
                    if hasattr(select, 'select') and platform.system() != 'Windows':
                        # Unix系统使用select
                        try:
                            ready, _, _ = select.select([self.control_pipe], [], [], 1.0)  # 1秒超时
                            if not ready:
                                # 超时，继续循环
                                continue
                            # 有数据可读，接收消息
                            message = self.control_pipe.recv()
                        except OSError:
                            # 如果select失败，使用非阻塞方式
                            try:
                                message = self.control_pipe.recv()
                            except (EOFError, BrokenPipeError):
                                self.logger.warning(f"[{self.stock_code}] 控制管道已关闭")
                                break
                            except:
                                # 没有数据可读，继续循环
                                continue
                    else:
                        # Windows系统或select失败，使用非阻塞方式
                        try:
                            message = self.control_pipe.recv()
                        except (EOFError, BrokenPipeError):
                            self.logger.warning(f"[{self.stock_code}] 控制管道已关闭")
                            break
                        except:
                            # 没有数据可读，继续循环
                            continue
                    
                    # 处理接收到的消息
                    if message is None:
                        continue
                    if isinstance(message, tuple) and len(message) == 2:
                        cmd, data = message
                        if cmd == 'stop':
                            self.logger.info(f"[{self.stock_code}] 收到停止信号，策略进程退出")
                            break
                        elif cmd == 'tick':
                            # 处理行情数据
                            # 每100次tick记录一次日志，避免日志过多
                            if not hasattr(self, '_tick_received_count'):
                                self._tick_received_count = 0
                            self._tick_received_count += 1
                            if self._tick_received_count % 100 == 0:
                                self.logger.info(f"[{self.stock_code}] 已接收 {self._tick_received_count} 次tick数据")
                            
                            signals = self.on_tick(data)
                            if signals:
                                self.send_trade_signal(signals)
                        elif cmd == 'update_base_price':
                            old_base_price = self.base_price
                            if isinstance(data, dict):
                                self.base_price = data['base_price']
                            else:
                                self.base_price = data
                            self.logger.info(f"[{self.stock_code}] 策略进程收到基准价更新: {old_base_price:.3f} -> {self.base_price:.3f}")
                        elif cmd == 'update_params':
                            # 处理参数更新
                            self.update_params(data)
                            self.logger.info(f"[{self.stock_code}] 策略参数已更新: {data}")
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
                    # 记录错误但不尝试发送日志
                    print(f"[{self.stock_code}] 策略运行错误：{str(e)}")
                    import traceback
                    print(f"[{self.stock_code}] 策略运行错误堆栈：{traceback.format_exc()}")
                    # 不要因为单次错误就退出，继续运行
                    continue
        except Exception as e:
            # 记录错误但不尝试发送日志
            print(f"[{self.stock_code}] 策略运行错误：{str(e)}")
            import traceback
            print(f"[{self.stock_code}] 策略运行错误堆栈：{traceback.format_exc()}")
        finally:
            # 清理资源
            try:
                if hasattr(self, 'control_pipe'):
                    self.control_pipe.close()
            except:
                pass

    def _timer_check_loop(self):
        """定时检查循环，独立于tick数据"""
        import time
        from datetime import datetime
        
        while True:
            try:
                # 每10秒检查一次清仓时间
                time.sleep(10)
                
                # 如果不清仓，跳过检查
                if self.clear_time == '00:00:00':
                    continue
                
                # 获取当前时间
                current_time = datetime.now()
                current_date = current_time.date()
                
                # 如果是新的一天，重置触发标志
                if self._last_clear_date != current_date:
                    self._clear_triggered_today = False
                    self._last_clear_date = current_date
                    self.logger.info(f"新的一天开始，重置清仓触发标志 (日期: {current_date})")
                
                # 如果今天已经触发过清仓，不再触发
                if self._clear_triggered_today:
                    continue
                
                # 解析清仓时间
                from datetime import time
                clear_hour, clear_minute, clear_second = map(int, self.clear_time.split(':'))
                clear_time_obj = time(clear_hour, clear_minute, clear_second)
                
                # 检查是否到达清仓时间
                current_time_obj = current_time.time()
                
                # 每10分钟记录一次清仓时间检查日志
                if not hasattr(self, '_last_timer_check_log'):
                    self._last_timer_check_log = None
                if (self._last_timer_check_log is None or 
                    (current_time_obj.minute % 10 == 0 and current_time_obj.second < 10)):
                    self.logger.info(f"[定时检查] 当前时间 {current_time_obj.strftime('%H:%M:%S')}, 清仓时间 {self.clear_time}, 已触发: {self._clear_triggered_today}")
                    self._last_timer_check_log = current_time_obj
                
                if current_time_obj >= clear_time_obj:
                    # 标记为已触发
                    self._clear_triggered_today = True
                    self.logger.info(f"[定时检查] 到达清仓时间 {self.clear_time}，触发清仓信号")
                    
                    # 生成清仓信号
                    try:
                        # 获取当前价格（如果有的话）
                        current_price = getattr(self, 'last_price', self.base_price)
                        
                        # 生成卖出信号
                        signal = {
                            'type': 'sell',
                            'price': current_price,
                            'volume': 0,  # 设置为0，让回测管理器使用全部可用持仓
                            'reason': f'定时清仓({self.clear_time})',
                            'stock_code': self.stock_code,
                            'strategy': self.strategy_name,
                            'timestamp': current_time
                        }
                        
                        # 发送交易信号
                        self.send_trade_signal([signal])
                        self.logger.info(f"[定时检查] 已发送清仓信号: {signal}")
                        
                    except Exception as e:
                        self.logger.error(f"[定时检查] 生成清仓信号失败: {str(e)}")
                
            except Exception as e:
                self.logger.error(f"[定时检查] 定时检查出错: {str(e)}")
                time.sleep(60)  # 出错时等待1分钟再继续

    def send_trade_signal(self, signals):
        """发送交易信号"""
        try:
            if not signals:
                return
            
            # 发送交易信号到控制管道
            try:
                self.control_pipe.send(('trade_signal', signals))
                #self.logger.info(f"已发送交易信号: {signals}")
            except (EOFError, BrokenPipeError, OSError):
                # 管道已关闭，不再发送信号
                pass
        
        except Exception as e:
            try:
                self.logger.error(f"发送交易信号失败: {str(e)}")
                import traceback
                self.logger.error(f"发送交易信号错误堆栈: {traceback.format_exc()}")
            except (EOFError, BrokenPipeError, OSError):
                # 管道已关闭，不再发送日志
                pass
            # 不要因为发送信号失败就退出策略进程
            pass 