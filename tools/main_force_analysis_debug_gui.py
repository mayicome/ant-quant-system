#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主力行为分析调试工具 - GUI版本
用于批量分析一组股票在某个时间段的所有主力行为的详细得分，包括总分和分项得分，导出到Excel文件
"""

import sys
import os
import pandas as pd
from datetime import datetime, date, timedelta
from typing import List, Dict
import traceback
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QFileDialog, 
                             QDateEdit, QTextEdit, QProgressBar, QMessageBox,
                             QGroupBox, QFileDialog)
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QDate
from PyQt5.QtGui import QFont

# 添加项目根目录到路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from core.stock_analyzer import StockAnalyzer
from core.backtest_engine import BacktestEngine
from key_price_calculator import KeyPriceCalculator
from utils.logger import Logger
from utils.trading_day import is_tradeday


class AnalysisThread(QThread):
    """分析线程"""
    progress = pyqtSignal(int, int, str)  # 当前进度, 总任务数, 当前任务描述
    finished = pyqtSignal(list)  # 分析结果
    error = pyqtSignal(str)  # 错误信息
    
    def __init__(self, stock_date_pairs: List[tuple]):
        """
        初始化分析线程
        stock_date_pairs: [(stock_code, analysis_date), ...] 股票代码和日期的配对列表
        """
        super().__init__()
        self.stock_date_pairs = stock_date_pairs  # [(stock_code, date), ...]
        self.debugger = None
    
    def run(self):
        """执行分析"""
        try:
            self.debugger = MainForceAnalysisDebugger()
            results = self.debugger.analyze_batch_from_pairs(
                self.stock_date_pairs,
                progress_callback=self._on_progress
            )
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(f"分析失败: {str(e)}\n{traceback.format_exc()}")
    
    def _on_progress(self, current: int, total: int, description: str):
        """进度回调"""
        self.progress.emit(current, total, description)


class MainForceAnalysisDebugger:
    """主力行为分析调试工具"""
    
    def __init__(self):
        self.stock_analyzer = StockAnalyzer()
        self.calculator = KeyPriceCalculator()
        self.logger = Logger()
        self.progress_callback = None
        
    def set_progress_callback(self, callback):
        """设置进度回调"""
        self.progress_callback = callback
        
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
                # 获取原始分数
                raw_total_score = high_dist.get('total_score', 0)
                # 应用与web程序相同的归一化：从最大120分归一化到0-100
                # 注意：虽然理论上三个公式都是0-100分，加权平均后也是0-100分
                # 但web程序使用120作为最大值进行归一化显示，这里保持一致
                normalized_score = round((min(raw_total_score, 120) / 120) * 100, 1) if raw_total_score > 0 else 0
                result['high_level_distribution'] = {
                    'total_score': normalized_score,  # 使用归一化后的分数，与web程序保持一致
                    'raw_total_score': raw_total_score,  # 保留原始分数供参考
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
            
            # 6. 涨停板行为分析
            try:
                limit_up = self.stock_analyzer.analyze_limit_up_behavior_comprehensive(
                    tick_data, daily_data, stock_code, str(analysis_date)
                )
                if limit_up.get('is_limit_up', False):
                    behaviors = limit_up.get('behaviors', {})
                    max_score = max(behaviors.values()) if behaviors else 0
                    # 应用归一化：从最大100分归一化到0-100（与web程序一致）
                    normalized_score = round((min(max_score, 100) / 100) * 100, 1) if max_score > 0 else 0
                    result['limit_up_behavior'] = {
                        'is_limit_up': True,
                        'total_score': normalized_score,  # 归一化后的分数（与web程序一致）
                        'raw_total_score': max_score,  # 原始最高分
                        'dominant_behavior': limit_up.get('dominant_behavior', ''),
                        'behavior_names': limit_up.get('behavior_names', {}),
                        'distribution_score': behaviors.get('distribution', 0),  # 诱多出货
                        'strong_seal_score': behaviors.get('strong_seal', 0),  # 强势封板
                        'wash_score': behaviors.get('wash', 0),  # 洗盘
                        'test_score': behaviors.get('test', 0),  # 试盘
                    }
                else:
                    result['limit_up_behavior'] = {
                        'is_limit_up': False,
                        'total_score': 0,
                        'raw_total_score': 0,
                        'dominant_behavior': '',
                        'distribution_score': 0,
                        'strong_seal_score': 0,
                        'wash_score': 0,
                        'test_score': 0,
                    }
            except Exception as e:
                result['limit_up_behavior'] = {'error': str(e)}
            
            # 7. 跌停板行为分析
            try:
                limit_down = self.stock_analyzer.analyze_limit_down_behavior_comprehensive(
                    tick_data, daily_data, stock_code, str(analysis_date)
                )
                if limit_down.get('is_limit_down', False):
                    behaviors = limit_down.get('behaviors', {})
                    max_score = max(behaviors.values()) if behaviors else 0
                    # 应用归一化：从最大100分归一化到0-100（与web程序一致）
                    normalized_score = round((min(max_score, 100) / 100) * 100, 1) if max_score > 0 else 0
                    result['limit_down_behavior'] = {
                        'is_limit_down': True,
                        'total_score': normalized_score,  # 归一化后的分数（与web程序一致）
                        'raw_total_score': max_score,  # 原始最高分
                        'dominant_behavior': limit_down.get('dominant_behavior', ''),
                        'behavior_names': limit_down.get('behavior_names', {}),
                        'wash_panic_score': behaviors.get('wash_panic', 0),  # 恐慌洗盘
                        'distribution_score': behaviors.get('distribution', 0),  # 出货砸盘
                        'passive_score': behaviors.get('passive', 0),  # 被动承压
                    }
                else:
                    result['limit_down_behavior'] = {
                        'is_limit_down': False,
                        'total_score': 0,
                        'raw_total_score': 0,
                        'dominant_behavior': '',
                        'wash_panic_score': 0,
                        'distribution_score': 0,
                        'passive_score': 0,
                    }
            except Exception as e:
                result['limit_down_behavior'] = {'error': str(e)}
            
            # 8. 极端行情分析
            try:
                extreme = self.stock_analyzer.analyze_extreme_swing_behavior(
                    tick_data, daily_data, stock_code, str(analysis_date)
                )
                if extreme.get('is_extreme_swing', False):
                    behaviors = extreme.get('behaviors', {})
                    max_score = extreme.get('max_score', 0)
                    # 应用归一化：从最大100分归一化到0-100（与web程序一致）
                    normalized_score = round((min(max_score, 100) / 100) * 100, 1) if max_score > 0 else 0
                    result['extreme_swing_behavior'] = {
                        'is_extreme_swing': True,
                        'switch_count': extreme.get('switch_count', 0),
                        'total_score': normalized_score,  # 归一化后的分数（与web程序一致）
                        'raw_total_score': max_score,  # 原始最高分
                        'dominant_behaviors': extreme.get('dominant_behaviors', []),
                        'behavior_names': extreme.get('behavior_names', {}),
                        'high_distribution_score': behaviors.get('high_distribution', 0),  # 高位诱多出货
                        'low_wash_score': behaviors.get('low_wash', 0),  # 低位恐慌洗盘
                        'capital_speculation_score': behaviors.get('capital_speculation', 0),  # 游资短期博弈
                    }
                else:
                    result['extreme_swing_behavior'] = {
                        'is_extreme_swing': False,
                        'switch_count': 0,
                        'total_score': 0,
                        'raw_total_score': 0,
                        'dominant_behaviors': [],
                        'high_distribution_score': 0,
                        'low_wash_score': 0,
                        'capital_speculation_score': 0,
                    }
            except Exception as e:
                result['extreme_swing_behavior'] = {'error': str(e)}
            
        except Exception as e:
            result['status'] = 'error'
            result['error'] = str(e)
        
        return result
    
    def analyze_batch(self, stock_codes: List[str], start_date: date, end_date: date, progress_callback=None) -> List[Dict]:
        """批量分析股票（旧版本，保留以兼容）"""
        self.progress_callback = progress_callback
        results = []
        trading_dates = self._get_trading_dates(start_date, end_date)
        total_tasks = len(stock_codes) * len(trading_dates)
        current_task = 0
        
        for stock_code in stock_codes:
            for analysis_date in trading_dates:
                current_task += 1
                description = f"{stock_code} - {analysis_date.strftime('%Y-%m-%d')}"
                
                if self.progress_callback:
                    self.progress_callback(current_task, total_tasks, description)
                
                result = self.analyze_single_stock_date(stock_code, analysis_date)
                results.append(result)
        
        return results
    
    def analyze_batch_from_pairs(self, stock_date_pairs: List[tuple], progress_callback=None) -> List[Dict]:
        """批量分析股票（新版本：从股票代码和日期配对列表分析）"""
        self.progress_callback = progress_callback
        results = []
        total_tasks = len(stock_date_pairs)
        current_task = 0
        
        for stock_code, analysis_date in stock_date_pairs:
            current_task += 1
            description = f"{stock_code} - {analysis_date.strftime('%Y-%m-%d')}"
            
            if self.progress_callback:
                self.progress_callback(current_task, total_tasks, description)
            
            result = self.analyze_single_stock_date(stock_code, analysis_date)
            results.append(result)
        
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
            row['高位出货_总分'] = hd.get('total_score', 0)  # 归一化后的分数（与web程序一致）
            row['高位出货_原始总分'] = hd.get('raw_total_score', hd.get('total_score', 0))  # 原始分数
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
            
            # 涨停板行为分析
            lu = result.get('limit_up_behavior', {})
            row['涨停板_是否涨停'] = '是' if lu.get('is_limit_up', False) else '否'
            row['涨停板_总分'] = lu.get('total_score', 0)
            row['涨停板_原始总分'] = lu.get('raw_total_score', 0)
            row['涨停板_主导行为'] = lu.get('behavior_names', {}).get(lu.get('dominant_behavior', ''), lu.get('dominant_behavior', ''))
            row['涨停板_诱多出货'] = lu.get('distribution_score', 0)
            row['涨停板_强势封板'] = lu.get('strong_seal_score', 0)
            row['涨停板_洗盘'] = lu.get('wash_score', 0)
            row['涨停板_试盘'] = lu.get('test_score', 0)
            
            # 跌停板行为分析
            ld = result.get('limit_down_behavior', {})
            row['跌停板_是否跌停'] = '是' if ld.get('is_limit_down', False) else '否'
            row['跌停板_总分'] = ld.get('total_score', 0)
            row['跌停板_原始总分'] = ld.get('raw_total_score', 0)
            row['跌停板_主导行为'] = ld.get('behavior_names', {}).get(ld.get('dominant_behavior', ''), ld.get('dominant_behavior', ''))
            row['跌停板_恐慌洗盘'] = ld.get('wash_panic_score', 0)
            row['跌停板_出货砸盘'] = ld.get('distribution_score', 0)
            row['跌停板_被动承压'] = ld.get('passive_score', 0)
            row['跌停板_试盘'] = ld.get('test_score', 0)
            
            # 极端行情分析
            es = result.get('extreme_swing_behavior', {})
            row['极端行情_是否极端'] = '是' if es.get('is_extreme_swing', False) else '否'
            row['极端行情_切换次数'] = es.get('switch_count', 0)
            row['极端行情_总分'] = es.get('total_score', 0)
            row['极端行情_原始总分'] = es.get('raw_total_score', 0)
            # 主导行为可能有多个，用逗号分隔
            dominant_behaviors = es.get('dominant_behaviors', [])
            behavior_names = es.get('behavior_names', {})
            dominant_names = [behavior_names.get(b, b) for b in dominant_behaviors] if dominant_behaviors else []
            row['极端行情_主导行为'] = '、'.join(dominant_names) if dominant_names else ''
            row['极端行情_高位诱多出货'] = es.get('high_distribution_score', 0)
            row['极端行情_低位恐慌洗盘'] = es.get('low_wash_score', 0)
            row['极端行情_游资短期博弈'] = es.get('capital_speculation_score', 0)
            
            rows.append(row)
        
        # 创建DataFrame
        df = pd.DataFrame(rows)
        
        # 导出到Excel
        with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='主力行为分析', index=False)
            
            # 设置列宽
            worksheet = writer.sheets['主力行为分析']
            from openpyxl.utils import get_column_letter
            for idx, col in enumerate(df.columns):
                max_length = max(
                    df[col].astype(str).map(len).max(),
                    len(col)
                ) + 2
                col_letter = get_column_letter(idx + 1)
                worksheet.column_dimensions[col_letter].width = min(max_length, 50)


class MainWindow(QMainWindow):
    """主窗口"""
    
    def __init__(self):
        super().__init__()
        self.analysis_results = None
        self.init_ui()
    
    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle('主力行为分析调试工具')
        self.setGeometry(100, 100, 800, 600)
        
        # 中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 主布局
        layout = QVBoxLayout()
        central_widget.setLayout(layout)
        
        # 股票和日期列表组
        stock_group = QGroupBox('股票代码和日期列表')
        stock_layout = QVBoxLayout()
        
        # 导入按钮
        import_btn = QPushButton('从文件导入股票代码和日期')
        import_btn.clicked.connect(self.import_stocks)
        stock_layout.addWidget(import_btn)
        
        # 股票和日期列表显示
        self.stock_text = QTextEdit()
        self.stock_text.setPlaceholderText('股票代码和日期列表，每行一个，格式：\n股票代码\t日期\n例如：\n000826\t2025-10-31\n000820\t2025-11-03\n\n支持制表符或空格分隔')
        self.stock_text.setMaximumHeight(200)
        stock_layout.addWidget(self.stock_text)
        
        stock_group.setLayout(stock_layout)
        layout.addWidget(stock_group)
        
        # 操作按钮
        button_layout = QHBoxLayout()
        
        self.analyze_btn = QPushButton('开始分析')
        self.analyze_btn.clicked.connect(self.start_analysis)
        self.analyze_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 10px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        button_layout.addWidget(self.analyze_btn)
        
        self.save_btn = QPushButton('保存到Excel')
        self.save_btn.clicked.connect(self.save_results)
        self.save_btn.setEnabled(False)
        self.save_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 10px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #0b7dda;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        button_layout.addWidget(self.save_btn)
        
        layout.addLayout(button_layout)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        # 状态信息
        self.status_label = QLabel('就绪')
        self.status_label.setStyleSheet("padding: 5px;")
        layout.addWidget(self.status_label)
        
        # 日志输出
        log_group = QGroupBox('分析日志')
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont('Consolas', 9))
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        layout.addWidget(log_group)
    
    def import_stocks(self):
        """导入股票代码和日期列表"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, 
            '选择股票代码和日期文件', 
            '', 
            '文本文件 (*.txt);;所有文件 (*)'
        )
        
        if file_path:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    self.stock_text.setPlainText(content)
                    self.log(f"已导入股票代码和日期列表: {file_path}")
            except Exception as e:
                QMessageBox.warning(self, '错误', f'读取文件失败: {str(e)}')
    
    def get_stock_date_pairs(self) -> List[tuple]:
        """
        获取股票代码和日期配对列表
        返回: [(stock_code, date), ...]
        """
        text = self.stock_text.toPlainText().strip()
        if not text:
            return []
        
        pairs = []
        for line_num, line in enumerate(text.split('\n'), 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # 尝试用制表符或空格分隔
            parts = line.split('\t')
            if len(parts) == 1:
                parts = line.split()
            
            if len(parts) < 2:
                self.log(f"警告: 第{line_num}行格式不正确，跳过: {line}")
                continue
            
            stock_code = parts[0].strip()
            date_str = parts[1].strip()
            
            # 解析日期
            try:
                analysis_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                pairs.append((stock_code, analysis_date))
            except ValueError:
                self.log(f"警告: 第{line_num}行日期格式不正确，跳过: {line}")
                continue
        
        return pairs
    
    def start_analysis(self):
        """开始分析"""
        # 获取股票代码和日期配对列表
        stock_date_pairs = self.get_stock_date_pairs()
        if not stock_date_pairs:
            QMessageBox.warning(self, '警告', '请先导入或输入股票代码和日期列表\n格式：股票代码\t日期（每行一个）')
            return
        
        # 禁用按钮
        self.analyze_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        
        # 显示进度条
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        
        # 清空日志
        self.log_text.clear()
        self.log(f"开始分析:")
        self.log(f"  任务数量: {len(stock_date_pairs)}")
        self.log(f"  股票代码和日期:")
        for stock_code, analysis_date in stock_date_pairs[:10]:  # 只显示前10个
            self.log(f"    {stock_code} - {analysis_date}")
        if len(stock_date_pairs) > 10:
            self.log(f"    ... 还有 {len(stock_date_pairs) - 10} 个任务")
        self.log("")
        
        # 创建分析线程
        self.analysis_thread = AnalysisThread(stock_date_pairs)
        self.analysis_thread.progress.connect(self.on_progress)
        self.analysis_thread.finished.connect(self.on_analysis_finished)
        self.analysis_thread.error.connect(self.on_analysis_error)
        self.analysis_thread.start()
    
    def on_progress(self, current: int, total: int, description: str):
        """更新进度"""
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.status_label.setText(f"分析中: {current}/{total} - {description}")
        self.log(f"[{current}/{total}] {description}")
        QApplication.processEvents()
    
    def on_analysis_finished(self, results: List[Dict]):
        """分析完成"""
        self.analysis_results = results
        
        # 统计结果
        success_count = sum(1 for r in results if r['status'] == 'success')
        total_count = len(results)
        
        self.log("")
        self.log(f"分析完成！")
        self.log(f"  总任务数: {total_count}")
        self.log(f"  成功: {success_count}")
        self.log(f"  失败: {total_count - success_count}")
        
        # 隐藏进度条
        self.progress_bar.setVisible(False)
        self.status_label.setText(f'分析完成: {success_count}/{total_count} 成功')
        
        # 启用按钮
        self.analyze_btn.setEnabled(True)
        self.save_btn.setEnabled(True)
        
        QMessageBox.information(self, '完成', f'分析完成！\n总任务数: {total_count}\n成功: {success_count}\n失败: {total_count - success_count}')
    
    def on_analysis_error(self, error_msg: str):
        """分析错误"""
        self.log(f"错误: {error_msg}")
        self.progress_bar.setVisible(False)
        self.status_label.setText('分析失败')
        self.analyze_btn.setEnabled(True)
        QMessageBox.critical(self, '错误', f'分析失败:\n{error_msg}')
    
    def save_results(self):
        """保存结果到Excel"""
        if not self.analysis_results:
            QMessageBox.warning(self, '警告', '没有分析结果可保存')
            return
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            '保存Excel文件',
            f'main_force_analysis_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx',
            'Excel文件 (*.xlsx);;所有文件 (*)'
        )
        
        if file_path:
            try:
                debugger = MainForceAnalysisDebugger()
                debugger.export_to_excel(self.analysis_results, file_path)
                self.log(f"结果已保存到: {file_path}")
                QMessageBox.information(self, '成功', f'结果已保存到:\n{file_path}')
            except Exception as e:
                QMessageBox.critical(self, '错误', f'保存失败:\n{str(e)}')
    
    def log(self, message: str):
        """添加日志"""
        self.log_text.append(message)
        # 自动滚动到底部
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())


def main():
    """主函数"""
    app = QApplication(sys.argv)
    app.setStyle('Fusion')  # 使用Fusion样式
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()

