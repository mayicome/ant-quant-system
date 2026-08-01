import os
import sys
from utils.trading_day import is_tradeday
from datetime import datetime, date
import time
import msvcrt  # Windows下的文件锁模块

# 将项目根目录添加到Python路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

# 然后再导入模块

from core.task_manager import TaskManager
from strategies.base_strategy import BaseStrategy
from core.backtest_engine import BacktestEngine
import logging
import math
import pandas as pd
from utils.logger import Logger

class BacktestManager:
    """回测任务管理器"""
    def __init__(self):
        self.logger = Logger(mode='backtest')  # 使用回测模式的日志
        self.strategies = {}  # 存储策略实例
        self.engines = {}     # 存储回测引擎实例
        self.task_manager = None  # 不再在这里创建 TaskManager
        self.pause_flag = False  # 添加暂停标志
        self.trade_records = []  # 添加交易记录列表
        self.cash_balance = 1000000  # 添加初始现金余额（默认100万）
        
        # 初始化管道
        self.log_pipe = MockPipe()
        self.control_pipe = MockPipe()
        
        # 设置管道的 logger
        self.log_pipe.set_logger(self.logger)
        self.control_pipe.set_logger(self.logger)
        
        # 从TaskManager复用策略类映射
        self.strategy_map = {
            "规则任务": "ModerateStrategy",
            "万能策略": "ModerateStrategy",
        }
        
    def set_text_edit(self, text_edit):
        """设置textEdit并添加handler"""
        if text_edit:
            self.logger.add_text_edit_handler(text_edit)
            #self.logger.info("回测日志系统初始化完成")
        
    def create_backtest_engine(self, stock_code, stock_data, start_date, end_date):
        """创建回测引擎实例"""
        try:
            engine = BacktestEngine(stock_code)
            engine.set_logger(self.logger)  # 设置logger
            
            # 加载历史数据
            success = engine.load_data(start_date, end_date)
            
            if success:
                self.engines[stock_code] = engine
                return engine
            else:
                self.logger.error(f"加载{stock_code}的历史数据失败")
                return None
                
        except Exception as e:
            self.logger.error(f"创建回测引擎失败: {str(e)}")
            return None
            
    def get_strategy_class(self, strategy_name):
        """获取策略类"""
        try:
            # 从策略映射中获取策略类名
            class_name = self.strategy_map.get(strategy_name)
            if not class_name:
                self.logger.error(f"未找到策略: {strategy_name}")
                return None
                
            # 修改导入逻辑，参考task_manager.py
            if class_name == "ConservativeStrategy":
                from strategies.conservative_strategy import ConservativeStrategy
                return ConservativeStrategy
            elif class_name == "ModerateStrategy":
                from strategies.moderate_strategy import ModerateStrategy
                return ModerateStrategy
            elif class_name == "AggressiveStrategy":
                from strategies.aggressive_strategy import AggressiveStrategy
                return AggressiveStrategy
            else:
                self.logger.error(f"未知的策略类型: {class_name}")
                return None
            
        except Exception as e:
            self.logger.error(f"获取策略类失败: {str(e)}")
            return None
            
    def create_strategy(self, stock_code, stock_data, strategy_name, params):
        """创建策略实例"""
        try:
            # 获取策略类
            strategy_class = self.get_strategy_class(strategy_name)
            if not strategy_class:
                self.logger.error(f"[{stock_code}] 获取策略类失败")
                return None
            
            # 构建任务信息
            task_info = {
                'stock_code': stock_data['stock_code'],
                'init_volume': stock_data['init_volume'],
                'init_cost': stock_data['open_price'],
                'base_price': stock_data['open_price'],
                'params': params,
                'buy_date': stock_data['buy_date'],  # 添加buy_date
                'strategy' : strategy_name
            }
            
            # 创建策略实例
            strategy = strategy_class(task_info, self.log_pipe, self.control_pipe)
            
            # 设置 control_pipe
            self.log_pipe.set_control_pipe(self.control_pipe)
            
            return strategy
            
        except Exception as e:
            self.logger.error(f"创建策略实例失败: {str(e)}")
            raise
            
    def start_backtest(self, stock_code, stock_data, strategy_name, params, buy_date, start_date, end_date, thread=None):
        """启动回测"""
        try:
            # 更新状态为运行中
            if hasattr(thread, 'status_signal'):
                thread.status_signal.emit("运行中")
                
            # 将buy_date添加到stock_data中
            if '_' in buy_date:
                buy_date = buy_date.split('_')[0]
            stock_data['buy_date'] = buy_date
            
            # 计算回测结束日期（买入日期 + 最大持有交易日）
            max_days = params.get('max_days', 3)  # 默认3天
            end_date = self.calculate_end_date(buy_date, max_days)
            
            # 获取当前是第几次回测
            current_index = getattr(thread, 'current_index', 1)  # 默认从1开始
            total_combinations = getattr(thread, 'total_combinations', 0)
            
            # 检查是否已有回测引擎
            if stock_code in self.engines:
                engine = self.engines[stock_code]
                #self.logger.info(f"[{stock_code}] 复用已有的回测引擎")
            else:
                # 创建新的回测引擎
                engine = BacktestEngine(stock_code)
                engine.set_logger(self.logger)  # 设置logger
                self.engines[stock_code] = engine
                #self.logger.info(f"[{stock_code}] 创建新引擎")
            
            # 检查缓存中是否已有数据
            cache_key = f"{stock_code}_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}"
            if cache_key in BacktestEngine._data_cache:
                self.logger.info(f"[{stock_code}] 使用缓存数据")
                engine.data = BacktestEngine._data_cache[cache_key]
            elif engine.data is None or len(engine.data) == 0:
                # 加载历史数据
                #self.logger.info(f"[{stock_code}] 开始加载历史数据")
                success = engine.load_data(start_date, end_date)
                if not success:
                    self.logger.error(f"加载{stock_code}的历史数据失败")
                    return False
            
            # 创建策略实例前，设置strategy属性
            self.log_pipe.strategy = None  # 清除旧的引用
            self.control_pipe.strategy = None
            
            strategy = self.create_strategy(stock_code, stock_data, strategy_name, params)
            if not strategy:
                self.logger.error(f"创建策略实例失败")
                return False
            
            # 设置strategy属性
            self.log_pipe.strategy = strategy
            self.control_pipe.strategy = strategy
            
            # 设置策略到回测引擎
            engine.set_strategy(strategy)
            
            # 开始回测
            order_id = 1
            
            # 记录当前可用仓位和现金余额
            current_volume = stock_data['init_volume']  # 当前持仓数量
            current_available = stock_data.get('available_volume', stock_data['init_volume'])  # 使用持仓信息中的可用持仓数量，如果没有则使用初始持仓数量
            self.cash_balance = 1000000  # 重置现金余额
            
            self.logger.info(f"[{stock_code}] 第 {current_index}/{total_combinations} 次回测开始，参数为：{params}")
            
            # 清空交易记录
            self.trade_records = []
            
            # 遍历每个tick数据
            count = 0
            last_price = None  # 添加变量记录最后一个有效价格
            current_date = None  # 当前处理的日期
            check_time = None  # 需要检查的时间点（11:05）
            days_held = 0  # 已经持有的天数
            
            for idx, tick_data in engine.data.iterrows():
                count += 1
                try:
                    # 只处理到结束日期当天的数据
                    if tick_data['time'].date() > end_date.date():
                        break
                    
                    # 检查是否需要停止
                    if thread and not thread.is_running:
                        self.logger.info(f"回测任务被用户停止：{stock_code}")
                        return False
                    
                    # 检查是否需要暂停
                    if self.pause_flag:
                        self.logger.info("回测暂停")
                        break
                    
                    # 如果持仓为0，提前结束回测
                    if current_volume <= 0:
                        self.logger.info(f"[{stock_code}] 持仓为0，提前结束回测")
                        break

                    # 记录每个tick的价格
                    if tick_data['lastPrice'] > 0:
                        last_price = tick_data['lastPrice']
                    
                    # 检查是否是新的一天
                    tick_date = tick_data['time'].date()
                    if current_date != tick_date:
                        current_date = tick_date
                        days_held += 1
                        self.logger.info(f"[{stock_code}] 当前日期: {current_date}, 已持有天数: {days_held}")
                    
                    # 检查是否需要检查11:05的涨停情况
                    if days_held >= params.get('max_days', 1):  # 如果已经持有足够的天数
                        tick_time = tick_data['time'].time()
                        if tick_time.hour == 11 and tick_time.minute == 5:
                            # 检查是否涨停
                            if tick_data['lastPrice'] < tick_data['lastClose'] * 1.1:  # 未涨停
                                self.logger.info(f"[{stock_code}] 11:05未涨停，清仓")
                                # 生成卖出信号
                                signals = [{
                                    'type': 'sell',
                                    'price': tick_data['lastPrice'],
                                    'volume': current_available,
                                    'reason': '11:05未涨停'
                                }]
                            else:
                                self.logger.info(f"[{stock_code}] 11:05涨停，继续持有")
                                signals = None
                        else:
                            signals = strategy.on_tick(tick_data)
                    else:
                        signals = strategy.on_tick(tick_data)
                    
                    # 处理交易信号
                    if signals:
                        for signal in signals:
                            if signal['type'] == 'sell':  # 卖出
                                # 如果是超过最大持仓天数的卖出信号，使用全部可用持仓
                                if signal.get('reason', '') == '超过最大持有天数':
                                    order_volume = current_available
                                else:
                                    # 计算每次应该卖出的数量
                                    if current_available >= signal['volume']*1.5:
                                        order_volume = signal['volume']
                                    else:
                                        order_volume = current_available
                                
                                # 检查可用持仓是否足够
                                if current_available <= 0:
                                    continue
                                    
                                # 计算实际交易数量
                                if order_volume > current_available:
                                    order_volume = current_available
                                
                                # 记录交易
                                trade_record = {
                                    'order_id': str(order_id),  # 转换为字符串
                                    'stock_code': stock_code,
                                    'type': "卖出",
                                    'time': tick_data['time'].strftime('%Y-%m-%d %H:%M:%S'),  # 转换为字符串格式
                                    'price': signal['price'],
                                    'volume': order_volume,
                                    'order_status': "已成",
                                    'trade_volume': order_volume,
                                    'trade_price': signal['price'],
                                    'strategy_name': strategy_name,
                                    'reason': signal.get('reason', ''),
                                    'cash_balance': self.cash_balance,
                                    'current_volume': current_volume,
                                    'current_available': current_available,
                                    'params': params  # 添加参数信息
                                }
                                
                                # 更新持仓和可用数量
                                current_volume -= order_volume
                                current_available -= order_volume
                                
                                # 更新现金余额（卖出增加现金）
                                self.cash_balance += order_volume * signal['price']
                                
                                self.trade_records.append(trade_record)
                                
                                # 通过control_pipe发送更新基准价格的消息
                                strategy.control_pipe.send(('update_base_price', {'base_price': signal['price']}))
                                
                                # 立即发送交易记录到UI
                                if hasattr(thread, 'trade_record_signal'):
                                    thread.trade_record_signal.emit(trade_record)
                            
                            else:  # 买入
                                order_volume = signal['volume']
                                required_cash = order_volume * signal['price']
                                
                                # 检查现金是否足够
                                if required_cash > self.cash_balance:
                                    self.logger.warning(f"现金不足，无法完成交易。所需现金: {required_cash}, 可用现金: {self.cash_balance}")
                                    continue
                                
                                # 记录交易
                                trade_record = {
                                    'order_id': str(order_id),  # 转换为字符串
                                    'stock_code': stock_code,
                                    'type': "买入",
                                    'time': tick_data['time'].strftime('%Y-%m-%d %H:%M:%S'),  # 转换为字符串格式
                                    'price': signal['price'],
                                    'volume': order_volume,
                                    'order_status': "已成",
                                    'trade_volume': order_volume,
                                    'trade_price': signal['price'],
                                    'strategy_name': strategy_name,
                                    'reason': signal.get('reason', ''),
                                    'cash_balance': self.cash_balance,
                                    'current_volume': current_volume,
                                    'current_available': current_available,
                                    'params': params  # 添加参数信息
                                }
                                
                                # 更新持仓和可用数量
                                current_volume += order_volume
                                current_available += order_volume
                                
                                # 更新现金余额（买入减少现金）
                                self.cash_balance -= required_cash
                                
                                self.trade_records.append(trade_record)
                                
                                # 立即发送交易记录到UI
                                if hasattr(thread, 'trade_record_signal'):
                                    thread.trade_record_signal.emit(trade_record)
                            
                            order_id += 1
                            
                except Exception as e:
                    self.logger.error(f"处理tick数据出错：{str(e)}")
                    import traceback
                    self.logger.error(f"错误堆栈：{traceback.format_exc()}")
                    continue
            
            # 确保使用最后一个有效价格
            if last_price is None:
                self.logger.warning(f"未找到有效价格，使用初始成本作为最后价格")
                last_price = stock_data['open_price']
            
            # 获取回测结束日期当天的最后一个价格
            if len(engine.data) > 0:
                # 将时间列转换为日期
                engine.data['date'] = engine.data['time'].dt.date
                
                # 获取回测结束日期当天的数据
                end_date_data = engine.data[engine.data['date'] == end_date.date()]
                
                # 添加日志
                self.logger.info(f"[{stock_code}] 回测结束日期: {end_date.date()}")
                self.logger.info(f"[{stock_code}] 数据中的日期范围: {engine.data['date'].min()} 到 {engine.data['date'].max()}")
                self.logger.info(f"[{stock_code}] 结束日期当天的数据条数: {len(end_date_data)}")
                
                if len(end_date_data) > 0:
                    # 使用回测结束日期当天的最后一个价格
                    last_price = end_date_data.iloc[-1]['lastPrice']
                    self.logger.info(f"[{stock_code}] 使用回测结束日期({end_date.date()})的最后一个价格：{last_price}")
                else:
                    # 如果没有回测结束日期的数据，使用最后一个有效价格
                    last_price = engine.data.iloc[-1]['lastPrice']
                    self.logger.info(f"[{stock_code}] 未找到回测结束日期的数据，使用最后一个有效价格：{last_price}")
                    self.logger.info(f"[{stock_code}] 最后一个有效价格的日期：{engine.data.iloc[-1]['date']}")
            
            # 将收盘价保存到stock_data中
            stock_data['last_price'] = last_price
            
            # 计算回测结果
            final_cash = self.cash_balance
            final_volume = current_volume
            final_value = final_cash + final_volume * last_price
            
            # 计算收益率(不含初始资金。因为大多数策略用不到初始资金)
            initial_value = 1000000 + stock_data['init_volume'] * stock_data['open_price']
            final_value -= 1000000
            initial_value -= 1000000
            return_rate = (final_value - initial_value) / initial_value * 100
            print(f"[{stock_code}] 回测结束，最终价值: {final_value}, 初始价值: {initial_value}, 收益率: {return_rate}%")
            
            # 生成回测记录
            backtest_record = {
                'stock_code': stock_code,
                'stock_name': stock_data.get('stock_name', ''),
                'strategy_name': strategy_name,
                'buy_date': buy_date,
                'start_date': start_date,
                'end_date': end_date,
                'max_days': max_days,
                'initial_cash': 1000000,
                'initial_volume': stock_data['init_volume'],
                'initial_price': stock_data['open_price'],
                'final_cash': final_cash,
                'final_volume': final_volume,
                'final_price': last_price,
                'return_rate': return_rate,
                'trade_count': len(self.trade_records),
                'params': params
            }
            
            # 保存回测记录
            self.save_backtest_record(backtest_record)
            
            # 发送回测结果到UI
            if hasattr(thread, 'backtest_result_signal'):
                thread.backtest_result_signal.emit(backtest_record)
                
            return True
            
        except Exception as e:
            self.logger.error(f"启动回测失败: {str(e)}")
            import traceback
            self.logger.error(f"错误详情: {traceback.format_exc()}")
            return False
            
    def stop_backtest(self, stock_code):
        """停止回测"""
        if stock_code in self.engines:
            del self.engines[stock_code]
        if stock_code in self.strategies:
            del self.strategies[stock_code]
        return True 

    def calculate_end_date(self, buy_date, max_days):
        """计算回测结束日期（买入日期 + 10个交易日）"""
        try:
            from datetime import datetime, timedelta
            
            # 将字符串日期转换为datetime对象
            if '_' in buy_date:
                buy_date = buy_date.split('_')[0]
            buy_date = datetime.strptime(buy_date, '%Y-%m-%d')
            
            current_date = buy_date
            trading_days = 0
            
            # 循环直到找到足够的交易日
            while trading_days < 10:
                current_date += timedelta(days=1)
                if is_tradeday(current_date):
                    trading_days += 1
            
            # 如果当前日期不是交易日，找到最近的前一个交易日
            while not is_tradeday(current_date):
                current_date -= timedelta(days=1)
            
            # 返回datetime对象
            return current_date
            
        except Exception as e:
            self.logger.error(f"计算回测结束日期失败: {str(e)}")
            return None

    def save_backtest_record(self, record):
        """保存回测记录到Excel文件"""
        try:
            # 确保目录存在
            records_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'backtest_records')
            os.makedirs(records_dir, exist_ok=True)
            
            # 生成文件名
            file_name = "backtest_records.xlsx"
            file_path = os.path.join(records_dir, file_name)
            backup_file_path = os.path.join(records_dir, "backtest_records_backup.xlsx")
            
            # 使用文件锁
            lock_file = os.path.join(records_dir, "backtest_records.lock")
            max_retries = 3
            retry_count = 0
            
            while retry_count < max_retries:
                try:
                    # 尝试创建锁文件
                    with open(lock_file, 'x') as f:
                        # 将日期对象转换为字符串
                        for key in ['buy_date', 'start_date', 'end_date']:
                            if key in record:
                                if isinstance(record[key], (datetime, date)):
                                    if '_' in record[key]:
                                        record[key] = record[key].split('_')[0]
                                    record[key] = record[key].strftime('%Y-%m-%d')
                                elif isinstance(record[key], str):
                                    try:
                                        # 如果已经是字符串格式的日期，确保格式正确
                                        if '_' in record[key]:
                                            record[key] = record[key].split('_')[0]
                                        datetime.strptime(record[key], '%Y-%m-%d')
                                    except ValueError:
                                        # 如果格式不正确，尝试转换
                                        try:
                                            dt = datetime.strptime(record[key], '%Y/%m/%d')
                                            record[key] = dt.strftime('%Y-%m-%d')
                                        except ValueError:
                                            self.logger.warning(f"无法转换日期格式: {record[key]}")
                        
                        # 确保所有数值类型都是基本类型
                        for key in ['initial_cash', 'initial_volume', 'initial_price', 
                                   'final_cash', 'final_volume', 'final_price', 'return_rate', 'trade_count']:
                            if key in record:
                                if isinstance(record[key], (float, int)):
                                    record[key] = float(record[key])
                        
                        # 从params中提取四个参数
                        params = record.get('params', {})
                        record['sell_times'] = params.get('sell_times', 3)
                        record['max_days'] = params.get('max_days', 2)
                        record['up_threshold'] = params.get('up_threshold', 1.0)
                        record['down_threshold'] = params.get('down_threshold', 1.0)
                        
                        # 读取现有记录
                        existing_records = []
                        if os.path.exists(file_path):
                            try:
                                # 先尝试读取现有文件
                                existing_records = pd.read_excel(file_path).to_dict('records')
                            except Exception as e:
                                self.logger.warning(f"读取回测记录文件失败，尝试读取备份文件：{str(e)}")
                                try:
                                    # 如果主文件读取失败，尝试读取备份文件
                                    if os.path.exists(backup_file_path):
                                        existing_records = pd.read_excel(backup_file_path).to_dict('records')
                                        # 如果备份文件读取成功，复制回主文件
                                        pd.DataFrame(existing_records).to_excel(file_path, index=False, engine='openpyxl')
                                except Exception as e2:
                                    self.logger.warning(f"读取备份文件也失败，将创建新文件：{str(e2)}")
                                    existing_records = []
                        
                        # 添加新记录
                        existing_records.append(record)
                        
                        # 转换为DataFrame
                        df = pd.DataFrame(existing_records)
                        
                        # 设置列的顺序
                        columns = [
                            'stock_code', 'stock_name', 'strategy_name', 'buy_date', 
                            'start_date', 'end_date', 'sell_times', 'max_days', 
                            'up_threshold', 'down_threshold', 'initial_cash', 
                            'initial_volume', 'initial_price', 'final_cash', 
                            'final_volume', 'final_price', 'return_rate', 'trade_count'
                        ]
                        
                        # 确保所有列都存在
                        for col in columns:
                            if col not in df.columns:
                                df[col] = None
                        
                        # 重新排序列
                        df = df[columns]
                        
                        try:
                            # 先保存到备份文件
                            df.to_excel(backup_file_path, index=False, engine='openpyxl')
                            
                            # 如果备份文件保存成功，再保存到主文件
                            df.to_excel(file_path, index=False, engine='openpyxl')
                            
                            # 添加一个小延迟，确保文件完全写入
                            time.sleep(0.5)
                            
                            #self.logger.info(f"回测记录已保存：{file_path}")
                        except Exception as e:
                            self.logger.error(f"保存回测记录失败：{str(e)}")
                            # 如果主文件保存失败，但备份文件保存成功，尝试从备份文件恢复
                            if os.path.exists(backup_file_path):
                                try:
                                    # 添加一个小延迟，确保文件完全写入
                                    time.sleep(0.5)
                                    pd.read_excel(backup_file_path).to_excel(file_path, index=False, engine='openpyxl')
                                    self.logger.info(f"已从备份文件恢复数据")
                                except Exception as e2:
                                    self.logger.error(f"从备份文件恢复数据失败：{str(e2)}")
                        
                        break  # 如果成功完成，跳出重试循环
                        
                except FileExistsError:
                    # 锁文件已存在，等待后重试
                    retry_count += 1
                    if retry_count < max_retries:
                        time.sleep(0.5)  # 等待0.5秒后重试
                    else:
                        self.logger.error("无法获取文件锁，已达到最大重试次数")
                        raise
                finally:
                    # 确保删除锁文件
                    try:
                        if os.path.exists(lock_file):
                            os.remove(lock_file)
                    except Exception as e:
                        self.logger.warning(f"删除锁文件失败：{str(e)}")
                    
        except Exception as e:
            self.logger.error(f"保存回测记录失败：{str(e)}")
            import traceback
            self.logger.error(f"错误详情：{traceback.format_exc()}")

# 修改MockPipe类的实现
class MockPipe:
    def __init__(self):
        self.strategy = None
        self.logger = None  # 初始化为 None
        self.control_pipe = None  # 添加 control_pipe 引用
        
    def set_logger(self, logger):
        """设置logger"""
        self.logger = logger
        
    def set_control_pipe(self, control_pipe):
        """设置control_pipe"""
        self.control_pipe = control_pipe
        
    def send(self, message):
        try:
            if isinstance(message, tuple):
                cmd, data = message
                if cmd == 'update_base_price':
                    if self.strategy:
                        # 兼容两种可能的格式
                        if isinstance(data, dict):
                            new_price = data.get('base_price')
                            stock_code = data.get('stock_code')
                        else:
                            new_price = data
                            stock_code = self.strategy.stock_code
                        # 更新策略的基准价
                        self.strategy.base_price = new_price
                        # 转发到control_pipe
                        if self.control_pipe:
                            self.control_pipe.send(('update_base_price', new_price))
            elif isinstance(message, str):
                if self.logger:
                    self.logger.info(message)
                pass
        except Exception as e:
            if self.logger:
                self.logger.error(f"MockPipe处理消息出错: {str(e)}")
    
    def recv(self):
        return None 