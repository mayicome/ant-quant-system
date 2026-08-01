#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
推荐值计算服务
自动计算弹性买入/卖出的推荐比例值
"""

import threading
import queue
from datetime import date, datetime, timedelta
from utils.logger import Logger
from best_sell_drop_percent_calculator import BestSellDropPercentCalculator
from best_buy_rise_percent_calculator import BestBuyRisePercentCalculator


class RecommendationService:
    """推荐值计算服务"""
    
    def __init__(self, max_concurrent_tasks=2):
        """
        初始化推荐值计算服务
        
        Args:
            max_concurrent_tasks: 最大并发计算任务数（默认2，避免卡顿）
        """
        self.logger = Logger()
        
        # 缓存推荐值：{stock_code: {'sell': {...}, 'buy': {...}}}
        # sell: {'strategy1': drop_percent, 'strategy2': drop_percent, 'last_update': date}
        # buy: {'strategy1': rise_percent, 'strategy2': rise_percent, 'last_update': date}
        self.recommendations = {}
        
        # 正在计算的股票集合，避免重复计算
        self._calculating = set()
        
        # 计算锁
        self._lock = threading.Lock()
        
        # 任务队列和并发控制
        self.max_concurrent_tasks = max_concurrent_tasks
        self._active_tasks = 0  # 当前活跃任务数
        self._task_queue = queue.Queue()  # 待处理任务队列
        self._worker_thread = None
        self._stop_worker = False
        
        # 启动工作线程
        self._start_worker()
    
    def get_sell_recommendations(self, stock_code):
        """
        获取弹性卖出推荐值
        
        Returns:
            dict: {
                'strategy1': float,  # 策略1推荐回落比例
                'strategy2': float,   # 策略2推荐回落比例
                'last_update': date,  # 最后更新时间
                'status': str         # 'calculated', 'calculating', 'error', 'not_calculated'
            } or None
        """
        with self._lock:
            if stock_code in self.recommendations:
                sell_data = self.recommendations[stock_code].get('sell')
                if sell_data:
                    return sell_data
        return None
    
    def get_buy_recommendations(self, stock_code):
        """
        获取弹性买入推荐值
        
        Returns:
            dict: {
                'strategy1': float,  # 策略1推荐反弹比例
                'strategy2': float,   # 策略2推荐反弹比例
                'last_update': date,  # 最后更新时间
                'status': str         # 'calculated', 'calculating', 'error', 'not_calculated'
            } or None
        """
        with self._lock:
            if stock_code in self.recommendations:
                buy_data = self.recommendations[stock_code].get('buy')
                if buy_data:
                    return buy_data
        return None
    
    def _start_worker(self):
        """启动工作线程处理任务队列"""
        if self._worker_thread is None or not self._worker_thread.is_alive():
            self._stop_worker = False
            self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker_thread.start()
    
    def _worker_loop(self):
        """工作线程循环，处理任务队列"""
        while not self._stop_worker:
            try:
                # 从队列获取任务，超时1秒
                task = self._task_queue.get(timeout=1)
                if task is None:  # 停止信号
                    break
                
                task_type, stock_code, days, sample_interval, callback = task
                
                # 检查是否正在计算
                with self._lock:
                    if stock_code in self._calculating:
                        self.logger.debug(f"股票 {stock_code} 的推荐值正在计算中，跳过")
                        continue
                    self._calculating.add(stock_code)
                    self._active_tasks += 1
                
                try:
                    # 检查队列中是否有同一股票的其他任务（买入或卖出）
                    # 如果有，一起处理，共享tick数据
                    pending_tasks = []
                    while True:
                        try:
                            next_task = self._task_queue.get_nowait()
                            if next_task[1] == stock_code:  # 同一股票
                                pending_tasks.append(next_task)
                            else:
                                # 不是同一股票，放回队列
                                self._task_queue.put(next_task)
                                break
                        except queue.Empty:
                            break
                    
                    # 如果同时有买入和卖出任务，先加载一次tick数据
                    has_sell = task_type == 'sell' or any(t[0] == 'sell' for t in pending_tasks)
                    has_buy = task_type == 'buy' or any(t[0] == 'buy' for t in pending_tasks)
                    
                    shared_tick_data = None
                    trading_days = None
                    if has_sell and has_buy:
                        # 同时需要买入和卖出，先加载一次tick数据
                        shared_tick_data, trading_days = self._load_tick_data_once(stock_code, days)
                        if shared_tick_data is None:
                            self.logger.warning(f"无法加载 {stock_code} 的tick数据，跳过计算")
                            # 标记所有相关任务为失败并完成
                            for pending_task in pending_tasks:
                                self._task_queue.task_done()
                            self._task_queue.task_done()
                            continue
                    
                    # 处理当前任务
                    if task_type == 'sell':
                        self._calculate_sell_recommendations(stock_code, days, sample_interval, callback, shared_tick_data, trading_days)
                    elif task_type == 'buy':
                        self._calculate_buy_recommendations(stock_code, days, sample_interval, callback, shared_tick_data, trading_days)
                    
                    # 处理待处理的任务
                    for pending_task in pending_tasks:
                        p_task_type, p_stock_code, p_days, p_sample_interval, p_callback = pending_task
                        if p_task_type == 'sell':
                            self._calculate_sell_recommendations(p_stock_code, p_days, p_sample_interval, p_callback, shared_tick_data, trading_days)
                        elif p_task_type == 'buy':
                            self._calculate_buy_recommendations(p_stock_code, p_days, p_sample_interval, p_callback, shared_tick_data, trading_days)
                        self._task_queue.task_done()
                    
                finally:
                    with self._lock:
                        self._calculating.discard(stock_code)
                        self._active_tasks -= 1
                
                self._task_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                self.logger.error(f"工作线程处理任务失败: {e}", exc_info=True)
    
    def _load_tick_data_once(self, stock_code, days):
        """加载tick数据（只加载一次，供卖出和买入计算共享）"""
        try:
            calculator = BestSellDropPercentCalculator(stock_code)
            trading_days = calculator.get_recent_trading_days(count=days)
            
            if not trading_days:
                self.logger.warning(f"无法获取 {stock_code} 的交易日")
                return None, None
            
            # 加载tick数据
            if not calculator.load_tick_data(trading_days):
                self.logger.warning(f"无法加载 {stock_code} 的tick数据")
                return None, None
            
            return calculator.tick_data, trading_days
        except Exception as e:
            self.logger.error(f"加载 {stock_code} 的tick数据失败: {e}", exc_info=True)
            return None, None
    
    def _calculate_sell_recommendations(self, stock_code, days, sample_interval, callback, shared_tick_data=None, trading_days=None):
        """实际计算弹性卖出推荐值（在工作线程中执行）"""
        try:
            calculator = BestSellDropPercentCalculator(stock_code)
            
            # 如果提供了共享的tick数据，直接使用；否则加载
            if shared_tick_data is not None and trading_days is not None:
                calculator.tick_data = shared_tick_data
            else:
                # 获取最近N个交易日
                trading_days = calculator.get_recent_trading_days(count=days)
                
                if not trading_days:
                    self.logger.warning(f"无法获取 {stock_code} 的交易日")
                    return
                
                # 加载tick数据
                if not calculator.load_tick_data(trading_days):
                    self.logger.warning(f"无法加载 {stock_code} 的tick数据")
                    return
            
            # 计算最优回落比例
            results = calculator.calculate_optimal_drop_percent(sample_interval=sample_interval)
            
            if not results:
                self.logger.warning(f"计算 {stock_code} 的卖出推荐值失败")
                return
            
            # 提取推荐值
            sorted_results = sorted(results.items(), key=lambda x: x[1]['avg_actual_profit'], reverse=True)
            strategy1_drop = sorted_results[0][0]  # 策略1：不考虑卖出率
            strategy1_data = results[strategy1_drop]
            
            # 策略2：卖出率≥75%
            target_sell_rate = 0.75
            candidates_above_75 = [(dp, r) for dp, r in results.items() if r['sell_rate'] >= target_sell_rate]
            if candidates_above_75:
                strategy2_drop, strategy2_data = max(candidates_above_75, key=lambda x: x[1]['avg_actual_profit'])
            else:
                strategy2_drop, strategy2_data = min(results.items(), key=lambda x: abs(x[1]['sell_rate'] - target_sell_rate))
            
            # 保存推荐值（包含详细信息）
            with self._lock:
                if stock_code not in self.recommendations:
                    self.recommendations[stock_code] = {}
                
                last_update_time = datetime.now()
                self.recommendations[stock_code]['sell'] = {
                    'strategy1': strategy1_drop,
                    'strategy1_sell_rate': strategy1_data.get('sell_rate', 0) * 100,  # 转换为百分比
                    'strategy1_avg_profit': strategy1_data.get('avg_actual_profit', 0) * 100,  # 转换为百分比
                    'strategy1_max_drawdown': strategy1_data.get('max_drawdown', 0),
                    'strategy2': strategy2_drop,
                    'strategy2_sell_rate': strategy2_data.get('sell_rate', 0) * 100,  # 转换为百分比
                    'strategy2_avg_profit': strategy2_data.get('avg_actual_profit', 0) * 100,  # 转换为百分比
                    'strategy2_max_drawdown': strategy2_data.get('max_drawdown', 0),
                    'last_update': last_update_time,
                    'status': 'calculated'
                }
            
            # 调用回调
            if callback:
                callback(stock_code, self.recommendations[stock_code]['sell'])
                
        except Exception as e:
            self.logger.error(f"计算 {stock_code} 的卖出推荐值失败: {e}", exc_info=True)
            with self._lock:
                if stock_code not in self.recommendations:
                    self.recommendations[stock_code] = {}
                self.recommendations[stock_code]['sell'] = {
                    'status': 'error',
                    'error': str(e)
                }
    
    def calculate_sell_recommendations_async(self, stock_code, days=5, sample_interval=20, callback=None):
        """
        异步计算弹性卖出推荐值（添加到任务队列）
        
        Args:
            stock_code: 股票代码
            days: 回测天数（默认5天）
            sample_interval: 采样间隔（默认20）
            callback: 计算完成后的回调函数 callback(stock_code, recommendations)
        """
        # 不在这里检查_calculating，让工作线程统一处理
        # 这样可以确保同一股票的买入和卖出任务能够一起处理
        
        # 添加到任务队列
        try:
            self._task_queue.put(('sell', stock_code, days, sample_interval, callback), block=False)
        except queue.Full:
            self.logger.warning(f"任务队列已满，跳过 {stock_code} 的卖出推荐值计算")
    
    def _calculate_buy_recommendations(self, stock_code, days, sample_interval, callback, shared_tick_data=None, trading_days=None):
        """实际计算弹性买入推荐值（在工作线程中执行）"""
        try:
            calculator = BestBuyRisePercentCalculator(stock_code)
            
            # 如果提供了共享的tick数据，直接使用；否则加载
            if shared_tick_data is not None and trading_days is not None:
                calculator.tick_data = shared_tick_data
            else:
                # 获取最近N个交易日
                trading_days = calculator.get_recent_trading_days(count=days)
                
                if not trading_days:
                    self.logger.warning(f"无法获取 {stock_code} 的交易日")
                    return
                
                # 加载tick数据
                if not calculator.load_tick_data(trading_days):
                    self.logger.warning(f"无法加载 {stock_code} 的tick数据")
                    return
            
            # 计算最优反弹比例
            results = calculator.calculate_optimal_rise_percent(sample_interval=sample_interval)
            
            if not results:
                self.logger.warning(f"计算 {stock_code} 的买入推荐值失败")
                return
            
            # 提取推荐值
            sorted_results = sorted(results.items(), key=lambda x: x[1]['avg_actual_saving'], reverse=True)
            strategy1_rise = sorted_results[0][0]  # 策略1：不考虑买入率
            strategy1_data = results[strategy1_rise]
            
            # 策略2：买入率≥75%
            target_buy_rate = 0.75
            candidates_above_75 = [(rp, r) for rp, r in results.items() if r['buy_rate'] >= target_buy_rate]
            if candidates_above_75:
                strategy2_rise, strategy2_data = max(candidates_above_75, key=lambda x: x[1]['avg_actual_saving'])
            else:
                strategy2_rise, strategy2_data = min(results.items(), key=lambda x: abs(x[1]['buy_rate'] - target_buy_rate))
            
            # 保存推荐值（包含详细信息）
            with self._lock:
                if stock_code not in self.recommendations:
                    self.recommendations[stock_code] = {}
                
                last_update_time = datetime.now()
                self.recommendations[stock_code]['buy'] = {
                    'strategy1': strategy1_rise,
                    'strategy1_buy_rate': strategy1_data.get('buy_rate', 0) * 100,  # 转换为百分比
                    'strategy1_avg_saving': strategy1_data.get('avg_actual_saving', 0) * 100,  # 转换为百分比
                    'strategy1_max_drawdown': strategy1_data.get('max_drawdown', 0),
                    'strategy2': strategy2_rise,
                    'strategy2_buy_rate': strategy2_data.get('buy_rate', 0) * 100,  # 转换为百分比
                    'strategy2_avg_saving': strategy2_data.get('avg_actual_saving', 0) * 100,  # 转换为百分比
                    'strategy2_max_drawdown': strategy2_data.get('max_drawdown', 0),
                    'last_update': last_update_time,
                    'status': 'calculated'
                }
            
            # 调用回调
            if callback:
                callback(stock_code, self.recommendations[stock_code]['buy'])
                
        except Exception as e:
            self.logger.error(f"计算 {stock_code} 的买入推荐值失败: {e}", exc_info=True)
            with self._lock:
                if stock_code not in self.recommendations:
                    self.recommendations[stock_code] = {}
                self.recommendations[stock_code]['buy'] = {
                    'status': 'error',
                    'error': str(e)
                }
    
    def calculate_buy_recommendations_async(self, stock_code, days=5, sample_interval=20, callback=None):
        """
        异步计算弹性买入推荐值（添加到任务队列）
        
        Args:
            stock_code: 股票代码
            days: 回测天数（默认5天）
            sample_interval: 采样间隔（默认20）
            callback: 计算完成后的回调函数 callback(stock_code, recommendations)
        """
        # 不在这里检查_calculating，让工作线程统一处理
        # 这样可以确保同一股票的买入和卖出任务能够一起处理
        
        # 添加到任务队列
        try:
            self._task_queue.put(('buy', stock_code, days, sample_interval, callback), block=False)
        except queue.Full:
            self.logger.warning(f"任务队列已满，跳过 {stock_code} 的买入推荐值计算")
    
    def is_calculating(self, stock_code):
        """检查是否正在计算"""
        with self._lock:
            return stock_code in self._calculating
    
    def clear_cache(self, stock_code=None):
        """清除缓存"""
        with self._lock:
            if stock_code:
                if stock_code in self.recommendations:
                    del self.recommendations[stock_code]
            else:
                self.recommendations.clear()


# 全局单例
_recommendation_service = None

def get_recommendation_service():
    """获取推荐值计算服务单例"""
    global _recommendation_service
    if _recommendation_service is None:
        _recommendation_service = RecommendationService()
    return _recommendation_service

