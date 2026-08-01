import pandas as pd
from datetime import datetime, timedelta
from xtquant import xtdata
import logging
import time
import os

class BacktestEngine:
    """回测引擎"""
    _data_cache = {}  # 类级别的数据缓存
    
    def __init__(self, stock_code):
        self.stock_code = stock_code
        self.data = None
        self.strategy = None
        self.logger = None  # 初始化为None，等待外部设置
        
    def set_logger(self, logger):
        """设置logger"""
        self.logger = logger
        
    def load_data(self, start_date, end_date, save_to_file=True):
        """加载历史tick数据"""
        try:
            # 转换为QMT识别的股票代码格式
            if not self.stock_code.endswith(('.SH', '.SZ', '.BJ')):
                if self.stock_code.startswith(('0', '1', '3')):
                    self.stock_code = f"{self.stock_code}.SZ"
                elif self.stock_code.startswith(('5', '6')):
                    self.stock_code = f"{self.stock_code}.SH"
                elif self.stock_code.startswith(('4', '8', '920')):
                    self.stock_code = f"{self.stock_code}.BJ"
            
            # 确保日期格式正确
            if isinstance(start_date, datetime):
                start_date = start_date.date()
            if isinstance(end_date, datetime):
                end_date = end_date.date()
            
            # 优化：如果是单日数据，使用更精确的时间范围
            if start_date == end_date:
                # 单日数据，只获取当天的交易时段数据
                startdate = start_date.strftime("%Y%m%d") + "093000"  # 从9:30开始
                enddate = start_date.strftime("%Y%m%d") + "150000"    # 到15:00结束
                self.logger.info(f"[{self.stock_code}] 单日数据请求：{startdate} 到 {enddate}")
            else:
                # 多日数据，包含集合竞价阶段
                startdate = start_date.strftime("%Y%m%d") + "091500"  # 从9:15开始，包含集合竞价
                next_date = end_date + timedelta(days=1)
                enddate = next_date.strftime("%Y%m%d") + "150000"
                self.logger.info(f"[{self.stock_code}] 多日数据请求：{startdate} 到 {enddate}")
            
            # 下载历史数据
            try:
                xtdata.download_history_data(self.stock_code, 'tick', startdate, enddate)
                time.sleep(0.05)  # 减少等待时间，提高效率
            except Exception as e:
                self.logger.error(f"下载历史数据失败：{str(e)}")
                return False
            
            # 获取历史行情数据
            try:
                df = xtdata.get_market_data_ex([], [self.stock_code], period='tick', 
                                             start_time=startdate, 
                                             end_time=enddate, 
                                             count=-1)
            except Exception as e:
                self.logger.error(f"获取历史行情数据失败：{str(e)}")
                return False
            
            if self.stock_code not in df or len(df[self.stock_code]) == 0:
                self.logger.error(f"未获取到股票{self.stock_code}的数据或数据为空")
                return False
                
            self.data = pd.DataFrame(df[self.stock_code])
            
            # 按时间排序
            self.data = self.data.sort_values('time')
            
            # 转换时间并过滤交易时段
            self.data['time'] = pd.to_datetime(self.data['time'], unit='ms')
            if self.data['time'].dt.tz is None:
                self.data['datetime'] = self.data['time'].dt.tz_localize('UTC').dt.tz_convert('Asia/Shanghai')
            else:
                self.data['datetime'] = self.data['time'].dt.tz_convert('Asia/Shanghai')
            
            # 将time列替换为datetime对象
            self.data['time'] = self.data['datetime']
            
            # 设置索引为时间戳字符串格式
            self.data.index = self.data['time'].dt.strftime('%Y%m%d%H%M%S')
            
            # 如果是单日数据，过滤非交易时段的数据以提高效率
            if start_date == end_date:
                trading_mask = (
                    # 上午交易时段 9:30:00 - 11:30:00
                    ((self.data['datetime'].dt.hour == 9) & (self.data['datetime'].dt.minute >= 30)) |
                    (self.data['datetime'].dt.hour == 10) |
                    ((self.data['datetime'].dt.hour == 11) & (self.data['datetime'].dt.minute <= 30)) |
                    # 下午交易时段 13:00:00 - 15:00:00
                    ((self.data['datetime'].dt.hour >= 13) & (self.data['datetime'].dt.hour < 15))
                )
                self.data = self.data[trading_mask]
            
            # 过滤无效价格
            self.data = self.data[self.data['lastPrice'] > 0]
            
            if len(self.data) == 0:
                self.logger.error(f"清洗后数据为空：{self.stock_code}")
                return False
            
            self.logger.info(f"[{self.stock_code}] 成功加载 {len(self.data)} 条tick数据")
            return True
            
        except Exception as e:
            self.logger.error(f"加载历史数据失败：{str(e)}")
            return False
            
    def set_strategy(self, strategy):
        """设置回测策略"""
        self.strategy = strategy
        
    def run_backtest(self):
        """运行回测"""
        if self.data is None or len(self.data) == 0:
            self.logger.error("没有数据可供回测")
            return False
            
        if self.strategy is None:
            self.logger.error("未设置策略")
            return False
            
        try:
            # 遍历每一条tick数据
            for _, tick in self.data.iterrows():
                # 调用策略的on_tick方法
                self.strategy.on_tick(tick)
                
            return True
            
        except Exception as e:
            self.logger.error(f"回测执行出错：{str(e)}")
            return False

    def save_data_by_date(self):
        """按日期保存数据"""
        try:
            # 确保data目录存在
            data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'ticks')
            os.makedirs(data_dir, exist_ok=True)
            
            # 创建一个副本用于保存
            save_data = self.data.copy()
            
            # 将datetime列转换为字符串格式
            save_data['time'] = save_data['datetime'].dt.strftime('%Y-%m-%d %H:%M:%S')
            
            # 按日期分组保存
            for date, group in save_data.groupby(save_data['datetime'].dt.date):
                try:
                    # 构造文件名
                    file_name = f"{self.stock_code}_{date.strftime('%Y%m%d')}.csv"
                    file_path = os.path.join(data_dir, file_name)
                    
                    # 保存到CSV文件
                    group.to_csv(file_path, index=False)
                except Exception as e:
                    self.logger.error(f"保存{date}的数据失败：{str(e)}")
                    continue
                
        except Exception as e:
            self.logger.error(f"保存tick数据失败：{str(e)}") 