"""
涨停板主力行为分析参数优化工具
自动搜索最优参数组合，提高识别准确率
"""

import sys
import os
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from itertools import product
import json
import copy

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QTextEdit, QLabel, 
                             QFileDialog, QProgressBar, QGroupBox, QSpinBox,
                             QDoubleSpinBox, QTableWidget, QTableWidgetItem,
                             QHeaderView, QMessageBox, QCheckBox)
import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QFont

from core.stock_analyzer import StockAnalyzer
from core.backtest_engine import BacktestEngine
from key_price_calculator import KeyPriceCalculator


class CachedStockAnalyzer(StockAnalyzer):
    """带股本缓存的StockAnalyzer"""
    
    def __init__(self, shares_cache: Dict[str, float] = None):
        super().__init__()
        self.shares_cache = shares_cache or {}
    
    def _get_stock_total_shares(self, stock_code: str) -> float:
        """获取股票总股本（万股），使用缓存（不打印日志）"""
        # 先检查缓存
        if stock_code in self.shares_cache:
            return self.shares_cache[stock_code]
        
        # 如果缓存中没有，静默获取（不打印日志）
        try:
            import xtquant.xtdata as xtdata
            # 尝试不同的股票代码格式
            stock_codes_to_try = [stock_code]
            if not '.' in stock_code:
                if stock_code.startswith(('0', '1', '3')):
                    stock_codes_to_try.append(f"{stock_code}.SZ")
                elif stock_code.startswith('6'):
                    stock_codes_to_try.append(f"{stock_code}.SH")
                elif stock_code.startswith(('8', '4')) or stock_code.startswith('920'):
                    stock_codes_to_try.append(f"{stock_code}.BJ")
            
            for code in stock_codes_to_try:
                try:
                    stock_info = xtdata.get_instrument_detail(code)
                    if stock_info and isinstance(stock_info, dict):
                        # 优先使用FloatVolume（流通股本）
                        shares = None
                        if 'FloatVolume' in stock_info:
                            shares = stock_info['FloatVolume']
                        elif 'TotalVolume' in stock_info:
                            shares = stock_info['TotalVolume']
                        if shares and shares > 0:
                            # 转换为万股
                            if shares > 10000:
                                shares = shares / 10000
                            self.shares_cache[stock_code] = shares
                            return shares
                except:
                    continue
        except:
            pass
        
        return 0.0


class ParameterizedAnalyzer:
    """参数化的涨停板分析器"""
    
    def __init__(self, base_analyzer: StockAnalyzer):
        self.base_analyzer = base_analyzer
    
    def analyze_with_params(self, tick_data: pd.DataFrame, daily_data: pd.DataFrame,
                           stock_code: str, params: Dict) -> Dict:
        """使用自定义参数进行分析"""
        # 这里我们需要修改分析器的内部参数
        # 由于原代码中参数是硬编码的，我们需要创建一个参数化的版本
        # 暂时使用原始分析器，后续可以通过猴子补丁或继承来参数化
        
        # 先使用原始分析器获取结果
        result = self.base_analyzer.analyze_limit_up_behavior_comprehensive(
            tick_data, daily_data, stock_code, None
        )
        
        # 如果结果不是涨停，直接返回
        if not result.get('is_limit_up', False):
            return result
        
        # 获取原始得分
        behaviors = result.get('behaviors', {})
        
        # 根据参数调整得分（这里简化处理，实际需要更复杂的参数化逻辑）
        # 由于参数化整个分析逻辑很复杂，我们采用权重调整的方式
        adjusted_behaviors = {}
        
        # 对每个行为的得分应用权重
        if 'strong_seal_weight' in params:
            adjusted_behaviors['strong_seal'] = behaviors.get('strong_seal', 0) * params['strong_seal_weight']
        else:
            adjusted_behaviors['strong_seal'] = behaviors.get('strong_seal', 0)
        
        if 'test_weight' in params:
            adjusted_behaviors['test'] = behaviors.get('test', 0) * params['test_weight']
        else:
            adjusted_behaviors['test'] = behaviors.get('test', 0)
        
        if 'wash_weight' in params:
            adjusted_behaviors['wash'] = behaviors.get('wash', 0) * params['wash_weight']
        else:
            adjusted_behaviors['wash'] = behaviors.get('wash', 0)
        
        if 'distribution_weight' in params:
            adjusted_behaviors['distribution'] = behaviors.get('distribution', 0) * params['distribution_weight']
        else:
            adjusted_behaviors['distribution'] = behaviors.get('distribution', 0)
        
        # 确定主导行为
        max_score = max(adjusted_behaviors.values())
        if max_score == 0:
            dominant_behavior = None
        else:
            max_behaviors = [key for key, score in adjusted_behaviors.items() if score == max_score]
            if len(max_behaviors) > 1:
                priority_order = ['strong_seal', 'distribution', 'test', 'wash']
                for priority_key in priority_order:
                    if priority_key in max_behaviors:
                        dominant_behavior = priority_key
                        break
                else:
                    dominant_behavior = max_behaviors[0]
            else:
                dominant_behavior = max_behaviors[0]
        
        return {
            'is_limit_up': True,
            'behaviors': adjusted_behaviors,
            'dominant_behavior': dominant_behavior,
            'behavior_names': result.get('behavior_names', {}),
            'stock_code': stock_code
        }


class OptimizationThread(QThread):
    """参数优化线程"""
    progress = pyqtSignal(int, str)  # 进度, 消息
    result = pyqtSignal(dict)  # 最优参数和准确率
    
    def __init__(self, stock_date_pairs: List[Tuple], actual_behaviors: List[str],
                 param_ranges: Dict, max_iterations: int = 1000, global_params: Dict = None):
        super().__init__()
        self.stock_date_pairs = stock_date_pairs
        self.actual_behaviors = actual_behaviors
        self.param_ranges = param_ranges
        self.max_iterations = max_iterations
        self.global_params = global_params or {}
        self.stop_flag = False
        
        # 初始化数据获取器
        self.key_price_calculator = KeyPriceCalculator()
        
        # 创建logger（BacktestEngine需要）
        from utils.logger import Logger
        self.logger = Logger()
        
        # 数据缓存：在优化开始前一次性加载所有数据
        self.data_cache = {}  # {(stock_code, date): (tick_data, daily_data)}
        
        # 股本缓存：避免重复获取
        self.shares_cache = {}  # {stock_code: shares_in_wan}
        
        # 分析结果缓存：预先计算好所有股票的原始行为得分（不应用权重）
        self.analysis_cache = {}  # {(stock_code, date): {behaviors: {...}, is_limit_up: bool}}
        
        # 创建带缓存的analyzer（在load_all_data之后创建，以便传入缓存）
        self.base_analyzer = None
        self.param_analyzer = None
    
    def stop(self):
        """停止优化"""
        self.stop_flag = True
    
    def load_all_data(self):
        """一次性加载所有需要的数据并缓存"""
        self.progress.emit(0, f"开始加载数据，共 {len(self.stock_date_pairs)} 条...")
        
        loaded_count = 0
        for i, (stock_code, date) in enumerate(self.stock_date_pairs):
            if self.stop_flag:
                break
            
            cache_key = (stock_code, str(date))
            if cache_key in self.data_cache:
                continue  # 已缓存，跳过
            
            try:
                # 转换日期格式
                if isinstance(date, str):
                    analysis_date = datetime.strptime(date, '%Y-%m-%d').date()
                else:
                    analysis_date = date if isinstance(date, type(datetime.now().date())) else datetime.strptime(str(date), '%Y-%m-%d').date()
                
                # 转换股票代码格式（添加交易所后缀）
                formatted_stock_code = stock_code
                if not stock_code.endswith(('.SH', '.SZ', '.BJ')):
                    if stock_code.startswith(('0', '1', '3')):
                        formatted_stock_code = f"{stock_code}.SZ"
                    elif stock_code.startswith(('5', '6')):
                        formatted_stock_code = f"{stock_code}.SH"
                    elif stock_code.startswith(('4', '8', '920')):
                        formatted_stock_code = f"{stock_code}.BJ"
                
                # 获取tick数据
                engine = BacktestEngine(stock_code=stock_code)
                engine.set_logger(self.logger)
                success = engine.load_data(analysis_date, analysis_date)
                
                if not success:
                    self.progress.emit(0, f"警告: {stock_code} {date} - load_data返回False")
                    continue
                
                if engine.data is None:
                    self.progress.emit(0, f"警告: {stock_code} {date} - engine.data为None")
                    continue
                
                if engine.data.empty:
                    self.progress.emit(0, f"警告: {stock_code} {date} - engine.data为空")
                    continue
                
                tick_data = engine.data
                
                # 获取日线数据（使用格式化后的股票代码）
                daily_data = self.key_price_calculator._get_qmt_daily_data(formatted_stock_code)
                
                if daily_data.empty:
                    self.progress.emit(0, f"警告: {stock_code} {date} - daily_data为空")
                    continue
                
                # 获取并缓存股本信息（只获取一次，静默获取，不打印日志）
                if stock_code not in self.shares_cache:
                    try:
                        import xtquant.xtdata as xtdata
                        # 尝试不同的股票代码格式
                        stock_codes_to_try = [stock_code, formatted_stock_code]
                        shares = None
                        for code in stock_codes_to_try:
                            try:
                                stock_info = xtdata.get_instrument_detail(code)
                                if stock_info and isinstance(stock_info, dict):
                                    # 优先使用FloatVolume（流通股本）
                                    if 'FloatVolume' in stock_info:
                                        shares = stock_info['FloatVolume']
                                    elif 'TotalVolume' in stock_info:
                                        shares = stock_info['TotalVolume']
                                    if shares and shares > 0:
                                        # 转换为万股
                                        if shares > 10000:
                                            shares = shares / 10000
                                        self.shares_cache[stock_code] = shares
                                        break
                            except:
                                continue
                    except Exception as e:
                        # 股本获取失败不影响数据加载，继续
                        pass
                
                # 缓存数据
                self.data_cache[cache_key] = (tick_data, daily_data)
                loaded_count += 1
                
                progress = int((i + 1) / len(self.stock_date_pairs) * 50)  # 数据加载占50%进度
                self.progress.emit(progress, f"已加载 {loaded_count}/{len(self.stock_date_pairs)} 条数据 ({stock_code} {date})...")
                
            except Exception as e:
                import traceback
                error_msg = f"错误: {stock_code} {date} - {str(e)}"
                self.progress.emit(0, error_msg)
                self.progress.emit(0, traceback.format_exc())
                continue
        
        self.progress.emit(50, f"数据加载完成，成功加载 {loaded_count} 条数据，缓存了 {len(self.shares_cache)} 个股票的股本信息")
        
        # 创建带缓存的analyzer
        self.base_analyzer = CachedStockAnalyzer(self.shares_cache)
        self.param_analyzer = ParameterizedAnalyzer(self.base_analyzer)
        
        # 预先计算所有股票的原始行为得分（只计算一次）
        self.progress.emit(50, "开始预计算分析结果...")
        precompute_count = 0
        for i, (stock_code, date) in enumerate(self.stock_date_pairs):
            if self.stop_flag:
                break
            
            cache_key = (stock_code, str(date))
            if cache_key not in self.data_cache:
                continue
            
            tick_data, daily_data = self.data_cache[cache_key]
            
            try:
                # 预先计算原始分析结果（不应用权重）
                result = self.base_analyzer.analyze_limit_up_behavior_comprehensive(
                    tick_data, daily_data, stock_code, None
                )
                # 缓存分析结果
                self.analysis_cache[cache_key] = {
                    'behaviors': result.get('behaviors', {}),
                    'is_limit_up': result.get('is_limit_up', False),
                    'behavior_names': result.get('behavior_names', {})
                }
                precompute_count += 1
                
                if (i + 1) % 5 == 0:
                    progress = 50 + int((i + 1) / len(self.stock_date_pairs) * 10)  # 预计算占10%进度
                    self.progress.emit(progress, f"预计算进度: {precompute_count}/{len(self.data_cache)}...")
            except Exception as e:
                # 分析失败，缓存空结果
                self.analysis_cache[cache_key] = {
                    'behaviors': {},
                    'is_limit_up': False,
                    'behavior_names': {}
                }
                continue
        
        self.progress.emit(60, f"预计算完成，成功分析 {precompute_count} 条数据")
        
        return loaded_count
    
    def evaluate_params(self, params: Dict, return_details: bool = False) -> float:
        """评估参数组合的准确率（使用预计算的分析结果，支持多维度参数调整）
        
        Args:
            params: 参数字典，可包含：
                - weight参数：strong_seal_weight, test_weight, wash_weight, distribution_weight
                - bias参数：strong_seal_bias, test_bias, wash_bias, distribution_bias
                - normalize: 是否归一化得分（bool）
                - min_score_diff: 最小得分差异阈值（只有当最高分与次高分差异>=此值时才确定主导行为）
            return_details: 是否返回详细信息（混淆矩阵等）
        
        Returns:
            准确率（0-1），如果return_details=True，返回(准确率, 详细信息字典)
        """
        correct = 0
        total = 0
        
        # 混淆矩阵：{实际行为: {预测行为: 数量}}
        confusion_matrix = {
            'strong_seal': {'strong_seal': 0, 'test': 0, 'wash': 0, 'distribution': 0},
            'test': {'strong_seal': 0, 'test': 0, 'wash': 0, 'distribution': 0},
            'wash': {'strong_seal': 0, 'test': 0, 'wash': 0, 'distribution': 0},
            'distribution': {'strong_seal': 0, 'test': 0, 'wash': 0, 'distribution': 0}
        }
        
        behavior_map = {
            '强势封板': 'strong_seal',
            '洗盘': 'wash',
            '试盘': 'test',
            '诱多出货': 'distribution'
        }
        
        # 用于归一化的得分范围（从所有样本中收集）
        all_scores = {'strong_seal': [], 'test': [], 'wash': [], 'distribution': []}
        
        for (stock_code, date), actual_behavior in zip(self.stock_date_pairs, self.actual_behaviors):
            if self.stop_flag:
                return 0.0 if not return_details else (0.0, {})
            
            try:
                # 从缓存获取预计算的分析结果
                cache_key = (stock_code, str(date))
                if cache_key not in self.analysis_cache:
                    continue  # 分析结果不存在，跳过
                
                cached_result = self.analysis_cache[cache_key]
                
                if not cached_result.get('is_limit_up', False):
                    continue
                
                # 获取原始行为得分
                behaviors = cached_result.get('behaviors', {})
                
                # 收集得分用于归一化
                if params.get('normalize', False):
                    for key in all_scores.keys():
                        score = behaviors.get(key, 0)
                        if score > 0:
                            all_scores[key].append(score)
                
            except Exception as e:
                continue
        
        # 计算归一化系数
        normalize_factors = {}
        if params.get('normalize', False):
            for key in all_scores.keys():
                scores = all_scores[key]
                if scores:
                    max_score = max(scores)
                    min_score = min(scores)
                    normalize_factors[key] = (max_score - min_score) if (max_score - min_score) > 0 else 1.0
                else:
                    normalize_factors[key] = 1.0
        
        # 重新遍历，应用参数并评估
        for (stock_code, date), actual_behavior in zip(self.stock_date_pairs, self.actual_behaviors):
            if self.stop_flag:
                return 0.0 if not return_details else (0.0, {})
            
            try:
                cache_key = (stock_code, str(date))
                if cache_key not in self.analysis_cache:
                    continue
                
                cached_result = self.analysis_cache[cache_key]
                if not cached_result.get('is_limit_up', False):
                    continue
                
                behaviors = cached_result.get('behaviors', {})
                
                # 应用参数调整
                adjusted_behaviors = {}
                
                for behavior_key in ['strong_seal', 'test', 'wash', 'distribution']:
                    score = behaviors.get(behavior_key, 0)
                    
                    # 1. 归一化（如果需要）
                    if params.get('normalize', False) and normalize_factors.get(behavior_key, 1.0) > 0:
                        # 归一化到0-100范围
                        min_val = min(all_scores[behavior_key]) if all_scores[behavior_key] else 0
                        score = ((score - min_val) / normalize_factors[behavior_key]) * 100 if normalize_factors[behavior_key] > 0 else score
                    
                    # 2. 应用权重
                    weight_key = f"{behavior_key}_weight"
                    if weight_key in params:
                        score = score * params[weight_key]
                    
                    # 3. 应用偏移量
                    bias_key = f"{behavior_key}_bias"
                    if bias_key in params:
                        score = score + params[bias_key]
                    
                    adjusted_behaviors[behavior_key] = score
                
                # 确定主导行为
                sorted_behaviors = sorted(adjusted_behaviors.items(), key=lambda x: x[1], reverse=True)
                max_score = sorted_behaviors[0][1] if sorted_behaviors else 0
                
                if max_score == 0:
                    predicted = None
                else:
                    # 检查最小得分差异阈值
                    min_score_diff = params.get('min_score_diff', 0)
                    if min_score_diff > 0 and len(sorted_behaviors) > 1:
                        second_score = sorted_behaviors[1][1]
                        if max_score - second_score < min_score_diff:
                            # 得分差异不够，使用优先级规则
                            priority_order = ['strong_seal', 'distribution', 'test', 'wash']
                            for priority_key in priority_order:
                                if adjusted_behaviors.get(priority_key, 0) > 0:
                                    predicted = priority_key
                                    break
                            else:
                                predicted = sorted_behaviors[0][0]
                        else:
                            predicted = sorted_behaviors[0][0]
                    else:
                        # 没有最小差异阈值，直接选择最高分
                        max_behaviors = [key for key, score in adjusted_behaviors.items() if score == max_score]
                        if len(max_behaviors) > 1:
                            priority_order = ['strong_seal', 'distribution', 'test', 'wash']
                            for priority_key in priority_order:
                                if priority_key in max_behaviors:
                                    predicted = priority_key
                                    break
                            else:
                                predicted = max_behaviors[0]
                        else:
                            predicted = sorted_behaviors[0][0]
                
                expected = behavior_map.get(actual_behavior)
                
                if expected and predicted:
                    confusion_matrix[expected][predicted] = confusion_matrix[expected].get(predicted, 0) + 1
                
                if predicted == expected:
                    correct += 1
                total += 1
                
            except Exception as e:
                continue
        
        if total == 0:
            if return_details:
                return 0.0, {'confusion_matrix': confusion_matrix, 'total': 0}
            return 0.0
        
        accuracy = correct / total
        
        if return_details:
            return accuracy, {
                'confusion_matrix': confusion_matrix,
                'total': total,
                'correct': correct,
                'accuracy': accuracy
            }
        
        return accuracy
    
    def run(self):
        """运行参数优化"""
        try:
            # 第一步：一次性加载所有数据
            loaded_count = self.load_all_data()
            if loaded_count == 0:
                self.progress.emit(0, "错误：没有成功加载任何数据")
                self.result.emit({
                    'params': {},
                    'accuracy': 0.0,
                    'error': '没有成功加载任何数据'
                })
                return
            
            # 第二步：开始参数优化（预计算已在load_all_data中完成）
            self.progress.emit(60, "开始参数优化...")
            
            best_params = None
            best_accuracy = 0.0
            
            # 计算总组合数
            total_combinations = 1
            for param_name, param_range in self.param_ranges.items():
                if isinstance(param_range, (list, tuple)):
                    total_combinations *= len(param_range)
            
            # 限制搜索次数
            if total_combinations > self.max_iterations:
                # 使用随机搜索
                self.progress.emit(50, f"参数组合数过多({total_combinations})，使用随机搜索...")
                iterations = min(self.max_iterations, total_combinations)
                
                for i in range(iterations):
                    if self.stop_flag:
                        break
                    
                    # 随机选择参数
                    params = {}
                    for param_name, param_range in self.param_ranges.items():
                        if isinstance(param_range, (list, tuple)):
                            params[param_name] = np.random.choice(param_range)
                        else:
                            params[param_name] = param_range
                    
                    # 添加全局参数
                    params.update(self.global_params)
                    
                    # 评估参数
                    accuracy = self.evaluate_params(params)
                    
                    if accuracy > best_accuracy:
                        best_accuracy = accuracy
                        best_params = params.copy()
                    
                    progress = 60 + int((i + 1) / iterations * 40)  # 优化占40%进度（60-100%）
                    self.progress.emit(progress, 
                        f"迭代 {i+1}/{iterations}: 当前最佳准确率 {best_accuracy:.2%}")
            else:
                # 使用网格搜索
                self.progress.emit(60, f"使用网格搜索，共 {total_combinations} 种组合...")
                
                param_names = list(self.param_ranges.keys())
                param_values = [self.param_ranges[name] if isinstance(self.param_ranges[name], (list, tuple)) 
                               else [self.param_ranges[name]] for name in param_names]
                
                iteration = 0
                for param_combo in product(*param_values):
                    if self.stop_flag:
                        break
                    
                    params = dict(zip(param_names, param_combo))
                    # 添加全局参数
                    params.update(self.global_params)
                    accuracy = self.evaluate_params(params)
                    
                    if accuracy > best_accuracy:
                        best_accuracy = accuracy
                        best_params = params.copy()
                    
                    iteration += 1
                    progress = 60 + int(iteration / total_combinations * 40)  # 优化占40%进度（60-100%）
                    self.progress.emit(progress, 
                        f"组合 {iteration}/{total_combinations}: 当前最佳准确率 {best_accuracy:.2%}")
            
            if best_params:
                # 获取最优参数的详细评估结果（包括混淆矩阵）
                accuracy, details = self.evaluate_params(best_params, return_details=True)
                self.result.emit({
                    'params': best_params,
                    'accuracy': best_accuracy,
                    'confusion_matrix': details.get('confusion_matrix', {}),
                    'total': details.get('total', 0),
                    'correct': details.get('correct', 0)
                })
            else:
                self.result.emit({
                    'params': {},
                    'accuracy': 0.0,
                    'confusion_matrix': {},
                    'total': 0,
                    'correct': 0
                })
                
        except Exception as e:
            import traceback
            self.progress.emit(0, f"优化过程出错: {str(e)}\n{traceback.format_exc()}")
            self.result.emit({
                'params': {},
                'accuracy': 0.0,
                'error': str(e)
            })


class ParameterOptimizerWindow(QMainWindow):
    """参数优化主窗口"""
    
    def __init__(self):
        super().__init__()
        self.stock_date_pairs = []
        self.actual_behaviors = []
        self.optimization_thread = None
        self.init_ui()
        # 自动加载 test.txt 文件（如果存在）
        self.auto_load_test_file()
    
    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle("涨停板主力行为分析 - 参数优化工具")
        self.setGeometry(100, 100, 1400, 900)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        
        # 文件输入区域
        file_group = QGroupBox("输入文件")
        file_layout = QVBoxLayout()
        
        file_btn_layout = QHBoxLayout()
        self.load_file_btn = QPushButton("加载数据文件")
        self.load_file_btn.clicked.connect(self.load_data_file)
        file_btn_layout.addWidget(self.load_file_btn)
        file_btn_layout.addStretch()
        
        self.file_label = QLabel("未加载文件")
        file_layout.addLayout(file_btn_layout)
        file_layout.addWidget(self.file_label)
        
        file_group.setLayout(file_layout)
        layout.addWidget(file_group)
        
        # 参数范围设置区域
        param_group = QGroupBox("参数搜索范围")
        param_layout = QVBoxLayout()
        
        # 创建参数表格
        self.param_table = QTableWidget()
        self.param_table.setColumnCount(4)
        self.param_table.setHorizontalHeaderLabels(["参数名", "最小值", "最大值", "步长"])
        self.param_table.horizontalHeader().setStretchLastSection(True)
        
        # 初始化默认参数范围（权重参数）
        self.init_param_table()
        
        param_layout.addWidget(self.param_table)
        
        # 全局参数设置
        global_param_layout = QHBoxLayout()
        global_param_layout.addWidget(QLabel("得分归一化:"))
        self.normalize_checkbox = QCheckBox()
        self.normalize_checkbox.setChecked(False)
        global_param_layout.addWidget(self.normalize_checkbox)
        
        global_param_layout.addWidget(QLabel("最小得分差异阈值:"))
        self.min_score_diff_spin = QDoubleSpinBox()
        self.min_score_diff_spin.setMinimum(0.0)
        self.min_score_diff_spin.setMaximum(100.0)
        self.min_score_diff_spin.setValue(0.0)
        self.min_score_diff_spin.setSingleStep(1.0)
        global_param_layout.addWidget(self.min_score_diff_spin)
        
        global_param_layout.addStretch()
        param_layout.addLayout(global_param_layout)
        
        param_group.setLayout(param_layout)
        layout.addWidget(param_group)
        
        # 优化设置
        opt_group = QGroupBox("优化设置")
        opt_layout = QHBoxLayout()
        
        opt_layout.addWidget(QLabel("最大迭代次数:"))
        self.max_iter_spin = QSpinBox()
        self.max_iter_spin.setMinimum(10)
        self.max_iter_spin.setMaximum(10000)
        self.max_iter_spin.setValue(500)
        opt_layout.addWidget(self.max_iter_spin)
        
        opt_layout.addStretch()
        opt_group.setLayout(opt_layout)
        layout.addWidget(opt_group)
        
        # 控制按钮
        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("开始优化")
        self.start_btn.clicked.connect(self.start_optimization)
        self.start_btn.setEnabled(False)
        
        self.stop_btn = QPushButton("停止优化")
        self.stop_btn.clicked.connect(self.stop_optimization)
        self.stop_btn.setEnabled(False)
        
        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.stop_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        # 进度条
        self.progress_bar = QProgressBar()
        layout.addWidget(self.progress_bar)
        
        # 日志输出
        log_group = QGroupBox("优化日志")
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        layout.addWidget(log_group)
        
        # 结果显示
        result_group = QGroupBox("最优参数结果")
        result_layout = QVBoxLayout()
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setFont(QFont("Consolas", 9))
        result_layout.addWidget(self.result_text)
        result_group.setLayout(result_layout)
        layout.addWidget(result_group)
        
        # 混淆矩阵显示
        confusion_group = QGroupBox("混淆矩阵分析")
        confusion_layout = QVBoxLayout()
        self.confusion_text = QTextEdit()
        self.confusion_text.setReadOnly(True)
        self.confusion_text.setFont(QFont("Consolas", 9))
        confusion_layout.addWidget(self.confusion_text)
        confusion_group.setLayout(confusion_layout)
        layout.addWidget(confusion_group)
        
        # 原始得分分析
        score_analysis_group = QGroupBox("原始得分分析")
        score_analysis_layout = QVBoxLayout()
        
        score_analysis_btn_layout = QHBoxLayout()
        self.analyze_scores_btn = QPushButton("分析原始得分")
        self.analyze_scores_btn.clicked.connect(self.analyze_raw_scores)
        self.analyze_scores_btn.setEnabled(False)
        
        self.diagnose_thresholds_btn = QPushButton("诊断阈值满足情况")
        self.diagnose_thresholds_btn.clicked.connect(self.diagnose_thresholds)
        self.diagnose_thresholds_btn.setEnabled(False)
        
        score_analysis_btn_layout.addWidget(self.analyze_scores_btn)
        score_analysis_btn_layout.addWidget(self.diagnose_thresholds_btn)
        score_analysis_btn_layout.addStretch()
        
        self.score_analysis_text = QTextEdit()
        self.score_analysis_text.setReadOnly(True)
        self.score_analysis_text.setFont(QFont("Consolas", 9))
        
        score_analysis_layout.addLayout(score_analysis_btn_layout)
        score_analysis_layout.addWidget(self.score_analysis_text)
        score_analysis_group.setLayout(score_analysis_layout)
        layout.addWidget(score_analysis_group)
    
    def init_param_table(self):
        """初始化参数表格"""
        default_params = [
            # 权重参数
            ("strong_seal_weight", 0.5, 2.0, 0.1),  # 强势封板权重
            ("test_weight", 0.5, 2.0, 0.1),        # 试盘权重
            ("wash_weight", 0.5, 2.0, 0.1),        # 洗盘权重
            ("distribution_weight", 0.5, 2.0, 0.1), # 诱多出货权重
            # 偏移量参数
            ("strong_seal_bias", -20.0, 20.0, 5.0),  # 强势封板偏移量
            ("test_bias", -20.0, 20.0, 5.0),        # 试盘偏移量
            ("wash_bias", -20.0, 20.0, 5.0),        # 洗盘偏移量
            ("distribution_bias", -20.0, 20.0, 5.0), # 诱多出货偏移量
        ]
        
        self.param_table.setRowCount(len(default_params))
        for i, (name, min_val, max_val, step) in enumerate(default_params):
            self.param_table.setItem(i, 0, QTableWidgetItem(name))
            self.param_table.setItem(i, 1, QTableWidgetItem(str(min_val)))
            self.param_table.setItem(i, 2, QTableWidgetItem(str(max_val)))
            self.param_table.setItem(i, 3, QTableWidgetItem(str(step)))
    
    def auto_load_test_file(self):
        """自动加载 test.txt 文件"""
        import os
        test_file = os.path.join(os.getcwd(), "test.txt")
        if os.path.exists(test_file):
            self.load_data_from_file(test_file)
        else:
            # 如果当前目录没有，尝试在 tools 目录下查找
            tools_test_file = os.path.join(os.path.dirname(__file__), "test.txt")
            if os.path.exists(tools_test_file):
                self.load_data_from_file(tools_test_file)
    
    def load_data_file(self):
        """加载数据文件（通过文件对话框）"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择数据文件", "", "文本文件 (*.txt);;所有文件 (*.*)"
        )
        
        if not file_path:
            return
        
        self.load_data_from_file(file_path)
    
    def load_data_from_file(self, file_path):
        """从指定文件路径加载数据"""
        
        try:
            self.stock_date_pairs = []
            self.actual_behaviors = []
            
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    parts = line.split('\t')
                    if len(parts) >= 3:
                        behavior = parts[0]
                        stock_code = parts[1]
                        date_str = parts[2]
                        
                        try:
                            date = datetime.strptime(date_str, '%Y-%m-%d').date()
                            self.actual_behaviors.append(behavior)
                            self.stock_date_pairs.append((stock_code, date))
                        except:
                            continue
            
            self.file_label.setText(f"已加载 {len(self.stock_date_pairs)} 条数据")
            self.start_btn.setEnabled(len(self.stock_date_pairs) > 0)
            self.analyze_scores_btn.setEnabled(len(self.stock_date_pairs) > 0)
            self.diagnose_thresholds_btn.setEnabled(len(self.stock_date_pairs) > 0)
            self.log(f"成功加载 {len(self.stock_date_pairs)} 条数据")
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载文件失败: {str(e)}")
    
    def get_param_ranges(self) -> Dict:
        """从表格获取参数范围"""
        param_ranges = {}
        
        for i in range(self.param_table.rowCount()):
            name_item = self.param_table.item(i, 0)
            min_item = self.param_table.item(i, 1)
            max_item = self.param_table.item(i, 2)
            step_item = self.param_table.item(i, 3)
            
            if not all([name_item, min_item, max_item, step_item]):
                continue
            
            try:
                name = name_item.text().strip()
                min_val = float(min_item.text())
                max_val = float(max_item.text())
                step = float(step_item.text())
                
                # 生成参数值列表
                values = []
                current = min_val
                while current <= max_val:
                    values.append(round(current, 3))
                    current += step
                
                param_ranges[name] = values
            except:
                continue
        
        return param_ranges
    
    def start_optimization(self):
        """开始优化"""
        if not self.stock_date_pairs:
            QMessageBox.warning(self, "警告", "请先加载数据文件")
            return
        
        param_ranges = self.get_param_ranges()
        if not param_ranges:
            QMessageBox.warning(self, "警告", "请设置参数搜索范围")
            return
        
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.result_text.clear()
        self.confusion_text.clear()
        
        max_iter = self.max_iter_spin.value()
        
        # 获取全局参数
        global_params = {
            'normalize': self.normalize_checkbox.isChecked(),
            'min_score_diff': self.min_score_diff_spin.value()
        }
        
        self.optimization_thread = OptimizationThread(
            self.stock_date_pairs,
            self.actual_behaviors,
            param_ranges,
            max_iter,
            global_params
        )
        self.optimization_thread.progress.connect(self.on_progress)
        self.optimization_thread.result.connect(self.on_result)
        self.optimization_thread.start()
        
        self.log("开始参数优化...")
    
    def stop_optimization(self):
        """停止优化"""
        if self.optimization_thread:
            self.optimization_thread.stop()
            self.optimization_thread.wait()
            self.log("优化已停止")
        
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
    
    def on_progress(self, progress: int, message: str):
        """更新进度"""
        self.progress_bar.setValue(progress)
        self.log(message)
    
    def on_result(self, result: Dict):
        """处理优化结果"""
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        
        if result.get('error'):
            self.log(f"优化失败: {result['error']}")
            return
        
        accuracy = result.get('accuracy', 0.0)
        params = result.get('params', {})
        confusion_matrix = result.get('confusion_matrix', {})
        total = result.get('total', 0)
        correct = result.get('correct', 0)
        
        self.log(f"\n优化完成！")
        self.log(f"最佳准确率: {accuracy:.2%}")
        self.log(f"正确: {correct}/{total}")
        
        # 显示最优参数
        result_text = "最优参数组合:\n\n"
        for param_name, param_value in sorted(params.items()):
            result_text += f"{param_name}: {param_value}\n"
        
        result_text += f"\n准确率: {accuracy:.2%}\n"
        result_text += f"正确: {correct}/{total}\n"
        result_text += f"\n参数JSON格式:\n{json.dumps(params, indent=2, ensure_ascii=False)}"
        
        self.result_text.setText(result_text)
        
        # 显示混淆矩阵
        self.display_confusion_matrix(confusion_matrix, total)
    
    def display_confusion_matrix(self, confusion_matrix: Dict, total: int):
        """显示混淆矩阵"""
        if not confusion_matrix:
            self.confusion_text.setText("无混淆矩阵数据")
            return
        
        behavior_names = {
            'strong_seal': '强势封板',
            'test': '试盘',
            'wash': '洗盘',
            'distribution': '诱多出货'
        }
        
        behavior_keys = ['strong_seal', 'test', 'wash', 'distribution']
        
        # 构建表格
        text = "混淆矩阵 (行=实际行为, 列=预测行为):\n\n"
        header = "实际/预测"
        text += f"{header:<12}"
        for pred_key in behavior_keys:
            text += f"{behavior_names[pred_key]:<12}"
        text += "\n" + "-" * 60 + "\n"
        
        for actual_key in behavior_keys:
            text += f"{behavior_names[actual_key]:<12}"
            row_total = sum(confusion_matrix.get(actual_key, {}).values())
            for pred_key in behavior_keys:
                count = confusion_matrix.get(actual_key, {}).get(pred_key, 0)
                if row_total > 0:
                    percentage = (count / row_total) * 100
                    cell_text = f"{count}({percentage:.1f}%)"
                    text += f"{cell_text:<12}"
                else:
                    text += f"{count:<12}"
            text += f" 总计: {row_total}\n"
        
        text += "\n" + "=" * 60 + "\n"
        text += "分析说明:\n"
        text += "- 对角线上的数字表示正确分类的数量\n"
        text += "- 非对角线上的数字表示错误分类的数量\n"
        text += "- 百分比表示该预测行为占该实际行为的比例\n"
        
        # 计算每个行为的准确率
        text += "\n各行为识别准确率:\n"
        for actual_key in behavior_keys:
            correct = confusion_matrix.get(actual_key, {}).get(actual_key, 0)
            row_total = sum(confusion_matrix.get(actual_key, {}).values())
            if row_total > 0:
                acc = (correct / row_total) * 100
                text += f"{behavior_names[actual_key]}: {correct}/{row_total} = {acc:.1f}%\n"
        
        self.confusion_text.setText(text)
    
    def analyze_raw_scores(self):
        """分析原始得分分布"""
        if not self.stock_date_pairs:
            QMessageBox.warning(self, "警告", "请先加载数据文件")
            return
        
        self.score_analysis_text.clear()
        self.score_analysis_text.append("正在分析原始得分，请稍候...")
        
        try:
            # 需要先加载数据并进行分析
            from core.stock_analyzer import StockAnalyzer
            from core.backtest_engine import BacktestEngine
            from key_price_calculator import KeyPriceCalculator
            from utils.logger import Logger
            
            logger = Logger()
            analyzer = StockAnalyzer()
            key_price_calculator = KeyPriceCalculator()
            
            behavior_map = {
                '强势封板': 'strong_seal',
                '洗盘': 'wash',
                '试盘': 'test',
                '诱多出货': 'distribution'
            }
            
            # 收集所有样本的原始得分
            all_scores = {
                'strong_seal': {'强势封板': [], '试盘': [], '洗盘': [], '诱多出货': []},
                'test': {'强势封板': [], '试盘': [], '洗盘': [], '诱多出货': []},
                'wash': {'强势封板': [], '试盘': [], '洗盘': [], '诱多出货': []},
                'distribution': {'强势封板': [], '试盘': [], '洗盘': [], '诱多出货': []}
            }
            
            loaded_count = 0
            for stock_code, date in self.stock_date_pairs:
                try:
                    # 转换日期格式
                    if isinstance(date, str):
                        analysis_date = datetime.strptime(date, '%Y-%m-%d').date()
                    else:
                        analysis_date = date
                    
                    # 获取tick数据
                    engine = BacktestEngine(stock_code=stock_code)
                    engine.set_logger(logger)
                    success = engine.load_data(analysis_date, analysis_date)
                    
                    if not success or engine.data is None or engine.data.empty:
                        continue
                    
                    tick_data = engine.data
                    
                    # 获取日线数据
                    formatted_stock_code = stock_code
                    if not stock_code.endswith(('.SH', '.SZ', '.BJ')):
                        if stock_code.startswith(('0', '1', '3')):
                            formatted_stock_code = f"{stock_code}.SZ"
                        elif stock_code.startswith(('5', '6')):
                            formatted_stock_code = f"{stock_code}.SH"
                        elif stock_code.startswith(('4', '8', '920')):
                            formatted_stock_code = f"{stock_code}.BJ"
                    
                    daily_data = key_price_calculator._get_qmt_daily_data(formatted_stock_code)
                    if daily_data.empty:
                        continue
                    
                    # 分析
                    result = analyzer.analyze_limit_up_behavior_comprehensive(
                        tick_data, daily_data, stock_code, None
                    )
                    
                    if not result.get('is_limit_up', False):
                        continue
                    
                    behaviors = result.get('behaviors', {})
                    actual_behavior = self.actual_behaviors[loaded_count] if loaded_count < len(self.actual_behaviors) else None
                    
                    if actual_behavior:
                        # 收集每个行为的得分
                        for behavior_key in ['strong_seal', 'test', 'wash', 'distribution']:
                            score = behaviors.get(behavior_key, 0)
                            all_scores[behavior_key][actual_behavior].append(score)
                    
                    loaded_count += 1
                    
                    if loaded_count % 5 == 0:
                        self.score_analysis_text.clear()
                        self.score_analysis_text.append(f"已分析 {loaded_count}/{len(self.stock_date_pairs)} 条数据...")
                        QApplication.processEvents()
                    
                except Exception as e:
                    continue
            
            # 显示分析结果
            self.display_score_analysis(all_scores)
            
        except Exception as e:
            import traceback
            self.score_analysis_text.setText(f"分析失败: {str(e)}\n{traceback.format_exc()}")
    
    def display_score_analysis(self, all_scores: Dict):
        """显示得分分析结果"""
        behavior_names = {
            'strong_seal': '强势封板',
            'test': '试盘',
            'wash': '洗盘',
            'distribution': '诱多出货'
        }
        
        text = "=" * 80 + "\n"
        text += "原始得分分析报告\n"
        text += "=" * 80 + "\n\n"
        
        # 对每个行为类型，显示在不同实际行为样本中的得分分布
        for behavior_key, behavior_name in behavior_names.items():
            text += f"\n【{behavior_name}】原始得分分布：\n"
            text += "-" * 80 + "\n"
            
            for actual_behavior in ['强势封板', '试盘', '洗盘', '诱多出货']:
                scores = all_scores[behavior_key][actual_behavior]
                if not scores:
                    continue
                
                scores_array = np.array(scores)
                text += f"\n  实际行为: {actual_behavior} (样本数: {len(scores)})\n"
                text += f"    最小值: {scores_array.min():.2f}\n"
                text += f"    最大值: {scores_array.max():.2f}\n"
                text += f"    平均值: {scores_array.mean():.2f}\n"
                text += f"    中位数: {np.median(scores_array):.2f}\n"
                text += f"    标准差: {scores_array.std():.2f}\n"
                if len(scores) > 0:
                    text += f"    得分列表: {sorted(scores, reverse=True)}\n"
        
        # 特别分析强势封板
        text += "\n" + "=" * 80 + "\n"
        text += "【强势封板】详细分析：\n"
        text += "-" * 80 + "\n"
        
        strong_seal_scores = all_scores['strong_seal']['强势封板']
        if strong_seal_scores:
            text += f"\n强势封板样本中，强势封板得分：\n"
            text += f"  样本数: {len(strong_seal_scores)}\n"
            text += f"  得分范围: {min(strong_seal_scores):.2f} ~ {max(strong_seal_scores):.2f}\n"
            text += f"  平均得分: {np.mean(strong_seal_scores):.2f}\n"
            text += f"  中位数得分: {np.median(strong_seal_scores):.2f}\n"
            text += f"  得分列表: {sorted(strong_seal_scores, reverse=True)}\n"
        
        # 对比其他行为在强势封板样本中的得分
        text += f"\n强势封板样本中，其他行为的得分对比：\n"
        for other_key, other_name in behavior_names.items():
            if other_key == 'strong_seal':
                continue
            other_scores = all_scores[other_key]['强势封板']
            if other_scores:
                text += f"  {other_name}: 平均 {np.mean(other_scores):.2f}, 最大 {max(other_scores):.2f}, 得分列表 {sorted(other_scores, reverse=True)}\n"
        
        # 分析为什么强势封板被误判为诱多出货
        text += "\n" + "=" * 80 + "\n"
        text += "【误判分析】强势封板被误判为诱多出货的原因：\n"
        text += "-" * 80 + "\n"
        
        strong_seal_distribution_scores = all_scores['distribution']['强势封板']
        if strong_seal_distribution_scores and strong_seal_scores:
            text += f"\n在强势封板样本中：\n"
            text += f"  强势封板平均得分: {np.mean(strong_seal_scores):.2f}\n"
            text += f"  诱多出货平均得分: {np.mean(strong_seal_distribution_scores):.2f}\n"
            text += f"  得分差异: {np.mean(strong_seal_distribution_scores) - np.mean(strong_seal_scores):.2f}\n"
            if np.mean(strong_seal_distribution_scores) > np.mean(strong_seal_scores):
                text += f"  ⚠️ 问题：诱多出货得分高于强势封板得分！\n"
            else:
                text += f"  ✓ 强势封板得分高于诱多出货得分\n"
        
        self.score_analysis_text.setText(text)
    
    def diagnose_thresholds(self):
        """诊断阈值满足情况，特别关注强势封板样本"""
        if not self.stock_date_pairs:
            QMessageBox.warning(self, "警告", "请先加载数据文件")
            return
        
        self.score_analysis_text.clear()
        self.score_analysis_text.append("正在诊断阈值满足情况，请稍候...")
        
        try:
            from core.stock_analyzer import StockAnalyzer
            from core.backtest_engine import BacktestEngine
            from key_price_calculator import KeyPriceCalculator
            from utils.logger import Logger
            
            logger = Logger()
            analyzer = StockAnalyzer()
            key_price_calculator = KeyPriceCalculator()
            
            text = "=" * 80 + "\n"
            text += "阈值满足情况诊断报告（重点关注强势封板样本）\n"
            text += "=" * 80 + "\n\n"
            
            strong_seal_count = 0
            for idx, (stock_code, date) in enumerate(self.stock_date_pairs):
                actual_behavior = self.actual_behaviors[idx] if idx < len(self.actual_behaviors) else None
                
                # 只诊断强势封板样本
                if actual_behavior != '强势封板':
                    continue
                
                strong_seal_count += 1
                try:
                    # 转换日期格式
                    if isinstance(date, str):
                        analysis_date = datetime.strptime(date, '%Y-%m-%d').date()
                    else:
                        analysis_date = date
                    
                    # 获取tick数据
                    engine = BacktestEngine(stock_code=stock_code)
                    engine.set_logger(logger)
                    success = engine.load_data(analysis_date, analysis_date)
                    
                    if not success or engine.data is None or engine.data.empty:
                        continue
                    
                    tick_data = engine.data
                    
                    # 获取日线数据
                    formatted_stock_code = stock_code
                    if not stock_code.endswith(('.SH', '.SZ', '.BJ')):
                        if stock_code.startswith(('0', '1', '3')):
                            formatted_stock_code = f"{stock_code}.SZ"
                        elif stock_code.startswith(('5', '6')):
                            formatted_stock_code = f"{stock_code}.SH"
                        elif stock_code.startswith(('4', '8', '920')):
                            formatted_stock_code = f"{stock_code}.BJ"
                    
                    daily_data = key_price_calculator._get_qmt_daily_data(formatted_stock_code)
                    if daily_data.empty:
                        continue
                    
                    # 先调用分析方法，这会添加 is_limit_up 列（方法会修改传入的DataFrame）
                    # 使用副本避免修改原始数据
                    tick_data_copy = tick_data.copy()
                    result = analyzer.analyze_limit_up_behavior_comprehensive(
                        tick_data_copy, daily_data, stock_code, None
                    )
                    
                    if not result.get('is_limit_up', False):
                        text += f"\n【样本 {strong_seal_count}】{stock_code} {date}\n"
                        text += "-" * 80 + "\n"
                        text += f"⚠️ 该样本不是涨停板，跳过诊断\n"
                        text += "\n" + "=" * 80 + "\n"
                        continue
                    
                    # 获取涨停数据（使用修改后的副本）
                    limit_up_data = tick_data_copy[tick_data_copy['is_limit_up'] == True]
                    if limit_up_data.empty:
                        text += f"\n【样本 {strong_seal_count}】{stock_code} {date}\n"
                        text += "-" * 80 + "\n"
                        text += f"⚠️ 无法获取涨停数据，跳过诊断\n"
                        text += "\n" + "=" * 80 + "\n"
                        continue
                    
                    # 计算阈值
                    small_seal_threshold = analyzer._get_relative_seal_threshold(stock_code, 20.0)
                    medium_seal_threshold_low = analyzer._get_relative_seal_threshold(stock_code, 50.0)
                    medium_seal_threshold_high = analyzer._get_relative_seal_threshold(stock_code, 100.0)
                    large_seal_threshold = analyzer._get_relative_seal_threshold(stock_code, 100.0)
                    big_seal_threshold = analyzer._get_relative_seal_threshold(stock_code, 200.0)
                    huge_seal_threshold = analyzer._get_relative_seal_threshold(stock_code, 500.0)
                    
                    # 获取总股本
                    total_shares_wan = analyzer._get_stock_total_shares(stock_code)
                    
                    # 计算实际值
                    bid_volumes = limit_up_data.apply(lambda row: row['bidVol'][0] if isinstance(row['bidVol'], list) and len(row['bidVol']) > 0 else 0, axis=1)
                    avg_bid1_vol = bid_volumes.mean() if len(bid_volumes) > 0 else 0
                    
                    # 计算涨停期间的平均单笔成交量（使用差值，因为volume是累计值）
                    if len(limit_up_data) > 1:
                        volume_diff = limit_up_data['volume'].diff().fillna(0)
                        avg_volume = volume_diff.mean() if len(volume_diff) > 0 else 0
                    elif len(limit_up_data) == 1:
                        avg_volume = limit_up_data['volume'].iloc[0]
                    else:
                        avg_volume = 0
                    
                    volume_ratio = (avg_volume / avg_bid1_vol * 100) if avg_bid1_vol > 0 else 0  # 转换为百分比
                    
                    # 计算封单稳定性
                    bid_volumes_all = limit_up_data.apply(lambda row: sum(row['bidVol']) if isinstance(row['bidVol'], (list, tuple, np.ndarray)) else 0, axis=1)
                    if len(bid_volumes_all) > 0:
                        vol_std = bid_volumes_all.std()
                        vol_mean = bid_volumes_all.mean()
                        volatility_ratio = (vol_std / vol_mean) if vol_mean > 0 else 1.0
                    else:
                        volatility_ratio = 1.0
                    
                    # 封板时间
                    if len(limit_up_data) > 0 and len(tick_data) > 0:
                        first_limit_up_time = limit_up_data.index[0]
                        tick_start_time = tick_data.index[0]
                        if isinstance(first_limit_up_time, pd.Timestamp) and isinstance(tick_start_time, pd.Timestamp):
                            time_to_limit = (first_limit_up_time - tick_start_time).total_seconds() / 60
                        else:
                            time_to_limit = None
                    else:
                        time_to_limit = None
                    
                    # 获取得分（已经在上面调用过了）
                    strong_seal_score = result.get('behaviors', {}).get('strong_seal', 0)
                    
                    # 输出诊断信息
                    text += f"\n【样本 {strong_seal_count}】{stock_code} {date}\n"
                    text += "-" * 80 + "\n"
                    text += f"总流通股: {total_shares_wan:.2f} 万股\n"
                    text += f"实际买一量: {avg_bid1_vol:.0f} 手\n"
                    text += f"实际成交量(平均单笔): {avg_volume:.0f} 手\n"
                    text += f"成交量占比: {volume_ratio:.2f}%\n"
                    text += f"封单波动率: {volatility_ratio:.2%}\n"
                    if time_to_limit is not None:
                        text += f"封板时间: {time_to_limit:.1f} 分钟\n"
                    text += f"\n阈值设置（相对阈值，已根据总股本调整）:\n"
                    text += f"  小封单阈值(20万股基础): {small_seal_threshold:.0f} 手\n"
                    text += f"  中等封单阈值(50万股基础): {medium_seal_threshold_low:.0f} 手\n"
                    text += f"  中等封单阈值(100万股基础): {medium_seal_threshold_high:.0f} 手\n"
                    text += f"  大封单阈值(100万股基础): {large_seal_threshold:.0f} 手\n"
                    text += f"  大封单阈值(200万股基础): {big_seal_threshold:.0f} 手\n"
                    text += f"  超大封单阈值(500万股基础): {huge_seal_threshold:.0f} 手\n"
                    
                    text += f"\n阈值满足情况:\n"
                    # 检查各个阈值
                    if avg_bid1_vol >= huge_seal_threshold:
                        text += f"  ✓ 满足超大封单阈值 ({avg_bid1_vol:.0f} >= {huge_seal_threshold:.0f})\n"
                    elif avg_bid1_vol >= big_seal_threshold:
                        text += f"  ✓ 满足大封单阈值 ({avg_bid1_vol:.0f} >= {big_seal_threshold:.0f})\n"
                    elif avg_bid1_vol >= large_seal_threshold:
                        text += f"  ✓ 满足大封单阈值 ({avg_bid1_vol:.0f} >= {large_seal_threshold:.0f})\n"
                    elif avg_bid1_vol >= medium_seal_threshold_high:
                        text += f"  ✓ 满足中等封单阈值 ({avg_bid1_vol:.0f} >= {medium_seal_threshold_high:.0f})\n"
                    elif avg_bid1_vol >= medium_seal_threshold_low:
                        text += f"  ✓ 满足中等封单阈值 ({avg_bid1_vol:.0f} >= {medium_seal_threshold_low:.0f})\n"
                    elif avg_bid1_vol >= small_seal_threshold:
                        text += f"  ✓ 满足小封单阈值 ({avg_bid1_vol:.0f} >= {small_seal_threshold:.0f})\n"
                    else:
                        text += f"  ✗ 不满足任何封单阈值 ({avg_bid1_vol:.0f} < {small_seal_threshold:.0f})\n"
                    
                    # 检查成交量占比（volume_ratio已经是百分比）
                    if volume_ratio < 10:
                        text += f"  ✓ 成交量占比很小 ({volume_ratio:.2f}% < 10%)\n"
                    elif volume_ratio < 15:
                        text += f"  ✓ 成交量占比小 ({volume_ratio:.2f}% < 15%)\n"
                    elif volume_ratio < 20:
                        text += f"  ✓ 成交量占比较小 ({volume_ratio:.2f}% < 20%)\n"
                    elif volume_ratio < 25:
                        text += f"  ✓ 成交量占比中等 ({volume_ratio:.2f}% < 25%)\n"
                    elif volume_ratio < 35:
                        text += f"  ⚠ 成交量占比较大 ({volume_ratio:.2f}% < 35%)\n"
                    elif volume_ratio < 40:
                        text += f"  ⚠ 成交量占比大 ({volume_ratio:.2f}% < 40%)\n"
                    else:
                        text += f"  ✗ 成交量占比很大 ({volume_ratio:.2f}% >= 40%)\n"
                    
                    # 检查封单稳定性
                    if volatility_ratio <= 0.20:
                        text += f"  ✓ 封单非常稳定 (波动率 {volatility_ratio:.2%} <= 20%)\n"
                    elif volatility_ratio <= 0.30:
                        text += f"  ✓ 封单较稳定 (波动率 {volatility_ratio:.2%} <= 30%)\n"
                    elif volatility_ratio <= 0.50:
                        text += f"  ⚠ 封单不够稳定 (波动率 {volatility_ratio:.2%} <= 50%)\n"
                    else:
                        text += f"  ✗ 封单不稳定 (波动率 {volatility_ratio:.2%} > 50%)\n"
                    
                    # 检查封板时间
                    if time_to_limit is not None:
                        if time_to_limit <= 30:
                            text += f"  ✓ 封板速度快 ({time_to_limit:.1f} 分钟 <= 30分钟)\n"
                        elif time_to_limit <= 60:
                            text += f"  ⚠ 封板速度较快 ({time_to_limit:.1f} 分钟 <= 60分钟)\n"
                        else:
                            text += f"  ✗ 封板速度慢 ({time_to_limit:.1f} 分钟 > 60分钟)\n"
                    
                    text += f"\n最终得分: {strong_seal_score} 分\n"
                    text += "\n" + "=" * 80 + "\n"
                    
                    if strong_seal_count % 3 == 0:
                        self.score_analysis_text.setText(text)
                        QApplication.processEvents()
                    
                except Exception as e:
                    import traceback
                    text += f"\n【样本 {strong_seal_count}】{stock_code} {date} - 分析失败: {str(e)}\n"
                    text += traceback.format_exc() + "\n"
                    continue
            
            text += f"\n诊断完成，共分析了 {strong_seal_count} 个强势封板样本\n"
            self.score_analysis_text.setText(text)
            
        except Exception as e:
            import traceback
            self.score_analysis_text.setText(f"诊断失败: {str(e)}\n{traceback.format_exc()}")
    
    def log(self, message: str):
        """添加日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")


def main():
    app = QApplication(sys.argv)
    window = ParameterOptimizerWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
