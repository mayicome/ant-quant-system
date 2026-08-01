import time
import logging
from datetime import datetime
from utils.logger import Logger

class BuyStrategy:
    """买入策略 - 实时监控股票价格，当最新价不大于买入价时执行买入"""
    
    def __init__(self, task_info, log_pipe, control_pipe=None):
        self.task_info = task_info
        self.log_pipe = log_pipe
        self.control_pipe = control_pipe
        self.stock_code = task_info.get('stock_code', '')
        self.buy_price = task_info.get('params', {}).get('buy_price', 0)
        self.buy_volume = task_info.get('params', {}).get('buy_volume', 0)
        self.buy_type = '限价'  # 固定为限价买入
        
        # 创建日志记录器
        self.logger = Logger(mode='live')
        
        # 运行状态
        self.running = True
        
        self.log_pipe.send(f"[{self.stock_code}] 买入策略初始化完成")
        self.log_pipe.send(f"[{self.stock_code}] 买入价格: {self.buy_price}, 买入数量: {self.buy_volume}")
    
    def is_trading_time(self, current_time):
        """判断是否在交易时段内"""
        try:
            # 获取时间部分
            time_obj = current_time.time()
            
            # 判断是否在交易时段内
            morning_start = datetime.strptime('09:30:00', '%H:%M:%S').time()
            morning_end = datetime.strptime('11:30:00', '%H:%M:%S').time()
            afternoon_start = datetime.strptime('13:00:00', '%H:%M:%S').time()
            afternoon_end = datetime.strptime('15:00:00', '%H:%M:%S').time()
            
            return (morning_start <= time_obj <= morning_end) or (afternoon_start <= time_obj < afternoon_end)
            
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 判断交易时段出错：{str(e)}")
            return False
    
    def run(self):
        """运行买入策略"""
        try:
            self.log_pipe.send(f"[{self.stock_code}] 开始执行买入策略")
            
            # 检查参数
            if self.buy_price <= 0:
                self.log_pipe.send(f"[{self.stock_code}] 错误：买入价格必须大于0")
                return
            
            if self.buy_volume <= 0:
                self.log_pipe.send(f"[{self.stock_code}] 错误：买入数量必须大于0")
                return
            
            # 开始监控循环
            while self.running:
                try:
                    # 检查控制信号
                    if self.control_pipe and self.control_pipe.poll():
                        try:
                            signal = self.control_pipe.recv()
                            if signal == 'stop':
                                self.log_pipe.send(f"[{self.stock_code}] 收到停止信号，退出买入策略")
                                break
                        except:
                            pass
                    
                    # 获取最新价格
                    current_price = self._get_current_price()
                    if current_price is None:
                        self.log_pipe.send(f"[{self.stock_code}] 无法获取当前价格，等待重试...")
                        time.sleep(5)
                        continue
                    
                    # 检查是否满足买入条件
                    if current_price <= self.buy_price:
                        self.log_pipe.send(f"[{self.stock_code}] 当前价格 {current_price} <= 买入价格 {self.buy_price}，满足买入条件")
                        
                        # 检查是否在交易时间内
                        if self.is_trading_time(datetime.now()):
                            # 执行买入
                            success = self._execute_buy(current_price)
                            if success:
                                self.log_pipe.send(f"[{self.stock_code}] 买入执行成功，策略完成")
                                break
                            else:
                                self.log_pipe.send(f"[{self.stock_code}] 买入执行失败，继续监控...")
                        else:
                            self.log_pipe.send(f"[{self.stock_code}] 价格满足条件但不在交易时间内，等待交易时间...")
                    else:
                        # 价格不满足条件，继续监控
                        self.log_pipe.send(f"[{self.stock_code}] 当前价格 {current_price} > 买入价格 {self.buy_price}，继续监控...")
                    
                    # 等待一段时间后再次检查
                    time.sleep(3)
                    
                except Exception as e:
                    self.log_pipe.send(f"[{self.stock_code}] 监控循环出错：{str(e)}")
                    time.sleep(5)
                    
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 买入策略执行出错：{str(e)}")
        finally:
            self.log_pipe.send(f"[{self.stock_code}] 买入策略已退出")
    
    def _get_current_price(self):
        """获取当前价格"""
        try:
            # 通过控制管道请求价格数据
            if self.control_pipe:
                self.control_pipe.send(('get_price', self.stock_code))
                
                # 等待价格数据响应（设置超时）
                import select
                if hasattr(select, 'select'):
                    # Unix系统
                    ready, _, _ = select.select([self.control_pipe], [], [], 1.0)
                    if ready:
                        try:
                            response = self.control_pipe.recv()
                            if isinstance(response, tuple) and response[0] == 'price_data':
                                return float(response[1])
                        except:
                            pass
                else:
                    # Windows系统，使用poll
                    if self.control_pipe.poll(1.0):
                        try:
                            response = self.control_pipe.recv()
                            if isinstance(response, tuple) and response[0] == 'price_data':
                                return float(response[1])
                        except:
                            pass
            
            # 如果无法获取实时价格，返回买入价格作为默认值
            self.log_pipe.send(f"[{self.stock_code}] 无法获取实时价格，使用买入价格作为参考")
            return self.buy_price
            
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 获取当前价格失败：{str(e)}")
            return self.buy_price
    
    def _execute_buy(self, current_price):
        """执行买入操作"""
        try:
            self.log_pipe.send(f"[{self.stock_code}] 开始执行买入操作...")
            
            # 构建买入交易信号（限价买入）
            buy_signal = {
                'type': 'buy',
                'price': self.buy_price,  # 使用设定的买入价格
                'volume': self.buy_volume,
                'reason': f'买入策略触发：当前价{current_price} <= 买入价{self.buy_price}'
            }
            
            # 发送交易信号
            if self.control_pipe:
                self.control_pipe.send(('trade_signal', [buy_signal]))
                self.log_pipe.send(f"[{self.stock_code}] 已发送买入交易信号：价格={self.buy_price}, 数量={self.buy_volume}")
                
                # 更新任务状态
                self.control_pipe.send(('update_task_status', {
                    'stock_code': self.stock_code,
                    'status': '已完成',
                    'reason': f'买入策略执行完成，已发送买入信号'
                }))
                
                return True
            else:
                self.log_pipe.send(f"[{self.stock_code}] 控制管道不可用，无法发送买入信号")
                return False
                
        except Exception as e:
            self.log_pipe.send(f"[{self.stock_code}] 执行买入操作失败：{str(e)}")
            return False
    
    def stop(self):
        """停止策略"""
        self.running = False
        self.log_pipe.send(f"[{self.stock_code}] 买入策略收到停止信号")
