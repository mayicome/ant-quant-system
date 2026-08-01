#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
弹性卖出回落比例计算器
针对一只股票前5个交易日的历史tick数据，回测不同回落比例的收益情况
"""

import sys
import os
from datetime import datetime, date, timedelta
import pandas as pd
import numpy as np
from utils.trading_day import is_tradeday
from utils.recommendation_tick_loader import load_ticks_for_trading_days

# 添加项目根目录到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)


class BestSellDropPercentCalculator:
    """弹性卖出回落比例计算器"""
    
    def __init__(self, stock_code, buy_price=None, initial_volume=1000):
        """
        初始化计算器
        
        Args:
            stock_code: 股票代码（如 '000001' 或 '000001.SZ'）
            buy_price: 买入价格，如果为None，则使用第一个交易日的开盘价
            initial_volume: 初始持仓数量（股）
        """
        self.stock_code = self._format_stock_code(stock_code)
        self.buy_price = buy_price
        self.initial_volume = initial_volume
        self.tick_data = {}  # 存储每个交易日的tick数据
        
    def _format_stock_code(self, code):
        """格式化股票代码为QMT格式"""
        if code.endswith(('.SH', '.SZ', '.BJ')):
            return code
        
        code_str = str(code).zfill(6)
        if code_str.startswith(('0', '1', '3')):
            return f"{code_str}.SZ"
        elif code_str.startswith(('5', '6')):
            return f"{code_str}.SH"
        elif code_str.startswith(('4', '8', '920')):
            return f"{code_str}.BJ"
        else:
            return f"{code_str}.SH"  # 默认沪市
    
    def get_recent_trading_days(self, count=5, base_date=None):
        """获取最近N个交易日"""
        if base_date is None:
            base_date = date.today()
        
        trading_days = []
        current_date = base_date
        
        # 往前查找交易日
        while len(trading_days) < count:
            if is_tradeday(current_date):
                trading_days.append(current_date)
            current_date -= timedelta(days=1)
            
            # 防止无限循环
            if (base_date - current_date).days > 365:
                break
        
        # 按日期正序排列（最早的在前）
        trading_days.sort()
        return trading_days
    
    def load_tick_data(self, trading_days):
        """加载指定交易日的tick数据（本地 data/ticks 优先，缺则大 QMT 按需同步；不用 miniQMT）。"""
        tick_data, _msgs = load_ticks_for_trading_days(
            self.stock_code,
            trading_days,
            allow_xtdata_fallback=False,
            allow_on_demand=True,
        )
        self.tick_data = tick_data
        return len(self.tick_data) > 0
    
    def simulate_best_sell_from_trigger(self, tick_data, start_index, drop_percent):
        """
        从指定tick点开始模拟弹性卖出策略
        以该tick的价格作为触发价格，从该tick开始模拟弹性卖出
        
        弹性卖出策略逻辑：
        1. 以指定tick的价格作为触发价格（假设在这个价格设置弹性卖出）
        2. 从这个tick开始追踪最高价
        3. 在后续tick中，如果价格从最高价回落到目标比例，就卖出
        
        例如：触发价格=10元（第1个tick的价格），回落比例=0.3%
        - 从10元开始追踪，最高价初始=10元
        - 价格继续涨到10.50元（最高价）
        - 价格回落到10.50 * (1 - 0.3%) = 10.47元时，触发卖出
        
        Args:
            tick_data: tick数据DataFrame
            start_index: 开始模拟的tick索引（以该tick的价格作为触发价格）
            drop_percent: 回落百分比（如0.3表示0.3%，即从最高价回落0.3%时卖出）
        
        Returns:
            dict: {
                'trigger_price': float,  # 触发价格（该tick的价格）
                'trigger_time': datetime,  # 触发时间（该tick的时间）
                'sell_price': float,  # 卖出价格（如果卖出）
                'sell_time': datetime,  # 卖出时间（如果卖出）
                'highest_price': float,  # 最高价
                'max_profit': float,  # 最大收益（最高价时的收益率，%）
                'actual_profit': float,  # 实际收益（卖出时的收益率，%，如果未卖出则为0）
                'sold': bool,  # 是否卖出
            }
        """
        if start_index >= len(tick_data):
            return None
        
        # 获取触发价格和时间（该tick的价格）
        trigger_tick = tick_data.iloc[start_index]
        trigger_price = trigger_tick['lastPrice']
        trigger_time = trigger_tick['datetime']
        
        # 从触发点开始追踪
        highest_price = trigger_price
        sell_price = None
        sell_time = None
        sold = False
        
        # 从触发点之后开始遍历
        for i in range(start_index + 1, len(tick_data)):
            tick = tick_data.iloc[i]
            current_price = tick['lastPrice']
            current_time = tick['datetime']
            
            # 更新最高价
            if current_price > highest_price:
                highest_price = current_price
            
            # 检查是否满足回落条件（最高价必须大于触发价格才有意义）
            if highest_price > trigger_price:
                # 计算回落目标价：最高价 * (1 - 回落百分比/100)
                target_price = highest_price * (1 - drop_percent / 100.0)
                
                # 只有当价格从最高价回落到目标价时才触发卖出
                if current_price <= target_price and current_price < highest_price:
                    sell_price = current_price
                    sell_time = current_time
                    sold = True
                    break  # 找到卖出点，退出循环
        
        # 计算收益
        max_profit = (highest_price - trigger_price) / trigger_price * 100 if highest_price > trigger_price else 0
        actual_profit = (sell_price - trigger_price) / trigger_price * 100 if sold and sell_price else 0
        
        return {
            'trigger_price': trigger_price,
            'trigger_time': trigger_time,
            'sell_price': sell_price,
            'sell_time': sell_time,
            'highest_price': highest_price,
            'max_profit': max_profit,
            'actual_profit': actual_profit,
            'sold': sold,
        }
    
    def simulate_all_drop_percents_from_trigger(self, tick_data, start_index, drop_percent_range):
        """
        优化版本：从指定tick点开始，一次性计算所有回落比例的结果
        只遍历一次tick数据，避免重复计算
        
        Args:
            tick_data: tick数据DataFrame
            start_index: 开始模拟的tick索引
            drop_percent_range: 回落比例列表
        
        Returns:
            dict: {
                'trigger_price': float,
                'trigger_time': datetime,
                'results_by_drop_percent': {
                    drop_percent: {
                        'sell_price': float,
                        'sell_time': datetime,
                        'highest_price': float,
                        'max_profit': float,
                        'actual_profit': float,
                        'sold': bool,
                    }
                }
            }
        """
        if start_index >= len(tick_data):
            return None
        
        # 重置索引以确保iloc能正常工作，并提取价格和时间数组（提高性能）
        tick_data_reset = tick_data.reset_index(drop=True)
        prices = tick_data_reset['lastPrice'].values
        times = tick_data_reset['datetime'].values
        
        # 获取触发价格和时间
        trigger_price = prices[start_index]
        trigger_time = times[start_index]
        
        # 初始化结果字典
        results_by_drop_percent = {}
        for drop_percent in drop_percent_range:
            results_by_drop_percent[drop_percent] = {
                'sell_price': None,
                'sell_time': None,
                'highest_price': trigger_price,
                'max_profit': 0,
                'actual_profit': 0,
                'sold': False,
            }
        
        # 从触发点开始追踪
        highest_price = trigger_price
        
        # 记录每个回落比例是否已卖出（避免重复计算）
        sold_flags = {dp: False for dp in drop_percent_range}
        
        # 从触发点之后开始遍历（只遍历一次）
        for i in range(start_index + 1, len(prices)):
            current_price = prices[i]
            current_time = times[i]
            
            # 更新最高价
            if current_price > highest_price:
                highest_price = current_price
            
            # 只有当最高价大于触发价格时，才检查卖出条件
            if highest_price > trigger_price:
                # 对所有未卖出的回落比例，检查是否满足卖出条件
                for drop_percent in drop_percent_range:
                    if sold_flags[drop_percent]:
                        continue  # 已经卖出，跳过
                    
                    # 计算回落目标价
                    target_price = highest_price * (1 - drop_percent / 100.0)
                    
                    # 检查是否满足卖出条件
                    if current_price <= target_price and current_price < highest_price:
                        result = results_by_drop_percent[drop_percent]
                        result['sell_price'] = current_price
                        result['sell_time'] = current_time
                        result['highest_price'] = highest_price
                        result['sold'] = True
                        sold_flags[drop_percent] = True
                
                # 更新所有未卖出结果的最大收益和最高价
                for drop_percent in drop_percent_range:
                    if not sold_flags[drop_percent]:
                        result = results_by_drop_percent[drop_percent]
                        result['highest_price'] = highest_price
        
        # 计算所有结果的收益
        for drop_percent in drop_percent_range:
            result = results_by_drop_percent[drop_percent]
            result['max_profit'] = (result['highest_price'] - trigger_price) / trigger_price * 100 if result['highest_price'] > trigger_price else 0
            if result['sold']:
                result['actual_profit'] = (result['sell_price'] - trigger_price) / trigger_price * 100
        
        return {
            'trigger_price': trigger_price,
            'trigger_time': trigger_time,
            'results_by_drop_percent': results_by_drop_percent,
        }
    
    def calculate_optimal_drop_percent(self, drop_percent_range=None, sample_interval=10):
        """
        计算最优回落比例
        对每个tick点，以该tick的价格作为触发价格，从该tick开始模拟弹性卖出
        不同的回落比例会在不同的时刻触发卖出，产生不同的收益
        
        Args:
            drop_percent_range: 回落比例范围，如 [0.1, 0.2, 0.3, ..., 1.0]
            sample_interval: 采样间隔（每隔N个tick点采样一次，减少计算量，默认10）
        
        Returns:
            dict: 包含每个回落比例的统计结果
        """
        if not self.tick_data:
            print("错误：没有加载tick数据")
            return None
        
        # 确定回落比例范围
        if drop_percent_range is None:
            # 默认范围：0.0% 到 3.0%，步长0.1%（包含0%用于对比最大收益）
            drop_percent_range = [round(x * 0.1, 1) for x in range(0, 31)]
        
        print(f"\n开始回测（对每个tick点，以该tick的价格作为触发价格，模拟弹性卖出）")
        print(f"采样间隔: 每隔 {sample_interval} 个tick点采样一次")
        print(f"测试回落比例范围: {drop_percent_range[0]:.1f}% ~ {drop_percent_range[-1]:.1f}%")
        print(f"共 {len(drop_percent_range)} 个回落比例")
        print("优化：对每个采样点，一次性计算所有回落比例的结果，避免重复遍历tick数据\n")
        
        # 初始化结果字典
        results = {dp: {
            'drop_percent': dp,
            'all_results': [],
            'total_actual_profit': 0,
            'max_drawdown': 0,    # 最大回撤
            'sold_count': 0,
            'total_samples': 0,
        } for dp in drop_percent_range}
        
        # 对每个交易日进行回测
        total_samples_processed = 0
        for trading_day, tick_data in sorted(self.tick_data.items()):
            # 对每个采样点进行回测（每隔sample_interval个tick点采样一次）
            sample_indices = list(range(0, len(tick_data), sample_interval))
            total_samples_processed += len(sample_indices)
            
            for start_index in sample_indices:
                # 一次性计算所有回落比例的结果（优化：只遍历一次tick数据）
                combined_result = self.simulate_all_drop_percents_from_trigger(
                    tick_data, start_index, drop_percent_range
                )
                
                if combined_result:
                    trigger_price = combined_result['trigger_price']
                    trigger_time = combined_result['trigger_time']
                    results_by_dp = combined_result['results_by_drop_percent']
                    
                    # 对每个回落比例，记录结果
                    for drop_percent in drop_percent_range:
                        result = results_by_dp[drop_percent]
                        results[drop_percent]['total_samples'] += 1
                        
                        # 计算回撤：相对于触发价格（指定价）的损失
                        # 第一，如果没有卖出，回撤为0
                        # 第二，如果卖出：
                        #   - 如果实际卖出价 >= 触发价格，回撤为0（没有亏损）
                        #   - 如果实际卖出价 < 触发价格，回撤 = (触发价格 - 实际卖出价) / 触发价格 * 100
                        if not result['sold']:
                            drawdown = 0
                        else:
                            sell_price = result['sell_price']
                            if sell_price >= trigger_price:
                                drawdown = 0
                            else:
                                drawdown = (trigger_price - sell_price) / trigger_price * 100
                        
                        # 记录详细结果
                        results[drop_percent]['all_results'].append({
                            'date': trading_day,
                            'trigger_price': trigger_price,
                            'trigger_time': trigger_time,
                            'sell_price': result['sell_price'],
                            'sell_time': result['sell_time'],
                            'highest_price': result['highest_price'],
                            'max_profit': result['max_profit'],
                            'actual_profit': result['actual_profit'],
                            'drawdown': drawdown,  # 回撤
                            'sold': result['sold'],
                        })
                        
                        if result['sold']:
                            results[drop_percent]['sold_count'] += 1
                            results[drop_percent]['total_actual_profit'] += result['actual_profit']
                        
                        # 统计最大回撤（只有当回撤 > 0 时才更新最大回撤）
                        if drawdown > 0:
                            results[drop_percent]['max_drawdown'] = max(results[drop_percent].get('max_drawdown', 0), drawdown)
        
        # 计算每个回落比例的统计结果
        print(f"共处理 {total_samples_processed} 个采样点\n")
        for drop_percent in drop_percent_range:
            print(f"测试回落比例: {drop_percent:.1f}%", end=" ... ")
            
            result = results[drop_percent]
            avg_actual_profit = result['total_actual_profit'] / result['sold_count'] if result['sold_count'] > 0 else 0
            sell_rate = result['sold_count'] / result['total_samples'] if result['total_samples'] > 0 else 0
            
            # 计算最大回撤
            max_drawdown = result.get('max_drawdown', 0)
            
            result['sell_rate'] = sell_rate
            result['avg_actual_profit'] = avg_actual_profit
            result['max_drawdown'] = max_drawdown
            
            print(f"卖出率: {sell_rate*100:.1f}% ({result['sold_count']}/{result['total_samples']}), "
                  f"平均实际收益: {avg_actual_profit:.2f}%, 最大回撤: {max_drawdown:.2f}%")
        
        return results
    
    def print_results(self, results):
        """打印结果"""
        if not results:
            print("没有结果可显示")
            return
        
        print("\n" + "="*80)
        print("回测结果汇总")
        print("="*80)
        print("说明：平均实际收益仅统计成功卖出的情况（按卖出数量加权平均）")
        print("-"*80)
        print(f"{'回落比例':<10} {'卖出率':<12} {'平均实际收益':<15} {'最大回撤':<15} {'采样点数':<10}")
        print("-"*80)
        
        # 按平均实际收益排序
        sorted_results = sorted(results.items(), key=lambda x: x[1]['avg_actual_profit'], reverse=True)
        
        for drop_percent, result in sorted_results:
            print(f"{drop_percent:>6.1f}%   {result['sell_rate']*100:>8.1f}% ({result['sold_count']}/{result['total_samples']})   "
                  f"{result['avg_actual_profit']:>12.2f}%   {result['max_drawdown']:>12.2f}%   "
                  f"{result['total_samples']:>8}")
        
        # 找出最优回落比例（不考虑卖出率，只看平均实际收益）
        best_drop_percent = sorted_results[0][0]
        best_result = sorted_results[0][1]
        
        # 找出卖出率≥75%的最优回落比例（用于希望尽可能卖出的情况）
        target_sell_rate = 0.75  # 目标卖出率75%
        
        # 直接找卖出率≥75%的，从中选平均收益最高的
        candidates_above_75 = [(dp, r) for dp, r in results.items() if r['sell_rate'] >= target_sell_rate]
        if candidates_above_75:
            # 从卖出率≥75%的中选平均收益最高的
            best_high_sell_rate = max(candidates_above_75, key=lambda x: x[1]['avg_actual_profit'])
            best_high_sell_rate_percent = best_high_sell_rate[0]
            best_high_sell_rate_result = best_high_sell_rate[1]
        else:
            # 如果没有≥75%的，找卖出率最接近75%的（作为备选）
            best_high_sell_rate = min(results.items(), key=lambda x: abs(x[1]['sell_rate'] - target_sell_rate))
            best_high_sell_rate_percent = best_high_sell_rate[0]
            best_high_sell_rate_result = best_high_sell_rate[1]
        
        print("\n" + "="*80)
        print("推荐回落比例（两种策略）")
        print("="*80)
        print("【策略1】不考虑卖出率，追求最高平均实际收益：")
        print(f"  推荐回落比例: {best_drop_percent:.1f}%")
        print(f"  - 卖出率: {best_result['sell_rate']*100:.1f}% ({best_result['sold_count']}/{best_result['total_samples']})")
        print(f"  - 平均实际收益: {best_result['avg_actual_profit']:.2f}% (仅统计成功卖出的{best_result['sold_count']}个采样点)")
        print(f"  - 最大回撤: {best_result['max_drawdown']:.2f}%")
        print()
        print("【策略2】希望尽可能卖出（卖出率大于等于75%），追求最高平均实际收益：")
        print(f"  推荐回落比例: {best_high_sell_rate_percent:.1f}%")
        print(f"  - 卖出率: {best_high_sell_rate_result['sell_rate']*100:.1f}% ({best_high_sell_rate_result['sold_count']}/{best_high_sell_rate_result['total_samples']})")
        print(f"  - 平均实际收益: {best_high_sell_rate_result['avg_actual_profit']:.2f}% (仅统计成功卖出的{best_high_sell_rate_result['sold_count']}个采样点)")
        print(f"  - 最大回撤: {best_high_sell_rate_result['max_drawdown']:.2f}%")
        print("="*80)
        
        # 显示统计信息（按日期分组）- 显示策略1的统计
        print(f"\n策略1最优回落比例 ({best_drop_percent:.1f}%) 的每日统计:")
        print("-"*80)
        
        # 按日期分组统计
        from collections import defaultdict
        day_stats = defaultdict(lambda: {'count': 0, 'sold': 0, 'total_profit': 0, 'max_drawdown': 0})
        
        for result in best_result['all_results']:
            day = result['date']
            day_stats[day]['count'] += 1
            if result['sold']:
                day_stats[day]['sold'] += 1
                day_stats[day]['total_profit'] += result['actual_profit']
            # 统计最大回撤
            if result.get('drawdown', 0) > 0:
                day_stats[day]['max_drawdown'] = max(day_stats[day]['max_drawdown'], result['drawdown'])
        
        print(f"{'日期':<12} {'采样点数':<10} {'卖出数':<10} {'卖出率':<10} {'平均实际收益':<15} {'最大回撤':<15}")
        print("-"*80)
        
        for day in sorted(day_stats.keys()):
            stats = day_stats[day]
            sell_rate = stats['sold'] / stats['count'] * 100 if stats['count'] > 0 else 0
            avg_profit = stats['total_profit'] / stats['sold'] if stats['sold'] > 0 else 0
            max_drawdown = stats['max_drawdown']
            print(f"{day.strftime('%Y-%m-%d'):<12} {stats['count']:>8}   {stats['sold']:>8}   "
                  f"{sell_rate:>8.1f}%   {avg_profit:>12.2f}%   {max_drawdown:>12.2f}%")


def main():
    """主函数"""
    print("="*80)
    print("弹性卖出回落比例计算器")
    print("="*80)
    
    # 获取用户输入
    stock_code = input("\n请输入股票代码（如 000001 或 000001.SZ）: ").strip()
    if not stock_code:
        print("错误：股票代码不能为空")
        return
    
    days_input = input("请输入回测交易日数量（默认5天）: ").strip()
    days = int(days_input) if days_input else 5
    
    sample_input = input("请输入采样间隔（每隔N个tick点采样一次，默认20，越小越精确但计算越慢）: ").strip()
    sample_interval = int(sample_input) if sample_input else 20
    
    # 创建计算器（不再需要buy_price参数，因为每个tick点都会作为触发价格）
    calculator = BestSellDropPercentCalculator(stock_code)
    
    # 获取最近N个交易日
    trading_days = calculator.get_recent_trading_days(count=days)
    print(f"\n找到 {len(trading_days)} 个交易日:")
    for day in trading_days:
        print(f"  - {day.strftime('%Y-%m-%d')}")
    
    # 加载tick数据
    if not calculator.load_tick_data(trading_days):
        print("错误：无法加载tick数据")
        return
    
    # 计算最优回落比例
    results = calculator.calculate_optimal_drop_percent(sample_interval=sample_interval)
    
    if results:
        # 打印结果
        calculator.print_results(results)
    else:
        print("错误：回测失败")


if __name__ == '__main__':
    main()

