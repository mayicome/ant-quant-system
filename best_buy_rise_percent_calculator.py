#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
弹性买入反弹比例计算器
针对一只股票前N个交易日的历史tick数据，回测不同反弹比例的买入成本情况
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


class BestBuyRisePercentCalculator:
    """弹性买入反弹比例计算器"""
    
    def __init__(self, stock_code, initial_volume=1000):
        """
        初始化计算器
        
        Args:
            stock_code: 股票代码（如 '000001' 或 '000001.SZ'）
            initial_volume: 初始持仓数量（股）
        """
        self.stock_code = self._format_stock_code(stock_code)
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
    
    def simulate_all_rise_percents_from_trigger(self, tick_data, start_index, rise_percent_range):
        """
        优化版本：从指定tick点开始，一次性计算所有反弹比例的结果
        只遍历一次tick数据，避免重复计算
        
        弹性买入策略逻辑：
        1. 以指定tick的价格作为触发价格（假设在这个价格设置弹性买入）
        2. 从这个tick开始追踪最低价
        3. 在后续tick中，如果价格从最低价反弹到目标比例，就买入
        
        Args:
            tick_data: tick数据DataFrame
            start_index: 开始模拟的tick索引
            rise_percent_range: 反弹比例列表
        
        Returns:
            dict: {
                'trigger_price': float,
                'trigger_time': datetime,
                'results_by_rise_percent': {
                    rise_percent: {
                        'buy_price': float,
                        'buy_time': datetime,
                        'lowest_price': float,
                        'max_saving': float,  # 最大节省（最低价时的节省比例，%）
                        'actual_saving': float,  # 实际节省（买入时的节省比例，%，如果未买入则为0）
                        'bought': bool,
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
        results_by_rise_percent = {}
        for rise_percent in rise_percent_range:
            results_by_rise_percent[rise_percent] = {
                'buy_price': None,
                'buy_time': None,
                'lowest_price': trigger_price,
                'max_saving': 0,
                'actual_saving': 0,
                'bought': False,
            }
        
        # 从触发点开始追踪
        lowest_price = trigger_price
        
        # 记录每个反弹比例是否已买入（避免重复计算）
        bought_flags = {rp: False for rp in rise_percent_range}
        
        # 从触发点之后开始遍历（只遍历一次）
        for i in range(start_index + 1, len(prices)):
            current_price = prices[i]
            current_time = times[i]
            
            # 更新最低价
            if current_price < lowest_price:
                lowest_price = current_price
            
            # 只有当最低价小于触发价格时，才检查买入条件
            if lowest_price < trigger_price:
                # 对所有未买入的反弹比例，检查是否满足买入条件
                for rise_percent in rise_percent_range:
                    if bought_flags[rise_percent]:
                        continue  # 已经买入，跳过
                    
                    # 计算反弹目标价：最低价 * (1 + 反弹百分比/100)
                    target_price = lowest_price * (1 + rise_percent / 100.0)
                    
                    # 检查是否满足买入条件
                    if current_price >= target_price and current_price > lowest_price:
                        result = results_by_rise_percent[rise_percent]
                        result['buy_price'] = current_price
                        result['buy_time'] = current_time
                        result['lowest_price'] = lowest_price
                        result['bought'] = True
                        bought_flags[rise_percent] = True
                
                # 更新所有未买入结果的最低价
                for rise_percent in rise_percent_range:
                    if not bought_flags[rise_percent]:
                        result = results_by_rise_percent[rise_percent]
                        result['lowest_price'] = lowest_price
        
        # 计算所有结果的节省
        for rise_percent in rise_percent_range:
            result = results_by_rise_percent[rise_percent]
            # 最大节省：如果能在最低价买入，相比触发价格节省的比例
            result['max_saving'] = (trigger_price - result['lowest_price']) / trigger_price * 100 if result['lowest_price'] < trigger_price else 0
            if result['bought']:
                # 实际节省：实际买入价相比触发价格节省的比例（如果买入价高于触发价格，则为负值，表示多付了）
                result['actual_saving'] = (trigger_price - result['buy_price']) / trigger_price * 100
        
        return {
            'trigger_price': trigger_price,
            'trigger_time': trigger_time,
            'results_by_rise_percent': results_by_rise_percent,
        }
    
    def calculate_optimal_rise_percent(self, rise_percent_range=None, sample_interval=20):
        """
        计算最优反弹比例
        对每个tick点，以该tick的价格作为触发价格，从该tick开始模拟弹性买入
        不同的反弹比例会在不同的时刻触发买入，产生不同的成本
        
        Args:
            rise_percent_range: 反弹比例范围，如 [0.1, 0.2, 0.3, ..., 1.0]
            sample_interval: 采样间隔（每隔N个tick点采样一次，减少计算量，默认20）
        
        Returns:
            dict: 包含每个反弹比例的统计结果
        """
        if not self.tick_data:
            print("错误：没有加载tick数据")
            return None
        
        # 确定反弹比例范围
        if rise_percent_range is None:
            # 默认范围：0.0% 到 3.0%，步长0.1%（包含0%用于对比最大节省）
            rise_percent_range = [round(x * 0.1, 1) for x in range(0, 31)]
        
        print(f"\n开始回测（对每个tick点，以该tick的价格作为触发价格，模拟弹性买入）")
        print(f"采样间隔: 每隔 {sample_interval} 个tick点采样一次")
        print(f"测试反弹比例范围: {rise_percent_range[0]:.1f}% ~ {rise_percent_range[-1]:.1f}%")
        print(f"共 {len(rise_percent_range)} 个反弹比例")
        print("优化：对每个采样点，一次性计算所有反弹比例的结果，避免重复遍历tick数据\n")
        
        # 初始化结果字典
        results = {rp: {
            'rise_percent': rp,
            'all_results': [],
            'total_actual_saving': 0,
            'max_drawdown': 0,    # 最大回撤（买入价高于触发价格的最大比例）
            'bought_count': 0,
            'total_samples': 0,
        } for rp in rise_percent_range}
        
        # 对每个交易日进行回测
        total_samples_processed = 0
        for trading_day, tick_data in sorted(self.tick_data.items()):
            # 对每个采样点进行回测（每隔sample_interval个tick点采样一次）
            sample_indices = list(range(0, len(tick_data), sample_interval))
            total_samples_processed += len(sample_indices)
            
            for start_index in sample_indices:
                # 一次性计算所有反弹比例的结果（优化：只遍历一次tick数据）
                combined_result = self.simulate_all_rise_percents_from_trigger(
                    tick_data, start_index, rise_percent_range
                )
                
                if combined_result:
                    trigger_price = combined_result['trigger_price']
                    trigger_time = combined_result['trigger_time']
                    results_by_rp = combined_result['results_by_rise_percent']
                    
                    # 对每个反弹比例，记录结果
                    for rise_percent in rise_percent_range:
                        result = results_by_rp[rise_percent]
                        results[rise_percent]['total_samples'] += 1
                        
                        # 计算回撤：相对于触发价格（指定价）的损失
                        # 第一，如果没有买入，回撤为0
                        # 第二，如果买入：
                        #   - 如果实际买入价 <= 触发价格，回撤为0（没有多付）
                        #   - 如果实际买入价 > 触发价格，回撤 = (实际买入价 - 触发价格) / 触发价格 * 100
                        if not result['bought']:
                            drawdown = 0
                        else:
                            buy_price = result['buy_price']
                            if buy_price <= trigger_price:
                                drawdown = 0
                            else:
                                drawdown = (buy_price - trigger_price) / trigger_price * 100
                        
                        # 记录详细结果
                        results[rise_percent]['all_results'].append({
                            'date': trading_day,
                            'trigger_price': trigger_price,
                            'trigger_time': trigger_time,
                            'buy_price': result['buy_price'],
                            'buy_time': result['buy_time'],
                            'lowest_price': result['lowest_price'],
                            'max_saving': result['max_saving'],
                            'actual_saving': result['actual_saving'],
                            'drawdown': drawdown,  # 回撤
                            'bought': result['bought'],
                        })
                        
                        if result['bought']:
                            results[rise_percent]['bought_count'] += 1
                            results[rise_percent]['total_actual_saving'] += result['actual_saving']
                        
                        # 统计最大回撤（只有当回撤 > 0 时才更新最大回撤）
                        if drawdown > 0:
                            results[rise_percent]['max_drawdown'] = max(results[rise_percent].get('max_drawdown', 0), drawdown)
        
        # 计算每个反弹比例的统计结果
        print(f"共处理 {total_samples_processed} 个采样点\n")
        for rise_percent in rise_percent_range:
            print(f"测试反弹比例: {rise_percent:.1f}%", end=" ... ")
            
            result = results[rise_percent]
            avg_actual_saving = result['total_actual_saving'] / result['bought_count'] if result['bought_count'] > 0 else 0
            buy_rate = result['bought_count'] / result['total_samples'] if result['total_samples'] > 0 else 0
            
            # 计算最大回撤
            max_drawdown = result.get('max_drawdown', 0)
            
            result['buy_rate'] = buy_rate
            result['avg_actual_saving'] = avg_actual_saving
            result['max_drawdown'] = max_drawdown
            
            print(f"买入率: {buy_rate*100:.1f}% ({result['bought_count']}/{result['total_samples']}), "
                  f"平均实际节省: {avg_actual_saving:.2f}%, 最大回撤: {max_drawdown:.2f}%")
        
        return results
    
    def print_results(self, results):
        """打印结果"""
        if not results:
            print("没有结果可显示")
            return
        
        print("\n" + "="*80)
        print("回测结果汇总")
        print("="*80)
        print("说明：平均实际节省仅统计成功买入的情况（按买入数量加权平均）")
        print("-"*80)
        print(f"{'反弹比例':<10} {'买入率':<12} {'平均实际节省':<15} {'最大回撤':<15} {'采样点数':<10}")
        print("-"*80)
        
        # 按平均实际节省排序（节省越多越好，所以降序）
        sorted_results = sorted(results.items(), key=lambda x: x[1]['avg_actual_saving'], reverse=True)
        
        for rise_percent, result in sorted_results:
            print(f"{rise_percent:>6.1f}%   {result['buy_rate']*100:>8.1f}% ({result['bought_count']}/{result['total_samples']})   "
                  f"{result['avg_actual_saving']:>12.2f}%   {result['max_drawdown']:>12.2f}%   "
                  f"{result['total_samples']:>8}")
        
        # 找出最优反弹比例
        best_rise_percent = sorted_results[0][0]
        best_result = sorted_results[0][1]
        
        # 找出买入率≥75%的最优反弹比例（用于希望尽可能买入的情况）
        target_buy_rate = 0.75  # 目标买入率75%
        
        # 直接找买入率≥75%的，从中选平均节省最高的
        candidates_above_75 = [(rp, r) for rp, r in results.items() if r['buy_rate'] >= target_buy_rate]
        if candidates_above_75:
            # 从买入率≥75%的中选平均节省最高的
            best_high_buy_rate = max(candidates_above_75, key=lambda x: x[1]['avg_actual_saving'])
            best_high_buy_rate_percent = best_high_buy_rate[0]
            best_high_buy_rate_result = best_high_buy_rate[1]
        else:
            # 如果没有≥75%的，找买入率最接近75%的（作为备选）
            best_high_buy_rate = min(results.items(), key=lambda x: abs(x[1]['buy_rate'] - target_buy_rate))
            best_high_buy_rate_percent = best_high_buy_rate[0]
            best_high_buy_rate_result = best_high_buy_rate[1]
        
        print("\n" + "="*80)
        print("推荐反弹比例（两种策略）")
        print("="*80)
        print("【策略1】不考虑买入率，追求最高平均实际节省：")
        print(f"  推荐反弹比例: {best_rise_percent:.1f}%")
        print(f"  - 买入率: {best_result['buy_rate']*100:.1f}% ({best_result['bought_count']}/{best_result['total_samples']})")
        print(f"  - 平均实际节省: {best_result['avg_actual_saving']:.2f}% (仅统计成功买入的{best_result['bought_count']}个采样点)")
        print(f"  - 最大回撤: {best_result['max_drawdown']:.2f}%")
        print()
        print("【策略2】希望尽可能买入（买入率大于等于75%），追求最高平均实际节省：")
        print(f"  推荐反弹比例: {best_high_buy_rate_percent:.1f}%")
        print(f"  - 买入率: {best_high_buy_rate_result['buy_rate']*100:.1f}% ({best_high_buy_rate_result['bought_count']}/{best_high_buy_rate_result['total_samples']})")
        print(f"  - 平均实际节省: {best_high_buy_rate_result['avg_actual_saving']:.2f}% (仅统计成功买入的{best_high_buy_rate_result['bought_count']}个采样点)")
        print(f"  - 最大回撤: {best_high_buy_rate_result['max_drawdown']:.2f}%")
        print("="*80)
        
        # 显示统计信息（按日期分组）- 显示策略1的统计
        print(f"\n策略1最优反弹比例 ({best_rise_percent:.1f}%) 的每日统计:")
        print("-"*80)
        
        # 按日期分组统计
        from collections import defaultdict
        day_stats = defaultdict(lambda: {'count': 0, 'bought': 0, 'total_saving': 0, 'max_drawdown': 0})
        
        for result in best_result['all_results']:
            day = result['date']
            day_stats[day]['count'] += 1
            if result['bought']:
                day_stats[day]['bought'] += 1
                day_stats[day]['total_saving'] += result['actual_saving']
            # 统计最大回撤
            if result.get('drawdown', 0) > 0:
                day_stats[day]['max_drawdown'] = max(day_stats[day]['max_drawdown'], result['drawdown'])
        
        print(f"{'日期':<12} {'采样点数':<10} {'买入数':<10} {'买入率':<10} {'平均实际节省':<15} {'最大回撤':<15}")
        print("-"*80)
        
        for day in sorted(day_stats.keys()):
            stats = day_stats[day]
            buy_rate = stats['bought'] / stats['count'] * 100 if stats['count'] > 0 else 0
            avg_saving = stats['total_saving'] / stats['bought'] if stats['bought'] > 0 else 0
            max_drawdown = stats['max_drawdown']
            print(f"{day.strftime('%Y-%m-%d'):<12} {stats['count']:>8}   {stats['bought']:>8}   "
                  f"{buy_rate:>8.1f}%   {avg_saving:>12.2f}%   {max_drawdown:>12.2f}%")


def main():
    """主函数"""
    print("="*80)
    print("弹性买入反弹比例计算器")
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
    
    # 创建计算器
    calculator = BestBuyRisePercentCalculator(stock_code)
    
    # 获取最近N个交易日
    trading_days = calculator.get_recent_trading_days(count=days)
    print(f"\n找到 {len(trading_days)} 个交易日:")
    for day in trading_days:
        print(f"  - {day.strftime('%Y-%m-%d')}")
    
    # 加载tick数据
    if not calculator.load_tick_data(trading_days):
        print("错误：无法加载tick数据")
        return
    
    # 计算最优反弹比例
    results = calculator.calculate_optimal_rise_percent(sample_interval=sample_interval)
    
    if results:
        # 打印结果
        calculator.print_results(results)
    else:
        print("错误：回测失败")


if __name__ == '__main__':
    main()

