"""
主力三大行为参数优化工具
自动搜索最优参数组合，提高识别准确率
支持：主力出货或洗盘、主力拉升、主力吸筹
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
import platform

# 导入声音播放模块
try:
    if platform.system() == 'Windows':
        import winsound
    else:
        winsound = None
except ImportError:
    winsound = None

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


class ParameterizedMainForceAnalyzer:
    """参数化的主力行为分析器"""
    
    def __init__(self, base_analyzer: StockAnalyzer):
        self.base_analyzer = base_analyzer
    
    def analyze_with_params(self, tick_data: pd.DataFrame, daily_data: pd.DataFrame,
                           stock_code: str, analysis_date: str, params: Dict) -> Dict:
        """使用自定义参数进行分析"""
        
        # 分析三种行为
        distribution_result = self.base_analyzer.analyze_high_level_distribution_or_wash_comprehensive(
            daily_data, tick_data, stock_code, analysis_date
        )
        lift_result = self.base_analyzer.analyze_main_force_lift_comprehensive(
            daily_data, tick_data, stock_code, analysis_date
        )
        accumulation_result = self.base_analyzer.analyze_low_level_accumulation_comprehensive(
            daily_data, tick_data, stock_code, analysis_date
        )
        
        # 获取原始得分
        distribution_score = distribution_result.get('total_score', 0)
        lift_score = lift_result.get('total_score', 0)
        accumulation_score = accumulation_result.get('total_score', 0)
        
        # 应用权重和偏移（硬编码优化后的参数）
        distribution_weight = params.get('distribution_weight', 0.5)  # 优化值：0.5
        distribution_bias = params.get('distribution_bias', 30.0)  # 优化值：30.0
        adjusted_distribution = distribution_score * distribution_weight + distribution_bias
        
        lift_weight = params.get('lift_weight', 1.3)  # 优化值：1.3
        lift_bias = params.get('lift_bias', 20.0)  # 优化值：20.0
        adjusted_lift = lift_score * lift_weight + lift_bias
        
        accumulation_weight = params.get('accumulation_weight', 1.0)  # 优化值：1.0
        accumulation_bias = params.get('accumulation_bias', 15.0)  # 优化值：15.0
        adjusted_accumulation = accumulation_score * accumulation_weight + accumulation_bias
        
        # 应用阈值判断（硬编码优化后的参数）
        distribution_threshold = params.get('distribution_threshold', 30.0)  # 优化值：30.0
        lift_threshold = params.get('lift_threshold', 25.0)  # 优化值：25.0
        accumulation_threshold = params.get('accumulation_threshold', 25.0)  # 优化值：25.0
        
        # 确定主导行为
        scores = {
            'distribution': adjusted_distribution if adjusted_distribution >= distribution_threshold else 0,
            'lift': adjusted_lift if adjusted_lift >= lift_threshold else 0,
            'accumulation': adjusted_accumulation if adjusted_accumulation >= accumulation_threshold else 0
        }
        
        # 如果启用归一化，将得分归一化到0-100
        if params.get('normalize', False):
            max_score = max(scores.values())
            if max_score > 0:
                for key in scores:
                    if scores[key] > 0:
                        scores[key] = (scores[key] / max_score) * 100
        
        # 确定主导行为
        max_score = max(scores.values())
        if max_score == 0:
            dominant_behavior = None
        else:
            # 需要最小分差
            min_score_diff = params.get('min_score_diff', 0.0)
            max_behaviors = [key for key, score in scores.items() if score == max_score]
            if len(max_behaviors) == 1:
                # 检查是否满足最小分差
                second_max = max([scores[k] for k in scores.keys() if k != max_behaviors[0]])
                if max_score - second_max >= min_score_diff:
                    dominant_behavior = max_behaviors[0]
                else:
                    dominant_behavior = None
            else:
                # 多个行为得分相同，按优先级选择
                priority_order = ['distribution', 'lift', 'accumulation']
                for priority_key in priority_order:
                    if priority_key in max_behaviors:
                        dominant_behavior = priority_key
                        break
                else:
                    dominant_behavior = max_behaviors[0]
        
        return {
            'dominant_behavior': dominant_behavior,
            'scores': scores,
            'raw_scores': {
                'distribution': distribution_score,
                'lift': lift_score,
                'accumulation': accumulation_score
            },
            'adjusted_scores': {
                'distribution': adjusted_distribution,
                'lift': adjusted_lift,
                'accumulation': adjusted_accumulation
            }
        }


class OptimizationThread(QThread):
    """参数优化线程"""
    
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished = pyqtSignal(dict)
    
    def __init__(self, data_list: List[Tuple[str, str, str]], 
                 param_ranges: Dict, analyzer: ParameterizedMainForceAnalyzer,
                 shares_cache: Dict[str, float], max_iterations: int = 1000):
        super().__init__()
        self.data_list = data_list
        self.param_ranges = param_ranges
        self.analyzer = analyzer
        self.shares_cache = shares_cache
        self.max_iterations = max_iterations
        self.should_stop = False
        
        # 数据缓存：{(stock_code, date): (tick_data, daily_data)}
        self.data_cache = {}
        # 分析结果缓存：{(stock_code, date): {distribution_score, lift_score, accumulation_score}}
        self.analysis_cache = {}
    
    def stop(self):
        self.should_stop = True
    
    def evaluate_params(self, params: Dict, loaded_data: List) -> Tuple[int, int, List]:
        """评估参数组合的准确率（使用预计算的结果）
        
        Returns:
            (correct, total, results)
        """
        correct = 0
        total = 0
        results = []
        
        for behavior, stock_code, date_str, _, _ in loaded_data:
            try:
                cache_key = (stock_code, date_str)
                if cache_key not in self.analysis_cache:
                    continue
                
                # 从缓存获取原始得分
                cached_scores = self.analysis_cache[cache_key]
                distribution_score = cached_scores['distribution_score']
                lift_score = cached_scores['lift_score']
                accumulation_score = cached_scores['accumulation_score']
                
                # 应用权重和偏移（硬编码优化后的参数）
                distribution_weight = params.get('distribution_weight', 0.5)  # 优化值：0.5
                distribution_bias = params.get('distribution_bias', 30.0)  # 优化值：30.0
                adjusted_distribution = distribution_score * distribution_weight + distribution_bias
                
                lift_weight = params.get('lift_weight', 1.3)  # 优化值：1.3
                lift_bias = params.get('lift_bias', 20.0)  # 优化值：20.0
                adjusted_lift = lift_score * lift_weight + lift_bias
                
                accumulation_weight = params.get('accumulation_weight', 1.0)  # 优化值：1.0
                accumulation_bias = params.get('accumulation_bias', 15.0)  # 优化值：15.0
                adjusted_accumulation = accumulation_score * accumulation_weight + accumulation_bias
                
                # 应用阈值判断（硬编码优化后的参数）
                distribution_threshold = params.get('distribution_threshold', 30.0)  # 优化值：30.0
                lift_threshold = params.get('lift_threshold', 25.0)  # 优化值：25.0
                accumulation_threshold = params.get('accumulation_threshold', 25.0)  # 优化值：25.0
                
                # 确定主导行为
                scores = {
                    'distribution': adjusted_distribution if adjusted_distribution >= distribution_threshold else 0,
                    'lift': adjusted_lift if adjusted_lift >= lift_threshold else 0,
                    'accumulation': adjusted_accumulation if adjusted_accumulation >= accumulation_threshold else 0
                }
                
                # 如果启用归一化，将得分归一化到0-100
                if params.get('normalize', False):
                    max_score = max(scores.values())
                    if max_score > 0:
                        for key in scores:
                            if scores[key] > 0:
                                scores[key] = (scores[key] / max_score) * 100
                
                # 确定主导行为
                max_score = max(scores.values())
                if max_score == 0:
                    predicted = None
                else:
                    min_score_diff = params.get('min_score_diff', 0.0)
                    max_behaviors = [key for key, score in scores.items() if score == max_score]
                    if len(max_behaviors) == 1:
                        second_max = max([scores[k] for k in scores.keys() if k != max_behaviors[0]])
                        if max_score - second_max >= min_score_diff:
                            predicted = max_behaviors[0]
                        else:
                            predicted = None
                    else:
                        priority_order = ['distribution', 'lift', 'accumulation']
                        for priority_key in priority_order:
                            if priority_key in max_behaviors:
                                predicted = priority_key
                                break
                        else:
                            predicted = max_behaviors[0]
                
                # 映射行为名称
                behavior_map = {
                    '主力出货或洗盘': 'distribution',
                    '主力拉升': 'lift',
                    '主力吸筹': 'accumulation'
                }
                actual = behavior_map.get(behavior, behavior)
                
                is_correct = (predicted == actual)
                if is_correct:
                    correct += 1
                total += 1
                
                results.append({
                    'stock_code': stock_code,
                    'date': date_str,
                    'actual': actual,
                    'predicted': predicted,
                    'is_correct': is_correct,
                    'scores': scores,
                    'raw_scores': {
                        'distribution': distribution_score,
                        'lift': lift_score,
                        'accumulation': accumulation_score
                    }
                })
            except Exception as e:
                self.status.emit(f"错误: {stock_code} {date_str} 评估失败: {str(e)}")
                continue
        
        return correct, total, results
    
    def run(self):
        try:
            # 创建logger（BacktestEngine需要）
            from utils.logger import Logger
            logger = Logger()
            
            # 创建KeyPriceCalculator用于获取日线数据
            key_price_calculator = KeyPriceCalculator()
            
            # 加载数据
            self.status.emit("开始加载数据，共 {} 条...".format(len(self.data_list)))
            loaded_data = []
            
            for i, (behavior, stock_code, date_str) in enumerate(self.data_list):
                if self.should_stop:
                    return
                
                self.progress.emit(int((i / len(self.data_list)) * 50))
                self.status.emit(f"加载数据 {i+1}/{len(self.data_list)}: {stock_code} {date_str}")
                
                try:
                    # 转换日期格式
                    analysis_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    
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
                    engine.set_logger(logger)
                    success = engine.load_data(analysis_date, analysis_date)
                    
                    if not success or engine.data is None or engine.data.empty:
                        self.status.emit(f"警告: {stock_code} {date_str} 无法获取tick数据，跳过")
                        continue
                    
                    tick_data = engine.data
                    
                    # 获取日线数据
                    daily_data = key_price_calculator._get_qmt_daily_data(formatted_stock_code)
                    if daily_data is None or daily_data.empty:
                        self.status.emit(f"警告: {stock_code} {date_str} 无法获取日线数据，跳过")
                        continue
                    
                    # 缓存数据
                    cache_key = (stock_code, date_str)
                    self.data_cache[cache_key] = (tick_data, daily_data)
                    loaded_data.append((behavior, stock_code, date_str, tick_data, daily_data))
                except Exception as e:
                    self.status.emit(f"错误: {stock_code} {date_str} 加载失败: {str(e)}")
                    import traceback
                    self.status.emit(traceback.format_exc())
                    continue
            
            if len(loaded_data) == 0:
                self.status.emit("错误：没有成功加载任何数据")
                self.finished.emit({})
                return
            
            self.status.emit(f"数据加载完成，成功加载 {len(loaded_data)} 条数据")
            
            # 预先计算所有股票的原始分析结果（只计算一次）
            self.status.emit("开始预计算分析结果...")
            precompute_count = 0
            for i, (behavior, stock_code, date_str, tick_data, daily_data) in enumerate(loaded_data):
                if self.should_stop:
                    return
                
                cache_key = (stock_code, date_str)
                try:
                    # 预先计算三种行为的原始得分
                    distribution_result = self.analyzer.base_analyzer.analyze_high_level_distribution_or_wash_comprehensive(
                        daily_data, tick_data, stock_code, date_str
                    )
                    lift_result = self.analyzer.base_analyzer.analyze_main_force_lift_comprehensive(
                        daily_data, tick_data, stock_code, date_str
                    )
                    accumulation_result = self.analyzer.base_analyzer.analyze_low_level_accumulation_comprehensive(
                        daily_data, tick_data, stock_code, date_str
                    )
                    
                    # 缓存原始得分
                    self.analysis_cache[cache_key] = {
                        'distribution_score': distribution_result.get('total_score', 0),
                        'lift_score': lift_result.get('total_score', 0),
                        'accumulation_score': accumulation_result.get('total_score', 0)
                    }
                    precompute_count += 1
                    
                    if (i + 1) % 5 == 0:
                        self.status.emit(f"预计算进度: {precompute_count}/{len(loaded_data)}...")
                except Exception as e:
                    # 分析失败，缓存空结果
                    self.analysis_cache[cache_key] = {
                        'distribution_score': 0,
                        'lift_score': 0,
                        'accumulation_score': 0
                    }
                    continue
            
            self.status.emit(f"预计算完成，成功分析 {precompute_count} 条数据")
            
            # 计算总组合数
            total_combinations = 1
            for param_name, param_range in self.param_ranges.items():
                if isinstance(param_range, (list, tuple)):
                    total_combinations *= len(param_range)
            
            # 限制搜索次数
            if total_combinations > self.max_iterations:
                # 使用随机搜索
                self.status.emit(f"参数组合数过多({total_combinations})，使用随机搜索（最多{self.max_iterations}次）...")
                iterations = min(self.max_iterations, total_combinations)
                
                best_params = None
                best_accuracy = 0
                best_results = None
                
                for i in range(iterations):
                    if self.should_stop:
                        return
                    
                    progress = 50 + int((i / iterations) * 50)
                    self.progress.emit(progress)
                    self.status.emit(f"随机搜索 {i+1}/{iterations}...")
                    
                    # 随机选择参数
                    params = {}
                    for param_name, param_range in self.param_ranges.items():
                        if isinstance(param_range, (list, tuple)):
                            params[param_name] = np.random.choice(param_range)
                        else:
                            params[param_name] = param_range
                    
                    # 评估参数（使用预计算的结果）
                    correct, total, results = self.evaluate_params(params, loaded_data)
                    
                    if total == 0:
                        continue
                    
                    accuracy = correct / total
                    
                    if accuracy > best_accuracy:
                        best_accuracy = accuracy
                        best_params = params.copy()
                        best_results = results
                        self.status.emit(f"发现更好的参数组合，准确率: {best_accuracy:.2%}")
            else:
                # 使用网格搜索
                param_names = list(self.param_ranges.keys())
                param_values = [self.param_ranges[name] for name in param_names]
                
                self.status.emit(f"使用网格搜索，共 {total_combinations} 种组合...")
                
                best_params = None
                best_accuracy = 0
                best_results = None
                
                iteration = 0
                for param_combo in product(*param_values):
                    if self.should_stop:
                        return
                    
                    iteration += 1
                    progress = 50 + int((iteration / total_combinations) * 50)
                    self.progress.emit(progress)
                    if iteration % 10 == 0 or iteration == total_combinations:
                        self.status.emit(f"测试参数组合 {iteration}/{total_combinations}...")
                    
                    # 构建参数字典
                    params = dict(zip(param_names, param_combo))
                    
                    # 评估参数（使用预计算的结果）
                    correct, total, results = self.evaluate_params(params, loaded_data)
                    
                    if total == 0:
                        continue
                    
                    accuracy = correct / total
                    
                    if accuracy > best_accuracy:
                        best_accuracy = accuracy
                        best_params = params.copy()
                        best_results = results
            
            if best_params is None:
                self.status.emit("优化失败: 没有找到有效参数组合")
                self.finished.emit({})
                return
            
            self.status.emit(f"优化完成！最佳准确率: {best_accuracy:.2%}")
            
            self.finished.emit({
                'params': best_params,
                'accuracy': best_accuracy,
                'results': best_results,
                'total': len(loaded_data)
            })
            
        except Exception as e:
            self.status.emit(f"优化过程出错: {str(e)}")
            import traceback
            self.status.emit(traceback.format_exc())
            self.finished.emit({})


class MainForceBehaviorOptimizerGUI(QMainWindow):
    """主力行为参数优化GUI"""
    
    def __init__(self):
        super().__init__()
        self.data_list = []
        self.param_ranges = {}
        self.optimization_thread = None
        self.shares_cache = {}
        self.init_ui()
        # 自动加载 test.txt 文件（如果存在）
        self.auto_load_test_file()
    
    def init_ui(self):
        self.setWindowTitle("主力三大行为参数优化工具")
        self.setGeometry(100, 100, 1200, 800)
        
        # 主窗口
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        
        # 数据加载区域
        data_group = QGroupBox("数据加载")
        data_layout = QVBoxLayout()
        
        file_layout = QHBoxLayout()
        self.file_label = QLabel("未加载文件")
        self.load_file_btn = QPushButton("加载数据文件")
        self.load_file_btn.clicked.connect(self.load_data_file)
        file_layout.addWidget(self.file_label)
        file_layout.addWidget(self.load_file_btn)
        file_layout.addStretch()
        data_layout.addLayout(file_layout)
        
        self.data_text = QTextEdit()
        self.data_text.setMaximumHeight(150)
        self.data_text.setReadOnly(True)
        data_layout.addWidget(self.data_text)
        
        data_group.setLayout(data_layout)
        main_layout.addWidget(data_group)
        
        # 参数范围设置区域
        param_group = QGroupBox("参数范围设置")
        param_layout = QVBoxLayout()
        
        # 创建参数表格
        self.param_table = QTableWidget()
        self.param_table.setColumnCount(4)
        self.param_table.setHorizontalHeaderLabels(["参数名", "最小值", "最大值", "步长"])
        self.param_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        param_layout.addWidget(self.param_table)
        
        # 添加默认参数
        self.add_default_params()
        
        param_group.setLayout(param_layout)
        main_layout.addWidget(param_group)
        
        # 控制按钮和设置
        control_group = QGroupBox("优化控制")
        control_layout = QVBoxLayout()
        
        # 最大迭代次数设置
        iteration_layout = QHBoxLayout()
        iteration_layout.addWidget(QLabel("最大迭代次数:"))
        self.max_iterations_spin = QSpinBox()
        self.max_iterations_spin.setMinimum(100)
        self.max_iterations_spin.setMaximum(100000)
        self.max_iterations_spin.setValue(1000)
        self.max_iterations_spin.setSingleStep(100)
        iteration_layout.addWidget(self.max_iterations_spin)
        iteration_layout.addWidget(QLabel("(当参数组合数超过此值时，使用随机搜索)"))
        iteration_layout.addStretch()
        control_layout.addLayout(iteration_layout)
        
        # 按钮
        button_layout = QHBoxLayout()
        self.optimize_btn = QPushButton("开始优化")
        self.optimize_btn.clicked.connect(self.start_optimization)
        self.stop_btn = QPushButton("停止优化")
        self.stop_btn.clicked.connect(self.stop_optimization)
        self.stop_btn.setEnabled(False)
        self.analyze_scores_btn = QPushButton("原始得分详细分析")
        self.analyze_scores_btn.clicked.connect(self.analyze_raw_scores)
        self.analyze_scores_btn.setEnabled(False)
        button_layout.addWidget(self.optimize_btn)
        button_layout.addWidget(self.stop_btn)
        button_layout.addWidget(self.analyze_scores_btn)
        button_layout.addStretch()
        control_layout.addLayout(button_layout)
        
        control_group.setLayout(control_layout)
        main_layout.addWidget(control_group)
        
        # 进度条
        self.progress_bar = QProgressBar()
        main_layout.addWidget(self.progress_bar)
        
        # 状态显示
        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        main_layout.addWidget(self.status_text)
        
        # 结果显示
        result_group = QGroupBox("优化结果")
        result_layout = QVBoxLayout()
        
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setFont(QFont("Courier", 10))
        result_layout.addWidget(self.result_text)
        
        result_group.setLayout(result_layout)
        main_layout.addWidget(result_group)
    
    def add_default_params(self):
        """添加默认参数（根据分析结果深度优化）"""
        default_params = [
            # 主力出货或洗盘参数（识别率100%，但可能过于激进，需要平衡）
            # 当前distribution_bias=25.0太高，导致拉升被误判为出货
            ("distribution_weight", 0.5, 1.2, 0.1),
            ("distribution_bias", 10.0, 30.0, 5.0),  # 降低下限，当前25.0太高
            ("distribution_threshold", 15.0, 30.0, 5.0),  # 当前15.0效果很好
            
            # 主力拉升参数（识别率18.2%，需要继续优化）
            # 问题：拉升得分平均34.8，但出货得分27.7+25.0偏移=52.7，超过拉升
            ("lift_weight", 1.0, 1.5, 0.1),  # 提高下限，当前1.1效果一般
            ("lift_bias", 0.0, 20.0, 5.0),  # 允许正偏移来对抗出货的高偏移，当前-5.0不够
            ("lift_threshold", 25.0, 35.0, 5.0),  # 当前35.0，可以适当降低
            
            # 主力吸筹参数（识别率0%，根本问题：吸筹时拉升得分最高53.3）
            # 需要大幅降低吸筹阈值，或者提高吸筹权重/偏移
            ("accumulation_weight", 0.8, 1.5, 0.1),  # 提高下限，当前0.7不够
            ("accumulation_bias", 15.0, 30.0, 5.0),  # 大幅提高偏移，当前10.0不够
            ("accumulation_threshold", 15.0, 30.0, 5.0),  # 大幅降低阈值，当前40.0太高
            
            # 最小分差（当前0.0，可以适当提高来避免误判）
            ("min_score_diff", 0.0, 8.0, 2.0),
        ]
        
        self.param_table.setRowCount(len(default_params))
        for i, (name, min_val, max_val, step) in enumerate(default_params):
            self.param_table.setItem(i, 0, QTableWidgetItem(name))
            self.param_table.setItem(i, 1, QTableWidgetItem(str(min_val)))
            self.param_table.setItem(i, 2, QTableWidgetItem(str(max_val)))
            self.param_table.setItem(i, 3, QTableWidgetItem(str(step)))
    
    def auto_load_test_file(self):
        """自动加载 test.txt 文件"""
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
            self.data_list = []
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    # 支持制表符或空格分隔
                    parts = line.split('\t') if '\t' in line else line.split()
                    if len(parts) >= 3:
                        behavior = parts[0]
                        stock_code = parts[1]
                        date_str = parts[2]
                        self.data_list.append((behavior, stock_code, date_str))
            
            self.file_label.setText(f"已加载 {len(self.data_list)} 条数据")
            self.data_text.clear()
            self.data_text.append(f"成功加载 {len(self.data_list)} 条数据：\n")
            for behavior, stock_code, date_str in self.data_list:
                self.data_text.append(f"{behavior} {stock_code} {date_str}")
            
            self.optimize_btn.setEnabled(len(self.data_list) > 0)
            self.analyze_scores_btn.setEnabled(len(self.data_list) > 0)
            self.status_text.append(f"成功加载 {len(self.data_list)} 条数据")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载文件失败: {str(e)}")
    
    def get_param_ranges(self):
        """从表格获取参数范围"""
        param_ranges = {}
        for i in range(self.param_table.rowCount()):
            name_item = self.param_table.item(i, 0)
            min_item = self.param_table.item(i, 1)
            max_item = self.param_table.item(i, 2)
            step_item = self.param_table.item(i, 3)
            
            if not all([name_item, min_item, max_item, step_item]):
                continue
            
            name = name_item.text().strip()
            try:
                min_val = float(min_item.text())
                max_val = float(max_item.text())
                step = float(step_item.text())
                
                if step <= 0:
                    continue
                
                # 生成参数值列表
                values = []
                current = min_val
                while current <= max_val:
                    values.append(current)
                    current += step
                # 确保包含最大值
                if values[-1] < max_val:
                    values.append(max_val)
                
                param_ranges[name] = values
            except ValueError:
                continue
        
        return param_ranges
    
    def start_optimization(self):
        """开始优化"""
        if len(self.data_list) == 0:
            QMessageBox.warning(self, "警告", "请先加载数据文件")
            return
        
        # 获取参数范围
        self.param_ranges = self.get_param_ranges()
        if len(self.param_ranges) == 0:
            QMessageBox.warning(self, "警告", "请设置参数范围")
            return
        
        # 创建分析器
        analyzer = StockAnalyzer()
        cached_analyzer = CachedStockAnalyzer(self.shares_cache)
        param_analyzer = ParameterizedMainForceAnalyzer(cached_analyzer)
        
        # 获取最大迭代次数
        max_iterations = self.max_iterations_spin.value()
        
        # 创建优化线程
        self.optimization_thread = OptimizationThread(
            self.data_list, self.param_ranges, param_analyzer, self.shares_cache, max_iterations
        )
        self.optimization_thread.progress.connect(self.progress_bar.setValue)
        self.optimization_thread.status.connect(self.on_status_update)
        self.optimization_thread.finished.connect(self.on_optimization_finished)
        
        # 更新UI
        self.optimize_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.result_text.clear()
        
        # 启动线程
        self.optimization_thread.start()
    
    def stop_optimization(self):
        """停止优化"""
        if self.optimization_thread:
            self.optimization_thread.stop()
            self.optimization_thread.wait()
            self.optimize_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
    
    def play_completion_sound(self):
        """播放完成提示音"""
        try:
            if platform.system() == 'Windows' and winsound:
                # Windows系统：播放系统提示音（连续3次，每次间隔200ms）
                for _ in range(3):
                    winsound.Beep(1000, 200)  # 频率1000Hz，持续200ms
                    if _ < 2:  # 最后一次不需要延迟
                        import time
                        time.sleep(0.1)
            else:
                # 其他系统：尝试使用系统通知音
                try:
                    import subprocess
                    if platform.system() == 'Darwin':  # macOS
                        subprocess.run(['afplay', '/System/Library/Sounds/Glass.aiff'])
                    elif platform.system() == 'Linux':  # Linux
                        subprocess.run(['aplay', '/usr/share/sounds/alsa/Front_Left.wav'], 
                                     stderr=subprocess.DEVNULL)
                except:
                    pass
        except Exception as e:
            # 如果播放声音失败，静默处理
            pass
    
    def on_status_update(self, message: str):
        """更新状态"""
        self.status_text.append(message)
        # 自动滚动到底部
        scrollbar = self.status_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def on_optimization_finished(self, result: Dict):
        """优化完成"""
        self.optimize_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setValue(100)
        
        # 播放完成提示音
        self.play_completion_sound()
        
        if not result:
            self.result_text.append("优化失败")
            return
        
        params = result.get('params', {})
        accuracy = result.get('accuracy', 0)
        results = result.get('results', [])
        total = result.get('total', 0)
        
        # 显示最优参数
        self.result_text.clear()
        self.result_text.append("=" * 60)
        self.result_text.append("最优参数组合:")
        self.result_text.append("=" * 60)
        for key, value in sorted(params.items()):
            self.result_text.append(f"  {key}: {value}")
        self.result_text.append("")
        self.result_text.append(f"准确率: {accuracy:.2%}")
        self.result_text.append(f"正确: {sum(1 for r in results if r.get('is_correct', False))}/{total}")
        self.result_text.append("")
        
        # 生成混淆矩阵
        self.generate_confusion_matrix(results)
        
        # 显示参数JSON格式
        self.result_text.append("参数JSON格式:")
        self.result_text.append(json.dumps(params, indent=2, ensure_ascii=False))
    
    def generate_confusion_matrix(self, results: List[Dict]):
        """生成混淆矩阵"""
        # 行为映射
        behavior_map = {
            'distribution': '主力出货或洗盘',
            'lift': '主力拉升',
            'accumulation': '主力吸筹'
        }
        
        # 收集实际和预测
        actual_behaviors = set()
        predicted_behaviors = set()
        for r in results:
            actual = r.get('actual')
            predicted = r.get('predicted')
            if actual:
                actual_behaviors.add(actual)
            if predicted:
                predicted_behaviors.add(predicted)
        
        all_behaviors = sorted(actual_behaviors | predicted_behaviors)
        
        # 构建混淆矩阵
        matrix = {}
        for actual in all_behaviors:
            matrix[actual] = {}
            for predicted in all_behaviors:
                matrix[actual][predicted] = 0
        
        for r in results:
            actual = r.get('actual')
            predicted = r.get('predicted')
            if actual and predicted:
                matrix[actual][predicted] = matrix[actual].get(predicted, 0) + 1
        
        # 显示混淆矩阵
        self.result_text.append("混淆矩阵 (行=实际行为, 列=预测行为):")
        self.result_text.append("-" * 60)
        
        # 表头
        header = "实际/预测       "
        for pred in all_behaviors:
            header += f"{behavior_map.get(pred, pred):<15}"
        self.result_text.append(header)
        self.result_text.append("-" * 60)
        
        # 矩阵内容
        for actual in all_behaviors:
            row = f"{behavior_map.get(actual, actual):<15}"
            total_actual = sum(matrix[actual].values())
            for pred in all_behaviors:
                count = matrix[actual][pred]
                if total_actual > 0:
                    percentage = (count / total_actual) * 100
                    row += f"{count}({percentage:.1f}%)    "
                else:
                    row += f"{count}(0.0%)    "
            row += f"总计: {total_actual}"
            self.result_text.append(row)
        
        self.result_text.append("=" * 60)
        self.result_text.append("分析说明:")
        self.result_text.append("- 对角线上的数字表示正确分类的数量")
        self.result_text.append("- 非对角线上的数字表示错误分类的数量")
        self.result_text.append("- 百分比表示该预测行为占该实际行为的比例")
        self.result_text.append("")
        
        # 各行为识别准确率
        self.result_text.append("各行为识别准确率:")
        for actual in all_behaviors:
            total_actual = sum(matrix[actual].values())
            correct = matrix[actual].get(actual, 0)
            if total_actual > 0:
                accuracy = correct / total_actual
                self.result_text.append(f"{behavior_map.get(actual, actual)}: {correct}/{total_actual} = {accuracy:.1%}")
        self.result_text.append("")
        
        # 显示误判样本详细信息
        misclassified = [r for r in results if not r.get('is_correct', False)]
        if misclassified:
            self.result_text.append("=" * 60)
            self.result_text.append("误判样本详细信息:")
            self.result_text.append("-" * 60)
            for i, sample in enumerate(misclassified[:10], 1):  # 最多显示10个误判样本
                self.result_text.append(f"\n【误判样本 {i}】")
                self.result_text.append(f"  股票代码: {sample.get('stock_code', 'N/A')}")
                self.result_text.append(f"  分析日期: {sample.get('date', 'N/A')}")
                self.result_text.append(f"  实际行为: {behavior_map.get(sample.get('actual', ''), sample.get('actual', 'N/A'))}")
                self.result_text.append(f"  预测行为: {behavior_map.get(sample.get('predicted', ''), sample.get('predicted', 'N/A') or '无')}")
                
                raw_scores = sample.get('raw_scores', {})
                scores = sample.get('scores', {})
                if raw_scores:
                    self.result_text.append(f"  原始得分: 出货或洗盘={raw_scores.get('distribution', 0):.1f}, "
                                          f"拉升={raw_scores.get('lift', 0):.1f}, "
                                          f"吸筹={raw_scores.get('accumulation', 0):.1f}")
                if scores:
                    self.result_text.append(f"  调整后得分: 出货或洗盘={scores.get('distribution', 0):.1f}, "
                                          f"拉升={scores.get('lift', 0):.1f}, "
                                          f"吸筹={scores.get('accumulation', 0):.1f}")
            if len(misclassified) > 10:
                self.result_text.append(f"\n... 还有 {len(misclassified) - 10} 个误判样本未显示")
            self.result_text.append("")
        
        # 原始得分分析
        self.result_text.append("=" * 60)
        self.result_text.append("原始得分分析:")
        self.result_text.append("-" * 60)
        
        # 按实际行为分组统计原始得分
        behavior_scores = {
            'distribution': {'distribution': [], 'lift': [], 'accumulation': []},
            'lift': {'distribution': [], 'lift': [], 'accumulation': []},
            'accumulation': {'distribution': [], 'lift': [], 'accumulation': []}
        }
        
        for r in results:
            actual = r.get('actual')
            raw_scores = r.get('raw_scores', {})
            if actual and actual in behavior_scores and raw_scores:
                behavior_scores[actual]['distribution'].append(raw_scores.get('distribution', 0))
                behavior_scores[actual]['lift'].append(raw_scores.get('lift', 0))
                behavior_scores[actual]['accumulation'].append(raw_scores.get('accumulation', 0))
        
        for actual_behavior in ['distribution', 'lift', 'accumulation']:
            if not behavior_scores[actual_behavior]['distribution']:
                continue
            
            self.result_text.append(f"\n【{behavior_map.get(actual_behavior, actual_behavior)}】原始得分统计:")
            for score_type in ['distribution', 'lift', 'accumulation']:
                scores = behavior_scores[actual_behavior][score_type]
                if scores:
                    avg_score = np.mean(scores)
                    max_score = np.max(scores)
                    min_score = np.min(scores)
                    self.result_text.append(f"  {behavior_map.get(score_type, score_type)}: "
                                          f"平均={avg_score:.1f}, 最大={max_score:.1f}, 最小={min_score:.1f}")
        
        self.result_text.append("")
    
    def analyze_raw_scores(self):
        """分析原始得分，显示每个公式的详细得分"""
        if not self.data_list:
            QMessageBox.warning(self, "警告", "请先加载数据文件")
            return
        
        self.result_text.clear()
        self.result_text.append("正在分析原始得分，请稍候...")
        QApplication.processEvents()
        
        try:
            from utils.logger import Logger
            logger = Logger()
            analyzer = StockAnalyzer()
            key_price_calculator = KeyPriceCalculator()
            
            behavior_map = {
                '主力出货或洗盘': 'distribution',
                '主力拉升': 'lift',
                '主力吸筹': 'accumulation'
            }
            
            # 收集所有样本的详细得分
            accumulation_samples = []  # 特别关注主力吸筹样本
            
            loaded_count = 0
            for behavior, stock_code, date_str in self.data_list:
                try:
                    # 转换日期格式
                    analysis_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    
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
                    
                    # 分析三种行为
                    distribution_result = analyzer.analyze_high_level_distribution_or_wash_comprehensive(
                        daily_data, tick_data, stock_code, date_str
                    )
                    lift_result = analyzer.analyze_main_force_lift_comprehensive(
                        daily_data, tick_data, stock_code, date_str
                    )
                    accumulation_result = analyzer.analyze_low_level_accumulation_comprehensive(
                        daily_data, tick_data, stock_code, date_str
                    )
                    
                    # 获取各公式的得分
                    distribution_formulas = distribution_result.get('formulas', {})
                    lift_formulas = lift_result.get('formulas', {})
                    accumulation_formulas = accumulation_result.get('formulas', {})
                    
                    # 获取关键数据（涨幅、位置、波动）
                    accumulation_formula1_details = accumulation_formulas.get('formula1', {}).get('details', {})
                    lift_formula1_details = lift_formulas.get('formula1', {}).get('details', {})
                    
                    sample_data = {
                            'behavior': behavior,
                            'stock_code': stock_code,
                            'date': date_str,
                            'distribution_total': distribution_result.get('total_score', 0),
                            'lift_total': lift_result.get('total_score', 0),
                            'accumulation_total': accumulation_result.get('total_score', 0),
                            'distribution_formulas': {
                                'formula1': distribution_formulas.get('formula1', {}).get('score', 0),
                                'formula2': distribution_formulas.get('formula2', {}).get('score', 0),
                                'formula3': distribution_formulas.get('formula3', {}).get('score', 0),
                            },
                            'lift_formulas': {
                                'formula1': lift_formulas.get('formula1', {}).get('score', 0),
                                'formula2': lift_formulas.get('formula2', {}).get('score', 0),
                                'formula3': lift_formulas.get('formula3', {}).get('score', 0),
                            },
                            'accumulation_formulas': {
                                'formula1': accumulation_formulas.get('formula1', {}).get('score', 0),
                                'formula2': accumulation_formulas.get('formula2', {}).get('score', 0),
                                'formula3': accumulation_formulas.get('formula3', {}).get('score', 0),
                            },
                            # 关键数据
                            'month_return': accumulation_formula1_details.get('month_return', 0),
                            'price_above_min': accumulation_formula1_details.get('price_above_min', 0),
                            'month_fluctuation': accumulation_formula1_details.get('month_fluctuation', 0),
                            'lift_month_return': lift_formula1_details.get('month_return', 0),
                            'lift_price_above_min': lift_formula1_details.get('price_above_min', 0),
                        'lift_percentile': lift_formula1_details.get('percentile', 0),
                    }
                    
                    # 如果是主力吸筹样本，特别记录
                    if behavior == '主力吸筹':
                        accumulation_samples.append(sample_data)
                    
                    loaded_count += 1
                    
                    if loaded_count % 5 == 0:
                        self.result_text.clear()
                        self.result_text.append(f"已分析 {loaded_count}/{len(self.data_list)} 条数据...")
                        QApplication.processEvents()
                    
                except Exception as e:
                    import traceback
                    self.result_text.append(f"分析 {stock_code} {date_str} 失败: {str(e)}\n")
                    continue
            
            # 显示分析结果
            self.display_detailed_score_analysis(accumulation_samples, behavior_map)
            
        except Exception as e:
            import traceback
            self.result_text.setText(f"分析失败: {str(e)}\n{traceback.format_exc()}")
    
    def display_detailed_score_analysis(self, accumulation_samples: List[Dict], behavior_map: Dict):
        """显示详细的得分分析结果"""
        text = "=" * 80 + "\n"
        text += "原始得分详细分析报告\n"
        text += "=" * 80 + "\n\n"
        
        # 特别分析主力吸筹样本
        text += "【主力吸筹样本详细分析】\n"
        text += "-" * 80 + "\n"
        text += f"共 {len(accumulation_samples)} 个主力吸筹样本\n\n"
        
        if not accumulation_samples:
            text += "未找到主力吸筹样本\n"
        else:
            for i, sample in enumerate(accumulation_samples, 1):
                text += f"\n【样本 {i}】{sample['stock_code']} {sample['date']}\n"
                text += f"  实际行为: {sample['behavior']}\n"
                text += f"\n  总分:\n"
                text += f"    出货或洗盘总分: {sample['distribution_total']:.1f}\n"
                text += f"    拉升总分: {sample['lift_total']:.1f}\n"
                text += f"    吸筹总分: {sample['accumulation_total']:.1f}\n"
                
                text += f"\n  关键数据（用于诊断）:\n"
                text += f"    近20日涨幅: {sample.get('month_return', 0):.2%}\n"
                text += f"    距离60日最低价: {sample.get('price_above_min', 0):.2%}\n"
                text += f"    近30日波动: {sample.get('month_fluctuation', 0):.2%}\n"
                text += f"    拉升公式1-60日分位: {sample.get('lift_percentile', 0):.1f}%\n"
                text += f"    拉升公式1-涨幅: {sample.get('lift_month_return', 0):.2%}\n"
                text += f"    拉升公式1-距离最低价: {sample.get('lift_price_above_min', 0):.2%}\n"
                
                text += f"\n  吸筹公式得分:\n"
                acc_formulas = sample['accumulation_formulas']
                text += f"    公式1（日K线级低位吸筹位置判断）: {acc_formulas['formula1']:.1f}\n"
                text += f"    公式2（Tick级卖档压单被啃食）: {acc_formulas['formula2']:.1f}\n"
                text += f"    公式3（Tick级分时抗跌+尾盘抢筹）: {acc_formulas['formula3']:.1f}\n"
                
                text += f"\n  拉升公式得分:\n"
                lift_formulas = sample['lift_formulas']
                text += f"    公式1（日K线级主力拉升位置判断）: {lift_formulas['formula1']:.1f}\n"
                text += f"    公式2（Tick级主动买单进攻）: {lift_formulas['formula2']:.1f}\n"
                text += f"    公式3（Tick级拉升后承接有力）: {lift_formulas['formula3']:.1f}\n"
                
                text += f"\n  出货或洗盘公式得分:\n"
                dist_formulas = sample['distribution_formulas']
                text += f"    公式1: {dist_formulas['formula1']:.1f}\n"
                text += f"    公式2: {dist_formulas['formula2']:.1f}\n"
                text += f"    公式3: {dist_formulas['formula3']:.1f}\n"
                
                text += "\n" + "-" * 80 + "\n"
        
        # 统计分析
        if accumulation_samples:
            text += "\n【主力吸筹样本统计分析】\n"
            text += "-" * 80 + "\n"
            
            # 吸筹公式得分统计
            acc_formula1_scores = [s['accumulation_formulas']['formula1'] for s in accumulation_samples]
            acc_formula2_scores = [s['accumulation_formulas']['formula2'] for s in accumulation_samples]
            acc_formula3_scores = [s['accumulation_formulas']['formula3'] for s in accumulation_samples]
            
            text += f"\n吸筹公式得分统计:\n"
            text += f"  公式1平均: {np.mean(acc_formula1_scores):.1f}, 最大: {np.max(acc_formula1_scores):.1f}, 最小: {np.min(acc_formula1_scores):.1f}\n"
            text += f"  公式2平均: {np.mean(acc_formula2_scores):.1f}, 最大: {np.max(acc_formula2_scores):.1f}, 最小: {np.min(acc_formula2_scores):.1f}\n"
            text += f"  公式3平均: {np.mean(acc_formula3_scores):.1f}, 最大: {np.max(acc_formula3_scores):.1f}, 最小: {np.min(acc_formula3_scores):.1f}\n"
            
            # 拉升公式得分统计
            lift_formula1_scores = [s['lift_formulas']['formula1'] for s in accumulation_samples]
            lift_formula2_scores = [s['lift_formulas']['formula2'] for s in accumulation_samples]
            lift_formula3_scores = [s['lift_formulas']['formula3'] for s in accumulation_samples]
            
            text += f"\n拉升公式得分统计（主力吸筹时）:\n"
            text += f"  公式1平均: {np.mean(lift_formula1_scores):.1f}, 最大: {np.max(lift_formula1_scores):.1f}, 最小: {np.min(lift_formula1_scores):.1f}\n"
            text += f"  公式2平均: {np.mean(lift_formula2_scores):.1f}, 最大: {np.max(lift_formula2_scores):.1f}, 最小: {np.min(lift_formula2_scores):.1f}\n"
            text += f"  公式3平均: {np.mean(lift_formula3_scores):.1f}, 最大: {np.max(lift_formula3_scores):.1f}, 最小: {np.min(lift_formula3_scores):.1f}\n"
            
            # 总分统计
            acc_totals = [s['accumulation_total'] for s in accumulation_samples]
            lift_totals = [s['lift_total'] for s in accumulation_samples]
            
            text += f"\n总分统计:\n"
            text += f"  吸筹总分平均: {np.mean(acc_totals):.1f}, 最大: {np.max(acc_totals):.1f}, 最小: {np.min(acc_totals):.1f}\n"
            text += f"  拉升总分平均: {np.mean(lift_totals):.1f}, 最大: {np.max(lift_totals):.1f}, 最小: {np.min(lift_totals):.1f}\n"
            text += f"  问题: 拉升总分平均 {np.mean(lift_totals):.1f} > 吸筹总分平均 {np.mean(acc_totals):.1f}\n"
        
        self.result_text.setText(text)


def main():
    app = QApplication(sys.argv)
    window = MainForceBehaviorOptimizerGUI()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()

