"""
量化参与度参数优化工具
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


class ParameterizedQuantitativeAnalyzer:
    """参数化的量化参与分析器"""
    
    def __init__(self, base_analyzer: StockAnalyzer):
        self.base_analyzer = base_analyzer
    
    def analyze_with_params(self, tick_data: pd.DataFrame, daily_data: pd.DataFrame,
                           stock_code: str, params: Dict) -> Dict:
        """使用自定义参数进行分析"""
        # 先使用原始分析器获取三个维度的得分
        result = self.base_analyzer.analyze_quantitative_participation_behavior(
            tick_data, daily_data, stock_code, None
        )
        
        # 获取三个维度的原始得分
        dimension_scores = result.get('dimension_scores', {})
        dim1_score = dimension_scores.get('volume_fluctuation', 0)
        dim2_score = dimension_scores.get('order_book_changes', 0)
        dim3_score = dimension_scores.get('volume_price_linkage', 0)
        
        # 应用权重参数
        dim1_weight = params.get('volume_fluctuation_weight', 1.0)
        dim2_weight = params.get('order_book_changes_weight', 1.0)
        dim3_weight = params.get('volume_price_linkage_weight', 1.0)
        
        adjusted_dim1 = dim1_score * dim1_weight
        adjusted_dim2 = dim2_score * dim2_weight
        adjusted_dim3 = dim3_score * dim3_weight
        
        # 应用阈值参数（判断是否满足条件）
        threshold = params.get('dimension_threshold', 50.0)
        
        satisfied_dimensions = sum([
            adjusted_dim1 >= threshold,
            adjusted_dim2 >= threshold,
            adjusted_dim3 >= threshold
        ])
        
        has_quantitative = satisfied_dimensions >= params.get('min_satisfied_dimensions', 2)
        
        # 计算综合得分
        overall_score = (adjusted_dim1 + adjusted_dim2 + adjusted_dim3) / 3
        
        # 如果满足条件，给予量化参与得分
        if has_quantitative:
            if satisfied_dimensions == 3:
                # 深度量化参与
                quantitative_score = min(100, overall_score + params.get('deep_participation_bonus', 20))
            else:
                # 普通量化参与
                quantitative_score = min(100, overall_score)
        else:
            quantitative_score = 0
        
        behaviors = {
            'quantitative_participation': quantitative_score
        }
        
        # 确定主导行为
        if quantitative_score >= params.get('min_quantitative_score', 50):
            dominant_behavior = 'quantitative_participation'
        else:
            dominant_behavior = None
        
        return {
            'has_quantitative_participation': has_quantitative,
            'behaviors': behaviors,
            'dominant_behavior': dominant_behavior,
            'behavior_names': {
                'quantitative_participation': '量化参与'
            },
            'dimension_scores': {
                'volume_fluctuation': adjusted_dim1,
                'order_book_changes': adjusted_dim2,
                'volume_price_linkage': adjusted_dim3
            },
            'satisfied_dimensions': satisfied_dimensions,
            'analysis_date': result.get('analysis_date'),
            'stock_code': result.get('stock_code')
        }


class OptimizationThread(QThread):
    """参数优化线程"""
    progress = pyqtSignal(int, str)
    result = pyqtSignal(dict)
    
    def __init__(self, stock_date_pairs: List[Tuple], actual_behaviors: List[str],
                 param_ranges: Dict, max_iter: int, global_params: Dict):
        super().__init__()
        self.stock_date_pairs = stock_date_pairs
        self.actual_behaviors = actual_behaviors
        self.param_ranges = param_ranges
        self.max_iter = max_iter
        self.global_params = global_params
        self.stop_flag = False
        self.analyzer = StockAnalyzer()
        self.param_analyzer = ParameterizedQuantitativeAnalyzer(self.analyzer)
        self.analysis_cache = {}
        
    def stop(self):
        """停止优化"""
        self.stop_flag = True
    
    def run(self):
        """执行优化"""
        try:
            # 步骤1：预加载所有分析结果（占60%进度）
            self.progress.emit(0, "开始加载数据...")
            total_samples = len(self.stock_date_pairs)
            
            from core.backtest_engine import BacktestEngine
            from key_price_calculator import KeyPriceCalculator
            from utils.logger import Logger
            
            key_price_calculator = KeyPriceCalculator()
            logger = Logger()
            loaded_count = 0
            
            for idx, (stock_code, date) in enumerate(self.stock_date_pairs):
                if self.stop_flag:
                    return
                
                try:
                    # 转换日期格式
                    if isinstance(date, str):
                        analysis_date = datetime.strptime(date, '%Y-%m-%d').date()
                    else:
                        analysis_date = date
                    
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
                        continue
                    
                    tick_data = engine.data
                    
                    # 获取日线数据（使用格式化后的股票代码）
                    daily_data = key_price_calculator._get_qmt_daily_data(formatted_stock_code)
                    
                    if daily_data.empty:
                        continue
                    
                    # 使用原始分析器获取三个维度的得分（不应用参数）
                    result = self.analyzer.analyze_quantitative_participation_behavior(
                        tick_data, daily_data, stock_code, str(date)
                    )
                    
                    # 缓存结果（只保存维度得分，不保存最终判断）
                    cache_key = (stock_code, str(date))
                    self.analysis_cache[cache_key] = {
                        'dimension_scores': result.get('dimension_scores', {}),
                        'has_quantitative_participation': result.get('has_quantitative_participation', False)
                    }
                    
                    loaded_count += 1
                    
                except Exception as e:
                    continue
                
                progress = int((idx + 1) / total_samples * 60)
                self.progress.emit(progress, f"加载数据 {idx + 1}/{total_samples}，成功 {loaded_count} 条...")
            
            if not self.analysis_cache:
                self.progress.emit(0, "错误：没有成功加载任何数据")
                self.result.emit({
                    'params': {},
                    'accuracy': 0.0,
                    'error': '没有成功加载任何数据'
                })
                return
            
            # 步骤2：参数优化（占40%进度）
            self.progress.emit(60, "开始参数优化...")
            
            # 生成所有参数组合
            param_names = list(self.param_ranges.keys())
            param_values_list = [self.param_ranges[name] for name in param_names]
            all_combinations = list(product(*param_values_list))
            
            # 限制迭代次数
            if len(all_combinations) > self.max_iter:
                # 随机采样
                import random
                all_combinations = random.sample(all_combinations, self.max_iter)
            
            total_combinations = len(all_combinations)
            best_accuracy = 0.0
            best_params = None
            iteration = 0
            
            for param_values in all_combinations:
                if self.stop_flag:
                    return
                
                # 构建参数字典
                params = dict(zip(param_names, param_values))
                params.update(self.global_params)
                
                # 评估参数组合
                accuracy = self.evaluate_params(params)
                
                if accuracy > best_accuracy:
                    best_accuracy = accuracy
                    best_params = params.copy()
                
                iteration += 1
                progress = 60 + int(iteration / total_combinations * 40)
                self.progress.emit(progress, 
                    f"组合 {iteration}/{total_combinations}: 当前最佳准确率 {best_accuracy:.2%}")
            
            if best_params:
                # 获取最优参数的详细评估结果
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
    
    def evaluate_params(self, params: Dict, return_details: bool = False) -> float:
        """评估参数组合的准确率"""
        correct = 0
        total = 0
        
        # 混淆矩阵
        confusion_matrix = {
            '深度量化参与': {'深度量化参与': 0, '量化参与': 0, '未检测到量化参与': 0},
            '量化参与': {'深度量化参与': 0, '量化参与': 0, '未检测到量化参与': 0},
            '未检测到量化参与': {'深度量化参与': 0, '量化参与': 0, '未检测到量化参与': 0}
        }
        
        # 记录误判样本的详细信息
        misclassified_samples = []
        
        for (stock_code, date), actual_behavior in zip(self.stock_date_pairs, self.actual_behaviors):
            if self.stop_flag:
                return 0.0 if not return_details else (0.0, {})
            
            try:
                cache_key = (stock_code, str(date))
                if cache_key not in self.analysis_cache:
                    continue
                
                cached_result = self.analysis_cache[cache_key]
                dimension_scores = cached_result.get('dimension_scores', {})
                
                # 应用参数调整
                dim1_score = dimension_scores.get('volume_fluctuation', 0)
                dim2_score = dimension_scores.get('order_book_changes', 0)
                dim3_score = dimension_scores.get('volume_price_linkage', 0)
                
                dim1_weight = params.get('volume_fluctuation_weight', 1.0)
                dim2_weight = params.get('order_book_changes_weight', 1.0)
                dim3_weight = params.get('volume_price_linkage_weight', 1.0)
                
                adjusted_dim1 = dim1_score * dim1_weight
                adjusted_dim2 = dim2_score * dim2_weight
                adjusted_dim3 = dim3_score * dim3_weight
                
                threshold = params.get('dimension_threshold', 50.0)
                satisfied_dimensions = sum([
                    adjusted_dim1 >= threshold,
                    adjusted_dim2 >= threshold,
                    adjusted_dim3 >= threshold
                ])
                
                # 计算加权得分（与stock_analyzer.py中的逻辑一致）
                weighted_score = (adjusted_dim1 * 0.3 + adjusted_dim2 * 0.4 + adjusted_dim3 * 0.3)
                
                # 判断是否有量化参与（与stock_analyzer.py中的逻辑一致）
                # 注意：这里使用原始得分判断，因为权重调整可能会降低得分
                # 根据原始得分分析：
                # - 深度量化参与：维度1=80，维度2=40，维度3=15（平均）
                # - 量化参与：维度1=80，维度2=44，维度3=40（平均）
                # - 未检测到量化参与：维度1=80，维度2=40，维度3=2.5（平均）
                # 关键区别：维度3得分！未检测到量化参与的维度3得分很低（<10）
                min_satisfied = params.get('min_satisfied_dimensions', 2)
                has_quantitative = satisfied_dimensions >= min_satisfied or (dim1_score >= 70 and dim3_score >= 20) or (dim1_score >= 60 and dim2_score >= 40 and dim3_score >= 10)
                
                # 判断是否为深度量化参与（与stock_analyzer.py中的逻辑一致）
                # 注意：这里使用原始得分判断，因为权重调整可能会降低得分
                # 根据原始得分分析：
                # - 深度量化参与：维度1=80，维度2=40，维度3=15（平均）
                # - 量化参与：维度1=80，维度2=44，维度3=40（平均）
                # - 未检测到量化参与：维度1=80，维度2=40，维度3=2.5（平均）
                # 关键区别：维度3得分！深度量化参与的维度3得分（15）低于量化参与的维度3得分（40）
                is_deep_participation = False
                if satisfied_dimensions == 3:
                    is_deep_participation = True
                elif dim1_score >= 75 and dim2_score >= 35 and dim3_score < 25:
                    # 维度1得分很高，维度2得分较高，但维度3得分较低（<25）
                    # 深度量化参与样本：80≥75 ✓，40≥35 ✓，15<25 ✓
                    # 量化参与样本：80≥75 ✓，44≥35 ✓，40<25 ✗（不会被识别为深度量化参与）
                    is_deep_participation = True
                elif dim1_score >= 70 and dim2_score >= 40 and dim3_score < 30 and weighted_score >= 45:
                    # 维度1和维度2得分都较高，但维度3得分较低，综合得分也较高
                    is_deep_participation = True
                
                # 预测行为
                if has_quantitative:
                    if is_deep_participation:
                        predicted_behavior = '深度量化参与'
                    else:
                        predicted_behavior = '量化参与'
                else:
                    predicted_behavior = '未检测到量化参与'
                
                # 更新混淆矩阵
                if actual_behavior in confusion_matrix:
                    confusion_matrix[actual_behavior][predicted_behavior] = \
                        confusion_matrix[actual_behavior].get(predicted_behavior, 0) + 1
                
                # 判断是否正确
                if actual_behavior == predicted_behavior:
                    correct += 1
                else:
                    # 记录误判样本的详细信息
                    misclassified_samples.append({
                        'stock_code': stock_code,
                        'date': str(date),
                        'actual_behavior': actual_behavior,
                        'predicted_behavior': predicted_behavior,
                        'dim1_score': dim1_score,
                        'dim2_score': dim2_score,
                        'dim3_score': dim3_score,
                        'satisfied_dimensions': satisfied_dimensions,
                        'has_quantitative': has_quantitative,
                        'is_deep_participation': is_deep_participation
                    })
                total += 1
                
            except Exception as e:
                continue
        
        accuracy = correct / total if total > 0 else 0.0
        
        if return_details:
            return accuracy, {
                'confusion_matrix': confusion_matrix,
                'total': total,
                'correct': correct,
                'misclassified_samples': misclassified_samples
            }
        else:
            return accuracy


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
        self.setWindowTitle("量化参与度分析 - 参数优化工具")
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
        
        # 初始化默认参数范围
        self.init_param_table()
        
        param_layout.addWidget(self.param_table)
        
        # 全局参数设置（维度阈值已移到参数表格中优化）
        global_param_layout = QHBoxLayout()
        global_param_layout.addWidget(QLabel("维度阈值（固定值，如需优化请添加到参数表格）:"))
        self.dimension_threshold_spin = QDoubleSpinBox()
        self.dimension_threshold_spin.setMinimum(0.0)
        self.dimension_threshold_spin.setMaximum(100.0)
        self.dimension_threshold_spin.setValue(50.0)
        self.dimension_threshold_spin.setSingleStep(1.0)
        self.dimension_threshold_spin.setEnabled(False)  # 如果参数表格中有dimension_threshold，则禁用
        global_param_layout.addWidget(self.dimension_threshold_spin)
        
        global_param_layout.addWidget(QLabel("最少满足维度数:"))
        self.min_satisfied_dimensions_spin = QSpinBox()
        self.min_satisfied_dimensions_spin.setMinimum(1)
        self.min_satisfied_dimensions_spin.setMaximum(3)
        self.min_satisfied_dimensions_spin.setValue(2)
        global_param_layout.addWidget(self.min_satisfied_dimensions_spin)
        
        global_param_layout.addWidget(QLabel("最小量化参与得分:"))
        self.min_quantitative_score_spin = QDoubleSpinBox()
        self.min_quantitative_score_spin.setMinimum(0.0)
        self.min_quantitative_score_spin.setMaximum(100.0)
        self.min_quantitative_score_spin.setValue(50.0)
        self.min_quantitative_score_spin.setSingleStep(1.0)
        global_param_layout.addWidget(self.min_quantitative_score_spin)
        
        global_param_layout.addWidget(QLabel("深度参与奖励分:"))
        self.deep_participation_bonus_spin = QDoubleSpinBox()
        self.deep_participation_bonus_spin.setMinimum(0.0)
        self.deep_participation_bonus_spin.setMaximum(50.0)
        self.deep_participation_bonus_spin.setValue(20.0)
        self.deep_participation_bonus_spin.setSingleStep(1.0)
        global_param_layout.addWidget(self.deep_participation_bonus_spin)
        
        global_param_layout.addStretch()
        param_layout.addLayout(global_param_layout)
        
        param_group.setLayout(param_layout)
        layout.addWidget(param_group)
        
        # 优化控制区域
        control_group = QGroupBox("优化控制")
        control_layout = QVBoxLayout()
        
        control_btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("开始优化")
        self.start_btn.clicked.connect(self.start_optimization)
        self.start_btn.setEnabled(False)
        
        self.stop_btn = QPushButton("停止优化")
        self.stop_btn.clicked.connect(self.stop_optimization)
        self.stop_btn.setEnabled(False)
        
        control_btn_layout.addWidget(self.start_btn)
        control_btn_layout.addWidget(self.stop_btn)
        control_btn_layout.addStretch()
        
        control_btn_layout.addWidget(QLabel("最大迭代次数:"))
        self.max_iter_spin = QSpinBox()
        self.max_iter_spin.setMinimum(1)
        self.max_iter_spin.setMaximum(100000)
        self.max_iter_spin.setValue(1000)
        control_btn_layout.addWidget(self.max_iter_spin)
        
        control_layout.addLayout(control_btn_layout)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        control_layout.addWidget(self.progress_bar)
        
        control_group.setLayout(control_layout)
        layout.addWidget(control_group)
        
        # 结果显示区域
        result_group = QGroupBox("优化结果")
        result_layout = QVBoxLayout()
        
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setFont(QFont("Courier", 10))
        result_layout.addWidget(self.result_text)
        
        result_group.setLayout(result_layout)
        layout.addWidget(result_group)
        
        # 混淆矩阵显示区域
        confusion_group = QGroupBox("混淆矩阵")
        confusion_layout = QVBoxLayout()
        
        self.confusion_text = QTextEdit()
        self.confusion_text.setReadOnly(True)
        self.confusion_text.setFont(QFont("Courier", 10))
        confusion_layout.addWidget(self.confusion_text)
        
        confusion_group.setLayout(confusion_layout)
        layout.addWidget(confusion_group)
        
        # 原始得分分析区域
        score_analysis_group = QGroupBox("原始得分分析")
        score_analysis_layout = QVBoxLayout()
        
        score_analysis_btn_layout = QHBoxLayout()
        self.analyze_scores_btn = QPushButton("分析原始得分")
        self.analyze_scores_btn.clicked.connect(self.analyze_raw_scores)
        self.analyze_scores_btn.setEnabled(False)
        score_analysis_btn_layout.addWidget(self.analyze_scores_btn)
        score_analysis_btn_layout.addStretch()
        
        score_analysis_layout.addLayout(score_analysis_btn_layout)
        
        self.score_analysis_text = QTextEdit()
        self.score_analysis_text.setReadOnly(True)
        self.score_analysis_text.setFont(QFont("Courier", 9))
        score_analysis_layout.addWidget(self.score_analysis_text)
        
        score_analysis_group.setLayout(score_analysis_layout)
        layout.addWidget(score_analysis_group)
    
    def init_param_table(self):
        """初始化参数表格"""
        # 三个维度的权重参数 + 维度阈值参数
        # 根据原始得分分析，调整参数范围：
        # - 维度1权重可以降低（因为所有样本都是80分，无法区分）
        # - 维度2权重需要提高（深度量化参与样本40分，需要权重放大才能达到阈值）
        # - 维度3权重需要大幅提高（深度量化参与样本0分，需要权重放大）
        # - 维度阈值降低到30-40（因为维度2和维度3得分偏低）
        default_params = [
            ('volume_fluctuation_weight', 0.3, 1.5, 0.1),  # 降低范围，因为无法区分
            ('order_book_changes_weight', 1.0, 3.0, 0.2),  # 提高范围，因为深度量化参与样本40分
            ('volume_price_linkage_weight', 1.0, 5.0, 0.5),  # 大幅提高范围，因为深度量化参与样本0分
            ('dimension_threshold', 30.0, 45.0, 5.0),  # 降低阈值范围，因为维度2和维度3得分偏低
        ]
        
        self.param_table.setRowCount(len(default_params))
        
        for i, (name, min_val, max_val, step) in enumerate(default_params):
            self.param_table.setItem(i, 0, QTableWidgetItem(name))
            self.param_table.setItem(i, 1, QTableWidgetItem(str(min_val)))
            self.param_table.setItem(i, 2, QTableWidgetItem(str(max_val)))
            self.param_table.setItem(i, 3, QTableWidgetItem(str(step)))
    
    def auto_load_test_file(self):
        """自动加载test.txt文件"""
        test_file_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'test.txt')
        if os.path.exists(test_file_path):
            self.load_data_from_file(test_file_path)
    
    def load_data_file(self):
        """加载数据文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择数据文件", "", "Text Files (*.txt);;All Files (*)"
        )
        if file_path:
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
                    
                    # 支持制表符和空格分隔
                    parts = line.split('\t') if '\t' in line else line.split()
                    if len(parts) >= 3:
                        behavior = parts[0]
                        stock_code = parts[1]
                        date_str = parts[2]
                        
                        # 处理日期格式（支持 2025-11-3 这样的格式）
                        try:
                            if len(date_str.split('-')) == 3:
                                year, month, day = date_str.split('-')
                                date_str = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
                            date = datetime.strptime(date_str, '%Y-%m-%d').date()
                            self.actual_behaviors.append(behavior)
                            self.stock_date_pairs.append((stock_code, date))
                        except:
                            continue
            
            self.file_label.setText(f"已加载 {len(self.stock_date_pairs)} 条数据")
            self.start_btn.setEnabled(len(self.stock_date_pairs) > 0)
            self.analyze_scores_btn.setEnabled(len(self.stock_date_pairs) > 0)
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
            'min_satisfied_dimensions': self.min_satisfied_dimensions_spin.value(),
            'min_quantitative_score': self.min_quantitative_score_spin.value(),
            'deep_participation_bonus': self.deep_participation_bonus_spin.value()
        }
        
        # 如果参数表格中没有dimension_threshold，使用固定值
        if 'dimension_threshold' not in param_ranges:
            global_params['dimension_threshold'] = self.dimension_threshold_spin.value()
        
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
    
    def stop_optimization(self):
        """停止优化"""
        if self.optimization_thread:
            self.optimization_thread.stop()
            self.optimization_thread.wait()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setValue(0)
    
    def on_progress(self, value: int, message: str):
        """更新进度"""
        self.progress_bar.setValue(value)
        self.log(message)
    
    def on_result(self, result: Dict):
        """处理优化结果"""
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        
        if 'error' in result:
            self.log(f"优化失败: {result['error']}")
            return
        
        params = result.get('params', {})
        accuracy = result.get('accuracy', 0.0)
        confusion_matrix = result.get('confusion_matrix', {})
        total = result.get('total', 0)
        correct = result.get('correct', 0)
        
        # 显示最优参数
        self.result_text.clear()
        self.result_text.append("最优参数组合:")
        for key, value in sorted(params.items()):
            if key not in ['dimension_threshold', 'min_satisfied_dimensions', 
                          'min_quantitative_score', 'deep_participation_bonus']:
                self.result_text.append(f"  {key}: {value}")
        
        self.result_text.append(f"\n准确率: {accuracy:.2%}")
        self.result_text.append(f"正确: {correct}/{total}")
        
        # 显示全局参数（排除已在参数表格中优化的参数）
        self.result_text.append(f"\n全局参数:")
        if 'dimension_threshold' not in params or params.get('dimension_threshold') == self.dimension_threshold_spin.value():
            self.result_text.append(f"  dimension_threshold: {params.get('dimension_threshold', 50.0)} (固定值)")
        else:
            self.result_text.append(f"  dimension_threshold: {params.get('dimension_threshold', 50.0)} (已优化)")
        self.result_text.append(f"  min_satisfied_dimensions: {params.get('min_satisfied_dimensions', 2)}")
        self.result_text.append(f"  min_quantitative_score: {params.get('min_quantitative_score', 50.0)}")
        self.result_text.append(f"  deep_participation_bonus: {params.get('deep_participation_bonus', 20.0)}")
        
        # 显示参数JSON格式
        self.result_text.append(f"\n参数JSON格式:")
        json_params = {k: v for k, v in params.items()}
        self.result_text.append(json.dumps(json_params, indent=2, ensure_ascii=False))
        
        # 显示混淆矩阵
        self.confusion_text.clear()
        self.confusion_text.append("混淆矩阵 (行=实际行为, 列=预测行为):")
        self.confusion_text.append("-" * 60)
        
        # 表头
        header = "实际/预测       "
        for pred_behavior in ['深度量化参与', '量化参与', '未检测到量化参与']:
            header += f"{pred_behavior:15s}"
        self.confusion_text.append(header)
        self.confusion_text.append("-" * 60)
        
        # 表格内容
        for actual_behavior in ['深度量化参与', '量化参与', '未检测到量化参与']:
            row = f"{actual_behavior:15s}"
            row_data = confusion_matrix.get(actual_behavior, {})
            total_actual = sum(row_data.values())
            
            for pred_behavior in ['深度量化参与', '量化参与', '未检测到量化参与']:
                count = row_data.get(pred_behavior, 0)
                if total_actual > 0:
                    pct = count / total_actual * 100
                    row += f"{count}({pct:.1f}%)    "
                else:
                    row += f"{count}(0.0%)    "
            
            row += f"总计: {total_actual}"
            self.confusion_text.append(row)
        
        self.confusion_text.append("=" * 60)
        self.confusion_text.append("分析说明:")
        self.confusion_text.append("- 对角线上的数字表示正确分类的数量")
        self.confusion_text.append("- 非对角线上的数字表示错误分类的数量")
        self.confusion_text.append("- 百分比表示该预测行为占该实际行为的比例")
        
        # 各行为识别准确率
        self.confusion_text.append("\n各行为识别准确率:")
        for actual_behavior in ['深度量化参与', '量化参与', '未检测到量化参与']:
            row_data = confusion_matrix.get(actual_behavior, {})
            total_actual = sum(row_data.values())
            correct_count = row_data.get(actual_behavior, 0)
            if total_actual > 0:
                acc = correct_count / total_actual * 100
                self.confusion_text.append(f"{actual_behavior}: {correct_count}/{total_actual} = {acc:.1f}%")
        
        # 显示误判样本的详细信息
        misclassified_samples = result.get('misclassified_samples', [])
        if misclassified_samples:
            self.confusion_text.append("\n" + "=" * 60)
            self.confusion_text.append("误判样本详细信息:")
            self.confusion_text.append("-" * 60)
            for i, sample in enumerate(misclassified_samples, 1):
                self.confusion_text.append(f"\n【误判样本 {i}】")
                self.confusion_text.append(f"  股票代码: {sample['stock_code']}")
                self.confusion_text.append(f"  分析日期: {sample['date']}")
                self.confusion_text.append(f"  实际行为: {sample['actual_behavior']}")
                self.confusion_text.append(f"  预测行为: {sample['predicted_behavior']}")
                self.confusion_text.append(f"  维度得分: 维度1={sample['dim1_score']:.1f}, 维度2={sample['dim2_score']:.1f}, 维度3={sample['dim3_score']:.1f}")
                self.confusion_text.append(f"  满足维度数: {sample['satisfied_dimensions']}/3")
                self.confusion_text.append(f"  是否有量化参与: {sample['has_quantitative']}")
                self.confusion_text.append(f"  是否为深度量化参与: {sample['is_deep_participation']}")
    
    def log(self, message: str):
        """记录日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.result_text.append(f"[{timestamp}] {message}")
    
    def analyze_raw_scores(self):
        """分析原始得分分布"""
        if not self.stock_date_pairs:
            QMessageBox.warning(self, "警告", "请先加载数据文件")
            return
        
        self.score_analysis_text.clear()
        self.score_analysis_text.append("正在分析原始得分，请稍候...")
        
        try:
            from core.backtest_engine import BacktestEngine
            from key_price_calculator import KeyPriceCalculator
            from utils.logger import Logger
            
            logger = Logger()
            analyzer = StockAnalyzer()
            key_price_calculator = KeyPriceCalculator()
            
            # 收集所有样本的原始得分
            all_scores = {
                'volume_fluctuation': {'深度量化参与': [], '量化参与': [], '未检测到量化参与': []},
                'order_book_changes': {'深度量化参与': [], '量化参与': [], '未检测到量化参与': []},
                'volume_price_linkage': {'深度量化参与': [], '量化参与': [], '未检测到量化参与': []}
            }
            
            for idx, (stock_code, date) in enumerate(self.stock_date_pairs):
                actual_behavior = self.actual_behaviors[idx] if idx < len(self.actual_behaviors) else None
                
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
                    
                    # 获取三个维度的得分
                    result = analyzer.analyze_quantitative_participation_behavior(
                        tick_data, daily_data, stock_code, str(date)
                    )
                    
                    dimension_scores = result.get('dimension_scores', {})
                    dim1_score = dimension_scores.get('volume_fluctuation', 0)
                    dim2_score = dimension_scores.get('order_book_changes', 0)
                    dim3_score = dimension_scores.get('volume_price_linkage', 0)
                    
                    if actual_behavior:
                        all_scores['volume_fluctuation'][actual_behavior].append(dim1_score)
                        all_scores['order_book_changes'][actual_behavior].append(dim2_score)
                        all_scores['volume_price_linkage'][actual_behavior].append(dim3_score)
                    
                except Exception as e:
                    continue
            
            # 生成分析报告
            text = "=" * 80 + "\n"
            text += "原始得分分析报告\n"
            text += "=" * 80 + "\n"
            
            dimension_names = {
                'volume_fluctuation': '3秒总手数波动',
                'order_book_changes': '盘口挂单变动',
                'volume_price_linkage': '量价联动逻辑'
            }
            
            behavior_names = ['深度量化参与', '量化参与', '未检测到量化参与']
            
            for dim_key, dim_name in dimension_names.items():
                text += f"\n【{dim_name}】原始得分分布：\n"
                text += "-" * 80 + "\n"
                
                for behavior_name in behavior_names:
                    scores = all_scores[dim_key][behavior_name]
                    if scores:
                        text += f"  实际行为: {behavior_name} (样本数: {len(scores)})\n"
                        text += f"    最小值: {min(scores):.2f}\n"
                        text += f"    最大值: {max(scores):.2f}\n"
                        text += f"    平均值: {np.mean(scores):.2f}\n"
                        text += f"    中位数: {np.median(scores):.2f}\n"
                        text += f"    标准差: {np.std(scores):.2f}\n"
                        text += f"    得分列表: {sorted(scores, reverse=True)}\n"
                    else:
                        text += f"  实际行为: {behavior_name} (样本数: 0)\n"
                
                text += "\n"
            
            # 分析问题
            text += "=" * 80 + "\n"
            text += "【问题诊断】\n"
            text += "-" * 80 + "\n"
            
            # 检查深度量化参与样本的得分
            deep_scores_dim1 = all_scores['volume_fluctuation']['深度量化参与']
            deep_scores_dim2 = all_scores['order_book_changes']['深度量化参与']
            deep_scores_dim3 = all_scores['volume_price_linkage']['深度量化参与']
            
            if deep_scores_dim1 or deep_scores_dim2 or deep_scores_dim3:
                text += "\n深度量化参与样本的维度得分：\n"
                if deep_scores_dim1:
                    avg_dim1 = np.mean(deep_scores_dim1)
                    text += f"  3秒总手数波动: 平均 {avg_dim1:.2f}, 满足阈值(50)的样本数: {sum(1 for s in deep_scores_dim1 if s >= 50)}/{len(deep_scores_dim1)}\n"
                if deep_scores_dim2:
                    avg_dim2 = np.mean(deep_scores_dim2)
                    text += f"  盘口挂单变动: 平均 {avg_dim2:.2f}, 满足阈值(50)的样本数: {sum(1 for s in deep_scores_dim2 if s >= 50)}/{len(deep_scores_dim2)}\n"
                if deep_scores_dim3:
                    avg_dim3 = np.mean(deep_scores_dim3)
                    text += f"  量价联动逻辑: 平均 {avg_dim3:.2f}, 满足阈值(50)的样本数: {sum(1 for s in deep_scores_dim3 if s >= 50)}/{len(deep_scores_dim3)}\n"
                
                # 检查是否所有维度都满足阈值
                if deep_scores_dim1 and deep_scores_dim2 and deep_scores_dim3:
                    satisfied_count = 0
                    for i in range(len(deep_scores_dim1)):
                        if (deep_scores_dim1[i] >= 50 and deep_scores_dim2[i] >= 50 and deep_scores_dim3[i] >= 50):
                            satisfied_count += 1
                    text += f"\n  同时满足3个维度阈值(50)的样本数: {satisfied_count}/{len(deep_scores_dim1)}\n"
                    if satisfied_count == 0:
                        text += f"  ⚠️ 问题：深度量化参与样本中，没有任何样本同时满足3个维度阈值(50)！\n"
                        text += f"  建议：降低维度阈值或调整权重参数\n"
            
            # 检查量化参与样本的得分
            quant_scores_dim1 = all_scores['volume_fluctuation']['量化参与']
            quant_scores_dim2 = all_scores['order_book_changes']['量化参与']
            quant_scores_dim3 = all_scores['volume_price_linkage']['量化参与']
            
            if quant_scores_dim1 or quant_scores_dim2 or quant_scores_dim3:
                text += "\n量化参与样本的维度得分：\n"
                if quant_scores_dim1:
                    avg_dim1 = np.mean(quant_scores_dim1)
                    text += f"  3秒总手数波动: 平均 {avg_dim1:.2f}, 满足阈值(50)的样本数: {sum(1 for s in quant_scores_dim1 if s >= 50)}/{len(quant_scores_dim1)}\n"
                if quant_scores_dim2:
                    avg_dim2 = np.mean(quant_scores_dim2)
                    text += f"  盘口挂单变动: 平均 {avg_dim2:.2f}, 满足阈值(50)的样本数: {sum(1 for s in quant_scores_dim2 if s >= 50)}/{len(quant_scores_dim2)}\n"
                if quant_scores_dim3:
                    avg_dim3 = np.mean(quant_scores_dim3)
                    text += f"  量价联动逻辑: 平均 {avg_dim3:.2f}, 满足阈值(50)的样本数: {sum(1 for s in quant_scores_dim3 if s >= 50)}/{len(quant_scores_dim3)}\n"
                
                # 检查是否至少2个维度满足阈值
                if quant_scores_dim1 and quant_scores_dim2 and quant_scores_dim3:
                    satisfied_count = 0
                    for i in range(len(quant_scores_dim1)):
                        satisfied_dims = sum([
                            quant_scores_dim1[i] >= 50,
                            quant_scores_dim2[i] >= 50,
                            quant_scores_dim3[i] >= 50
                        ])
                        if satisfied_dims >= 2:
                            satisfied_count += 1
                    text += f"\n  至少满足2个维度阈值(50)的样本数: {satisfied_count}/{len(quant_scores_dim1)}\n"
                    if satisfied_count < len(quant_scores_dim1):
                        text += f"  ⚠️ 问题：量化参与样本中，有 {len(quant_scores_dim1) - satisfied_count} 个样本无法满足至少2个维度阈值(50)！\n"
                        text += f"  建议：降低维度阈值或调整权重参数\n"
            
            self.score_analysis_text.setText(text)
            
        except Exception as e:
            import traceback
            QMessageBox.critical(self, "错误", f"分析原始得分失败: {str(e)}\n{traceback.format_exc()}")


def main():
    app = QApplication(sys.argv)
    window = ParameterOptimizerWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()

