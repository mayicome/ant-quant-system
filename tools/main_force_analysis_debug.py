#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主力行为分析调试工具
用于批量分析一组股票在某个时间段的所有主力行为的详细得分，包括总分和分项得分，导出到Excel文件
"""

import sys
import os
import pandas as pd
from datetime import datetime, date, timedelta
from typing import List, Dict, Tuple
import traceback

# 添加项目根目录到路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from core.stock_analyzer import StockAnalyzer
from core.backtest_engine import BacktestEngine
from key_price_calculator import KeyPriceCalculator
from utils.logger import Logger
from utils.trading_day import is_tradeday


class MainForceAnalysisDebugger:
    """主力行为分析调试工具"""
    
    def __init__(self):
        self.stock_analyzer = StockAnalyzer()
        self.calculator = KeyPriceCalculator()
        self.logger = Logger()
        
    def get_tick_data(self, stock_code: str, analysis_date: date) -> pd.DataFrame:
        """获取指定日期的tick数据"""
        try:
            # 使用BacktestEngine获取tick数据
            engine = BacktestEngine(stock_code=stock_code)
            engine.set_logger(self.logger)
            success = engine.load_data(analysis_date, analysis_date)
            
            if not success or engine.data is None or engine.data.empty:
                return pd.DataFrame()
            
            return engine.data
        except Exception as e:
            print(f"  获取tick数据失败 {stock_code} {analysis_date}: {e}")
            return pd.DataFrame()
    
    def get_daily_data(self, stock_code: str, days: int = 60, base_date: date = None) -> pd.DataFrame:
        """获取日线数据"""
        try:
            # 构造完整的股票代码（带后缀）
            full_stock_code = stock_code
            if not '.' in stock_code:
                if stock_code.startswith(('0', '1', '3')):
                    full_stock_code = f"{stock_code}.SZ"
                elif stock_code.startswith('6'):
                    full_stock_code = f"{stock_code}.SH"
                elif stock_code.startswith('8') or stock_code.startswith('4') or stock_code.startswith('920'):
                    full_stock_code = f"{stock_code}.BJ"
            
            # 使用calculator获取日线数据
            daily_df = self.calculator._get_qmt_daily_data(full_stock_code)
            
            if daily_df is None or daily_df.empty:
                return pd.DataFrame()
            
            return daily_df
        except Exception as e:
            print(f"  获取日线数据失败 {stock_code}: {e}")
            return pd.DataFrame()
    
    def analyze_single_stock_date(self, stock_code: str, analysis_date: date) -> Dict:
        """分析单只股票单个日期的主力行为"""
        result = {
            'stock_code': stock_code,
            'analysis_date': analysis_date.strftime('%Y-%m-%d'),
            'status': 'success',
            'error': None,
            'high_level_distribution': {},
            'low_level_accumulation': {},
            'main_force_lift': {},
            'main_force_wash': {},
            'main_force_sweep': {}
        }
        
        try:
            # 获取数据
            tick_data = self.get_tick_data(stock_code, analysis_date)
            if tick_data.empty:
                result['status'] = 'no_tick_data'
                result['error'] = '无tick数据'
                return result
            
            daily_data = self.get_daily_data(stock_code, days=60, base_date=analysis_date)
            if daily_data.empty:
                result['status'] = 'no_daily_data'
                result['error'] = '无日线数据'
                return result
            
            # 执行5种主力行为分析
            # 1. 高位出货
            try:
                high_dist = self.stock_analyzer.analyze_high_level_distribution_comprehensive(
                    daily_data, tick_data, stock_code, str(analysis_date)
                )
                result['high_level_distribution'] = {
                    'total_score': high_dist.get('total_score', 0),
                    'risk_level': high_dist.get('risk_level', ''),
                    'formula1_score': high_dist.get('formulas', {}).get('formula1', {}).get('score', 0),
                    'formula2_score': high_dist.get('formulas', {}).get('formula2', {}).get('score', 0),
                    'formula3_score': high_dist.get('formulas', {}).get('formula3', {}).get('score', 0),
                }
            except Exception as e:
                result['high_level_distribution'] = {'error': str(e)}
            
            # 2. 低位吸筹
            try:
                low_acc = self.stock_analyzer.analyze_low_level_accumulation_comprehensive(
                    daily_data, tick_data, stock_code, str(analysis_date)
                )
                result['low_level_accumulation'] = {
                    'total_score': low_acc.get('total_score', 0),
                    'risk_level': low_acc.get('risk_level', ''),
                    'formula1_score': low_acc.get('formulas', {}).get('formula1', {}).get('score', 0),
                    'formula2_score': low_acc.get('formulas', {}).get('formula2', {}).get('score', 0),
                    'formula3_score': low_acc.get('formulas', {}).get('formula3', {}).get('score', 0),
                }
            except Exception as e:
                result['low_level_accumulation'] = {'error': str(e)}
            
            # 3. 主力拉升
            try:
                lift = self.stock_analyzer.analyze_main_force_lift_comprehensive(
                    daily_data, tick_data, stock_code, str(analysis_date)
                )
                result['main_force_lift'] = {
                    'total_score': lift.get('total_score', 0),
                    'risk_level': lift.get('risk_level', ''),
                    'formula1_score': lift.get('formulas', {}).get('formula1', {}).get('score', 0),
                    'formula2_score': lift.get('formulas', {}).get('formula2', {}).get('score', 0),
                    'formula3_score': lift.get('formulas', {}).get('formula3', {}).get('score', 0),
                }
            except Exception as e:
                result['main_force_lift'] = {'error': str(e)}
            
            # 4. 主力洗盘
            try:
                wash = self.stock_analyzer.analyze_main_force_wash_comprehensive(
                    daily_data, tick_data, stock_code, str(analysis_date)
                )
                result['main_force_wash'] = {
                    'total_score': wash.get('total_score', 0),
                    'risk_level': wash.get('risk_level', ''),
                    'formula1_score': wash.get('formulas', {}).get('formula1', {}).get('score', 0),
                    'formula2_score': wash.get('formulas', {}).get('formula2', {}).get('score', 0),
                    'formula3_score': wash.get('formulas', {}).get('formula3', {}).get('score', 0),
                }
            except Exception as e:
                result['main_force_wash'] = {'error': str(e)}
            
            # 5. 主力扫货
            try:
                sweep = self.stock_analyzer.analyze_main_force_sweep_comprehensive(
                    daily_data, tick_data, stock_code, str(analysis_date)
                )
                result['main_force_sweep'] = {
                    'total_score': sweep.get('total_score', 0),
                    'risk_level': sweep.get('risk_level', ''),
                    'formula1_score': sweep.get('formulas', {}).get('formula1', {}).get('score', 0),
                    'formula2_score': sweep.get('formulas', {}).get('formula2', {}).get('score', 0),
                    'formula3_score': sweep.get('formulas', {}).get('formula3', {}).get('score', 0),
                }
            except Exception as e:
                result['main_force_sweep'] = {'error': str(e)}
            
        except Exception as e:
            result['status'] = 'error'
            result['error'] = str(e)
            traceback.print_exc()
        
        return result
    
    def analyze_batch(self, stock_codes: List[str], start_date: date, end_date: date) -> List[Dict]:
        """批量分析股票"""
        results = []
        total_tasks = len(stock_codes) * len(self._get_trading_dates(start_date, end_date))
        current_task = 0
        
        print(f"\n开始批量分析:")
        print(f"  股票数量: {len(stock_codes)}")
        print(f"  日期范围: {start_date} 至 {end_date}")
        print(f"  总任务数: {total_tasks}")
        print()
        
        for stock_code in stock_codes:
            print(f"分析股票: {stock_code}")
            trading_dates = self._get_trading_dates(start_date, end_date)
            
            for analysis_date in trading_dates:
                current_task += 1
                print(f"  [{current_task}/{total_tasks}] {analysis_date.strftime('%Y-%m-%d')}...", end=' ')
                
                result = self.analyze_single_stock_date(stock_code, analysis_date)
                results.append(result)
                
                if result['status'] == 'success':
                    print("✓")
                else:
                    print(f"✗ ({result.get('error', 'unknown')})")
        
        return results
    
    def _get_trading_dates(self, start_date: date, end_date: date) -> List[date]:
        """获取交易日列表"""
        trading_dates = []
        current_date = start_date
        while current_date <= end_date:
            if is_tradeday(current_date):
                trading_dates.append(current_date)
            current_date += timedelta(days=1)
        return trading_dates
    
    def export_to_excel(self, results: List[Dict], output_file: str):
        """导出结果到Excel"""
        print(f"\n导出结果到Excel: {output_file}")
        
        # 准备数据
        rows = []
        for result in results:
            row = {
                '股票代码': result['stock_code'],
                '分析日期': result['analysis_date'],
                '状态': result['status'],
                '错误信息': result.get('error', ''),
            }
            
            # 高位出货
            hd = result.get('high_level_distribution', {})
            row['高位出货_总分'] = hd.get('total_score', 0)
            row['高位出货_风险等级'] = hd.get('risk_level', '')
            row['高位出货_公式1'] = hd.get('formula1_score', 0)
            row['高位出货_公式2'] = hd.get('formula2_score', 0)
            row['高位出货_公式3'] = hd.get('formula3_score', 0)
            
            # 低位吸筹
            la = result.get('low_level_accumulation', {})
            row['低位吸筹_总分'] = la.get('total_score', 0)
            row['低位吸筹_风险等级'] = la.get('risk_level', '')
            row['低位吸筹_公式1'] = la.get('formula1_score', 0)
            row['低位吸筹_公式2'] = la.get('formula2_score', 0)
            row['低位吸筹_公式3'] = la.get('formula3_score', 0)
            
            # 主力拉升
            mfl = result.get('main_force_lift', {})
            row['主力拉升_总分'] = mfl.get('total_score', 0)
            row['主力拉升_风险等级'] = mfl.get('risk_level', '')
            row['主力拉升_公式1'] = mfl.get('formula1_score', 0)
            row['主力拉升_公式2'] = mfl.get('formula2_score', 0)
            row['主力拉升_公式3'] = mfl.get('formula3_score', 0)
            
            # 主力洗盘
            mfw = result.get('main_force_wash', {})
            row['主力洗盘_总分'] = mfw.get('total_score', 0)
            row['主力洗盘_风险等级'] = mfw.get('risk_level', '')
            row['主力洗盘_公式1'] = mfw.get('formula1_score', 0)
            row['主力洗盘_公式2'] = mfw.get('formula2_score', 0)
            row['主力洗盘_公式3'] = mfw.get('formula3_score', 0)
            
            # 主力扫货
            mfs = result.get('main_force_sweep', {})
            row['主力扫货_总分'] = mfs.get('total_score', 0)
            row['主力扫货_风险等级'] = mfs.get('risk_level', '')
            row['主力扫货_公式1'] = mfs.get('formula1_score', 0)
            row['主力扫货_公式2'] = mfs.get('formula2_score', 0)
            row['主力扫货_公式3'] = mfs.get('formula3_score', 0)
            
            rows.append(row)
        
        # 创建DataFrame
        df = pd.DataFrame(rows)
        
        # 导出到Excel
        with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='主力行为分析', index=False)
            
            # 设置列宽
            worksheet = writer.sheets['主力行为分析']
            for idx, col in enumerate(df.columns):
                max_length = max(
                    df[col].astype(str).map(len).max(),
                    len(col)
                ) + 2
                worksheet.column_dimensions[chr(65 + idx)].width = min(max_length, 50)
        
        print(f"✓ 导出完成，共 {len(rows)} 条记录")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='主力行为分析调试工具')
    parser.add_argument('--stocks', type=str, required=True, help='股票代码列表，用逗号分隔，如: 000001.SZ,600000.SH')
    parser.add_argument('--start-date', type=str, required=True, help='开始日期，格式: YYYY-MM-DD')
    parser.add_argument('--end-date', type=str, required=True, help='结束日期，格式: YYYY-MM-DD')
    parser.add_argument('--output', type=str, default='main_force_analysis.xlsx', help='输出Excel文件名')
    
    args = parser.parse_args()
    
    # 解析股票代码
    stock_codes = [s.strip() for s in args.stocks.split(',')]
    
    # 解析日期
    start_date = datetime.strptime(args.start_date, '%Y-%m-%d').date()
    end_date = datetime.strptime(args.end_date, '%Y-%m-%d').date()
    
    # 创建调试工具
    debugger = MainForceAnalysisDebugger()
    print("✓ 调试工具初始化成功")
    
    # 批量分析
    results = debugger.analyze_batch(stock_codes, start_date, end_date)
    
    # 导出到Excel
    debugger.export_to_excel(results, args.output)
    
    print(f"\n完成！结果已保存到: {args.output}")


if __name__ == '__main__':
    main()

